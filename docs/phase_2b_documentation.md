# Phase 2B Documentation — `models/bond_pricing.py`

## In plain English

A bond is essentially a loan: you hand over money today, and in exchange
you receive a series of interest payments over time, plus your original
money back at the end. This part figures out what that whole series of
future payments is actually worth in today's money, using the current
interest rates from Phase 1. The core idea is simple: a payment arriving
further in the future is worth less today than the same-sized payment
arriving sooner — money now is more useful than the same amount of money
later. This part calculates exactly how much less, for every single
payment a bond will make over its life, then adds all of those
today's-money values together to arrive at a fair price for the bond as a
whole.

---

## Technical details

Curve-based bond pricing: values a bond as the sum of its cash flows, each
discounted at the JGB curve yield interpolated to *that cash flow's own*
maturity — not one flat yield applied to the whole bond. Reads the
portfolio via `config.portfolio_loader.load_portfolio()` (Phase 2A) and the
curve via `data.jgb_curve_loader.load_jgb_curve()` (Phase 1); this module
never hardcodes a bond list or a curve.

**File in this phase:** `models/bond_pricing.py` — `curve_yield_at()`
(interpolation/extrapolation), `price_bond()` (single-bond pricing),
`price_portfolio()` (batch pricing), `__main__` (loads the real portfolio
and curve, prints results).

**Output contract:** `price_bond(face_value, coupon_rate, maturity_years,
curve, freq=2) -> float`, the bond's price per `face_value` of face amount.
`price_portfolio(portfolio, curve, freq=2) -> pd.DataFrame` with columns
`[name, maturity_years, coupon_rate, weight, price]`, one row per bond, in
portfolio order. A portfolio-level weighted price is
`(df.weight * df.price).sum()`, valid directly because `load_portfolio()`
already guarantees weights sum to 1.0.

---

## 1. What each piece does, and why it was built that way

### 1.1 `curve_yield_at(curve, maturity_years)` — interpolation and the extrapolation policy

**What it does**

Looks up (or extrapolates) `curve['yield']` at an arbitrary maturity via
`numpy.interp`, after defensively re-sorting the curve by
`maturity_years`. Accepts either a scalar or an array-like of maturities —
`price_bond` calls it once with the bond's whole cash-flow-time array
rather than once per cash flow.

Within the curve's tenor range: linear interpolation between the two
bracketing points. Outside it — a cash flow shorter than the curve's
shortest tenor, or longer than its longest — **flat extrapolation**: the
boundary yield is held constant, via `numpy.interp`'s `left=`/`right=`
arguments passed explicitly (not left as its unstated default, so the
choice reads as a decision, not an accident).

**Why it was built this way**

- **Flat extrapolation was chosen over linearly continuing the curve's
  terminal slope**, which was the explicit alternative on the table. The
  reason: continuing an observed slope past the last (or first) quoted
  tenor can produce an implausible or even negative yield the further out
  it's extrapolated — e.g. continuing a steep front-end slope out to a
  synthetic 50Y tenor, or a downward-sloping short end out past 1M. Flat
  extrapolation cannot do that: every value it returns is one the curve
  actually quoted somewhere, so it can never invent a yield outside the
  curve's own observed range. This is the conservative default named in
  the Part B requirements, and it is applied identically at both ends
  rather than picking different policies for the short and long side.
- **This is not a hypothetical edge case for this project — it is
  guaranteed to trigger on every bond, every time the live/cache tier
  serves the curve.** The live/cache grid's shortest tenor is 1Y (no
  sub-year points at all — `_MOF_TENOR_COLUMNS` in
  `data/jgb_curve_loader.py` starts at 1Y; Phase 1 doc §4.5). Every
  semiannual bond's *first* coupon cash flow lands at `t=0.5`, below that
  grid's shortest tenor. So under the live/cache tier, the extrapolation
  branch isn't a rare tail case reached only by an unusually short or long
  synthetic maturity — it fires on the very first cash flow of every bond
  in the portfolio. `test_first_coupon_requires_extrapolation_on_live_grid_but_not_snapshot`
  exercises exactly this, and contrasts it with the snapshot grid (whose
  shortest tenor is 1M = 0.083Y), where the same `t=0.5` lookup is a normal
  interpolation, not an extrapolation.
- **No assumption about how many tenors the curve has, or which ones.**
  `curve_yield_at` reads `curve['maturity_years']`/`curve['yield']`
  directly; it does not reindex onto a fixed tenor set or special-case a
  point count. This is a direct continuation of Phase 1's non-fixed-tenor-
  grid contract (§4.5) — reindexing here would silently break the moment
  the curve's source tier changed, which is exactly the failure mode that
  contract exists to prevent.
