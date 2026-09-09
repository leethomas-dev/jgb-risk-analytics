# Phase 4.5A Documentation — `models/bootstrap.py`

## In plain English

Every calculation in this project so far has priced a bond by looking up
one interest rate per payment date, straight off the curve of rates the
government publishes. This part builds a cleaner, more theoretically
correct set of rates from that curve — a "zero curve" — by working
outward from the shortest loan length to the longest, using each step's
own payment schedule to solve for exactly one new rate at a time. It then
runs the sharpest test available for "did the arithmetic work": take a
hypothetical bond built from the published curve's own numbers, price it
using the new zero curve instead, and check it comes back to the same
value either way. It does, to a razor-thin margin.

**An important correction folded in after the first version of this
page:** that test only proves the *arithmetic* is self-consistent. It
doesn't prove the *starting assumption* is realistic — and checking
against the government's own published methodology shows that assumption
is a genuine simplification, not a fact about the data (§1 below). A
second check, new in this revision, measures how big an approximation
that simplification actually is.

---

## Technical details

Bootstraps a zero-coupon (spot) discount curve, **treating** an observed
JGB curve **as if it were a par yield curve**. Reads no curve of its own —
takes a curve DataFrame (the same shape `load_jgb_curve()` returns) and
reuses `bond_pricing.curve_yield_at()` for interpolation, so the
curve-shape assumption feeding the bootstrap is identical to the one
`price_bond` already uses everywhere else.

