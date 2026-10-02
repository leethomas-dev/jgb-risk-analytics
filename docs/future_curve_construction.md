# Future curve construction — decision record

**Status:** accepted limitation, measured. Not a solved problem.
**Decided:** 2026-10-02, during Special Phase B
([`special_phase_b_documentation.md`](special_phase_b_documentation.md),
which holds the full method and test detail).
**For Phase 6:** this is the planned-remediation section for the curve.
It is written to be read on its own.

## In plain English

Every price and risk number in this project starts from the Ministry of
Finance's published JGB curve. The project reads that curve as if each
rate were the yield of a bond priced at exactly 100 (a "par curve"). It
isn't: MOF's curve runs through the yields of real bonds, and some of
them were issued years ago with coupons under 1%.

Checking the curve against real bond prices turned up something more
interesting than the size of that error. **MOF's curve has built-in
distortions at 15Y and 25Y.** At those two points it is built from old
low-coupon bonds, while its neighbours are built from new ones. The
"peak" at 25Y that this project's curve models struggled to fit is
mostly that distortion, not a feature of the market. Anyone using MOF's
curve inherits it.

The proper fix — building the curve from individual bond prices — is
possible, and was deliberately put off. The reason it can wait:
**the project's own portfolio is off by only −2 to +5bp, mostly under
2bp.** The factor results Phase 5 will use move by up to 7.9% (the slope
factor), against a 10% limit, with one known gap in that test that would
push the number up.

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

## 2. The headline finding: MOF's curve has artifacts at 15Y and 25Y

This is a property of **MOF's published curve**, not only of this
project's model.

MOF builds each grid year from specific bonds. Measured against JSDA's
reference prices for 2026-08-31 (§4), the bonds behind each grid year
look like this:

| Grid year | Bonds MOF uses | Their coupon | Their price | Coupon gap* |
| --- | --- | --- | --- | --- |
| 10Y | newest 10Y issue | 2.7% | ~98 | +0.5bp |
| **15Y** | **20Y-class issues from years ago** | **0.4–0.5%** | **~65** | **+16bp** |
| 20Y | newest 20Y issue | 3.7% | ~99 | +0.6bp |
| **25Y** | **30Y-class issues from years ago** | **0.7%** | **~47** | **+32bp** |
| 30Y | newest 30Y issue | 4.0% | ~98 | +0.4bp |
| 40Y | newest 40Y issue | 3.8% | ~94 | +0.8bp |

\* How far the bond's yield sits above what a par bond of the same
maturity would yield on the same curve — the coupon effect.

On a rising curve, a deep-discount bond yields more than a par bond of
the same maturity. So MOF's 25Y point sits about 32bp higher than a par
25Y yield would, while 20Y and 30Y sit at par. That's why MOF's curve
peaks at 25Y (4.10%, above 30Y's 4.09% on 2026-08-31). The same happens
at 15Y, at about half the size. Smaller versions appear at 6–9Y (+2 to
+3bp), where MOF uses older 10Y issues.

**The distortion is growing.** It roughly tripled over the last two
years as yields rose and the old bonds fell further below par
(quarterly averages, 2024-Q3 → 2026-Q3: 25Y 7.3 → 21.6bp; 15Y 3.2 →
12.9bp).

**What this overturns in earlier phases:**

- **Phase 4.5B §7** read Nelson-Siegel's and Svensson's +14 to +19bp
  miss at 25Y as a shape "neither model's smooth humps capture". Most of
  it is the artifact: the models were being blamed for failing to fit
  something that shouldn't be there. Out of sample, both miss 25Y by
  ~23bp (Special Phase B §2.3).
- **Phase 4.5B-DL §3** explained the poor Diebold-Li fits of April–August
  2026 as "a genuine curve regime… not a data problem" — a hump into 25Y.
  That is the period when the 25Y artifact was at its largest (~19–22bp),
  so the data-artifact explanation now has to be considered first.
- Any reading of 15Y or 25Y as rich or cheap against its neighbours.

---

## 3. How big the error is

### 3.1 The earlier bound

Before measurement the error could only be bounded, by pricing
hypothetical bonds off the bootstrapped curve (Phase 4.5A §5):

| Case | 1–3Y | to 10Y | 15Y | 20–40Y |
| --- | --- | --- | --- | --- |
| Zero-coupon bond | under 1bp | single digits | +19bp | **+28 to +45bp** (25Y: +45bp) |
| High-coupon bond (2× the yield) | under 1bp | single digits | −11bp | **−6 to −17bp** |

### 3.2 The measurement

All 294 fixed-coupon JGBs maturing in 1–40Y, priced off the project's
zero curve for 2026-08-31 and compared with JSDA's quotes:

- **MOF's curve fits the bonds closely** (median 0.85bp from each bond's
  own yield), so the pricing gap is almost entirely the coupon effect.
