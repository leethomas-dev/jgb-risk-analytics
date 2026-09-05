# Phase 4A Documentation — `data/jgb_curve_history_loader.py`

## In plain English

Every part of this project so far has worked with *today's* interest
rates. This part goes back in time: it loads years of daily Japanese
government bond rates instead of just one day's, which the next part
(Phase 4B) needs to study how the whole curve tends to move. The
Japanese government has published a daily rate sheet since 1974, but
it hasn't always published the same set of loan lengths — the 40-year
bond, for example, didn't exist before 2007, so there's simply no rate
to report for it before then. This part decides, plainly and out loud,
which loan lengths and which date range are safe to use together, so
nothing downstream ends up comparing a real rate to one that was
quietly invented to fill a gap.

---

## Technical details

Loads a date-indexed time series of JGB par yields — the input Phase 4B
(PCA) needs but `load_jgb_curve()` (Phase 1) can't provide, since that
function only ever returns the latest single day.

**File:** `data/jgb_curve_history_loader.py` — `load_jgb_curve_history()`
(public entry point), `_load_raw_history()` (live → cache → snapshot tier
selection), `_parse_mof_history_csv()` (shared parser, all three tiers),
`_apply_lookback_window()` / `_apply_ragged_tenor_policy()` (the date and
tenor filtering, in that order).

**Output contract:** `load_jgb_curve_history(lookback_years=None,
prefer_live=True, verbose=True) -> pd.DataFrame` — a `DatetimeIndex`
(ascending), one column per retained tenor (float years, ascending),
yields as decimals, **no missing values**. Metadata about what was kept
or dropped is attached to `df.attrs` (see §1.3).

---

## 1. What each piece does, and why

### 1.1 Why a sibling module, not an extension of `jgb_curve_loader.py`

Phase 1's loader has a fixed, simple contract: one date, one row, never a
missing value. This part's contract is a different shape entirely: many
dates, a wide matrix, and *genuine* missing cells that have to be handled
rather than forbidden. Bending Phase 1's tested, complete module to
support both shapes would be the riskier path; a sibling module keeps
each one's contract simple, and both already-passing test suites
untouched. What genuinely is shared — the tenor-column mapping
(`_MOF_TENOR_COLUMNS`) and the logic that finds the header row inside
MOF's CSV layout (`_find_mof_header_row_index`, extracted from Phase 1's
loader in this same change so both modules call one copy) — is imported,
not copied.

### 1.2 Source: MOF's historical file

Same reference page as the live current-curve pull, one row per business
day back to **1974-09-24**: a ~1.2MB CSV, noticeably slower to fetch than
the single-row file — the reason this loader has its own, longer request
timeout and its own cache (§1.4).

### 1.3 The two-step ragged-tenor policy — the main design problem

MOF's own tenor set has grown over time. Confirmed directly against the
source file:

| Tenor | First published |
| --- | --- |
| 1Y – 9Y | 1974-09-24 (start of the file) |
| 10Y | 1986-07-05 |
| 20Y | 1986-12-01 |
| 15Y | 1991-08-30 |
| 30Y | 1999-09-02 |
| 25Y | 2004-03-22 |
| 40Y | 2007-11-06 |

A tenor's absence before its introduction date shows up in MOF's own file
as a literal `-`. Separately — and this matters — three of the *earliest*
tenors (1Y, 2Y, 3Y) have their own multi-month reporting gap in
1978–1980, discovered by checking the raw file directly rather than
assumed. That gap is a different kind of missing data from a tenor not
existing yet: it's a real tenor that simply wasn't reported for a stretch
of months.

The policy treats both cases with one rule, deliberately, because the
correct response to each is the same — don't invent a value:

1. **Tenor selection.** A tenor column is kept only if it has **zero**
   missing values across the *entire* requested window; otherwise it's
   dropped for the whole window. Interpolating a tenor that hadn't been
   introduced yet would be inventing a yield for a bond that didn't
   exist — not filling a gap. Interpolating across the 1978–1980 gap
   would mean fabricating months of a real tenor's history, which isn't
   "filling a data outage" either at that length. A wide window can hit
   either case, or both, and the rule doesn't need to tell them apart to
   respond correctly to it.
2. **Residual row check.** After step 1, any date still missing a value
   in a *retained* tenor is dropped, row by row. Checked directly:
   **there are no interior gaps in any tenor anywhere in the file after
   2010** — so this step is a safety net for a future bad print, not
   something that fires today. It exists so a single bad value in the
   feed can't silently slip through rather than being caught.

