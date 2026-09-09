# Phase 4.6C Documentation — cash flow ladder (`models/cash_flow_ladder.py`)

## In plain English

Every other part of this project asks "how much is this portfolio worth,
and how sensitive is that value to interest rates?" This part asks a much
simpler, more concrete question: on any given day between now and 2066,
how much actual cash does this portfolio receive, and how much of that is
routine interest versus a loan being paid back in full? It adds up every
bond's payments by the date they land, keeps interest and principal
separate the whole way through, and reports the total two ways — the raw
amount that actually changes hands (nominal), and what that future amount
is worth in today's money (present value). The chart makes the shape of
that obvious at a glance: six sharp spikes, one for each bond's
redemption, with routine interest payments as a thin, shrinking floor
underneath.

---

## Technical details

Aggregates every holding's coupon and principal payments by calendar
date, in both nominal and present-value terms. Computes no new pricing of
its own — reuses `bond_pricing.cash_flow_schedule` (the exact schedule
`price_bond` prices against) for cash-flow timing, and a new
`bond_pricing.discount_factors_at` (factored out of `price_bond` for this
exact reuse) for present-valuing each payment.

**File:** `models/cash_flow_ladder.py` — `CashFlowLadderResult` (frozen),
`compute_cash_flow_ladder()`, `plot_cash_flow_ladder()`.

**Output contract:** `compute_cash_flow_ladder(portfolio, curve, freq=2,
valuation_date=None, threshold_years=20.0) -> CashFlowLadderResult` — a
frozen result carrying the valuation date, frequency, ultra-long
threshold, and the full by-date ladder table (columns: `date`,
`years_from_valuation`, `coupon_nominal`, `principal_nominal`,
`total_nominal`, `coupon_pv`, `principal_pv`, `total_pv`), plus
`total_nominal` / `total_pv` / `ultra_long_nominal_share` /
`ultra_long_pv_share` properties. `plot_cash_flow_ladder(result,
output_path=..., basis="nominal") -> Path`, saving a PNG.

---

## 1. One small, reuse-motivated edit to Phase 2B/4.6A's `price_bond`

`price_bond`'s own two discounting branches (par-curve `curve_yield_at` +
compounding, or zero-curve `models.bootstrap.discount_factor_at`) were
factored into a new function, `discount_factors_at(curve, times, freq=2)`
— returning a genuine discount factor for either basis (so a caller
always multiplies cash flows by it, never divides for one basis and
multiplies for the other; the original par-curve branch divided by a
*compounding* factor internally, a small inversion needed to unify the
two into one shared function). `price_bond` itself now just calls it and
multiplies — **confirmed, via the full pre-existing test suite passing
unchanged both before and after, to produce bit-for-bit the same prices
as before.**

**Why this was worth a real code change, not a duplication.** This
module needs exactly this — "the discount factor for an arbitrary cash
flow, on whichever curve basis was actually passed in" — and price_bond
already computed exactly that internally, just not as something another
module could call. Re-deriving the same par/zero-curve branching a third
time (`bootstrap.py` already has its own zero-curve-only version,
`discount_factor_at`) would have been the kind of duplicated validated
logic this project has consistently avoided (the same reasoning behind
promoting `cash_flow_schedule` in Phase 4.6B and `hump_factors` in Phase
4.5B-DL). `discount_factors_at` is directly tested against `price_bond`'s
own output on both curve bases
(`test_discount_factors_at_reproduces_price_bond_on_a_par_curve` /
`..._on_a_zero_curve`) and against `bootstrap.discount_factor_at`
directly, confirming the delegation is exact.

```
Before this part's discount_factors_at refactor: 302 tests passing
After:                                            320 tests passing (18 new)
```

All 302 pre-existing tests pass unchanged.

---

## 2. Units: per 100 face value, weighted by portfolio weight — not a real position size

Every cash-flow figure in this module is each bond's own cash flow (per
its 100 face value, `config/portfolio.json`) **scaled by that bond's own
portfolio `weight`** before being summed across holdings — the same
aggregation `price_portfolio`'s own portfolio-level price already uses
(`(df.weight * df.price).sum()`), and the same one
`key_rate_duration_portfolio` / `dv01_by_tenor_portfolio` use for their
own `portfolio_total` rows. This is not a new convention introduced here,
and — per Phase 3B §1.1's own units disclaimer, which applies identically
— it is **not a real position size**: the portfolio itself is
illustrative (Phase 2A), so nothing in this ladder should be read as real
currency cash flow for an actual book.

