# Phase 4D Documentation — `models/pc_scores.py` + `models/factor_pnl_attribution.py`

> **Special Phase A (2026-09-27):** figures in this doc were computed on
> the par discounting basis in force when this phase was built. The
> project now discounts on the bootstrapped zero curve by default; current
> values are in [`special_phase_a_documentation.md`](special_phase_a_documentation.md) §6.

## In plain English

Phase 4B found the handful of patterns the JGB curve tends to move in.
Phase 4C answered "if a *typical* bad day happened on one pattern, what
would it do to this portfolio?" Neither of those tells you what happened
on any *particular* day. This part closes that gap. First, it scores
every real day in the historical data on each of those patterns — not
"the curve moved," but "today looked 80% like a level shift and barely
like anything else." Second, it takes one specific day's move and splits
the portfolio's actual profit or loss into "the part explained by the
level pattern," "the part explained by the slope pattern," "the part
explained by the curvature pattern," and — reported just as visibly as
the other three — "the part none of those patterns explain." A risk desk
doesn't want to hear "the market moved"; it wants to hear "here is what
the market's move did to me, and which pattern drove it," and this is
the part of the project that answers that question.

---

## Technical details

Two deliverables, one doc, matching Phase 4.5C's own convention for a
phase with more than one module.

**Section 1 — daily PC scores.** `models/pc_scores.py` —
`PCScoreResult` (frozen), `compute_pc_scores()`. Projects every daily
yield-change row in a Phase 4A history onto Phase 4B's PCA eigenbasis,
producing a date-indexed score series.

**Section 2 — factor P&L attribution.** `models/factor_pnl_attribution.py`
— `FactorPnLAttribution` (frozen), `compute_factor_pnl_attribution()`.
Combines Section 1's scores with Phase 4C's portfolio factor exposures to
split one day's realized portfolio P&L into level/slope/curvature plus a
residual.

