"""Betting agents. Every agent plays basic strategy; they differ only in how
much they bet, which is the entire subject of the experiment."""

from __future__ import annotations

from typing import Optional

from blackjack_advisor import unit_spread
from .engine import Shoe, Table
from .neural import BettingNet, features


class Agent:
    """Base class: holds a bankroll and decides a bet from the shoe state."""

    def __init__(self, name: str, bankroll: float, play_override=None) -> None:
        self.name = name
        self.bankroll = bankroll
        self.start_bankroll = bankroll
        self.busted_at: Optional[int] = None  # round index of ruin, if any
        # Optional count-based playing deviations; None => basic strategy.
        self.play_override = play_override

    @property
    def alive(self) -> bool:
        return self.busted_at is None

    def _clamp(self, bet: float, table: Table) -> float:
        cap = min(table.max_bet, self.bankroll)
        return max(min(bet, cap), table.min_bet)

    def bet(self, shoe: Shoe, table: Table) -> float:  # pragma: no cover
        raise NotImplementedError


class CounterAgent(Agent):
    """Perfect card counter using the classic count-based unit ramp.

    Bets one unit (the table minimum) when the shoe is neutral or cold, and
    ramps up to ``max_units`` as the true count climbs -- the standard betting
    spread a real counter with a modest bankroll uses.
    """

    def __init__(
        self,
        name: str,
        bankroll: float,
        max_units: float = 8.0,
        play_override=None,
    ) -> None:
        super().__init__(name, bankroll, play_override)
        self.max_units = max_units

    def bet(self, shoe: Shoe, table: Table) -> float:
        units = unit_spread(shoe.true_count(), max_units=self.max_units)
        return self._clamp(units * table.min_bet, table)


class NeuralAgent(Agent):
    """Bets according to a trained :class:`BettingNet` policy."""

    def __init__(self, name: str, bankroll: float, net: BettingNet,
                 play_override=None) -> None:
        super().__init__(name, bankroll, play_override)
        self.net = net

    def bet(self, shoe: Shoe, table: Table) -> float:
        feats = features(
            shoe.true_count(),
            shoe.decks_remaining(),
            self.bankroll / self.start_bankroll,
        )
        stake = self.net.bet_fraction(feats) * self.bankroll
        # _clamp floors at the table minimum, enforcing the forced minimum bet.
        return self._clamp(stake, table)


class FlatAgent(Agent):
    """Baseline: always bets the table minimum (no counting)."""

    def bet(self, shoe: Shoe, table: Table) -> float:
        return table.min_bet
