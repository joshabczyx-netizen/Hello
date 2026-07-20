"""A small neural network that learns a blackjack *betting* policy.

The network maps a game-state feature vector to a bet size as a **fraction of
the current bankroll** (Kelly-style, so it scales correctly with any bankroll).
A real player is forced to bet at least the table minimum every hand, so during
both training and play the staked fraction is floored at ``min_fraction``. The
only thing the network really controls is *how much above the minimum to bet
when the count is favorable* -- which is exactly the card counter's decision.

Training uses Evolution Strategies (ES), a gradient-free optimizer, against the
Kelly objective (mean log-growth of bankroll per hand). Under that objective the
optimal policy bets more when the edge is higher, so a well-trained net should
rediscover the count-based bet ramp from scratch.

The net is initialized *pessimistically* (betting near the floor everywhere) so
ES starts from "flat minimum" and only has to discover where raising the bet
pays off -- a clean, one-directional learning signal.
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np

from .engine import Shoe, play_round

# Cap on the fraction of bankroll staked on one hand. Keeps log-growth finite
# (a fully resplit-and-doubled loss is ~ -8 units) and the policy sane.
MAX_BET_FRACTION = 0.10
# Forced table minimum as a fraction of a reference bankroll: a $1000 bankroll
# at a $10 table means every hand bets at least 1% of bankroll.
DEFAULT_MIN_FRACTION = 0.01


def features(true_count: float, decks_remaining: float, bankroll_ratio: float) -> np.ndarray:
    """Normalize raw game state into a bounded feature vector."""
    return np.array(
        [
            np.clip(true_count / 5.0, -3.0, 3.0),
            decks_remaining / 6.0,
            np.clip(bankroll_ratio, 0.0, 3.0) / 3.0,
        ],
        dtype=np.float64,
    )


class BettingNet:
    """A tiny feed-forward net: features -> hidden (tanh) -> bet fraction."""

    def __init__(
        self,
        hidden: int = 8,
        n_features: int = 3,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        self.n_features = n_features
        self.hidden = hidden
        g = rng or np.random.default_rng()
        self.w1 = g.standard_normal((n_features, hidden)) * 0.3
        self.b1 = np.zeros(hidden)
        self.w2 = g.standard_normal((hidden, 1)) * 0.3
        # Pessimistic bias: sigmoid(-4) ~ 0.018, so the net starts near the
        # betting floor everywhere and learns to raise from there.
        self.b2 = np.array([-4.0])

    # -- inference -------------------------------------------------------- #
    def bet_fraction(self, feats: np.ndarray) -> float:
        """Return the bet size as a fraction of bankroll, in [0, MAX_BET_FRACTION]."""
        h = np.tanh(feats @ self.w1 + self.b1)
        out = float((h @ self.w2 + self.b2)[0])
        return 1.0 / (1.0 + math.exp(-out)) * MAX_BET_FRACTION

    # -- flat parameter vector (for ES) ----------------------------------- #
    def get_params(self) -> np.ndarray:
        return np.concatenate(
            [self.w1.ravel(), self.b1, self.w2.ravel(), self.b2]
        )

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
    def load(cls, path: str) -> "BettingNet":
        data = np.load(path)
        net = cls(hidden=data["b1"].shape[0], n_features=data["w1"].shape[0])
        net.w1, net.b1, net.w2, net.b2 = (
            data["w1"], data["b1"], data["w2"], data["b2"]
        )
        return net


# --------------------------------------------------------------------------- #
# Evolution-strategies training
# --------------------------------------------------------------------------- #
def _evaluate(
    theta: np.ndarray,
    net: BettingNet,
    shoes: int,
    seed: int,
    min_fraction: float,
) -> float:
    """Mean log-growth per hand for a policy, over `shoes` fresh shoes.

    Every hand stakes at least `min_fraction` of bankroll (the forced table
    minimum); the net can only raise the bet above that floor.
    """
    net.set_params(theta)
    rng = __import__("random").Random(seed)
    total_log = 0.0
    hands = 0
    for _ in range(shoes):
        shoe = Shoe(rng=rng)
        shoe.shuffle()
        while not shoe.needs_shuffle():
            feats = features(shoe.true_count(), shoe.decks_remaining(), 1.0)
            f = max(net.bet_fraction(feats), min_fraction)
            m = play_round(shoe, 1.0)  # profit per unit staked
            total_log += math.log(max(1.0 + f * m, 1e-6))
            hands += 1
    return total_log / max(hands, 1)


def train(
    net: BettingNet,
    generations: int = 30,
    population: int = 24,
    sigma: float = 0.2,
    lr: float = 0.2,
    shoes_per_eval: int = 120,
    min_fraction: float = DEFAULT_MIN_FRACTION,
    seed: int = 0,
    verbose: bool = True,
) -> List[float]:
    """Train `net` in place with Evolution Strategies.

    Uses antithetic sampling and rank-based fitness shaping for stability.
    Returns the best mean-log-growth achieved at each generation.
    """
    rng = np.random.default_rng(seed)
    theta = net.get_params()
    n = theta.size
    half = population // 2
    history: List[float] = []

    for gen in range(generations):
        eps = rng.standard_normal((half, n))
        eps = np.concatenate([eps, -eps], axis=0)  # antithetic pairs

        eval_seed = 1000 + gen  # common random numbers within a generation
        fitness = np.array([
            _evaluate(theta + sigma * eps[i], net, shoes_per_eval,
                      eval_seed, min_fraction)
            for i in range(population)
        ])

        ranks = np.empty_like(fitness)
        ranks[np.argsort(fitness)] = np.arange(population)
        shaped = ranks / (population - 1) - 0.5  # rank-normalized reward

        grad = (eps.T @ shaped) / (population * sigma)
        theta = theta + lr * grad

        best = float(fitness.max())
        history.append(best)
        if verbose:
            net.set_params(theta)
            center = _evaluate(theta, net, shoes_per_eval, eval_seed,
                               min_fraction)
            print(f"  gen {gen + 1:2d}/{generations}  "
                  f"best={best:+.6f}  center={center:+.6f}")

    net.set_params(theta)
    return history