- **Near-par bonds are priced correctly:** all 66 bonds priced 98–102
  are within 0.55bp.
- **Low-coupon bonds are not:** the 111 bonds priced below 85 have a
  median gap of +13.8bp. By maturity bucket the average gap is under
  2bp to 10Y, +8 to +11bp at 10–20Y, and +16 to +25bp beyond 20Y, up to
  +33bp; up to ~3 points of price per 100.
- **The coupon drives it:** within every maturity bucket the gap tracks
  how far the coupon sits below the yield (correlation −0.79 to −0.97).

**Closer to the extreme than expected.** At 25Y the measured gap is
**+32bp against a +45bp bound** — and the bound assumes a zero-coupon
bond, while the bonds behind MOF's 25Y point carry 0.7% coupons. At 15Y
it is +16bp against +19bp. The working assumption before measurement
was that the real error would sit well inside the bound. At the two
tenors that matter, it doesn't.

The zero curve itself is roughly 25bp too high near 25Y and ~15bp near
15Y (a rough reading from the deep-discount bonds, not a fitted
correction).

---

## 4. What makes deferring defensible

**The project's own portfolio is off by −2 to +5bp, mostly under 2bp.**

Real JGBs with coupons and maturities like the portfolio's six bonds
(2–40Y, coupons 1.0–3.8%) show these gaps against the project's zero
curve. The portfolio's bonds sit near par and mature at grid years MOF
builds from new issues, so the artifacts at 15Y and 25Y barely touch
them.

This is a property of **this portfolio**, not of the method. A portfolio
holding old low-coupon long-end issues would be mispriced by up to ~3
points per 100 (§3.2), and the decision would not hold for it (§10).

---

## 5. Effect on the factor results Phase 5 will use

The 2-year curve history was corrected for the coupon effect at the
grid years MOF builds from old issues, and PCA, factor exposure (4C) and
daily factor P&L (4D) were re-run (method: Special Phase B §4.2).

| Check | Result | Limit set before the test |
| --- | --- | --- |
| Factor shapes (cosine similarity) | ≥ 0.997 | 0.99 |
| Share of variance per factor | moves ≤ 0.5 points | 2 points |
| +1 std P&L: PC1 / PC2 / PC3 | −0.2% / **+7.9%** / −8.4% | 10% |
| Daily factor P&L | changes ≤ 2.4% of a typical day's P&L | 10% |

**PC2 (slope): 7.9% measured, with a known omission that pushes the same
way.** The test corrects only the smooth, curve-driven part of the
error. It leaves out the jumps at 15Y and 25Y on the days MOF switches to
a different issue. Correcting those too would mean changing the history
further at the same two tenors that drive the 7.9% now, so it would very
likely move PC2 further from the original. How far was not measured.

PC3's −8.4% is a large share of a very small number (under 0.01 per 100
face). PC1 and the daily attribution are well clear of their limits.

The limits are judgement calls, set before the test. A 5% exposure limit
would already flag PC2.

---

## 6. A finding: the "safe" channel was the exposed one

**The assumption.** PCA runs on daily changes in yields. A steady error
cancels out of a change, so the PCA should be insulated from the par
error. Repricing the portfolio on the curve's *level* (factor exposure,
factor P&L) should be where the error shows. The Special Phase B plan
was built on that assumption.

**What the test found — the opposite.** Splitting the exposure change by
what was corrected:

| | PCA only corrected | Today's curve only corrected | Both |
| --- | --- | --- | --- |
| PC1 | −0.7% | +0.5% | −0.2% |
| PC2 | **+6.9%** | +1.0% | +7.9% |
| PC3 | **−9.8%** | +1.4% | −8.4% |

The level channel is about 1%. The PCA channel is about 7–10%.

**Why.** The error isn't a steady offset. It's the coupon effect of old
bonds, and that grows when yields rise and shrinks when they fall: its
daily change moves with the 25Y yield's daily change (correlation 0.8).
So it doesn't cancel in differences; it changes how much 15Y and 25Y
move within each factor, which reshapes the factors slightly. The level
channel is small only because this portfolio's bonds are near par
(§4).

