# Future curve construction — decision record

**Status:** accepted limitation, measured. Not a solved problem.
**Decided:** 2026-10-02, during Special Phase B
([`special_phase_b_documentation.md`](special_phase_b_documentation.md)).

## In plain English

Every price and risk number in this project starts from MOF's published
JGB curve. The project reads that curve as if each rate were the yield of
a bond priced at exactly 100 (a "par curve"). It isn't. MOF's curve runs
through the yields of real bonds, and many of those bonds were issued
years ago with very low coupons.

The proper fix is to build the curve directly from individual bond
prices. That fix is possible: the data exists, and the main unknown
(whether coupons are included) has been checked. It was deliberately
**not** done now. Instead, the error was measured:

- Bonds priced near 100 are priced correctly. Old low-coupon bonds are
  off by up to ~32bp of yield (~3 points of price), mostly around 15Y and
  25Y.
- The project's own portfolio is off by 5bp or less.
- The risk factors Phase 5 will use shift by up to ~8% (the slope
  factor), under the limits set before the test.

This page records why the shortcut stays for now, how big its error is,
how to remove it later, and what should trigger that.

---

## 1. The problem

**What MOF publishes.** Per MOF's own methodology
([`phase_4_5a_documentation.md`](phase_4_5a_documentation.md) §1), MOF
picks real benchmark JGBs near each grid year — each class's newest
issue, plus the issues maturing nearest the grid year on each side —
takes their market yields to maturity, and draws a cubic spline through
them. No step prices anything at 100.

**What the project does with it.** Phase 4.5A treats those yields as par
yields and bootstraps a zero curve from them. Since Special Phase A,
every price and risk figure discounts on that zero curve.

**Why no curve model can fix this from MOF's data alone.** A bond's
yield depends on its coupon: on a rising curve, a low-coupon bond yields
more than a par bond of the same maturity. MOF publishes one yield per
grid year and no coupons. Every method that starts from MOF's numbers —
bootstrap, Nelson-Siegel, Svensson, Diebold-Li, a spline — has to assume
something about the missing coupons. Par is the usual assumption. Only
bond-level data removes the need for one.

---

## 2. How big the error is

### 2.1 The earlier bound (Phase 4.5A §5)

Before measurement, the error could only be bounded, by pricing
hypothetical bonds off the bootstrapped curve:

| Case | 1–3Y | to 10Y | 20–40Y |
| --- | --- | --- | --- |
| Zero-coupon bond | under 1bp | single digits | **+28 to +45bp** |
| High-coupon bond (2× the yield) | under 1bp | single digits | **−6 to −17bp** |

Old low-coupon JGBs push the real error toward the zero-coupon side.

### 2.2 The measurement (Special Phase B §3)

All 294 fixed-coupon JGBs maturing in 1–40Y, priced off the zero curve
for 2026-08-31 and compared with JSDA's reference quotes for that day:

- **MOF's curve fits the bonds closely** (median 0.85bp from each bond's
  own yield), so the gap is almost entirely the coupon effect.
- **Near-par bonds are priced correctly:** all 66 bonds priced 98–102
  are within 0.55bp.
- **Low-coupon bonds are not:** the 111 bonds priced below 85 have a
  median gap of +13.8bp. By maturity bucket, the average gap is under
  2bp to 10Y, +8 to +11bp at 10–20Y, and +16 to +25bp beyond 20Y (up to
  +33bp).
- **The coupon drives it:** within every maturity bucket the gap tracks
  how far the coupon sits below the yield (correlation −0.79 to −0.97).
- **The grid years MOF builds from old issues carry it:** +16bp at 15Y
  (issues priced ~65) and +32bp at 25Y (issues priced ~47). The grid
  years built from new issues (2, 5, 10, 20, 30, 40Y) are within 1bp.
- **MOF's 25Y "peak" is largely this artifact.** The 25Y point comes
  from ~0.7%-coupon bonds; 20Y and 30Y come from near-par bonds. Both
  Nelson-Siegel and Svensson miss 25Y by ~23bp out of sample (Special
  Phase B §2.3), and this is why.
- **The zero curve itself is roughly 25bp too high near 25Y and ~15bp
  near 15Y** (a rough reading from the deep-discount bonds, not a fitted
  correction).
- **The project's portfolio is barely affected:** real JGBs with similar
  coupons and maturities are off by −2.0 to +4.7bp, most under 2bp.