---

## 3. Real calendar dates, and why they align cleanly across all six bonds

Each bond's cash-flow times (`cash_flow_schedule`, years from the
valuation date) are converted to real calendar dates the same way Phase
4.6A's `accrued_interest()` already does:
`valuation_date + round(years * DAYS_PER_YEAR)` days
(`config.portfolio_loader.DAYS_PER_YEAR = 365.25`) — reimplemented as the
same one-line formula here rather than importing `bond_pricing`'s private
`_coupon_boundaries()` (which returns period *boundaries* for accrued-
interest lookups, including the valuation-date anchor itself — a
different shape than the plain per-cash-flow date list this module
needs). This mirrors Phase 4.6A's own choice to write a small,
self-contained `_resolve_date()` rather than import a private
cross-module helper for three lines of logic — small, precedented
duplication preferred over a private dependency.

**A genuinely clean aggregation, not a coincidence.** Every bond in the
default portfolio shares the same `freq=2` and the same valuation date,
and every one of its maturities (2, 5, 10, 20, 30, 40 years) is a whole
number — so every bond's own cash-flow times are an exact subset of
`{0.5, 1.0, 1.5, ..., 40.0}` years. The result: all six bonds' first
coupons land on the *same* calendar date, and every shorter bond's
payment dates are a subset of the 40-year bond's own 80-date grid. This
is checked directly, not assumed —
`test_first_payment_date_aggregates_every_bonds_own_first_coupon`
confirms the very first ladder row already sums contributions from all
six bonds, not one.

---

## 4. The real cross-check: total present value equals the portfolio's own price

The clearest possible validation that this module's present-valuing is
correct: sum every discounted cash flow across the whole ladder, and
compare against `price_portfolio`'s own weighted clean price — two
structurally independent computations of the same underlying quantity (a
bond's price *is*, by definition, the sum of its discounted future cash
flows; this module just keeps each one visible individually instead of
summing them inside `price_bond`).

**Measured, against the committed snapshot curve** (`prefer_live=False`,
reproducible):

```
Total present value (this module):        99.326949
Portfolio-weighted clean price (Phase 2B): 99.326949
```

Agreement to `rel=1e-9` — effectively exact, as expected, since both
numbers are built from the same underlying discount factors
(`discount_factors_at`, §1), just combined in two different places.
`test_total_present_value_equals_the_portfolios_weighted_clean_price`
checks this directly. A second, curve-independent invariant is checked
too: total nominal principal must equal `sum(weight * face_value)`
across the portfolio exactly, since every bond redeems its own face value
exactly once — for this portfolio (all `face_value = 100`, weights
summing to 1.0), that's exactly `100.0`.

---

## 5. The finding: cash concentrates at redemption; risk concentrates further, in present-value terms

**Measured, against the committed snapshot curve, default portfolio:**

```
Total nominal cash flow (per 100 face value, portfolio-weighted): 146.7500
Total present value:                                               99.3269
Share landing at/beyond 20Y -- nominal: 36.5%   present value: 20.1%
```

The chart (`outputs/cash_flow_ladder_nominal.png`,
`outputs/cash_flow_ladder_pv.png`) makes the shape immediately visible:
six sharp spikes — one at each bond's own redemption date — sitting on a
thin, steadily shrinking floor of coupon-only payments between them. The
20Y, 30Y, and 40Y bonds' redemptions are visually the three tallest
spikes in the nominal chart.

**The comparison worth stating plainly:** **36.5%** of this portfolio's
*nominal* cash flow lands in the ultra-long (20Y+) segment — close to,
though not identical to, the **~40% portfolio weight** Phase 3C's own
doc reports for that segment (the two numbers answer different questions:
portfolio weight is a snapshot of today's *value* allocation; nominal
cash flow share adds up *every future dollar*, coupon and principal
alike, weighted by how many payments a bond makes — a longer bond makes
more coupon payments in absolute count, which pulls its own nominal share
up somewhat past its value weight). But in **present-value** terms, that
same ultra-long share drops to **20.1%** — telling a real, different
story: a far-future payment is discounted far more heavily than a near
one, so the ultra-long segment's *cash* looks far less dominant once
brought back to today's money than its **~51–56% share of the
portfolio's interest-rate risk** (Phase 3C, Phase 4C) would suggest.

