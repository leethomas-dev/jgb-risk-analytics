# Phase 1 Documentation — `data/jgb_curve_loader.py`

## In plain English

This part answers one question: "what are Japanese government bonds
currently paying in interest, across loan lengths from one month to 40
years?" It checks the Japanese government's own website for the latest
numbers. If that fails (no internet, site down), it falls back to the
last successful check saved on this computer. If even that isn't
available — a brand-new computer that's never connected — it falls back
to a real set of rates saved inside the program, from one specific past
date. Whichever it uses, it says so out loud, so nobody is left guessing
how current the numbers are.

---

## Technical details

`load_jgb_curve()` is the only place any downstream code gets a JGB par
yield curve from. Nothing else in the project fetches or hardcodes one.

**Functions:** `load_jgb_curve(prefer_live=True, verbose=True)` (public
entry point; tries **live → cache → snapshot**, in order), `_fetch_live_curve()`
(the live pull), `_write_cache()` / `_read_cache()` (a local cache of the
last good pull), `_snapshot_curve_dataframe()` (the offline fallback,
served unchanged), `_standardize_curve()` / `_validate_curve_df()` (shared
formatting and sanity checks used by all three sources).

**Output contract:** a `pandas.DataFrame`, columns `maturity_years`
(float, years) and `yield` (float, decimal — `0.0288`, not `2.88`),
sorted ascending.

---

## 1. What each piece does, and why

### 1.1 Constants

`MOF_CSV_URL`, a 10-second request timeout, a cache file path, plausible-yield
bounds (`-1%` to `+10%`, wide enough to admit any real JGB rate but narrow
enough to catch an obvious unit error like a rate left in percent form),
and the embedded snapshot itself — a real MOF curve for one fixed date
(`SNAPSHOT_DATE`), stored in percent and converted to decimal only where
it's used, so it can be diffed against the source page directly.

**Why:** everything the loader can fall back on is a plain constant at
the top of one file — a reviewer can see every hardcoded input, and
exactly how it's used, without running anything. The snapshot's date sits
next to the data so refreshing it later is a two-line edit (§5).

### 1.2 The offline snapshot (`_snapshot_curve_dataframe`)

The last-resort fallback: a real MOF curve for `2026-04-06`, served
exactly as observed — no adjustment, no re-anchoring to a more recent
rate.

| tenor | yield (%) |
| --- | --- |
| 1M | 0.77 | 3M | 0.87 | 6M | 0.91 | 1Y | 1.12 | 2Y | 1.40 | 3Y | 1.60 |
| 5Y | 1.82 | 7Y | 2.19 | 10Y | 2.40 | 20Y | 3.32 | 30Y | 3.73 | 40Y | 3.91 |

**Why unmodified, not adjusted to a recent print:** an earlier version
shifted this curve to match a more recent 10-year rate. That was removed
— a shift fixes the overall level but keeps a months-old *shape*, turning
the fallback into an approximation with its own error to explain. Serving
the real, unmodified curve means the only issue is age, which is simple
to state and simple to fix (§5). This tier is only reached when the live
pull fails *and* there's no usable cache — e.g. a fresh install with no
network yet.

### 1.3 The live pull (`_fetch_live_curve`)

Downloads MOF's current-year file, finds its header row by content (the
file has a title line before it and a footnote after — searching for the
line starting `"Date,"` survives either changing), parses it, drops any
row whose date doesn't parse (removes the trailing footnote without
assuming how many lines it is), and takes the last valid row as today's
curve. Requires at least 5 tenors to have parsed, or it fails — deliberately,
so a garbled or truncated download drops to the next fallback tier rather
than pricing off bad data.

**Why a 10-second timeout:** downloads have no default timeout in the
library used here: without one, a stalled connection can hang forever.
Since this loader's whole purpose is "always return a curve," an
indefinite hang is worse than a quick, clean failure that lets the
fallback run.

### 1.4 The local cache (`_write_cache` / `_read_cache`)

