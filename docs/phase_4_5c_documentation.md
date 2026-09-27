# Phase 4.5C Documentation — pricing integration + PCA comparison

> **Special Phase A (2026-09-27):** the zero curve wired in here as an
> option is now the project-wide default, and §2's −4.72% DV01 gap turned
> out to answer a narrower question than the one that mattered (§2.1).
> See [`special_phase_a_documentation.md`](special_phase_a_documentation.md).

## In plain English

Parts A and B built a more theoretically correct set of interest rates
(a "zero curve") and a smooth mathematical description of it, but
neither was actually plugged into the pricing engine yet — every price,
duration, and hedge-sizing number this project produces still used the
older, simpler approach. This part does two things. First, it wires the
zero curve in as an option (never replacing the original approach, which
stays the default) and uses it to answer a real question: how much did
that older, simpler approach actually get wrong? The answer turns out to
depend heavily on how far away a bond's repayment date is — barely
anything for a 2-year bond, but a real, material amount for a 40-year
one. Second, it checks something Part B's smooth curve description
implicitly claims: that its three shape parameters (called "level",
"slope", and "curvature") describe the same real market patterns Phase
4B's separate statistical analysis (PCA) already found in years of real
data. The honest answer is: partly. One of the three lines up well; the
other two, less so — and this part explains why, rather than picking the
answer that sounds better.

---

## Technical details

Two deliverables, described in the same module docstrings and organized
here in the same order:

**C1 — pricing integration.** `models/bond_pricing.py`'s `price_bond`
(and, by extension, everything built on it) can now discount off a
bootstrapped zero curve instead of a par curve — auto-detected from which
rate column the curve has, not a new parameter, so no existing call site
anywhere in this project needed to change. `models/zero_curve_impact.py`
(new) reprices the standard portfolio both ways and reports the
difference in price, effective duration, and DV01, plus a per-tenor
KRD/DV01 comparison.

**C2 — NS/Svensson vs. PCA loadings.** `models/zero_curve_impact.py`
also compares Nelson-Siegel's fitted basis-function shapes (Part B)
against PCA's own loading vectors (Phase 4B) via cosine similarity, with
explicit sign-alignment handling.

**Files touched:** `models/bond_pricing.py` (extended — `price_bond`
gains a zero-curve discounting branch), `models/key_rate_duration.py`
(extended — `_bump_curve_at`/`effective_duration_bond` generalized to
bump either curve's rate column), `models/dv01.py` (unchanged — inherits
the capability automatically), `models/zero_curve_impact.py` (new — the
comparison logic and both charts).

---

## 1. C1: making `price_bond` discount off either curve basis

### 1.1 Auto-detection by column, not a new parameter

`price_bond(face_value, coupon_rate, maturity_years, curve, freq=2)`'s
signature is **completely unchanged**. What changed is what it does with
`curve`: if `curve` has a `yield` column, it takes the exact, original
code path (`curve_yield_at` + compounding) — byte-for-byte the same logic
that existed before this phase. If `curve` has a `zero_rate` column
instead — i.e. a `models.bootstrap.bootstrap_zero_curve()` result, passed
in unmodified — it discounts each cash flow via
`models.bootstrap.discount_factor_at()` instead:

```python
if "zero_rate" in curve.columns:
    from models.bootstrap import discount_factor_at
    discount_factors = discount_factor_at(curve, cash_flow_times, freq=freq)
    return float(np.sum(cash_flows * discount_factors))

yields_at_flows = curve_yield_at(curve, cash_flow_times)
discount_factors = (1.0 + yields_at_flows / freq) ** (freq * cash_flow_times)
return float(np.sum(cash_flows / discount_factors))
```

**Why detection-by-column, not a `discount_basis` parameter:** this
project's pricing/KRD/DV01/ultra-long/factor-exposure modules already
share one long-standing principle — a curve is an opaque DataFrame, read
for whatever it actually contains, never assumed to have a fixed shape
(`docs/phase_1_documentation.md` §3.5). Column-detection is a direct
extension of that same principle to *which discounting basis* a curve
implies, not a new idea bolted on. The practical payoff is real:
`price_portfolio`, `key_rate_duration_portfolio`,
`dv01_portfolio`/`dv01_by_tenor_portfolio` — every one of them just
passes `curve` straight through to a bond-level function — so **all of
them already support zero-curve discounting with zero code changes of
their own**, simply by being handed a `bootstrap_zero_curve()` result.