- **Defensive re-sort inside the function**, rather than trusting the
  caller. `load_jgb_curve()` guarantees a sorted frame, but a hand-built
  test fixture (or a future caller) might not construct one in order;
  `numpy.interp` requires ascending x-values to behave correctly, so
  sorting here removes a footgun rather than documenting it as a caller
  obligation.
- **Vectorized, not scalar-only.** Accepting an array-like `maturity_years`
  means `price_bond` can evaluate an entire cash-flow schedule (up to 80
  rows for the longest illustrative bond) in one `numpy.interp` call
  instead of one Python-level call per cash flow — cheap now, and it's
  also the shape Phase 3's per-tenor curve bumping will want to reuse.

---

### 1.2 `price_bond(face_value, coupon_rate, maturity_years, curve, freq=2)`

**What it does**

Validates inputs, builds a cash-flow schedule of `freq` payments per year
generated **backward from `maturity_years`** in steps of `1/freq` (so the
final payment lands exactly at `maturity_years`), looks up each cash
flow's own yield via `curve_yield_at`, discounts each cash flow at
`(1 + y(t_i)/freq)^(freq * t_i)`, and sums.

**Why it was built this way**

- **Schedule anchored to maturity, generated backward, not forward from
  "today."** Real coupon dates are fixed relative to a bond's maturity
  (semiannual JGB coupons fall on the same day-of-year as redemption, going
  back), so counting backward from `maturity_years` in `1/freq` steps is
  the market-realistic anchor point, not an arbitrary implementation
  choice.
- **A `maturity_years` that isn't an exact multiple of `1/freq` gets a
  single short front "stub" period, not an error.** `n_periods =
  max(1, round(maturity_years * freq))` and the schedule is built
  backward from that, so the *first* period absorbs any fractional
  remainder. This matters concretely for Phase 2A's schema-forward-
  compatibility work: a real-issue bond's `maturity_years`, when derived
  from an actual `maturity_date`, is essentially never an exact multiple of
  `0.5` (e.g. `19.98`, not `20`). `price_bond` handles that today with no
  special-casing, so a future real-issue portfolio prices through this
  function unchanged. The `max(1, ...)` floor additionally guards an
  extremely short maturity from producing an empty schedule (pricing to
  zero) rather than a single terminal payment.
- **Vectorized cash-flow-time and discount-factor arrays** (`numpy.arange`
  / elementwise power), rather than a per-period Python loop accumulating a
  running total. Both are equally correct for a schedule this short (at
  most 80 rows); vectorizing was chosen because it is also the natural
  shape for Phase 3's bump-and-reprice loop to build on, and it keeps
  `curve_yield_at`'s one-call vectorized lookup meaningful rather than
  wrapping it in a loop that defeats the point.
- **Explicit input validation** (`face_value > 0`, `maturity_years > 0`,
  `freq > 0`, `curve` non-empty), each with a message naming the offending
  value — the same "a config/input error should read like one" principle
  `config/portfolio_loader.py`'s validation follows, rather than letting a
  bad input surface later as a cryptic `numpy` shape or division error.
- **No per-call setup cost, and nothing cached against a specific curve
  instance.** This is deliberate, not an oversight: Phase 3 adds Key Rate
  Duration, DV01, and the ultra-long duration profile, all computed by
  bumping one tenor's yield in `curve['yield']` and calling `price_bond`
  again — potentially thousands of calls across all bonds × all tenors ×
  both bump directions. Because `price_bond` takes `curve` as a plain
  argument and does no memoization tied to a particular `curve` object,
  that loop needs no new plumbing in this module when Phase 3 arrives; it
  is already the cheapest possible thing to call repeatedly.
- **Discounts at the interpolated *par* yield directly — not a bootstrapped
  zero/spot rate.** `y(t_i)` comes straight from `curve_yield_at`, which
  reads par yields (the curve loader's own output contract — Phase 1 §
  "Output contract"). This is a deliberate simplification, not an
  oversight, and is significant enough to warrant its own limitation entry
  (§3.2) rather than being buried here.

---

### 1.3 `price_portfolio(portfolio, curve, freq=2)`

**What it does**

Prices every `Bond` in an already-loaded `portfolio` against one
already-loaded `curve`, returning one row per bond.

**Why it was built this way**

