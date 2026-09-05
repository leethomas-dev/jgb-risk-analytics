/# Phase 4B Documentation — `models/pca.py`

## In plain English

The JGB curve doesn't move as 15 independent numbers each day — when
rates move, tenors mostly move together, in a handful of recognizable
patterns. One common pattern is "everything moves up or down together."
Another is "short-term rates and long-term rates move in opposite
directions" (the curve steepens or flattens). This part finds those
patterns directly from real historical data, rather than assuming them,
and measures how much of the curve's day-to-day movement each pattern
actually accounts for. That matters because it turns "15 numbers to
track" into a handful of underlying drivers — which is what Phase 4C uses
next to show which of those drivers this portfolio is actually exposed
to.

---

## Technical details

Principal Component Analysis (PCA) of daily JGB yield changes: finds the
small number of curve-movement patterns ("components") that explain most
of the observed covariance between tenors, estimated directly from real
history via Phase 4A's loader.

**File:** `models/pca.py` — `CurvePCAResult` (the result, frozen),
`compute_curve_pca()`.

**Output contract:** `compute_curve_pca(history, n_components=3) ->
CurvePCAResult` — a frozen result carrying the window used, the tenor
grid, per-component explained variance (absolute and as a share, plus the
full spectrum), unit-norm tenor loadings, and `component_std` /
`cumulative_explained_variance_ratio` / `implied_yield_shock()` derived
from those.

---

## 1. What each piece does, and why

### 1.1 Changes, not levels — and why that's not a stylistic choice

`compute_curve_pca` takes the levels history `load_jgb_curve_history`
returns and differences it internally (`history.diff().dropna()`) before
doing anything else. Yield **levels** are strongly non-stationary (today's
rate sits close to yesterday's) and highly correlated across tenors for a
reason that has nothing to do with risk — the whole curve drifts with the
general level of rates over time. Running PCA on levels would mostly
recover that drift as "PC1": a trend, not a risk factor. A risk model
needs the covariance structure of **movements** — on a day rates move,
which tenors moved together, and by how much — because that's what
actually feeds a P&L distribution. Differencing once, inside this
function, means every caller gets this right automatically rather than
having to remember to difference first.

**A checked-not-assumed point about the differencing itself.** `history.diff()`
operates on row position, not on calendar spacing — if two consecutive
retained rows aren't actually one business day apart (a multi-day holiday
cluster, or a date the ragged-tenor policy's residual-row check happened
to drop), that row's "daily change" silently covers however many days
actually elapsed. Checked directly against the project's default 2-year
window rather than assumed away: **20 of 484 observations span more than
3 calendar days** (Japanese public-holiday clusters — Golden Week, New
Year, up to 7 days). This is not a defect: with no market open in
between, the pre/post print-to-print change *is* the correct estimate of
that gap's movement — this is the standard trading-day-return convention
every daily-change fixed-income risk model uses, not a uniform-calendar-
spacing assumption. The same reasoning would hold if the residual-row
check (`docs/phase_4a_documentation.md` §1.3 step 2) ever actually
dropped an interior row for a genuine data outage.

### 1.2 Hand-rolled SVD, not scikit-learn

This module does exactly one eigendecomposition, on one small
(≈500 × 15) matrix. `numpy.linalg.svd` does this correctly, is already in
the project's dependency graph (via pandas), and finishes in a fraction
of a second at this size. Adding scikit-learn — a large library built
around fitting/training pipelines and many estimators — for one function
call would be the same heavy-dependency-for-one-feature tradeoff
`matplotlib`'s addition in Phase 3C was weighed against
(`docs/phase_3c_documentation.md` §1.4), only more so: matplotlib at
least renders every chart this project produces; scikit-learn's `PCA`
would only earn its place if this project were about to run several more
estimators, which it isn't right now.

SVD is used instead of eigendecomposing the covariance matrix
(`XᵀX`) directly — numerically more stable, since forming `XᵀX` squares
the matrix's condition number before decomposing it. Same eigenvectors
and eigenvalues either way (`test_matches_an_independent_numpy_covariance_eigendecomposition`
checks this directly, via a *different* numpy code path —
`np.cov` + `np.linalg.eigh`).

This module requires `history` to already be complete (no missing
values) — `load_jgb_curve_history`'s default ragged-tenor policy
guarantees this for any window, so nothing here needs to repair or work
around gaps itself. A pairwise-complete-covariance approach that *could*
accept a ragged input directly was built, tested, and deliberately not
kept in the end — see §5.

### 1.3 The sign convention

