# Phase 2B Documentation — `models/bond_pricing.py`

## In plain English

A bond is essentially a loan: you hand over money today, and get a series
of interest payments over time plus your money back at the end. This part
figures out what that whole series of future payments is worth in today's
money, using current interest rates. The core idea: a payment further in
the future is worth less today than the same-sized payment arriving
sooner. This calculates exactly how much less, for every payment a bond
makes, then adds them all up to get a fair price.

---

## Technical details

Values a bond as the sum of its cash flows, each discounted at the
interest rate for *that specific payment's own* timing — not one flat
rate applied to the whole bond. Reads the portfolio and curve through the
Phase 2A and Phase 1 loaders; never hardcodes either.

**File:** `models/bond_pricing.py` — `curve_yield_at()` (rate lookup
between/beyond the curve's known points), `price_bond()` (one bond),
`price_portfolio()` (many bonds at once).

**Output contract:** `price_bond(face_value, coupon_rate, maturity_years,
curve, freq=2) -> float`, price per `face_value` of face amount.
`price_portfolio(portfolio, curve, freq=None) -> pd.DataFrame`, one row
per bond. A portfolio-level weighted price is `(df.weight *
df.price).sum()`.

---

## 1. What each piece does, and why

### 1.1 `curve_yield_at(curve, maturity_years)`

Looks up the interest rate for any maturity, even one the curve doesn't
quote directly — straight-line interpolation between the two nearest
known points. For a maturity *beyond* the curve's range (shorter than its
shortest quote, or longer than its longest), the rate is held flat at
whatever the nearest quoted point says, rather than continuing whatever
slope the curve had at the edge.

**Why flat, not a continued slope:** continuing a slope indefinitely can
produce an implausible or even negative rate the further out it's pushed.
Holding flat can't do that — every rate this function returns is one the
curve actually quoted somewhere. This isn't a rare edge case here either:
the live/cached curve's shortest quote is 1 year, but every bond's first
interest payment lands at 6 months — so this flat-rate rule fires on the
very first payment of every bond in the portfolio whenever that data
source is used.

This function also makes no assumption about how many rates the curve
has, or which maturities they're at — it reads whatever's there. Assuming
a fixed set would silently break the moment the data source changed.

### 1.2 `price_bond(face_value, coupon_rate, maturity_years, curve, freq=2)`

Builds the schedule of interest payments (twice a year, by default,
counted backward from the maturity date so the final payment lands
exactly there), looks up the rate for each payment's own timing, and
discounts and sums them.

**Why counted backward from maturity:** real bond payments are scheduled
relative to the redemption date, not from today — so building the
schedule backward from maturity is the realistic anchor, not an arbitrary
choice. A maturity that isn't an exact multiple of the payment interval
(e.g. derived from a real date rather than a round number) just gets one
shorter first payment period rather than an error — this keeps a future
real-bond portfolio pricing correctly with no special-casing needed.

**Why the discount rate is the curve's own quoted rate, applied directly**
— not a more rigorously derived one: this is a standard simplification
for this kind of tool, not an oversight. It's precise enough for the
purpose here, but a reviewer should know it isn't the same as a
fully rigorous derivation (§3.2).

### 1.3 `price_portfolio(portfolio, curve, freq=None)`

Prices every bond in a portfolio against one curve, returning one row
each. Takes both as plain inputs rather than loading them itself, so it's
easy to test and easy to reuse against a curve that's about to be modified
(needed by Phase 3's sensitivity calculations, which reprice the same
bond many times against slightly changed curves).

**`freq=None` prices each bond at its own `Bond.freq`** (Phase 2A
addition) rather than one frequency shared by the whole portfolio — a
portfolio can mix payment frequencies across bonds, and each one prices
correctly at its own. Pass an explicit `freq` to override every bond to
that one shared frequency instead; this is still needed in at least one
place — `models/zero_curve_impact.py`'s par-vs-zero comparison requires
every bond discounted at the *same* frequency its zero curve was
bootstrapped with, so per-bond freq would be wrong there specifically
(that module's own docstring has the reasoning). Every other
`_portfolio`-suffixed function downstream of this one (Key Rate Duration,
DV01, the cash flow ladder, bond analytics, factor exposure) follows the
same `None` = per-bond, explicit-int = override convention.

---

## 2. Why these specific tests

Three kinds of check, each catching a different way this could silently
go wrong: an **analytic check** against the textbook bond-price formula
(catches a scheduling or discounting bug, independent of curve shape); a
**flat-extrapolation check**, including one that proves a straight-line
continuation would have given a meaningfully different (and wrong) answer
— so the test can't pass by accident; and a **genuine cross-check across
both real data-source shapes** (the 12-point and 15-point curve grids),
each verified against an independently computed expected price, not just
"it ran without an error."

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 No accrued interest or settlement date.** This computes a "clean"
price as of today — no partial-period interest owed, no settlement-date
offset. Matches the same boundary Phase 2A already drew (its `issue_date`
field is stored but never used).

**3.2 The discount rate is the curve's own quoted rate, used directly —
not a more rigorously derived one.** A more rigorous approach would first
derive an implied rate specific to each maturity from the quoted curve,
then discount with that instead — the two can diverge for a curve with
real curvature, more so at longer maturities. This was a deliberate,
specified choice for this phase, not something discovered missing
afterward — flagged here and in the code so a reviewer knows exactly what
precision to expect from an absolute price.

**Resolved, in part (Phase 4.5).** A bootstrapped zero curve now exists
(`models/bootstrap.py`, `docs/phase_4_5a_documentation.md`) and, as of
Phase 4.5C, `price_bond` can discount against it directly — pass a
`bootstrap_zero_curve()` result instead of a par curve and it's
auto-detected (`docs/phase_4_5c_documentation.md` §1). **The par-curve
basis remains the default**, unchanged, for every existing caller; the
zero-curve basis is opt-in. The quantified difference between the two
(Phase 4.5C §2) is real and, at the long end of this project's
portfolio, large — up to ~770bp of price on the 40Y bond — confirming
this was not a cosmetic simplification.

This resolution is itself qualified, not absolute: the zero curve was
built by treating MOF's published curve as if it were a par curve, which
checking against MOF's own methodology found it is not — MOF publishes a
fitted yield-to-maturity curve on real benchmark issues, not a
constructed par curve (`docs/phase_4_5a_documentation.md` §1). So this
resolution trades the original limitation for a smaller, measured one
(the "coupon effect," quantified in that doc's §5), not a full
elimination of every curve-based limitation. Not deleted here since the
history (this was a named, deliberate Phase 2B simplification, not an
oversight) is worth keeping.

**3.3 Flat extrapolation is a policy, not a market forecast.** It keeps
any rate this function invents plausible, but it isn't a claim that real
rates are actually flat beyond the curve's quoted range — the true rate
out there is genuinely unknown from this data.

---

## 4. What would change this design

**A more rigorously derived discount rate**, if ever built, would slot in
as one new function between the curve loader and `price_bond` — nothing
in `price_bond` itself would need to change, since it already just looks
up whatever curve it's handed.

**Settlement-date and accrued-interest modeling** would need a settlement
date as an input and a day-count convention — not built now; see §3.1.

---

## 5. Relationship to the fallback re-anchoring policy

This module adds no new hardcoded or fallback data of its own — it's pure
pricing logic reading the curve and portfolio through the existing
loaders. Phase 1's re-anchoring policy has nothing new to govern here.
