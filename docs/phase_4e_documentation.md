# Phase 4E Documentation — current-month top-up (`data/jgb_curve_history_loader.py`) + refresh button (`app.py`)

## In plain English

The Japanese government publishes its long daily history of bond rates
only once a month, after each month ends. On its own, that meant the
project's history (and everything built on it: the PCA patterns, the
daily scores, the "most recent day" P&L breakdown) was always weeks out
of date. The government also publishes a separate "this month so far"
sheet that's updated every day. This part fills the gap from that sheet,
and saves what it sees locally so that no days are lost in the few days
after a month ends, before the long history catches up.

The dashboard also gets a "Refresh market data" button. Before, it
downloaded the data once and kept showing it until the app restarted,
which on a hosted app could mean days-old numbers. And when some recent
days can't be loaded at all (for example on a new computer while the
government's site is down), the dashboard now says which dates are
missing and why, instead of quietly showing older data.

---

## Technical details

Extends Phase 4A's loader and adds one sidebar control to the Phase 4.7
dashboard. There's no new module and the loader's output contract is
unchanged: dates × tenors, decimal yields, no missing values.

**File:** `data/jgb_curve_history_loader.py` —
`_extend_with_recent_rows()` (the top-up, called from
`load_jgb_curve_history()`), `_fetch_current_month_rows()`,
`_read_recent_cache()` / `_write_recent_cache()`,
`_contiguous_recent_rows()` / `_month_is_complete_through()` (the gap
rule, §3).

**Dashboard:** `app.py` — `_refresh_market_data()` and the "Market
data" block in the sidebar (§4), plus the missing-data warnings (§5),
worded by `missing_recent_days_note()` in the loader module.

**When the top-up runs:** only with `prefer_live=True`, after the live → cache →
snapshot history has loaded. `prefer_live=False` never tops up, so every
test stays deterministic. `df.attrs["recent_rows_appended"]` records how
many rows were added.

---

## 1. Why this was needed

| MOF file | Covers | Updated |
| --- | --- | --- |
| `jgbcme_all.csv` (historical, Phase 4A) | 1974 → end of last month | once a month, after the month ends |
| `jgbcme.csv` (current month, Phase 1) | this month so far | every business day |

Checked on 2026-09-26: the historical file ended 2026-08-31, while the
current-month file had every business day from 2026-09-01 to 2026-09-24.
Without a top-up, Phase 4D's "most recent factor P&L attribution" showed
2026-08-28 → 2026-08-31, nearly a month old.

Both files have the same layout and the same 15 tenors, so the
current-month file goes through the same parser as the historical one
(`_parse_mof_history_csv`, with a lower minimum row count).

Appending the current month's rows to the history, rather than using
Phase 1's single latest curve in Phase 4D, keeps the PCA, the daily
scores and the attribution on one consistent data source and tenor
grid (see `docs/phase_4d_documentation.md` §5).

---

## 2. The recent-rows cache

**File:** `data/_jgb_curve_recent_cache.csv`. Gitignored and
machine-local, stored in MOF's own CSV layout so it's read back through
the same parser.

**Why it's needed.** Once a month ends, the current-month file moves on
to the new month, but the historical file may not include the finished
month yet. For those days, the only copy of the finished month is what
earlier runs saved.

**Lifecycle, e.g. around the end of September:**

1. **During September:** each run downloads the current-month file (which
   has all of September so far), saves those rows to the cache and
   appends them to the history.
2. **Early October:** the current-month file now shows October, and the
   historical file still ends 31 Aug. The cache supplies September; the
   fresh pull supplies October.
3. **MOF adds September to the historical file:** on the next run with a
   fresh live pull, cached rows up to the historical file's last date are
   dropped. The cache then holds only October.

Rows are only dropped after a **fresh live** historical pull. If the
history came from the fallback cache or snapshot, which may be old,
nothing is dropped.

If both the current-month download and the cache are unavailable,
nothing is appended. A failure here never stops the history loading.

---

## 3. No fake daily moves across a gap

PCA (Phase 4B) and the daily scores (Phase 4D) treat each row-to-row
difference as one day's move. If a week were missing, that week's whole
move would count as a single "day," a huge outlier that would distort
the PCA and the σ values.

Gaps can only appear **between months**, because each download of the
current-month file contains that whole month so far. So the rule is:

- A new month is appended only if it's the month right after the last
  appended date's month, **and**
- that previous month is complete: its last row is the month's last
  weekday (Dec 31 excepted, since the JGB market is closed then).

Otherwise, that month and everything after it are held back until MOF's
historical file fills the gap. There's no holiday calendar, so a month
ending on some other holiday reads as incomplete. That's the safe
direction: the data arrives a few days later, but no day is ever
skipped.

**Missing tenors.** A new row must have every tenor that the historical
file's last row has. Otherwise Phase 4A's ragged-tenor policy would drop
that tenor from the whole window over a single bad day. Rows that fail
this check are skipped and logged.

---

## 4. The refresh button

The dashboard caches its loaders with `@st.cache_data`
(`docs/phase_4_7_documentation.md` §2), and those caches never expire.
So MOF was only contacted the first time the app ran, and a long-running
app kept showing that first result while still labelling it "live."

The sidebar now has a **Market data** block showing:

- the latest MOF date and its source (live / cache / snapshot). This is
  normally one date, since the current curve and the daily history end
  on the same day. Only when they differ does it name both, and say that
  sections 5 and 7 are behind.
- when the data was fetched, in Japan time
- a **Refresh market data** button

The button clears every cached step (curve, history, PCA, PC scores,
bootstrap, curve fits and the fetch time), and the page rerun then
downloads everything again. It clears all of them, not just the two
downloads, because the rest are keyed on the curve or history. Leaving
them would only pile up cache entries for data that's no longer shown.