A PCA loading vector's sign is mathematically arbitrary — `svd(X)` and
the same decomposition with one component's sign flipped both describe
`X` equally correctly. Left alone, the sign is an artifact of the solver,
not a fact about the data. This module fixes it deterministically: for
each component, flip its sign (and the matching column of the score
matrix `U`) so its **largest-magnitude tenor loading is positive** — the
same rule scikit-learn's own `svd_flip` uses by default. This makes the
output reproducible run to run without asserting an economic direction
(it does *not* mean "PC1 always means yields went up") — the actual sign
*pattern* across tenors is then read off as an empirical finding (§2),
not forced into a label.

### 1.4 `CurvePCAResult` and its derived properties

Frozen, like Phase 3C's `UltraLongProfile` — a snapshot of one fit.
`component_std` (√explained_variance) and `cumulative_explained_variance_ratio`
are computed on the fly from the stored arrays rather than stored
themselves, so they can't drift out of sync with the numbers they're
based on (the same reasoning as `UltraLongProfile`'s share properties,
`docs/phase_3c_documentation.md` §1.2).

`implied_yield_shock(component)` — `component_std × that component's
loading vector` — is the direct handoff to Phase 4C: the real, decimal-
yield-units shock a "+1 standard deviation move in this factor" implies
at every tenor. Phase 4C multiplies a KRD/DV01 vector against this
directly; nothing about what "one standard deviation" means is
recomputed there.

### 1.5 The lookback window: a modelling choice, made explicitly

`DEFAULT_PCA_LOOKBACK_YEARS = 2.0`. The BOJ formally exited yield curve
control (YCC) in March 2024; a 2-year window (2024-09-02 to 2026-08-31
against the committed snapshot) sits **entirely after** that exit. A
longer window would capture years where short- and medium-tenor yields
were deliberately pinned by policy — real history, but not representative
of the volatility a *current* risk model should assume. The tradeoff,
stated plainly rather than hidden in a constant: a 2-year window yields
485 daily observations against 15 tenors (still comfortably `n_obs >>
n_tenors` for a stable covariance estimate); a 5-year window yields
~1,220 — more statistically comfortable, but at the cost of diluting
today's regime with a suppressed one. Computed directly for comparison:

| Window | PC1 | PC2 | PC3 | Cumulative top 3 |
| --- | --- | --- | --- | --- |
| 2 years (chosen) | 75.3% | 20.5% | 1.7% | 97.5% |
| 5 years (spans YCC) | 77.8% | 16.8% | 2.1% | 96.7% |

Both windows land in a similar place for the top-3 total, but the
5-year split shifts weight from PC2 toward PC1 relative to the 2-year
window — consistent with a YCC-era curve where medium/long yields were
more policy-anchored, so more of the observed movement outside that
anchor loads onto a single broad factor rather than a distinct slope
factor. The 2-year window is used by default here; `n_components` and
the lookback (via `load_jgb_curve_history`'s own parameter) are both
ordinary inputs a caller can override.

Measured directly across every distinct regime in the data (10Y daily
yield volatility), the same point shows up even more sharply:

| Era | 10Y level range | Daily volatility |
| --- | --- | --- |
| 1986–1999 (bubble collapse) | 0.77%–8.11% | 4.92 bp |
| 1999–2016 (ZIRP begins) | 0.21%–2.43% | 2.83 bp |
| 2016–2024 (YCC era) | -0.29%–0.96% | 1.57 bp |
| 2024–2026 (current) | 0.73%–2.94% | 3.08 bp |

Today's regime is nearly **2x** more volatile than the YCC years it just
left — a wider window would systematically understate current risk by
blending the two. (One tempting shortcut doesn't hold up: "older data is
just noisier, so exclude it on that basis." Checked directly against
1980-86 vs. today: 1980-86's daily volatility was 9.7bp vs. today's
9.9bp — essentially identical, despite wildly different yield levels.
The real reason to keep the window short is regime relevance, shown in
the table above, not that old data is inherently messier.)

### 1.6 Why one fixed window, not PCA stitched across eras

MOF's tenor set grew in stages, not all at once (9 tenors from 1974; +10Y/
20Y in 1986; +15Y in 1991; +30Y in 1999; +25Y in 2004; +40Y in 2007). An
alternative design would run PCA separately per era — whatever tenors
were complete then — and combine the results into one long-run view.
Considered and rejected, for three concrete reasons:

1. **Different eras give incompatible loading vectors.** A 9-tenor era's
   PC1 is a 9-dimensional vector; a 15-tenor era's is 15-dimensional —
   nothing to average or concatenate between them.
2. **Explained variance isn't comparable once the tenor set changes.**
   Confirmed directly: dropping just one tenor (40Y) from the *same*
   dates shifted PC1's share from 75.3% to 78.4% (and moved real weight
   between PC1/PC2 too) — "75% of variance" stops meaning the same thing
   across tenor sets, let alone across eras with different market
   regimes as well.
