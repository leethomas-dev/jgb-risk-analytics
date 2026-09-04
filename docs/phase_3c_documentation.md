# Phase 3C Documentation — `models/ultra_long_profile.py`

## In plain English

This part answers one question, in both percentage and currency terms:
"how much of our interest-rate risk sits in bonds with 20 years or more
left before they're paid back?" That matters a lot for Japanese
government bonds specifically, because the very-long end of the market
(20 to 40 years) has been a genuine, widely-discussed area of investor
attention — large institutions like insurers and pension funds buy
heavily there, and that shows up as real, distinct price behavior.
Knowing your *overall* risk isn't enough for a portfolio like this — you
need to know *where* it's concentrated. This part draws a line at 20
years, adds up how much risk sits on each side, and produces a chart so
the split is visible at a glance.

---

## Technical details

A focused view on the ultra-long (20Y+) segment of the curve — the
Japan-specific angle of this project, since ultra-long JGB demand is a
persistent, real market theme, and the illustrative portfolio (Phase 2A)
deliberately puts ~40% of its weight there to give this metric something
real to show. Computes no new sensitivity of its own — it splits the
portfolio-level totals Phase 3A and 3B already produce.

**File:** `models/ultra_long_profile.py` — `UltraLongProfile` (the
result), `compute_ultra_long_profile()`, `plot_ultra_long_profile()`.

**Output contract:** `compute_ultra_long_profile(portfolio, curve,
threshold_years=20.0, ...) -> UltraLongProfile`, a frozen result object
with the threshold used, every curve tenor, which ones counted as
ultra-long, the full per-tenor KRD/DV01, the totals, the ultra-long-only
totals, and two share properties (0 to 1). `plot_ultra_long_profile(profile,
output_path=..., metric="dv01") -> Path`, saving a PNG.

---

## 1. What each piece does, and why

### 1.1 The 20-year line: derived from the live data, never a fixed list

20 years is both this project's specified example and the conventional
cutoff used in JGB market commentary for "ultra-long" — matching the
illustrative portfolio's own design (its 20/30/40-year bonds exist
specifically to populate this segment).

Critically, *which* tenors count as ultra-long is computed fresh each
time from whatever rates the curve actually has — never a fixed list.
This matters concretely: this project's two real data sources return
different tenor sets. One classifies {20, 30, 40} as ultra-long; the
other, which also quotes 25 years, classifies {20, 25, 30, 40}. A
hardcoded list would have gotten one of the two wrong. `threshold_years`
is also an adjustable input, not a hardwired constant, so a reader can
re-run the split at a different cutoff.

### 1.2 `UltraLongProfile`

An immutable snapshot of one computation's full result — the threshold
used, every tenor, which ones counted as ultra-long, and the totals. The
ultra-long tenor set is stored explicitly (not left for a caller to
re-derive) because, per §1.1, it depends on which data source served the
curve and isn't otherwise knowable from the threshold alone. The two
share figures are computed on the fly from the stored totals rather than
stored themselves, so they can never silently drift out of sync with the
numbers they're based on.

### 1.3 `compute_ultra_long_profile(...)`

Calls Phase 3A's and 3B's existing portfolio-level functions once each,
takes their totals, and splits the tenor columns at the threshold. No
pricing or sensitivity math is reimplemented — this is a slice-and-sum
over numbers already computed and already validated elsewhere.

### 1.4 `plot_ultra_long_profile(...)`

A bar chart — one bar per tenor, colored blue below the threshold and red
at or above it — saved as a PNG (default: `outputs/ultra_long_dv01_profile.png`,
not checked into version control, the same treatment this project already
gives other machine-generated files). `matplotlib` is a new dependency
for this part of the project, run in a display-free mode since this
function only ever saves a file and never opens a window. The chart
defaults to the currency figure (DV01) rather than the percentage one
(KRD), since that's the more concrete number named in this part's own
requirements; either can be requested.

### 1.5 `__main__`

Prints which tenors counted as ultra-long, the full breakdown tables, the
chart's saved location, and the interpretive summary (§2) — the specific
number a risk committee would actually ask for, so it's visible on any
real run, not only recoverable by inspecting the code's output object.

---

## 2. The finding: ~40% weight, ~51% risk

A real run against the live curve and the default portfolio:

```
Total portfolio KRD:   10.20 years,  of which ultra-long: 5.21 (51.1%)
Total portfolio DV01:  0.0963,       of which ultra-long: 0.0493 (51.2%)
```

The portfolio's 20/30/40-year bonds hold **40%** of its *weight* but
account for **51%** of its *risk*. This is a real, explainable effect,
not a computation quirk: a bond's contribution to overall risk is its
weight multiplied by its own duration, and duration itself grows with
maturity — the 2-year bond's duration is about 2, the 40-year bond's is
about 19, roughly ten times larger for a bond with twenty times the
maturity. So putting equal *value* weight into a long bond and a short
bond never leaves the two contributing equally to *risk* — the longer
bond dominates. That's exactly what "40% weight, 51% risk" shows.

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 Inherits every limitation already named for KRD and DV01** — the
underlying rate-curve simplification, the non-fixed tenor grid (which
here directly changes *which tenors count as ultra-long*, not just which
columns a table has), and the illustrative (not real) portfolio data
behind the 40%/51% finding.

**3.2 The 20-year line is a convention, not a real market boundary.** A
bond at 19.9 years and one at 20.1 years are economically almost
identical; nothing in the actual market draws a sharp line between them.
`threshold_years` is adjustable specifically so a reader isn't locked
into one arbitrary cutoff.

**3.3 This module's own correctness rides entirely on Phase 3A/3B's
validation.** Since no new sensitivity math happens here, there's nothing
new to bump-and-reprice-check — what this module's own tests confirm is
that the split itself (which numbers land on which side of the line) is
computed correctly, independently re-checked against a fresh calculation
rather than the module's own logic run twice. A bug in the underlying
KRD or DV01 numbers themselves would have already been caught (or not)
upstream, before reaching this module.

---

## 4. What would change this design

**A configurable threshold in a future dashboard** needs no change here
— `threshold_years` is already an ordinary input.

**A more rigorous rate curve**, if built later, needs no change either —
this module only ever reads Phase 3A/3B's totals, whatever curve produced
them.

---

## 5. Relationship to the fallback re-anchoring policy

This module adds no new hardcoded market data — its one constant (the
20-year threshold) is a convention, not an observation of anything that
moves. Nothing new for Phase 1's re-anchoring policy to govern.
