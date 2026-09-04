# Phase 3A Documentation — `models/key_rate_duration.py`

## In plain English

"How much does this bond's price move when interest rates change?" is
usually answered with one number ("duration"). But short-term rates and
long-term rates don't always move together — they can move fairly
independently. This part breaks that single number down by loan length:
exactly how much of this bond's — or portfolio's — risk comes from
short-term rate moves, versus medium, versus long. That matters a lot for
Japanese government bonds specifically, since very long-term rates
(20–40 years) are known to behave differently from the rest, driven by
large institutional buyers concentrated at that end of the market.

---

## Technical details

Key Rate Duration (KRD): per-tenor price sensitivity. For each interest
rate on the curve, nudge it up and down by one basis point (0.01%), hold
every other rate fixed, reprice the bond both ways, and measure the
resulting % price change. Reads the portfolio and prices via the Phase 2A
and Phase 2B loaders; reuses `price_bond`'s existing rate-lookup logic
without modification.

**File:** `models/key_rate_duration.py` — `key_rate_duration_bond()`
(one bond's KRD), `effective_duration_bond()` (a whole-curve-shift
duration, used to sanity-check KRD), `key_rate_duration_portfolio()`
(portfolio-level version).

**Output contract:** `key_rate_duration_bond(face_value, coupon_rate,
maturity_years, curve, freq=2, bump_size=0.0001) -> pd.Series`, one KRD
value per curve tenor. `effective_duration_bond(...) -> float`, one
number. `key_rate_duration_portfolio(...) -> pd.DataFrame`, one row per
bond plus a `portfolio_total` row, one column per tenor.

---

## 1. What each piece does, and why

### 1.1 The bump shape: a tent, and why it's automatic rather than coded

```
KRD_k = -(1/P_base) * (P_up_k - P_down_k) / (2 * bump_size)
```

Nudging one interest rate up (or down) and repricing doesn't just affect
that one rate — because `price_bond` looks up rates by straight-line
interpolation between the two nearest quoted points, moving *one* point
also shifts every maturity between it and its neighbors, tapering
linearly to zero exactly at each neighbor. The result is a **triangular**
sensitivity shape ("tent"), not a sharp, isolated spike — and this wasn't
built as a separate rule; it falls straight out of reusing the existing
interpolation logic unmodified. The alternative (affecting *only* the
exact bumped maturity, nothing else) was rejected on principle: KRD
should measure sensitivity to the same rate model the bond is actually
priced against, not a different one invented just for this metric.

At the two ends of the curve, the tent has one sloped side and one flat
side instead of two sloped sides — because rates beyond the curve's
quoted range are held flat at the nearest known rate, so bumping an end
point moves every maturity beyond it by the full amount, not a tapering
one.

**Why this matters for summing across tenors:** at any given maturity,
the tents from every tenor add up to exactly 1.0 — verified directly, not
assumed. That's what makes "the sum of a bond's KRDs across all tenors"
meaningfully approximate its overall (whole-curve) duration (§2) — a
property the rejected alternative (an isolated spike) would not have had,
since a payment landing between two grid points would then be invisible
to every single-tenor bump.

### 1.2 Two-sided vs. one-sided bumping

A one-sided bump (nudge up only, compare to the unbumped price) was the
more obvious first choice, but was replaced with the two-sided version
above: a one-sided estimate carries a larger, avoidable numerical error
for the same bump size, while a two-sided one cancels most of it for one
extra reprice. Both `key_rate_duration_bond` and `effective_duration_bond`
(§1.3) use the same two-sided method deliberately — the sanity check in
§2 only cleanly isolates a bond's real convexity if both sides of the
comparison use an identical method; mixing the two would blur a genuine
effect together with a numerical-method mismatch.

### 1.3 `effective_duration_bond(...)`

The same two-sided approach, but shifting *every* tenor at once — a true
whole-curve move. Exists purely as the independent number `key_rate_duration_bond`'s
tenor-by-tenor sum is checked against (§2); it's not a new pricing
calculation, just `price_bond` called on two uniformly shifted curves.

### 1.4 `key_rate_duration_portfolio(...)`

Runs `key_rate_duration_bond` once per holding and combines the results
into one table, plus a portfolio-level total row equal to each bond's
weight times its own KRD, summed — the same weighting already used for a
portfolio-level price (Phase 2B).

### 1.5 `__main__`

Prints the full KRD table and, for every bond, the §2 sanity check —
making a future bug in the tent-shape or two-sided logic visible on any
real run, not only inside the automated tests.

---

## 2. The sanity check: does summed KRD match overall duration?

A real run against the live MOF curve:

| Bond | sum(KRD) | overall duration | gap |
| --- | --- | --- | --- |
| JGB_2Y | 1.968 | 1.968 | ~0 |
| JGB_5Y | 4.778 | 4.778 | ~0 |
| JGB_10Y | 8.916 | 8.916 | ~0 |
| JGB_20Y | 14.388 | 14.388 | ~0 |
| JGB_30Y | 17.432 | 17.432 | ~0 |
| JGB_40Y | 19.441 | 19.441 | ~0.00001 |

The two nearly match, and the tiny remaining gap has a specific, expected
cause rather than being a bug: bond prices don't move in a perfectly
straight line as rates change (that curvature is a real, well-known
property called convexity) — so summing many small, separate rate moves
isn't *quite* identical to one combined move. That effect grows with
maturity, matching the pattern above exactly (near zero at 2 years, a
few thousandths of a percent at 40). Switching to the two-sided method
(§1.2) already removes the larger, avoidable part of this gap; what's
left is the real, small effect described here.

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 Inherits the pricing engine's rate-curve simplification.** KRD is
computed on top of `price_bond`, which discounts each payment at the
curve's directly-quoted rate rather than a more rigorously derived one
(Phase 2B §3.2) — worth restating here specifically, since a duration
number invites a sharper reading than a price does. Read any KRD figure
in this project as the sensitivity of that same approximation, not of a
theoretically exact rate.

**3.2 KRD's tenor set isn't fixed.** Same as everywhere else in this
project — the number and location of KRD values depends on which data
source served the curve. A "10-year KRD" from two different runs is only
comparable if both runs happened to use a source quoting 10 years
directly.

**3.3 The sum-vs-overall-duration gap isn't exactly zero.** Small (a few
thousandths of a percent at 40 years, §2), well understood, and immaterial
next to every other approximation already in this project — but a
consumer that needs one exact duration number should call
`effective_duration_bond` directly rather than summing the KRD table and
treating that as precise.

---

## 4. What would change this design

If the pricing engine's rate-curve simplification (§3.1) were replaced
with a more rigorous one, this module would need no changes at all — it
already just bumps whatever curve it's handed and reprices.

---

## 5. Relationship to the fallback re-anchoring policy

This module adds no new hardcoded market data — its one constant (the
0.01% bump size) is a methodology choice, not an observation of anything
that moves. Nothing new for Phase 1's re-anchoring policy to govern.
