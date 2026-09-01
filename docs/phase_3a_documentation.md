# Phase 3A Documentation — `models/key_rate_duration.py`

Key Rate Duration (KRD): per-tenor price sensitivity. Bumps one grid
tenor's yield up **and** down by 1bp, holds every other tenor fixed,
reprices both via `models.bond_pricing.price_bond()`, and measures the
resulting % price change as a **central (two-sided) finite difference**.
Reads the portfolio via `config.portfolio_loader.load_portfolio()`
(Phase 2A) and prices via `price_bond()` (Phase 2B); this module hardcodes
neither a bond list nor a curve, and does no curve interpolation of its
own — it reuses `price_bond` / `curve_yield_at` exactly as Phase 2B shipped
them.

**File in this part:** `models/key_rate_duration.py` — `key_rate_duration_bond()`
(single-bond KRD), `effective_duration_bond()` (the parallel-shift
benchmark KRDs are checked against), `key_rate_duration_portfolio()`
(portfolio-level aggregation), `__main__` (loads the real portfolio and
curve, prints the KRD table and the sanity check).

**Output contract:** `key_rate_duration_bond(face_value, coupon_rate,
maturity_years, curve, freq=2, bump_size=0.0001) -> pd.Series`, indexed by
the curve's own `maturity_years` (ascending), one KRD value per grid tenor.
`effective_duration_bond(...) -> float`, same signature, one number.
`key_rate_duration_portfolio(portfolio, curve, freq=2, bump_size=0.0001)
-> pd.DataFrame`, indexed by bond name (portfolio order) plus a final
`portfolio_total` row, columns = the curve's tenors (ascending).

---

## 1. What each piece does, and why it was built that way

### 1.1 The bump-shape decision: a point bump becomes a triangular tent, on purpose

**What it does**

`_bump_curve_at(curve_sorted, row_index, bump_size)` adds `bump_size`
(positive or negative) to exactly one row of the (defensively re-sorted)
curve DataFrame and returns the modified copy — nothing more.
`key_rate_duration_bond` calls `price_bond` once against the unbumped
curve for the base price (used only to normalize the result), then twice
per grid tenor — once against a copy bumped `+bump_size` at that row, once
against a copy bumped `-bump_size` at that same row — and computes

```
KRD_k = -(1/P_base) * (P_up_k - P_down_k) / (2 * bump_size)
```

**Why it was built this way**

The Part A requirements named two bump-shape alternatives and asked for a
documented choice: a **pure point shift** (bump only the exact grid
maturity, leaving every other maturity — including one a hair away — fully
unaffected, producing a kink) or a **triangular/tent shape** tapering to
zero at the neighboring tenors. This module implements the triangular
form, and the key design fact is that it did not need to be coded as a
separate shape at all:

- `curve_yield_at` (Phase 2B) already linearly interpolates between
  adjacent grid tenors and flat-extrapolates beyond the ends. Bumping only
  row `k`'s yield, leaving rows `k-1` and `k+1` untouched, changes the
  *interpolated* curve exactly like a tent: full `bump_size` at `t_k`,
  ramping linearly to `0` at `t_{k-1}` and `t_{k+1}`, and unchanged beyond
  those neighbors — because that is what linear interpolation between an
  unmoved point and a moved point produces, not a separate rule this module
  had to write. This holds for either sign of `bump_size` — the up-leg and
  down-leg of the central difference each produce the same tent, mirrored:
  `Δyield(t_{k-1}) = 0`, rising linearly to `Δyield(t_k) = bump_size`, then
  falling linearly back to `Δyield(t_{k+1}) = 0`.