**One day only** (2026-08-31), and not a fixed number: the implied
correction at 25Y grew from ~7bp to ~22bp over the last two years
(quarterly averages, 2024-Q3 7.3bp → 2026-Q3 21.6bp; 15Y 3.2 → 12.9bp)
as yields rose and the old bonds fell further below par.

### 2.3 Effect on factor results (Special Phase B §4)

The 2-year curve history was corrected for the coupon effect at the grid
years MOF builds from old issues, and PCA, factor exposure and daily
factor P&L were re-run:

| Check | Result | Limit set before the test |
| --- | --- | --- |
| Factor shapes (cosine similarity) | ≥ 0.997 | 0.99 |
| Share of variance per factor | moves ≤ 0.5 points | 2 points |
| +1 std P&L: PC1 / PC2 / PC3 | −0.2% / **+7.9%** / −8.4% | 10% |
| Daily factor P&L | changes ≤ 2.4% of a typical day's P&L | 10% |

**Not material by the pre-set limits.** The slope factor (PC2) is the
closest, at +7.9%; PC3's −8.4% is a large share of a very small number.

**Which channel matters — the opposite of what was expected.** The
working assumption was that change-based results (the PCA) would be safe,
since a steady error cancels out of daily changes, and that repricing on
the curve's level would carry the error. The measurement says otherwise
for this portfolio:

- **Level channel ≤ 1.4%.** The portfolio's bonds are near par and
  mature at grid years built from new issues.
- **PCA channel up to ~7–10%.** The correction isn't steady: it moves
  with the yields (correlation 0.8 at 25Y), so it changes how much 15Y
  and 25Y move within each factor.

Phase 5 consumes the factors, so the PCA channel is the one to watch.

**Limitations of this test:** it covers only the smooth, curve-driven
part of the error, not the jumps when MOF switches to a different issue
at 15Y or 25Y; the old issues' coupons are held fixed over the window;
one portfolio; and the limits are judgement calls — a 5% limit would
flag PC2. A first run of the test showed PC2 at +11.4% before a design
flaw was fixed (Special Phase B §4.6 records both runs and the order
they happened in).

---

## 3. The decision

**The par simplification stays for now.** Reasons:

- **Project priorities.** This is a portfolio project on a job-search
  timeline. Phases 5 (VaR/ES), 5.5, 6 (the SR 11-7 validation report)
  and 7 are unbuilt, and the app isn't deployed. The validation report
  is the most differentiating piece of the project and doesn't exist
  yet.
- **A measured limitation with a checked remediation path is a
  legitimate validation position** — it's what a model validation report
  is for. An unmeasured one wouldn't be; this one is now measured.
- **The measured effect on what the project reports is small:** ≤5bp on
  the portfolio's bonds, and factor results within the pre-set limits.
- **The remaining risk is a dependency on external data** whose terms
  forbid redistribution and whose rate limits make a long daily history
  impractical (§4).

**What this does not claim.** The zero curve is not right. It misprices
old low-coupon bonds by up to ~3 points per 100, its shape is distorted
around 15Y and 25Y, and the slope factor's exposure carries roughly 8%
of uncertainty from this. Phase 6 should state all three.

---

## 4. The remediation path: fit the curve to JSDA bond prices

### 4.1 The data, and what's been checked

JSDA publishes daily reference prices for every JGB (公社債店頭売買参考統計値):
`https://market.jsda.or.jp/shijyo/saiken/baibai/baisanchi/files/<YYYY>/S<YYMMDD>.csv`.
`data/jsda_reference_loader.py` already reads it.

| Item | Status |
| --- | --- |
| URL pattern works | **Checked** (S260831, S260901 downloaded) |
| Coupons included | **Checked** — with maturity, coupon months, average price, compound and simple yields |
| Column meanings | **Checked** — simple yield recomputed from price matches to 0.09bp; this code's compound yield matches JSDA's to 0.19bp (medians) |
| Dated the next business day | **Checked** — S260901 matches MOF's 08-31 curve (0.2bp); S260831 matches 08-28 (0.4bp) |
| Quotes mostly simple yield, not price | **Not a problem** — the file carries the average price; work from that |
| Rate limits | **Checked** — HTTP 429 on the format PDF, header file, index and terms pages within minutes |
| Archives back to 2002 | Reported by earlier research (Special Phase A §9); not checked here |
| Official format document (`baisan_csv.pdf`, `csvheaderbaisan.xlsx`) | Not read — rate-limited both times |
| Terms of use | JSDA's site terms ([jsda.or.jp/menseki](https://www.jsda.or.jp/menseki/)) forbid reuse or copying without permission; they don't name `market.jsda.or.jp`, and are treated as applying. **Raw files are never committed** (`data/jsda/` is gitignored); only derived results are. |

