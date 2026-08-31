# Phase 1 Documentation — `data/jgb_curve_loader.py`

JGB par yield curve loader. This is the only input path for every downstream model
(`/models`: pricing, key rate duration, PCA, scenario/VaR) and for the SR 11-7
validation work in `/validation`. Downstream code never fetches or hard-codes a
curve; it calls `load_jgb_curve()` and receives one table.

**Functions in the module:**

1. `load_jgb_curve(prefer_live=True, verbose=True)`: public entry point; source chain **live → cache → snapshot**
2. `_fetch_live_curve()`: the live MOF pull + parse
3. `_write_cache()` / `_read_cache()`: machine-local write-through cache of the last good pull
4. `_snapshot_curve_dataframe()`: the embedded offline floor, served unmodified
5. `_standardize_curve()` / `_validate_curve_df()`shared frame builder and sanity gate

**Output contract (all paths):** a `pandas.DataFrame` with columns
`maturity_years` (`float`, tenor in years) and `yield` (`float`, par yield as a
**decimal** — `0.0288`, not `2.88`), sorted ascending by `maturity_years` with a
fresh `RangeIndex`.

---

## 1. What each function does, and why it was built that way

### 1.1 Module-level constants

**What it does**

```python
MOF_CSV_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
LIVE_REQUEST_TIMEOUT_SECONDS = 10

CACHE_PATH = Path(__file__).with_name("_jgb_curve_cache.json")

MIN_PLAUSIBLE_YIELD = -0.01   # -1%, decimal
MAX_PLAUSIBLE_YIELD =  0.10   # +10%, decimal

SNAPSHOT_DATE = "2026-04-06"
SNAPSHOT_CURVE_PCT: dict[float, float] = { 0.083: 0.77, 0.25: 0.87, ... , 40: 3.91 }
```

`SNAPSHOT_CURVE_PCT` is a real MOF par curve for one date, in percent. It is
served **unmodified** — there is no parallel shift and no separate anchor point.

**Why it was built this way**

- **Constants, not a config file or a fixture loaded at runtime.** A model
  validator can read every hard-coded input, and the exact transformation applied
  to it, by reading the top of one file (no need to execute anything, mock a
  network call, or inspect a pickle). The provenance review in section 2 is a
  code read. The one runtime-written input, the cache, is explicitly _not_ source
  and is bypassed for reproducible runs (§1.5, §4.2).
- **Percent at rest, decimal at the boundary.** `SNAPSHOT_CURVE_PCT` matches how
  the source quotes it (`2.40`), so a reviewer can diff it against the source
  page directly. The single `/ 100.0` conversion happens in the curve builders
  (`_snapshot_curve_dataframe`, `_fetch_live_curve`), not here — see §1.2, §1.3.
- **`MIN_PLAUSIBLE_YIELD` / `MAX_PLAUSIBLE_YIELD`.** A JGB par yield in decimal
  form realistically sits between roughly −0.3% and +5%; the band is set wider
  (−1% to +10%) so it never rejects real data but still catches a units or parse
  error — a curve of `2.40` (percent left undivided) or `240` (basis points) is
  an order of magnitude outside it. Enforced by `_validate_curve_df` on both the
  live pull and the cache (§1.3), closing the range-check gap noted in §4.4.
- **`CACHE_PATH` sits next to the module** (`data/_jgb_curve_cache.json`), one
  JSON file, gitignored. Machine-local state that changes on every successful
  pull has no place in version control (§4.2).
- **`SNAPSHOT_DATE` is a sibling constant to the curve**, not embedded in a
  comment, so re-anchoring the floor is editing two adjacent lines and nothing
  else (§6).
- **`SNAPSHOT_CURVE_PCT` keys are mixed `int`/`float`** (`10` vs `0.25`). This is
  harmless because every consumer casts with `float(tenor)`, but it is worth
  knowing that `SNAPSHOT_CURVE_PCT[10]` and `SNAPSHOT_CURVE_PCT[10.0]` both
  resolve (Python dict key equality across numeric types).
