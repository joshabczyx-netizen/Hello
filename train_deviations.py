#!/usr/bin/env python3
"""Train the neural playing-deviation net and validate what it learned.

    python train_deviations.py                 # ~3-5 min, saves play_weights.npz
    python train_deviations.py --generations 45 --samples-per-eval 6000
"""

from __future__ import annotations

import argparse
import random

import numpy as np

from blackjack_sim.deviations import (
    PlayNet,
    basic_stand,
    build_dataset,
    make_override,
    train_supervised,
)
from blackjack_sim.engine import Shoe, play_round


def print_decision_grids(net: PlayNet) -> None:
    """Show the actual (margin-gated) play; '*' marks a deviation from basic."""
    override = make_override(net)
    ups = list(range(2, 12))
    header = "      " + "".join(f"{('A' if u == 11 else u)!s:>4}" for u in ups)
    for tc in (-5, 0, 5):
        print(f"\n  Play grid at true count {tc:+d} ('*' = deviation from basic):")
        print(header)
        for total in range(16, 11, -1):
            row = f"  {total:>3}: "
            for u in ups:
                forced = override(["10", str(total - 10)], u, tc)
                if forced is None:
                    act = "S" if basic_stand(total, u) else "H"
                    mark = " "
                else:
                    act = forced
                    mark = "*"
                row += f"{act + mark:>4}"
            print(row)


def print_learned_indices(net: PlayNet) -> None:
    """For each hand, the true-count threshold where the gated play deviates."""
    override = make_override(net)
    print("\n  Learned deviation indices (true count where play leaves basic):")
    print("      hand      deviation        basic play")
    for total, up in [(16, 9), (16, 10), (15, 10), (12, 2), (12, 3),
                      (13, 2), (12, 4), (12, 6)]:
        cards = ["10", str(total - 10)]
        idx = None
        base_is_stand = basic_stand(total, up)
        for tc in range(-10, 11):
            forced = override(cards, up, tc)
            if forced is not None:
                idx = tc
                break
        base = "stand" if base_is_stand else "hit"
        if idx is None:
            dev_s = "(none)"
        else:
            new_act = "stand" if not base_is_stand else "hit"
            dev_s = f"{new_act} at TC{'>=' if not base_is_stand else '<='}{idx}"
        up_s = "A" if up == 11 else str(up)
        print(f"    {total} vs {up_s:<3}   {dev_s:<18} {base}")


def measure_ev_gain(net: PlayNet, rounds: int = 300_000, seed: int = 123) -> None:
    """Flat-bet EV with vs. without deviations, over identical shoes."""
    override = make_override(net)
    base_total = dev_total = 0.0
    rng_seed = random.Random(seed).randrange(2**31)
    shoe_a = Shoe(rng=random.Random(rng_seed))
    shoe_b = Shoe(rng=random.Random(rng_seed))  # identical shuffle sequence
    for _ in range(rounds):
        if shoe_a.needs_shuffle():
            shoe_a.shuffle()
            shoe_b.shuffle()
        base_total += play_round(shoe_a, 1.0)
        dev_total += play_round(shoe_b, 1.0, play_override=override)
    b = 100 * base_total / rounds
    d = 100 * dev_total / rounds
    print(f"\n  Flat-bet edge over {rounds:,} identical hands:")
    print(f"    basic strategy      : {b:+.3f}% per unit")
    print(f"    with deviations     : {d:+.3f}% per unit")
    print(f"    improvement         : {d - b:+.3f}%")


def main() -> int:
    p = argparse.ArgumentParser(description="Train the playing-deviation net.")
    p.add_argument("--output", default="play_weights.npz")
    p.add_argument("--samples", type=int, default=300_000,
                   help="Paired-CRN simulation samples for the training set.")
    p.add_argument("--epochs", type=int, default=120)
    p.add_argument("--hidden", type=int, default=16)
    p.add_argument("--lr", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    net = PlayNet(hidden=args.hidden, rng=np.random.default_rng(args.seed))
    print(f"Building dataset ({args.samples:,} paired-CRN samples)...")
    X, advantage = build_dataset(args.samples, seed=args.seed)
    print(f"Training playing-deviation net ({args.epochs} epochs)...")
    train_supervised(net, X, advantage, epochs=args.epochs, lr=args.lr,
                     seed=args.seed)
    net.save(args.output)
    print(f"\nSaved trained weights to {args.output}")

    print_decision_grids(net)
    print_learned_indices(net)
    measure_ev_gain(net)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
