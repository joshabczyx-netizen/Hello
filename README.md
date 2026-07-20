# Blackjack Betting Advisor

A Python tool that recommends **how much to bet** in blackjack based on the
cards you've recently seen dealt. Feed it the last ~20 cards and it tells you
whether the remaining deck favors you and how to size your bet.

> ⚠️ This is an educational/statistical tool. It does not guarantee winnings —
> it just makes the mathematically optimal betting decision given the
> information you provide. Card counting is legal but many casinos will ask you
> to leave if they catch you doing it.

## The idea

Blackjack is one of the few casino games where the odds shift as cards leave
the deck. When lots of low cards (2–6) have been dealt, the remaining shoe is
rich in tens and aces, which favors the player. The advisor turns that
observation into a bet size using three well-established pieces of math:

1. **Card counting** — each seen card nudges a *running count* (Hi-Lo by
   default; several systems supported).
2. **True count** — the running count divided by the estimated decks remaining,
   which is what actually predicts your edge.
3. **Kelly criterion** — converts your estimated edge into an optimal fraction
   of your bankroll to wager, plus a simpler unit-spread recommendation.

## Usage

No dependencies beyond the Python standard library (3.8+).

```bash
# Score the last cards you saw
python blackjack_advisor.py A 10 5 5 K 2 6 3

# Commas and suits are fine and ignored
python blackjack_advisor.py "5h,6c,7d,2s,3h,Kd,As"

# Add a bankroll to get a Kelly-sized bet
python blackjack_advisor.py --bankroll 10000 --unit-size 25  5 6 7 2 3 4 5 6

# Pick a different counting system and shoe size
python blackjack_advisor.py --system zen --decks 8  10 J Q A

# Feed cards live, one at a time, and watch the count evolve
python blackjack_advisor.py --interactive
```

### Example output

```
====================================================
  BLACKJACK BETTING ADVISOR
====================================================
  Counting system     : hi-lo
  Cards observed      : 8
  Running count       : +6
  Decks remaining     : 5.85
  True count          : +1.03
  Estimated edge      : +0.01%
----------------------------------------------------
  Recommended units   : 1.0  (= 25.00 at 25/unit)
----------------------------------------------------
  ADVICE: Slight player edge -- bet small, stay disciplined.
====================================================
```

## Options

| Flag | Meaning | Default |
|------|---------|---------|
| `cards` | Recently dealt cards (positional) | — |
| `-s, --system` | `hi-lo`, `hi-opt-ii`, `ko`, `omega-ii`, `zen` | `hi-lo` |
| `-d, --decks` | Total decks in the shoe | `6` |
| `-b, --bankroll` | Enables Kelly bet sizing | off |
| `-u, --unit-size` | Currency value of one unit | `25` |
| `--max-units` | Cap on the unit spread | `8` |
| `--kelly-fraction` | Fraction of full Kelly (0.5 = half) | `0.5` |
| `-i, --interactive` | Enter cards one at a time | off |

## How the numbers are derived

- **Edge** is modeled as `-0.5% + 0.5% × true_count`, a standard rule of thumb
  for a typical 6-deck game. Both constants are tunable in `estimate_edge()` to
  match a specific rule set (dealer hits/stands soft 17, 3:2 vs 6:5, etc.).
- **Kelly fraction** is `edge / variance`, with blackjack's per-unit variance
  taken as ~1.32. Half-Kelly is the default because full Kelly's bankroll
  swings are brutal in practice.
- **True count** assumes the cards you pass represent what's been removed from
  the shoe. For best accuracy, count *every* card since the last shuffle, not
  just the last 20 — but the last 20 still give a useful directional read.

## Using it as a library

```python
import blackjack_advisor as bja

rec = bja.evaluate(["5", "6", "7", "2", "3", "K", "A"],
                   system="hi-lo", total_decks=6, bankroll=10_000)
print(rec.true_count, rec.edge_pct, rec.unit_bet, rec.kelly_bet)
```

## Tests

```bash
pip install pytest
pytest
```