- **`from __future__ import annotations` is the first statement in the module.**
  It makes annotations lazy — stored as strings, evaluated only on demand (PEP 563) — rather than evaluated at definition time. Without it, the subscripted
  builtins in annotations such as `dict[float, float]`, `list[dict[str, float]]`,
  and `tuple[pd.DataFrame, str, int]` raise `TypeError` on Python 3.7–3.8; with
  it, the module imports on those versions too, and the small cost of
  constructing those generic-alias objects at import time is removed.

---

### 1.2 `_snapshot_curve_dataframe()` — the offline floor

**What it does**

Builds the embedded curve: iterate `SNAPSHOT_CURVE_PCT` in tenor order, divide
each value by 100, hand the rows to `_standardize_curve` (§1.3), return the
two-column DataFrame. No shift, no anchoring, no fitting — the numbers come out
exactly as they went in, just in decimal and sorted.

Snapshot curve (`SNAPSHOT_DATE` = 2026-04-06):

| tenor      | yield (%) | yield (decimal) |
| ---------- | --------- | --------------- |
| 1M (0.083) | 0.77      | 0.0077          |
| 3M (0.25)  | 0.87      | 0.0087          |
| 6M (0.5)   | 0.91      | 0.0091          |
| 1Y         | 1.12      | 0.0112          |
| 2Y         | 1.40      | 0.0140          |
| 3Y         | 1.60      | 0.0160          |
| 5Y         | 1.82      | 0.0182          |
| 7Y         | 2.19      | 0.0219          |
| 10Y        | 2.40      | 0.0240          |
| 20Y        | 3.32      | 0.0332          |
| 30Y        | 3.73      | 0.0373          |
| 40Y        | 3.91      | 0.0391          |

**Why it was built this way**

- **Served unmodified.** An earlier design lifted this curve by a parallel shift
  to a more recent 10Y print. That was removed: a parallel shift fixes the level
  but freezes a months-old _shape_, and it made the floor an approximation with
  its own error to defend. Serving the raw observed curve means the only defect
  is age (§4.1), which is simple to state and simple to fix by committing a newer
  snapshot (§6).
- **It is a floor, not a normal path.** `_snapshot_curve_dataframe` is reached
  only when the live pull fails _and_ the cache is missing or unusable — e.g. a
  fresh clone on a machine with no network. In every other case the loader
  returns live or cached data.
- **Real, not synthetic.** See §3.
- **`sorted(...)` on the dict items** so the rows are tenor-ordered before they
  reach `_standardize_curve`.
- **`float(tenor)` cast** normalizes the mixed int/float keys (§1.1) so
  `maturity_years` has a single dtype.

---

### 1.3 `_fetch_live_curve()`, `_standardize_curve()`, `_validate_curve_df()`

**What `_fetch_live_curve` does**

Pulls the current-year file from MOF and builds a curve from its most recent row,
or raises. Steps:

1. `requests.get(MOF_CSV_URL, timeout=LIVE_REQUEST_TIMEOUT_SECONDS)` then
   `response.raise_for_status()`.
2. Split the body into lines; find the first line starting with `"Date,"` and
   treat that as the header. Raise `ValueError` if there is no such line.
3. `pd.read_csv` from that line onward. Raise `ValueError` if there is no `Date`
   column.
4. Parse the `Date` column with `pd.to_datetime(..., format="%Y/%m/%d",
errors="coerce")`; keep only rows that parsed; raise if none did. Take the
   last surviving row (`iloc[-1]`) as the latest curve.
5. For each `(column, tenor)` in `_MOF_TENOR_COLUMNS`, if the column exists,
   `pd.to_numeric(..., errors="coerce")` the cell; skip it if `NaN`; otherwise
   append `{maturity_years: float(tenor), yield: value / 100.0}`.
6. If fewer than 5 tenors survived, raise `ValueError`.
7. `_standardize_curve(rows)` → `_validate_curve_df(df)` → return.

**What `_standardize_curve` / `_validate_curve_df` do**

