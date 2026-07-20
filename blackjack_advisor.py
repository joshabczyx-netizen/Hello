#!/usr/bin/env python3
"""Blackjack betting-strategy advisor.

Given the recently dealt cards, this tool estimates how favorable the
remaining deck is and recommends how much to bet. The "intelligence" here is
classic card-counting math combined with the Kelly criterion for optimal bet
sizing -- the same reasoning professional advantage players use.

How it works
------------
1. Every seen card shifts a *running count* using a chosen counting system
   (Hi-Lo by default). Low cards (good for the player when gone) push the
   count up; high cards push it down.
2. The running count is normalized by the number of decks still in the shoe
   to get the *true count*, which is what actually predicts the edge.
3. The true count is mapped to an estimated *player edge*.
4. The edge and blackjack's variance feed the *Kelly criterion* to size the
   bet, alongside a simpler unit-spread recommendation.

Nothing here beats the house on its own -- it just makes the statistically
optimal decision given the information you provide.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


# --------------------------------------------------------------------------- #
# Card counting systems
# --------------------------------------------------------------------------- #
# Each system assigns an integer weight to every rank. "Balanced" systems sum
# to zero across a full deck and require a true-count conversion; "unbalanced"
# systems (like KO) do not, but we still expose a true count for consistency.
COUNTING_SYSTEMS: Dict[str, Dict[str, object]] = {
    "hi-lo": {
        "balanced": True,
        "weights": {
            "2": 1, "3": 1, "4": 1, "5": 1, "6": 1,
            "7": 0, "8": 0, "9": 0,
            "10": -1, "J": -1, "Q": -1, "K": -1, "A": -1,
        },
    },
    "hi-opt-ii": {
        "balanced": True,
        "weights": {
            "2": 1, "3": 1, "4": 2, "5": 2, "6": 1,
            "7": 1, "8": 0, "9": 0,
            "10": -2, "J": -2, "Q": -2, "K": -2, "A": 0,
        },
    },
    "ko": {
        "balanced": False,
        "weights": {
            "2": 1, "3": 1, "4": 1, "5": 1, "6": 1,
            "7": 1, "8": 0, "9": 0,
            "10": -1, "J": -1, "Q": -1, "K": -1, "A": -1,
        },
    },
    "omega-ii": {
        "balanced": True,
        "weights": {
            "2": 1, "3": 1, "4": 2, "5": 2, "6": 2,
            "7": 1, "8": 0, "9": -1,
            "10": -2, "J": -2, "Q": -2, "K": -2, "A": 0,
        },
    },
    "zen": {
        "balanced": True,
        "weights": {
            "2": 1, "3": 1, "4": 2, "5": 2, "6": 2,
            "7": 1, "8": 0, "9": 0,
            "10": -2, "J": -2, "Q": -2, "K": -2, "A": -1,
        },
    },
}

# Canonical ranks. Face cards and tens all normalize to their point identity;
# suits are irrelevant to counting so we discard them.
_RANK_ALIASES = {
    "1": "A", "A": "A", "ACE": "A",
    "2": "2", "3": "3", "4": "4", "5": "5", "6": "6",
    "7": "7", "8": "8", "9": "9",
    "10": "10", "T": "10", "TEN": "10",
    "J": "J", "JACK": "J",
    "Q": "Q", "QUEEN": "Q",
    "K": "K", "KING": "K",
}


class CardError(ValueError):
    """Raised when a card token cannot be understood."""


def normalize_card(token: str) -> str:
    """Turn a loose card string into a canonical rank.

    Accepts things like ``"AH"``, ``"10"``, ``"Ts"``, ``"queen"``, ``"5"``.
    Suit characters (s/h/d/c and unicode suits) are stripped.
    """
    raw = token.strip().upper()
    if not raw:
        raise CardError("empty card token")

    # Strip a trailing suit if present (e.g. "AH", "10S", "K♣").
    for suit in ("S", "H", "D", "C", "♠", "♥", "♦", "♣"):
        if len(raw) > 1 and raw.endswith(suit):
            raw = raw[: -len(suit)]
            break

    if raw not in _RANK_ALIASES:
        raise CardError(f"unrecognized card: {token!r}")
    return _RANK_ALIASES[raw]


def parse_cards(tokens: Sequence[str]) -> List[str]:
    """Normalize a sequence of card tokens, splitting on commas/whitespace."""
    cards: List[str] = []
    for chunk in tokens:
        for piece in chunk.replace(",", " ").split():
            cards.append(normalize_card(piece))
    return cards


# --------------------------------------------------------------------------- #
# Core evaluation
# --------------------------------------------------------------------------- #
@dataclass
class Recommendation:
    system: str
    cards_seen: int
    running_count: int
    decks_remaining: float
    true_count: float
    edge_pct: float
    unit_bet: float
    kelly_fraction: float
    kelly_bet: float
    advice: str
    breakdown: Dict[str, int] = field(default_factory=dict)

    def format_report(self, bankroll: Optional[float], unit_size: float) -> str:
        lines = [
            "=" * 52,
            "  BLACKJACK BETTING ADVISOR",
            "=" * 52,
            f"  Counting system     : {self.system}",
            f"  Cards observed      : {self.cards_seen}",
            f"  Running count       : {self.running_count:+d}",
            f"  Decks remaining     : {self.decks_remaining:.2f}",
            f"  True count          : {self.true_count:+.2f}",
            f"  Estimated edge      : {self.edge_pct:+.2f}%",
            "-" * 52,
            f"  Recommended units   : {self.unit_bet:.1f}  "
            f"(= {self.unit_bet * unit_size:,.2f} at {unit_size:g}/unit)",
        ]
        if bankroll is not None:
            lines.append(
                f"  Kelly fraction      : {self.kelly_fraction * 100:.2f}% "
                f"of bankroll"
            )
            lines.append(
                f"  Kelly bet           : {self.kelly_bet:,.2f} "
                f"(bankroll {bankroll:,.2f})"
            )
        lines.append("-" * 52)
        lines.append(f"  ADVICE: {self.advice}")
        lines.append("=" * 52)
        return "\n".join(lines)


def running_count(cards: Sequence[str], system: str = "hi-lo") -> int:
    """Sum the counting weights of the given cards."""
    key = system.lower()
    if key not in COUNTING_SYSTEMS:
        raise ValueError(
            f"unknown system {system!r}; choose from {sorted(COUNTING_SYSTEMS)}"
        )
    weights: Dict[str, int] = COUNTING_SYSTEMS[key]["weights"]  # type: ignore
    return sum(weights[c] for c in cards)


def decks_remaining(cards_seen: int, total_decks: float) -> float:
    """Estimate decks left in the shoe, floored so we never divide by ~0."""
    remaining_cards = max(total_decks * 52 - cards_seen, 0.0)
    # Never report less than a quarter deck: past that, true count explodes and
    # a good counter has already left the table (or the shoe is reshuffled).
    return max(remaining_cards / 52.0, 0.25)


def estimate_edge(
    true_count: float,
    base_edge: float = -0.50,
    edge_per_count: float = 0.50,
) -> float:
    """Estimate player edge in percent from the true count.

    Rule of thumb for a typical 6-deck game: the player starts at roughly a
    -0.5% disadvantage and gains about +0.5% for every +1 of true count.
    Both figures are tunable to match a specific rule set.
    """
    return base_edge + edge_per_count * true_count


def unit_spread(true_count: float, max_units: float = 8.0) -> float:
    """Classic betting spread in units, keyed off the true count.

    At or below a true count of +1 you sit at the table minimum (1 unit).
    Above that, bet (true_count - 1) units, capped so you don't get backed
    off for an obvious spread.
    """
    if true_count <= 1:
        return 1.0
    return min(max(round(true_count - 1), 1), max_units)


def kelly(
    edge_pct: float,
    bankroll: float,
    variance: float = 1.32,
    fraction: float = 0.5,
) -> "tuple[float, float]":
    """Kelly-criterion bet sizing.

    ``f* = edge / variance`` gives the optimal fraction of bankroll to wager
    per hand. Blackjack's per-unit variance is ~1.3. Most players use a
    *fractional* Kelly (default: half) to cut bankroll swings. A non-positive
    edge returns a zero bet -- you should be at the table minimum or not
    betting at all.
    """
    edge = edge_pct / 100.0
    if edge <= 0:
        return 0.0, 0.0
    full_fraction = edge / variance
    used_fraction = full_fraction * fraction
    return used_fraction, used_fraction * bankroll


def _advice_text(true_count: float, edge_pct: float) -> str:
    if edge_pct <= 0:
        return "House has the edge -- bet the minimum or sit out."
    if true_count < 2:
        return "Slight player edge -- bet small, stay disciplined."
    if true_count < 4:
        return "Favorable count -- raise your bet."
    return "Strongly favorable -- press your bet toward your spread cap."


def evaluate(
    cards: Sequence[str],
    system: str = "hi-lo",
    total_decks: float = 6.0,
    bankroll: Optional[float] = None,
    unit_size: float = 25.0,
    max_units: float = 8.0,
    kelly_fraction: float = 0.5,
    base_edge: float = -0.50,
    edge_per_count: float = 0.50,
) -> Recommendation:
    """Run the full pipeline and return a :class:`Recommendation`."""
    rc = running_count(cards, system)
    decks = decks_remaining(len(cards), total_decks)
    tc = rc / decks
    edge = estimate_edge(tc, base_edge, edge_per_count)
    units = unit_spread(tc, max_units)

    if bankroll is not None:
        k_frac, k_bet = kelly(edge, bankroll, fraction=kelly_fraction)
    else:
        k_frac, k_bet = 0.0, 0.0

    breakdown: Dict[str, int] = {}
    for c in cards:
        breakdown[c] = breakdown.get(c, 0) + 1

    return Recommendation(
        system=system.lower(),
        cards_seen=len(cards),
        running_count=rc,
        decks_remaining=decks,
        true_count=tc,
        edge_pct=edge,
        unit_bet=units,
        kelly_fraction=k_frac,
        kelly_bet=k_bet,
        advice=_advice_text(tc, edge),
        breakdown=breakdown,
    )


# --------------------------------------------------------------------------- #
# Command-line interface
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blackjack_advisor",
        description="Recommend a blackjack bet from recently dealt cards.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "cards",
        nargs="*",
        help="Recently dealt cards, e.g. 'A 10 5 5 K 2' or '5,6,7'. "
        "Suits are ignored.",
    )
    p.add_argument(
        "-s", "--system",
        default="hi-lo",
        choices=sorted(COUNTING_SYSTEMS),
        help="Card-counting system.",
    )
    p.add_argument(
        "-d", "--decks",
        type=float,
        default=6.0,
        help="Total decks in the shoe.",
    )
    p.add_argument(
        "-b", "--bankroll",
        type=float,
        default=None,
        help="Bankroll, to enable Kelly bet sizing.",
    )
    p.add_argument(
        "-u", "--unit-size",
        type=float,
        default=25.0,
        help="Currency value of one betting unit.",
    )
    p.add_argument(
        "--max-units",
        type=float,
        default=8.0,
        help="Cap on the unit spread.",
    )
    p.add_argument(
        "--kelly-fraction",
        type=float,
        default=0.5,
        help="Fraction of full Kelly to use (0.5 = half Kelly).",
    )
    p.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Feed cards one at a time and watch the count evolve.",
    )
    return p


def _run_interactive(args: argparse.Namespace) -> int:
    print("Interactive mode. Enter cards (e.g. 'A', '10', 'K'); "
          "'reset' to clear, 'quit' to exit.\n")
    seen: List[str] = list(parse_cards(args.cards)) if args.cards else []
    while True:
        try:
            line = input("card> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        low = line.lower()
        if low in ("quit", "exit", "q"):
            return 0
        if low in ("reset", "clear"):
            seen.clear()
            print("(count reset)\n")
            continue
        try:
            seen.extend(parse_cards([line]))
        except CardError as exc:
            print(f"  ! {exc}")
            continue
        rec = evaluate(
            seen,
            system=args.system,
            total_decks=args.decks,
            bankroll=args.bankroll,
            unit_size=args.unit_size,
            max_units=args.max_units,
            kelly_fraction=args.kelly_fraction,
        )
        print(rec.format_report(args.bankroll, args.unit_size))
        print()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.interactive:
        return _run_interactive(args)

    if not args.cards:
        print("No cards provided. Pass cards as arguments or use "
              "--interactive.\n", file=sys.stderr)
        _build_parser().print_help(sys.stderr)
        return 2

    try:
        cards = parse_cards(args.cards)
    except CardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rec = evaluate(
        cards,
        system=args.system,
        total_decks=args.decks,
        bankroll=args.bankroll,
        unit_size=args.unit_size,
        max_units=args.max_units,
        kelly_fraction=args.kelly_fraction,
    )
    print(rec.format_report(args.bankroll, args.unit_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
