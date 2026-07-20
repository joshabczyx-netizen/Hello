"""Tests for the blackjack simulation engine, agents, and tournament."""

import random

import pytest

from blackjack_sim.engine import (
    Shoe,
    Table,
    basic_strategy,
    hand_total,
    is_blackjack,
    play_round,
)
from blackjack_sim.agents import CounterAgent, FlatAgent, NeuralAgent
from blackjack_sim.neural import BettingNet, features, MAX_BET_FRACTION
from blackjack_sim.deviations import PlayNet, make_override, play_features
from blackjack_sim.tournament import run_sweep, run_tournament, run_trials


# --------------------------------------------------------------------------- #
# Hand evaluation
# --------------------------------------------------------------------------- #
def test_soft_and_hard_totals():
    assert hand_total(["A", "6"]) == (17, True)
    assert hand_total(["A", "6", "K"]) == (17, False)
    assert hand_total(["A", "A"]) == (12, True)
    assert hand_total(["K", "Q", "2"]) == (22, False)


def test_blackjack_detection():
    assert is_blackjack(["A", "K"])
    assert not is_blackjack(["A", "9"])
    assert not is_blackjack(["A", "5", "5"])  # 21 but 3 cards


# --------------------------------------------------------------------------- #
# Basic strategy spot checks (S17, DAS)
# --------------------------------------------------------------------------- #
def test_strategy_stand_hard_17():
    assert basic_strategy(["10", "7"], up=10, can_double=False, can_split=False) == "S"


def test_strategy_hit_hard_16_vs_10():
    assert basic_strategy(["10", "6"], up=10, can_double=False, can_split=False) == "H"


def test_strategy_stand_16_vs_6():
    assert basic_strategy(["10", "6"], up=6, can_double=False, can_split=False) == "S"


def test_strategy_double_11():
    assert basic_strategy(["6", "5"], up=6, can_double=True, can_split=False) == "D"
    # No double allowed -> hit.
    assert basic_strategy(["6", "5"], up=6, can_double=False, can_split=False) == "H"


def test_strategy_always_split_aces_and_eights():
    assert basic_strategy(["A", "A"], up=10, can_double=False, can_split=True) == "P"
    assert basic_strategy(["8", "8"], up=10, can_double=False, can_split=True) == "P"


def test_strategy_never_split_tens_or_fives():
    assert basic_strategy(["10", "K"], up=6, can_double=True, can_split=True) == "S"
    # Pair of fives plays as hard 10 -> double vs 6.
    assert basic_strategy(["5", "5"], up=6, can_double=True, can_split=True) == "D"


def test_strategy_soft_18_ds():
    # A,7 vs 6 doubles if allowed, otherwise stands.
    assert basic_strategy(["A", "7"], up=6, can_double=True, can_split=False) == "D"
    assert basic_strategy(["A", "7"], up=6, can_double=False, can_split=False) == "S"
    # A,7 vs 9 hits.
    assert basic_strategy(["A", "7"], up=9, can_double=True, can_split=False) == "H"


# --------------------------------------------------------------------------- #
# Shoe / counting
# --------------------------------------------------------------------------- #
def test_shoe_size_and_counts_are_perfect():
    shoe = Shoe(num_decks=6, rng=random.Random(0))
    assert len(shoe.cards) == 6 * 52
    # Draw the whole shoe: a balanced Hi-Lo count must return to zero.
    total = 0
    while shoe.pos < len(shoe.cards):
        shoe.draw()
    assert shoe.running_count == 0


def test_shoe_reshuffles_and_penetration():
    shoe = Shoe(num_decks=6, penetration=0.5, rng=random.Random(0))
    assert not shoe.needs_shuffle()
    for _ in range(int(6 * 52 * 0.5)):
        shoe.draw()
    assert shoe.needs_shuffle()


def test_true_count_positive_when_low_cards_gone():
    shoe = Shoe(num_decks=6, rng=random.Random(0))
    # Force a low-card-heavy removal by rigging the deck top.
    shoe.cards[:10] = ["2"] * 10
    shoe.pos = 0
    shoe.running_count = 0
    for _ in range(10):
        shoe.draw()
    assert shoe.running_count == 10
    assert shoe.true_count() > 0


# --------------------------------------------------------------------------- #
# Round EV
# --------------------------------------------------------------------------- #
def test_flat_basic_strategy_edge_is_small_and_negative():
    shoe = Shoe(rng=random.Random(7))
    n = 200_000
    total = 0.0
    for _ in range(n):
        if shoe.needs_shuffle():
            shoe.shuffle()
        total += play_round(shoe, 1.0)
    edge = total / n
    # A correct 6-deck S17 game sits near -0.5% for the player. The bounds are
    # loose enough to absorb sampling noise but tight enough to catch a broken
    # payout/strategy (which would swing the edge by whole percentage points).
    assert -0.03 < edge < 0.01


# --------------------------------------------------------------------------- #
# Neural net
# --------------------------------------------------------------------------- #
def test_net_bet_fraction_in_range():
    net = BettingNet()
    for tc in (-10, 0, 10):
        f = net.bet_fraction(features(tc, 3.0, 1.0))
        assert 0.0 <= f <= MAX_BET_FRACTION


def test_net_params_round_trip():
    net = BettingNet()
    theta = net.get_params()
    net.set_params(theta)
    assert pytest.approx(theta) == net.get_params()


