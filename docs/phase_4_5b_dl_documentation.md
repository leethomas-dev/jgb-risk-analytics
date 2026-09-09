# Phase 4.5B (Diebold-Li) Documentation — `models/diebold_li.py`

This is a Phase 4.5B addition (it extends `models/curve_fitting.py`'s
Nelson-Siegel/Svensson fitting — see §1), not a Phase 4B one, even though
its Part 3 comparison reads Phase 4B's existing PCA output. The model
being added here is Diebold-Li's *dynamic* Nelson-Siegel, squarely a
continuation of the cross-sectional Nelson-Siegel/Svensson fitting Phase
4.5B already built — Phase 4B itself (PCA) is a comparison target, not
the phase this work belongs to.

## Why a new doc, not an extension of `phase_4_5b_documentation.md`

This is substantial new content — a new model, a new historical fitting
exercise, and a genuine comparison against Phase 4B's PCA — not a small
addendum. Following this project's own pattern of one doc per deliverable
(Phase 4.5A and 4.5B each got their own doc despite both being sub-parts
of one phase), it gets its own file here, cross-referenced from
`phase_4_5b_documentation.md` rather than appended into it. That keeps
the existing NS/Svensson doc focused, and keeps this addition easy to
find on its own.

## In plain English

Phase 4.5B fit a smooth curve shape to a *single day's* interest rates,
carefully searching for the one "decay" number (tau) that shape needed.
That search is unstable enough that it has to be done carefully every
time. Diebold and Li's insight (2006) was: what if you just *fix* that
number once, using a value that works reasonably well across many days,
instead of re-searching for it every day? Once tau is fixed, fitting the
other three numbers becomes a plain, ordinary regression — fast, stable,
guaranteed to produce an answer. That unlocks something Phase 4.5B's
own daily fit couldn't practically do: running the same fit across
*years* of daily curves and watching how its three parameters (read as
level, slope, and curvature) move over time. Phase 4B's PCA already
produces its own level/slope/curvature story from the same historical
data, by a completely different statistical method. This part builds the
second story and compares the two — honestly reporting where they agree
closely, and where they don't.

---

## Technical details

Fits Diebold & Li's (2006) dynamic Nelson-Siegel — Nelson-Siegel with tau
held FIXED at one constant — across an entire historical curve series
from Phase 4A, producing date-indexed time series of `beta0`, `beta1`,
`beta2`. Every date is fit on its own **bootstrapped zero curve**
(`models.bootstrap.bootstrap_zero_curve_history`, added alongside this
module), never the raw historical par-treated series directly — the same
principle Phase 4.5B established for the single-date case.

**File:** `models/diebold_li.py` — `fit_diebold_li_cross_section()` (one
date, OLS), `fit_diebold_li_history()` (the full time series),
`DieboldLiHistoryResult` (frozen result, with `.rmse_bp` and
`.poor_fit_dates()`), `tau_sensitivity()` (the sweep behind the tau
choice), `compare_to_pca()` / `DieboldLiPCAComparison` (the Part 3
comparison), `plot_beta_time_series()` / `plot_pca_comparison()` (charts).
Also added: `models.bootstrap.bootstrap_zero_curve_history()` (Part A's
own batch extension) and `models.curve_fitting.hump_factors()` (promoted
from private to public so this module can reuse the exact same
Nelson-Siegel basis functions rather than re-deriving them).

**Output contract:** `fit_diebold_li_history(history, tau=DEFAULT_TAU) ->
DieboldLiHistoryResult`, where `history` is a
`load_jgb_curve_history()`-shaped DataFrame. The result carries the fixed
`tau` used, the tenor grid actually fit (read from `history.columns`),
the bootstrapped zero-curve targets, the fitted rates, the beta time
series, and a per-date `rmse_bp` property.

---

## 1. Why a separate module, not an extension of `curve_fitting.py`

