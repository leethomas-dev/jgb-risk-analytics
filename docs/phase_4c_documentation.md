# Phase 4C Documentation — `models/factor_exposure.py`

## In plain English

Phase 4B found the handful of patterns the JGB curve tends to move in —
"everything shifts together," "short and long ends move apart," and so
on — and how big a typical day's move in each pattern actually is. This
part asks the question those patterns exist to answer: if the biggest
pattern (the one that explains most of the curve's real movement) had a
typical bad day, what happens to *this* portfolio's value? It answers in
both a percentage and a real currency figure, shows which of the three
patterns actually drives the portfolio's risk, and checks whether that
matches what Phase 3C already found about this portfolio leaning heavily
on its longest-dated bonds.

---

## Technical details

Projects the portfolio's existing Key Rate Duration / DV01 profile
(Phase 3A/3B) onto Phase 4B's PCA components, to quantify — in both
percent and currency terms — the P&L impact of a +1 standard deviation
move in each factor. Computes no new sensitivity of its own; recombines
already-validated numbers, the same pattern `ultra_long_profile.py`
(Phase 3C) uses for its own split.

**File:** `models/factor_exposure.py` — `FactorExposure` (one
component's exposure), `PortfolioFactorExposureResult` (the full result,
both frozen), `compute_portfolio_factor_exposure()`,
`plot_factor_exposure()`.

**Output contract:** `compute_portfolio_factor_exposure(portfolio, curve,
pca_result, freq=None, bump_size=DEFAULT_BUMP_SIZE) ->
PortfolioFactorExposureResult` — `freq` is forwarded unchanged to
`key_rate_duration_portfolio`/`dv01_by_tenor_portfolio`/`price_portfolio`:
`None` (default) prices each bond at its own `Bond.freq`, an explicit int
overrides every bond to one shared frequency (`docs/phase_2b_
documentation.md §1.3`). The PCA window used, its tenor grid, the
curve actually priced against (see §1.1), the portfolio's KRD/DV01 on
that grid, and one `FactorExposure` (percent and currency P&L) per PCA
component. `plot_factor_exposure(result, output_path=...,
metric="dollar") -> Path`, saving a PNG to `outputs/factor_exposure.png`.

---

## 1. What each piece does, and why

### 1.1 Tenor alignment — the main design problem

Phase 3A/3B's `key_rate_duration_portfolio` / `dv01_by_tenor_portfolio`
compute a value at every tenor on whatever curve they're handed — by
design, per `key_rate_duration.py`'s own docstring: "no assumption is
made about how many tenors the curve has or which ones." Phase 4B's PCA
loadings live on a *different* grid — whatever Phase 4A's ragged-tenor
policy retained for its lookback window — and there's no guarantee the
two match. Checked directly, they don't:

| Source | Tenor grid (prefer_live=False) |
| --- | --- |
| `load_jgb_curve()` (Phase 1 snapshot) | 0.083, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30, 40 |
| `load_jgb_curve_history()` (Phase 4A, 2yr window) | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30, 40 |

Only 9 of the 15 PCA tenors are on Phase 1's grid at all; Phase 1's three
sub-1-year bills never appear on Phase 4A's grid, since the historical
file has never published one.

Two ways to reconcile this were available:

1. **Restrict to the intersection** — keep only the 9 shared tenors,
   drop the rest from both sides.
2. **Recompute KRD/DV01 directly on the PCA's own grid** — build a curve
   whose tenor points are exactly the PCA tenors, with yield *levels*
   taken from Phase 1's curve via `bond_pricing.curve_yield_at()` (the
   same straight-line interpolation `price_bond` already uses for every
   cash flow that doesn't land on a curve grid point), then run
   `key_rate_duration_portfolio` / `dv01_by_tenor_portfolio` on that
   curve unmodified.

This module uses (2), implemented in `_curve_aligned_to_pca_grid()`, for
two reasons. First, (1) throws away real information for no benefit —
none of it is fabricated, but a portfolio bond's sensitivity at a tenor
the intersection excludes doesn't disappear from the portfolio, it just
disappears from the analysis. Second, and more fundamentally, KRD isn't
safe to reconcile by interpolating the *KRD values themselves* across
grids the way (1) implicitly would for the tenors it does keep: a KRD is
a tent-shaped sensitivity tied to its own grid's specific neighboring
points (`key_rate_duration.py`'s own docstring), not a smooth function of
maturity — there's no valid way to ask "what would the KRD at 15Y have
been on a grid that never had a 15Y point" by interpolation. (2) sidesteps
the question entirely: `key_rate_duration_portfolio` already supports an
arbitrary grid by design, so recomputing it fresh on the PCA's own grid
needs no new capability, only a curve with the right tenor points — and
building *that* is exactly what `curve_yield_at`'s interpolation is for.

This means every KRD/DV01 number this module reports is a **fresh
computation on the PCA-aligned grid**, not a lookup into Phase 3A/3B's
own native-grid tables. The two are honestly different: on Phase 1's
native 12-tenor grid, this portfolio's DV01 ultra-long (≥20Y) share is
**55.7%** (`compute_ultra_long_profile`, Phase 3C); on the PCA-aligned
15-tenor grid, it's **52.6%**. Both are real, correctly-computed numbers
— a denser grid splits the same total risk across more tent shapes,
shifting a few points of share without changing the total. The
qualitative finding (roughly half the book's rate risk sits at 20Y+) is
stable across both; the exact percentage is grid-dependent, and that
dependency is disclosed here rather than left for a reader to discover.

**Extrapolation, checked rather than assumed.** If a PCA tenor fell
outside Phase 1's curve range, `curve_yield_at` would extrapolate flat
rather than interpolate — a different, weaker kind of estimate for that
one point. Checked directly: it doesn't happen on this project's real
data (Phase 4A's grid, 1Y–40Y, sits entirely inside Phase 1's range on
both the live and snapshot sources). `_curve_aligned_to_pca_grid` reports
which tenors (if any) needed it via `extrapolated_tenors`, and
`test_extrapolation_is_detected_when_the_curve_is_narrower_than_the_pca_grid`
proves the detection logic actually fires, against a synthetic case,
rather than only ever reporting empty by construction.

### 1.2 The linear estimate, validated against an exact reprice

Each component's exposure is `Σ_k -KRD_k × shock_k` (percent) and
`Σ_k -DV01_k × (shock_k / bump_size)` (currency) — `shock_k` being
`pca_result.implied_yield_shock(component)`, the +1-std yield-change
vector Phase 4B already computed. This is the standard parametric
approach to sizing a multi-tenor factor move (the same sensitivity ×
shock, summed-across-factors logic RiskMetrics-style parametric VaR
uses), but it's a **first-order estimate** — it doesn't capture the
convexity an actual reprice under the full shocked curve would show.

Rather than assume that gap is small, `__main__` checks it: build the
shocked curve (`aligned_curve.yield + shock`), reprice the whole
portfolio via `price_portfolio`, and compare. On the project's default
2-year window (`prefer_live=False`):

| Component | Linear estimate | Exact reprice | Gap |
| --- | --- | --- | --- |
| PC1 | -0.2923% | -0.2943% | -0.0020 pp |
| PC2 | -0.0599% | -0.0628% | -0.0029 pp |
| PC3 | +0.0050% | +0.0044% | -0.0007 pp |

All three gaps are a fraction of a basis point of price — the linear
estimate is a good approximation *at these shock sizes* (component
standard deviations of roughly 1.5–10bp on this window), which is
checked here, not a property that would automatically hold for a much
larger shock (e.g. a multi-standard-deviation stress move, where
convexity would matter more — that's this project's Phase 5, not this
module).

### 1.3 Units — matching Phase 3B's DV01 convention, not inventing a new one

`pct_pnl` is a fraction of portfolio value (e.g. `-0.0029` == -0.29%).
`dollar_pnl` is **currency per 100 face value** — the exact convention
`dv01.py` established in Phase 3B (`docs/phase_3b_documentation.md`
§"units") and `ultra_long_profile.py` reused in Phase 3C: never scaled to
a real position size, since this project's portfolio weights and coupons
are already illustrative (Phase 2A), and inventing a position size on
top would just add a second made-up number.

The two figures are **independently computed**, not one derived from the
other: `pct_pnl` comes from the KRD vector, which `key_rate_duration_portfolio`
weights by portfolio weight alone; `dollar_pnl` comes from the DV01
vector, which `dv01_by_tenor_portfolio` weights each bond's sensitivity
by that bond's *own* price. Since every bond in this portfolio prices
near par by construction (`config/portfolio.json`'s own disclaimer), the
two stay close — checked directly, the gap between `dollar_pnl` and
`pct_pnl × base_price` is under half a cent per 100 face on all three
components — but they are not expected to be bit-identical, and the code
doesn't force them to be.

### 1.4 The chart: a diverging, not categorical, color job

`plot_factor_exposure` colors each bar by sign (gain/loss), using a
validated blue/red diverging pair (`#2a78d6` / `#e34948`) rather than
reusing `ultra_long_profile.py`'s blue/red pair directly. The two charts
are answering different *jobs*: Phase 3C's
chart is categorical (which group — ultra-long or not — does this tenor
belong to), while this chart is polarity (did this factor move help or
hurt the portfolio) — the same color pair would have been the wrong
reason for the right-looking chart. `metric="dollar"` (default) or
`"pct"`, matching `plot_ultra_long_profile`'s own `metric` parameter
pattern exactly.

### 1.5 `__main__`

Loads the default PCA window, computes the exposure table, runs the
§1.2 exact-reprice sanity check and prints its actual numbers (not just
asserts them in tests), saves the chart, and prints the interpretive
summary in §2 below.

---

## 2. Interpretive finding: which factor actually drives this portfolio

On the project's default 2-year PCA window and the committed snapshot
data (`prefer_live=False`):

| Component | Variance share | % P&L (+1 std) | $ P&L (+1 std, per 100 face) |
| --- | --- | --- | --- |
| PC1 | 75.3% | -0.29% | -0.293 |
| PC2 | 20.5% | -0.06% | -0.063 |
| PC3 | 1.7% | +0.01% | +0.004 |

**PC1 dominates**, by roughly 4-5x the next-largest component. This
isn't only because PC1 explains the most curve-change variance — its
loadings are also checked directly to stay elevated from the belly of
the curve through the 40Y point (0.28–0.32 from 8Y to 40Y, vs. 0.08–0.16
for 1Y–3Y), rather than being flat across all tenors. A "level" factor
with that shape moves the long end by roughly as much as, or slightly
more than, the middle of the curve — and this portfolio's DV01 is
already concentrated exactly there: **51.8%** of it sits at 20Y or beyond
on the PCA-aligned grid (§1.1), consistent with Phase 3C's finding
(**55.7%** on Phase 1's native grid) that this portfolio's ultra-long
holdings dominate its overall interest-rate risk. The two results agree
because both are reading the same underlying portfolio and the same
underlying curve shape — not because one was built to match the other.

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 A linear, single-factor-at-a-time estimate.** Each row in §2
answers "what if *only* this component moved by its own +1 std" — it
does not represent a joint, multi-factor stress scenario, and the three
rows should not be summed as if they were independent, simultaneous
moves with no correlation structure between them (by construction, PCA
components ARE uncorrelated with each other over the fitting window, so
summing variances would be valid — summing signed P&L figures as if they
happened together is a different, stronger claim this module doesn't
make).

**3.2 Inherits Phase 4B's lookback-window caveat.** Every number in §2 is
conditional on Phase 4B's chosen 2-year, post-YCC window
(`docs/phase_4b_documentation.md` §1.5/§4.2) — a different window would
shift the split between PC1 and PC2 and, with it, exactly how dominant
PC1 looks.

**3.3 Grid-dependent risk shares (§1.1).** The 51.8% vs. 55.7%
ultra-long-share gap between this module's PCA-aligned grid and Phase
3C's native grid is real and disclosed, not an error in either — but a
reader comparing the two documents' numbers directly needs to know they
were computed on different grids.

**3.4 A historical covariance estimate assumes the recent past is a
reasonable guide to near-future curve behavior** — Phase 4B's own
caveat (§4.3 there), inherited here and now expressed as a concrete
currency figure, which reads as more certain than a variance ratio does.

**3.5 Inherited the no-zero-curve caveat carried through the whole
project — resolved, in part, by Phase 4.5.** At the time this module was
built, pricing here (via `price_bond`/`price_portfolio`) was on the same
par-curve, non-bootstrapped basis established in Phase 1 and carried
through Phase 2B. A bootstrapped zero curve now exists and can be
substituted directly (`docs/phase_4_5a_documentation.md`,
`docs/phase_4_5c_documentation.md`); this module's own KRD/DV01/factor-
exposure calls still default to the par curve, unchanged, and were not
rerun against the zero curve as part of Phase 4.5 — see
`docs/phase_4_5c_documentation.md` §2 for how large that difference is
elsewhere in this project's own portfolio.

---

## 4. What would change this design

**A joint, multi-factor stress scenario** (§3.1) — combining all three
components' shocks into one simultaneous curve move, weighted by however
correlated a chosen scenario assumes them to be — would be new work: a
different question (a specific stress narrative) than "how exposed is
this portfolio to each factor on its own," and closer to this project's
own Phase 5 (Scenario/VaR) than an extension of this module.

**A different PCA window or component count** needs no code change here
— both are `compute_curve_pca`'s own parameters (`docs/phase_4b_documentation.md`),
and this module recomputes cleanly against whatever `CurvePCAResult` it's
handed.

**Restricting to the tenor intersection instead of recomputing on the
PCA grid** (§1.1's rejected option) would be a straightforward swap
inside `_curve_aligned_to_pca_grid` alone if a future need ever favored
it — not built now because it discards real information for no accuracy
benefit on this project's own grids.

---

## 5. Relationship to Phase 3A/3B/3C and the fallback re-anchoring policy

This module adds no new hardcoded market or portfolio data — it reads
the portfolio and curve through their own loaders (Phase 1, Phase 2A)
and reuses Phase 3A/3B's pricing functions unmodified, inheriting their
re-anchoring policy rather than adding a new one
(`docs/phase_1_documentation.md` §5). Its own KRD/DV01 numbers are real
computations, not copies of Phase 3A/3B/3C's — see §1.1 for why they can
legitimately differ from those modules' own native-grid figures for the
same portfolio and (approximately) the same curve.
