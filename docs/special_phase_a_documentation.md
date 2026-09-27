# Special Phase A Documentation — zero-curve discounting for the whole risk stack

## In plain English

Until this phase, every price and risk number in this project discounted
each future payment straight off the government's published par yields.
Phase 4.5 built a more correct set of rates (the zero curve) and measured
how different it was — but kept it optional. This phase makes it the
default for everything: prices, duration, DV01, the ultra-long profile,
factor exposure, daily P&L attribution, the cash flow ladder and the
dashboard.

It started as a decision to *not* switch. Checking the numbers behind
that decision showed they were measuring the wrong thing. Measured
properly, the old approach understated the portfolio's exposure to the
curve steepening or flattening by about 45%, and got the direction of
its exposure to the curve's "hump" wrong — the very risks this project
exists to measure. The fix costs a fraction of a second per dashboard
refresh, so it was made before Phase 5 (stress testing and VaR) could
build on the understated numbers.

The number that changes most: **68% of the portfolio's rate risk now
sits in its 20Y+ bonds, not 51%.** Section 6 has every before/after
figure.

---

## Technical details

**What changed.** `models/bond_pricing.py` gains a `basis` setting on
`discount_factors_at` / `price_bond` / `price_portfolio` / `dirty_price` /
`dirty_price_portfolio`: `"zero"` (new default) or `"par"`. On the zero
basis a par curve (`yield` column) is bootstrapped at the bond's own
coupon frequency (`models.bootstrap.bootstrap_zero_curve`, cached by
curve and frequency) and each cash flow is discounted off that zero
curve. Every sensitivity module already worked by "bump the par curve,
reprice", so they now measure **bump par → re-bootstrap → reprice** with
no logic change — they only forward `basis`:
`key_rate_duration.py`, `dv01.py`, `ultra_long_profile.py`,
`factor_exposure.py`, `factor_pnl_attribution.py`, `cash_flow_ladder.py`,
`bond_analytics.py`.

**New:** `models.factor_exposure.compare_factor_exposure_bases()` — the
par-vs-zero factor monitor (§7).

**Deliberately still on `"par"`:** yield-to-maturity, modified duration
and convexity (`bond_analytics.py`, `bootstrap.implied_ytm`) — defined by
one flat yield, and on a flat curve the two bases price identically
(tested). Phase 4.5C's comparison (`zero_curve_impact.py`) — pinned to
`"par"` so it still measures what its doc describes.

**Unchanged:** an explicit zero curve (`zero_rate` column) is discounted
as given, as in Phase 4.5C; asking for `"par"` on one raises rather than
silently reinterpreting it. The history loader, PCA, PC scores and curve
fits don't price anything and are untouched.

**Tests:** 374 → 386, all passing (§4).

---

## 1. How the decision was reached

The sequence matters more than the result: a documented figure was
questioned, found to answer the wrong question, and both the figure and
the conclusion drawn from it were corrected before anything was built on
them.

**Step 1 — the starting point.** Phase 4.5C measured zero-basis DV01 at
**4.7% below** par-basis DV01 and presented it as the cost of the par
simplification. Planning Phase 5, that suggested deferring the switch:
the error looked small and **conservative** (overstating risk).

**Step 2 — the benchmark was wrong.** That 4.7% compared sensitivities
to a shift in **zero** rates. This project's risk factors (Phase 4B's
PCA) are moves in **par** yields. The benchmark that matters is: shift
the par curve, re-bootstrap, reprice on the zero basis. Against it:

| Portfolio DV01 (per 100 face) | Upward curve (snapshot) | Same curve inverted |
| --- | --- | --- |
| A. Par basis (old default) | 0.09627 | 0.14290 |
| B. Zero basis, zero-rate shift (Phase 4.5C) | 0.09172 | 0.14616 |
| C. Zero basis, par shift + re-bootstrap | 0.09749 | 0.13870 |
| **A vs. C** | **−1.3% (understates)** | **+3.0% (overstates)** |