3. **A component's identity isn't guaranteed to persist across separate
   fits.** "PC2" is only defined as "whatever direction captures the
   second-most variance in *this* covariance matrix" — matching it to
   another era's "PC2" needs explicit loading-similarity matching (or
   rotation) across fits, not assumed continuity.

Resolving the three problems above (component tracking/rotation across
eras) is a real, legitimate technique — but it answers a different
question ("how has the JGB market's own factor structure evolved since
1974") than this module exists to answer ("what factors describe TODAY's
curve, for Phase 4C's current portfolio"). Phase 4C needs one
tenor-consistent factor set aligned to the current curve; a stitched
multi-era result wouldn't have a single tenor grid to align a KRD vector
against in the first place. A historical factor-evolution study is a
legitimate, separate deliverable — noted in §5 — not a strict improvement
on this one.

**Where the older, more extreme data actually belongs:** not blended into
a "normal regime" covariance estimate, but replayed directly — the exact
historical yield-curve move from a stress period, applied unchanged to
today's portfolio via the pricing/KRD/DV01 machinery that already exists.
That's historical-scenario analysis, and it's this project's own Phase 5
(Scenario/VaR), not Phase 4B. `load_jgb_curve_history` keeps the full
history available (no lookback limit needed) for exactly that future use.

### 1.7 `__main__`

Loads the default window, fits PCA, prints explained variance (with
cumulative total) and the full loadings table, then runs the §2 sanity
check and prints its actual result — pass or fail — rather than only
asserting it in the test suite.

---

## 2. The sanity check: does this curve show the conventional shapes?

Litterman & Scheinkman's classic 1991 result for a sovereign yield curve:
the first three components typically read as **level** (all tenors move
the same way), **slope** (short and long ends move opposite ways), and
**curvature** (the belly moves opposite to both wings). This is
conventional, not guaranteed — checked directly against the project's
2-year window, not assumed:

```
PC1 ('level'?): consistent sign across all 15 tenors -- True
PC2 ('slope'?): short end (1Y = -0.165) vs. long end (40Y = +0.454) opposite sign -- True
PC3 ('curvature'?): wings (1Y = +0.324, 40Y = +0.527) same sign -- True; belly (8Y = -0.123) opposes them -- True
```

All three hold. The top 3 components together explain **97.5%** of total
curve-change variance — consistent with the conventional finding that a
sovereign curve's movements are overwhelmingly low-dimensional. Had any
of these checks failed, the correct response would be to report that
finding as-is (per the project brief) rather than relabel or discard it —
this loader's real, ragged-tenor-aware history makes that an honest
possibility, not a foregone conclusion.

---

## 3. Fitted, not calibrated — a correction to an earlier assumption

An earlier stage of planning this project assumed the PCA covariance
structure would have to be **hand-calibrated** from plausible assumptions,
since no real curve history was available at the time. That assumption
never made it into any committed code or doc in this repository — there
was nothing to edit — but it's worth stating plainly here since it shaped
the original Phase 4 scope: with Phase 4A's historical loader now in
place, every number this module reports is **estimated directly from
real observed MOF yield changes**. Nothing here is an assumption dressed
up as a result. This is a material improvement to the model's
defensibility for the SR 11-7 report — a fitted covariance structure
carries "this is what the market actually did," while a calibrated one
would only ever carry "this is what we assumed the market would do."

---

## 4. Known limitations (for the SR 11-7 validation report)

**4.1 Inherits Phase 4A's ragged-tenor policy.** The tenor grid PCA runs
on is whatever the requested lookback window's intersection of complete
tenors turns out to be (`docs/phase_4a_documentation.md` §1.3) — a longer
window can mean *fewer* tenors, not more data, which would change which
covariance structure gets estimated.

**4.2 The lookback window is a real modelling choice with a real
tradeoff (§1.5), not a neutral default.** A different, equally defensible
choice (e.g. 5 years) gives a visibly different split between PC1 and
PC2. Anyone consuming this module's output should know which window
produced it.

**4.3 A historical covariance estimate assumes the recent past is a
reasonable guide to near-future curve behavior.** True of any
historically-fitted risk factor model, not specific to this
implementation — worth restating since Phase 4C turns this into a
currency P&L figure, which reads as more concrete than a variance ratio.

**4.4 The conventional level/slope/curvature labels are interpretive,
not load-bearing.** The code and the result object never rely on these
labels meaning anything — `CurvePCAResult` just calls them "component 1,
2, 3." §2's check is a validation signal, not a classification the rest
of the pipeline depends on.

---

## 5. What would change this design

**Exponentially-weighted (RiskMetrics-style) covariance, instead of a
fixed equal-weight window.** This is the standard alternative to the hard
window-cutoff approach used here, and it targets the exact same tradeoff
§1.5 documents (a short window has less data; a long one dilutes today's
regime with a stale one) without picking a hard boundary — older
observations decay smoothly rather than being fully in-sample one day and
fully excluded the next. Not used here, deliberately: a fixed post-YCC
window gives a reader (or a validator) a plain, falsifiable claim — "this
result is estimated from the regime that started when YCC ended, full
stop" — where an EWMA half-life is one more parameter to justify and
makes "which regime is this covariance estimate actually describing"
harder to answer precisely. Equally defensible; the smaller-decision-
surface option was preferred for this project. Would slot in as an
alternative weighting scheme inside `compute_curve_pca` (a `halflife`
parameter producing weighted, not uniform, row weights before the SVD)
without changing `CurvePCAResult`'s shape.

**A different lookback window or `n_components`** need no code change —
both are already ordinary parameters (`load_jgb_curve_history`'s
`lookback_years`, `compute_curve_pca`'s `n_components`).

**Adding scikit-learn later** (§1.2), if this project ever needed several
more estimators, would replace the SVD block inside `compute_curve_pca`
alone — the function's signature and `CurvePCAResult`'s shape wouldn't
need to change, since scikit-learn's `PCA` produces the same loadings and
explained-variance numbers from the same input.

**A historical factor-evolution study** (§1.6) — PCA run separately per
tenor-set era, with components explicitly matched or rotated across eras
— would be new work, not a modification of this module: a different
question (how has the JGB market's factor structure changed since 1974)
with a different kind of output (a narrative across eras, not one
current factor set), for a goal this project doesn't currently have.

**Pairwise-complete covariance for a ragged window** — estimating each
tenor pair's covariance from whatever dates both tenors actually share
(`pandas.DataFrame.cov()`'s native NaN handling), instead of requiring
`load_jgb_curve_history`'s default policy to drop an incomplete tenor for
the whole window first — was actually built and tested during this
project's development (via `np.linalg.eigh` on the resulting covariance
matrix, plus a positive-semi-definiteness check, since a pairwise-built
matrix isn't automatically guaranteed valid the way a complete-case one
is). It reduced to identical output on this project's own 2-year default
window, and correctly handled a genuinely ragged one in testing. It was
removed rather than kept, for reasons worth being explicit about:

- **The sample size here never needed it.** Pairwise-complete exists to
  rescue an estimate that's genuinely short of data — its entire value
  proposition is "use every real observation because you can't afford to
  waste any." That's not this project's situation: the default window
  gives **485 daily observations across 15 tenors, ≈32 observations per
  tenor** (computed directly, not estimated) — comfortably past the point
  where more data meaningfully tightens a covariance estimate. Paying for
  pairwise-complete's validity risk buys a benefit (more usable
  observations) that this project's own numbers say it doesn't need.
- **Nothing in the recommended workflow ever calls it with ragged data.**
  The default window has no gaps, and §1.6 already establishes that
  widening the window is the wrong move for *regime* reasons regardless of
  whether missing tenors are handled well.
- **Keeping unused code that exists solely to make a not-recommended
  action less lossy** was judged not worth its added surface (a new
  failure mode, a new loader parameter, and real documentation upkeep
  that had already surfaced mid-development).

If a genuine future need for it arises (e.g. a deliberate wide-window
sensitivity study, or a future dataset where the obs-per-tenor ratio is
actually tight), this paragraph is the starting point for rebuilding it,
not a dead end — that combination of conditions is the honest trigger for
revisiting this decision, not a stylistic preference.

**EM-based covariance imputation** — iteratively estimating the
covariance matrix and the missing cells together, each refined against
the other until convergence — was considered and rejected on a more basic
ground than pairwise-complete's, and wasn't built to reach it: unlike
pairwise-complete, an EM estimate *is* guaranteed positive semi-definite,
which actually fixes pairwise-complete's own validity gap. But it does
this by filling in a real number for every missing cell — exactly the
values `docs/phase_4a_documentation.md` §1.3's policy exists to refuse:
a yield for a bond that hadn't been issued yet, or for months of a genuine
1978–1980 reporting gap. Trading a real methodological wart (non-PSD-ness,
never actually encountered against this project's own data) for a
manufactured number is the wrong trade for a model whose central
commitment is that every input is something MOF actually published, not
something inferred to make a matrix behave. This is why the two
alternatives get different treatment above: pairwise-complete was worth
actually building and testing to find out it wasn't needed; EM-imputation
fails the project's own stated principle before the question of whether
it's needed even comes up.

---

## 6. Relationship to the fallback re-anchoring policy

This module adds no new hardcoded market data of its own — it computes
directly from whatever `load_jgb_curve_history` returns, inheriting that
module's re-anchoring policy (`docs/phase_4a_documentation.md` §2) rather
than adding a new one. `DEFAULT_PCA_LOOKBACK_YEARS` is a methodology
choice (§1.5), not an observation of anything that moves, so there's
nothing here for Phase 1's policy to separately govern.
