#!/usr/bin/env python3
"""Train the neural-network betting policy and save its weights.

    python train_nn.py                 # train with defaults, save nn_weights.npz
    python train_nn.py --generations 40 --output nn_weights.npz
"""

from __future__ import annotations

import argparse

import numpy as np

from blackjack_sim.neural import BettingNet, train


def main() -> int:
    p = argparse.ArgumentParser(description="Train the blackjack betting net.")
    p.add_argument("--output", default="nn_weights.npz", help="Weights file.")
    p.add_argument("--generations", type=int, default=30)
    p.add_argument("--population", type=int, default=20)
    # A high shoes-per-eval is needed to resolve the (small) betting-ramp
    # signal above the noise of a stochastic card game; too few and the net
    # correctly but unhelpfully settles on flat minimum betting.
    p.add_argument("--shoes-per-eval", type=int, default=500)
    p.add_argument("--hidden", type=int, default=8)
    p.add_argument("--sigma", type=float, default=0.2)
    p.add_argument("--lr", type=float, default=0.25)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    net = BettingNet(hidden=args.hidden, rng=np.random.default_rng(args.seed))
    print(f"Training betting net ({args.generations} generations, "
          f"population {args.population})...")
    train(
        net,
        generations=args.generations,
        population=args.population,
        sigma=args.sigma,
        lr=args.lr,
        shoes_per_eval=args.shoes_per_eval,
        seed=args.seed,
    )
    net.save(args.output)
    print(f"\nSaved trained weights to {args.output}")

    # Show what the learned policy does across the count range. Bets are shown
    # in table-minimum units assuming a bankroll of 100 units (e.g. $1000/$10),
    # with the forced 1-unit minimum applied.
    from blackjack_sim.neural import features, DEFAULT_MIN_FRACTION
    print("\nLearned bet ramp vs. true count (mid-shoe, $1000 bankroll / $10 min):")
    for tc in (-5, -2, 0, 1, 2, 3, 5, 8, 10):
        f = max(net.bet_fraction(features(tc, 3.0, 1.0)), DEFAULT_MIN_FRACTION)
        units = f / DEFAULT_MIN_FRACTION
        bar = "#" * int(units * 3)
        print(f"  TC {tc:+d}: {units:4.1f} units ({f*100:4.1f}% of bankroll)  {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