**Why these three numbers (40% value weight, ~51–56% risk share, 20%
PV-weighted cash share) don't have to agree, and don't:** they are three
different lenses on the same six bonds. Value weight is a snapshot;
risk share (KRD/DV01) measures *sensitivity*, which — per Phase 3C's own
finding — grows faster than value weight with maturity (a longer bond's
duration grows faster than its price); present-value cash share measures
*how much of the portfolio's actual worth traces back to a payment that
far out*, which — per this module's own finding — shrinks with maturity,
because discounting works in the opposite direction to duration's own
growth. All three are real, correctly computed, and telling
complementary parts of the same story: **the portfolio's value today
depends most on payments that, individually, are not the largest ones
in nominal terms** — the near-term coupons — while its *risk* depends
most on the bonds whose *sensitivity* is largest, which are the longest-
dated ones, even though their actual present-value contribution to
today's price is comparatively modest.

---

## 6. Charting: annual buckets, not exact dates

`plot_cash_flow_ladder()` buckets by **calendar year**, not by each exact
payment date, for legibility — the portfolio's longest bond alone has 80
semiannual payment dates over 40 years, and a bar per exact date would be
too dense to show the *shape* the phase brief actually asks for ("where
cash flows concentrate"). Annual buckets are the standard convention for
a cash flow ladder chart for exactly this reason. The underlying
`result.ladder` DataFrame keeps full date-level granularity regardless —
aggregation to years happens only inside the chart function, for display,
never upstream in `compute_cash_flow_ladder()` itself.

**Colors, validated via the dataviz skill.** Coupon and principal are a
categorical (identity) distinction, not a polarity one — the fixed
categorical pair (blue `#2a78d6`, orange `#eb6834`, slots 1–2 of the
project's validated default palette) is used, run through the skill's
palette validator (`validate_palette.js`) and confirmed to pass every
adjacency check (worst adjacent CVD ΔE 24.7, well above the ≥8 target) —
appropriate here since the two categories are drawn as touching, stacked
bar segments, exactly the "adjacent" pairing the validator checks.

---

## 7. Known limitations (for the SR 11-7 validation report)

**7.1 Cash flows are portfolio-weighted, not a real position size (§2).**
Same disclaimer as every other portfolio-level currency figure in this
project (Phase 3B §1.1) — this is not real cash for a real book.

**7.2 Calendar dates are a synthetic approximation** (§3), built from
`DAYS_PER_YEAR = 365.25` rather than real per-bond coupon dates — the
same limitation Phase 4.6A's accrued interest already carries, for the
same reason (the illustrative portfolio has no real coupon dates to be
more precise than).

**7.3 The chart's annual bucketing is a display choice** (§6) — a reader
needing exact payment dates should read `result.ladder` directly rather
than the chart, which deliberately trades date-level precision for
legibility.

**7.4 Inherits `price_bond`'s own curve-interpolation and rate-quotation
simplifications** (Phase 2B §3.2) — every present-value figure here is
only as accurate as the curve discounting it's built on.

**7.5 The 40%-weight / ~51–56%-risk / 20%-PV-cash-share comparison (§5)
is illustrative-portfolio-specific.** A different portfolio's coupon
structure or maturity mix could shift any of the three numbers
independently — the *qualitative* relationship (risk share > value
weight > PV cash share, for this portfolio's specific ultra-long tilt) is
not a general law.

---

## 8. What would change this design

**Real per-bond coupon dates**, once a real-bond portfolio exists (Phase
2A §2), would let this module read them directly instead of deriving a
synthetic calendar from `DAYS_PER_YEAR` — the same additive change named
in Phase 4.6A's own doc (§7), benefiting both modules together.

**A finer (quarterly or monthly) chart bucketing**, if ever needed, would
be a parameter on `plot_cash_flow_ladder` (a `bucket` argument controlling
the `.dt.year` grouping key) — additive, not a rewrite of the aggregation
logic in `compute_cash_flow_ladder`, which already reports at full date
granularity regardless.

---

## 9. Relationship to the fallback re-anchoring policy and earlier phases

This module adds no new hardcoded market or portfolio data — it computes
directly from whatever `load_jgb_curve()` / `load_portfolio()` return,
inheriting those loaders' re-anchoring policies rather than adding a new
one. It reuses `models.ultra_long_profile.DEFAULT_ULTRA_LONG_THRESHOLD_YEARS`
directly (Phase 3C) rather than choosing a second, independent
ultra-long cutoff, so "ultra-long" means the same thing across both
modules.