So the error was smaller than thought, but pointed the other way —
**understating** on today's curve — and its sign depends on curve shape.
Two supporting arguments for deferring also fell away: "conservative"
(wrong sign) and "performance cost" (a full re-bootstrapped KRD vector
takes 36ms vs. 28ms).

**Step 3 — the factor check.** Phase 5's risk numbers come from factor
moves, not parallel ones. Pricing each +1 std PCA factor both ways:

| +1 std factor P&L (per 100 face) | Par basis | Zero basis | Par vs. zero |
| --- | --- | --- | --- |
| PC1 (level) | −0.2709 | −0.2766 | −2% |
| **PC2 (slope)** | **−0.0544** | **−0.0986** | **−45%** |
| PC3 (curvature) | +0.0038 | −0.0099 | **sign flips** |
| Combined 1 std factor risk (√ sum of squares) | 0.2763 | 0.2938 | −6% |

Checked for numerical artefacts: the result is linear (0.1× the shock →
0.1× the P&L), symmetric (−1 std mirrors +1 std), and has a clear
mechanism (§2).

**Step 4 — decision: switch before Phase 5.** A 45% understatement of
slope risk can't be called "small relative to other uncertainty", and
slope is exactly what this project's BoJ-policy-versus-fiscal story is
about: the short end moving on policy while the long end moves on term
premium. A wrong sign on curvature isn't an approximation at all. Every
argument for deferring had gone; what remained was the churn of numbers
moving — not enough on its own against a fix that costs milliseconds.

---

## 2. The mechanism: why par pricing misses slope risk

When par yields steepen, bootstrapped zero rates steepen **more**. PC2's
+1 std move raises the 30Y par yield by **2.08bp**; the 30Y zero rate
rises by **3.44bp**. A zero rate at a long maturity has to "make up" for
every earlier coupon having been discounted at lower rates, so it moves
further than the par yield it came from.

Discounting straight off par yields (the old default) moves each cash
flow's rate by the *par* change — so it misses that amplification. For
a **parallel** move there's almost no reshaping to amplify, which is why
the parallel DV01 gap is only ~1.3% while the slope gap is ~45%, and why
a parallel-only check (the first thing measured, §1) could never have
found this.

