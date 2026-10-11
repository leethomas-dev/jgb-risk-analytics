# Phase 4.6A Documentation — settlement, day count, clean/dirty price (`models/day_count.py`, `models/bond_pricing.py` extended)

> **Special Phase A (2026-09-27):** figures in this doc were computed on
> the par discounting basis in force when this phase was built. The
> project now discounts on the bootstrapped zero curve by default; current
> values are in [`special_phase_a_documentation.md`](special_phase_a_documentation.md) §6.

> **Day count corrected (2026-10-11):** accrued interest was labelled
> ACT/365 but computed coupon-per-period × days ÷ days in the period,
> which is Actual/Actual ICMA arithmetic (the US/euro government-bond
> method), not Japan's. It now computes annual coupon × days ÷ 365, the
> JGB convention and the formula Special Phase B's JSDA check already
> used. Dates-to-years conversion moved from 365.25 to 365 at the same
> time (Phase 2A). See §3. No figure in this doc changes: every accrued
> figure shown is at the default settlement date, where accrued is zero.

## In plain English

Every price this project has computed so far has been a "clean" price —
what a bond is quoted at, ignoring the fact that whoever holds a bond
between its interest payments is owed a slice of the next payment for the
days they've held it. This part adds that missing piece: given a date
someone actually pays for the bond ("settlement"), how many days has
interest been quietly building up since the last payment, and how much is
that worth? It answers using the exact counting rule Japanese government
bonds use for this (real calendar days, divided by 365), and adds that
amount to the clean price to get the "dirty" price — the number that's
actually paid. Along the way it deals honestly with a few edge cases: what
happens on the day a coupon is paid, what happens for a bond that hasn't
paid its first coupon yet, and one specific question — a short window
some bond markets have where a seller keeps the next coupon rather than
the buyer — that this project could not confirm applies to JGBs from a
primary government source, so it says so plainly and states the
assumption made instead.

---

## Technical details

Adds settlement-date-aware accrued interest and clean/dirty pricing on top
of the existing, untouched pricing engine. `price_bond()` (Phase 2B) is
unchanged — signature and behavior both — and continues to return what
this phase now documents explicitly as the bond's **clean** price. Two new
functions in the same file build on it: `accrued_interest()` and
`dirty_price()` (`= price_bond() + accrued_interest()`), plus portfolio-
level versions of both. A new small module, `models/day_count.py`,
supplies the day count convention (Actual/365) both new functions use.

**Files:** `models/day_count.py` (new) — `day_count()`, `year_fraction()`.
`models/bond_pricing.py` (extended) — `_cash_flow_schedule()` (factored
out of `price_bond`, unchanged behavior), `_coupon_boundaries()`,
`accrued_interest()`, `dirty_price()`, `accrued_interest_portfolio()`,
`dirty_price_portfolio()`.

**Output contract:** `accrued_interest(face_value, coupon_rate,
maturity_years, freq=2, valuation_date=None, settlement_date=None,
day_count_convention="ACT/365") -> float`. `dirty_price(..., curve, ...) ->
float`. Both portfolio versions return a `pd.DataFrame`, one row per bond,
no total row — the same shape convention `price_portfolio` already uses.

**The critical constraint, met exactly as required:** every pre-existing
test still passes, unchanged.

```
Before this phase: 261 tests passing
After:              284 tests passing (23 new: 8 in tests/test_day_count.py,
                    15 in tests/test_bond_pricing.py)
```

Not one of the 261 pre-existing tests was edited. This isn't a coincidence
— it's a direct consequence of the main design decision in §1 below:
`price_bond` itself gained zero new parameters and zero new code paths.

---

## 1. The main design decision: two new functions, not a new parameter on `price_bond`

The phase brief asked for settlement date support with "all existing
tests passing unchanged" as a non-negotiable constraint. Two designs were
available:

1. **Add `settlement_date` (and friends) as new optional parameters on
   `price_bond` itself**, defaulting to reproduce today's behavior.
2. **Leave `price_bond` completely untouched**, and add settlement-aware
   accrued interest and dirty pricing as new, separate functions that call
   it.