### 1.2 What actually needed to change: one small, additive generalization

Only one other file needed a real code change.
`key_rate_duration.py`'s bump logic (`_bump_curve_at`,
`effective_duration_bond`) previously hardcoded the literal string
`"yield"` when nudging one curve row. A curve with a `zero_rate` column
instead would have silently created a *spurious new* `"yield"` column via
pandas' `.loc` assignment — a real, dangerous landmine, not a
hypothetical one. The fix: a small `_rate_column(curve)` helper that
returns whichever rate column is actually present, used everywhere the
old code hardcoded `"yield"`. For any existing (par-curve) caller,
`_rate_column` returns `"yield"` — the exact original literal — so
behavior is unchanged bit-for-bit.

`dv01.py` needed **no changes at all**: every function in it is a thin
currency-unit wrapper over `price_bond` and `key_rate_duration.py`'s
functions (its own module docstring says as much), so it inherited
zero-curve support automatically once those two did.

### 1.3 Avoiding a circular import

`models.bootstrap` already imports `curve_yield_at` from
`models.bond_pricing` (Part A). Importing `models.bootstrap` back into
`models.bond_pricing` at module level would create a cycle. The fix is a
**local import inside the zero-curve branch** — deferred to call time,
so the module-level import graph stays acyclic. This isn't a new pattern
introduced here: `models.bootstrap.implied_ytm` already uses exactly this
technique, for the reverse direction, for the same reason
(`docs/phase_4_5a_documentation.md`).

### 1.4 The critical constraint: every existing test passes, unchanged

Per the phase brief, this was treated as non-negotiable, not aspirational.

```
Before this phase's C1 changes: 236 tests passing
After:                          261 tests passing (25 new)
```

All 236 pre-existing tests pass **unchanged** — not one was edited to
accommodate the new behavior. This is a direct, mechanical consequence of
§1.1/§1.2's design: every existing test passes a `"yield"`-column curve,
which resolves to the exact original code path in both
`price_bond` and `key_rate_duration.py`. The 25 new tests
(`tests/test_bond_pricing.py`, `tests/test_key_rate_duration.py`,
`tests/test_zero_curve_impact.py`) cover the new zero-curve branch
specifically — including a direct confirmation that `price_bond`'s new
branch reproduces Part A's own self-consistency result
(`test_zero_basis_reprices_to_par_consistently_when_freq_matches_end_to_end`),
proving the two independent call paths (`models.bootstrap.
price_via_zero_curve` and `price_bond` itself) agree.

---

## 2. Quantifying the cost — the real numbers