After every successful live pull, the curve is saved to a small file next
to this module (`data/_jgb_curve_cache.json`, not checked into version
control — it's specific to this machine, not the project). The write is
atomic (a crash mid-write can't corrupt it), and the saved curve is
re-validated on the way back out, so a manually edited or corrupted cache
file is caught rather than trusted.

**Why:** before this existed, a failed live pull dropped straight to the
months-old snapshot. Now it drops to the last curve *this machine* pulled
successfully — usually hours old, a real curve, not an approximation.

### 1.5 `load_jgb_curve(prefer_live=True, verbose=True)`

The public entry point. Tries the live pull first; on failure, tries the
cache; on failure, falls back to the snapshot. Every path prints which
tier it used and how stale that data is — nothing is silent.

`prefer_live=False` skips both the network call and the cache, returning
only the versioned snapshot — the one input that's identical on every
machine and doesn't change over time. `verbose=False` silences the log
lines.

**Why `prefer_live=False` exists:** a validation run that needs to be
reproducible months later, on any machine, can't depend on "whatever this
machine happened to have cached." Skipping straight to the snapshot
removes that dependency entirely.

### 1.6 `__main__` self-check

Running the file directly prints the curve and two checks: is it strictly
increasing, and is the long end higher than the short end overall. A real
JGB curve can have small local dips (e.g. the 20-year rate briefly above
the 30-year) without being wrong — that's a known, real feature of this
market, driven by specific investors (insurers, pension funds) concentrating
their buying at particular points on the long end. The check reports this
rather than treating it as an error.

---

## 2. Where the data comes from

The embedded snapshot's numbers come from Trading Economics, a data
aggregator that redistributes rates rather than publishing them first-hand
— the primary source is Japan's Ministry of Finance (MOF) directly. The
live pull and the cache both go straight to MOF, so only the offline
snapshot carries this secondary-source gap (§3.3 below).

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 The snapshot can be arbitrarily stale.** It's a real curve, but for
one fixed past date — every tenor is as old as `SNAPSHOT_DATE`. Mitigated
by being the last-resort tier only, by a clear "may be stale" log line,
and by the re-anchoring policy (§5): it must be refreshed before any
external-facing use.

**3.2 The cache is specific to one machine and isn't version-controlled.**
A run served from the cache can't be reproduced from the project's source
code alone. `prefer_live=False` sidesteps this by skipping the cache
entirely for any run that needs to be reproducible.

**3.3 The snapshot is secondary-sourced.** It comes from an aggregator,
not MOF directly, so it can differ slightly from the primary reference
rate in timing or rounding. The live pull and cache don't have this
issue.

**3.4 The live pull's error-checking has real gaps.** It checks that a
parsed curve has enough tenors, sensible maturities, and yields inside a
plausible band — but it does **not** check that the date is actually
recent (a frozen upstream file would pass), and it would **not** catch
MOF switching to already-decimal values, since the resulting numbers
would still land inside the plausible band while being 100× too small. A
bad pull that passes these checks gets written to the cache and served on
later offline runs until the next good pull.

**3.5 The three sources don't return the same tenors.** Live/cache: 15
points, 1Y–40Y, no maturities under a year. Snapshot: 12 points, 1 month
to 40 years, missing several mid-range points the live source has. Any
downstream code that assumes a fixed list of tenors will silently break
depending on which source served the curve — every phase in this project
derives its tenor set from the curve at runtime instead, for this reason.

**3.6 No historical series.** The loader only ever returns the latest
curve — one date. MOF also publishes decades of daily history on the same
page, untouched here. Phase 4 (PCA) needs a time series of curve changes
and can't run without this being built first.

---

## 4. What would change this design

**A paid vendor feed**, if the project ever had one, would replace just
the live-pull internals — nothing about the function's signature, its
output shape, or any downstream code would need to change. That's the
point of keeping every consumer behind this one function.

**Automating the snapshot refresh** (rather than a person editing two
constants by hand) could run as a scheduled job proposing the edit as a
pull request — kept out of the library's own runtime deliberately, so a
machine-fetched curve still gets a human glance before becoming the
committed fallback.

---

## 5. Fallback data re-anchoring policy (project-wide)

Any hardcoded or fallback dataset in this project is a snapshot of
something that moves, not a live source — and must be manually refreshed
to a current value before any demo, presentation, or interview use. This
is the canonical statement of that policy; every other phase's own doc
names its own instance of it (or explains why it doesn't apply) rather
than restating this.

For this module specifically: re-anchoring means replacing the snapshot
constants with a fresh real curve for a recent date — one edit, no
transformation. The cache reduces how often the snapshot is actually
reached, but doesn't remove the obligation — a fresh install, a CI run,
or `prefer_live=False` will always land on it.