- `_standardize_curve(rows)` — the one place the output contract is produced:
  `DataFrame` with `[maturity_years, yield]`, both cast to `float`, sorted
  ascending, `reset_index(drop=True)`. Used by the live pull, the snapshot, and
  the cache reader, so all three return byte-identical structure.
- `_validate_curve_df(df)` — raises `ValueError` unless the frame is a plausible
  curve: exact columns, at least 5 tenors, maturities positive and unique, no
  `NaN` yields, and every yield inside `[MIN_PLAUSIBLE_YIELD,
MAX_PLAUSIBLE_YIELD]`. Run on the live pull and on the cache read (§1.5).

**Why it was built this way**

- **Explicit 10-second timeout.** `requests` has no default timeout; without the
  argument a stalled connect or read blocks the process forever. For a loader
  whose whole point is "always return a curve," an indefinite hang is strictly
  worse than a clean failure, because it prevents the fallback from ever running.
  10s is comfortably above MOF's normal latency for a small CSV and bounds the
  worst-case wait before the fallback engages.
- **`raise_for_status()` before parsing.** A 404 or a 5xx often still has a body
  (an HTML error page). Converting non-2xx into an exception stops that body from
  being fed to `read_csv` and mis-parsed into a "successful" curve.
- **Header located by content, not by line number.** MOF's file is not a bare
  table: it opens with a title line (`Interest Rate (August 2026),,,...`) and
  ends with a free-text footer note. Assuming the header is line 0 — or line 1 —
  would break on any change to that preamble. Searching for `"Date,"` is
  resilient to the preamble growing or shrinking, and its absence is a clean
  signal that the format changed (→ raise → fallback).
- **Dates parsed with `errors="coerce"`, then filtered.** The trailing footer
  note lands in the DataFrame as a row whose `Date` cell is prose. Coercing
  unparseable dates to `NaT` and dropping them removes that row without
  hard-coding "drop the last line" (the number of footer lines is not
  guaranteed).
- **`iloc[-1]` for the latest row.** MOF appends chronologically, so the last
  valid row is the most recent business day — the curve we want.
- **Per-cell `to_numeric(errors="coerce")` and skip on `NaN`.** MOF leaves a
  tenor blank on days with no reference bond at that point. Coercing per cell and
  skipping blanks yields a curve with a gap rather than a `NaN` yield propagating
  into discounting or a bump-and-reprice.
- **`_MOF_TENOR_COLUMNS` is a hard-coded label → tenor-in-years map.** MOF's
  English CSV header is a fixed, known row
  (`Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y`), so the mapping
  from label to tenor is embedded rather than inferred from the header text. It
  also fixes the tenor universe the live path can return: 15 points, 1Y through
  40Y, no sub-year tenors. This differs from the snapshot's 12 points — see §4.5.
- **`< 5` tenor floor, then `_validate_curve_df`.** The early count check gives a
  specific message for a truncated/garbled parse; the validator then adds the
  semantic band. Between them, a structurally-valid but nonsensical curve (all
  zeros, values still in percent, basis points) is rejected as a failure and the
  loader falls through to the cache or snapshot rather than pricing off it.
- **Shared builder / validator across all three sources.** One definition of
  "a valid curve," applied identically to live, cache, and snapshot, so a bug
  cannot make one path accept what another rejects.
- **Raises rather than returning a sentinel.** `load_jgb_curve` is built around
  `try/except`; raising keeps the failure path in one place and preserves the
  specific exception for logging.
- **`from io import StringIO` imported inside the function.** Minor: keeps the
  import next to its single use; no measurable cost since the module is already
  imported once per process.

---

### 1.4 `_write_cache()` / `_read_cache()` — the write-through cache

**What it does**

- `_write_cache(df)` — called after every successful, validated live pull. Writes
  a JSON payload `{fetched_utc, source, curve}` to `CACHE_PATH`, where `curve` is
  a list of `[maturity_years, yield]` pairs. The write is **atomic**:
  `tempfile.mkstemp` in the same directory, `json.dump`, then `os.replace` onto
  `CACHE_PATH`. On any error the temp file is unlinked and the exception
  re-raised (the caller downgrades it to a warning — §1.5).