`curve_fitting.py`'s entire design center is the *opposite* problem: tau
is genuinely unknown and has to be found carefully, because it's unstable
(a bounded, two-stage grid search — `docs/phase_4_5b_documentation.md`
§4). Here, tau is a fixed, externally-chosen constant, and the whole
fitting problem collapses to one linear regression. Bolting that onto
`curve_fitting.py` would mean one module doing two conceptually different
jobs — searching for an unknown, unstable non-linear parameter vs.
batch-regressing with a known one — behind one set of names. This
project already made the equivalent call once before, for a similarly-
shaped reason: Phase 4A's history loader is a sibling module to Phase 1's
current-curve loader, not an extension of it, specifically because their
contracts are different shapes (`docs/phase_4a_documentation.md` §1.1).
The same reasoning applies here.

**What genuinely is shared:** the Nelson-Siegel basis functions
themselves. `models.curve_fitting.hump_factors()` was promoted from a
private (`_hump_factors`) to a public function specifically so this
module imports it directly rather than re-deriving the same math a second
time — one implementation of the model, two different fitting strategies
built on top of it. Nothing about `curve_fitting.py`'s own cross-sectional
fitting logic, bounds, or grid search was touched.

---

## 2. The tau choice — derived for JGBs, not copied from the original paper

Diebold & Li (2006) fixed tau at the value maximizing the curvature
loading at a 30-month maturity, calibrated for **US Treasuries** — a
market whose quoted curve mostly runs under 10 years. JGB curves run to
**40 years** with genuine long-end curvature (this project's own
`docs/phase_3c_documentation.md` and `docs/phase_4b_documentation.md`
findings), so copying that number, or even its "target a specific short/
medium maturity" framing, uncritically would center the model's hump at a
point with no principled connection to Japan's own tenor range.

**What was done instead:** `tau_sensitivity()` sweeps a bounded grid of
candidate tau values and fits (via the same closed-form OLS) every date
in the project's default historical window (the same 2-year, post-YCC
window Phase 4B's PCA already uses by default —
`models.pca.DEFAULT_PCA_LOOKBACK_YEARS` — reused directly, not
re-derived, so the two models' outputs are comparable over the same
dates in §3). `DEFAULT_TAU` is set to the value minimizing the **median**
per-date RMSE across that whole sample — median rather than mean, so the
persistent cluster of harder-to-fit dates found in §4 doesn't pull the
choice away from what fits a typical day well.

**The sweep, against the committed snapshot history**
(`prefer_live=False`, window 2024-09-02 to 2026-08-31, 486 dates, 15
tenors):

| tau (years) | Median RMSE | Mean RMSE |
| --- | --- | --- |
| 0.5 | 47.9bp | 49.5bp |
| 1.0 | 37.9bp | 38.2bp |
| 2.0 | 22.2bp | 22.1bp |
| 3.0 | 13.8bp | 14.0bp |
| 5.0 | 7.4bp | 8.2bp |
| **6.78 (empirical optimum)** | **5.45bp** | 7.4bp |
| **7.0 (DEFAULT_TAU)** | **5.47bp** | 7.4bp |
| 10.0 | 6.9bp | 8.1bp |
| 15.0 | 8.2bp | 8.5bp |
| 20.0 | 8.5bp | 8.3bp |
| 30.0 | 8.0bp | 7.8bp |
| 40.0 | 7.5bp | 7.5bp |
| 50.0 | 7.2bp | 7.2bp |

`DEFAULT_TAU = 7.0` — a clean, easily-communicated number rounded from
the sweep's exact minimum (6.78 years); the median RMSE difference
between the two (5.45bp vs. 5.47bp) is negligible, since the region
around the minimum is fairly flat (§2's "sensitivity check," directly
below).

