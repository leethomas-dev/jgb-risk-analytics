# Phase 3B Documentation — `models/dv01.py`

## In plain English

Phase 3A measures interest-rate risk as a percentage of a bond's price,
broken down by loan length. This part converts that same risk into an
actual currency amount: specifically, how many yen a bond — or the whole
portfolio — would gain or lose if interest rates moved by one hundredth
of one percent, a small standard amount used throughout the finance
industry as a common yardstick. That currency figure, broken down by loan
length the same way Phase 3A's percentage figure is, is the number
someone would actually use to decide the size of a hedge — a separate,
offsetting investment meant to protect against interest rate moves.

---

## Technical details

DV01 ("dollar value of 01"): the currency-terms price sensitivity to a 1bp
move in yield — the same underlying sensitivity Key Rate Duration
(Phase 3A) measures in percentage/duration terms, rescaled into currency
units so it can be read directly as a hedge size. Reads the portfolio via
`config.portfolio_loader.load_portfolio()` (Phase 2A), prices via
`models.bond_pricing.price_bond()` (Phase 2B), and reuses
`models.key_rate_duration.effective_duration_bond()` /
`key_rate_duration_bond()` (Phase 3A) directly — this module computes no
new price sensitivity of its own; it is a currency-unit conversion layer
over the two phases beneath it.

**File in this part:** `models/dv01.py` — `dv01_bond()` (single-bond DV01),
`dv01_by_tenor_bond()` (per-tenor DV01, the hedge-ratio table),
`dv01_portfolio()` (per-bond portfolio aggregation), `dv01_by_tenor_portfolio()`
(per-tenor portfolio aggregation), `__main__` (loads the real portfolio and
curve, prints both DV01 tables and the validation check).

**Output contract:** `dv01_bond(face_value, coupon_rate, maturity_years,
curve, freq=2, bump_size=0.0001) -> float`, currency per 100 face value,
for a `bump_size` move in yield. `dv01_by_tenor_bond(...) -> pd.Series`,
indexed by the curve's own `maturity_years` (ascending), same units.
`dv01_portfolio(portfolio, curve, freq=2, bump_size=0.0001) -> pd.DataFrame`
with columns `[name, maturity_years, coupon_rate, weight, price,
modified_duration, dv01]`, one row per bond, in portfolio order — no
baked-in total row. `dv01_by_tenor_portfolio(...) -> pd.DataFrame`, indexed
by bond name (portfolio order) plus a final `portfolio_total` row, columns
= the curve's tenors (ascending).

---

## 1. What each piece does, and why it was built that way

### 1.1 Currency units: why "per 100 face," never notional-scaled

**What it does**

Every DV01 figure in this module is in the same units `price_bond` already
uses: currency per 100 face value (JPY, for JGBs). Nothing in this module
multiplies by an actual position size or assets-under-management figure.

**Why it was built this way**

The Part B requirements named three candidate conventions and asked for
one, made explicit: DV01 per 100 face, DV01 per bond (i.e. per the bond's
own `face_value`, which happens to also be 100 for every bond in the
illustrative portfolio — Phase 2A §1.1), or DV01 scaled to some assumed
notional. "Per 100 face" was chosen:

- **It costs nothing.** `price_bond`, `price_portfolio` (Phase 2B §1.3),
  and `key_rate_duration_portfolio` (Phase 3A §1.4) already all report
  figures "per 100 face" without any notional scaling. Matching that
  convention exactly means every DV01 number in this module is directly
  comparable to the price and KRD figures already produced elsewhere in
  the project, with no unit conversion a reader has to track or get wrong.
