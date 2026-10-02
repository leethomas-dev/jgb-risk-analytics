# Special Phase B Documentation — measuring and documenting the par-curve simplification

## In plain English

The project reads MOF's published curve as if every rate on it were the
yield of a bond priced at exactly 100 (a "par curve"). It isn't: MOF's
curve runs through the yields of real bonds whose coupons can be far from
today's rates. Rebuilding the curve from individual bond prices would fix
that, but it was deliberately put off (see
[`future_curve_construction.md`](future_curve_construction.md) for the
decision and the remediation path). This phase does the cheaper, honest
thing instead: it corrects claims the project was making too strongly,
checks a model comparison properly, and measures how much the par
simplification actually matters.

Done so far:

1. **The dashboard no longer calls PC1 a parallel shift.** It now
   describes PC1 from its real loadings, e.g. "every tenor moves the same
   way, but not equally: 20Y moves ~4.2x as much as 1Y".
2. **Svensson's win over Nelson-Siegel was re-tested on tenors it never
   saw.** Svensson still predicts the middle of the curve better, but
   much less decisively than the in-sample numbers suggested, and it
   extrapolates badly at 40Y.
3. **The par error was measured against real bond prices.** Bonds priced
   near 100 are priced correctly (under 1bp). Old low-coupon bonds are
   not: up to ~32bp of yield (~3 points of price) at 25Y. Bonds like the
   project's own portfolio are off by 5bp or less. The 25Y "peak" in
   MOF's curve turns out to come from those low-coupon bonds.

---

## Technical details

**Files:** `models/pca.py` (`describe_level_shape()`,
`PARALLEL_SHIFT_MAX_RATIO`), `app.py` (sections 5 and 7 captions),
`models/curve_fitting.py` (`leave_one_tenor_out()`),
`data/jsda_reference_loader.py` (`load_jsda_jgbs()`, new),
`models/par_error_check.py` (`measure_par_error()`,
`mof_selected_issues()`, new).

**Tests:** 386 → 401, all passing (4 in `tests/test_pca.py`, 3 in
`tests/test_curve_fitting.py`, 8 in `tests/test_par_error_check.py` — one
of which needs the hand-downloaded JSDA file and is skipped without it).

---

## 1. PC1 is described from its loadings, not called a parallel shift

**The wrong claim.** Section 5's caption said PC1 "typically moves every
tenor the same direction (a parallel shift)", and section 7's said a PC1
spike meant "the whole curve shifted together". The loadings don't
support "parallel": on the default 2-year window, PC1 is 0.08 at 1Y and
0.28–0.32 from 7Y out (4C §2). The 5- and 10-year windows look the same.

**"Level" stays as the name.** Every PC1 loading has the same sign, and
"level" is the standard industry name for that shape. Only the
"parallel" reading was wrong.

**The fix.** `describe_level_shape(pc1_loadings)` builds the wording each
time the app runs, so it stays right if the window changes:

- all loadings the same sign, largest more than 1.25x the smallest →
  "every tenor moves the same way, but not equally: 20Y moves ~4.2x as
  much as 1Y" (the current output);
- within 1.25x → "close to a parallel shift";
- mixed signs → "tenors do not all move the same way in this window",
  rather than claiming a level move.

The 1.25 threshold (`PARALLEL_SHIFT_MAX_RATIO`) is a wording choice, not
a statistical test. PC2 and PC3 captions are unchanged ("typically…").

---

## 2. Nelson-Siegel vs. Svensson: the nesting caveat and an out-of-sample test

### 2.1 Why the in-sample comparison proves little

Svensson is Nelson-Siegel plus one extra hump term. Setting that term's
weight (`beta3`) to zero gives back Nelson-Siegel exactly, for any
`tau2 > tau1`. So Svensson can almost always fit at least as well as
Nelson-Siegel on the same points, and its lower in-sample RMSE
(4.27bp vs. 6.68bp, 4.5B §6) was close to guaranteed.

"Almost", because the grid search requires `tau2 > tau1` within the same
`[0.05, 50]` bounds. Nelson-Siegel's own best fit on the snapshot sits at
`tau = 50`, the upper bound, where no larger `tau2` exists; Svensson can
only get very close to it, not reach it exactly.

Two more reasons the in-sample number is weak evidence: only 15 of the
~80 fit points are real MOF quotes (the rest are Part A's interpolation,
4.5B §3), and nothing in 4.5B penalised Svensson's two extra parameters.

### 2.2 The test: leave one tenor out

`leave_one_tenor_out(par_curve)` asks which model better predicts a real
MOF tenor it never saw. For each of the 15 tenors:

1. drop that tenor from the par curve,
2. bootstrap the remaining 14,
3. fit both models,
4. compare each model's rate at the dropped tenor with the full curve's
   zero rate there (residual = target − model, the same sign as
   `residuals_bp`).