**The sensitivity check, reported as asked:** sensitivity is real, and
**asymmetric**. Moving tau far below the optimum is severe — at
`tau=0.5`, median RMSE is nearly **9x** worse than at the optimum.
Moving tau above the optimum is much gentler — even at `tau=50` (the
upper edge of Phase 4.5B's own search bounds), median RMSE is only
about 1.3x worse, not catastrophically so. The practical takeaway: this
choice matters a great deal if tau is too small, and only moderately if
it's too large — worth knowing before treating `DEFAULT_TAU` as a
precisely-tuned constant rather than a reasonable point in a fairly wide,
flat-bottomed valley.

**A striking contrast worth naming explicitly:** the historically-optimal
FIXED tau (~6.8-7.0 years) is nowhere near the ~34-year tau Phase 4.5B's
own cross-sectional fit finds for the single most recent date
(`docs/phase_4_5b_documentation.md` §6). This isn't a contradiction —
it's the exact motivation for fixing tau in the first place: a freely
re-optimized per-date tau can land in very different places depending on
that day's specific curve shape (and, per Phase 4.5B §5, can even fail to
converge at all on some real curve shapes). A single, historically-
representative tau trades away that day-to-day flexibility deliberately,
in exchange for the stability that makes a time series of betas
meaningful to compare across dates at all.

---

## 3. Running it across history — the beta time series and per-date fit quality

Across the same 486-date window (`prefer_live=False`, `DEFAULT_TAU=7.0`):

```
           beta0      beta1      beta2
mean     +4.97%     -4.29%     -2.86%
std      +0.62%     +0.51%     +1.56%
min      +3.65%     -5.22%     -5.03%
max      +5.97%     -3.04%     +0.82%
```

`beta0` (long-run level) sits consistently around 4-6%, well above where
current short rates trade — a known Nelson-Siegel artifact for an
upward-sloping curve extending to 40 years, not a claim about the
"true" long-run rate. `beta1` (short-rate component) stays reliably
negative (short rates below the long-run level, i.e. an upward-sloping
curve throughout the window), and `beta2` (curvature) swings from
notably negative to slightly positive over the period — the most
volatile of the three, consistent with curvature being the hardest
feature of a curve's shape to pin down from noisy day-to-day moves.

**Per-date fit quality:** median RMSE 5.47bp, mean 7.45bp, ranging from
3.55bp (best) to 18.19bp (worst).