- **Takes `portfolio` and `curve` as plain arguments — no loading inside
  it.** `price_portfolio` does no file or network I/O of its own; loading
  is `__main__`'s job (or a test's, or eventually Phase 3's). This keeps
  it trivially testable against in-memory fixtures
  (`test_price_portfolio_matches_price_bond_per_row` etc. never touch disk
  or network) and reusable unchanged for Phase 3's repricing loop, which
  will want to call it repeatedly against a *mutated* curve it already
  holds in memory — an implicit "load the curve" step inside this function
  would be actively in the way there.
- **Returns a `DataFrame`, not a `list[dict]` or a second dataclass.**
  Portfolio-level aggregation (`(df.weight * df.price).sum()`) and any
  future per-bucket rollup (Phase 3's KRD/duration profile) are natural
  `pandas` operations; returning a `DataFrame` here means `__main__` (and
  Phase 3) get that for free rather than converting first.

---

### 1.4 `__main__`

**What it does**

Loads the curve via `load_jgb_curve()` (default `prefer_live=True` —
real usage, not the deterministic test path) and the portfolio via
`load_portfolio()`, prices it, and prints a per-bond table plus the
portfolio-level weighted price.

**Why it was built this way**

- **Uses the real, non-pinned defaults**, matching the pattern in both
  `data/jgb_curve_loader.py`'s and `config/portfolio_loader.py`'s own
  `__main__` blocks: a person running this file directly wants to see what
  the pricing engine actually does right now, including which curve tier
  served it (the loader's own `verbose=True` log line reports that) — not
  the pinned snapshot every test uses for determinism.

---

## 2. Testing strategy: why these three checks, specifically

The Part B requirements named three specific kinds of test — an analytic
check, a direct extrapolation test, and genuine grid-independence — rather
than leaving test coverage to judgment. Each targets a different way this
kind of pricing function silently goes wrong:

- **The closed-form analytic check** (`test_matches_closed_form_flat_yield_price_*`,
  at freq 1/2/4) validates the discounting and cash-flow-schedule
  mechanics *independently of curve shape*, by comparing against the
  textbook flat-yield bond price formula computed directly in the test —
  not by re-deriving `price_bond`'s own logic. This is the check that
  would catch an off-by-one in the schedule, a wrong compounding exponent,
  or a coupon-amount error, none of which a curve-shape-dependent test
  would necessarily surface.
- **The extrapolation tests** (`test_extrapolation_is_flat_*`,
  `test_price_bond_uses_flat_extrapolation_beyond_curve_range`) exist
  because a policy that is only described in a comment can silently drift
  the next time someone touches `curve_yield_at` — nothing would fail.
  `test_extrapolation_would_diverge_from_flat_if_linear_continuation_were_used`
  goes one step further: it computes what a linear-continuation answer
  *would* be for the test's own curve and asserts it's meaningfully
  different from the flat answer actually returned, so the test cannot
  pass by coincidence if flat and linear happen to agree for some curve.
- **Grid-independence** (`test_price_bond_correct_against_both_grid_shapes`,
  parametrized over both grids) is the one explicitly required to be a
  *real* test, not a claim — so it prices the same bond against the real
  12-point snapshot (via `load_jgb_curve(prefer_live=False)`) and a
  15-point fixture built from `data.jgb_curve_loader._MOF_TENOR_COLUMNS`
  (§4 below explains why a fixture was needed rather than a live pull),
  and checks each result against an independently-recomputed expected
  price — not merely that both calls complete without an exception.
  `test_yield_at_exact_shared_tenor_matches_both_grids_own_quote` adds a
  narrower, harder-to-get-wrong-by-accident check: 10Y is an exact tenor
  point on both grids, so the yield used there should match each curve's
  own quote exactly, with zero interpolation error, regardless of how many
  other points surround it.

---

## 3. Known limitations (for the SR 11-7 validation report)

### 3.1 Clean price only; no accrued interest or settlement-date modeling

`price_bond` computes a clean price as of "now," anchored purely to
`maturity_years` — there is no settlement date, no day-count convention
applied to accrual, and no distinction between a clean and a dirty
(accrued-interest-inclusive) price. This continues the boundary Phase 2A
already drew: `config/portfolio_loader.py`'s `issue_date` field (§3.3 of
the Phase 2A doc) is captured on `Bond` but not read by anything, including
this module.

Mitigant: named explicitly here and in `price_bond`'s own docstring, so a
reviewer sees it as a stated scope boundary rather than a gap discovered
by inspection.

### 3.2 Par yield used directly as the discount rate, not a bootstrapped zero curve

Each cash flow at time `t_i` is discounted at `curve_yield_at(curve, t_i)`
— the curve's own **par** yield at that maturity, applied as if it were
the correct spot/zero rate for a cash flow landing exactly there. This is
a standard, common simplification in quick curve-based analytics, but it
is not the same as a rigorous zero-curve bootstrap, which would first
derive the true zero rate at each maturity from the par curve (accounting
for the fact that a par bond's stated yield reflects a blend of all its
own coupon dates, not a single point-in-time rate) and discount with
*that* instead. The two can diverge meaningfully for a curve with real
curvature, more so at longer maturities where more compounding periods
accumulate the error.

This is a **deliberate scope decision for Part B, following the exact
pricing formula given in the Part B requirements** (`Price = sum_i [CF_i /
(1 + y(t_i)/freq)^(freq*t_i)]`, with `y(t_i)` read directly off the par
curve) — not an oversight discovered after the fact. It is called out here
because it departs from an earlier, more general expectation, noted when
Phase 1 shipped, that bootstrapping par yields to zero/discount factors
would be Phase 2 work; that bootstrap was not built, and this pricing
engine does not depend on it existing.

Mitigant: named explicitly here, in `price_bond`'s docstring, and tracked
as a Phase 6 validation-report assumptions-section item — a reviewer
should know this pricing engine's discount rates are par yields, not
bootstrapped zero rates, before trusting an absolute price to more
precision than that approximation supports.

### 3.3 Flat extrapolation is a policy choice, not a model of what the curve would actually do there

Flat extrapolation (§1.1) guarantees a *plausible* yield beyond the
curve's quoted range, but it is not a claim that the real JGB curve is
actually flat past 40Y or below 1M (or 1Y, for the live/cache grid) — it
is simply the most defensible default among the alternatives considered,
chosen specifically to avoid manufacturing an implausible number. The true
market yield beyond the quoted range is genuinely unknown from this data;
flat extrapolation does not resolve that, it just avoids making it worse.

Mitigant: named as a policy, with its rejected alternative, in
`curve_yield_at`'s own docstring (§1.1) rather than presented as
market-accurate.

---

## 4. Grid-independence fixture: why a hand-built 15-point curve, not a live pull

The 15-point live/cache-shaped fixture (`_build_live_grid_fixture` in
`tests/test_bond_pricing.py`) is built from `data.jgb_curve_loader`'s own
`_MOF_TENOR_COLUMNS` constant — the real tenor set (1Y…10Y, 15Y, 20Y, 25Y,
30Y, 40Y) — paired with synthetic, merely-monotonic yields, rather than
either (a) hitting the live MOF endpoint in a test, or (b) hand-typing a
plausible-looking tenor list from memory.

**Why:** a test that reaches the network is neither deterministic nor
always available (exactly the reasoning `prefer_live=False` exists for in
the first place — Phase 1 §1.5), so pulling live data into a test suite was
never on the table. Deriving the tenor set from `_MOF_TENOR_COLUMNS`
itself, instead of retyping `[1, 2, 3, ..., 40]` by hand, means the fixture
cannot silently drift out of sync with the real live/cache grid if that
constant is ever revised — the whole point of this fixture is to be an
honest stand-in for that grid's *shape*, and importing the source of truth
for that shape is the only way to guarantee it.

---

## 5. What would change this design

### 5.1 A real zero-curve bootstrap

If §3.2's par-yield approximation needed replacing with a proper bootstrap,
the change would be additive and contained: a new function (e.g.
`bootstrap_zero_curve(par_curve) -> zero_curve`, same `[maturity_years,
yield]` shape) sitting between `load_jgb_curve()` and `price_bond`, with
`price_bond` itself unchanged — it already just calls `curve_yield_at` on
whatever curve it's handed, par or zero. This is the same "swap behind a
stable interface" property Phase 1's curve loader and Phase 2A's portfolio
loader were each built around.

### 5.2 Settlement-date and accrued-interest modeling

Adding a clean/dirty price distinction would need a settlement date (a new
`price_bond` parameter, or reading `Bond.issue_date` together with a
day-count convention) and would change what `cash_flow_times` represents —
currently just "years from now," it would need to become "years from
settlement," with an explicit accrued-interest adjustment subtracted from
the clean price. Not built now; flagged in §3.1 as the reason it isn't.

---

## 6. Relationship to the fallback re-anchoring policy

`models/bond_pricing.py` introduces no new hardcoded or fallback market
data — it contains only pricing logic, and reads the curve and portfolio
through the Phase 1 and Phase 2A loaders respectively. The project-wide
re-anchoring policy (Phase 1 doc §6) therefore has no new instance to
govern here; this section says so explicitly, the same way Phase 2A's doc
addressed the policy directly rather than leaving its applicability
unstated.