- `_read_cache()` — reads the JSON, rebuilds the frame through
  `_standardize_curve`, runs `_validate_curve_df`, computes `age_days` from
  `fetched_utc`, and returns `(df, fetched_utc, age_days)`. Raises on a missing,
  unreadable, malformed, or implausible cache.

**Why it was built this way**

- **A write-through cache, so an offline run still gets a real recent curve.**
  Before this, a failed live pull dropped straight to the months-old snapshot.
  Now it drops to the last curve this machine actually fetched — typically hours
  or a day old, a real full curve with a real shape, no approximation.
- **A separate JSON file, not the module's own source.** Rewriting constants in
  `.py` at runtime is fragile (formatting, read-only filesystems, concurrent
  runs, a crash mid-write corrupting the module) and pollutes git. A sidecar file
  has none of those problems.
- **Atomic write (`mkstemp` + `os.replace`).** A process killed mid-write can
  never leave a half-written `CACHE_PATH`; the worst case is an orphan
  `._jgb_cache_*.tmp` (gitignored) and the previous good cache still in place.
- **Validated before it is written and again when it is read.** `_fetch_live_curve`
  already validated, so a malformed curve is never cached; re-validating on read
  guards against a cache edited or truncated out of band.
- **`fetched_utc` carried in the payload and printed on use.** The staleness of
  the cache is never silent — `load_jgb_curve` logs "CACHED curve from … (N
  day(s) old)". Same loud-signal principle as the snapshot log line.
- **Gitignored.** The cache differs from machine to machine by whenever each last
  had network; committing it would make "what curve did this run use?"
  unanswerable from source (§4.2).

---

### 1.5 `load_jgb_curve(prefer_live=True, verbose=True)`

**What it does**

The public entry point. Source chain:

1. **`prefer_live=True` (default):** call `_fetch_live_curve()` inside
   `try/except Exception`.
   - Success → `_write_cache(df)` (a cache-write failure is logged, not raised),
     log `Loaded LIVE curve from MOF`, return.
   - Failure → log the exception repr, continue to step 2.
2. **Cache:** if `CACHE_PATH` exists, `_read_cache()` inside `try/except`.
   - Success → log `Loaded CACHED curve from <ts> (<n> day(s) old)`, return.
   - Failure → log, continue to step 3.
3. **Snapshot:** `_snapshot_curve_dataframe()`, log `Loaded SNAPSHOT curve … for
<SNAPSHOT_DATE>, served unmodified`, return.

**`prefer_live=False`** skips steps 1 and 2 entirely — no network, no cache read,
no cache write — and returns the snapshot. `verbose=False` silences every log
line.

**Why it was built this way**

- **Live → cache → snapshot, in that order.** Freshness first for interactive
  use; the cache is a real recent curve when the network is down; the snapshot is
  the last-resort floor that always exists. Each tier is only reached when the one
  above it is unavailable, so each log line is a real signal about how degraded
  the data is.
- **`prefer_live=False` bypasses the cache too.** A pinned SR 11-7 validation run
  has to be reproducible months later and on any machine, so it must not depend
  on "whatever this machine last cached." Bypassing both network and cache leaves
  exactly one deterministic input: the versioned snapshot constants.
- **`prefer_live` as a plain boolean argument**, not an environment variable or a
  settings module: the callers that need determinism (unit tests, validation
  runs) set it explicitly at the call site, where the choice is visible in the
  calling code and in stack traces.
- **Broad `except Exception` on the live pull, deliberately.** The failure
  surface of an HTTP-plus-CSV pull is open-ended and spans multiple libraries:
  `requests.ConnectionError`, `Timeout`, `HTTPError`, SSL errors, DNS failures
  surfacing as `OSError`, `UnicodeDecodeError`, `pandas.errors.ParserError`, plus
  the `ValueError`s this module raises itself (including the validator), and
  `KeyError`/`IndexError` on an unexpected frame shape. Enumerating them is a
  maintenance liability, and any type missed would crash a pricing or VaR run for
  zero gain when a usable fallback exists. `except Exception` does not catch
  `KeyboardInterrupt` or `SystemExit`; the exception is not swallowed — it is
  rendered with `{exc!r}` to stderr; the `try` wraps one call; and `# noqa: BLE001`
  records that the blanket catch is intentional for the linter. The cache read
  and the cache write are wrapped the same way and for the same reason.