- At the two grid ends, the tent is asymmetric rather than symmetric:
  bumping `t_0` (or `t_{n-1}`) also moves every maturity beyond that end,
  at the *full* `bump_size`, because `curve_yield_at` flat-extrapolates off
  the end tenor's own (now-bumped) yield. So the endpoint tents have one
  sloped side (toward the interior neighbor) and one flat side (extending
  to the extremes of maturity), rather than tapering to zero on both sides.
  For the shortest tenor `t(0)`: `Δyield` is flat at `bump_size` for every
  maturity `<= t(0)`, ramps linearly down to `0` between `t(0)` and `t(1)`,
  then stays flat at `0` for every maturity `>= t(1)` (the mirror image
  holds at the longest tenor).
- Getting the *other* alternative — a true point kink, affecting price only
  if a cash flow lands at exactly `t_k` — would require bypassing or
  special-casing `curve_yield_at`'s interpolation inside this module. That
  was rejected on principle: KRD should measure sensitivity to the same
  curve model `price_bond` actually prices against, not a second,
  KRD-only interpolation invented to get a different bump shape.
- The triangular tents form a **partition of unity**: at every maturity
  `m`, summing all tenors' tent weights gives exactly `1` (an interior
  point is covered by two overlapping ramps summing to `1`; a point beyond
  either end is covered by that end's flat extension, itself `1`). Bumping
  *every* tenor by the same `bump_size` is therefore equivalent to one true
  parallel shift of `bump_size` at every maturity — which is exactly why
  summing a bond's KRDs across all tenors approximates its effective
  duration (§1.3, §2). A pure point-shift bump has no such property: a cash
  flow that never lands on an exact grid tenor is invisible to every
  single-tenor bump, so the KRDs would systematically undercount and the
  sum would diverge from the bond's real duration — precisely the failure
  mode the Part A requirements warned this choice is meant to avoid. Every
  adjacent tenor's tent overlaps its neighbors so their weights always sum
  to exactly 1.0, flat at the two ends.

  Two different quantities are worth keeping distinct here, both verified
  directly against `curve_yield_at` (bumping each grid row by `1.0` in turn
  on a 5-point curve `[t0, t1, t2, t3, t4]` and reading off the resulting
  weight at a fine grid of maturities) rather than assumed:

  - **Each tent taken on its own** goes from `0` (at each of its two
    neighboring tenors) up to `1.0` (at its own tenor) and back to `0`.
    Where two adjacent tents overlap — the interval between two consecutive
    grid tenors — one is descending while the other is ascending, and they
    cross at the interval's midpoint, where both equal exactly `0.5`
    (confirmed numerically: `tent(t0)=1.000`, `tent(midpoint t0–t1)=0.500`,
    `tent(t1)=0.000` for the tent centered at `t0`, and the mirror image
    for its neighbor). The **upper envelope** of all the tents together —
    the largest value in view at each maturity — therefore zigzags between
    `1.0` at every grid tenor and `0.5` at every midpoint between tenors.
    It never reaches `0.0` anywhere in that range: `0.0` is a property of
    one tent viewed in isolation, at a maturity where a *different* tent
    already owns the peak — not a dip the overlaid envelope itself shows.
  - **The sum of all tents at a given maturity** — not just the largest one
    — is a different quantity: exactly two tents are active at any interior
    point (or one flat-extrapolated tent, outside `[t0, t4]`), one falling
    by precisely the amount the other rises, so the total is constant at
    `1.0` everywhere (confirmed numerically at 21 sample points from
    `m=-0.5` to `m=4.5`). This constant-`1.0` sum, not the `0.5`-to-`1.0`
    envelope above, is the "partition of unity" property this section is
    building toward.

### 1.2 One-sided vs. central difference: why this module uses central

Two finite-difference conventions were on the table for turning a bumped
repricing into a derivative estimate: a **one-sided (forward) difference**,
`KRD_k = -(1/P) * (P_bumped_k - P_base) / bump_size` (bump up only, compare
against the already-computed base price), or the **central (two-sided)
difference** this module uses, `KRD_k = -(1/P) * (P_up_k - P_down_k) /
(2 * bump_size)` (bump both up and down, compare the two against each
other). Central was chosen:

- A one-sided forward difference has a **first-order** truncation error in
  `bump_size` — its error shrinks proportionally to `bump_size` itself. A
  central difference's leading error term is **second-order** — it
  shrinks proportionally to `bump_size²` — because the central formula
  cancels the first-order term algebraically (the forward and backward
  Taylor expansions' matching odd-order term subtracts out). At
  `bump_size = 1bp = 0.0001`, that is the difference between an error that
  scales with `10⁻⁴` and one that scales with `10⁻⁸`: a substantial
  accuracy gain for one extra reprice per tenor (a second bumped curve,
  `P_down_k`, alongside `P_up_k`, instead of reusing the single shared
  `P_base`).
- **`key_rate_duration_bond` and `effective_duration_bond` use this same
  method, deliberately** — not just one of them. The
  sum-of-KRDs-vs-effective-duration sanity check (§2) is only a clean
  isolation of a bond's genuine convexity (the reason the two numbers
  aren't bit-identical even with a consistent method — §2) if both sides of
  the comparison use the same finite-difference methodology. Comparing a
  central-difference KRD sum against a one-sided effective-duration
  benchmark, or vice versa, would fold a truncation-error mismatch into the
  comparison alongside the convexity effect the check actually exists to
  surface — muddying exactly the signal it's meant to isolate.

### 1.3 `effective_duration_bond(face_value, coupon_rate, maturity_years, curve, freq=2, bump_size=0.0001)`

**What it does**

The same central-difference methodology as `key_rate_duration_bond`, but
bumping *every* grid tenor by `+bump_size` at once for the up leg, and
every grid tenor by `-bump_size` at once for the down leg — two true
parallel shifts, per §1.1's partition-of-unity argument — and returning

```
D_eff = -(1/P_base) * (P_shifted_up - P_shifted_down) / (2 * bump_size)
```

**Why it was built this way**

- **Exists specifically as the independent benchmark** `key_rate_duration_bond`'s
  summed output is checked against (the sanity check the Part A
  requirements named explicitly — §2 below). It is not a new pricing
  formula: it is `price_bond`, called on two uniformly shifted curves,
  using the same bump-size and finite-difference convention as every KRD
  call so the two numbers are comparable on equal terms.
- **Uses the same central-difference methodology as `key_rate_duration_bond`
  at every step**, for exactly the reason given in §1.2: the two functions
  staying on one method is what keeps the sanity check a clean isolation of
  one specific effect (convexity) instead of a mix of that effect and a
  formula mismatch.

### 1.4 `key_rate_duration_portfolio(portfolio, curve, freq=2, bump_size=0.0001)`

**What it does**

Prices every `Bond` in an already-loaded portfolio against one
already-loaded curve, one `key_rate_duration_bond` call per bond, and
returns a `DataFrame`: one row per bond (indexed by name, portfolio order),
one column per curve tenor, plus a final `portfolio_total` row equal to

```
KRD_portfolio,k = sum_i weight_i * KRD_i,k
```

**Why it was built this way**

- **Takes `portfolio` and `curve` as plain arguments, no loading inside
  it** — the same shape as `price_portfolio` (Phase 2B §1.3), for the same
  reason: no hidden file/network I/O, trivially testable against in-memory
  fixtures, and reusable unchanged against a curve a future caller already
  holds and mutates elsewhere.
- **The weighted-sum total row reuses `price_portfolio`'s own weight
  convention exactly** — `load_portfolio()` already guarantees
  `sum(weight) == 1.0` (Phase 2A §1.4), so `weight_i * KRD_i,k` summed
  across bonds needs no separate notional or normalization step, the same
  way `(df.weight * df.price).sum()` needed none for a portfolio-level
  price. This is the standard portfolio-KRD construction: a market-value-
  weighted average of each holding's own per-tenor KRD.
- **Returns a `DataFrame`, not a dict of Series or a second dataclass** —
  the "rows = bonds, columns = tenors" shape the Part A requirements asked
  for directly, and the natural `pandas` shape for the Part C ultra-long
  segment isolation to slice columns out of.

### 1.5 `__main__`

**What it does**

Loads the curve via `load_jgb_curve()` and the portfolio via
`load_portfolio()` (both real, non-pinned defaults), prints the full
per-bond-plus-total KRD table, then — for every bond — prints
`sum(KRD)` next to `effective_duration_bond(...)` and their gap.

**Why it was built this way**

- **Uses the real, non-pinned defaults**, matching the pattern already
  established in every prior phase's own `__main__` block (Phase 1, 2A,
  2B): a person running this file directly wants to see what the metric
  actually produces right now, including which curve tier served it (the
  curve loader's own `verbose=True` log line reports that).
- **Prints the sanity check itself, not just the KRD table.** The Part A
  requirements frame the sum-vs-effective-duration check as something to
  "investigate rather than paper over" if it fails — making the gap visible
  on every real run, not only inside the test suite, means a future change
  that quietly breaks the tent-shape property (§1.1) or the matched
  finite-difference methodology (§1.2) would show up the first time
  someone runs the module directly.

---

## 2. The sanity check: sum of KRDs vs. effective duration

Per the Part A requirements, `tests/test_key_rate_duration.py` includes
`test_sum_of_krds_approximates_effective_duration` (and a portfolio-wide
variant), asserting `krd.sum() == pytest.approx(eff_dur, rel=1e-5)` for a
range of maturities and coupons, including the shipped illustrative
portfolio. The tolerance is tight enough to be a real check, not a bound
loose enough to pass regardless of whether the underlying construction is
sound (§1.1, §1.2).

A real run against the live MOF curve (`python -m models.key_rate_duration`)
shows:

| Bond | sum(KRD) | effective duration | gap |
| --- | --- | --- | --- |
| JGB_2Y | 1.968 | 1.968 | −0.00000 |
| JGB_5Y | 4.778 | 4.778 | −0.00000 |
| JGB_10Y | 8.916 | 8.916 | −0.00000 |
| JGB_20Y | 14.388 | 14.388 | −0.00000 |
| JGB_30Y | 17.432 | 17.432 | −0.00000 |
| JGB_40Y | 19.441 | 19.441 | −0.00001 |

**Why there's a gap at all, and why it grows (very slightly) with
maturity.** §1.1 established that the tent functions are a partition of
unity, so bumping every tenor by `bump_size` is equivalent, *at the level
of the yield curve itself*, to one true parallel shift. But `price_bond` is
a **nonlinear** function of the curve (each cash flow's discount factor is
a nonlinear function of the yield at its own maturity), so summing
individually-bumped repricings is not exactly the same operation as one
combined repricing — there is a genuine, small convexity cross-term between
the two. That cross-term scales with the bond's convexity, which grows with
maturity, which is exactly the pattern above: indistinguishable from zero
at 2Y, a few parts in `10⁻⁵` (relative) at 40Y. Central differencing (§1.2)
keeps this residual to its true, second-order size — the leading,
first-order error a one-sided formula would carry is cancelled by
construction, so what remains here is the convexity effect itself (plus
ordinary floating-point rounding in `price_bond`'s repeated discounting),
not an artifact of the derivative estimate. This was investigated (per the
Part A instruction to investigate rather than paper over a gap) and traced
to that specific, expected source.

---

## 3. Known limitations (for the SR 11-7 validation report)

### 3.1 Inherited caveat: no zero curve — KRD is computed off par yields directly

`key_rate_duration_bond` and `key_rate_duration_portfolio` bump and
reprice through `price_bond` unchanged, and `price_bond` discounts each
cash flow at the curve's **par** yield interpolated to that cash flow's own
maturity — not a bootstrapped zero/spot rate (Phase 2B §3.2). This module
adds no new approximation on top of that, but it is worth restating plainly
rather than leaving it implicit, because a KRD number invites a sharper
reading than a price does: **a KRD of "8.9 years at the 10Y tenor" is the
sensitivity of a par-yield-discounted price to a 1bp move in the 10Y par
yield, not the sensitivity of a rigorously-discounted price to a 1bp move
in the true 10Y zero rate.** The two can diverge — more so for a bond whose
cash flows are far from a single quoted tenor, and more so the more
curvature the underlying curve has — for the same reason the underlying
price itself carries that caveat (Phase 2B §3.2). Nothing in this module
corrects for it; the bump is applied to, and the sensitivity is measured
against, the par curve as-is.

Mitigant: named here, and referenced from `price_bond`'s own docstring
(Phase 2B §3.2); a reviewer should read any KRD or DV01 (Phase 3B) number
from this project as a sensitivity of the par-yield pricing approximation,
not of a theoretically rigorous zero-curve price.

### 3.2 KRD tenors are whatever the curve's source tier happens to provide

Same non-fixed-tenor-grid contract as Phase 1 (§4.5) and Phase 2B (§1.1):
`key_rate_duration_bond`'s output Series has as many entries as `curve` has
rows, at whatever maturities that source tier quotes. A KRD profile
computed from the live/cache tier (15 points, 1Y–40Y) and one computed from
the snapshot tier (12 points, 1M–40Y) are **not directly comparable
column-for-column** — a "10Y KRD" from one run and a "10Y KRD" from another
mean the same thing only because 10Y happens to be a shared tenor on both
grids; a tenor present on only one grid (e.g. the snapshot's 1M, or the
live grid's 15Y) has no counterpart to compare against on the other. This
is not a new limitation this module introduces — it is Phase 1's existing
contract, restated here because KRD is the first Phase 3 output where a
ragged tenor set across runs is likely to actually matter to a reader
(a duration figure changes by run *and* by which tenors are present to
receive it).

### 3.3 The sum-of-KRDs / effective-duration gap is not exactly zero, but is immaterial

§2's gap (on the order of `10⁻⁵` years of duration at 40Y, smaller at
shorter maturities) means `sum(key_rate_duration_bond(...))` is not
bit-identical to `effective_duration_bond(...)` — a downstream consumer
that needs one canonical duration number for a bond should call
`effective_duration_bond` directly rather than summing the KRD Series and
treating the result as exact. This residual is small enough, relative to
every other approximation already
named in this project (par-yield discounting §3.1, ACT/365.25 maturity
resolution, illustrative portfolio data), that it is not a practically
meaningful source of error in portfolio risk reporting at these
maturities — it should not be asserted to be exactly zero in a validation
write-up, but it also does not warrant further numerical work.

---

## 4. What would change this design

### 4.1 A real zero-curve bootstrap

If §3.1's par-yield approximation were replaced with a bootstrapped zero
curve (Phase 2B §5.1's proposed `bootstrap_zero_curve`), this module would
need no changes at all beyond passing the zero curve through instead of the
par curve — `key_rate_duration_bond` and `key_rate_duration_portfolio`
already just bump `curve['yield']` and call `price_bond`, whatever curve
they're handed. This is the same "swap behind a stable interface" property
Phase 1's and Phase 2B's own designs were built around.

---

## 5. Relationship to the fallback re-anchoring policy

`models/key_rate_duration.py` introduces no new hardcoded or fallback
market data — `DEFAULT_BUMP_SIZE` (1bp) is a methodology constant, not an
observation of anything that moves, and every curve and portfolio value it
touches is read through the Phase 1 and Phase 2A loaders respectively. The
project-wide re-anchoring policy (Phase 1 doc §6) therefore has no new
instance to govern here, the same conclusion Phase 2B's doc reached for
`models/bond_pricing.py` (its §6).
