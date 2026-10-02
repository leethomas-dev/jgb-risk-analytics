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

---

## Technical details

**Files:** `models/pca.py` (`describe_level_shape()`,
`PARALLEL_SHIFT_MAX_RATIO`), `app.py` (sections 5 and 7 captions),
`models/curve_fitting.py` (`leave_one_tenor_out()`).

**Tests:** 386 → 393, all passing (4 in `tests/test_pca.py`, 3 in
`tests/test_curve_fitting.py`).

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
