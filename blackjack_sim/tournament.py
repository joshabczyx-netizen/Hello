"""Run a head-to-head betting tournament and record bankroll trajectories.

Each agent plays its own independent shoe (its own random shuffles), so the
comparison isolates the *betting policy* from any shared-luck artifacts. All
agents start with the same bankroll and play the same number of rounds.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .agents import Agent
from .engine import Shoe, Table, play_round


@dataclass
class AgentResult:
    name: str
    start_bankroll: float
    final_bankroll: float
    peak: float
    trough: float
    busted_at: int | None
    trajectory: List[float] = field(default_factory=list)

    @property
    def profit(self) -> float:
        return self.final_bankroll - self.start_bankroll

    @property
    def roi_pct(self) -> float:
        return 100.0 * self.profit / self.start_bankroll


@dataclass
class TournamentResult:
    rounds: int
    results: List[AgentResult]

    def standings(self) -> List[AgentResult]:
        return sorted(self.results, key=lambda r: r.final_bankroll, reverse=True)


def run_tournament(
    agents: Sequence[Agent],
    rounds: int = 1000,
    table: Table | None = None,
    seed: int | None = None,
) -> TournamentResult:
    """Play `rounds` hands for each agent on independent random shoes."""
    table = table or Table()
    base_rng = random.Random(seed)

    # Independent shoe (and RNG stream) per agent.
    shoes: Dict[str, Shoe] = {}
    for agent in agents:
        agent_seed = base_rng.randrange(2**31)
        shoes[agent.name] = Shoe(rng=random.Random(agent_seed))

    trajectories: Dict[str, List[float]] = {a.name: [a.bankroll] for a in agents}
    peaks: Dict[str, float] = {a.name: a.bankroll for a in agents}
    troughs: Dict[str, float] = {a.name: a.bankroll for a in agents}

    for rnd in range(1, rounds + 1):
        for agent in agents:
            shoe = shoes[agent.name]
            if shoe.needs_shuffle():
                shoe.shuffle()

            if agent.alive:
                if agent.bankroll < table.min_bet:
                    agent.busted_at = rnd
                else:
                    stake = agent.bet(shoe, table)
                    profit = play_round(shoe, stake,
                                        play_override=agent.play_override)
                    # A player can never lose more than their bankroll (they
                    # can't stake funds they don't have to double/split).
                    agent.bankroll = max(0.0, agent.bankroll + profit)
            else:
                # Ruined agents sit out but the shoe keeps burning (harmless).
                play_round(shoe, table.min_bet)

            trajectories[agent.name].append(agent.bankroll)
            peaks[agent.name] = max(peaks[agent.name], agent.bankroll)
            troughs[agent.name] = min(troughs[agent.name], agent.bankroll)

    results = [
        AgentResult(
            name=a.name,
            start_bankroll=a.start_bankroll,
            final_bankroll=a.bankroll,
            peak=peaks[a.name],
            trough=troughs[a.name],
            busted_at=a.busted_at,
            trajectory=trajectories[a.name],
        )
        for a in agents
    ]
    return TournamentResult(rounds=rounds, results=results)


@dataclass
class TrialsSummary:
    trials: int
    rounds: int
    counter_finals: List[float]
    neural_finals: List[float]
    nn_beats_counter_avg: int  # sessions the NN beat the counters' session mean
    counter_ruins: int = 0     # counter agents that hit $0 (across all sessions)
    counter_count: int = 0     # total counter agents played
    neural_ruins: int = 0      # neural agents that hit $0

    def _stats(self, finals: List[float], start: float) -> Dict[str, float]:
        return {
            "mean": statistics.mean(finals),
            "median": statistics.median(finals),
            "min": min(finals),
            "max": max(finals),
            "roi_pct": 100.0 * (statistics.mean(finals) - start) / start,
            "below_start_rate": sum(1 for f in finals if f < start) / len(finals),
            "profit_rate": sum(1 for f in finals if f > start) / len(finals),
        }

    @property
    def counter_ruin_rate(self) -> float:
        return self.counter_ruins / max(self.counter_count, 1)

    @property
    def neural_ruin_rate(self) -> float:
        return self.neural_ruins / max(self.trials, 1)

    def format(self, start_bankroll: float) -> str:
        c = self._stats(self.counter_finals, start_bankroll)
        n = self._stats(self.neural_finals, start_bankroll)
        lines = [
            "=" * 68,
            f"  MULTI-SESSION SUMMARY  ({self.trials} sessions x "
            f"{self.rounds} rounds, start ${start_bankroll:,.0f})",
            "=" * 68,
            f"  {'metric':<22}{'Counters (pooled)':>22}{'NeuralNet':>22}",
            "-" * 68,
            f"  {'mean final':<22}{c['mean']:>22,.0f}{n['mean']:>22,.0f}",
            f"  {'median final':<22}{c['median']:>22,.0f}{n['median']:>22,.0f}",
            f"  {'best session':<22}{c['max']:>22,.0f}{n['max']:>22,.0f}",
            f"  {'worst session':<22}{c['min']:>22,.0f}{n['min']:>22,.0f}",
            f"  {'ended profitable':<22}{c['profit_rate']*100:>21.0f}%"
            f"{n['profit_rate']*100:>21.0f}%",
            f"  {'ended below start':<22}{c['below_start_rate']*100:>21.0f}%"
            f"{n['below_start_rate']*100:>21.0f}%",
            f"  {'went bust ($0)':<22}{self.counter_ruin_rate*100:>21.1f}%"
            f"{self.neural_ruin_rate*100:>21.1f}%",
            "-" * 68,
            f"  NeuralNet beat the counters' average in "
            f"{self.nn_beats_counter_avg}/{self.trials} sessions "
            f"({100*self.nn_beats_counter_avg/self.trials:.0f}%).",
            "=" * 68,
        ]
        return "\n".join(lines)


def run_trials(
    make_agents,
    trials: int,
    rounds: int,
    table: Table | None = None,
    seed: int | None = None,
) -> TrialsSummary:
    """Run many independent sessions and aggregate counter vs. NN outcomes.

    `make_agents` is a zero-arg factory returning a fresh list of agents whose
    names start with 'Counter' (the rule-based instances) plus one 'NeuralNet'.
    """
    base = random.Random(seed)
    counter_finals: List[float] = []
    neural_finals: List[float] = []
    nn_beats = 0
    counter_ruins = 0
    counter_count = 0
    neural_ruins = 0
    for _ in range(trials):
        agents = make_agents()
        result = run_tournament(agents, rounds=rounds, table=table,
                                seed=base.randrange(2**31))
        session_counters = [r for r in result.results
                            if r.name.startswith("Counter")]
        nn = next(r for r in result.results if r.name == "NeuralNet")
        counter_finals.extend(r.final_bankroll for r in session_counters)
        neural_finals.append(nn.final_bankroll)
        counter_count += len(session_counters)
        counter_ruins += sum(1 for r in session_counters if r.busted_at)
        neural_ruins += 1 if nn.busted_at else 0
        if nn.final_bankroll > statistics.mean(
                [r.final_bankroll for r in session_counters]):
            nn_beats += 1
    return TrialsSummary(
        trials=trials,
        rounds=rounds,
        counter_finals=counter_finals,
        neural_finals=neural_finals,
        nn_beats_counter_avg=nn_beats,
        counter_ruins=counter_ruins,
        counter_count=counter_count,
        neural_ruins=neural_ruins,
    )


def run_sweep(
    make_agents_for,
    bankrolls: Sequence[float],
    trials: int,
    rounds: int,
    table: Table | None = None,
    seed: int | None = None,
) -> List[tuple]:
    """Run a trials batch at each starting bankroll.

    `make_agents_for(bankroll)` returns a fresh agent list for that bankroll.
    Returns a list of (bankroll, TrialsSummary).
    """
    out = []
    for b in bankrolls:
        summary = run_trials(lambda b=b: make_agents_for(b), trials=trials,
                             rounds=rounds, table=table, seed=seed)
        out.append((b, summary))
    return out


def format_sweep(sweep: List[tuple]) -> str:
    lines = [
        "=" * 74,
        "  BANKROLL SWEEP  (mean ROI and ruin rate by starting bankroll)",
        "=" * 74,
        f"  {'bankroll':>10} | {'Counter ROI':>12}{'Counter ruin':>14}"
        f" | {'Neural ROI':>12}{'Neural ruin':>13}",
        "-" * 74,
    ]
    for b, s in sweep:
        c = s._stats(s.counter_finals, b)
        n = s._stats(s.neural_finals, b)
        lines.append(
            f"  {b:>10,.0f} | {c['roi_pct']:>11.1f}%{s.counter_ruin_rate*100:>13.1f}%"
            f" | {n['roi_pct']:>11.1f}%{s.neural_ruin_rate*100:>12.1f}%"
        )
    lines.append("=" * 74)
    return "\n".join(lines)


def format_standings(result: TournamentResult) -> str:
    lines = [
        "=" * 68,
        f"  TOURNAMENT RESULTS  ({result.rounds} rounds each)",
        "=" * 68,
        f"  {'rank':<5}{'agent':<20}{'final':>12}{'ROI':>10}"
        f"{'peak':>10}{'status':>11}",
        "-" * 68,
    ]
    for i, r in enumerate(result.standings(), 1):
        status = f"bust@{r.busted_at}" if r.busted_at else "alive"
        lines.append(
            f"  {i:<5}{r.name:<20}{r.final_bankroll:>12,.0f}"
            f"{r.roi_pct:>9.1f}%{r.peak:>10,.0f}{status:>11}"
        )
    lines.append("=" * 68)
    return "\n".join(lines)


def summarize_group(results: List[AgentResult], label: str) -> str:
    """One-line summary of a group of agents (e.g. the four counters)."""
    finals = [r.final_bankroll for r in results]
    busts = sum(1 for r in results if r.busted_at)
    mean = statistics.mean(finals)
    return (
        f"  {label}: mean final {mean:,.0f} "
        f"(min {min(finals):,.0f}, max {max(finals):,.0f}), "
        f"{busts}/{len(results)} busted"
    )
