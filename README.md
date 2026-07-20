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

## Simulation: card counters vs. a neural network

The `blackjack_sim/` package pits **4 perfect-card-counting agents** against a
**neural network that learns its own betting policy**, all starting with $1000.

- **Full engine** (`blackjack_sim/engine.py`): 6-deck shoe, S17, 3:2 blackjack,
  double/split/DAS, dealer peek, random shuffles at 75% penetration. Verified to
  reproduce the correct ~−0.6% basic-strategy house edge.
- **Everyone plays textbook basic strategy** for hit/stand/double/split. The
  contest is purely about **bet sizing**, which is the only thing card counting
  actually changes.
- **Counters** (`CounterAgent`): perfect Hi-Lo count of every card dealt, betting
  the classic count-based unit ramp (1 unit when cold, up to 8 units when hot).
- **Neural net** (`blackjack_sim/neural.py`): a small numpy MLP mapping
  `(true count, decks remaining, bankroll ratio)` to a bet size in units. It is
  trained from scratch with **Evolution Strategies** against the Kelly objective
  (mean log-growth of bankroll). Because that objective is maximized by betting
  more when the edge is higher, the net *rediscovers* the count-based ramp on its
  own — no strategy is hard-coded into it.

### Run it

A trained `nn_weights.npz` is already included, so you can run the tournament
immediately:

```bash
pip install numpy

# Run the tournament: 4 counters + the neural net, $1000 each, 1000 rounds
python compete.py --rounds 1000 --seed 42 --chart bankrolls.svg --csv bankrolls.csv

# See the strategy difference across many sessions (variance averages out)
python compete.py --trials 500 --rounds 500

# Optional: retrain the betting net from scratch (~15 min; needs lots of shoes
# per evaluation to resolve the ramp above the noise). Overwrites nn_weights.npz
python train_nn.py
```

`compete.py` prints final standings, and with `--chart` / `--csv` writes an SVG
bankroll chart and per-round trajectories. If `nn_weights.npz` is missing it
falls back to an untrained net (and warns you).

### What the net learns

Trained only to grow its bankroll (self-play, no strategy hard-coded), the net
independently rediscovers the **Kelly-optimal count-based bet ramp**:

```
Learned bet ramp vs. true count ($1000 bankroll / $10 min):
  TC <= +2 :  1.0 units      (no edge -> bet the minimum)
  TC +3    :  1.1 units
  TC +5    :  1.9 units
  TC +8    :  3.1 units
  TC +10   :  3.8 units
```

That is close to the theoretical Kelly stake (edge / variance) at each count —
and notably *more conservative* than the counters' textbook 1–8 unit spread,
which over-bets for such a small bankroll.

### What the tournament shows (500 sessions x 500 rounds, $1000 start)

| metric              | Counters (1–8 spread) | Neural net (learned Kelly) |
|---------------------|-----------------------|----------------------------|
| mean final          | ~$1,010               | ~$980                      |
| worst session       | **$0 (busted)**       | ~$230 (never busts)        |
| best session        | ~$2,500               | ~$1,900                    |
| ended profitable    | ~52%                  | ~45%                       |

The honest takeaways:

- On a **$1000 bankroll the edge is thin and variance dominates** — any single
  session is mostly luck, which is why you should compare across many.
- The aggressive counter spread has a slightly **higher mean** (it bets more
  money on +EV hands) but risks **ruin** — some sessions bust to zero.
- The Kelly-optimal net trades a little mean for **much lower variance and zero
  busts**. Neither reliably "beats" the other; they sit on different points of
  the same risk/return curve. Counting is real, but it needs a big bankroll and
  a wide spread (or many sessions) to overcome the house edge dependably.

## Tests

```bash
pip install pytest numpy
pytest
```
