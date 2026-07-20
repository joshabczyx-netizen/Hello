"""Tests for the blackjack betting advisor."""

import pytest

import blackjack_advisor as bja


# --------------------------------------------------------------------------- #
# Card parsing
# --------------------------------------------------------------------------- #
def test_normalize_faces_and_tens():
    assert bja.normalize_card("K") == "K"
    assert bja.normalize_card("t") == "10"
    assert bja.normalize_card("10") == "10"
    assert bja.normalize_card("ace") == "A"
    assert bja.normalize_card("1") == "A"


def test_normalize_strips_suits():
    assert bja.normalize_card("AH") == "A"
    assert bja.normalize_card("10s") == "10"
    assert bja.normalize_card("K♣") == "K"


def test_normalize_rejects_garbage():
    with pytest.raises(bja.CardError):
        bja.normalize_card("Z")
    with pytest.raises(bja.CardError):
        bja.normalize_card("")


def test_parse_cards_splits_commas_and_spaces():
    assert bja.parse_cards(["A,10 5", "K"]) == ["A", "10", "5", "K"]


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #
def test_hi_lo_running_count():
    # 5 low cards (+5), one 8 (0), two tens (-2) => +3
    cards = ["2", "3", "4", "5", "6", "8", "10", "K"]
    assert bja.running_count(cards, "hi-lo") == 3


def test_hi_lo_balanced_full_deck_is_zero():
    weights = bja.COUNTING_SYSTEMS["hi-lo"]["weights"]
    # Full deck: 4 of each rank. Balanced systems must sum to zero.
    total = sum(weights[r] * 4 for r in weights)
    assert total == 0


def test_unknown_system_raises():
    with pytest.raises(ValueError):
        bja.running_count(["A"], "made-up-system")


# --------------------------------------------------------------------------- #
# Deck / true count math
# --------------------------------------------------------------------------- #
def test_decks_remaining_floor():
    # Even with a near-empty shoe we never go below a quarter deck.
    assert bja.decks_remaining(cards_seen=311, total_decks=6) == 0.25


def test_true_count_scales_with_decks_remaining():
    rec = bja.evaluate(["2", "3", "4", "5"], system="hi-lo", total_decks=6)
    # 4 low cards => running +4; ~5.9 decks left => true count < running.
    assert rec.running_count == 4
    assert rec.true_count < rec.running_count
    assert rec.true_count > 0


# --------------------------------------------------------------------------- #
# Edge + betting
# --------------------------------------------------------------------------- #
def test_edge_negative_at_zero_count():
    assert bja.estimate_edge(0.0) == pytest.approx(-0.50)


def test_edge_positive_at_high_count():
    assert bja.estimate_edge(4.0) == pytest.approx(1.50)


def test_unit_spread_minimum_when_cold():
    assert bja.unit_spread(-3) == 1.0
    assert bja.unit_spread(1) == 1.0


def test_unit_spread_scales_and_caps():
    assert bja.unit_spread(5) == 4
    assert bja.unit_spread(50, max_units=8) == 8


def test_kelly_zero_when_no_edge():
    frac, bet = bja.kelly(edge_pct=-1.0, bankroll=10_000)
    assert frac == 0.0 and bet == 0.0


def test_kelly_positive_scales_with_edge():
    frac, bet = bja.kelly(edge_pct=2.0, bankroll=10_000, fraction=1.0)
    # f* = 0.02 / 1.32 ~= 0.01515
    assert frac == pytest.approx(0.02 / 1.32)
    assert bet == pytest.approx(frac * 10_000)


def test_half_kelly_is_half_of_full():
    full, _ = bja.kelly(edge_pct=3.0, bankroll=5_000, fraction=1.0)
    half, _ = bja.kelly(edge_pct=3.0, bankroll=5_000, fraction=0.5)
    assert half == pytest.approx(full / 2)


# --------------------------------------------------------------------------- #
# End-to-end
# --------------------------------------------------------------------------- #
def test_evaluate_hot_shoe_recommends_more():
    # A pile of low cards gone => deck rich in tens => player edge.
    hot = bja.evaluate(["2", "3", "4", "5", "6"] * 4, system="hi-lo",
                       total_decks=6, bankroll=10_000)
    assert hot.true_count > 0
    assert hot.edge_pct > 0
    assert hot.unit_bet > 1
    assert hot.kelly_bet > 0


def test_evaluate_cold_shoe_recommends_minimum():
    cold = bja.evaluate(["10", "J", "Q", "K", "A"] * 4, system="hi-lo",
                        total_decks=6, bankroll=10_000)
    assert cold.true_count < 0
    assert cold.edge_pct < 0
    assert cold.unit_bet == 1.0
    assert cold.kelly_bet == 0.0


def test_report_renders():
    rec = bja.evaluate(["A", "10", "5"], bankroll=1000)
    text = rec.format_report(bankroll=1000, unit_size=25)
    assert "BLACKJACK BETTING ADVISOR" in text
    assert "True count" in text


def test_main_runs_with_cards(capsys):
    code = bja.main(["A", "10", "5", "5", "K", "2"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ADVICE" in out
