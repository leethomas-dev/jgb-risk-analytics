# Phase 4.6B Documentation — yield and duration measures (`models/bond_analytics.py`)

## In plain English

This part adds the numbers every bond desk actually looks at first:
yield to maturity (the single interest rate that "explains" a bond's
price), duration (how much its price moves for a small change in
rates), and convexity (a correction that makes that estimate much better
for a bigger change in rates). This project has always computed a
bond's price by discounting each of its payments at the specific rate
the government's own curve quotes for that exact date — sophisticated,
but not what shows up on a market data screen. This part runs that
process in reverse for reporting purposes: given a price, what single
rate would explain it? And it double-checks its own duration number two
different ways — once against a much simpler textbook case (a bond that
pays no interest at all, where the answer is provably exact), and once
against a completely different, already-existing calculation elsewhere
in this project. That second check turns up something worth explaining
rather than hiding: the two numbers agree closely for a short bond but
genuinely diverge for a very long one — not a bug, but a real
consequence of how much the actual curve slopes between a long bond's
first payment and its last.

---

## Technical details

Adds yield-to-maturity, Macaulay/modified duration, and convexity —
built on the existing pricing engine (`price_bond`, Phase 2B) and reusing
the existing zero-finder (`models.bootstrap.implied_ytm`, Phase 4.5A) and
bump size constant (`models.key_rate_duration.DEFAULT_BUMP_SIZE`, Phase
3A) rather than introducing new ones.

**File:** `models/bond_analytics.py` — `yield_to_maturity()`,
`macaulay_duration()`, `modified_duration()`, `convexity()`,
`bond_analytics_portfolio()`.

**Output contract:** `yield_to_maturity(face_value, coupon_rate,
maturity_years, price, freq=2) -> float`. `macaulay_duration(...)` /
`modified_duration(...)` / `convexity(...)` each take an already-solved
`ytm` (not a curve) and return a `float`. `bond_analytics_portfolio(portfolio,
curve, freq=2) -> pd.DataFrame` — one row per bond plus a
`portfolio_total` row, columns `[name, maturity_years, coupon_rate,
weight, price, ytm, macaulay_duration, modified_duration,
effective_duration, convexity]`.

---

## 1. Direction of causation, and which price YTM is solved against

Everywhere else in this project, a bond's price is computed **from** the
curve (`price_bond`). Here that runs in reverse, for reporting purposes
only: `yield_to_maturity()` takes an **already-computed** price and
solves for the single flat rate that would have produced it. Nothing
upstream in this project ever takes a YTM as an input — it is a
description of a price, never a driver of one.