This phase uses (2). `price_bond` is called directly, by name, from six
other modules already (`key_rate_duration.py`, `dv01.py`,
`ultra_long_profile.py`, `factor_exposure.py`, `models.bootstrap`,
`models.curve_fitting`'s zero-curve-basis integration) — every one of
them passes a fixed, small set of positional/keyword arguments that
doesn't include a settlement date. Option (1) would have been safe in
principle (a new parameter with a safe default doesn't break an existing
call site), but option (2) is safer in a stronger sense: it makes "zero
behavior change to `price_bond`" a structural fact instead of a
consequence of choosing the right default value, matching the same
verify-by-construction preference this project used for Phase 4.5C's
column-auto-detection design (`docs/phase_4_5c_documentation.md` §1.1) —
there, adding a new code path only inside an `if "zero_rate" in
curve.columns` branch meant every existing "yield"-column caller was
provably unaffected. Here, not touching `price_bond` at all is the same
idea taken one step further.

The practical cost of this choice is that a caller who wants a dirty
price does `dirty_price(...)` instead of `price_bond(..., settlement_date=...)`
— a slightly less compact call, in exchange for the stronger guarantee.

---

## 2. Day count convention: Actual/365 — verified, with one caveat disclosed

**What was checked.** The phase brief specifically asked to verify the
JGB day count convention rather than assume it, and to say so if it
couldn't be confirmed. A primary MOF or JSDA document spelling out the
exact accrued-interest day-count formula for secondary-market JGB trades
could not be located in the time available (the MOF page consulted for
Phase 4.5A's own methodology check, `docs/phase_4_5a_documentation.md`
§1, covers curve construction, not accrued interest). What **was** found,
independently, across multiple secondary industry references (day-count
convention glossaries and a fixed-income systems vendor's own
documentation), is a specific, named convention — "Actual/365 (Japanese)"
— consistently described as: count real calendar days between two dates
(no 30-day-month approximation), and divide by a **fixed** 365, never 366,
even when the span crosses a leap day. This is the same rule commonly
called "Actual/365 Fixed" elsewhere, and it is genuinely different from
"Actual/Actual (ISDA)", which splits a leap-year-crossing span into a
366-denominator leap portion and a 365-denominator non-leap portion.

**What this module implements**, per that description: `models/day_count.py`'s
`ACT_365` convention counts actual calendar days
(`day_count()`) and divides by a constant 365 (`year_fraction()`) —
matching the description found, and matching the phase brief's own
instruction ("JGBs use Actual/365 — implement that"). This is disclosed
as **verified against secondary industry sources, not a primary MOF/JSDA
document** — a real distinction from the primary-source verification
Phase 4.5A achieved for MOF's curve methodology (`docs/phase_4_5a_documentation.md`
§1), and worth a validator's attention for that reason.

**Why not Actual/Actual.** Two variants exist, and neither is Japan's:
- **ISDA** (days in a leap year ÷ 366, other days ÷ 365) is a swap
  convention, not used for JGBs.
- **ICMA** (coupon per period × days ÷ days in the period) is the
  government-bond convention in e.g. the US and euro area. This module
  used ICMA's arithmetic by mistake until 2026-10-11 (§3).

**Structured for one convention, extensible to more.** `day_count.py` is a
small registry keyed by convention name (`_YEAR_LENGTH_DAYS`), specifically
so a second convention (30/360, Actual/Actual ICMA) could be added later —
one function, one registry entry — without changing either public
function's signature or any caller. Only ACT/365 is implemented, since
it's the only one this project needs; the brief was explicit not to build
conventions not required.

**Two outputs.** `day_count.py` exposes `day_count()` (actual days between
two dates) and `year_fraction()` (days ÷ 365). `accrued_interest()` uses
`year_fraction()` (§3).

---

## 3. Accrued interest: the formula

```
accrued = face_value * coupon_rate * days_elapsed / 365
```

`days_elapsed` is actual calendar days since the current period started.
The denominator is a fixed 365, not the period's own length, so accrued
on the last day of a 183-day period is slightly **more** than one
period's coupon (183/365 > 1/2). That is the convention, not an error.
This is the same formula `models/par_error_check.py` uses against real
JSDA prices (Special Phase B).

**Correction, 2026-10-11.** As first built, this phase followed the
phase brief's wording (*"coupon amount prorated by days elapsed since the
last coupon date, over days in the coupon period"*) and computed:

```
accrued = (face_value * coupon_rate / freq) * (days_elapsed / days_in_period)
```

That is Actual/Actual ICMA arithmetic: the US/euro government-bond method,
not Japan's. It was labelled ACT/365 throughout, and the original version
of this section defended it as "the standard bond-market accrual
convention". The two formulas differ by at most about 0.3% of the accrued
amount (182 or 183 days per period against 182.5), a few thousandths per
100 of price. It was found by checking the label against the arithmetic
while preparing to explain the convention, not by a failing test.
`test_accrued_interest_divides_by_365_not_by_the_period_length` now pins
the convention.