**Poor-fit dates, flagged and investigated, not just counted.** Using
the rule "RMSE exceeds 2x the sample's own median" (`poor_fit_dates()`),
**94 of 486 dates (19%)** are flagged — not a handful of anomalous days,
but a **persistent regime**: every flagged date falls between
**2026-04-10 and 2026-08-31**, the last ~5 months of the window (14 in
April, 16 in May, 22 in June, 22 in July, 20 in August). Inspecting the
worst dates directly explains why: by mid-2026, the curve's ultra-long
segment develops a real hump-then-decline shape — yields rise to a peak
around 25Y (e.g. 2026-07-08: 25Y = 4.06%) and then **fall** through 30Y
(4.00%) and 40Y (3.94%). A single-hump Nelson-Siegel curve structurally
cannot represent that: fitting one hump well (to match the rise into
25Y) leaves no remaining flexibility to also turn the curve back down
before 40Y. This is a genuine curve regime the model handles badly, per
the phase brief's own framing — not a data problem, and not fixable by
re-tuning tau (a different single-hump tau doesn't add a second hump). A
second hump term (Svensson) is the direct answer to exactly this shape,
already built in Phase 4.5B.

---

## 4. Comparison to Phase 4B's PCA factors — an uneven, honestly-reported result

`compare_to_pca()` correlates the CHANGE in each beta (`beta0.diff()`,
etc.) against the day-to-day score of the corresponding PCA component
(`beta0` vs. PC1, `beta1` vs. PC2, `beta2` vs. PC3) — changes, not
levels, for the same reason `models.pca` itself is fit on yield changes
rather than levels (its own module docstring): a beta LEVEL series
drifts with the general level of rates for reasons unrelated to which
curve-movement pattern occurred on a given day, while a PCA component
score is, by construction, already a day-to-day change. Comparing a
level series to a change series would not be a fair comparison of the
same thing.

**Sign alignment, handled explicitly.** A PCA loading's sign is
mathematically arbitrary (`docs/phase_4b_documentation.md` §1.3) — a
negative raw correlation could just as easily mean "these track each
other closely, with the PCA convention's sign happening to point the
other way" as "these move oppositely." Both are reported:
`sign_aligned_correlation` is `abs(raw_correlation)` — mathematically
identical to flipping the arbitrarily-signed series before correlating —
so a large negative raw number is never mistaken for a real inverse
relationship.

**The measured result** (same 486-date window, 485 daily changes after
differencing):

| Beta | PCA component | Raw correlation | Sign-aligned correlation |
| --- | --- | --- | --- |
| `beta0` (level) | PC1 (level) | +0.269 | **0.269** |
| `beta1` (slope) | PC2 (slope) | -0.955 | **0.955** |
| `beta2` (curvature) | PC3 (curvature) | -0.504 | **0.504** |

**Reported honestly, not smoothed into one story:** the correspondence is
**genuinely uneven**, not uniformly strong or uniformly weak.

- **Slope: very strong (0.955).** Diebold-Li's `beta1` and PCA's PC2
  track each other closely — the clearest case where "these are two
  different routes to the same underlying factor" is a well-supported
  empirical claim.
- **Curvature: moderate (0.504).** A real, non-trivial relationship, but
  far from a clean identity — consistent with curvature being the
  noisiest, least stable feature of a curve's shape (§3's finding that
  `beta2` is the most volatile parameter).
- **Level: weak (0.269).** The most surprising result, and worth taking
  seriously rather than explaining away. A plausible reason: Diebold-Li's
  `beta0` is a strict mathematical asymptote (the curve's value as
  maturity goes to infinity), while PC1's actual tenor loadings are NOT
  flat across the curve — Phase 4C's own finding
  (`docs/phase_4c_documentation.md` §2) is that PC1's loadings are
  visibly elevated from the belly through 40Y (0.28-0.32) versus the
  front end (0.08-0.16). PC1 behaves more like a "level with a long-end
  tilt" than a textbook parallel shift, which `beta0` alone doesn't
  capture — some of what PC1 measures likely leaks into `beta1`/`beta2`
  instead. This is offered as a plausible explanation, not a proven one.

**What this comparison does and does not show.** A strong correlation
(slope) is genuine empirical support that two independently-derived
statistical objects are picking up the same real signal — that is a
finding, not an assumption. A weak correlation (level) is an equally
real finding, not a failure of either method: Nelson-Siegel imposes a
fixed functional form on what "level" means; PCA extracts whatever
direction the data actually moves in the most, with no functional-form
assumption at all. They are related constructions, answering related but
not identical questions, and this comparison is evidence about exactly
how related — not proof that either one is "the truth" the other should
be validated against.