**The tenor is dropped before bootstrapping, not after.** Dropping it
afterwards would leak: the bootstrapped points either side are built
from it. `test_loto_held_out_tenor_never_reaches_its_own_fold` checks
this — moving the held-out 10Y quote by 50bp leaves that fold's
prediction unchanged. A leaky version moves it by ~25bp, so the test
would catch the mistake.

The two end tenors (1Y, 40Y) are flagged `edge`: predicting them is
extrapolation, not interpolation, and they're reported separately.

### 2.3 Result (snapshot curve, 2026-08-31)

| Tenor | NS residual | Svensson residual |
| --- | --- | --- |
| 1Y (edge) | +0.58bp | −21.86bp |
| 2Y | +9.78bp | +1.69bp |
| 3Y | +2.87bp | −2.01bp |
| 4Y | +2.65bp | +2.51bp |
| 5Y | −1.23bp | +2.08bp |
| 6Y | −6.56bp | −1.48bp |
| 7Y | −7.90bp | −0.90bp |
| 8Y | −6.40bp | +1.99bp |
| 9Y | −7.53bp | +0.46bp |
| 10Y | −5.74bp | +2.72bp |
| 15Y | +3.44bp | +8.45bp |
| 20Y | +0.25bp | −3.28bp |
| **25Y** | **+23.75bp** | **+22.53bp** |
| 30Y | −2.05bp | −4.96bp |
| 40Y (edge) | −32.18bp | **+98.87bp** |

| Summary | NS | Svensson |
| --- | --- | --- |
| RMSE, 13 interior tenors | 8.45bp | 7.05bp |
| RMSE, interior excluding 25Y | 5.51bp | 3.40bp |
| Interior tenors where it's closer | 4 | 9 |
| Converged, all 15 folds | 0 of 15 | 15 of 15 |

**What this shows:**

- **Svensson is better inside the curve, but modestly.** It's closer on
  9 of 13 interior tenors, mostly the belly (6–10Y) — the same place its
  in-sample win came from (4.5B §7). Out of sample the RMSE gap is
  8.45 vs. 7.05bp, not the in-sample 6.68 vs. 4.27bp.
- **Svensson extrapolates badly.** With 40Y held out, it misses 40Y by
  ~99bp; at 1Y by ~22bp. Its second hump fits the data it has and then
  turns away. Nelson-Siegel, flatter by construction, misses 40Y by 32bp
  and 1Y by under 1bp. Neither model should be used beyond the quoted
  range.
- **25Y can't be predicted from its neighbours by either model** (~23bp
  miss for both). That's consistent with the in-sample finding (4.5B §7):
  the curve peaks at 25Y, a kink no smooth curve makes. Whether that kink
  is real or comes from MOF's spline and the par simplification is a
  question for item 3.
- **Nelson-Siegel never converges** (tau lands on the 50-year bound in
  every fold), so its parameters remain uninterpretable even where its
  predictions are fine.

**Caveat:** the targets are Part A's zero rates, which carry the par
simplification. This tests how well each model predicts the curve's
*shape*, not how close either is to a true zero curve.

**Bottom line for 4.5B:** the dashboard's rule — prefer a converged fit —
still picks Svensson, and that's the right call for describing the 1–30Y
shape. "Svensson fits better" should be stated as "Svensson interpolates
the belly better; neither model handles 25Y; Svensson must not be
extrapolated."

---

## 3. Measuring the par error against real bond prices

### 3.1 The test

If the bootstrapped zero curve were right, it would price the real bonds
MOF built its curve from at their market prices. So every fixed-coupon
JGB maturing in 1–40Y (294 bonds) is priced off the zero curve for
2026-08-31, using its own coupon and real coupon dates, and compared
with JSDA's reference quote for the same day.

Each gap (model yield minus market yield, in bp) is split in two:

- **off-curve:** MOF's curve at the bond's maturity minus the bond's own
  market yield. How far the bond sits from MOF's smooth curve — not
  caused by the par assumption.
- **coupon effect:** the rest. What treating MOF's curve as par does to a
  bond with this coupon.

Both yields come from the same yield function, so convention differences
cancel.

### 3.2 The data, and checks on it

- **Source:** JSDA's daily reference prices (公社債店頭売買参考統計値),
  `market.jsda.or.jp/shijyo/saiken/baibai/baisanchi/files/<YYYY>/S<YYMMDD>.csv`.
  Each row has the issue, maturity, coupon, coupon months, average
  price, and compound and simple yields.
