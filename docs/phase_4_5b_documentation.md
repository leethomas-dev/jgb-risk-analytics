# Phase 4.5B Documentation — `models/curve_fitting.py`

> **Special Phase B (2026-10-02) — read before §6–8.** The 25Y miss in
> §7 (+14 to +19bp) is mostly a **data artifact in MOF's curve**, not a
> model failure: MOF builds its 25Y point from 30Y-class bonds issued
> years ago with 0.7% coupons, trading near 47, which yield ~32bp more
> than par bonds would; 20Y and 30Y come from near-par issues. 15Y has
> the same issue (~16bp). Don't read 15Y or 25Y residuals as rich/cheap
> signals. Also: Svensson almost nests Nelson-Siegel, so its lower
> in-sample RMSE (§6) proves little; out of sample it wins modestly in
> the belly and misses 40Y by ~99bp when extrapolating. See
> [`future_curve_construction.md`](future_curve_construction.md) §2 and
> [`special_phase_b_documentation.md`](special_phase_b_documentation.md) §2–3.

**Related, added later:** `models/diebold_li.py` extends this phase's
cross-sectional Nelson-Siegel/Svensson fitting with Diebold & Li's (2006)
DYNAMIC (fixed-tau) Nelson-Siegel, run across an entire historical curve
series and compared against Phase 4B's PCA factors — see
[`phase_4_5b_dl_documentation.md`](phase_4_5b_dl_documentation.md), kept
as its own doc rather than folded into this one (substantial new
content, same one-doc-per-deliverable convention used throughout this
phase).

## In plain English

Part A turned the government's published rate table into a curve of ~80
discrete points (a rate for every six months out to 40 years). That's
precise, but it's not a *description* — there's no simple way to say "the
curve looks like X." This part fits two textbook mathematical shapes to
that curve: Nelson-Siegel (a smooth curve controlled by 4 numbers) and
Svensson (the same idea with 6 numbers, allowing one extra bend). Both
are fit to the *same* zero curve Part A built, both are reported, and
whichever one actually matches the data better is reported as the winner
— without assuming in advance which one that would be, and it turns out
the answer is a little more specific than "the fancier one wins": Svensson
wins clearly overall, but mainly because it captures the short end of the
curve better, not because of anything special happening at the very long
end (20–40 years), which is where a "Japan has a complicated long end"
guess might have expected the difference to show up.

---

## Technical details

Fits Nelson-Siegel (4 parameters) and Svensson (6 parameters) to the
BOOTSTRAPPED ZERO curve from Part A (`models.bootstrap.
bootstrap_zero_curve`) — never to a par curve directly (§2). Both models'
decay parameter(s) are found by a bounded grid search, with the remaining
parameters solved by ordinary least squares at each grid point (§4) —
not a joint nonlinear optimizer.

**File:** `models/curve_fitting.py` — `fit_nelson_siegel()` /
`fit_svensson()` (the two public entry points), `NelsonSiegelFitResult` /
`SvenssonFitResult` (frozen results, each with a `.rate_at()` method and
a `.rmse_bp` property), `residuals_bp()` / `residuals_at_original_tenors()`
(diagnostics), `_grid_search_ns_tau()` / `_grid_search_svensson_taus()`
(the optimization).

**Output contract:** `fit_nelson_siegel(zero_curve) ->
NelsonSiegelFitResult` and `fit_svensson(zero_curve) ->
SvenssonFitResult`, where `zero_curve` is a
`models.bootstrap.bootstrap_zero_curve()` result (columns
`[maturity_years, zero_rate]`, any number of rows ≥ the model's own
parameter count). Both results carry the fit points, the fitted
parameters, the model's own rate at each fit point, an `rmse_bp`
property, and a `converged` flag.

---

## 1. The models, and their conventional parameter interpretation

**Nelson-Siegel** (4 parameters — `beta0, beta1, beta2, tau`):

```
y(m) = beta0 + beta1 * f1(m, tau) + beta2 * (f1(m, tau) - exp(-m/tau))
where f1(m, tau) = (1 - exp(-m/tau)) / (m/tau)
```

- **`beta0` — long-run level.** As `m → ∞`, `f1 → 0`, so `y(m) → beta0`.
  Checked directly, not just asserted:
  `test_beta0_is_the_long_run_level_limit` confirms `rate_at()` at a very
  large maturity converges to `beta0`.
- **`beta1` — short-term component (often read as slope).** As `m → 0`,
  `f1 → 1`, so `y(0) = beta0 + beta1` — the model's instantaneous short
  rate. Also checked directly (`test_beta0_plus_beta1_is_the_short_rate_limit`).