- **It doesn't pretend to a precision the data doesn't have.** The
  portfolio is illustrative (Phase 2A's own disclaimer: no ISINs, no real
  coupon schedules, weights not derived from any real fund's holdings or
  AUM). Scaling by a "notional" would mean inventing a dollar amount with
  no more basis than the coupons and weights already carry — introducing
  a second illustrative number to explain rather than removing one.
  "Per bond" (i.e. per this bond's own `face_value`) happens to coincide
  with "per 100 face" for every bond in the shipped portfolio (all
  `face_value = 100`), so it was not chosen as a separate convention; the
  two are the same number here, and "per 100 face" is the more general
  statement of what's actually being computed, since it does not depend on
  every bond happening to share the same face value.
- **A real trading book would scale this by real notional.** That
  substitution, when it happens, is additive and localized — see §4.2.

### 1.2 `dv01_bond(face_value, coupon_rate, maturity_years, curve, freq=2, bump_size=0.0001)`

**What it does**

```
DV01 = Price * ModifiedDuration * bump_size
```

`Price` is `price_bond(...)`; `ModifiedDuration` is
`effective_duration_bond(...)` (Phase 3A §1.3) — the same central-difference,
parallel-shift modified duration Phase 3A already computes and validates.
Neither is recomputed here.

**Why it was built this way**

- **`bump_size` does double duty, deliberately.** It is both the numerical
  differencing step `effective_duration_bond` uses internally to estimate
  the derivative, _and_, by definition, the shock size this DV01 figure
  represents. The default (`DEFAULT_BUMP_SIZE`, 1bp, imported from
  `key_rate_duration.py` rather than redefined) is what makes this a
  genuine "DV01" — dollar value of _one basis point_, specifically — not
  the dollar value of some other shock size. A caller passing a different
  `bump_size` gets a real, correctly-computed number; it is just no longer
  "DV01" in the traditional sense if they do, and the docstring says so.
- **No pricing or differencing logic is repeated here.** `dv01_bond` is
  two function calls and a multiplication. This is deliberate: Phase 3A
  already validated `effective_duration_bond` (the sum-of-KRDs sanity
  check, Phase 3A §2); re-deriving modified duration a second way inside
  this module would either duplicate that validation work or risk a
  second implementation quietly drifting from the first.

### 1.3 `dv01_by_tenor_bond(face_value, coupon_rate, maturity_years, curve, freq=2, bump_size=0.0001)`

**What it does**

```
DV01_k = Price * KRD_k * bump_size
```

`Price` is one `price_bond(...)` call (shared across every tenor — a
bond's price does not depend on which tenor's DV01 is being asked about);
`KRD_k` is `key_rate_duration_bond(...)`'s per-tenor Series (Phase 3A
§1.1). Returned as a `pandas.Series` indexed by the same `maturity_years`
KRD uses.

**Why it was built this way**

- **This is the number the Part B requirements call out specifically as
  "what converts into hedge ratios."** A duration or KRD figure (years,
  or a dimensionless-ish sensitivity) tells a reader _how much_ risk sits
  at a tenor; DV01 tells them _how many currency units_ — the number
  actually needed to size a hedge instrument against a specific point on
  the curve, e.g. "how much of a 10Y hedge do I need to offset this
  book's 10Y exposure."
- **A pure rescaling, so it inherits Phase 3A's own accuracy property
  with no new error.** Because `DV01_k` is `KRD_k` multiplied by the same
  two constants (`Price` and `bump_size`) at every tenor, summing this
  Series across tenors approximates `dv01_bond(...)` to the same
  precision `key_rate_duration_bond`'s per-tenor sum approximates
  `effective_duration_bond` (Phase 3A §2, ~`10⁻⁵` relative) — the
  rescaling is linear and does not introduce a second source of gap.
  `test_sum_of_dv01_by_tenor_approximates_dv01_bond` checks this directly
  rather than assuming it follows from Phase 3A's own check.

### 1.4 `dv01_portfolio(portfolio, curve, freq=2, bump_size=0.0001)`

**What it does**

One row per bond (portfolio order): `[name, maturity_years, coupon_rate,
weight, price, modified_duration, dv01]`. No baked-in total row.

**Why it was built this way**

- **Mirrors `price_portfolio`'s shape, not `key_rate_duration_portfolio`'s
  — a deliberate choice between the project's two existing portfolio-
  table conventions.** `dv01_portfolio` is structurally the direct DV01
  extension of `price_portfolio` (Phase 2B §1.3): same one-row-per-bond
  shape, two new columns appended (`modified_duration`, `dv01`), same
  question answered ("what is each bond's own figure"). A portfolio-level
  total is exactly as computable from this table as `price_portfolio`'s
  own weighted price already is — `(df.weight * df.dv01).sum()`, valid
  directly because `load_portfolio()` guarantees weights sum to 1.0
  (Phase 2A §1.4), the same weight convention Phase 2B and Phase 3A both
  already rely on. No new aggregation rule is introduced by leaving the
  total out of the DataFrame.
- **`dv01_by_tenor_portfolio` (§1.5) makes the opposite choice on
  purpose**, for the matching reason: it mirrors
  `key_rate_duration_portfolio` (baked-in `portfolio_total` row) because
  it answers a per-_tenor_ question, not a per-_bond_ one — the same
  split in shape Phase 2B and Phase 3A already established between
  "one row per bond, no total" and "one row per bond plus a total row,"
  now applied consistently to a third metric instead of introducing a
  third convention.

### 1.5 `dv01_by_tenor_portfolio(portfolio, curve, freq=2, bump_size=0.0001)`

**What it does**

```
DV01_portfolio,k = sum_i weight_i * DV01_i,k
```

One row per bond (indexed by name, portfolio order) plus a final
`portfolio_total` row, one column per curve tenor.

**Why it was built this way**

