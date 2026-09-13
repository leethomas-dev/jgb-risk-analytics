# Phase 3B Documentation — `models/dv01.py`

## In plain English

Phase 3A measures interest-rate risk as a percentage of a bond's price,
broken down by loan length. This part converts that same risk into an
actual currency amount: how many yen a bond — or the whole portfolio —
would gain or lose if interest rates moved by one hundredth of one
percent, a small standard amount used throughout the finance industry as
a common yardstick. That currency figure, broken down by loan length the
same way, is the number someone would actually use to decide the size of
a hedge — a separate, offsetting investment meant to protect against
interest rate moves.

---

## Technical details

DV01 ("dollar value of 01"): the currency-terms version of Key Rate
Duration (Phase 3A) — the same sensitivity, rescaled from a percentage
into currency so it can be read directly as a hedge size. Reuses Phase
3A's duration/KRD calculations and Phase 2B's pricing directly; computes
no new sensitivity itself.

**File:** `models/dv01.py` — `dv01_bond()`, `dv01_by_tenor_bond()`
(per-tenor, the hedge-ratio table), `dv01_portfolio()`,
`dv01_by_tenor_portfolio()`.

**Output contract:** `dv01_bond(...) -> float`, currency per 100 face
value. `dv01_by_tenor_bond(...) -> pd.Series`, same units, one value per
tenor. `dv01_portfolio(...) -> pd.DataFrame`, one row per bond, columns
`[name, maturity_years, coupon_rate, weight, price, modified_duration,
dv01]`, no total row. `dv01_by_tenor_portfolio(...) -> pd.DataFrame`, one
row per bond plus a `portfolio_total` row, one column per tenor.

---

## 1. What each piece does, and why

### 1.1 Units: currency per 100 face value, never scaled to a real position size

Every DV01 number here is in the same units `price_bond` already uses —
currency per 100 face value — never multiplied by an actual position
size. Three conventions were on the table: per 100 face, per the bond's
own face value (the same number here, since every bond's face value
happens to be 100), or scaled to some assumed real position size. Per
100 face was chosen because it costs nothing (every other number in this
project already uses it, so nothing needs converting), and because
inventing a "position size" would just be a second made-up number
alongside the portfolio's already-illustrative coupons and weights,
rather than removing an approximation. A real trading book would apply
an actual position size on top of this — see §4.2.

### 1.2 `dv01_bond(...)`

```
DV01 = Price * ModifiedDuration * bump_size
```

Two existing numbers multiplied together — nothing recomputed. `bump_size`
does double duty: it's both the small numerical step the underlying
duration calculation uses internally, and, by definition, the shock size
this DV01 represents — its default (one basis point) is what makes this a
genuine "DV01" rather than the value of some other-sized move.

### 1.3 `dv01_by_tenor_bond(...)`

```
DV01_k = Price * KRD_k * bump_size
```

The per-tenor breakdown — the number that actually sizes a hedge ("how
much of a 10-year hedge do I need to offset this bond's 10-year
exposure"), as opposed to a duration or KRD figure, which only says *how
much* risk sits there, not in what units to hedge it. Because this is
just KRD rescaled by the same two numbers at every tenor, it inherits
Phase 3A's own accuracy directly — no new source of error from the
rescaling itself.

### 1.4 / 1.5 `dv01_portfolio(...)` and `dv01_by_tenor_portfolio(...)`

Two different table shapes, matching two existing patterns in this
project rather than inventing a third: `dv01_portfolio` mirrors
`price_portfolio` (one row per bond, no total row — a caller computes the
portfolio-level figure with `(df.weight * df.dv01).sum()`), because it
answers a per-*bond* question. `dv01_by_tenor_portfolio` mirrors Phase
3A's portfolio KRD table (one row per bond plus a built-in weighted total
row) because it answers a per-*tenor* question instead — the portfolio-
level version of the hedge-sizing table in §1.3. The two tables are
cross-checked against each other: fully summing either one lands on the
same overall figure.