This also answers "why not just bump the zero curve directly?" (Phase
4.5C's option B): the PCA factors describe par-yield moves. Applying
them to zero rates would impose factor shapes on a curve they weren't
estimated from.

---

## 3. Design

### 3.1 The switch point: one function

Every price in the project flows through `discount_factors_at`. Putting
the bootstrap there means KRD, DV01, factor exposure and attribution —
which all bump the par curve and call `price_bond` — change behaviour
without changing code. The alternative, threading "bump, re-bootstrap"
logic into each sensitivity module, would have duplicated it five times.

### 3.2 Why an explicit `basis` argument now

Phase 4.5C chose column auto-detection (`yield` vs `zero_rate`) over a
parameter. That still works for explicit zero curves, but a par curve
now needs two readings: discount on its bootstrapped zero curve (the
default) or straight off its yields (for comparison and for the monitor,
§7). Only an argument can say which. Default `"zero"` means no existing
caller had to opt in; `"par"` keeps the original formula bit-for-bit.

### 3.3 Bootstrap frequency and caching

A par curve is bootstrapped at the frequency of the bond being priced
(`freq`), so the discounting always matches the zero curve's own
compounding (Phase 4.5A §1.4) — including for a portfolio mixing
frequencies (one cached zero curve per frequency). Bootstraps are cached
per distinct curve (`lru_cache`, 256 entries): a portfolio KRD run
reprices six bonds against the same ~31 bumped curves, so ~190
bootstraps become ~31. Cost on the dashboard's per-edit risk
calculations: **110ms → 174ms**.

### 3.4 Alternatives rejected

| Option | Why not |
| --- | --- |
| Keep the par basis | §1: understates slope risk ~45%, wrong curvature sign |
| Bump zero rates directly | Mismatched with par-based PCA factors (§2) |
| Pass zero curves in, keep column detection only | KRD would bump zero rates — same mismatch |
| Fit the curve to real JGB issue prices | Right long-term answer; needs issue-level data and a real-ISIN portfolio (§9) |

---

## 4. Validation

**Equivalence to the method it claims to be.**
`test_zero_basis_krd_matches_a_hand_built_bump_rebootstrap_reprice`
rebuilds every tenor's KRD by hand — bump the par yield, call
`bootstrap_zero_curve`, price off it — and matches to 1e-9.

**Existing checks, still passing on the new default:**
- Sum of KRDs vs. effective duration: gap ≤ 0.00003 years (40Y).
- DV01 formula vs. a direct one-sided bump: ≤ 0.15%.
- Phase 4C linear estimate vs. exact reprice: PC1 −0.3036% vs −0.2965%
  (0.007pp), PC2 −0.1099% vs −0.1058% (0.004pp). Slightly larger than on
  the par basis, still negligible at 1 std.

**New tests (12):**
- basis guards: default is `"zero"`; unknown basis rejected; `"par"` on a
  zero curve rejected; cached results identical to bootstrapping directly,
  and different curves never share a cache entry;
- flat curve: both bases price identically;
- a par bond's KRD sits entirely at its own tenor (§5);
- a zero-coupon bond has negative KRDs at earlier tenors, total still
  equal to effective duration (§5);
- Phase 4.6B's duration gap on the zero basis: bounded, peaking at 20Y;
- Phase 4C: risk beyond a shorter PCA grid is still kept (total), with the
  exact "40Y folds into 30Y" identity pinned to `"par"` (§5);
- the monitor: columns match each basis computed directly; the PC2/PC3
  finding pinned (§7).

**Tests moved to `basis="par"`** (they check the par formula itself —
flat extrapolation, the three grid-shape prices, par-vs-zero price
difference, the KRD tent's concentration and endpoint behaviour, Phase
4.6B's par-basis duration-gap finding).

---

## 5. New behaviour to expect

**Risk lands where bonds mature.** On the zero basis, a par KRD answers
"which par bond would hedge this": the 30Y bond's risk sits at 30Y
instead of being shared with 25Y through interpolation. A bond whose
coupon equals its par yield has *all* its KRD at its own tenor.

**Small negative KRDs between maturities.** Raising one quoted par yield
lifts zero rates up to it but slightly **lowers** them from the next
quoted maturity onward (checked: +1bp at 15Y moves zero rates +1.3bp at
15Y and −0.2bp from 20Y out). Bonds maturing beyond it gain, so that
tenor's portfolio KRD can go negative — e.g. 15Y: −0.109. Real, and
explained in the dashboard's KRD caption.

**Ultra-long share: 51% → 68%.** A direct consequence of the two points
above — the 20Y/30Y/40Y bonds' risk is no longer partly assigned to 15Y
and 25Y interpolation neighbours. Phase 3C's "40% of weight, 51% of
risk" becomes **40% of weight, 68% of risk**.

**The "40Y folds into 30Y" identity is par-only.** On a PCA window
without 40Y (Phase 4C §1.1), the total risk is still kept (10.666 →
10.674), but dropping the 40Y par point also reshapes the bootstrapped
curve beyond 30Y, so the risk spreads over the long end rather than
landing exactly in 30Y.

**Phase 4.6B's modified-vs-effective duration gap** no longer grows all
the way to 40Y: it peaks for the 20Y bond (5.1%), whose cash flows span
the curve's steepest stretch (10Y–20Y), where re-bootstrapping amplifies
par shifts most.

---

## 6. Before / after

Committed data (`prefer_live=False`: curve snapshot 2026-08-31, PCA
history 2024-09-02 to 2026-08-31), default portfolio, per 100 face.

### 6.1 Portfolio headline

| Figure | Par (before) | Zero (after) | Change |
| --- | --- | --- | --- |
| Price | 94.8836 | 93.0471 | −1.9% |
| Effective duration (Σ KRD) | 10.197 | 10.666 | +4.6% |
| DV01 | 0.096267 | 0.097494 | +1.3% |
| Ultra-long (≥20Y) share of KRD | 51.1% | 68.8% | +17.7pp |
| Ultra-long (≥20Y) share of DV01 | 51.2% | 67.9% | +16.7pp |
| Value-weighted YTM | 2.931% | 3.057% | +12.6bp |
| Cash flow ladder: PV share ≥20Y | 19.0% | 17.8% | −1.2pp |

### 6.2 Per bond

| Bond | Price (par → zero) | Eff. duration | DV01 | YTM |
| --- | --- | --- | --- | --- |
| JGB_2Y | 98.5485 → 98.5439 | 1.9678 → 1.9705 | 0.019393 → 0.019418 | 1.742% → 1.744% |
| JGB_5Y | 96.5986 → 96.5275 | 4.7778 → 4.8111 | 0.046153 → 0.046440 | 2.223% → 2.238% |
| JGB_10Y | 92.3023 → 91.6987 | 8.9162 → 9.1470 | 0.082298 → 0.083877 | 2.892% → 2.965% |
| JGB_20Y | 91.2314 → 88.1177 | 14.3880 → 15.3127 | 0.131264 → 0.134932 | 3.620% → 3.858% |
| JGB_30Y | 94.1634 → 89.3891 | 17.4325 → 18.5647 | 0.164150 → 0.165948 | 3.829% → 4.119% |
| JGB_40Y | 98.9681 → 94.0930 | 19.4411 → 20.3968 | 0.192405 → 0.191920 | 3.851% → 4.102% |

### 6.3 Portfolio KRD by tenor

| Tenor | Par | Zero | Tenor | Par | Zero |
| --- | --- | --- | --- | --- | --- |
| 1Y | 0.027 | −0.007 | 9Y | 0.137 | −0.032 |
| 2Y | 0.337 | 0.287 | 10Y | 2.360 | 2.337 |
| 3Y | 0.064 | −0.014 | 15Y | 0.625 | −0.109 |
| 4Y | 0.083 | −0.019 | **20Y** | 2.071 | **2.375** |
| 5Y | 1.014 | 0.959 | **25Y** | 0.421 | **−0.080** |
| 6Y | 0.102 | −0.021 | **30Y** | 1.771 | **2.933** |
| 7Y | 0.115 | −0.024 | **40Y** | 0.946 | **2.108** |
| 8Y | 0.126 | −0.028 | | | |

### 6.4 Factor exposure (Phase 4C) and attribution (Phase 4D)

| Figure | Par (before) | Zero (after) |
| --- | --- | --- |
| PC1 +1 std P&L | −0.287% / −0.2709 | −0.304% / −0.2766 |
| PC2 +1 std P&L | −0.057% / −0.0544 | −0.110% / −0.0986 |
| PC3 +1 std P&L | +0.005% / +0.0038 | −0.010% / −0.0099 |
| 4D, 2026-08-28 → 08-31: attributed PC1 / PC2 / PC3 | −0.1293 / +0.0261 / +0.0060 | −0.1320 / +0.0472 / −0.0155 |
| 4D: actual P&L (full reprice) | −0.1111 | −0.1110 |
| 4D: unexplained residual | −0.0138 | −0.0107 |

On this date the attribution's residual shrinks by about a fifth on the
zero basis, and the actual one-day P&L is almost identical on both
(−0.1111 vs −0.1110). One day isn't evidence of a general improvement;
Phase 6 should compare residuals across the full history.

---

## 7. The par-vs-zero factor monitor

`compare_factor_exposure_bases(portfolio, curve, pca_result)` prices each
factor's +1 std exposure on both bases and returns, per component:
`par_dollar`, `zero_dollar`, `dollar_gap`, `gap_vs_total` and
`relative_gap`.

| Component | Par | Zero | Gap | Gap vs. total factor risk | Relative gap |
| --- | --- | --- | --- | --- | --- |
| PC1 | −0.2709 | −0.2766 | −0.0057 | −2.0% | −2.1% |
| PC2 | −0.0544 | −0.0986 | −0.0442 | **−15.0%** | −44.9% |
| PC3 | +0.0038 | −0.0099 | −0.0137 | −4.7% | −138.6% |

**Why factor exposures, not a parallel DV01 gap:** the parallel gap reads
~1.3% while PC2 is off by ~45% (§2). A parallel monitor would stay quiet
exactly when it matters.

**Why `gap_vs_total` is the alerting measure:** it expresses each gap as
a share of the portfolio's combined 1 std factor risk, so it's comparable
across factors and doesn't explode when one factor's own exposure is
near zero (PC3's relative gap is −139% on a tiny number).

**What it monitors now that the default is zero:** how much the headline
risk depends on the choice of discounting basis — model uncertainty to
report, not an error to fix. The alert threshold is Phase 6's to set.

---

## 8. Known limitations (for the SR 11-7 validation report)

**8.1 The zero curve is coherent within this project's assumptions, not
ground truth.** It's bootstrapped by treating MOF's curve as par yields;
MOF's curve is actually a fitted yield-to-maturity curve on real issues
(Phase 4.5A §1). This phase removes the inconsistency between the risk
factors and the pricing, not that assumption.

**8.2 Interpolation shapes the zero curve.** The bootstrap interpolates
par yields in straight lines onto a semiannual grid, so the zero curve
and the size of the small negative KRDs (§5) depend on that choice. A
smoother interpolation would soften them without changing totals.

**8.3 Negative KRDs are harder to read.** Correct, but counter-intuitive
on a chart; the dashboard caption explains them.

**8.4 Earlier phase docs keep their par-basis figures.** They were true
when each phase was built. Each affected doc carries a note pointing
here; §6 is the single place with current values.

**8.5 The linear factor estimate drifts slightly further from exact
repricing** (PC1: 0.007pp vs 0.003pp) — still negligible at 1 std;
Phase 5's large stress moves should reprice fully.

---

## 9. What would change this design

**Fitting the curve to real JGB issue prices** instead of bootstrapping
MOF's curve. Researched 2026-09-27: JSDA publishes free daily reference
prices per issue (dealer mid-quotes at 3pm, CSV, archives from 2002);
MOF publishes an annual outstanding-by-issue list (maturity, amount, no
coupons). Not adopted now: JSDA rate-limits bulk downloads (so a
multi-year history for the PCA isn't practical, leaving factors and
pricing on different curves), the fit needs issue filtering and handling
of futures/BoJ distortions, and the portfolio is illustrative. Belongs
with the future real-ISIN portfolio (Phase 8). JSDA's CSV layout and
terms of use still need checking by hand — every JSDA page was
rate-limited during the research.

**A smoother par interpolation** in the bootstrap (§8.2) — local to
`bootstrap_zero_curve`, no change here.

---

## 10. Carried into Phase 6 (validation and monitoring)

- **The monitor (§7)** as a standing check, with a threshold on
  `gap_vs_total`, run on every refresh.
- **This investigation as a worked example** of model validation: a
  documented figure (4.7%) was questioned, found to answer a different
  question (zero-rate risk, not par-factor risk), and the figure, the
  conclusion ("conservative, defer") and the decision were all
  corrected — including two supporting arguments that didn't survive
  checking.
- **Curve-shape dependence:** the size and sign of basis effects depend
  on slope (§1 step 2); worth re-running §6 after any large curve move.
- **Attribution residuals across history:** §6.4's smaller residual is
  one day; compare Phase 4D residuals on both bases over the full window.

---

## 11. Relationship to earlier phases

- **Phase 4.5A–C** built the zero curve, wired it in as an option, and
  measured it (§2.1 of the 4.5C doc now records the corrected benchmark).
  Without that work the gap couldn't have been measured, and this phase
  would have been a much larger build.
- **Phase 4B** supplies the par-yield factors that make "bump par,
  re-bootstrap" the consistent definition of risk.
- **Phases 2B, 3A–3C, 4C, 4D, 4.6A–C, 4.7** keep their original par-basis
  figures with a pointer here.
- **Fallback re-anchoring policy** (`docs/phase_1_documentation.md` §5):
  no new hardcoded data.