- **The same weighted-sum aggregation `key_rate_duration_portfolio`
  already uses for KRD** (Phase 3A §1.4), applied here to DV01's
  currency-terms values instead of KRD's percentage-terms ones. No new
  aggregation rule — the same one reapplied to a different metric.
- **This is the table that actually sizes tenor-specific hedges at the
  portfolio level**: "how many currency units does the whole book gain or
  lose if only the 20Y point moves 1bp" — the portfolio-level analogue of
  §1.3's per-bond hedge-ratio table.
- **Cross-checked against `dv01_portfolio`'s own weighted total**
  (`test_dv01_by_tenor_portfolio_total_matches_dv01_portfolio_weighted_total`):
  fully summing either table's numbers should land on the same portfolio-
  level figure, since both are two different breakdowns of the same
  underlying per-bond DV01 values.

### 1.6 `__main__`

**What it does**

Loads the curve via `load_jgb_curve()` and the portfolio via
`load_portfolio()` (both real, non-pinned defaults), prints the per-bond
DV01 table, the portfolio-level weighted DV01, the per-tenor DV01 table,
and — for every bond — the formula-vs-direct-bump validation check (§2).

**Why it was built this way**

- **Uses the real, non-pinned defaults**, matching every prior phase's own
  `__main__` block (Phase 1, 2A, 2B, 3A): a person running this file
  directly wants to see what the metric produces right now, including
  which curve tier served it.
- **Prints the validation check itself, not just the DV01 tables.** Same
  reasoning as Phase 3A §1.5: making the formula/direct gap visible on
  every real run, not only inside the test suite, means a future change
  that quietly breaks the relationship (§2) would show up the first time
  someone runs the module directly.

---

## 2. The validation check: DV01 formula vs. a direct bump-and-reprice

Per the Part B requirements, `tests/test_dv01.py` includes
`test_dv01_formula_matches_direct_bump_for_default_portfolio`, comparing
`dv01_bond(...)` against an independently-computed reference —
`price_bond` called once on the unbumped curve and once on a curve with
every tenor shifted `+1bp`, with no call to `dv01_bond`,
`effective_duration_bond`, or `key_rate_duration_bond` in the reference
computation. This is a **genuine, non-circular check**: the formula path
uses `effective_duration_bond`'s _central_ difference (up `1bp` and down
`1bp`, averaged — Phase 3A §1.2), while the reference path uses a real,
_one-sided_ `+1bp` shock — the two are independent computations of related
but not identical quantities, so agreement is informative rather than
tautological.

A real run against the live MOF curve (`python -m models.dv01`) shows:

| Bond    | formula  | direct bump | gap      |
| ------- | -------- | ----------- | -------- |
| JGB_2Y  | 0.019393 | 0.019390    | +0.0124% |
| JGB_5Y  | 0.046153 | 0.046141    | +0.0269% |
| JGB_10Y | 0.082298 | 0.082257    | +0.0500% |
| JGB_20Y | 0.131264 | 0.131146    | +0.0897% |
| JGB_30Y | 0.164150 | 0.163951    | +0.1213% |
| JGB_40Y | 0.192405 | 0.192122    | +0.1473% |

**Why the formula is consistently a touch _larger_ than the direct bump,
and why the gap grows with maturity.** `ModifiedDuration` is a first-order
(linear) sensitivity measure — it assumes price falls by a constant
multiple of the yield move, however large that move is. A real bond's
price is convex in yield, not linear: as yield rises, price falls by
_less_ than the linear approximation predicts, because convexity cushions
the loss (symmetrically, a yield fall gains _more_ than the linear
estimate). So a real `+1bp` shock produces a price drop slightly smaller
than `Price * ModifiedDuration * bump_size` predicts — exactly the pattern
observed: `formula > direct`, consistently, at every maturity. Convexity
itself grows with maturity, which is why the gap does too — the same
qualitative story as Phase 3A §2's KRD-sum-vs-effective-duration gap,
which has the identical root cause (a first-order/linear approximation
compared against something that captures a genuine second-order effect).
The test tolerance (`rel=5e-3`, §2 of `tests/test_dv01.py`) is set well
above the largest observed gap (`~0.15%` at 40Y) specifically so that a
real error — a sign flip, a missing `bump_size` multiplication, a units
bug — would blow through it by orders of magnitude, while this expected,
explained convexity gap does not.

---

## 3. Known limitations (for the SR 11-7 validation report)

### 3.1 Inherited caveat: no zero curve — DV01 is computed off par yields directly