**Solved against the clean price, not the dirty price** (Phase 4.6A's
`price_bond()` output, not `dirty_price()`). The reasoning is mechanical,
not a convenience: `price_bond`'s cash-flow schedule
(`cash_flow_schedule`, promoted to a public function from Phase 4.6A's
private `_cash_flow_schedule` specifically so this module could reuse it
directly — the same "promote a private helper for a second module to
share" pattern `models.curve_fitting.hump_factors` used for
`models.diebold_li`, `docs/phase_4_5b_dl_documentation.md` §1) is anchored
at `t=0` = "a coupon just occurred" — exactly the clean price's own
assumption (Phase 4.6A's own doc, module docstring). Real-market practice
conventionally solves YTM against the **dirty** price instead, discounting
from the actual settlement date with a genuine fractional first coupon
period. This project's `dirty_price()` does **not** rebuild that schedule
from a settlement date to do that — it adds a separately prorated
`accrued_interest()` on top of the same `t=0`-anchored schedule
(`docs/phase_4_6a_documentation.md` §3). Solving this module's flat-yield
equation against the dirty price would therefore mix two different
schedule assumptions into one root-find — a more deeply hidden error than
plainly using the clean price and saying so. This is a **disclosed
simplification**, not the textbook-standard convention; see §5.1.

**Reusing `implied_ytm`, not a new solver.** `models.bootstrap.implied_ytm`
(Phase 4.5A) already does exactly this — inverts `price_bond` by bisection
over a wide bracket (`[-2%, 30%]`), reused directly here rather than a
second, independent root-finder for the same problem.

**Non-convergence, reported rather than silently wrong (the phase brief's
own instruction).** Two layers, not one:

1. `implied_ytm`'s own bracket check raises `ValueError` if the target
   price has no solution inside `[-2%, 30%]` — propagated unchanged, with
   its own clear message.
2. `yield_to_maturity()` independently **reprices** the solved yield via
   `price_bond` (a separate call path from `implied_ytm`'s own internal
   bisection loop) and raises `RuntimeError` if that reprice isn't within
   `YTM_VERIFY_TOLERANCE` (`1e-6`, several orders looser than
   `implied_ytm`'s own internal `tol=1e-10`) of the target price. This is
   a genuine second guard, not a restatement of the first — proven to
   actually fire, not just asserted in a docstring:
   `test_ytm_verification_actually_fires_on_a_broken_solver` monkeypatches
   `implied_ytm` itself to return a deliberately wrong yield and confirms
   `yield_to_maturity` catches the resulting mismatch rather than
   returning it.

---

## 2. Macaulay duration, modified duration, and the effective-duration cross-check

**Macaulay duration** (`macaulay_duration`): the weighted-average time to
a bond's cash flows, weighted by each flow's own present-value share —
discounted at the single flat `ytm`, exactly as the phase brief specifies.
**Modified duration** (`modified_duration`) = Macaulay / (1 + ytm/freq),
the brief's own formula, computed as a one-line adjustment on top of it.

**The zero-coupon correctness anchor, exact by construction.** A
zero-coupon bond has exactly one cash flow, so its present-value share is
1.0 regardless of the discount rate — the weighted average collapses to
that one cash flow's own time. `test_zero_coupon_macaulay_duration_equals_maturity_exactly`
confirms this holds to `abs=1e-9` across four maturities and four
different yields (including a negative one).

**The cross-check the phase brief calls "the real tests": modified
duration vs. `effective_duration_bond` (Phase 3A).** These measure two
*different* sensitivities — modified duration is analytic, defined
against the bond's own single flat YTM; `effective_duration_bond` is
curve-based, bumping the real curve's every point and repricing with each
cash flow discounted at its own, different, curve-implied rate. They
coincide exactly only when the curve is flat across a bond's own cash
flows — and this project's real curve is not flat (2Y quotes ~1.4%, 40Y
quotes ~3.9%, snapshot curve).

**Investigated, not assumed, per the phase brief's own instruction ("a
material gap indicates an error — investigate rather than loosen
tolerance").** Two runs isolate the effect cleanly:

**Control — a genuinely flat curve.** With `curve_yield_at` returning the
same rate everywhere, a bond's own YTM equals the curve's rate exactly at
every cash flow, so the two measures should — and do — agree tightly
(`test_modified_matches_effective_duration_closely_on_a_flat_curve`,
`rel=1e-3`, itself far looser than the actual observed agreement of
`~1e-5`–`1e-8` relative across maturities 2Y–40Y). This rules out an
implementation bug: the formulas agree almost exactly whenever the
premise they're both measuring (a single flat rate) actually holds.

**The real snapshot curve** (`prefer_live=False`, reproducible):

| Bond | Modified duration | Effective duration | Gap | Gap (relative) |
| --- | --- | --- | --- | --- |
| JGB_2Y | 1.9713 | 1.9712 | +0.000037 | +0.0019% |
| JGB_5Y | 4.7909 | 4.7898 | +0.001068 | +0.0223% |
| JGB_10Y | 8.9883 | 8.9718 | +0.016445 | +0.1833% |
| JGB_20Y | 14.8958 | 14.6498 | +0.245963 | +1.6789% |
| JGB_30Y | 18.5863 | 17.8702 | +0.716112 | +4.0073% |
| JGB_40Y | 21.0259 | 19.7945 | +1.231375 | +6.2208% |

**Reading this, not hiding it.** The gap is negligible at the short end
and grows to a genuinely material **6.2% relative gap at 40 years** — the
same recurring theme this project has already found twice before, at two
different scales: Phase 3A's own (tiny, ~0.001%) sum-of-KRD-vs-effective-
duration convexity residual, and Phase 4.5A/4.5C's (large, up to 772bp of
price) par-vs-zero-curve coupon effect. In every case, a single
representative rate substitutes for a genuinely curve-shaped one, and the
substitution error grows with how much of the curve's own shape sits
between a bond's cash flows — here, specifically, with how much yield
curve slope a bond's own maturity spans. `test_modified_vs_effective_duration_gap_on_the_real_curve_is_bounded_and_grows_with_maturity`
checks this pattern directly (monotonically increasing with maturity,
bounded at `<1%` for the 2Y bond and `<15%` for the 40Y bond — both well
above the measured values, so a genuine multi-fold regression would still
be caught) rather than loosening a single tolerance to paper over it.