**Why it matters.** Phase 5 consumes the factors. The intuition "PCA is
on changes, so a level error doesn't matter" is reasonable, and was
wrong here. Any error that scales with the level of rates reaches the
factors.

### 6.1 The first run of this test was wrong, and how that was caught

The first run held every grid year's coupon fixed at its 2026-08-31
value — including the newest issues at 20Y, 30Y and 40Y (3.7–4.0%) —
across the whole 2024–2026 window. It showed PC2 at **+11.4%**, over the
limit.

Its own output showed the flaw: it applied an average correction of
about **−5bp at 20Y, 30Y and 40Y**. Those grid years are built from new
issues, which trade near par whenever they're the newest. Holding a 2026
coupon fixed back to 2024, when yields were far lower, turned them into
premium bonds that never existed and invented a correction for them. The
fix treats grid years built from new issues as par. A second, smaller
fix made the correction run to convergence (0.01bp) instead of a fixed
six steps; that moved PC2 by ~0.1 points.

Both fixes came after seeing a result over the limit. The reasoning for
the first stands on its own — the −5bp correction at near-par grid years
can't be right — but a reviewer should know the order things happened
in.

---

## 7. The decision

**The par simplification stays for now.** Reasons:

- **The portfolio is barely affected** (§4): −2 to +5bp, mostly under
  2bp.
- **Project priorities.** This is a portfolio project on a job-search
  timeline. Phases 5 (VaR/ES), 5.5, 6 (the SR 11-7 validation report)
  and 7 are unbuilt, and the app isn't deployed. The validation report
  is the most differentiating piece and doesn't exist yet.
- **A measured limitation with a checked remediation path is a
  legitimate validation position** — it's what a validation report is
  for. An unmeasured one wouldn't be; this one is now measured.
- **The remaining fix depends on external data** whose terms forbid
  redistribution and whose rate limits make a long daily history
  impractical (§8, §9).

**What this does not claim:**

- The zero curve is not right. It misprices old low-coupon bonds by up
  to ~3 points per 100.
- MOF's curve, and therefore the project's, is distorted at 15Y and 25Y
  by 16bp and 32bp (2026-08-31), and growing.
- The slope factor's exposure carries roughly 8% uncertainty from this,
  with a known omission pushing it higher.

Phase 6 should state all three.

---

## 8. Data handling

