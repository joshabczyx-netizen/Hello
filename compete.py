#!/usr/bin/env python3
"""Pit 4 perfect-counting rule-based agents against the neural-net agent.

Everyone starts with the same bankroll and plays the same number of rounds on
their own independently shuffled shoe. If a trained weights file is missing, a
fresh (untrained) net is used and a warning is printed -- run train_nn.py first
for a meaningful neural competitor.

    python compete.py                       # default 1000 rounds, $1000 each
    python compete.py --rounds 2000 --seed 7 --chart bankrolls.svg
"""

from __future__ import annotations

import argparse
import os

from blackjack_sim.agents import CounterAgent, NeuralAgent
from blackjack_sim.engine import Table
from blackjack_sim.neural import BettingNet
from blackjack_sim.tournament import (
    format_standings,
    run_tournament,
    run_trials,
    summarize_group,
)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Card counters vs. a neural-net bettor.")
    p.add_argument("--rounds", type=int, default=1000)
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--counters", type=int, default=4)
    p.add_argument("--weights", default="nn_weights.npz")
    p.add_argument("--play-weights", default="play_weights.npz",
                   help="Deviation-net weights, used when --deviations is set.")
    p.add_argument("--deviations", action="store_true",
                   help="All agents use the learned count-based playing "
                        "deviations instead of pure basic strategy.")
    p.add_argument("--min-bet", type=float, default=10.0)
    p.add_argument("--max-bet", type=float, default=500.0)
    p.add_argument("--max-units", type=float, default=8.0,
                   help="Counter betting-spread cap, in table-minimum units.")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--trials", type=int, default=1,
                   help="Run this many independent sessions and aggregate "
                        "(the strategy difference only shows up across many).")
    p.add_argument("--chart", default=None,
                   help="Write an SVG bankroll chart to this path.")
    p.add_argument("--csv", default=None,
                   help="Write per-round bankroll trajectories to this CSV.")
    args = p.parse_args()

    if os.path.exists(args.weights):
        net = BettingNet.load(args.weights)
        print(f"Loaded trained betting net from {args.weights}")
    else:
        net = BettingNet()
        print(f"! {args.weights} not found -- using an UNTRAINED net. "
              f"Run 'python train_nn.py' first for a real competitor.")

    override = None
    if args.deviations:
        if os.path.exists(args.play_weights):
            from blackjack_sim.deviations import PlayNet, make_override
            override = make_override(PlayNet.load(args.play_weights))
            print(f"Using learned playing deviations from {args.play_weights}")
        else:
            print(f"! {args.play_weights} not found -- run "
                  f"'python train_deviations.py'. Using basic strategy.")

    def make_agents():
        agents = [
            CounterAgent(f"Counter-{i+1}", args.bankroll,
                         max_units=args.max_units, play_override=override)
            for i in range(args.counters)
        ]
        agents.append(NeuralAgent("NeuralNet", args.bankroll, net,
                                  play_override=override))
        return agents

    table = Table(min_bet=args.min_bet, max_bet=args.max_bet)

    if args.trials > 1:
        summary = run_trials(make_agents, trials=args.trials,
                             rounds=args.rounds, table=table, seed=args.seed)
        print()
        print(summary.format(args.bankroll))
        return 0

    result = run_tournament(make_agents(), rounds=args.rounds, table=table,
                            seed=args.seed)

    print()
    print(format_standings(result))
    print()
    counters = [r for r in result.results if r.name.startswith("Counter")]
    nn = next(r for r in result.results if r.name == "NeuralNet")
    print(summarize_group(counters, "Counters "))
    print(f"  NeuralNet: final {nn.final_bankroll:,.0f} "
          f"(ROI {nn.roi_pct:+.1f}%), "
          f"{'busted@' + str(nn.busted_at) if nn.busted_at else 'alive'}")

    if args.chart:
        from blackjack_sim.plot import save_svg
        save_svg(result, args.chart)
        print(f"\nChart written to {args.chart}")

    if args.csv:
        _write_csv(result, args.csv)
        print(f"Trajectories written to {args.csv}")
    return 0


def _write_csv(result, path: str) -> None:
    names = [r.name for r in result.results]
    rows = zip(*[r.trajectory for r in result.results])
    with open(path, "w") as fh:
        fh.write("round," + ",".join(names) + "\n")
        for i, row in enumerate(rows):
            fh.write(f"{i}," + ",".join(f"{v:.2f}" for v in row) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