`models.zero_curve_impact.compute_par_vs_zero_impact` reprices the
standard portfolio (`config/portfolio.json`) against the committed
snapshot curve (`prefer_live=False`, reproducible) both ways. Unlike
`price_portfolio` and the other `_portfolio` functions downstream of it
(which default to pricing each bond at its own `Bond.freq`, `docs/
phase_2b_documentation.md §1.3`), `compute_par_vs_zero_impact` keeps
`freq` a single required argument for the whole portfolio -- every bond
here must be discounted at the same frequency the zero curve was itself
bootstrapped with, or the par-vs-zero comparison would be internally
inconsistent (that module's own docstring has the full reasoning).

**Per bond:**

| Bond | Par price | Zero price | Price diff | Par duration | Zero duration | Duration diff | Par DV01 | Zero DV01 | DV01 diff |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| JGB_2Y | 98.5485 | 98.5439 | -0.47bp | 1.9678 | 1.9678 | -0.0000 | 0.0194 | 0.0194 | -0.01% |
| JGB_5Y | 96.5986 | 96.5275 | -7.36bp | 4.7778 | 4.7774 | -0.0005 | 0.0462 | 0.0461 | -0.08% |
| JGB_10Y | 92.3023 | 91.6987 | -65.40bp | 8.9162 | 8.9074 | -0.0088 | 0.0823 | 0.0817 | -0.75% |
| **JGB_20Y** | 91.2314 | 88.1177 | **-341.30bp** | 14.3880 | 14.2119 | -0.1761 | 0.1313 | 0.1252 | -4.60% |
| **JGB_30Y** | 94.1634 | 89.3891 | **-507.03bp** | 17.4325 | 16.9243 | -0.5081 | 0.1641 | 0.1513 | -7.84% |
| **JGB_40Y** | 98.9681 | 94.0930 | **-492.59bp** | 19.4411 | 18.8032 | -0.6380 | 0.1924 | 0.1769 | **-8.05%** |

**Portfolio-weighted totals:**

```
Price:     par = 94.884   zero = 93.047   diff = -1.837
Duration:  par = 10.197   zero = 10.028   diff = -0.169 years
DV01:      par = 0.09627  zero = 0.09172  diff = -0.00455 (-4.72%)
```

**The finding: small at the front end, dramatic at the long end.** The
2Y bond's price moves half a basis point; the 30Y bond's moves over
**500 basis points** — 5.1% of price, on a *single* bond, with the 40Y
close behind (493bp). This tracks the same mechanism Part A's own
coupon-effect check already quantified from a different angle
(`docs/phase_4_5a_documentation.md` §5): the further out a cash flow
sits, the more the curve's own directly-quoted rate at that maturity
diverges from a genuinely bootstrapped zero rate there — until the curve
flattens (25Y–40Y all ~4.1%), which is why 40Y doesn't exceed 30Y. On
an upward-sloping curve like this one, **the zero-curve basis prices
consistently below the par basis** — the direction is checked directly
(`test_zero_basis_prices_below_par_basis_for_this_portfolio`), not
assumed. This is exactly the kind of benchmarking figure the eventual
Phase 6 SR 11-7 report needs: not "the old approach was wrong," but a
quantified, reproducible measure of how wrong, and where.

**Per-tenor KRD, portfolio total row (aligned to the par curve's own
tenors):**

| Tenor | Par KRD | Zero KRD | Tenor | Par KRD | Zero KRD |
| --- | --- | --- | --- | --- | --- |
| 1Y | 0.0268 | 0.0276 | 9Y | 0.1365 | 0.1406 |
| 2Y | 0.3372 | 0.3386 | 10Y | 2.3596 | 2.3678 |
| 3Y | 0.0641 | 0.0661 | 15Y | 0.6247 | 0.6349 |
| 4Y | 0.0833 | 0.0858 | **20Y** | 2.0705 | 2.0303 |
| 5Y | 1.0136 | 1.0166 | **25Y** | 0.4212 | 0.4005 |
| 6Y | 0.1017 | 0.1052 | **30Y** | 1.7706 | 1.6727 |
| 7Y | 0.1147 | 0.1185 | **40Y** | 0.9463 | 0.8925 |
| 8Y | 0.1262 | 0.1302 |  |  |  |

The largest absolute per-tenor KRD swings sit at 30Y and 40Y, consistent
with the per-bond finding above.

### 2.1 Correction (2026-09-27): which question the DV01 gap answers

The −4.72% DV01 gap above is real, but it answers a narrower question
than this section originally implied. It compares par-basis DV01 with
zero-basis DV01 **for a shift in zero rates**. This project's risk
factors (Phase 4B's PCA) are moves in **par yields**, so the benchmark
that matters for its risk numbers is different: shift the par curve,
re-bootstrap the zero curve, and reprice on the zero basis.

| Portfolio DV01 (per 100 face) | Upward curve (snapshot) | Same curve, inverted |
| --- | --- | --- |
| A. Par basis, par shift (this project) | 0.09627 | 0.14290 |
| B. Zero basis, zero-rate shift (the table above) | 0.09172 | 0.14616 |
| C. Zero basis, par shift + re-bootstrap | 0.09749 | 0.13870 |
| **Par basis vs. C** | **−1.3%** | **+3.0%** |

Against the right benchmark (C), the par simplification's risk error is
**smaller** than −4.72% suggested, and on today's upward-sloping curve it
**understates** risk (per bond: 2Y −0.1%, 5Y −0.6%, 10Y −1.9%,
20Y −2.7%, 30Y −1.1%, 40Y +0.3%). Its sign depends on curve shape: on an
inverted curve it overstates. The price gaps above are unaffected — they
compare valuations, not risk.

The factor-level check went further: on the par basis, PC2 (slope)
exposure is ~45% smaller than on the consistent zero basis and PC3's
sign flips. That led to Special Phase A, which made zero-curve
discounting (bump par, re-bootstrap, reprice) the project-wide default:
[`special_phase_a_documentation.md`](special_phase_a_documentation.md).

---

## 3. C2: do NS's loadings resemble PCA's? A partial, honest answer

### 3.1 Method

`models.zero_curve_impact.compare_ns_shapes_to_pca` evaluates
Nelson-Siegel's own already-fitted basis functions — `1`, `f1(m, tau)`,
`f2(m, tau)`, using the exact `tau` `fit_nelson_siegel` found for today's
zero curve (Part B) — at PCA's own tenor grid (Phase 4B), normalizes each
to a unit vector (matching PCA's own normalization), and compares to
PCA's loading vectors via **cosine similarity** — the standard way to
compare two loading/eigenvector *shapes*. This is a different comparison
from `models.diebold_li.compare_to_pca`'s Pearson correlation of two
*time series*; here, both objects are static shapes over the tenor axis
at a single date, not something that moves day to day.

**Sign:** PCA's loading sign is mathematically arbitrary (fixed only by
`models.pca`'s own "largest-magnitude-positive" convention). NS's basis
functions have no such ambiguity — `f1(m, tau) = (1-exp(-m/tau))/(m/tau)`
is strictly positive for any real `m, tau > 0`, not a solver artifact.
Only PCA's side can flip sign arbitrarily, so the same
`sign_aligned_cosine_similarity = abs(raw_cosine_similarity)` treatment
used throughout this project (`docs/phase_4b_documentation.md` §1.3,
`docs/phase_4_5b_dl_documentation.md` §4) is applied here too.

### 3.2 The result

Against the committed snapshot curve and the default 2-year PCA window
(both `prefer_live=False`), Nelson-Siegel's fitted `tau = 50.0` years —
the top of its search range, so this fit is flagged as not converged
(`docs/phase_4_5b_documentation.md` §5; the live curve does the same):

| Beta | PCA component | Cosine similarity |
| --- | --- | --- |
| `beta0` (level) | PC1 (level) | **0.962** — strong |
| `beta1` (slope) | PC2 (slope) | **0.229** — weak |
| `beta2` (curvature) | PC3 (curvature) | **0.112** — essentially none |

**Reported as found, not smoothed into one story.** `beta0`'s shape is
trivially flat (a constant `1` at every maturity); PCA's PC1 turns out to
be reasonably close to flat too (checked separately, Phase 4C's own
finding that PC1's loadings stay in a fairly narrow band, elevated from
the belly through 40Y) — hence the strong match. `beta1` and `beta2` do
**not** resemble PC2/PC3 well under this tau, and the loadings comparison
chart (`outputs/ns_vs_pca_loadings.png`) makes the mismatch visually
obvious: NS's `beta1` shape declines gently and monotonically from 1Y to
40Y, never changing sign, while PC2 swings from clearly negative (short
end) to clearly positive (long end) — a genuine slope. NS's `beta2` shape
*rises* monotonically across the whole grid; PC3 instead dips negative
around 25Y between two positive wings — a real hump-and-trough shape
`beta2` never takes on at this tau.

### 3.3 Why — and the tau-dependence check that keeps this from overclaiming

The mismatch traces to a specific, checkable cause: **`tau = 50` years is
large relative to this project's 1-40Y tenor range**, so `f1`/`f2` barely
decay across it — `f1` only falls from 0.990 at 1Y to 0.688 at 40Y,
nowhere near the sign change a genuine slope factor needs, and `f2`'s
"hump" (centered near `m = tau`) never turns over within a 40-year grid
at all. This is a property of *this specific fitted tau*, not of
Nelson-Siegel's functional form in the abstract — checked directly, not
assumed:

- **Svensson's much smaller `tau1 = 2.94`** (Part B, same data; this fit
  does converge) produces a visibly steeper-decaying `beta1` shape and a
  correspondingly stronger — though still partial — match to PC2 (cosine
  0.522, more than double NS's).
- **`models.diebold_li`'s comparison**, structurally different (a fixed,
  much smaller `tau = 7.0` years, and a *time-series* correlation of
  day-to-day score movements rather than a static shape comparison),
  finds a much stronger slope correspondence — **0.955**
  (`docs/phase_4_5b_dl_documentation.md` §4).

Put together, these three results are not contradictory — they show that
**how "slope-like" or "curvature-like" a Nelson-Siegel component looks
depends heavily on tau**, and a tau chosen purely to minimize RMSE
against a single day's curve (Part B's cross-sectional fit) does not
automatically produce components that resemble PCA's empirically-derived
factor shapes. Neither model is being called "wrong": one imposes a
functional form and finds whatever tau best fits the data at hand; the
other extracts whatever direction the data moves in most, with no
functional-form assumption at all. Their level components resembling
each other closely is a genuine, reportable finding; their slope and
curvature components not resembling each other closely under NS's own
best-fit tau is an equally genuine, reportable finding — not a failure of
either method, and not evidence that either factor is "the truth" the
other should be validated against.

---

## 4. Charts

`models.zero_curve_impact.plot_par_zero_fitted_curve` saves the par
curve, the bootstrapped zero curve, and both parametric fits on one axis
to `outputs/par_zero_fitted_curve.png` — visually, the zero curve and
both fits sit consistently above the par curve from roughly 7Y onward,
directly illustrating §2's price/duration/DV01 findings.
`models.zero_curve_impact.plot_loadings_comparison` saves the three
paired (NS shape, sign-aligned PCA loading) panels to
`outputs/ns_vs_pca_loadings.png` — the visual counterpart to §3.2's
table, making the slope/curvature shape mismatch immediately legible.

---

## 5. Known limitations (for the SR 11-7 validation report)

**5.1 (Superseded by Special Phase A.) The zero-curve basis is opt-in, not default.** Every existing
function's default behavior is unchanged; a caller must deliberately pass
a `bootstrap_zero_curve()` result to get the more theoretically grounded
pricing. Nothing in this project's `__main__` blocks or default portfolio
runs uses it automatically.

**5.2 Inherits Part A's own coupon-effect caveat, now expressed as a
concrete P&L figure.** The zero curve's own limitation
(`docs/phase_4_5a_documentation.md` §1/§5 — MOF's curve is a fitted YTM
series, treated as a par curve) flows directly into every number in §2.
The ~500bp 30Y price gap is the difference between two approximations, not
between an approximation and ground truth.

**5.5 The −4.72% DV01 gap is not the risk error that matters here (§2.1).**
It measures sensitivity to zero-rate shifts; this project's factors are
par-yield moves. Against the consistent benchmark the error is −1.3%
(understating) on today's curve, and its sign flips on an inverted curve.

**5.3 The C2 loading comparison is a single cross-section, at one tau.**
§3.3's tau-dependence finding is itself evidence that this comparison
would look different on a different day (a different fitted tau) or a
different tau-selection method (Svensson, Diebold-Li). It should not be
read as a permanent verdict on whether Nelson-Siegel and PCA "agree."

**5.4 The per-tenor KRD/DV01 comparison (§2) depends on the alignment
choice (§1 of this doc; interpolating the zero curve onto the par
curve's tenors).** The same reconciliation-vs-intersection tradeoff Phase
4C already documented for a different grid pair
(`docs/phase_4c_documentation.md` §1.1) applies here too.

---

## 6. What would change this design

**Making the zero-curve basis the default**, if this project's use case
ever called for it, would be a one-line change at each call site (pass a
zero curve instead of a par curve) — no change to `price_bond`,
`key_rate_duration.py`, or `dv01.py` themselves, since the auto-detection
already supports both.

**Extending C2's comparison across multiple dates**, turning the static
shape comparison into a time series, is exactly what
`models.diebold_li` already does via a different (fixed-tau) route
(`docs/phase_4_5b_dl_documentation.md`) — not rebuilt here to avoid
duplicating that work.

**A Waggoner (1997) smoothing-spline benchmark**, per
`docs/phase_4_5b_dl_documentation.md` §6, would give C1's comparison a
third basis to benchmark against, not just par vs. zero — planned for
Phase 6, not built now.

---

## 7. Relationship to the fallback re-anchoring policy and earlier phases

This phase adds no new hardcoded market data of its own — every function
here computes from whatever `load_jgb_curve()` / `load_portfolio()` /
`load_jgb_curve_history()` return, inheriting those loaders'
re-anchoring policies rather than adding new ones. See §5 (this doc) and
the earlier-phase docs (`docs/phase_2b_documentation.md`,
`docs/phase_3a_documentation.md`, `docs/phase_3b_documentation.md`,
`docs/phase_4c_documentation.md`) for how each phase's own no-zero-curve
caveat was updated once this phase resolved it, in part.
