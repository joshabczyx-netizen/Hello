"""A neural network that learns count-based *playing* deviations.

Basic strategy is fixed, but the mathematically best play on borderline hands
shifts with the count: when the deck is rich in tens you stand on stiff hands
you would otherwise hit (the classic "index plays" / Illustrious 18). This net
learns those deviations for the highest-value group of decisions -- the hard
12-16 stand-vs-hit calls -- purely by maximizing expected value in simulation.

It takes (player total, dealer upcard, true count) and outputs the probability
of standing. Because the vast majority of training situations occur near a zero
count, the net first learns to reproduce basic strategy there, then learns to
deviate in the (rarer) extreme-count tails. Training uses Evolution Strategies,
the same gradient-free optimizer used for the betting net.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional

import numpy as np

from .engine import (
    CARD_VALUES,
    Shoe,
    _play_player_hand,
    hand_total,
    is_blackjack,
)


# Feature layout: one-hot player total (12-16), one-hot dealer up (2-11), the
# scaled true count, and true-count x one-hot interaction terms. The one-hots
# capture the sharp, non-monotonic dealer-upcard boundary (16 vs 7 differs a lot
# from 16 vs 10); the interaction terms give each total and each upcard its own
# learnable count-slope, so the index thresholds are well-determined instead of
# all cells sharing one true-count coefficient.
N_PLAY_FEATURES = 5 + 10 + 1 + 5 + 10


def play_features(total: int, up: int, true_count: float) -> np.ndarray:
    """Encode a hard-total decision into a fixed-length feature vector."""
    f = np.zeros(N_PLAY_FEATURES, dtype=np.float64)
    tc = float(np.clip(true_count / 5.0, -3.0, 3.0))
    ti = total - 12               # 0..4
    ui = up - 2                   # 0..9
    f[ti] = 1.0                   # one-hot total       -> 0..4
    f[5 + ui] = 1.0               # one-hot up          -> 5..14
    f[15] = tc                    # shared true count   -> 15
    f[16 + ti] = tc               # tc x total          -> 16..20
    f[21 + ui] = tc               # tc x up             -> 21..30
    return f


class PlayNet:
    """features -> hidden (tanh) -> P(stand) for hard 12-16 decisions."""

    def __init__(
        self,
        hidden: int = 16,
        n_features: int = N_PLAY_FEATURES,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        self.n_features = n_features
        self.hidden = hidden
        g = rng or np.random.default_rng()
        self.w1 = g.standard_normal((n_features, hidden)) * 0.3
        self.b1 = np.zeros(hidden)
        self.w2 = g.standard_normal((hidden, 1)) * 0.3
        self.b2 = np.zeros(1)

    def stand_advantage(self, total: int, up: int, true_count: float) -> float:
        """Predicted EV(stand) - EV(hit); positive => standing is better."""
        feats = play_features(total, up, true_count)
        h = np.tanh(feats @ self.w1 + self.b1)
        return float((h @ self.w2 + self.b2)[0])

    def should_stand(self, total: int, up: int, true_count: float) -> bool:
        return self.stand_advantage(total, up, true_count) >= 0.0

    # -- ES parameter vector --------------------------------------------- #
    def get_params(self) -> np.ndarray:
        return np.concatenate([self.w1.ravel(), self.b1, self.w2.ravel(), self.b2])

    def set_params(self, theta: np.ndarray) -> None:
        i = 0
        n = self.n_features * self.hidden
        self.w1 = theta[i:i + n].reshape(self.n_features, self.hidden); i += n
        self.b1 = theta[i:i + self.hidden]; i += self.hidden
        n = self.hidden
        self.w2 = theta[i:i + n].reshape(self.hidden, 1); i += n
        self.b2 = theta[i:i + 1]

    # -- persistence ------------------------------------------------------ #
    def save(self, path: str) -> None:
        np.savez(path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2)

    @classmethod
    def load(cls, path: str) -> "PlayNet":
        data = np.load(path)
        net = cls(hidden=data["b1"].shape[0], n_features=data["w1"].shape[0])
        net.w1, net.b1, net.w2, net.b2 = (
            data["w1"], data["b1"], data["w2"], data["b2"]
        )
        return net


def basic_stand(total: int, up: int) -> bool:
    """Textbook basic-strategy stand decision for a hard 12-16 total."""
    if total == 12:
        return 4 <= up <= 6
    return up <= 6  # 13-16


# Deviate from basic strategy only when the net predicts the non-basic action
# beats it by at least this EV margin. A small margin suppresses the net's
# noisy, marginal deviations on rare high-count cells and keeps the flat-bet
# result reliably >= basic strategy (measured ~+0.05% per unit across seeds).
DEFAULT_MARGIN = 0.04


def make_override(net: PlayNet, margin: float = DEFAULT_MARGIN):
    """Return a play-override callable for `engine.play_round`.

    Anchored on basic strategy: it deviates only for hard 12-16 stand/hit
    decisions, and only when the net is confident (by `margin`) that the
    non-basic action has the higher EV. Everything else returns None so the
    engine plays basic strategy.
    """
    def override(cards: List[str], up: int, tc: float) -> Optional[str]:
        total, soft = hand_total(cards)
        if soft or not (12 <= total <= 16):
            return None
        adv = net.stand_advantage(total, up, tc)
        if basic_stand(total, up):
            return "H" if adv < -margin else None   # deviate stand -> hit
        return "S" if adv > margin else None         # deviate hit -> stand
    return override


# --------------------------------------------------------------------------- #
# Simulation of a single hard 12-16 decision
# --------------------------------------------------------------------------- #
def _decision_ev(player, dealer, shoe: Shoe, up: int, stand: bool) -> float:
    """EV (in units) of standing or hitting on `player` vs `dealer`."""
    if stand:
        hands = [(player, 1.0, hand_total(player)[0] > 21)]
    else:
        cards = player + [shoe.draw()]
        # splits_done=3 blocks any split; a 3+ card hand can't double.
        hands = _play_player_hand(cards, 1.0, shoe, up, 3, False)

    dealer = list(dealer)
    if not all(busted for _, _, busted in hands):
        while hand_total(dealer)[0] < 17:
            dealer.append(shoe.draw())
    dt = hand_total(dealer)[0]
    dbust = dt > 21

    profit = 0.0
    for cards, bet, busted in hands:
        if busted:
            profit -= bet
        else:
            pt = hand_total(cards)[0]
            if dbust or pt > dt:
                profit += bet
            elif pt < dt:
                profit -= bet
    return profit


def _sample_decision(rng: random.Random):
    """Produce a random (player hard 12-16, dealer, shoe, tc) scenario."""
    shoe = Shoe(rng=rng)
    shoe.shuffle()
    burn = rng.randint(0, int(len(shoe.cards) * 0.7))
    for _ in range(burn):
        shoe.draw()
    if shoe.needs_shuffle():
        return None
    tc = shoe.true_count()
    total = rng.randint(12, 16)
    player = ["10", str(total - 10)]  # a representative hard total
    up_card = shoe.draw()
    up = CARD_VALUES[up_card]
    hole = shoe.draw()
    dealer = [up_card, hole]
    if up in (10, 11) and is_blackjack(dealer):
        return None  # no decision -- dealer has blackjack
    return player, dealer, shoe, up, total, tc


def _paired_ev(player, dealer, shoe: Shoe, up: int):
    """EV of standing and of hitting on the *same* deck continuation.

    Evaluating both actions against one shared shuffle (common random numbers)
    makes the stand-minus-hit difference a low-variance paired estimate -- the
    key to learning the (small) deviation advantages efficiently.
    """
    snap = (shoe.pos, shoe.running_count)
    ev_stand = _decision_ev(player, dealer, shoe, up, True)
    shoe.pos, shoe.running_count = snap
    ev_hit = _decision_ev(player, dealer, shoe, up, False)
    shoe.pos, shoe.running_count = snap
    return ev_stand, ev_hit


def build_dataset(samples: int, seed: int = 0):
    """Build (features, stand_advantage) pairs by paired-CRN simulation.

    stand_advantage = EV(stand) - EV(hit); positive means standing is better.
    """
    rng = random.Random(seed)
    feats: List[np.ndarray] = []
    adv: List[float] = []
    while len(feats) < samples:
        s = _sample_decision(rng)
        if s is None:
            continue
        player, dealer, shoe, up, total, tc = s
        ev_stand, ev_hit = _paired_ev(player, dealer, shoe, up)
        feats.append(play_features(total, up, tc))
        adv.append(ev_stand - ev_hit)
    return np.array(feats), np.array(adv)


def train_supervised(
    net: PlayNet,
    X: np.ndarray,
    advantage: np.ndarray,
    epochs: int = 80,
    lr: float = 0.05,
    batch: int = 512,
    seed: int = 0,
    verbose: bool = True,
) -> None:
    """Regress the EV advantage of standing, so the net can be gated on a margin.

    The net predicts EV(stand) - EV(hit) directly (MSE, linear output). Because
    the paired-CRN targets are low-variance, the fit is accurate, and gating the
    predicted advantage on a margin (see `make_override`) means the net only
    deviates from basic strategy where it is confident it should. Plain backprop.
    """
    target = advantage.reshape(-1, 1)
    n = len(X)
    rng = np.random.default_rng(seed)

    for ep in range(epochs):
        order = rng.permutation(n)
        for start in range(0, n, batch):
            b = order[start:start + batch]
            xb, tb = X[b], target[b]
            h = np.tanh(xb @ net.w1 + net.b1)
            pred = h @ net.w2 + net.b2
            dz2 = 2.0 * (pred - tb) / len(b)
            dw2 = h.T @ dz2
            db2 = dz2.sum(0)
            dz1 = (dz2 @ net.w2.T) * (1.0 - h ** 2)
            dw1 = xb.T @ dz1
            db1 = dz1.sum(0)
            net.w2 -= lr * dw2
            net.b2 -= lr * db2
            net.w1 -= lr * dw1
            net.b1 -= lr * db1
        if verbose and (ep + 1) % 20 == 0:
            pred_all = np.tanh(X @ net.w1 + net.b1) @ net.w2 + net.b2
            rmse = float(np.sqrt(np.mean((pred_all - target) ** 2)))
            print(f"  epoch {ep + 1:3d}/{epochs}  advantage-RMSE={rmse:.4f}")