---

## 3. Convexity: central-difference, cross-checked against a closed form

**Method, and why.** `convexity()` uses a central difference on
`price_bond` — the same bump-and-reprice technique this project already
uses for KRD, DV01, and `effective_duration_bond` (Phase 3A/3B), reusing
`models.key_rate_duration.DEFAULT_BUMP_SIZE` (1bp) as the default bump —
applied here to the bond's own flat yield via a 2-point flat curve, not to
a curve tenor:

```
convexity = (P(y+h) + P(y-h) - 2*P(y)) / (P(y) * h^2)
```

A closed-form analytic convexity formula exists and was **not** used as
the module's own implementation — the central-difference choice keeps
this project's sensitivity-computation methodology uniform (bump-and-
reprice everywhere, no closed-form derivative anywhere) rather than
introducing a second methodology just for this one number.

**Cross-checked against that closed form anyway, independently** (not
adopted as the implementation, used only to verify it):
`test_convexity_matches_an_independent_closed_form_formula` computes
convexity via `sum_i[t_i*(t_i+1/freq)*CF_i / (1+y/freq)^(freq*t_i+2)] / Price`
— a completely separate code path — and confirms agreement to `rel=1e-5`
or tighter, at three maturities. Measured directly during development:
at `DEFAULT_BUMP_SIZE` (1bp), the two agree to **~1e-6–1e-7 relative**
error, comfortably before floating-point noise would start to matter at
a much smaller bump — confirming 1bp costs no real accuracy for this
calculation and there was no need to pick a different, convexity-specific
bump size.

---

## 4. The Taylor approximation: why convexity earns its place

The clearest possible demonstration that convexity is computed correctly
is showing it actually improves a prediction — the phase brief's own
framing. For the portfolio's longest bond (JGB_40Y, snapshot curve,
`ytm = 3.5272%`, `modified_duration = 21.0259`, `convexity = 643.88`):

| Δy | Actual reprice | Duration-only | error | Duration + convexity | error |
| --- | --- | --- | --- | --- | --- |
| +10bp | −2.0708% | −2.1026% | 0.0318% | −2.0704% | **0.0004%** |
| +100bp | −18.1501% | −21.0259% | 2.8758% | −17.8065% | **0.3436%** |
| +200bp | −31.6979% | −42.0517% | 10.3538% | −29.1741% | **2.5238%** |
| −200bp | +58.6052% | +42.0517% | 16.5535% | +54.9293% | **3.6759%** |

At a small move (10bp), both approximations are close, but
duration+convexity is already ~80x more accurate. At the large moves the
brief specifically asks about (100–200bp), duration-alone's error grows
into double digits (up to 16.6 percentage points on a −200bp shock) while
duration+convexity stays within a few percentage points throughout — the
second-order term is doing real, substantial work, not a cosmetic
correction. `test_duration_plus_convexity_tracks_actual_reprice_better_than_duration_alone`
checks this holds at 100bp and 200bp in both directions;
`test_duration_plus_convexity_is_a_tighter_bound_for_a_small_move_too`
confirms the correction doesn't accidentally hurt at a small move either.

---

## 5. Portfolio-level reporting

`bond_analytics_portfolio()` reports every measure above, per bond, plus
a `portfolio_total` row — weighted the same way `key_rate_duration_portfolio`
/ `dv01_by_tenor_portfolio` already weight their own total rows (each
bond's config weight, `load_portfolio()`'s own guarantee that these sum
to 1.0). `effective_duration` is included as a column directly alongside
`modified_duration`, specifically so §2's cross-check is visible in the
table itself, not only in a separate test. The `portfolio_total` row's
`ytm` is a value-weighted **average** of the bonds' own individual
yields — a common market approximation for "the portfolio's yield," not
a rigorously derived single discount rate for a multi-bond book (no such
single rate generally exists once the bonds have different cash-flow
timing).

**A real run** (snapshot curve, `prefer_live=False`):

