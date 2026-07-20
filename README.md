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

## Bankroll sweep: why counting needs a big bankroll

`sweep.py` reruns the tournament across a range of starting bankrolls (fixed
$10 table) to show how the payoff depends on bankroll size.

```bash
python sweep.py --trials 400 --rounds 500 --chart sweep.svg
```

Representative output (mean ROI and true ruin rate, 300 sessions x 500 rounds):

| bankroll | Counter ROI | Counter ruin | Neural ROI | Neural ruin |
|---------:|------------:|-------------:|-----------:|------------:|
| $500     | −2.1%       | **18.8%**    | −4.4%      | 7.0%        |
| $1,000   | −1.1%       | 1.0%         | −1.5%      | 0.0%        |
| $2,000   | −0.5%       | 0.0%         | 0.0%       | 0.0%        |
| $5,000   | −0.2%       | 0.0%         | +0.9%      | 0.0%        |
| $10,000  | −0.1%       | 0.0%         | **+1.1%**  | 0.0%        |
| $50,000  | −0.0%       | 0.0%         | +0.4%      | 0.0%        |

What this shows:

- **Ruin risk is a small-bankroll problem.** The counters' aggressive 1–8
  spread ruins ~19% of $500 rolls but essentially never busts a $2,000+ roll.
- **The Kelly net only turns profitable once the bankroll clears the forced
  minimum** — negative at $500–$1,000 (the $10 min forces over-betting on bad
  counts), crossing zero near $2,000 and peaking around +1% at $10,000.
- Above ~$10k the net's ROI dips again because the $500 **table maximum** caps
  its Kelly bets, so it can no longer scale with the bankroll.

The headline lesson of the whole project: *counting is real, but its edge is
thin and only pays reliably with a bankroll large relative to the table minimum
and small relative to the table maximum.*

## Neural playing deviations (index plays)

Basic strategy is fixed, but the best *play* on borderline stiff hands shifts
with the count — the classic "index plays" (stand on 16 vs 10 when the deck is
ten-rich, etc.). `blackjack_sim/deviations.py` trains a second neural net
(`PlayNet`) on the highest-value group of these — the **hard 12–16 stand/hit
decisions** — purely from expected value.

How it learns (and why the first approach failed): evaluating a decision by
playing one hand is far too noisy (±1 per hand) for the ~0.02 EV a deviation is
worth. The trainer instead uses **paired common random numbers** — it plays both
*stand* and *hit* on the *same* deck continuation, so the EV *difference* is a
low-variance target — builds a dataset of `(state, stand-advantage)` pairs, and
fits the net by supervised regression. The net then deviates only when it
predicts the non-basic action wins by a margin.

```bash
# Train the deviation net (~1-2 min), saves play_weights.npz, prints what it learned
python train_deviations.py

# Run the tournament with all agents using the learned deviations
python compete.py --deviations --trials 500 --rounds 500
```

What it learns and what it's worth:

- The net recovers the **direction and rough thresholds of the real index
  plays** — e.g. stand on 16 vs 10 and 15 vs 10 as the count climbs — validated
  against the engine's own EV. (Rare high-count cells stay noisy; there is
  little data out there.)
- The measured **flat-bet EV gain is only ~+0.05% per unit** — real but tiny.

That tiny number is the point. Playing deviations barely move flat-bet EV
because their value is **bet-weighted**: they matter only on the high-count
hands where you are *also* betting big. It reinforces the whole project's
thesis — **betting is where card counting's money is**, and the playing
deviations are a small garnish on top.

## Tests

```bash
pip install pytest numpy
pytest
```