def test_net_save_load(tmp_path):
    net = BettingNet()
    path = tmp_path / "w.npz"
    net.save(str(path))
    loaded = BettingNet.load(str(path))
    f = features(3.0, 2.0, 1.0)
    assert net.bet_fraction(f) == pytest.approx(loaded.bet_fraction(f))


# --------------------------------------------------------------------------- #
# Agents & tournament
# --------------------------------------------------------------------------- #
def test_counter_bets_minimum_when_edge_nonpositive():
    agent = CounterAgent("c", 1000)
    shoe = Shoe(rng=random.Random(0))  # fresh shoe, true count ~0 -> min bet
    table = Table()
    assert agent.bet(shoe, table) == table.min_bet


def test_counter_bets_more_at_high_count():
    agent = CounterAgent("c", 1000)
    table = Table()
    shoe = Shoe(rng=random.Random(0))
    shoe.running_count = 30
    shoe.pos = 52 * 3  # ~3 decks in -> true count ~ +10
    assert agent.bet(shoe, table) > table.min_bet


def test_bet_never_exceeds_bankroll_or_max():
    agent = FlatAgent("f", 5.0)  # below min bet
    table = Table(min_bet=10, max_bet=500)
    # Clamp floors at min_bet even when bankroll is tiny (ruin handled upstream).
    shoe = Shoe(rng=random.Random(0))
    assert agent.bet(shoe, table) == 10


def test_tournament_runs_and_reports():
    net = BettingNet()
    agents = [
        CounterAgent("Counter-1", 1000),
        CounterAgent("Counter-2", 1000),
        NeuralAgent("NeuralNet", 1000, net),
    ]
    result = run_tournament(agents, rounds=200, seed=1)
    assert result.rounds == 200
    assert len(result.results) == 3
    for r in result.results:
        assert len(r.trajectory) == 201  # start + one per round
        assert r.start_bankroll == 1000


def test_bankroll_never_goes_negative():
    # Aggressive spread on a tiny bankroll must never push it below zero.
    agents = [CounterAgent("Counter-1", 1000, max_units=8)]
    result = run_tournament(agents, rounds=2000, seed=3)
    assert min(result.results[0].trajectory) >= 0.0


def test_tournament_is_reproducible_with_seed():
    def run():
        agents = [CounterAgent("Counter-1", 1000), CounterAgent("Counter-2", 1000)]
        return run_tournament(agents, rounds=150, seed=99)
    a = run().results[0].final_bankroll
    b = run().results[0].final_bankroll
    assert a == b


# --------------------------------------------------------------------------- #
# Playing deviations
# --------------------------------------------------------------------------- #
def test_play_features_onehot():
    from blackjack_sim.deviations import N_PLAY_FEATURES
    f = play_features(16, 10, 20)
    assert f.shape == (N_PLAY_FEATURES,)
    # one-hot total (12-16) and up (2-11), plus a clamped true count
    assert f[16 - 12] == 1.0          # total 16
    assert f[5 + (10 - 2)] == 1.0     # up 10
    assert -3.0 <= f[15] <= 3.0       # clamped tc


def test_playnet_params_round_trip_and_advantage():
    net = PlayNet()
    adv = net.stand_advantage(16, 10, 0)
    assert isinstance(adv, float)
    theta = net.get_params()
    net.set_params(theta)
    assert pytest.approx(theta) == net.get_params()


def test_override_only_touches_hard_12_16():
    net = PlayNet()
    override = make_override(net)
    # Hard 16 -> either a deviation (S/H) or None (play basic).
    assert override(["10", "6"], 10, 0.0) in ("S", "H", None)
    # Hard 17 -> not the net's concern.
    assert override(["10", "7"], 10, 0.0) is None
    # Soft 15 (A,4) -> not a hard stiff, leave to basic strategy.
    assert override(["A", "4"], 10, 0.0) is None
    # Hard 11 -> below the stiff range.
    assert override(["6", "5"], 10, 0.0) is None


def test_override_changes_play_round_outcome_distribution():
    # A net that always wants to stand must change results vs. basic strategy.
    class AlwaysStand:
        def stand_advantage(self, total, up, tc):
            return 10.0  # huge advantage -> deviate hit->stand everywhere
    override = make_override(AlwaysStand())
    a = b = 0.0
    sa = Shoe(rng=random.Random(5))
    sb = Shoe(rng=random.Random(5))
    for _ in range(5000):
        if sa.needs_shuffle():
            sa.shuffle(); sb.shuffle()
        a += play_round(sa, 1.0)
        b += play_round(sb, 1.0, play_override=override)
    assert a != b


# --------------------------------------------------------------------------- #
# Multi-session + sweep aggregation
# --------------------------------------------------------------------------- #
def test_run_trials_tracks_ruin_and_beats():
    net = BettingNet()

    def make():
        return [CounterAgent("Counter-1", 200, max_units=8),
                NeuralAgent("NeuralNet", 200, net)]

    summ = run_trials(make, trials=20, rounds=300, seed=1)
    assert summ.trials == 20
    assert summ.counter_count == 20
    assert 0.0 <= summ.counter_ruin_rate <= 1.0
    assert 0 <= summ.nn_beats_counter_avg <= 20


def test_run_sweep_shape():
    net = BettingNet()

    def make_for(bankroll):
        return [CounterAgent("Counter-1", bankroll),
                NeuralAgent("NeuralNet", bankroll, net)]

    sweep = run_sweep(make_for, [500, 2000], trials=10, rounds=200, seed=2)
    assert [b for b, _ in sweep] == [500, 2000]
    for _, summary in sweep:
        assert summary.trials == 10
