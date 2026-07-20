#!/usr/bin/env python3
"""Sweep the starting bankroll to show how counting's payoff depends on it.

At a fixed table ($10 min), a small bankroll is forced to over-bet the minimum
on bad counts and can't spread wide on good ones without risking ruin. As the
bankroll grows, the Kelly bettor turns a reliable profit and ruin risk vanishes.

    python sweep.py
    python sweep.py --trials 500 --rounds 500 --chart sweep.svg
"""

from __future__ import annotations

import argparse
import os

from blackjack_sim.agents import CounterAgent, NeuralAgent
from blackjack_sim.engine import Table
from blackjack_sim.neural import BettingNet
from blackjack_sim.tournament import format_sweep, run_sweep


def main() -> int:
    p = argparse.ArgumentParser(description="Bankroll sweep for counters vs. net.")
    p.add_argument("--bankrolls", type=float, nargs="+",
                   default=[500, 1000, 2000, 5000, 10000, 25000, 50000])
    p.add_argument("--trials", type=int, default=400)
    p.add_argument("--rounds", type=int, default=500)
    p.add_argument("--counters", type=int, default=4)
    p.add_argument("--weights", default="nn_weights.npz")
    p.add_argument("--min-bet", type=float, default=10.0)
    p.add_argument("--max-bet", type=float, default=500.0)
    p.add_argument("--max-units", type=float, default=8.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--chart", default=None, help="Write an SVG ROI chart here.")
    args = p.parse_args()

    if os.path.exists(args.weights):
        net = BettingNet.load(args.weights)
    else:
        net = BettingNet()
        print(f"! {args.weights} not found -- using an UNTRAINED net.")

    def make_agents_for(bankroll):
        agents = [CounterAgent(f"Counter-{i+1}", bankroll,
                               max_units=args.max_units)
                  for i in range(args.counters)]
        agents.append(NeuralAgent("NeuralNet", bankroll, net))
        return agents

    table = Table(min_bet=args.min_bet, max_bet=args.max_bet)
    print(f"Sweeping {len(args.bankrolls)} bankrolls x {args.trials} sessions "
          f"x {args.rounds} rounds...\n")
    sweep = run_sweep(make_agents_for, args.bankrolls, trials=args.trials,
                      rounds=args.rounds, table=table, seed=args.seed)
    print(format_sweep(sweep))

    if args.chart:
        from blackjack_sim.plot import save_sweep_svg
        save_sweep_svg(sweep, args.chart)
        print(f"\nChart written to {args.chart}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