Both take `freq=None` by default, computing each bond at its own
`Bond.freq` rather than one frequency shared by the whole portfolio
(`docs/phase_2b_documentation.md §1.3`); pass an explicit `freq` to
override every bond to one shared frequency instead.

### 1.6 `__main__`

Prints both tables plus the §2 validation check on every real run, so a
future bug would show up immediately, not only inside the tests.

---

## 2. Validation: does the formula match a real bumped reprice?

`dv01_bond`'s formula is checked against an independent reference: price
the bond normally, then price it again against a curve with every rate
genuinely shifted up by one basis point, and take the difference. This is
a real, non-circular check — the formula uses a two-sided (up-and-down,
averaged) rate move internally, while the reference uses a genuine
one-sided move, so the two are independent calculations, not the same
number computed twice.

A real run against the live MOF curve:

| Bond | formula | direct reprice | gap |
| --- | --- | --- | --- |
| JGB_2Y | 0.019393 | 0.019390 | +0.01% |
| JGB_5Y | 0.046153 | 0.046141 | +0.03% |
| JGB_10Y | 0.082298 | 0.082257 | +0.05% |
| JGB_20Y | 0.131264 | 0.131146 | +0.09% |
| JGB_30Y | 0.164150 | 0.163951 | +0.12% |
| JGB_40Y | 0.192405 | 0.192122 | +0.15% |

The formula is consistently a touch *larger* than the real reprice, and
the gap grows with maturity — the same convexity effect explained in
Phase 3A §2: the formula assumes price moves in a straight line with
yield, but a real bond's price curves slightly, cushioning the actual
move. The gap is small and explained, not a bug; the test's tolerance is
set well above the largest observed value specifically so a real error
(a sign mistake, a missing multiplication) would still be caught.

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 Inherits the pricing engine's rate-curve simplification.** Same as
Phase 3A — DV01 is computed on the curve's directly-quoted rate, not a
more rigorously derived one. Worth restating here specifically, since a
currency figure reads as more concrete/tradeable than a percentage one,
inviting even more confidence than it should carry.

**Resolved, in part (Phase 4.5).** DV01 (a thin unit conversion over
`price_bond`/`effective_duration_bond`, module docstring) inherited
zero-curve support automatically once those did, with no code changes of
its own — pass a bootstrapped zero curve instead of a par curve. **The
par-curve basis remains the default.** See the same note in
`docs/phase_2b_documentation.md` §3.2 and
`docs/phase_4_5c_documentation.md` §1/§2 for the mechanism and the
quantified DV01 difference (up to ~14% at the 40Y bond).

**3.2 DV01's tenor set isn't fixed** — same as every other module; a
DV01 table from one data source isn't directly comparable, column for
column, to one from another.

**3.3 "Per 100 face" is not a real position size.** None of this
module's output should be read as real currency P&L for an actual book —
that would need an actual notional this project doesn't have, since the
portfolio itself is illustrative (Phase 2A). Worth restating here since
DV01 is the first output in this project whose units (currency) could be
mistaken for a real dollar figure by a reader who hasn't seen that
disclaimer.

**3.4 The formula/reprice gap (§2) is real, explained, and small — not
exactly zero.** A consumer needing the exact price impact of a specific
shock should reprice directly rather than trust the formula to the last
digit.

---

## 4. What would change this design

**A more rigorous rate curve** (§3.1), if built later, needs no change
here — every function already just reprices whatever curve it's handed.

**Scaling to a real position size** would be additive: a `notional`
input multiplying this module's "per 100 face" output by
`notional / 100`, applied at the point of use — nothing in the core
formulas would need to change.

---

## 5. Relationship to the fallback re-anchoring policy

This module adds no new hardcoded data — it's a unit conversion over
Phase 2B and Phase 3A's existing calculations. Nothing new for Phase 1's
re-anchoring policy to govern.