**Also touched:** `models/pca.py` — `CurvePCAResult` gained one new field,
`full_loadings` (the complete, sign-fixed eigenvector basis, all
`n_tenors` components, not just the top few `compute_curve_pca` keeps).
Needed for Section 1's completeness check (below); defaults to `None` so
the one existing test that constructs a `CurvePCAResult` directly
(`tests/test_zero_curve_impact.py`'s `_synthetic_pca_result`) is
unaffected.

**Output contracts:**

- `compute_pc_scores(history, pca_result) -> PCScoreResult` — `scores`
  (date-indexed, columns `"PC1".."PCn"`, **decimal** yield-change units),
  `standardized_scores` (same shape, columns `"PC1_std".."PCn_std"`,
  dimensionless), `full_scores` (all `n_tenors` components, for the
  completeness check), `component_std`, plus a `.percentile(date,
  component)` method.
- `compute_factor_pnl_attribution(portfolio, history, pca_result,
  score_result, as_of_date, freq=None, bump_size=DEFAULT_BUMP_SIZE) ->
  FactorPnLAttribution` — `attributed_pct` / `attributed_dollar` (one
  entry per component), `residual_pct` / `residual_dollar`,
  `actual_pct_pnl` / `actual_dollar_pnl` (from an exact reprice, not the
  linear estimate), `as_of_date` / `previous_date`.

---

## 1. Section 1: the units decision, and why it had to be explicit

The phase brief was blunt about this, for good reason: a PCA score is
meaningless without a stated unit, and it is *very* easy to silently
present one thing as another.

`score_i(t) = eigenvector_i . daily_change_vector(t)`. `history` (Phase
4A's loader) is **decimal** yields (`0.015 == 1.5%`), and Phase 4B's
eigenvectors are **unit-norm**. A dot product of a unit vector against a
decimal-yield-change vector is itself in **decimal yield-change units** —
not basis points, and emphatically **not** a standard-deviation count.
`compute_pc_scores` returns exactly that in `scores`, labeled as such
everywhere (module docstring, field docstring, `__main__`'s own printed
output, and the dashboard's chart axis, which converts to basis points
only for *display*, `* 10000`, never in the underlying data).

A **standardized** version — `score / sqrt(eigenvalue)`, dimensionless,
"how many standard deviations from typical" — is also exposed, as a
**separate** field (`standardized_scores`) with **distinctly-named**
columns (`"PC1_std"`, not `"PC1"`). `scores` and `standardized_scores`
can never collide in a merge, a print statement, or a chart legend,
because their column names never overlap.

**Raw, not mean-centered — and why that's safe.** `models.pca.compute_curve_pca`
mean-centers the change matrix before its SVD (its own docstring: level/
slope/curvature are patterns in *deviations* from the average daily
change, not the average move itself). `compute_pc_scores` deliberately
does **not** re-center: it projects the **raw** daily change, exactly as
the phase brief's formula states. This does not corrupt the verification
in §2 below — adding back the (constant, day-independent) mean shifts
every score in a component's series by the *same* constant, which moves
a distribution's center but never its standard deviation. Using the raw
projection is also what makes §3's completeness check possible at all: it
lets scores sum back to the exact *original* (uncentered) change vector,
not a centered approximation of it.

---

## 2. The sqrt(eigenvalue) verification — measured, not assumed

The phase brief asked for this explicitly, as a correctness check. Run
directly against the committed snapshot, the project's default 2-year
window (`prefer_live=False`):

```
PC1: observed std =   9.8846bp   sqrt(eigenvalue) =   9.8846bp   gap = +0.000000bp
PC2: observed std =   5.1602bp   sqrt(eigenvalue) =   5.1602bp   gap = +0.000000bp
PC3: observed std =   1.4718bp   sqrt(eigenvalue) =   1.4718bp   gap = +0.000000bp
```

**Exact, to the precision printed** — not a coincidence, and not merely
"close." The reason it must hold exactly, worked through: `score_i(t) =
loading_i . X(t) = loading_i . Xc(t) + loading_i . mean(X)`, where `Xc`
is the mean-centered matrix `compute_curve_pca` actually decomposes. The
second term is a single scalar, constant across every day `t` — it shifts
where the score series is *centered*, but adding a constant to every
observation never changes a series' variance. So `Var(score_i) =
Var(loading_i . Xc(t))`, which is *exactly* `eigenvalue_i` by
construction of the SVD (`S_i^2 / (n_obs - 1)`, `models/pca.py`'s own
`eigenvalues_all`). `tests/test_pc_scores.py::test_score_std_equals_sqrt_eigenvalue`
checks this to `rtol=1e-9` against real data, not just this one snapshot
run.

---

## 3. The completeness check, and why it needed a new field on `CurvePCAResult`

"Scores reconstruct the original change vector when summed across all
components" is only true if *all components* means literally every one —
`n_tenors` of them, not just the top 3 Phase 4B keeps by default. Phase
4B's `CurvePCAResult` only ever stored the kept slice (`loadings`); the
full eigenbasis was computed inside `compute_curve_pca`'s SVD but
discarded before it left the function.

**The fix: `full_loadings`**, a new field on `CurvePCAResult` — the same
sign-fixed basis as `loadings`, but every component. `loadings` is now
exactly `full_loadings.iloc[:n_components]`. Because `n_obs > n_tenors`
is already a hard requirement of `compute_curve_pca` (needed for a stable
covariance estimate in the first place), the SVD's `Vt` is always the
**full**, square, orthogonal `n_tenors x n_tenors` matrix — nothing new
had to be computed, only kept instead of discarded.

**Result, real data, default window:**

```
max |reconstructed - original| across the full 485-day sample: 3.25e-18
```

Floating-point noise. `full_loadings @ full_loadings.T == I` to
`atol=1e-10` (`test_full_loadings_is_a_complete_orthonormal_basis`) —
confirming this is a property of the orthonormal basis itself, not an
artifact of one lucky reconstruction.

**Why a default of `None`, not a required field.** One existing test
(`tests/test_zero_curve_impact.py::_synthetic_pca_result`) builds a
`CurvePCAResult` directly, by hand, with only the pre-Phase-4D fields —
a legitimate, already-passing test that has nothing to do with this
phase and was never going to be edited to accommodate it (per this
phase's own standing instruction). Giving `full_loadings` a default of
`None` means that test keeps working unchanged; `compute_pc_scores`
raises a clear, actionable `ValueError` if it's ever handed a result
where `full_loadings` wasn't populated, rather than an opaque
`AttributeError` reaching a caller two frames deeper.

---

## 4. The sign convention — reused, not reinvented

The phase brief asks for *a* sign convention to be fixed and documented,
so "a positive PC1 day" means the same real curve movement every time.
Phase 4B (`models/pca.py`) already fixes one, deterministically: each
component's **largest-magnitude tenor loading is positive** (the same
rule scikit-learn's `svd_flip` uses). Because `compute_pc_scores` projects
against that *same*, already-signed eigenbasis (`full_loadings`, itself
sign-fixed before `loadings` is even sliced from it), every score
inherits that fixed convention automatically — there is nothing left for
this phase to fix a second time, and no risk of two competing rules
disagreeing with each other.

`test_a_positive_pc1_day_means_the_same_direction_every_time` checks this
holds up as an actual empirical fact, not just a solver artifact: on the
top-decile PC1-score days, the curve's *average* tenor-by-tenor move is
reliably more positive than on the bottom-decile days — a genuinely
"level up" reading, checked, not asserted.

---

## 5. Tenor alignment — a different problem from Phase 4C's, with a simpler answer

Phase 4C had to reconcile two *different* tenor grids (Phase 1's live
curve vs. Phase 4A's history), by interpolating yield **levels** onto the
PCA grid (`curve_yield_at`) — valid because a yield level is a smooth
function of maturity.

A PCA loading is not that kind of object. It's a fixed set of per-tenor
*coefficients*, each tied to a specific tenor's **identity**, not a
smooth curve you can interpolate between. There is no valid way to ask
"what would this eigenvector's 12Y coefficient have been" on a grid that
never had a 12Y point. So `compute_pc_scores` doesn't attempt
reconciliation at all: it **requires** `history`'s tenor grid to exactly
match `pca_result.tenors`, and raises a clear `ValueError` naming both
grids if they don't (`test_rejects_a_history_whose_tenor_grid_does_not_
match_the_pca_fit`). In practice this means: call it with the *same*
history object (or a different window with an identical retained tenor
set) the PCA fit was actually run on — never a different window whose
own ragged-tenor policy (`docs/phase_4a_documentation.md` §1.3) happened
to keep a different set.

This also resolves Section 2's own version of the same question.
Attribution needs two real curves at two specific dates; because
`history`'s rows already live on the exact grid the PCA loadings do,
`_curve_from_history_row` builds each day's curve directly from a
`history` row — **no interpolation step at all**, unlike Phase 4C's
`_curve_aligned_to_pca_grid`. Checked directly, not just designed around:
`test_works_on_a_window_with_a_different_retained_tenor_count` runs the
whole pipeline on a 21-year window (14 tenors — 40Y hasn't been retained,
since the window crosses its 2007-11-06 introduction) to confirm nothing
here hardcodes 15 tenors specifically.

---

## 6. Section 2: what "portfolio exposure to factor i" means here

The phase brief's formula — `attributed P&L = exposure to factor i x
score_i for that day` — needs "exposure" defined in units that multiply
sensibly against a *raw* score, not a +1-standard-deviation one.

Phase 4C's `FactorExposure.pct_pnl` / `dollar_pnl` are P&L figures for a
shock of size `component_std` (one full standard deviation, in decimal
yield units — `implied_yield_shock`'s own definition). Dividing either by
that same `component_std` converts it into "P&L per unit of raw decimal
score" — a genuine sensitivity, not an approximation of one — and
multiplying that by the day's *actual* score (`compute_pc_scores`'s
output) gives that day's attributed P&L. Nothing here recomputes a KRD or
DV01 value; `compute_factor_pnl_attribution` calls
`compute_portfolio_factor_exposure` once (against the **previous** day's
curve — start-of-period sensitivities applied to the realized move, the
standard convention for duration-based daily P&L attribution) and rescales
its two output numbers.

**Two curves, two specific dates — not "the curve" and "today".** Every
`FactorPnLAttribution` carries `as_of_date` and `previous_date` (ISO
strings) — the exact pair of days the change is between. There is no
generic "today" when a caller might be running off a cached snapshot;
an attribution without both dates stated would be silently ambiguous
about which move it even describes.

---

## 7. The residual — measured across the full sample, not just one day

**Actual P&L is an exact reprice, not the linear estimate.**
`compute_factor_pnl_attribution` reprices the whole portfolio off both
days' *real* historical curves (`price_portfolio`), the same
exact-reprice technique Phase 4C's own `__main__` sanity check uses
(`docs/phase_4c_documentation.md` §1.2) — not the KRD-based linear
approximation the three attributed components themselves are built from.
`residual_pct` / `residual_dollar` is `actual - attributed.sum()`, by
construction — never netted away, never presented as anything other than
"what the three-factor model didn't explain."

**Measured across all 485 days in the project's default window**
(`prefer_live=False`):

| | bp of price |
| --- | --- |
| Mean \|residual\| | 1.24bp |
| Median \|residual\| | 0.83bp |
| 95th percentile \|residual\| | 3.92bp |
| Max \|residual\| | 12.32bp |
| Mean \|actual daily P&L\| | 22.81bp |
| Median \|actual daily P&L\| | 16.75bp |

On days with a move bigger than 1bp, the residual averages **~11%** of
the actual move (median **~5%**) — small most of the time, but not
negligible, and occasionally over a tenth of a percent of price on its
own (the max, 12.3bp). This is consistent with, and not contradicted by,
Phase 4B's own finding that the top 3 components explain **97.5%** of
curve-change *variance*: a small share of unexplained variance still
implies a real, visible day-to-day P&L residual, because (a) variance
explained and P&L explained aren't the same statistic — the residual's
size on a given day depends on how that day's specific *shape* of curve
move (not just its size) happened to fall outside the three kept
patterns, and (b) the linear (KRD-based) approximation underlying the
three attributed components carries its own small gap from an exact
reprice, which Phase 4C's §1.2 already quantified separately as a
fraction of a basis point at typical shock sizes and which is folded
into this residual too, not reported as a fourth, separate number.

**What this implies about the three-factor model's adequacy.** For the
large majority of days, level + slope + curvature is a good — not
perfect — description of what happened to this portfolio. A risk desk
reading this residual as "small and usually stable" would be reading it
correctly; a validator reading it as "occasionally material, and growing
on the specific days the market moves in an unusual shape" would also be
reading it correctly. Neither reading is hidden by this design — that is
the entire point of reporting the residual as a first-class number rather
than only reporting the three components that sum to less than the whole
truth.

---

## 8. Known limitations (for the SR 11-7 validation report)

**8.1 Inherits every limitation already named for Phase 4B/4C.** The
2-year, post-YCC lookback window (`docs/phase_4b_documentation.md` §1.5),
the linear/first-order nature of the underlying KRD-based sensitivity
(`docs/phase_4c_documentation.md` §3.1), and the historical-covariance
assumption (that recent history is a reasonable guide to near-future
behavior) all apply identically here — this phase adds a residual measure
on top of them, it doesn't remove any of them.

**8.2 The sensitivity used for attribution is fixed at the previous day's
curve, not re-estimated intraday.** A large single-day move changes the
portfolio's own KRD/DV01 profile somewhat by the end of that day (bond
convexity); this attribution uses the *start-of-day* sensitivity applied
to the whole day's move, the standard convention, but not a claim that
the sensitivity was constant throughout the day.

**8.3 The tenor-grid-match requirement (§5) is strict by design, not a
temporary limitation.** A caller who wants scores/attribution against a
history window with a *different* retained tenor set than the PCA fit
must refit PCA on that window first — there is no reconciliation path,
because (per §5) none would be mathematically valid for a loading vector.

**8.4 The residual figures in §7 are pinned to the committed snapshot's
default 2-year window.** A different lookback window, or a different
market regime, would show a different residual distribution — this is
the same caveat Phase 4B/4C already carry for every other number derived
from that window, restated here since a residual is easy to over-read as
a fixed property of "how good PCA is" rather than of this specific
fitted window.

---

## 9. What would change this design

**A rolling or exponentially-weighted attribution across many days at
once**, rather than one `as_of_date` per call, would be a straightforward
loop over `compute_factor_pnl_attribution` at the caller level — nothing
about the function's own contract would need to change (the dashboard's
own residual-distribution table, if ever added, would be built exactly
this way).

**Re-estimating sensitivity mid-day, or using an average of the two
days' KRD/DV01 profiles instead of only the previous day's**, would
change §6's one `compute_portfolio_factor_exposure` call to two, averaged
— a real alternative attribution convention, not implemented here because
start-of-period sensitivity is the standard, simplest convention and
because Phase 4C's own gap between the linear estimate and an exact
reprice (a fraction of a basis point at typical shock sizes) suggests the
convexity this would correct for is not the dominant source of the
residual measured in §7.

**A fourth+ explicit factor**, if Phase 4B's `n_components` were ever
raised above 3, needs no code change here — `compute_pc_scores` and
`compute_factor_pnl_attribution` both already iterate `1..n_components`
generically, reading it off `pca_result` rather than assuming 3.

---

## 10. Relationship to the fallback re-anchoring policy

This phase adds no new hardcoded market or portfolio data of its own — it
computes directly from whatever `load_jgb_curve_history` /
`load_portfolio` / `compute_curve_pca` return, inheriting those modules'
re-anchoring policies rather than adding a new one
(`docs/phase_1_documentation.md` §5, `docs/phase_4a_documentation.md`
§2). The dashboard's PC-score chart and attribution card (§11 of
`docs/phase_4_7_documentation.md`'s own successor — see `app.py`'s
section 7) disclose the change's own `as_of_date` / `previous_date`
directly, the same "make the policy's effect visible, don't change the
policy" approach Phase 4.7 already established for the yield curve
section.