- **JSDA's terms forbid reuse or copying without permission**
  ([jsda.or.jp/menseki](https://www.jsda.or.jp/menseki/)). They don't
  name the `market.jsda.or.jp` data site specifically; the project
  treats them as applying.
- **Raw JSDA files are never committed.** They live in `data/jsda/`,
  which is gitignored, and the code never downloads them. Only derived
  numbers (gaps, aggregates, coupons, which are public issue terms) are
  committed, in code constants and these docs.
- **One test skips on a fresh clone, by design.**
  `tests/test_par_error_check.py::test_measured_par_error_on_real_jsda_quotes`
  needs the real file and reports *skipped*, not failed, without it.
  Every other test, including all of item 4's, runs on committed data.
  To run it: download
  `https://market.jsda.or.jp/shijyo/saiken/baibai/baisanchi/files/2026/S260901.csv`
  (the 2026-08-31 quotes, §9.1) into `data/jsda/`.
- **Reproducing the factor results** needs no JSDA file:
  `python -m models.par_error_factor_impact` (about two minutes).

---

## 9. The remediation path: fit the curve to JSDA bond prices

### 9.1 Feasibility

JSDA publishes daily reference prices for every JGB (公社債店頭売買参考統計値).
`data/jsda_reference_loader.py` already reads the file.

| Item | Status |
| --- | --- |
| URL pattern `.../baisanchi/files/<YYYY>/S<YYMMDD>.csv` | **Checked** — S260831 and S260901 downloaded |
| Coupons included | **Checked** — with maturity, coupon months, average price, compound and simple yields |
| Column meanings | **Checked** — simple yield recomputed from price matches JSDA's to 0.09bp; this code's compound yield matches JSDA's to 0.19bp (medians) |
| Dated the next business day | **Checked** — S260901 matches MOF's 08-31 curve (0.2bp average); S260831 matches 08-28 (0.4bp) |
| Headline quotes are simple yield, not price | **Not a problem** — the file also carries the average price; work from that |
| Archives back to 2002 | Yearly archive pages, checked by the project owner; not re-checked in Special Phase B |
| Rate limits | **Checked** — HTTP 429 on the format PDF, header file, index and terms pages within minutes. One file a day is fine; a multi-year daily history is impractical. |
| Official format document (`baisan_csv.pdf`, `csvheaderbaisan.xlsx`) | Not read — rate-limited both times; column meanings were checked from the data instead |

### 9.2 Design questions, kept for when this is picked up

Raised before this decision and left open on purpose. What Special
Phase B learned is noted against each.

- **A. Order of work.** Fit **today's** curve to bond prices first; leave
  the history on MOF data. Measuring the error is already done (§3).
- **B. Fitting method.** Nelson-Siegel/Svensson fitted to prices
  (minimising duration-weighted price errors, the Gürkaynak-Sack-Wright
  approach), a bootstrap from selected issues, or a smoothing spline
  (Waggoner 1997 is already the planned Phase 6 benchmark). Learned:
  Svensson extrapolates badly (missed 40Y by ~99bp with 40Y held out), so
  the long end needs care. The project has no scipy by design; a
  price-based fit needs an iterative solver.
- **C. Which bonds to use.** Exclude very short, illiquid, and BoJ- or
  futures-distorted issues; choose average vs. median quotes. Clean/dirty
  pricing, Actual/365 accrued interest and T+1 settlement already work
  (`models/par_error_check.py`).
- **D. History.** PCA would stay on MOF data. A cheaper middle step now
  exists: `coupon_corrected_history()` (Special Phase B §4) adjusts MOF's
  history for the coupon effect using only issue coupons, no JSDA
  history. To be more than a test input it needs the coupons of the
  issues MOF used **on each date**, which would also capture the
  issue-switch jumps §5 leaves out.
- **E. Fallback.** Today's method (MOF + par bootstrap), with its
  measured error, when JSDA data isn't available.
- **F. Docs and figures to update afterwards.** 4.5A, 4.5B (residuals and
  §7), 4.5B-DL (§3 poor-fit regime, §4), 4C, 4D, Special Phase A, and the
  app.
- **G. What "accurate" means.** A zero curve is fitted, not observed
  (JGB STRIPS barely trade). Set a target on pricing error across the
  included bonds — e.g. mean gap under 2bp, with no pattern by coupon —
  against today's mean 6.5bp, median 2.0bp, maximum 35.5bp.

---

## 10. Related known issue: the Diebold-Li vs. PCA basis mismatch

Diebold-Li ([`phase_4_5b_dl_documentation.md`](phase_4_5b_dl_documentation.md) §4)
fits the bootstrapped **zero** curve, while Phase 4B's PCA runs on MOF's
**par-treated** yields (left on par deliberately by Special Phase A). The
weak level correlation between them (**0.269**; slope 0.955, curvature
0.504) may partly come from that mismatch. Not fixed: running Diebold-Li
on the same par history as the PCA would give a like-for-like comparison
and is a reasonable future step.

---

## 11. Monitoring, and when to revisit

**Monitors.**

- **Special Phase A's factor-basis monitor stays**
  (`compare_factor_exposure_bases()`, Special Phase A §7). It compares
  factor exposures on par vs. zero **discounting** — a different
  question from this one, still worth watching.
- **The check for this error** is
  `models.par_error_factor_impact.compare_factor_results()`, with its
  pre-set limits (`MATERIAL_*`) as alert thresholds. About 15 seconds
  with a short attribution window. Phase 6 decides whether it runs on
  every data refresh.

**Revisit this decision if any of these happens:**

1. **The portfolio comes to hold older low-coupon long-end issues** —
   anything trading well below par beyond ~10Y. The measured error
   depends on what's held: near-par bonds are priced within ~1bp,
   deep-discount ones up to ~3 points per 100 off.
2. **Anything uses the 15Y or 25Y region of the curve** — holding bonds
   maturing near those points, reading rates, residuals or rich/cheap
   signals there, or interpreting a curve shape that peaks there.
3. **`compare_factor_results()` flags anything material**, or PC2's
   change passes 10%. The artifact tripled in two years; if yields keep
   rising, the PC2 effect grows with it.
4. **Phase 6 sets a factor-exposure tolerance tighter than ~8%.** The
   slope factor's par-error uncertainty would then be out of tolerance.
5. **JSDA access changes** — a licence, an API, or terms that permit
   redistribution.
6. **MOF changes its curve methodology** or the bonds it selects.