### 4.2 The design questions, kept for when this is picked up

These were raised before this decision and are left open on purpose.
What Special Phase B learned is noted against each.

- **A. Order of work.** Proposed: fit **today's** curve to bond prices
  first; leave the history on MOF data. The measurement step is already
  done (§2.2).
- **B. Fitting method.** Nelson-Siegel/Svensson fitted to prices
  (minimising duration-weighted price errors, the Gürkaynak-Sack-Wright
  approach), a bootstrap from selected issues, or a smoothing spline
  (Waggoner 1997 is already the planned Phase 6 benchmark). Learned:
  Svensson extrapolates badly past the data (it missed 40Y by ~99bp when
  40Y was held out, §2.3 of Special Phase B), so the fit needs a
  constraint or care at the long end. The project has no scipy, by
  design; a price-based fit would need an iterative solver.
- **C. Which bonds to use.** Exclude very short issues, illiquid ones,
  and ones distorted by BoJ holdings or bond-futures delivery; decide
  between average and median quotes. Clean/dirty pricing and Actual/365
  accrued interest already work (`models/par_error_check.py`, 4.6A); JGB
  settlement is T+1.
- **D. History.** Rate limits make a multi-year bond-level history
  impractical, so PCA would stay on MOF data. A cheaper middle step now
  exists: `coupon_corrected_history()` (Special Phase B §4) adjusts
  MOF's history for the coupon effect using only issue coupons, no JSDA
  history. It needs the coupons of the issues MOF used **on each date**,
  rather than today's, to be more than a test input.
- **E. Fallback.** Today's method (MOF + par bootstrap), with its
  measured error, when JSDA data isn't available.
- **F. Docs and figures to update afterwards.** 4.5A, 4.5B (residuals
  and the 25Y finding), 4.5B-DL, 4C, 4D, Special Phase A, and the app.
- **G. What "accurate" means.** A zero curve is fitted, not observed
  (JGB STRIPS barely trade). A target in pricing error on the included
  bonds — e.g. mean gap under 2bp with no pattern by coupon — compared
  against today's mean 6.5bp, median 2.0bp, maximum 35.5bp.

---

## 5. Related known issue: the Diebold-Li vs. PCA basis mismatch

Diebold-Li ([`phase_4_5b_dl_documentation.md`](phase_4_5b_dl_documentation.md) §4)
fits the bootstrapped **zero** curve, while Phase 4B's PCA runs on MOF's
**par-treated** yields (left on par deliberately by Special Phase A). The
weak level correlation between them (**0.269**; slope is 0.955,
curvature 0.504) may partly come from that mismatch. Not fixed: running
Diebold-Li on the same par history as the PCA would give a like-for-like
comparison and is a reasonable future step.

---

## 6. Monitoring, and when to revisit

**What already exists, and what it doesn't cover.** Special Phase A's
factor monitor (`compare_factor_exposure_bases()`, Special Phase A §7)
compares factor exposures on par vs. zero **discounting**. It does
**not** watch the error recorded here. The check for this error is
`models.par_error_factor_impact.compare_factor_results()`; its pre-set
limits (`MATERIAL_*`) are the natural alert thresholds. Phase 6 should
decide whether to run it on every data refresh (about 15 seconds with a
short attribution window).

**Revisit this decision if any of these happens:**

1. **`compare_factor_results()` flags anything material** on a refresh.
   The 25Y correction roughly tripled over two years (§2.2); if that
   continues, the PC2 effect will grow.
2. **Phase 6 sets a tolerance on factor exposure tighter than ~8%.**
   The slope factor's par-error uncertainty would then be out of
   tolerance.
3. **The portfolio changes to real issues (Phase 8)**, particularly old
   low-coupon bonds or bonds maturing near 15Y or 25Y — the level channel
   would no longer be small.
4. **A use case needs reliable rates or residuals at 15Y or 25Y**, e.g.
   rich/cheap analysis or showing the fitted curve's residuals as a
   finding. Today those points carry the artifact.
5. **JSDA access changes** — a licence, a published API, or terms that
   permit redistribution.
6. **MOF changes its curve methodology.**