- **A cache-write failure never breaks a good live return.** If the disk is
  read-only or full, `load_jgb_curve` still returns the live curve; the cache
  just is not refreshed this run.
- **Logs go to `stderr`, not `stdout`.** A caller can run
  `python -m data.jgb_curve_loader > curve.csv` (or pipe the frame) and get clean
  data on stdout while the provenance line goes to the terminal.
- **Every path names its source and its age.** Live says so; the cache prints its
  fetch timestamp and day count; the snapshot prints `SNAPSHOT_DATE` and why it
  was reached (`no live pull and no usable cache`, or `prefer_live=False`). That
  line is the run-time record of exactly which data was in force.
- **`verbose` defaults to true.** The common case (someone runs a model) should
  say out loud whether it used live, cached, or snapshot data. Callers that want
  clean output (test suites, batch jobs) pass `verbose=False`.

---

### 1.6 `__main__` self-check

**What it does**

Run directly, the module loads the curve (default `load_jgb_curve()`), prints it
with yields formatted as percentages, and reports two checks:
`is_monotonic_increasing` on the yields, and whether the shortest tenor yields
less than the longest ("upward-sloping overall"). If the curve is upward-sloping
but not strictly monotonic, it prints a note that long-end local inversions
(20s/30s, 25s/30s) are a real feature of the JGB curve, driven by insurer and
pension duration-extension demand and supply technicals, not a data error.

**Why it was built this way**

- **Monotonicity is reported, not asserted.** A real JGB curve is not required to
  rise at every step; treating a long-end inversion as a failure would reject
  valid data. The two checks are separated so that a _units_ bug (which usually
  breaks "upward-sloping overall" too) is distinguishable from a benign local
  inversion (which only trips strict monotonicity).
- It is a smoke test, not a unit test: no assertions, no exit code. Its job is to
  make an obviously-wrong curve (all zeros, percent/decimal confusion, scrambled
  tenors) visible on sight.

---

## 2. Data sources

Every hard-coded market data value in the file, with its exact source.

### 2.1 Embedded snapshot — `SNAPSHOT_CURVE_PCT` / `SNAPSHOT_DATE`

Trading Economics (tradingeconomics.com), Japan government bond yield pages — the
tenor table as displayed on the **2026-04-06** version of the page. Twelve
tenors, 1M–40Y, stored in percent and served without transformation.

There are no other hard-coded market values. `MIN_PLAUSIBLE_YIELD` /
`MAX_PLAUSIBLE_YIELD` are chosen validation bounds, not observations.

### 2.2 Source classification

Trading Economics is a **data aggregator**. Its bond-yield quotes are OTC
interbank quotes redistributed by the site; it is not the originator of the
numbers. The **primary** sources for JGB yields are the Japanese Ministry of
Finance (`jgbcme.csv` / the MOF interest-rate reference page) and the Bank of
Japan. The embedded snapshot is therefore **secondary-sourced**.

The live pull (`_fetch_live_curve`) targets the MOF CSV **directly**, and the
cache is a copy of whatever the live pull returned — so both of those are
primary-sourced. Only the offline floor carries the aggregator dependency
(§4.3).

The in-file comment above `SNAPSHOT_CURVE_PCT` describes it as a "REAL MOF JGB
par yield curve … actual published reference rates." That overstates directness:
the values were taken from Trading Economics, not read from a MOF publication.

---

## 3. Why real data instead of synthetic data

The offline floor is a real observed curve, not a generated one (e.g.
Nelson–Siegel parameters or a stylised upward slope). Reasoning:

- **Credibility under questioning.** This project is a portfolio piece that will
  be walked through by people who validate models. "This is the real published
  JGB curve for 2026-04-06, served unmodified" is a claim a reviewer can check
  against a public page in a minute. "This is a plausible-looking curve I
  parameterised" invites the immediate question of whether any downstream number
  — a KRD profile, a PCA loading, a VaR — is an artefact of the chosen parameters
  rather than of the market.