**File:** `models/bootstrap.py` — `bootstrap_zero_curve()` (the public
entry point), `zero_rate_at()` (interpolate the resulting zero curve, the
zero-curve analogue of `curve_yield_at`), `discount_factor_at()` (a
discount factor for an arbitrary maturity, built from the interpolated
zero rate), `price_via_zero_curve()` (price a bond off the zero curve
instead of the par curve), `implied_ytm()` (solve a bond's own
yield-to-maturity by inverting `price_bond`), `coupon_effect_sensitivity()`
(the new check quantifying §1's simplification), `_semiannual_grid()`
(the coupon-date grid the bootstrap runs on).

**Output contract:** `bootstrap_zero_curve(par_curve, freq=2) ->
pd.DataFrame`, columns `[maturity_years, discount_factor, zero_rate]`,
sorted ascending — one row per semiannual grid point from one period out
to the input curve's own longest maturity, plus any input tenor below one
year carried through unchanged (see §2.3).

---

## 1. What MOF's curve actually is — verified against source

Bootstrapping needs a **par curve**: at each maturity, the coupon rate
that would price a bond of that maturity to exactly 100. That's what
supplies the known "price = 100" side of every step's equation (§2.1).
Before building on that assumption, it was checked directly against MOF's
own published methodology, rather than assumed.

**Source checked:** `outline-e.pdf` ("Calculation method of interest rate
(Outline)"), linked from the Q&A page
(`https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm`)
off the same reference-rate page Phase 1's loader targets
(`docs/phase_1_documentation.md`). In full:

> **1. Grids Setting** — Grids are set up at intervals of one year from 1
> year to 40 years.
> **2. Selection of JGBs for the calculation at each grid** — [maps grid
> years 1–2 / 3–5 / 6–10 / 11–20 / 21–30 / 31–40 to the 2Y / 5Y / 10Y /
> 20Y / 30Y / 40Y benchmark JGB classes]
> **3. Selection of specific securities** — (1) The on-the-run securities
> which have the largest issue number in the same class of JGBs are
> selected. (2) Besides (1), securities whose remaining maturity is the
> nearest to grid year are selected on both sides of each grid.
> **4. Formation of the yield curve** — The yield curve is formed by
> interpolating through a **cubic spline function**, utilizing the
> **prevailing market yields** of securities selected in 3 as contact
> points.
> **5. Calculation of interest rates** — From the yield curve of 4, the
> interest rates on a constant maturity basis are calculated.

The companion Q&A page adds that these are "the semiannual compound
interest rate on a constant maturity basis calculated on prevailing
[secondary-market] prices of fixed income JGBs," sourced from JSDA's
"Reference Statistical Prices (Yields) for OTC Bond Transactions."

**Finding: this is not a par curve.** MOF selects specific, real,
outstanding benchmark JGB issues trading nearest each maturity grid
point, takes their actual secondary-market **yields to maturity** —
whatever coupon and price those bonds actually trade at, never assumed to
be 100 — and fits a **cubic spline through those YTM points**. No step
prices anything to par, and no bootstrapping is involved anywhere in
MOF's own process. This is a fitted YTM curve on real, non-par-priced
bonds. The methodology document is explicit and unambiguous on this
point — there is no genuine ambiguity to flag here.

**Why this module treats it as a par curve anyway.** Bootstrapping
mechanically requires the "prices to 100" assumption somewhere. Treating
a constant-maturity YTM series as if it were a par curve is a
**standard, widely used simplification** in practice (the same treatment
routinely applied to, e.g., US Treasury CMT rates) — kept here
deliberately, not restructured, with its resulting error measured
directly rather than left unquantified (§5).

**The mechanism: the coupon effect.** Two bonds of the same maturity but
different coupons have different YTMs even off one identical, correctly
bootstrapped zero curve, because their cash flow timing differs: a
low-coupon bond returns more of its value at the far end of the curve
(closer to a zero-coupon bond); a high-coupon bond returns more of it
earlier. On a curve that isn't flat, that pulls each bond's own YTM away
from the "par rate" at its maturity — the size and direction driven by
the curve's own slope and curvature there. This matters unusually much
for JGBs specifically: many outstanding issues carry near-zero coupons
left over from the 2016–2024 ZIRP/YCC era and now trade at deep
discounts, so the coupon dispersion across the real benchmark issues MOF
actually samples is wide — exactly the condition under which this
simplification's error is largest.

---

## 2. What each piece does, and why

### 2.1 The method: iterative bootstrapping, no numerical optimizer

A curve **treated as** a par curve prices a bond, by construction, to its
own face value under its own quoted rate (§1 on why that's an assumption,
not a fact, for this specific input). Working outward from the shortest
maturity, each step's par-pricing equation has exactly one unknown — the
discount factor for that maturity's own final cash flow — because every
earlier cash flow's discount factor was already solved at a shorter step.
That's one linear equation in one unknown at every step, solved in closed
form:

```
DF_n = (1 - coupon_n * sum(DF_1..DF_{n-1})) / (1 + coupon_n)
```

No root-finding or optimization is needed anywhere in this recursion —
that machinery is reserved for Part B's curve-fitting problem (a
genuinely different, nonlinear problem) and for `implied_ytm()` (§5),
which inverts a single bond's price rather than bootstrapping a curve.

### 2.2 Design decision 1 — the cash flow grid: semiannual, via `curve_yield_at`

MOF quotes rates at ~12–15 discrete tenors, but bootstrapping needs a rate
at *every* coupon date the pricing engine actually uses — semiannual,
matching `price_bond`'s own `freq=2` default. This module interpolates
the input curve onto that finer grid using `bond_pricing.curve_yield_at()`
directly — the same straight-line-interpolate/flat-extrapolate function
`price_bond` already uses for every cash flow, not a second, invented
interpolation rule.

**Why reusing it specifically, not just "an" interpolation:** the
self-consistency test (§3) only isolates a genuine bootstrap error if the
curve-shape assumption feeding the bootstrap is the *same* one
`price_bond` uses elsewhere — a different interpolation method would make
that check compare two competing models of the curve's shape, not verify
the bootstrap arithmetic.

**Why this choice is material, not a formality.** MOF's own grid has real
10-year gaps in the ultra-long region (10Y → 20Y → 30Y → 40Y).
Straight-line interpolation assumes zero curvature across each such gap —
it cannot see, and will not reproduce, any actual hump or bow the true
curve has between quoted points. A curvature-aware method (a cubic
spline — notably, the very method MOF's own methodology uses to build the
curve in the first place, §1 — or the parametric fit built in Part B)
would shape that region differently. Part B's Nelson-Siegel/Svensson fit
is this project's principled way of asking whether the data actually
supports curvature in that region, rather than assuming none
(`docs/phase_4_5b_documentation.md`).

### 2.3 Design decision 2 — the short end: used directly, not bootstrapped

Money-market instruments (MOF's 1M/3M/6M tenors) pay no intermediate
coupon — there's nothing to bootstrap through. Any input tenor below
`MONEY_MARKET_CUTOFF_YEARS` (1.0) is carried into the output as its own
zero rate, unchanged.

This isn't a bolted-on special case. The bootstrap recursion above, run
at the very first semiannual grid point (0.5Y, with no prior period to
net out — `cumulative_df = 0`), reduces algebraically to:

```
DF_1 = 1 / (1 + y_1/freq)
```

— exactly the standard single-period discounting of the quoted rate
directly, with no earlier coupon to net out. Solving for the equivalent
zero rate at that same point gives back `y_1` itself exactly (worked
through in full below). This is checked directly, not just asserted:
`test_zero_rate_at_first_grid_point_equals_quoted_par_yield` confirms the
algebraic identity holds in the actual implementation.

**The algebra, worked through**, since it's the basis of the whole design
decision: with `T_1 = 1/freq` and `DF_1 = 1/(1+y_1/freq)`,

```
zero_rate_1 = freq * (DF_1^(-1/(freq*T_1)) - 1)
            = freq * (DF_1^(-1) - 1)                [since freq*T_1 = 1]
            = freq * ((1 + y_1/freq) - 1)
            = y_1
```

### 2.4 Design decision 3 — compounding: semiannual, and kept consistent

`freq=2` (semiannual) matches `bond_pricing.price_bond`'s own default
exactly, and is a genuine parameter, not hardcoded —
`test_freq_changes_the_grid_spacing` confirms `freq=1` produces an annual
grid instead. A mismatch between the compounding convention used to
*build* this zero curve and the one used to *discount against it* later
is a silent, hard-to-find error. `test_freq_matching_between_bootstrap_
and_reprice_is_required_for_par` makes this concrete: holding the
cash-flow schedule fixed and changing only the compounding `freq` passed
to `discount_factor_at` moves a bond that should reprice to exactly 100
to 100.126 instead — a small-looking number that is nonetheless the
entire class of bug this design decision exists to prevent.

### 2.5 `zero_rate_at` / `discount_factor_at` / `price_via_zero_curve`

`zero_rate_at` is the zero-curve analogue of `curve_yield_at` — same
straight-line-interpolate/flat-extrapolate policy, same reasoning.
`discount_factor_at` computes a discount factor from the *interpolated
zero rate*, not by interpolating the `discount_factor` column directly —
one interpolation rule, applied consistently. `price_via_zero_curve`
builds a bond's cash-flow schedule the same way `price_bond` does and
discounts each flow via `discount_factor_at` — the zero-curve-based
pricing function used by the self-consistency test (§3) and the
coupon-effect check (§5) alike, so both share one implementation rather
than each inlining its own. All three are also the functions a future
zero-curve discounting path in `price_bond` itself (Part C) would call.

---

## 3. The self-consistency test — what it does, and does not, show

Take one bond per curve-quoted tenor — coupon equal to that tenor's own
quoted rate, **treated** as a par yield (§1) — and reprice it by
discounting its cash flows off the bootstrapped zero curve instead. A
real run against the live MOF curve (`prefer_live=True`, at the point of
writing):

```
   1.000Y  coupon=1.5570%  reprice = 100.000000  gap = +1.42e-14
   2.000Y  coupon=1.8480%  reprice = 100.000000  gap = +4.26e-14
   5.000Y  coupon=2.2580%  reprice = 100.000000  gap = -5.68e-14
  10.000Y  coupon=2.8960%  reprice = 100.000000  gap = -4.26e-14
  20.000Y  coupon=3.7170%  reprice = 100.000000  gap = -8.53e-14
  30.000Y  coupon=3.9610%  reprice = 100.000000  gap = +1.14e-13
  40.000Y  coupon=3.9650%  reprice = 100.000000  gap = -5.68e-14
```

Every tenor reprices to within `1e-13` of par — floating-point noise, not
an approximation. This isn't a coincidence of tolerance: because every
curve-quoted tenor in this project's real data is a whole or half year,
its cash-flow times land *exactly* on the semiannual bootstrap grid, so
the very equation used to *solve* the discount factor at that tenor *is*
that same bond's own par-pricing equation. A failure here would mean the
bootstrap arithmetic itself is wrong, not a rounding or interpolation
artifact.

**What this does not show — the knock-on.** This is a test of **internal
consistency**: given the par-curve assumption as an input, does the
bootstrap arithmetic correctly invert it? It passes by construction for
*any* input treated as a par curve, correct or not, because the discount
factors were themselves solved *from* that same assumption — it cannot,
even in principle, detect that the assumption is a simplification of what
MOF actually publishes (§1). `test_self_consistency_passing_does_not_
imply_par_curve_input_fidelity` demonstrates this directly. **Input
fidelity** — how far the par-curve assumption actually is from reality —
is a different question, and it's what §5's coupon-effect check measures
instead. The passing self-consistency test above should not be read as
validation of the par-curve assumption itself.

`tests/test_bootstrap.py` runs the self-consistency check against both
real grid shapes (the 12-point snapshot and a 15-point live/cache-shaped
fixture, `prefer_live=False` for determinism) with an explicit `abs=1e-6`
tolerance — tighter than needed by several orders of magnitude,
deliberately, so a real bootstrap bug would fail it clearly.

---

## 4. Other checks: monotonicity, decreasing discount factors, flat curve

**Monotonic maturity ordering** and **strictly decreasing discount
factors** are checked directly against the real snapshot curve — both
hold, as expected for an upward-sloping, all-positive-yield curve.

**A flat input curve produces an equally flat zero curve.** Provable
directly from the recursion: if every `y_n = y0`, then by induction
`DF_n = (1+y0/freq)^(-n)`, which is exactly the standard flat-rate
discount factor at `T_n = n/freq` — so `zero_rate_n = y0` for every grid
point. `test_flat_par_curve_produces_an_equally_flat_zero_curve` confirms
this holds in the implementation, to `atol=1e-10`. This is also, usefully,
a limiting case of §5's coupon effect: on a flat curve the coupon effect
is exactly zero, since every cash flow discounts at the same rate
regardless of when it lands (`test_coupon_effect_is_zero_on_a_flat_curve`).

---

## 5. The coupon-effect sensitivity check — quantifying the simplification

`coupon_effect_sensitivity(par_curve)` measures how large §1's
approximation actually is, without needing real individual-bond price
data (which this project doesn't have yet — see "what would change this
design" below). For each input tenor, it:

1. Bootstraps the zero curve as normal (§2.1–2.4).
2. Prices a **hypothetical zero-coupon bond** of that same maturity off
   that same zero curve (`price_via_zero_curve`), and solves *that bond's
   own* YTM (`implied_ytm`, which inverts `price_bond` by bisection —
   bond price is strictly decreasing in yield, so the root is unique; no
   scipy dependency needed, matching this project's existing
   no-heavy-dependency-for-one-use pattern, `docs/phase_4b_documentation.md`
   §1.2).
3. Does the same for a **hypothetical high-coupon bond** (coupon = 2×
   the input rate, `DEFAULT_HIGH_COUPON_MULTIPLE` — an arbitrary but
   round, reproducible stand-in for an older, premium-priced JGB, not
   fitted to any real issue).
4. Reports each scenario's YTM gap against the input rate, in basis
   points.

A real bond trading at that maturity away from the assumed par coupon —
not hypothetical for JGBs, given real near-zero-coupon legacy issues from
the ZIRP/YCC era (§1) — would show a YTM gap of roughly this size and
direction, purely from the coupon effect, holding the curve shape fixed.

**Measured result, against the committed snapshot curve**
(`prefer_live=False`, reproducible):

| Tenor | Input rate | Zero-coupon YTM | Gap | High-coupon (2×) YTM | Gap |
| --- | --- | --- | --- | --- | --- |
| 1Y | 1.1200% | 1.1206% | +0.06bp | 1.1194% | -0.06bp |
| 2Y | 1.4000% | 1.4026% | +0.26bp | 1.3975% | -0.25bp |
| 3Y | 1.6000% | 1.6054% | +0.54bp | 1.5949% | -0.51bp |
| 5Y | 1.8200% | 1.8304% | +1.04bp | 1.8106% | -0.94bp |
| 7Y | 2.1900% | 2.2211% | +3.11bp | 2.1635% | -2.65bp |
| 10Y | 2.4000% | 2.4441% | +4.41bp | 2.3655% | -3.45bp |
| **20Y** | 3.3200% | 3.5840% | **+26.40bp** | 3.1829% | **-13.71bp** |
| **30Y** | 3.7300% | 4.2084% | **+47.84bp** | 3.5677% | **-16.23bp** |
| **40Y** | 3.9100% | 4.5382% | **+62.82bp** | 3.7677% | **-14.23bp** |

**Reading this, per the phase brief's expectation:** the gap is a
fraction of a basis point out to 5Y, single digits out to 10Y, and jumps
sharply in the **ultra-long segment (20Y–40Y)** — 26 to 63bp for the
zero-coupon scenario — exactly the maturities this project's portfolio is
most concentrated in (`docs/phase_3c_documentation.md`'s ~51% ultra-long
DV01 share). This matches the expected mechanism directly: the gap grows
with maturity (more time for cash-flow timing to matter) and with curve
steepness/curvature (the snapshot curve's steepest segment is exactly
10Y→20Y, `docs/phase_1_documentation.md`'s own curve). The gap is also
asymmetric and non-monotonic past 20Y (peaking at 30Y, easing slightly by
40Y) — a real, curve-shape-dependent feature, not a bug: it tracks the
snapshot curve's own long-end flattening (`docs/phase_1_documentation.md`
§1.6 notes this kind of long-end behavior is a known, real JGB feature),
not an artifact of this check.

**Direction matches theory:** on this upward-sloping curve, the
zero-coupon (low-coupon) scenario's YTM sits *above* the input rate at
every tenor (more of its value returns at the higher-yielding long end),
and the high-coupon scenario's YTM sits *below* it (more value returns
earlier, at the lower-yielding short end) —
`test_coupon_effect_direction_matches_theory_on_an_upward_sloping_curve`
checks both directions explicitly, not just "some gap exists."

**The principled alternative, not built here:** fit Nelson-Siegel/
Svensson (or bootstrap) directly against observed prices of real,
individual benchmark JGB issues — never assuming any bond prices at par.
This is blocked on a data source this project doesn't have yet: a MOF
issue reference module supplying real outstanding issues with their real
coupons and secondary-market prices. Until that exists, the par-curve
simplification is the practical option, used with its error measured
(above) rather than left unquantified.

---

## 6. Known limitations (for the SR 11-7 validation report)

**6.1 The input curve is treated as a par curve; it is actually a fitted
YTM curve on real benchmark issues (§1).** This is the primary limitation
of this module, verified against MOF's own methodology rather than
assumed. Its measured size is §5's coupon-effect table — small at the
front end, tens of basis points in the ultra-long segment this project's
portfolio is most exposed to. Every zero rate, and everything built on
this zero curve later (Part C's zero-curve pricing path), inherits this.

**6.2 The self-consistency test (§3) validates arithmetic, not input
fidelity.** Restated here because it's an easy result to over-read: a
clean, near-zero self-consistency gap says the bootstrap correctly
inverts its own assumption, and says nothing about whether that
assumption matches the real market (§1, §3's "knock-on" discussion).

**6.3 The interpolation choice shapes the ultra-long zero curve, and this
is unverified against the true market shape.** Straight-line
interpolation of the input curve's par-treated rates across MOF's 10-year
gaps (10Y–20Y–30Y–40Y) assumes no curvature the data can't see — a real,
stated limitation, not an oversight (§2.2). Part B's parametric fit
offers one way to check this assumption against the data rather than
simply relying on it.

**6.4 The high-coupon scenario's multiplier (2×) is an arbitrary,
reproducible convention, not fitted to a real issue.** It's useful for
showing the coupon effect's rough symmetry and order of magnitude, not as
a claim about any specific outstanding JGB's actual coupon.

**6.5 Inherits the pricing engine's underlying conventions.** The
semiannual compounding convention (§2.4) and the treatment of sub-1-year
instruments as zero-coupon-equivalent (§2.3) are both deliberate,
documented simplifications consistent with the rest of this project, not
a fully general treasury-curve-construction implementation.

**6.6 This module's own correctness rides on `curve_yield_at`'s and
`price_bond`'s existing validation.** No new interpolation or pricing
logic was written for the bootstrap itself, and `implied_ytm` inverts
`price_bond` directly rather than reimplementing bond pricing — a bug in
either would already have been caught (or not) by Phase 2B's own tests.

---

## 7. What would change this design

**Fitting directly against real individual bond prices** (§5's
"principled alternative") is the actual fix for §6.1/§1 — not an
interpolation change, but a different, non-par-assuming construction
entirely. Blocked on a future MOF issue reference module (real issues,
real coupons, real secondary-market prices), which this project does not
have yet.

**A curvature-aware interpolation method for the ultra-long region**
(§6.3), if the data supports it, would slot into the one call to
`curve_yield_at` inside `bootstrap_zero_curve` — nothing about the
recursion itself would need to change. Part B's parametric fit is the
mechanism this project actually uses to investigate this now.

**A different compounding convention or day-count basis** (§6.5), if
ever needed for a specific instrument class, would be a parameter change
(`freq`) or a new short-end branch — additive, not a rewrite of the
recursion.

**A calibrated (rather than arbitrary 2×) high-coupon scenario** (§6.4)
would need real coupon data across outstanding issues — the same MOF
issue reference module blocking the principled alternative above.

---

## 8. Relationship to the fallback re-anchoring policy and earlier phases

This module adds no new hardcoded market data of its own — it computes
directly from whatever `load_jgb_curve()` (or any curve of the same
shape) returns, inheriting that module's re-anchoring policy
(`docs/phase_1_documentation.md` §5) rather than adding a new one.
`DEFAULT_HIGH_COUPON_MULTIPLE` (2.0) is a methodology convention (§6.4),
not an observation of anything that moves, so there's nothing new here
for that policy to separately govern.

**Resolves, in part, the no-zero-curve caveat carried since Phase 2B —
with a new, more precisely characterized caveat of its own.** Phase 2B
(`docs/phase_2b_documentation.md` §3.2), Phase 3A
(`docs/phase_3a_documentation.md` §3.1), and Phase 3B
(`docs/phase_3b_documentation.md` §3.1) all named the same simplification:
pricing discounts off the curve's own quoted rate directly, not a more
rigorously derived zero rate. This module builds a more rigorous zero
curve — but, per §1, from an input that itself turned out not to be the
par curve bootstrapping ideally wants, so the result trades one named
simplification for a different, smaller, and now-measured one (§5). It is
not yet wired into `price_bond` — that integration, and the quantified
before/after pricing difference it enables, is Part C
(`docs/phase_4_5c_documentation.md`). Until Part C lands, `price_bond`'s
default behavior is unchanged, and the earlier phases' caveats still
describe the code path actually used by default; each has been annotated
to point to this resolution rather than left to assert a limitation that
is (partially) fixed.