Nothing is ever filled in — every value in the output is a real published
print. What survived is never left implicit: `load_jgb_curve_history()`
logs it, and also attaches it to the returned DataFrame as
`df.attrs["retained_tenors"]`, `["dropped_tenors"]`, `["window_start"]`,
`["window_end"]`, `["source_tier"]`, `["lookback_years"]`, and
`["rows_dropped_for_residual_gaps"]` — a caller can read exactly what it
got without re-deriving it. (`pandas.DataFrame.attrs` is best-effort and
doesn't reliably survive every further DataFrame operation — read it
straight off this function's return value, not after reshaping it.)

**Three real windows against the committed snapshot** (fetched
2026-09-04, 1974-09-24 to 2026-08-31), showing the policy's actual
range of outcomes:

| `lookback_years` | Retained tenors | Dropped tenors | Rows |
| --- | --- | --- | --- |
| `2.0` | all 15 | none | 486 |
| `25.0` | 13 | `25Y`, `40Y` (window starts ~2001, before either existed) | 6,121 |
| `None` (full history) | 6 (`4Y`–`9Y`) | the other 9, **including** `1Y`/`2Y`/`3Y` for the 1978–1980 gap | 13,290 |

The `None` row is the sharpest illustration: asking for "everything"
costs nine of fifteen tenors, because the intersection has to hold across
52 years. This is disclosed, not hidden — but it's also why Phase 4B does
not default to `lookback_years=None` (see its own doc).

### 1.4 Cache and snapshot: same live → cache → snapshot pattern as Phase 1

- **Cache** (`data/_jgb_curve_history_cache.csv` + a small
  `.meta.json` sidecar recording when it was fetched): the raw CSV text
  from the last successful live pull, gitignored, machine-local.
- **Snapshot** (`data/jgb_curve_history_snapshot.csv`, committed): a real
  MOF historical file saved unmodified. Storing the **raw text**, for
  both the cache and the snapshot, rather than a separately-parsed
  format, means all three tiers go through the exact same parser
  (`_parse_mof_history_csv`) — there's only one parsing path to trust,
  and the snapshot stays directly diffable against the live source, the
  same reasoning Phase 1 used for its own snapshot
  (`docs/phase_1_documentation.md` §1.2).

**Why a wider plausibility band than Phase 1's.** Phase 1's curve loader
checks yields against [-1%, +10%] — tuned for *today's* plausible range.
That band would incorrectly reject real history: JGB yields hit **~12.1%**
in the April 1980 oil-shock era and as low as **-0.42%** in the
negative-rate years, both confirmed directly against the source file.
This loader uses [-1%, +20%] instead — wide enough for the real
historical extremes, still tight enough to catch a percent/decimal unit
error.

### 1.5 `__main__`

Runs three lookback windows (2 years, 25 years, and the full history)
against the snapshot tier, printing each one's shape and its retained /
dropped tenors — so the table in §1.3 is reproducible by running the
file, not just asserted here.

---

## 2. Cache and snapshot re-anchoring policy

Per the project-wide policy (`docs/phase_1_documentation.md` §5): the
committed snapshot (`data/jgb_curve_history_snapshot.csv`) is a **fixed
vintage** — fetched **2026-09-04**, covering 1974-09-24 through
2026-08-31 — and must be manually refreshed (re-download and re-commit)
before any demo, presentation, or interview use, the same as Phase 1's
single-curve snapshot. It's reached whenever `prefer_live=False` is
used (as every test in this project does, for determinism) or when a
fresh machine has no cache yet.

The machine-local cache reduces how often the snapshot tier is actually
hit on a normal run, but doesn't remove this obligation — a CI run or a
fresh clone will always land on the committed snapshot, not the cache.

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 The ragged-tenor policy can be aggressive for a wide window.**
Asking for decades of history can cost most of the tenor grid (§1.3's
`None` row) — this is a real tradeoff of the "no fabricated values" rule,
not a bug. A caller that needs the full 15-tenor grid should choose a
window starting after 2007-11-06 (40Y's introduction).

**3.2 Inherits Phase 1's tenor-set caveat, sharpened.** Just as
`load_jgb_curve()`'s tenor set depends on which source served it, this
loader's tenor set depends on *which window* was requested — two
different `lookback_years` values against the same source can return
different tenor sets entirely (§1.3's table).

**3.3 The snapshot is a fixed vintage.** Same caveat as Phase 1's
snapshot, restated here since it governs a much larger, more expensive-
to-refresh file — see §2.

**3.4 No settlement/business-day calendar reasoning.** Dates are taken
as MOF published them; no attempt is made to reconcile against a
Japanese market holiday calendar or detect a silently-skipped business
day beyond the residual-gap check in §1.3 step 2.

---

## 4. What would change this design

**A less conservative ragged-tenor policy** (e.g., interpolating across a
short, clearly-real data outage rather than dropping the tenor) would
slot into `_apply_ragged_tenor_policy` alone — nothing about the tier
selection or the output contract would need to change. Not built now
because no genuine short outage exists anywhere in the post-2010 data to
justify the added complexity (§1.3 step 2).

**A business-day calendar check** (§3.4), if ever needed, would be an
additional validation step over the parsed history — additive, not a
change to the existing parsing or tiering.

---

## 5. Relationship to Phase 1

This module reuses two things directly from `data/jgb_curve_loader.py`:
`_MOF_TENOR_COLUMNS` (unchanged) and `_find_mof_header_row_index`
(extracted from inside `_fetch_live_curve` into its own function, with no
behavior change, specifically so this module could call the same logic
instead of re-implementing it — Phase 1's own 108 tests were re-run
unchanged after this extraction to confirm nothing moved).

Everything else — the wider plausibility band, the raw-text cache/
snapshot format, the lookback-window and ragged-tenor logic — is new,
because Phase 1's contract (one row, never missing) had nothing
equivalent to reuse.