- **The limitation is nameable.** A real snapshot has exactly one defect —
  age (§4.1) — which is one sentence to state and one commit to fix (§6). A
  synthetic floor would additionally require defending the generating model as a
  second, nested modelling choice.

---

## 4. Known limitations (for the SR 11-7 validation report)

These are written to be quoted into `/validation`.

### 4.1 Snapshot staleness (floor only)

`SNAPSHOT_CURVE_PCT` is a real MOF par curve for a **single fixed past date**
(`SNAPSHOT_DATE` = 2026-04-06) and is served unmodified. There is no shape or
level approximation — but the whole curve, level and shape, is as of that date.
When the snapshot is the source, every tenor can be arbitrarily stale.

Mitigants: it is the **last** tier, reached only when the live pull fails _and_
the cache is missing/unusable; the log line states the date and that it "may be
stale"; and the re-anchor policy (§6) requires refreshing it before any
demo/presentation/interview. Automating that refresh is §5.2.

### 4.2 Cache is machine-local and unversioned

The write-through cache (`CACHE_PATH`, §1.4) holds the last curve _this machine_
fetched. Its contents therefore differ between machines and are not in git, so a
run that was served from the cache cannot be reproduced from source alone.

Mitigants: `prefer_live=False` bypasses the cache entirely, so every pinned
validation run depends only on the versioned snapshot; the cache payload carries
`fetched_utc` and the loader prints its age on use; and the cache is re-validated
on read (`_validate_curve_df`), so an out-of-band edit or truncation is caught.

### 4.3 Secondary source risk (embedded snapshot)

`SNAPSHOT_CURVE_PCT` is from Trading Economics, an aggregator redistributing OTC
interbank quotes, not from the primary issuers (MOF `jgbcme.csv`, BOJ). Aggregator
values can differ from the primary reference rates in snap time, rounding, and
interpolation method. The live pull and the cache do not have this dependency
(§2.2). Committing a snapshot taken straight from MOF would remove it.

### 4.4 Live-fetch fragility (partial semantic validation)

`_fetch_live_curve` now runs `_validate_curve_df` on its result, so a parsed
curve must have the right columns, ≥ 5 tenors, positive unique maturities, no
`NaN` yields, and every yield within `[MIN_PLAUSIBLE_YIELD, MAX_PLAUSIBLE_YIELD]`.
That closes the plain range-check gap. What is still **not** checked:

- **Date recency.** Nothing verifies the `iloc[-1]` row is recent rather than a
  stale or cached MOF file — a frozen upstream file would be accepted as current.
- **A decimal-vs-percent regression in the safe direction.** The band catches
  values left in percent (`2.40`) or in basis points (`240`), because those land
  far outside it. It does **not** catch MOF switching to already-decimal values
  (`0.0240`), because `0.0240 / 100 = 0.00024` is still inside the band — the
  curve would be silently 100× too small.
- **Tenor-set completeness.** Five of the expected ~15 tenors is enough to pass;
  a curve missing its long end would be accepted.

A format change that stays inside all of the above can still return **wrong data
as if it were a good live pull**, and — because the pull looks successful — that
curve is then **written to the cache** and served on later offline runs until the
next good pull.

### 4.5 Path-dependent tenor grid

The three sources do not return the same set of maturities. The live path (and
therefore the cache, which mirrors it) draws its tenors from `_MOF_TENOR_COLUMNS`:
1Y, 2Y, …, 10Y, 15Y, 20Y, 25Y, 30Y, 40Y — 15 points, no sub-year tenors. The
snapshot draws them from `SNAPSHOT_CURVE_PCT`: 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y,
10Y, 20Y, 30Y, 40Y — 12 points, with no 4Y/6Y/8Y/9Y/15Y/25Y. Downstream code that
pins tenors (KRD bucket definitions, a PCA pillar set) will see its input grid
change depending on which tier served the curve, and a history assembled from a
mix of tiers will have ragged tenors. The output-contract columns are identical
across tiers; the row set is not.