| Bond | Price | YTM | Macaulay | Modified | Effective | Convexity |
| --- | --- | --- | --- | --- | --- | --- |
| JGB_2Y | 99.2174 | 1.3982% | 1.9851 | 1.9713 | 1.9712 | 4.88 |
| JGB_5Y | 98.5171 | 1.8116% | 4.8342 | 4.7909 | 4.7898 | 25.82 |
| JGB_10Y | 96.7707 | 2.3645% | 9.0945 | 8.9883 | 8.9718 | 90.29 |
| JGB_20Y | 98.1408 | 3.1257% | 15.1286 | 14.8958 | 14.6498 | 271.43 |
| JGB_30Y | 101.6310 | 3.4127% | 18.9035 | 18.5863 | 17.8702 | 464.87 |
| JGB_40Y | 105.8248 | 3.5272% | 21.3967 | 21.0259 | 19.7945 | 643.88 |
| **portfolio_total** | 99.3269 | 2.4966% | 10.7827 | 10.6258 | 10.3540 | 203.30 |

---

## 6. Known limitations (for the SR 11-7 validation report)

**6.1 YTM is solved against the clean price, a disclosed departure from
standard market practice (§1).** Real-market YTM conventionally uses the
dirty price with a genuine fractional first period discounted from
settlement. This project's clean-price choice is the internally
consistent one given how `dirty_price()` is actually built (an additive
accrued-interest layer, not a schedule rebuild) — but a reader comparing
this project's YTM figures against a market data terminal's own YTM for
the same bond should expect a small, systematic difference for exactly
this reason, growing with how far settlement sits from a coupon date.

**6.2 Modified duration and effective duration are different sensitivities
that diverge with real curve slope — a genuine, material effect at the
long end, not a rounding-level one (§2).** A 6.2% relative gap at 40
years is not something to average away; a consumer needing "the" duration
of a long JGB should be explicit about which of the two they mean.

**6.3 Every measure in this module inherits `price_bond`'s own curve-
interpolation and rate-quotation simplifications** (Phase 2B §3.2, Phase
4.5A/4.5C's coupon-effect and par-vs-zero findings) — restated here since
a YTM or duration figure invites a sharper reading than the price it's
derived from.

**6.4 Convexity's closed-form cross-check (§3) uses the same cash-flow
schedule (`cash_flow_schedule`) and the same `price_bond` the
central-difference implementation calls — it is an independent
DERIVATION, not an independent DATA SOURCE.** A bug shared by both (e.g.
in `cash_flow_schedule` itself) would not be caught by this cross-check;
it would already have been caught (or not) by Phase 2B's own tests for
that function.

**6.5 The portfolio-level YTM is an approximation, not a rigorous single
rate for the book (§5).** A value-weighted average of individual yields
is standard market shorthand, not a number with the same theoretical
grounding as a single bond's own YTM.

---

## 7. What would change this design

**Solving YTM against the dirty price** (§6.1), if ever needed for
closer alignment with market-data-terminal conventions, would need
`price_bond`'s own cash-flow schedule rebuilt from an actual settlement
date (a genuine fractional first period, not `dirty_price()`'s current
additive accrued-interest layer) — a larger change than this phase's
scope, not a parameter tweak.

**A closed-form (rather than central-difference) convexity
implementation**, if this project ever added a second use for the closed
form beyond cross-checking, would replace `convexity()`'s own body
directly — §3's cross-check test already confirms the two formulas agree,
so switching would change no reported number materially.

**A rigorous single-rate portfolio yield** (§6.5) — e.g. solving for the
one flat rate that reprices the WHOLE portfolio's combined cash flows to
its combined price — would be a new function alongside
`bond_analytics_portfolio`, not a change to it; not built now since the
value-weighted average is the standard, well-understood convention for
this kind of report.

---

## 8. Relationship to the fallback re-anchoring policy and earlier phases

This phase adds no new hardcoded market or portfolio data — every function
computes from whatever `price_bond`, `load_portfolio()`, or
`load_jgb_curve()` return, inheriting those modules' re-anchoring
policies rather than adding a new one. `YTM_VERIFY_TOLERANCE` and the
reuse of `DEFAULT_BUMP_SIZE` are methodology choices (§1, §3), not
observations of anything that moves.

**One small, in-spirit edit to Phase 4.6A's own code.** Phase 4.6A's
private `_cash_flow_schedule` was promoted to a public `cash_flow_schedule`
(a rename only — identical behavior, confirmed by the full pre-existing
test suite passing unchanged both before and after) so this module could
reuse it directly, rather than re-deriving the same schedule a second
time or importing a private symbol across a module boundary. This mirrors
`models.curve_fitting.hump_factors`'s own promotion from private for
`models.diebold_li` to reuse (`docs/phase_4_5b_dl_documentation.md` §1) —
an established pattern in this project, not a new one introduced here.