**The coupon calendar accrued interest runs on.** Real coupon dates don't
exist anywhere in this project's data — the illustrative portfolio
(`config/portfolio.json`) gives only `maturity_years`, never a real
`issue_date`/`maturity_date` (Phase 2A, §2 of its own doc). `_coupon_boundaries()`
builds a synthetic-but-consistent calendar directly from
`price_bond`'s own cash-flow schedule (`_cash_flow_schedule`, factored out
of `price_bond` unchanged specifically for this reuse): each cash-flow
time (a year offset from `valuation_date`) becomes a calendar date via
`valuation_date + round(t * DAYS_PER_YEAR)` days, using the same
`DAYS_PER_YEAR = 365` (ACT/365; 365.25 before 2026-10-11) that
`config/portfolio_loader.py` uses for the reverse conversion (a real
`maturity_date` → years remaining, Phase 2A §2). Coupon periods are
therefore 182 or 183 days by construction, not the real ~181–184 day
range a genuine calendar would produce, and over 30 years the synthetic
dates fall about 8 days earlier than the same calendar day. Disclosed in
§6 as a bounded approximation, not a precision claim.

---

## 4. `valuation_date` vs. `settlement_date` — two dates, one default

`accrued_interest()` and `dirty_price()` take both:

- **`valuation_date`** — the date `maturity_years` is measured *from*.
  Defaults to today, the same default `config.portfolio_loader.load_portfolio()`'s
  own `valuation_date` parameter uses (Phase 2A). A caller mixing this
  module with a portfolio loaded against a *different*, pinned
  `valuation_date` should pass that same value here — documented directly
  in `accrued_interest`'s own docstring as a caller responsibility, since
  nothing in this module can detect that mismatch on its own (there is no
  link back to whatever `valuation_date` a portfolio's `maturity_years`
  was actually resolved against).
- **`settlement_date`** — the date accrued interest is measured *as of*.
  Defaults to `valuation_date` — the phase brief's own instruction
  ("optional parameter defaulting to the valuation date"). At that
  default, `accrued_interest()` returns exactly `0.0`: the cash-flow
  schedule already treats `valuation_date` as a coupon date (§3), so
  there's nothing yet to accrue. This is *why* `price_bond`'s own output
  can be documented as the clean price without any code change to it
  (§1) — clean and dirty coincide exactly at the default, by construction.

---

## 5. Coupon-date edge cases — handled explicitly, not by accident

**Settlement exactly on a coupon date.** Coupon periods are treated as
half-open (`[period_start, period_end)`). Landing exactly on a boundary
always means "a fresh period just started" — accrued resets to `0.0` —
never "the previous period just ended," which would otherwise disagree by
a full period's coupon. Checked directly, not just at the first boundary:
`test_accrued_is_zero_exactly_on_a_later_coupon_date` uses the *third*
coupon date of a 2-year bond.

**Settlement before a newly-issued bond's first coupon.** In this
project's schedule, `valuation_date` (boundary 0) is *always* before the
first coupon (`_cash_flow_schedule`'s own construction — the first cash
flow lands at `t > 0`) — so this "newly issued, no coupon paid yet" case
is actually the general, everyday case for period 0, not a rare one.
Handled by the same period-finding loop as every other period, not a
special branch — `test_accrued_before_the_first_coupon_of_a_newly_issued_bond`
confirms it directly.

**Settlement at or beyond maturity.** Exactly on the maturity date returns
`0.0` (the final coupon/redemption day, treated the same as any other
boundary). Strictly after it raises `ValueError` — settling a matured
bond isn't a case this function is willing to guess an answer for.