### 4.6 No historical curve series (Phase 3 dependency)

`_fetch_live_curve` pulls MOF's current-year file (`jgbcme.csv`) and keeps only
its last row — one curve, the latest published business day. MOF also publishes
the full history (a multi-decade `jgbcm_all.csv` plus per-year files) on the same
reference page, and the loader does not touch it.

For Phase 1 that is the right scope: pricing, KRD, and single-date scenarios need
one current curve, and downloading decades of daily data to read the last line
would only widen the parse surface. But it is a known forward gap:

- **PCA (Phase 3)** estimates the level / slope / curvature factors from a time
  series of daily curve _changes_ over a lookback window — it cannot run off a
  single curve.
- **VaR backtesting** needs a historical window of curves to replay.

Wiring in a historical loader (same URL path, the `jgbcm_all.csv` / per-year
files) is deferred to Phase 3.

---

## 5. What would change this design

### 5.1 Paid vendor feed or a verified full current curve

If the project had paid vendor access (Bloomberg, Refinitiv) or an otherwise
verified full **current** curve, the change would be contained to **the inside of
`load_jgb_curve`** — replace `_fetch_live_curve` with the vendor client, and
either drop the snapshot constants and `_snapshot_curve_dataframe` or demote them
to a last-resort behind the vendor call.

What would **not** change:

- the signature `load_jgb_curve(prefer_live=True, verbose=True)`;
- the output contract (`maturity_years`, decimal `yield`, sorted, `RangeIndex`);
- any downstream consumer in `/models` or `/validation`.

This is deliberate. The live-fetch-first architecture exists specifically so that
improving the data source is a swap of the fetch implementation behind a stable
interface, not a refactor that ripples into pricing, KRD, PCA, or the VaR code.

### 5.2 Automating the snapshot refresh

The floor (`SNAPSHOT_CURVE_PCT` / `SNAPSHOT_DATE`) is currently refreshed by a
person editing two lines (§6). A scheduled job or CI step — **not** the library
at run time — could fetch the current full curve from MOF periodically and open
that edit as a pull request, keeping the floor a few days fresh instead of
months.

Kept as a commit, not a runtime write, on purpose:

- **Auditability.** The floor stays a reviewed constant in version control,
  identical on every machine and in git history (§1.1). The runtime cache already
  covers the "fresh but machine-local" case (§1.4); the floor's job is to be the
  fixed, inspectable last resort.
- **A machine-fetched curve still gets a human glance** before it becomes the
  committed floor — `_validate_curve_df` rules out structural nonsense (§1.3) but
  not a plausible-looking wrong curve (§4.4).

Committing MOF's historical file (§4.6) would fold into the same job.

---

## 6. Fallback data re-anchoring policy (project-wide)

This section is the canonical statement of the policy; later phase documentation
references it rather than restating it.

The embedded floor built by `_snapshot_curve_dataframe()` — `SNAPSHOT_CURVE_PCT`
together with `SNAPSHOT_DATE` — is the concrete instance this policy governs in
Phase 1.

This fallback data is a static snapshot, not auto-updating. It must be manually
re-anchored to the latest available print before any demo, presentation, or
interview use. This is a standing policy for this project: any other hardcoded or
fallback dataset added in later phases (e.g. BOJ holdings/free-float data, auction
bid-to-cover figures) follows this same re-anchor-before-use discipline,
documented individually in each phase's own documentation but governed by this
same principle.

Operationally, re-anchoring the Phase 1 floor means replacing `SNAPSHOT_CURVE_PCT`
and `SNAPSHOT_DATE` with a fresh real curve for a recent date — one edit, no
transformation, no separate anchor to keep in sync. The write-through cache
(§1.4) reduces how often the floor is actually reached, but does not remove the
obligation: fresh clones, CI runners, and any first run without network still land
on it, and `prefer_live=False` always does. Automating the replacement on a
schedule is described in §5.2. The `verbose` log line records which tier — live,
cache, or snapshot — was in force for a given run; this section records the
standing obligation to keep the snapshot current.