**An added reason not to over-read this, disclosed explicitly:** the two
sides of this comparison sit on slightly different bases. Diebold-Li here
fits the **bootstrapped zero curve** (Part 4.5's own principle); Phase
4B's PCA — built before Part 4.5 existed, and deliberately not rebuilt
here to avoid restructuring already-shipped work — operates on the raw
historical **par-treated** yield changes directly. This is a real,
disclosed asymmetry, on top of the functional-form and sign-convention
differences already discussed above.

---

## 5. Charts

`plot_beta_time_series()` saves the three beta series over the fitted
window to `outputs/diebold_li_betas.png`. `plot_pca_comparison()` saves
the three paired (Δbeta, sign-aligned PCA score) series, each
standardized for visual comparability, to `outputs/diebold_li_vs_pca.png`
— both headless-safe (matplotlib's `Agg` backend), matching this
project's existing chart modules.

---

## 6. Planned SR 11-7 benchmark model — recorded, not built

For the eventual Phase 6 validation write-up: a **smoothing spline**
(specifically **Waggoner (1997)**, whose roughness penalty varies by
maturity segment — heavy smoothing at the short end, more flexibility at
the volatile ultra-long end) is the intended **benchmark model** this
project's Nelson-Siegel, Svensson, and Diebold-Li fits will eventually be
compared against for SR 11-7 purposes. It is recorded here as a planned
future benchmark only — not built, and nothing in this module or
`curve_fitting.py` depends on it existing yet.

---

## 7. Known limitations (for the SR 11-7 validation report)

**7.1 Inherits Part A's par-curve simplification, once per historical
date.** Every date's fit is against a zero curve built by treating that
date's MOF-published series as a par curve — the same measured
simplification as Phase 4.5A (`docs/phase_4_5a_documentation.md` §1/§5),
now applied across the whole history, not just the current day.

**7.2 A fixed tau trades per-date accuracy for stability — by design, but
still a real tradeoff.** §2's sensitivity table shows fit quality does
vary with tau; `DEFAULT_TAU` sits in a fairly flat region but is not
free of the tradeoff.

**7.3 A genuine curve regime this model handles badly, identified and
explained, not hidden.** §3's poor-fit cluster (April-August 2026) is a
real structural limitation of a single-hump functional form when the
curve itself develops a real double-inflection (hump-then-decline) shape
at the ultra-long end — precisely the segment this project's portfolio
is most exposed to (`docs/phase_3c_documentation.md`).

**7.4 The PCA comparison sits across two different bases (§4's "added
reason").** Diebold-Li here uses the bootstrapped zero curve; the
existing PCA module uses raw historical yield changes. A future,
fully-matched comparison would need PCA rebuilt on the same zero-curve
basis — not done here, to avoid restructuring already-shipped Phase 4B
work.

**7.5 The level (`beta0` vs. PC1) correspondence is weak, and only
partially explained.** §4's proposed explanation (PC1's non-flat tenor
loadings) is plausible, not confirmed by a further, dedicated
investigation. Flagged as an open question, not resolved.

**7.6 Correlation, not causation or equivalence.** Even the strong slope
correspondence (0.955) shows two methods tracking a similar signal
closely — it does not establish that either method's factor is "the
correct" slope factor, or that the two are interchangeable in every use.

---

## 8. What would change this design

**Rebuilding PCA on the bootstrapped zero-curve history** (§7.4) would
make §4's comparison fully apples-to-apples — additive work (a new
`compute_curve_pca`-style call against
`models.bootstrap.bootstrap_zero_curve_history`'s output), not a change
to the existing, shipped PCA module.

**A Waggoner (1997) smoothing-spline benchmark** (§6) is the planned next
step for the eventual Phase 6 SR 11-7 write-up — not built now.

**Investigating the weak level correspondence further** (§7.5) — e.g. by
checking whether a rolling or windowed decomposition of PC1 shows a
long-end tilt component separable from a pure level component — would be
new analysis, not a change to this module's existing fitting logic.

**A different fixed tau**, if a future dataset or window shifted the
sweep's optimum, is a one-constant change (`DEFAULT_TAU`) — `tau_sensitivity()`
is already built to re-derive it.

---

## 9. Relationship to the fallback re-anchoring policy and earlier phases

This module adds no new hardcoded market data — it computes directly from
whatever `load_jgb_curve_history()` returns, inheriting that loader's
re-anchoring policy (`docs/phase_4a_documentation.md` §2) rather than
adding a new one. `DEFAULT_TAU`, the tau-sweep bounds, and
`POOR_FIT_RMSE_MULTIPLE` are methodology choices (§2, §3), not
observations of anything that moves, so there is nothing new here for
that policy to separately govern.