**An ex-coupon (ex-dividend) period — NOT implemented, and disclosed as an
unverified assumption, per the phase brief's own instruction.** Several
bond markets (UK Gilts among the best-known) have a short window
immediately before a coupon date during which a buyer settling in that
window does *not* receive the upcoming coupon — it stays with the seller
— which flips the sign of accrued interest for a few days each period. A
real, deliberate search was made for whether JGBs have an equivalent
convention (Ministry of Finance and JPX/JSCC pages and PDFs, general web
search for "JGB ex-coupon"/"ex-dividend"/"record date"); no primary
source stating either way was found. **The assumption made instead, used
by this module:** JGBs trade cum-coupon throughout the period — no
ex-coupon window, accrued interest never goes negative or resets early.
This is not a guess pulled from nowhere — it's consistent with (a) Japan's
JGBs settling through a dematerialized, real-time DVP book-entry system
(the Bank of Japan's JGB Book-Entry System), which removes the
paper-certificate/registrar "books closed" period that historically
motivated ex-dividend windows in markets like UK Gilts, and (b) most other
electronically-settled sovereign bond markets (e.g. US Treasuries) sharing
this same cum-coupon-throughout convention. But it is an inference from
general market-structure reasoning, not a confirmed fact about JGBs
specifically, and it is flagged as exactly that — both here and in
`accrued_interest`'s own docstring — rather than silently built in.

---

## 6. Known limitations (for the SR 11-7 validation report)

**6.1 The day count convention is verified against secondary sources, not
a primary MOF/JSDA document (§2).** A real, disclosed gap — different in
kind from Phase 4.5A's curve-methodology verification, which did reach a
primary source.

**6.2 The ex-coupon question is an unverified assumption, not a confirmed
fact (§5).** If JGBs do have an ex-coupon window this project's search
didn't surface, accrued interest computed by this module would be wrong
in sign for trades settling inside that window, each period.

**6.3 The coupon calendar is synthetic, built from `DAYS_PER_YEAR = 365`,
not real per-bond coupon dates (§3).** The illustrative portfolio has no
real coupon dates to be more precise than; this matches Phase 2A's
reverse conversion rather than adding a new source of imprecision. A real-bond portfolio (Phase 2A §2, not built yet) would
supply real coupon dates this module could use directly instead.

**6.4 `valuation_date` consistency between this module and a loaded
portfolio is a caller responsibility, not enforced in code (§4).** Passing
mismatched dates to `load_portfolio(valuation_date=...)` and to
`accrued_interest(..., valuation_date=...)` desyncs the two silently.

**6.5 Only ACT/365 is implemented.** `day_count_convention` is a genuine
parameter, but passing anything else raises — no other convention has
been built, since none is needed yet (§2).

**6.6 Whether JGB accrual counts 29 February is unverified.** This module
counts it (a settlement spanning 29 February accrues one more day).
Secondary sources disagree on whether the Japanese convention does; no
primary MOF/JSDA source was found (checked 2026-10-11). The effect is at
most one day's accrual, coupon ÷ 365 (about 0.01 per 100 at a 3.5%
coupon), once every four years. Confirm against a JSDA primary source
before the SR 11-7 report.

---

## 7. What would change this design

**A confirmed primary source on JGB ex-coupon conventions**, if found
later, would settle §5's assumption one way or the other — implemented as
a bounded window check inside `accrued_interest` (return `0.0`, or the
negative of the upcoming coupon, when `settlement_date` falls inside it),
without changing the function's signature.

**A second day count convention** (30/360, Actual/Actual ICMA) would be
one function plus one `_YEAR_LENGTH_DAYS`-style registry entry in
`day_count.py` (§2) — no change to `accrued_interest`'s or `dirty_price`'s
own logic, both of which already take `day_count_convention` as a plain
parameter.

**Real per-bond coupon dates** (§6.3), once a real-bond portfolio exists
(Phase 2A §2), would let `_coupon_boundaries` read them directly instead
of deriving a synthetic calendar from `DAYS_PER_YEAR` — an additive
change (an "if real dates are present, use them" branch), not a rewrite.

---

## 8. Relationship to the fallback re-anchoring policy and earlier phases

This phase adds no new hardcoded market or portfolio data — `day_count.py`'s
`_YEAR_LENGTH_DAYS` and `bond_pricing.py`'s `DAYS_PER_YEAR` reuse (§3) are
methodology constants, not observations of anything that moves, so
there's nothing new here for Phase 1's re-anchoring policy
(`docs/phase_1_documentation.md` §5) to separately govern.

**Resolves a limitation named since Phase 2B.** Phase 2B's own doc
(`docs/phase_2b_documentation.md` §3.1) named "no accrued interest or
settlement date" as a known limitation, matching the same boundary Phase
2A drew for `issue_date` (its own doc, §3.3). This phase resolves it —
`price_bond` itself is unchanged (§1), so that limitation now applies only
to callers who don't also call `accrued_interest`/`dirty_price`, which is
every existing caller in this project by default (§1's "practical cost").
