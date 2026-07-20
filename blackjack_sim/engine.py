"""A compact but faithful blackjack engine.

Rules modeled (a common Vegas shoe game):
  * 6-deck shoe, reshuffled at ~75% penetration, random shuffle.
  * Dealer stands on all 17 (S17).
  * Blackjack pays 3:2.
  * Player may double any first two cards, and double after split (DAS).
  * Split up to 4 hands; split aces get exactly one card each.
  * Dealer peeks for blackjack on a ten/ace upcard (US rules).
  * No surrender, no insurance (never +EV without a count deviation).

All players play textbook basic strategy for *playing* decisions; the whole
point of the simulation is to compare *betting* decisions, which is where card
counting (and a learned policy) actually matters.

The shoe keeps a running Hi-Lo count of every card it deals, so any agent can
ask it for a perfect true count of all cards dealt so far.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Reuse the counting weights from the advisor so the two tools never drift.
from blackjack_advisor import COUNTING_SYSTEMS

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
CARD_VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11,
}
_HILO = COUNTING_SYSTEMS["hi-lo"]["weights"]  # type: ignore


# --------------------------------------------------------------------------- #
# Hand evaluation
# --------------------------------------------------------------------------- #
def hand_total(cards: List[str]) -> Tuple[int, bool]:
    """Return (best total, is_soft). Soft means an ace still counts as 11."""
    total = sum(CARD_VALUES[c] for c in cards)
    aces = cards.count("A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def is_blackjack(cards: List[str]) -> bool:
    return len(cards) == 2 and hand_total(cards)[0] == 21


def _upcard_value(card: str) -> int:
    return CARD_VALUES[card]


# --------------------------------------------------------------------------- #
# Basic strategy (4-8 deck, S17, DAS)
# --------------------------------------------------------------------------- #
def _hard_action(total: int, up: int) -> str:
    if total >= 17:
        return "S"
    if total <= 8:
        return "H"
    if total == 9:
        return "D" if 3 <= up <= 6 else "H"
    if total == 10:
        return "D" if up <= 9 else "H"
    if total == 11:
        return "D" if up <= 10 else "H"
    if total == 12:
        return "S" if 4 <= up <= 6 else "H"
    return "S" if up <= 6 else "H"  # 13-16


def _soft_action(total: int, up: int) -> str:
    if total >= 19:
        return "S"
    if total == 18:  # A,7
        if up in (2, 7, 8):
            return "S"
        if 3 <= up <= 6:
            return "Ds"
        return "H"
    if total == 17:
        return "D" if 3 <= up <= 6 else "H"
    if total in (15, 16):
        return "D" if 4 <= up <= 6 else "H"
    if total in (13, 14):
        return "D" if 5 <= up <= 6 else "H"
    return "H"


def _should_split(value: int, up: int) -> bool:
    if value == 11:  # aces
        return True
    if value == 10:
        return False
    if value == 9:
        return up in (2, 3, 4, 5, 6, 8, 9)
    if value == 8:
        return True
    if value == 7:
        return up <= 7
    if value == 6:
        return 2 <= up <= 6
    if value == 5:
        return False  # treat as hard 10
    if value == 4:
        return up in (5, 6)
    if value in (2, 3):
        return up <= 7
    return False


def basic_strategy(
    cards: List[str],
    up: int,
    can_double: bool,
    can_split: bool,
) -> str:
    """Return one of 'H', 'S', 'D' (double), 'P' (split)."""
    if (
        can_split
        and len(cards) == 2
        and CARD_VALUES[cards[0]] == CARD_VALUES[cards[1]]
        and _should_split(CARD_VALUES[cards[0]], up)
    ):
        return "P"

    total, soft = hand_total(cards)
    action = _soft_action(total, up) if soft else _hard_action(total, up)

    if action == "D":
        return "D" if can_double else "H"
    if action == "Ds":
        return "D" if can_double else "S"
    return action


# --------------------------------------------------------------------------- #
# Shoe
# --------------------------------------------------------------------------- #
class Shoe:
    """A multi-deck shoe that tracks a perfect Hi-Lo running count."""

    def __init__(
        self,
        num_decks: int = 6,
        penetration: float = 0.75,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.num_decks = num_decks
        self.penetration = penetration
        self.rng = rng or random.Random()
        self.cards: List[str] = []
        self.pos = 0
        self.running_count = 0
        self._cut = 0
        self.shuffle()

    def shuffle(self) -> None:
        self.cards = [r for r in RANKS for _ in range(4 * self.num_decks)]
        self.rng.shuffle(self.cards)
        self.pos = 0
        self.running_count = 0
        self._cut = int(len(self.cards) * self.penetration)

    def needs_shuffle(self) -> bool:
        return self.pos >= self._cut

    def draw(self) -> str:
        if self.pos >= len(self.cards):
            self.shuffle()
        card = self.cards[self.pos]
        self.pos += 1
        self.running_count += _HILO[card]
        return card

    def decks_remaining(self) -> float:
        return max((len(self.cards) - self.pos) / 52.0, 0.25)

    def true_count(self) -> float:
        return self.running_count / self.decks_remaining()


@dataclass
class Table:
    min_bet: float = 10.0
    max_bet: float = 500.0


# --------------------------------------------------------------------------- #
# Round play
# --------------------------------------------------------------------------- #
def _play_player_hand(
    cards: List[str],
    bet: float,
    shoe: Shoe,
    up: int,
    splits_done: int,
    from_split_ace: bool,
) -> List[Tuple[List[str], float, bool]]:
    """Play one player hand to completion, returning (cards, bet, busted)."""
    # Split aces receive exactly one card and cannot act further.
    if from_split_ace:
        return [(cards, bet, hand_total(cards)[0] > 21)]

    while True:
        total, _ = hand_total(cards)
        if total > 21:
            return [(cards, bet, True)]

        can_double = len(cards) == 2
        can_split = len(cards) == 2 and splits_done < 3
        action = basic_strategy(cards, up, can_double, can_split)

        if action == "S":
            return [(cards, bet, False)]
        if action == "H":
            cards = cards + [shoe.draw()]
            continue
        if action == "D":
            cards = cards + [shoe.draw()]
            return [(cards, bet * 2, hand_total(cards)[0] > 21)]
        if action == "P":
            is_ace = cards[0] == "A"
            h1 = [cards[0], shoe.draw()]
            h2 = [cards[1], shoe.draw()]
            out: List[Tuple[List[str], float, bool]] = []
            out += _play_player_hand(h1, bet, shoe, up, splits_done + 1, is_ace)
            out += _play_player_hand(h2, bet, shoe, up, splits_done + 1, is_ace)
            return out


def play_round(shoe: Shoe, base_bet: float) -> float:
    """Play one full round for a single player and return net profit.

    Profit is expressed in the same units as ``base_bet`` (so passing 1.0
    yields the profit *multiple* per unit staked). Blackjack pays 3:2.
    """
    player = [shoe.draw(), shoe.draw()]
    dealer = [shoe.draw(), shoe.draw()]  # dealer[1] is the hole card
    up = _upcard_value(dealer[0])

    player_bj = is_blackjack(player)
    dealer_bj = is_blackjack(dealer)

    # Dealer peeks on ten/ace up.
    if up in (10, 11) and dealer_bj:
        return 0.0 if player_bj else -base_bet
    if player_bj:
        return 1.5 * base_bet

    hands = _play_player_hand(player, base_bet, shoe, up, 0, False)

    all_busted = all(busted for _, _, busted in hands)
    if not all_busted:
        while True:
            dt, _ = hand_total(dealer)
            if dt < 17:
                dealer.append(shoe.draw())
            else:
                break

    dealer_total, _ = hand_total(dealer)
    dealer_bust = dealer_total > 21

    profit = 0.0
    for cards, bet, busted in hands:
        if busted:
            profit -= bet
            continue
        ptotal, _ = hand_total(cards)
        if dealer_bust or ptotal > dealer_total:
            profit += bet
        elif ptotal < dealer_total:
            profit -= bet
        # equal totals push
    return profit
