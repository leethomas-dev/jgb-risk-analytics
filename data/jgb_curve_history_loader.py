"""
jgb_curve_history_loader.py

Loads a TIME SERIES of JGB par yield curves for Phase 4 (PCA). Sibling to
jgb_curve_loader.py rather than an extension of it: that module's whole
contract (one date, one row, no NaN) is fundamentally different from what
PCA needs (many dates, a wide matrix, genuine missing cells) -- forcing
both shapes through one function would complicate a module that's already
complete and tested (108 passing tests). What IS shared is imported
directly rather than reimplemented: _MOF_TENOR_COLUMNS (the CSV-column ->
tenor-year mapping) and _find_mof_header_row_index (locating the header
row in MOF's CSV layout, which both files share).

Source: MOF's historical file, same reference page as the current-curve
pull, one row per business day back to 1974-09-24:
https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv

Tiers, same live -> cache -> snapshot resilience pattern as Phase 1,
applied to the whole raw history (see _load_raw_history):

1. LIVE: download the full historical CSV fresh. Slow (~1.2MB, thousands
   of rows) compared to the current-curve pull, hence a longer timeout
   and the cache below.
2. CACHE: the last successful live pull's raw text, saved next to this
   module (gitignored, machine-local).
3. SNAPSHOT: data/jgb_curve_history_snapshot.csv, a real MOF historical
   file committed to the repo, served through the SAME parser as the live
   pull (see module docstring re: "diffable against the source directly",
   docs/phase_1_documentation.md §1.2's reasoning applied here too).
   Fetched 2026-09-04; re-anchor before demo/interview use exactly like
   the Phase 1 snapshot (docs/phase_4a_documentation.md).

RAGGED TENOR HISTORY -- the central design problem this module solves,
in two steps applied after the lookback window is sliced (see
_apply_ragged_tenor_policy):

  1. TENOR SELECTION: a tenor column is kept only if it has NO missing
     value anywhere in the requested window; otherwise it is dropped for
     the WHOLE window. This handles two genuinely different situations
     with one rule, deliberately: (a) a tenor MOF hadn't started
     publishing yet at the window's start (e.g. no 40Y before
     2007-11-06) -- interpolating a value here would be inventing a
     yield for a bond that didn't exist, not filling a gap; (b) a rare
     real reporting gap in an already-active tenor (e.g. 1Y/2Y/3Y have
     several-month gaps in 1978-1980) -- interpolating across MONTHS is
     not "filling a data outage" either, it's fabricating a trend. A
     wide enough window can hit either case, or both; the rule doesn't
     need to tell them apart because the correct response is the same
     either way: don't manufacture the missing values, just be explicit
     that the tenor isn't usable for that window.
  2. RESIDUAL ROW CHECK: after step 1, any date row still missing a
     value in a RETAINED tenor is dropped (row-wise). In practice this
     never fires for a modern window -- there are no interior gaps in
     any tenor anywhere after 2010 (checked directly against the source
     file) -- but it's kept as an explicit safety net rather than an
     assumption, so a single bad print in the raw feed can't silently
     leak a NaN (or worse, a prior value some other library forward-
     filled) into the output.

Neither step ever fills a value -- every cell in the output is a real
published print. What tenors and dates actually survive is never silent:
load_jgb_curve_history() logs it (verbose=True, the default) and also
attaches it to the returned DataFrame's .attrs (retained_tenors,
dropped_tenors, window_start, window_end, source_tier, lookback_years) --
note pandas .attrs is best-effort and doesn't reliably survive further
DataFrame operations, so a caller that needs this metadata should read it
immediately from the object this function returns.

Output contract: a date-indexed pandas.DataFrame (DatetimeIndex, ascending,
name "date"), one column per retained tenor (float years, ascending,
columns.name "maturity_years"), yields as DECIMALS -- same convention as
load_jgb_curve(). No NaN in the returned frame.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from data.jgb_curve_loader import _find_mof_header_row_index, _MOF_TENOR_COLUMNS

MOF_HISTORY_CSV_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    "historical/jgbcme_all.csv"
)
# Longer than the current-curve loader's 10s: this file is a multi-decade
# daily series (~1.2MB), not one row -- a short timeout would make a slow
# but healthy connection look identical to a dead one.
HISTORY_REQUEST_TIMEOUT_SECONDS = 30

# Local write-through cache: the last successful live pull's RAW CSV text
# (not a re-parsed copy) plus a small sidecar with when it was fetched --
# storing raw text means the cache is read back through the exact same
# parser as a live pull or the snapshot, so there is only one parsing path
# to trust. Machine-local, gitignored, never committed.
HISTORY_CACHE_PATH = Path(__file__).with_name("_jgb_curve_history_cache.csv")
HISTORY_CACHE_META_PATH = Path(__file__).with_name("_jgb_curve_history_cache.meta.json")

# Committed fallback: a real MOF historical file, saved verbatim. See
# docs/phase_4a_documentation.md for the re-anchor policy.
HISTORY_SNAPSHOT_PATH = Path(__file__).with_name("jgb_curve_history_snapshot.csv")
HISTORY_SNAPSHOT_FETCHED_DATE = "2026-09-04"

# Plausibility band for a decimal JGB yield ANYWHERE in the published
# history -- much wider than load_jgb_curve_loader's [-1%, +10%], which is
# tuned for TODAY's plausible range. JGB yields genuinely reached ~12.1%
# in the April 1980 oil-shock era and as low as -0.42% during the
# negative-rate years; this band comfortably admits the real historical
# extremes while still catching a percent/decimal unit error (e.g. a raw
# "10.327" mis-parsed as 1032.7%).
MIN_PLAUSIBLE_YIELD_HISTORY = -0.01  # -1%
MAX_PLAUSIBLE_YIELD_HISTORY = 0.20  # +20%

MIN_TENORS_FOR_VALID_PULL = 5
# ~1 trading year -- guards against a truncated/garbled download being
# mistaken for real (if thin) history, the same role Phase 1's min-tenor
# check plays for a single curve.
MIN_ROWS_FOR_VALID_PULL = 250

# Same ACT/365.25 (average calendar year) approximation config/portfolio_loader.py
# uses for "years remaining" -- redefined here rather than imported, since
# data/ has no dependency on config/ elsewhere and this is a well-known
# constant, not project-specific logic.
_DAYS_PER_YEAR = 365.25


def _parse_mof_history_csv(text: str, *, verbose: bool = False) -> pd.DataFrame:
    """Parse MOF's historical CSV text into a wide, RAW (NaN-containing)
    decimal-yield DataFrame: DatetimeIndex (ascending, name "date"),
    columns = every recognized tenor (float years, ascending, name
    "maturity_years"). A cell is NaN wherever MOF's own file has "-"
    (not yet published for that tenor on that date). Shared by all three
    tiers (see module docstring) so there is exactly one parsing path.

    A duplicate date in the raw file is resolved by keeping the last
    occurrence -- logged (verbose=True) rather than silently dropped, the
    same "nothing silent" standard the ragged-tenor policy holds itself to
    (module docstring); in practice this has never fired against a real
    MOF pull, but a silent dedup is exactly the kind of thing that should
    be visible if it ever does.

    Raises ValueError on any structural failure -- no header row, no
    'Date' column, no parseable date rows, or too few usable tenors/rows
    (see MIN_TENORS_FOR_VALID_PULL / MIN_ROWS_FOR_VALID_PULL).
    """
    lines = text.splitlines()
    header_idx = _find_mof_header_row_index(lines)
    if header_idx is None:
        raise ValueError(
            "Unexpected MOF historical CSV format: no 'Date,...' header row found"
        )

    raw = pd.read_csv(StringIO("\n".join(lines[header_idx:])), skip_blank_lines=True)
    if "Date" not in raw.columns:
        raise ValueError("Unexpected MOF historical CSV format: no 'Date' column found")

    parsed_dates = pd.to_datetime(raw["Date"], format="%Y/%m/%d", errors="coerce")
    data_rows = raw.loc[parsed_dates.notna()].copy()
    if data_rows.empty:
        raise ValueError("MOF historical CSV had a 'Date' column but no parseable date rows")

    tenor_data: dict[float, pd.Series] = {}
    for col, tenor in _MOF_TENOR_COLUMNS.items():
        if col not in data_rows.columns:
            continue
        values_pct = pd.to_numeric(data_rows[col].replace("-", pd.NA), errors="coerce")
        tenor_data[float(tenor)] = (values_pct / 100.0).to_numpy()

    if len(tenor_data) < MIN_TENORS_FOR_VALID_PULL:
        raise ValueError(
            f"MOF historical CSV parsed but yielded too few usable tenor columns "
            f"({len(tenor_data)} < {MIN_TENORS_FOR_VALID_PULL})"
        )

    index = pd.DatetimeIndex(parsed_dates.loc[data_rows.index], name="date")
    df = pd.DataFrame(tenor_data, index=index)
    df = df.sort_index()
    n_duplicates = int(df.index.duplicated().sum())
    if n_duplicates and verbose:
        print(
            f"[jgb_curve_history_loader] Found {n_duplicates} duplicate date(s) in the "
            "raw MOF file; keeping each date's LAST occurrence and dropping the rest.",
            file=sys.stderr,
        )
    df = df.loc[~df.index.duplicated(keep="last")]
    df = df.reindex(sorted(df.columns), axis=1)
    df.columns.name = "maturity_years"

    if len(df) < MIN_ROWS_FOR_VALID_PULL:
        raise ValueError(
            f"MOF historical CSV parsed but yielded too few rows "
            f"({len(df)} < {MIN_ROWS_FOR_VALID_PULL})"
        )

    return df


def _validate_history_df_raw(df: pd.DataFrame) -> None:
    """Raise ValueError unless df is a plausible raw (NaN-permitting)
    historical curve matrix. Structural checks mirror
    jgb_curve_loader._validate_curve_df; the yield-range check uses the
    wider historical band and ignores NaN cells (a NaN here means "not
    yet published", not "implausible")."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("history index is not a DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("history index is not sorted ascending")
    if df.index.duplicated().any():
        raise ValueError("history has duplicate dates")
    if len(df.columns) < MIN_TENORS_FOR_VALID_PULL:
        raise ValueError(f"history has only {len(df.columns)} tenor column(s)")
    if len(df) < MIN_ROWS_FOR_VALID_PULL:
        raise ValueError(f"history has only {len(df)} row(s)")

    values = df.to_numpy()
    observed = values[~pd.isna(values)]
    if observed.size and (
        observed.min() < MIN_PLAUSIBLE_YIELD_HISTORY
        or observed.max() > MAX_PLAUSIBLE_YIELD_HISTORY
    ):
        raise ValueError(
            f"history has yields outside "
            f"[{MIN_PLAUSIBLE_YIELD_HISTORY}, {MAX_PLAUSIBLE_YIELD_HISTORY}]: "
            f"min={observed.min()}, max={observed.max()}"
        )


def _fetch_live_history_text() -> str:
    """Download MOF's historical CSV and return its raw text, unparsed --
    parsing is _parse_mof_history_csv's job (shared with the cache and
    snapshot tiers); this function's only job is the network call."""
    response = requests.get(MOF_HISTORY_CSV_URL, timeout=HISTORY_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def _write_history_cache(raw_text: str) -> None:
    """Atomically persist the raw CSV text to HISTORY_CACHE_PATH, plus a
    small sidecar recording when it was fetched (for the cache's age
    report -- see load_jgb_curve_history)."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(HISTORY_CACHE_PATH.parent), prefix="._jgb_history_cache_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(raw_text)
        os.replace(tmp_name, HISTORY_CACHE_PATH)  # atomic within the same filesystem
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    meta = {
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": MOF_HISTORY_CSV_URL,
    }
    meta_fd, meta_tmp = tempfile.mkstemp(
        dir=str(HISTORY_CACHE_META_PATH.parent), prefix="._jgb_history_meta_", suffix=".tmp"
    )
    try:
        with os.fdopen(meta_fd, "w") as fh:
            json.dump(meta, fh, indent=2)
        os.replace(meta_tmp, HISTORY_CACHE_META_PATH)
    except Exception:
        try:
            os.unlink(meta_tmp)
        except OSError:
            pass
        raise


def _read_history_cache(*, verbose: bool) -> tuple[pd.DataFrame, str, int]:
    """Return (raw history df, fetched_utc, age_days) from the cache.
    Raises on a missing / unreadable / malformed / implausible cache."""
    with open(HISTORY_CACHE_PATH) as fh:
        raw_text = fh.read()
    df = _parse_mof_history_csv(raw_text, verbose=verbose)
    _validate_history_df_raw(df)

    with open(HISTORY_CACHE_META_PATH) as fh:
        meta = json.load(fh)
    fetched_utc = str(meta["fetched_utc"])
    fetched_dt = datetime.strptime(fetched_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    age_days = (datetime.now(timezone.utc) - fetched_dt).days
    return df, fetched_utc, age_days


def _read_snapshot_history(*, verbose: bool) -> pd.DataFrame:
    """Load the committed snapshot file through the SAME parser used for
    a live pull -- the snapshot is a real MOF file saved unmodified, not a
    separately-encoded format, so there is nothing snapshot-specific to
    trust here beyond the file on disk (docs/phase_1_documentation.md
    §1.2's "served unmodified" reasoning, applied to history)."""
    with open(HISTORY_SNAPSHOT_PATH) as fh:
        raw_text = fh.read()
    df = _parse_mof_history_csv(raw_text, verbose=verbose)
    _validate_history_df_raw(df)
    return df


def _load_raw_history(*, prefer_live: bool, verbose: bool) -> tuple[pd.DataFrame, str]:
    """Tier selection: live -> cache -> snapshot, mirroring
    jgb_curve_loader.load_jgb_curve's control flow exactly (nested
    try/except/else, early return on the first usable tier). Returns
    (raw wide history -- may contain NaN, see module docstring --, the
    tier name actually used)."""
    if prefer_live:
        try:
            raw_text = _fetch_live_history_text()
            live_df = _parse_mof_history_csv(raw_text, verbose=verbose)
            _validate_history_df_raw(live_df)
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all fallback
            if verbose:
                print(
                    f"[jgb_curve_history_loader] Live MOF historical pull failed "
                    f"({exc!r}); trying local cache.",
                    file=sys.stderr,
                )
        else:
            try:
                _write_history_cache(raw_text)
            except Exception as exc:  # noqa: BLE001 - cache write must not break a good pull
                if verbose:
                    print(
                        f"[jgb_curve_history_loader] Could not update history cache "
                        f"({exc!r}); continuing with the live pull.",
                        file=sys.stderr,
                    )
            if verbose:
                print(
                    f"[jgb_curve_history_loader] Loaded LIVE history from MOF: "
                    f"{len(live_df)} dates, {live_df.index.min().date()} to "
                    f"{live_df.index.max().date()}.",
                    file=sys.stderr,
                )
            return live_df, "live"

        if HISTORY_CACHE_PATH.exists() and HISTORY_CACHE_META_PATH.exists():
            try:
                cached_df, fetched_utc, age_days = _read_history_cache(verbose=verbose)
            except Exception as exc:  # noqa: BLE001 - bad cache -> fall through to snapshot
                if verbose:
                    print(
                        f"[jgb_curve_history_loader] History cache present but "
                        f"unusable ({exc!r}); falling back to the embedded snapshot.",
                        file=sys.stderr,
                    )
            else:
                if verbose:
                    print(
                        f"[jgb_curve_history_loader] Loaded CACHED history from "
                        f"{fetched_utc} ({age_days} day(s) old): {len(cached_df)} dates, "
                        f"{cached_df.index.min().date()} to {cached_df.index.max().date()}.",
                        file=sys.stderr,
                    )
                return cached_df, "cache"

    snapshot_df = _read_snapshot_history(verbose=verbose)
    if verbose:
        why = "no live pull and no usable cache" if prefer_live else "prefer_live=False"
        print(
            f"[jgb_curve_history_loader] Loaded SNAPSHOT history: real MOF history "
            f"fetched {HISTORY_SNAPSHOT_FETCHED_DATE} ({why}), {len(snapshot_df)} dates, "
            f"{snapshot_df.index.min().date()} to {snapshot_df.index.max().date()}. "
            "A fixed vintage -- nothing published after that date is included; see "
            "docs/phase_4a_documentation.md.",
            file=sys.stderr,
        )
    return snapshot_df, "snapshot"


def _apply_lookback_window(df_raw: pd.DataFrame, lookback_years: float | None) -> pd.DataFrame:
    """Slice df_raw to its last `lookback_years` years, measured back from
    the LATEST date actually present in df_raw (not from today -- the
    snapshot tier's latest date is whenever it was last fetched, and a
    reproducible prefer_live=False run must anchor to that, not to
    "today"). lookback_years=None returns df_raw unchanged (the full
    available history)."""
    if lookback_years is None:
        return df_raw
    if lookback_years <= 0:
        raise ValueError(f"lookback_years must be positive, got {lookback_years}")
    cutoff = df_raw.index.max() - pd.Timedelta(days=lookback_years * _DAYS_PER_YEAR)
    return df_raw.loc[df_raw.index >= cutoff]


def _apply_ragged_tenor_policy(df_windowed: pd.DataFrame, *, verbose: bool) -> pd.DataFrame:
    """Apply the two-step ragged-tenor policy described in the module
    docstring to an already-windowed raw history, and report the result
    (verbose logging + .attrs on the returned frame). Raises ValueError if
    nothing survives (an unreasonable combination of window and data)."""
    is_complete = df_windowed.notna().all(axis=0)
    retained = sorted(df_windowed.columns[is_complete])
    dropped = sorted(df_windowed.columns[~is_complete])

    out = df_windowed[retained].copy()
    rows_before = len(out)
    out = out.dropna(axis=0, how="any")
    rows_dropped = rows_before - len(out)

    if out.empty or not retained:
        raise ValueError(
            "the ragged-tenor policy left no usable data for this window -- "
            f"window had {len(df_windowed)} row(s) before filtering, "
            f"tenors on the raw grid: {list(df_windowed.columns)}"
        )

    out.columns.name = "maturity_years"
    window_start = out.index.min().date().isoformat()
    window_end = out.index.max().date().isoformat()

    out.attrs["retained_tenors"] = retained
    out.attrs["dropped_tenors"] = dropped
    out.attrs["window_start"] = window_start
    out.attrs["window_end"] = window_end
    out.attrs["rows_dropped_for_residual_gaps"] = rows_dropped

    if verbose:
        if dropped:
            print(
                f"[jgb_curve_history_loader] Ragged-tenor policy: dropped "
                f"{len(dropped)} tenor(s) not complete across the FULL requested "
                f"window: {dropped} -- either not yet published by MOF at the "
                f"window's start, or missing on at least one date within it. "
                f"Retaining {len(retained)}: {retained}.",
                file=sys.stderr,
            )
        else:
            print(
                f"[jgb_curve_history_loader] All {len(retained)} tenor(s) on the "
                f"source grid are complete for the requested window: {retained}.",
                file=sys.stderr,
            )
        if rows_dropped:
            print(
                f"[jgb_curve_history_loader] Dropped {rows_dropped} additional date "
                "row(s) with a residual gap in an otherwise-complete tenor -- treated "
                "as a genuine data outage, not interpolated.",
                file=sys.stderr,
            )
        print(
            f"[jgb_curve_history_loader] Retained date range: {window_start} to "
            f"{window_end} ({len(out)} observations).",
            file=sys.stderr,
        )

    return out


def load_jgb_curve_history(
    lookback_years: float | None = None,
    prefer_live: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Load a time series of JGB par yield curves.

    Parameters
    ----------
    lookback_years : keep only the last `lookback_years` years, measured
        back from the latest date the selected source tier actually has
        (see _apply_lookback_window). None (default) returns the full
        available history -- be aware this widens the window enough that
        the ragged-tenor policy (module docstring) will typically drop
        most tenors introduced after 1974; see
        docs/phase_4a_documentation.md for the numbers. Callers that want
        today's full 15-tenor grid should pass a window that starts after
        2007-11-06 (40Y's introduction) -- e.g. a few years.
    prefer_live, verbose : same meaning as load_jgb_curve's (Phase 1):
        prefer_live=True (default) tries live -> cache -> snapshot;
        prefer_live=False skips straight to the versioned snapshot, for a
        deterministic/reproducible run. verbose controls the stderr log.

    Returns
    -------
    pd.DataFrame, DatetimeIndex ascending (name "date"), one column per
    retained tenor (float years, ascending, columns.name
    "maturity_years"), yields as decimals, no NaN. See module docstring
    for what .attrs carries and its caveats.

    Raises
    ------
    ValueError : lookback_years <= 0, or the requested window/data leaves
        nothing usable after the ragged-tenor policy.
    """
    df_raw, source_tier = _load_raw_history(prefer_live=prefer_live, verbose=verbose)

    df_windowed = _apply_lookback_window(df_raw, lookback_years)
    if df_windowed.empty:
        raise ValueError(
            f"lookback_years={lookback_years} leaves no rows in the {source_tier} "
            f"history (available range: {df_raw.index.min().date()} to "
            f"{df_raw.index.max().date()})"
        )

    df_final = _apply_ragged_tenor_policy(df_windowed, verbose=verbose)
    df_final.attrs["source_tier"] = source_tier
    df_final.attrs["lookback_years"] = lookback_years
    return df_final


if __name__ == "__main__":
    for label, lookback in [
        ("last 2 years", 2.0),
        ("last 25 years", 25.0),
        ("full available history", None),
    ]:
        print()
        print("=" * 72)
        print(f"load_jgb_curve_history(lookback_years={lookback!r})  -- {label}")
        print("=" * 72)
        history = load_jgb_curve_history(lookback_years=lookback, prefer_live=False)
        print(
            f"Shape: {history.shape[0]} dates x {history.shape[1]} tenors, "
            f"{history.attrs['window_start']} to {history.attrs['window_end']}"
        )
        print(f"Tenors retained: {history.attrs['retained_tenors']}")
        print(f"Tenors dropped:  {history.attrs['dropped_tenors']}")