- **Not committed.** JSDA's terms forbid reuse or copying without
  permission ([jsda.or.jp/menseki](https://www.jsda.or.jp/menseki/)). The
  file is downloaded by hand into `data/jsda/` (gitignored); only derived
  results are recorded here.
- **Dated the next business day — confirmed.** `S260901.csv` sits 0.2bp
  from MOF's 08-31 curve on average; `S260831.csv` sits 1.0bp from 08-31
  but 0.4bp from 08-28. So `S260901` holds the 08-31 quotes.
- **Column meanings checked, not assumed.** Recomputing simple yield from
  price and coupon matches JSDA's column to 0.09bp (median). This code's
  compound yield matches JSDA's to 0.19bp (median, 0.36bp at the 95th
  percentile), so the price-to-yield conventions agree.
- **Prices, not simple yields, are used.** JSDA's headline figure for
  ordinary JGBs is the simple yield, but the file also carries the
  average price; the check works from the price directly.
- **Settlement:** T+1 (2026-09-01); accrued interest on Actual/365.

### 3.3 Result

**MOF's curve fits the bonds closely** (off-curve gap 0.85bp median), so
almost all of the gap is the coupon effect.

| Maturity | Bonds | Avg coupon effect | Range | Avg price gap (per 100) |
| --- | --- | --- | --- | --- |
| 1–3Y | 56 | +0.1bp | 0.0 to +0.4bp | −0.01 |
| 3–5Y | 45 | +0.3bp | −0.4 to +1.4bp | 0.00 |
| 5–10Y | 66 | +1.5bp | −0.1 to +6.9bp | −0.13 |
| 10–15Y | 31 | +7.8bp | +1.1 to +15.8bp | −0.52 |
| 15–20Y | 37 | +10.8bp | +0.6 to +25.1bp | −1.01 |
| 20–25Y | 24 | +24.7bp | +9.2 to +33.0bp | −2.46 |
| 25–30Y | 25 | +16.4bp | +0.4 to +32.4bp | −1.38 |
| 30–40Y | 10 | +16.0bp | +0.8 to +25.7bp | −2.03 |

**The coupon drives it.** Within every maturity bucket, the coupon effect
tracks how far the coupon sits below the yield (correlation −0.79 to
−0.97; −0.93 or stronger beyond 10Y). The 66 bonds priced 98–102 are all
within 0.55bp. The 111 bonds priced below 85 have a median gap of
+13.8bp. A positive gap means the zero curve makes the bond yield too
much — prices it too cheaply.

### 3.4 The bonds MOF's curve is built from

Using MOF's own selection rule (newest issue in the class, plus the
issues maturing nearest each grid year):

| Grid | Bonds MOF uses | Their price | Gap |
| --- | --- | --- | --- |
| 1–5Y | 2Y/5Y issues | 96–100 | −0.4 to +2.0bp |
| 6–9Y | 10Y class, older issues | ~87–91 | +2.3 to +3.2bp |
| 10Y | newest 10Y | ~98 | +0.8bp |
| **15Y** | 20Y class, older issues | **~65** | **+15.8bp** |
| 20Y | newest 20Y | ~99 | +1.0bp |
| **25Y** | 30Y class, older issues | **~47** | **+32.3 to +32.5bp** |
| 30Y | newest 30Y | ~98 | −0.2bp |
| 40Y | newest 40Y | ~94 | +0.2bp |

**This explains the 25Y kink.** MOF's 25Y point is the yield of 30Y-class
bonds issued years ago with coupons under 1%, trading near 47. On a
rising curve, such a bond yields more than a par bond of the same
maturity. The 20Y, 30Y and 40Y points come from new, near-par bonds. So
MOF's curve peaks at 25Y (4.10%, above 30Y's 4.09%) partly because it
switches between low-coupon and par bonds — the kink both Nelson-Siegel
and Svensson miss by ~23bp (§2.3). The 15Y point has the same issue at
about half the size.

### 3.5 What this means for the project's numbers

- **The project's own portfolio is barely affected.** Its bonds (2–40Y,
  coupons 1.0–3.8%) sit near par and mature at grid years built from new
  issues. Real JGBs with similar coupons and maturities show gaps of
  −2.0 to +4.7bp, most under 2bp.
- **The zero curve's shape is wrong around 15Y and 25Y.** For a deep-
  discount bond, most of the value is the final payment, so a ~3-point
  price gap on a 25Y bond priced ~47 means the zero rate there is roughly
  25bp too high (≈ 6.6% too little value ÷ 25 years). About 15bp at 15Y.
  This is a rough reading, not a fitted correction.
- **A real-bond portfolio would be affected** (Phase 8): old low-coupon
  issues would be mispriced by up to ~3 points per 100.
- **Factor results are not tested here.** PCA runs on MOF's yields, not
  the zero curve. Whether the 15Y/25Y distortion moves day to day enough
  to affect factors is item 4's question.

**Limitations.**

- One day only (2026-08-31).
- Some of the low-coupon gap may be real market pricing — for example,
  different demand for low-coupon bonds — that even a curve fitted to
  bond prices would leave in. Separating the two needs that fitted
  curve.
- JSDA quotes are dealer reference prices, not trades.
- The class mapping and selection rule follow MOF's published outline;
  MOF's exact issue choice on the day isn't published.