`dv01_bond` and `dv01_by_tenor_bond` call `price_bond`, `effective_duration_bond`,
and `key_rate_duration_bond` unchanged, all three of which discount and
bump against the curve's **par** yield, not a bootstrapped zero/spot rate
(Phase 2B §3.2, restated for KRD in Phase 3A §3.1). A DV01 figure invites
an even sharper reading than a KRD figure does, because it is quoted in
currency units that look like a real, tradeable dollar amount: a DV01 of
"0.0823 per 100 face at the 10Y tenor" is the currency sensitivity of the
par-yield-discounted price to a 1bp move in the 10Y par yield — not the
sensitivity of a rigorously zero-curve-discounted price to a move in the
true 10Y zero rate. Nothing in this module corrects for that; every DV01
here inherits the same approximation the price and KRD it's built from
already carry.

Mitigant: named here, in Phase 2B §3.2, and in Phase 3A §3.1; a reviewer
should read every DV01 figure in this project as a currency-scaled
sensitivity of the par-yield pricing approximation, not a market-precise
dollar risk figure.

### 3.2 DV01 tenors are whatever the curve's source tier happens to provide

Same non-fixed-tenor-grid contract as Phase 1 (§4.5), Phase 2B (§1.1), and
Phase 3A (§3.2): `dv01_by_tenor_bond`'s output Series, and
`dv01_by_tenor_portfolio`'s columns, have as many entries as `curve` has
rows, at whatever maturities that source tier quotes. A per-tenor DV01
table computed from the live/cache tier (15 points) and one computed from
the snapshot tier (12 points) are not directly comparable column-for-
column, for the same reason a KRD table isn't (Phase 3A §3.2) — this
module introduces no new instance of the limitation, only a second metric
it now applies to.

### 3.3 "Per 100 face" is not a real notional — DV01 figures are illustrative, not book-level dollar risk

Every DV01 in this module is scaled the same way price and KRD already
are — per 100 face value, with no assumed position size (§1.1). This means
none of the numbers in this module's `__main__` output, or in
`dv01_portfolio` / `dv01_by_tenor_portfolio`, should be read as "how many
JPY this portfolio would actually gain or lose" for any real book: that
would require multiplying by an actual notional or AUM figure this project
does not have, because the underlying portfolio itself is illustrative
(Phase 2A §3.1 — no ISINs, no real coupons, weights not derived from any
real fund's holdings). This is not a new limitation introduced here; it is
the same illustrative-data caveat Phase 2A's portfolio already carries,
restated because DV01 is the first Phase 3 output whose units (currency)
could plausibly be mistaken for a real dollar figure by a reader who
hasn't seen Phase 2A's disclaimer.

### 3.4 The formula/direct-bump gap is convexity, not an error — but it is not exactly zero

§2's gap (up to `~0.15%` relative at 40Y in the illustrative portfolio)
means `dv01_bond(...)` is not bit-identical to a real bumped repricing —
a downstream consumer that needs the _exact_ price impact of a specific
1bp shock (rather than a first-order estimate of it) should reprice
directly with a bumped curve rather than trust the formula to the last
digit. The gap is well below every other approximation already named in
this project (par-yield discounting §3.1, illustrative portfolio data
§3.3), so it is not a practically meaningful source of error for portfolio
risk reporting at these maturities — it should not be asserted to be
exactly zero in a validation write-up, and its magnitude and cause (§2)
should be cited rather than assumed away.

---

## 4. What would change this design

### 4.1 A real zero-curve bootstrap

If §3.1's par-yield approximation were replaced with a bootstrapped zero
curve (Phase 2B §5.1), this module would need no changes beyond passing
the zero curve through instead of the par curve — every function here
already just calls `price_bond` / `effective_duration_bond` /
`key_rate_duration_bond` on whatever curve it's handed. The same "swap
behind a stable interface" property Phase 1, Phase 2B, and Phase 3A were
each built around.

### 4.2 Scaling to real notional or AUM

If a real position size (or a real fund's AUM) were introduced, the
change would be additive and localized: a `notional` parameter (or a
per-bond notional column joined onto the portfolio) multiplying `dv01_bond`
/ `dv01_by_tenor_bond`'s "per 100 face" output by `notional / 100` at the
point of use, most naturally in `dv01_portfolio` / `dv01_by_tenor_portfolio`
or in a caller built on top of them. Nothing in `dv01_bond`'s or
`dv01_by_tenor_bond`'s own formula would need to change — "per 100 face"
was chosen specifically because it is the natural unit to scale up from
(§1.1), not one that would need to be un-done first.

---

## 5. Relationship to the fallback re-anchoring policy

`models/dv01.py` introduces no new hardcoded or fallback market data — it
contains only a currency-unit conversion over Phase 2B's pricing and
Phase 3A's duration/KRD calculations, and reads the curve and portfolio
through the Phase 1 and Phase 2A loaders respectively. The project-wide
re-anchoring policy (Phase 1 doc §6) therefore has no new instance to
govern here, the same conclusion Phase 2B's doc (§6) and Phase 3A's doc
(§5) each reached for their own modules.