- **`beta2` — medium-term "hump" component.** Governed by the same `tau`
  as `beta1`; contributes a hump (or trough) centered near `m = tau`,
  vanishing at both very short and very long maturities.
- **`tau` — decay.** Controls both where `beta1`'s influence fades and
  where `beta2`'s hump is centered.

**Svensson** (6 parameters — adds `beta3, tau2`):

```
y(m) = beta0 + beta1*f1(m,tau1) + beta2*(f1(m,tau1) - exp(-m/tau1))
                                 + beta3*(f1(m,tau2) - exp(-m/tau2))
```

Identical to Nelson-Siegel, with a **second hump term** on its own decay
parameter `tau2` — letting the curve have two humps/inflections instead
of one. `beta0`'s long-run-level identity holds for Svensson too
(`test_svensson_beta0_is_the_long_run_level_limit`).

---

## 2. Fitting the ZERO curve, not the par curve

Every fit in this module targets a `bootstrap_zero_curve()` result, never
a par curve. Fitting NS/Svensson directly to a par curve is actually the
*more common* choice in industry practice — but a less principled one
here specifically, because this project already built a bootstrapped zero
curve (Part A) precisely to strip out the coupon-mixing distortion that
comes from reading a par-like series at face value
(`docs/phase_4_5a_documentation.md` §1's "coupon effect"). Fitting the
parametric model to the par curve instead would reintroduce exactly that
distortion into the smooth curve this module produces. `fit_nelson_siegel`
/ `fit_svensson` both validate their input has a `zero_rate` column and
reject anything shaped like a par curve (`yield` column only).

**Inherits Part A's own caveat.** The zero curve being fit here was
itself built by *treating* MOF's fitted-YTM series as a par curve — a
documented, measured simplification, not a fact about the underlying data
(`docs/phase_4_5a_documentation.md` §1/§5). This module describes that
curve's shape smoothly; it does not, and cannot, correct for that
upstream simplification. Every parameter and residual below describes
Part A's zero curve, not an independently verified ground truth.

---

## 3. What's actually being fit — and a caveat about it

`bootstrap_zero_curve()` returns roughly 80 points (one per semiannual
grid point, plus any sub-1Y money-market points the curve has) — but
only the 15 MOF tenors among them are genuine observations; the rest are points Part A generated by
straight-line-interpolating the (par-treated) input curve before
bootstrapping (`docs/phase_4_5a_documentation.md` §2.2). This module
fits against **all** of them, deliberately: that's the full object Part
A actually produced, and a future zero-curve-based pricing path (Part C)
would discount against every one of those points, not just the ~12–15
real ones.

**The disclosed tradeoff:** fitting against all ~80 points means the
reported RMSE partly measures how well a smooth parametric curve can
reproduce Part A's own piecewise-linear interpolation — including the
artificial "kinks" where one interpolated straight segment meets the
next (most visible across the sparse 10Y→20Y→30Y→40Y region).
`residuals_at_original_tenors()` isolates residuals at MOF's actual
quoted tenors specifically — the more meaningful diagnostic for "how well
does this parametric curve match the real data" — and §7 reports both.

---

## 4. The optimization approach — grid search, not joint nonlinear fitting

For a **fixed** `tau` (or `tau1, tau2`), `y(m)` is **linear** in the
betas — the hump functions become fixed columns of a design matrix once
the decay parameter(s) are fixed, so the betas that minimize squared
error are one ordinary-least-squares solve (`np.linalg.lstsq`), not an
iterative search. This module exploits that directly: it searches only
over `tau` (Nelson-Siegel) or `(tau1, tau2)` (Svensson), solving the
betas in closed form at every candidate.

**Why not joint nonlinear optimization of all parameters at once** — the
phase brief's own caution, taken seriously: `tau`/`lambda` decay
parameters are notoriously unstable under naive unbounded nonlinear
fitting. A gradient-based joint optimizer can walk `tau` to an
implausible extreme chasing a marginal, poorly-identified improvement, or
fail to converge outright — especially for Svensson, where `tau1` and
`tau2` can trade off against each other. Fixing the decay parameter(s) on
a bounded grid and solving the betas in closed form at every point
confines the genuinely nonlinear, unstable part of the problem to one (or
two) dimensions, searched exhaustively over sensible bounds rather than
trusted to a gradient step — and needs no `scipy` dependency (this
project has none installed; the same no-heavy-dependency-for-one-use
reasoning already applied to scikit-learn,
`docs/phase_4b_documentation.md` §1.2, and matplotlib,
`docs/phase_3c_documentation.md` §1.4).

**Bounds:** `[TAU_MIN, TAU_MAX] = [0.05, 50.0]` years — wide enough to
span well below the shortest maturity a curve here quotes (1Y on
today's grid; under a year for a curve with bills) to comfortably beyond
the longest (40 years), narrow enough to keep the search a genuine, bounded exploration
rather than an unbounded one.

**Two-stage (coarse, then refined) grid search.** Each fit runs a coarse
log-spaced grid across the full bounds (400 points for Nelson-Siegel;
150×150, `tau2 > tau1` enforced, for Svensson — ~11k valid combinations,
each a single closed-form 4-column least-squares solve, comfortably under
a second), then a finer linear grid zoomed into a window around the
coarse optimum. Standard pattern-search refinement; still only ever
closed-form linear least squares at each point, no gradient method
anywhere.

---

## 5. Convergence — checked and reported, not assumed

Since this is a grid search rather than an iterative optimizer,
"converged" has a concrete, checkable meaning here: did the refined best
`tau` (or `tau1, tau2`) land strictly inside `[TAU_MIN, TAU_MAX]`, or at
its edge? Landing at the edge means the search could not find a genuine
interior minimum within the space actually explored — reported as
`converged=False`, not silently accepted as a real fit.

**On the committed snapshot curve** (`prefer_live=False`, reproducible:
MOF, 2026-08-31): Svensson converges; Nelson-Siegel does not.

```
Nelson-Siegel:  tau = 50.000                      converged = False
Svensson:       tau1 = 2.941, tau2 = 19.447       converged = True
```

**What the non-convergence looks like.** Nelson-Siegel's search lands
exactly on `tau = TAU_MAX = 50.0`, with an implausible `beta0 ≈ -34%` —
a textbook case of the instability the phase brief warns about. The same
happened on a live MOF pull during this phase's development (`tau = 50`,
`beta0 ≈ -31%`), where probing up to `tau = 10,000` showed RMSE still
improving as `tau → ∞`: Nelson-Siegel's single hump can't find a good
interior optimum for this curve's shape, and wants to flatten out into a
slow trend a saturating hump is poorly suited to. Widening the bound
wouldn't fix it (§9).

**The pattern is about grid shape.** Both cases are the 15-tenor MOF grid
(1Y–40Y, no sub-year points). The snapshot's earlier 12-tenor grid, with
1M/3M/6M bills, gave Nelson-Siegel a steep short end to anchor its hump
on, and it converged there (`tau ≈ 34`). Since 2026-09-27 the snapshot
sits on the live grid, so this result is now reproducible offline rather
than a one-off live observation.
`tests/test_curve_fitting.py::test_convergence_on_the_real_snapshot_curve`
pins it, and `test_convergence_flag_actually_triggers_on_a_hard_case`
proves the mechanism fires on a deterministic synthetic case too.

---

## 6. Results, and which model wins — measured, not assumed

**On the committed snapshot curve** (`prefer_live=False`):

| Parameter | Nelson-Siegel | Svensson |
| --- | --- | --- |
| beta0 (long-run level) | -34.2511% | -4.3123% |
| beta1 (short-term/slope) | +35.4491% | +5.6436% |
| beta2 (medium-term/hump 1) | +59.3923% | +3.0532% |
| beta3 (medium-term/hump 2) | — | +26.8926% |
| tau1 (decay) | 50.000 | 2.941 |
| tau2 (decay) | — | 19.447 |
| **RMSE (all 80 fit points)** | **6.680bp** | **4.274bp** |
| Converged | **False** | True |

**Svensson fits this curve better** — 4.3bp RMSE vs. 6.7bp — and is the
only one of the two that converged, so its parameters are the only ones
worth reading (Nelson-Siegel's betas, sitting on a boundary `tau`, are
large offsetting numbers, not meaningful levels). This is measured, not
assumed: the phase brief warns against assuming Svensson wins because of
Japan's complex ultra-long segment, and the residual table below shows
that assumption would have been the wrong story. The dashboard follows
the same rule — a converged fit is preferred over a non-converged one
regardless of RMSE (`app.py`, `docs/phase_4_7_documentation.md`).

---

## 7. Residuals by tenor — where the improvement actually comes from

Residuals at MOF's **original quoted tenors** specifically
(`residuals_at_original_tenors` — the meaningful subset, §3), snapshot
curve:

| Tenor | NS residual | Svensson residual |
| --- | --- | --- |
| 1Y | +7.01bp | -5.33bp |
| 2Y | +9.04bp | +0.27bp |
| 3Y | +3.12bp | -1.26bp |
| 4Y | +2.44bp | +2.24bp |
| 5Y | -1.33bp | +1.95bp |
| 6Y | -6.48bp | -0.63bp |
| 7Y | -7.86bp | -0.37bp |
| 8Y | -6.47bp | +1.82bp |
| 9Y | -7.50bp | +0.86bp |
| 10Y | -6.06bp | +1.82bp |
| 15Y | +0.47bp | +1.93bp |
| **20Y** | **-0.17bp** | **-4.15bp** |
| **25Y** | **+19.18bp** | **+14.25bp** |
| **30Y** | **-1.64bp** | **-3.95bp** |
| **40Y** | **+2.46bp** | **+7.79bp** |

**The finding, stated plainly:** Svensson's overall RMSE win comes from
the **belly (6Y–10Y)**, where Nelson-Siegel misses by 6–8bp and Svensson
gets within 2bp, and from 2Y. In the **ultra-long segment (20Y–40Y)** —
the part of the curve this project's portfolio is most concentrated in
(`docs/phase_3c_documentation.md`'s ~51% ultra-long DV01 share) —
Svensson is **not better**: it's worse at 20Y, 30Y and 40Y, and better
only at 25Y. Both models miss 25Y badly (+14 to +19bp): the curve peaks
there (4.10%, above 30Y's 4.09%), a kink neither model's smooth humps
capture. A "Svensson wins because Japan's long end is complicated"
narrative, assumed in advance, would have been contradicted by this
curve's own residuals — which is exactly why the phase brief asked to let
the fit quality decide rather than assume it.

---

## 8. Known limitations (for the SR 11-7 validation report)

**8.1 Inherits Part A's par-curve simplification and its measured error.**
Every parameter and residual here describes a curve built by treating
MOF's fitted-YTM series as a par curve — a real, quantified
simplification (§2, `docs/phase_4_5a_documentation.md` §5), not a
correction of it.

**8.2 The RMSE partly reflects Part A's own interpolation, not only real
market data (§3).** Most of the ~80 fit points are Part A's own
straight-line-interpolated values between MOF's 15 real tenors, not
independent observations. `residuals_at_original_tenors` is the more
meaningful diagnostic and should be preferred over the whole-grid RMSE
when assessing real-world fit quality.

**8.3 The ultra-long-segment fit is not obviously better under either
model.** §7's finding is a real, disclosed result: the improvement
Svensson offers over Nelson-Siegel on this curve comes mostly from the
belly, not the ultra-long segment this project's portfolio actually
cares most about, and both miss the 25Y peak by 14–19bp. Neither model should be read as "solving" the
ultra-long region's shape.

**8.4 Convergence is data-shape-dependent, not universal.** §5's result
is real and reproducible: Nelson-Siegel fails to converge (in the sense
defined here) on the committed snapshot and on live MOF curves — both
15-tenor grids lacking sub-year points. Any consumer of this module should check
`.converged` rather than assume it.

**8.5 A grid search finds the best point on a finite grid, not a
provably global continuous optimum.** The two-stage (coarse + refined)
design narrows this gap considerably, but a sufficiently pathological
curve could in principle have a true optimum between grid points that
neither stage samples exactly. Not observed on this project's real data.

---

## 9. What would change this design

**A wider or narrower `TAU_MAX`/`TAU_MIN`**, if a future curve shape
needed it, is a module-level constant change — no restructuring. Widening
it blindly to chase convergence on every possible input isn't
recommended, though: §5's case showed RMSE still improving as
`tau → ∞`, meaning a wider bound there would have hidden a genuine
non-convergence behind an even-more-implausible boundary value rather
than fixing anything.

**A finer or coarser grid resolution** is a constant change
(`NS_COARSE_GRID_POINTS` etc.) trading runtime for search precision — no
change to the fitting logic itself.

**Fitting directly against real individual bond prices instead of Part
A's zero curve** would be the more fundamental fix to §8.1 — blocked on
the same future MOF issue reference module named in
`docs/phase_4_5a_documentation.md` §5/§7.

---

## 10. Relationship to the fallback re-anchoring policy and earlier phases

This module adds no new hardcoded market data — it computes directly from
whatever `bootstrap_zero_curve()` returns, inheriting Part A's
re-anchoring policy rather than adding a new one. `TAU_MIN`, `TAU_MAX`,
and the grid resolution constants are methodology choices (§4), not
observations of anything that moves, so there's nothing new here for that
policy to separately govern.