**Why a button, not an automatic expiry.** MOF publishes once per
business day, so a timed expiry would mostly re-download unchanged data
(the history file is ~1.2MB) and make random page loads slow. A button
refreshes only when someone wants it.

**Checked directly** (Streamlit's `AppTest`): a normal rerun keeps the
cached fetch time; clicking the button changes it and raises no errors.

---

## 5. Missing-data warnings

**Why.** The loaders never crash when MOF can't be reached. They fall
back quietly, which means the dashboard could show old data without
saying so. For example, on a new machine (so no recent-rows cache) where
the current-month download fails, the history stops at the end of last
month and the PCA, scores and attribution all quietly use older data.

**What the loader now reports** (`df.attrs`, which survives Streamlit's
cache):

| attr | Values |
| --- | --- |
| `source_tier` | `live` / `cache` / `snapshot` (Phase 4A) |
| `current_month_pull` | `ok` / `failed` / `skipped` (`prefer_live=False`) |
| `recent_rows_held_back` | `(first, last)` dates held back by the gap rule (§3), or `None` |

**What the dashboard shows,** as a yellow warning under the page title:

1. **If the current curve isn't live:** which saved curve (and date)
   sections 1–4 and 6 are using instead.
2. **If the history stops before the latest MOF data:** the missing date
   range, where the PCA / scores / attribution stop, and why. The reasons
   are built from the attrs above.
   - The latest MOF date comes from the live current curve's as-of date.
   - If MOF can't be reached, the range runs to the last business day
     before today, and the warning says it couldn't check.

For example, on a new machine where the current-month download fails:

> Curve history is missing 2026-09-01 to 2026-09-24. PCA factors, daily
> scores and P&L attribution stop at 2026-08-31. Why: the current-month
> download from MOF failed, and this machine has no saved copy of those
> days. Try **Refresh market data** later.

When nothing is missing, no warning is shown.

**Checked directly** (Streamlit's `AppTest`, with the downloads made to
fail and the caches pointed at empty paths): a normal run shows no
warning; the scenario above shows the text above; with no network at all
on a new machine, both warnings appear and the app still loads from the
snapshots.

---

## 6. Tests

`tests/test_jgb_curve_history_loader.py` (23 tests). The network pull is
replaced with synthetic rows and the cache is pointed at a temporary
folder, so the tests never touch MOF or the real cache. They cover:

- appending the current month
- rows surviving in the cache when a later download fails
- bridging into a new month after a complete previous month
- holding back a new month after an incomplete one
- never bridging a skipped month
- fresh rows overriding cached rows for the same date
- skipping a row with a missing tenor
- pruning cached rows once the historical file covers them
- the cache storing yields exactly
- no top-up when `prefer_live=False`
- the status the loader reports (failed download, held-back rows,
  skipped)
- the warning text: no warning when up to date or over a weekend, the
  missing range and reason, the "couldn't check" case ending on a
  business day, held-back rows, and a fallback history source

---

## 7. Known limitations (for the SR 11-7 validation report)

**7.1 The cache is machine-local.** On a host that resets its disk (e.g.
Streamlit Community Cloud after a restart), the cache starts empty. The
current month still tops up, but the previous month can't be bridged
until MOF's historical file includes it.

**7.2 End-of-month days depend on someone running it.** The cache only
updates when the loader runs. If nothing runs between a month's last
business day and the current-month file switching over, those days are
missing until MOF's historical file includes them. The gap rule (§3)
makes sure this only delays data and never corrupts it. (Here "runs"
means the app's cache being filled, i.e. a first load or a refresh, §4.)

**7.3 The PCA now moves day to day.** The 2-year window ends at the
latest available day, so loadings, σ values and +1σ exposures change
slightly as new days arrive. Figures in the Phase 4B/4C/4D docs come
from the fixed snapshot (`prefer_live=False`) and won't exactly match a
live dashboard.

**7.4 Two downloads of the same file.** Phase 1's `load_jgb_curve()` and
this top-up each download `jgbcme.csv` separately. It's a small file, so
sharing one download wasn't worth coupling the two loaders.

**7.5 No holiday calendar in the warning either.** When MOF can't be
reached, the warning counts every weekday up to today as possibly
missing, including Japanese holidays and today (whose rates may not be
published yet). It says it couldn't check, so a reader knows the range
is an upper bound.

**7.6 A refresh applies to every viewer.** Streamlit's data cache is
shared across all sessions, so one viewer's refresh updates the data
for everyone, and the first page load after it is slow for whoever
triggers it. Nothing stops repeated clicks, but each one is just two
downloads from MOF.

---

## 8. What would change this design

**A Japanese holiday calendar** would let a month that ends on a holiday
count as complete, so the next month could follow straight away instead
of waiting for MOF's historical file (§3).

**An automatic daily refresh** (e.g. a cache expiry timed just after
MOF's daily publication) would remove the need to click, at the cost of
a slow page load once a day. Not built, because the button already
covers it.

**Persistent storage** (e.g. a small database, or committing the cache)
would fix §7.1 for a hosted deployment. It isn't built now, because the
only cost today is a few days' delay after a restart near a month end.

---

## 9. Relationship to Phase 4A and the fallback re-anchoring policy

This phase adds no hardcoded data. Every appended row is a real MOF
figure, and the cache holds only what MOF itself published. The
committed snapshot (`data/jgb_curve_history_snapshot.csv`) and its
re-anchoring policy are unchanged (`docs/phase_4a_documentation.md` §2).
The top-up never applies to the snapshot tier with `prefer_live=False`.
