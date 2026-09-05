"""
jgb_curve_loader.py

Loads a Japanese Government Bond (JGB) par yield curve for the pricing /
KRD / DV01 models in /models.

Curve sources, tried in order:

1. LIVE: pull the current curve directly from Japan's Ministry of Finance
   (MOF), which publishes JGB reference yields as a public CSV. Wrapped
   in try/except so any failure (no network, a format change, a timeout)
   never crashes the program; a successful pull is validated and written
   to a local cache (see 2).

2. CACHE: the most recent curve a live pull returned ON THIS MACHINE,
   used when the live pull fails so an offline run still gets a real,
   recent curve. Machine-local, gitignored, never committed.

3. SNAPSHOT: a real MOF curve for one fixed past date, embedded as
   constants and served UNMODIFIED. The floor -- reached only when the
   live pull fails AND no cache exists. May be stale since it's a fixed
   date; refresh by committing a newer snapshot (docs/phase_1_documentation.md
   §5).

Output contract: load_jgb_curve() returns a DataFrame with columns
maturity_years (float, years) and yield (float, decimal -- 0.0288, not
2.88), sorted ascending with a fresh RangeIndex.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

MOF_CSV_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
LIVE_REQUEST_TIMEOUT_SECONDS = 10

# Local write-through cache: the most recent validated live curve. Sits next to
# this module, is rewritten on every successful pull, and is gitignored (it is
# machine-local state, not source). See docs/phase_1_documentation.md §1.4.
CACHE_PATH = Path(__file__).with_name("_jgb_curve_cache.json")

# Plausibility band for a JGB par yield expressed as a decimal. A curve with any
# yield outside this range is treated as a parse/format failure, not as data --
# the semantic check docs/phase_1_documentation.md §3.4 discusses.
# It gates both the live pull and anything read back from the cache.
MIN_PLAUSIBLE_YIELD = -0.01  # -1%
MAX_PLAUSIBLE_YIELD = 0.10  # +10%

# ---------------------------------------------------------------------------
# SNAPSHOT floor: a REAL MOF JGB par yield curve for SNAPSHOT_DATE (percent).
# Actual published reference rates, not synthetic/interpolated values, and
# served without any transformation. Keys are tenor in years, values percent.
# Refresh by replacing both constants with a newer real curve (see §5).
# ---------------------------------------------------------------------------
SNAPSHOT_DATE = "2026-04-06"
SNAPSHOT_CURVE_PCT: dict[float, float] = {
    0.083: 0.77,
    0.25: 0.87,
    0.5: 0.91,
    1: 1.12,
    2: 1.40,
    3: 1.60,
    5: 1.82,
    7: 2.19,
    10: 2.40,
    20: 3.32,
    30: 3.73,
    40: 3.91,
}

# Column headers on the MOF CSV -> tenor in years. MOF's English CSV header
# row looks like: Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y
_MOF_TENOR_COLUMNS: dict[str, float] = {
    "1Y": 1,
    "2Y": 2,
    "3Y": 3,
    "4Y": 4,
    "5Y": 5,
    "6Y": 6,
    "7Y": 7,
    "8Y": 8,
    "9Y": 9,
    "10Y": 10,
    "15Y": 15,
    "20Y": 20,
    "25Y": 25,
    "30Y": 30,
    "40Y": 40,
}


def _standardize_curve(rows: list[dict[str, float]]) -> pd.DataFrame:
    """Return the canonical two-column curve frame: sorted, fresh RangeIndex."""
    df = pd.DataFrame(rows, columns=["maturity_years", "yield"])
    df = df.astype({"maturity_years": float, "yield": float})
    return df.sort_values("maturity_years").reset_index(drop=True)


def _validate_curve_df(df: pd.DataFrame, *, min_tenors: int = 5) -> None:
    """Raise ValueError unless df is a plausible JGB par curve.

    Checks structure (columns, tenor count, positive unique maturities, no NaN)
    and the semantic yield band [MIN_PLAUSIBLE_YIELD, MAX_PLAUSIBLE_YIELD]. Run
    on the live pull and on anything read back from the cache, so neither path
    can hand a malformed-but-parseable curve to the models.
    """
    if list(df.columns) != ["maturity_years", "yield"]:
        raise ValueError(f"curve has unexpected columns: {list(df.columns)}")
    if len(df) < min_tenors:
        raise ValueError(f"curve has only {len(df)} tenor(s) (< {min_tenors})")
    if df["maturity_years"].le(0).any():
        raise ValueError("curve has a non-positive maturity")
    if df["maturity_years"].duplicated().any():
        raise ValueError("curve has duplicate maturities")
    if df["yield"].isna().any():
        raise ValueError("curve has a NaN yield")
    bad = df.loc[
        (df["yield"] < MIN_PLAUSIBLE_YIELD) | (df["yield"] > MAX_PLAUSIBLE_YIELD)
    ]
    if not bad.empty:
        raise ValueError(
            f"curve has yields outside [{MIN_PLAUSIBLE_YIELD}, {MAX_PLAUSIBLE_YIELD}]: "
            f"{bad.to_dict('records')}"
        )


def _snapshot_curve_dataframe() -> pd.DataFrame:
    """Build the embedded SNAPSHOT_DATE curve, served unmodified (no shift)."""
    rows = [
        {"maturity_years": float(tenor), "yield": pct / 100.0}
        for tenor, pct in sorted(SNAPSHOT_CURVE_PCT.items())
    ]
    return _standardize_curve(rows)


def _find_mof_header_row_index(lines: list[str]) -> int | None:
    """Locate the "Date,1Y,2Y,..." header row inside a raw MOF CSV.

    Both the current-curve file and the historical file
    (data/jgb_curve_history_loader.py) share this exact layout: a title
    line first (e.g. "Interest Rate (August 2026),,,..." or, for the
    historical file, just "Interest Rate,,,..."), then the real header,
    then data rows, then a stray footer note. Searching by content rather
    than assuming a fixed line number survives either title format.
    Returns None if no such row is found.
    """
    return next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)


def _fetch_live_curve() -> pd.DataFrame:
    """Pull the latest published JGB curve directly from MOF.

    Raises on any failure (network, HTTP status, parsing, implausible values) --
    callers are expected to catch and fall back to the cache or snapshot.
    """
    response = requests.get(MOF_CSV_URL, timeout=LIVE_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    from io import StringIO

    lines = response.text.splitlines()
    header_idx = _find_mof_header_row_index(lines)
    if header_idx is None:
        raise ValueError("Unexpected MOF CSV format: no 'Date,...' header row found")

    raw = pd.read_csv(StringIO("\n".join(lines[header_idx:])), skip_blank_lines=True)
    if "Date" not in raw.columns:
        raise ValueError("Unexpected MOF CSV format: no 'Date' column found")

    # The file ends with a stray footer note in the Date column (e.g. a
    # "clear your browser cache" tip) whose tenor columns are all blank.
    # Keep only rows that actually parse as dates.
    parsed_dates = pd.to_datetime(raw["Date"], format="%Y/%m/%d", errors="coerce")
    data_rows = raw.loc[parsed_dates.notna()]
    if data_rows.empty:
        raise ValueError("MOF CSV had a 'Date' column but no parseable date rows")
    latest_row = data_rows.iloc[-1]

    rows = []
    for col, tenor in _MOF_TENOR_COLUMNS.items():
        if col not in raw.columns:
            continue
        value = pd.to_numeric(latest_row[col], errors="coerce")
        if pd.isna(value):
            continue
        rows.append({"maturity_years": float(tenor), "yield": float(value) / 100.0})

    if len(rows) < 5:
        # Too few usable tenors to call this a real curve pull.
        raise ValueError("MOF CSV parsed but yielded too few usable tenors")

    df = _standardize_curve(rows)
    _validate_curve_df(df)
    return df


def _write_cache(df: pd.DataFrame) -> None:
    """Atomically persist a validated curve to CACHE_PATH with a UTC timestamp."""
    payload = {
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": MOF_CSV_URL,
        "curve": [
            [float(m), float(y)] for m, y in zip(df["maturity_years"], df["yield"])
        ],
    }
    fd, tmp_name = tempfile.mkstemp(
        dir=str(CACHE_PATH.parent), prefix="._jgb_cache_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_name, CACHE_PATH)  # atomic within the same filesystem
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_cache() -> tuple[pd.DataFrame, str, int]:
    """Return (curve, fetched_utc, age_days) from CACHE_PATH.

    Raises on a missing / unreadable / malformed / implausible cache.
    """
    with open(CACHE_PATH) as fh:
        payload = json.load(fh)
    fetched_utc = str(payload["fetched_utc"])
    df = _standardize_curve(
        [{"maturity_years": m, "yield": y} for m, y in payload["curve"]]
    )
    _validate_curve_df(df)
    fetched_dt = datetime.strptime(fetched_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    age_days = (datetime.now(timezone.utc) - fetched_dt).days
    return df, fetched_utc, age_days


def load_jgb_curve(prefer_live: bool = True, verbose: bool = True) -> pd.DataFrame:
    """Load the JGB par yield curve.

    Parameters
    ----------
    prefer_live : if True (default), try the live MOF pull first; on success
        cache it and return it, on failure fall back to the local cache and then
        to the embedded snapshot. If False, skip BOTH the live pull and the cache
        and return the embedded snapshot directly -- use this for deterministic
        tests / CI and for reproducible SR 11-7 validation runs.
    verbose : print which source was used (and, for the cache, how old it is) to
        stderr.

    Returns
    -------
    pd.DataFrame with columns [maturity_years, yield] (yield as a decimal),
    sorted ascending by maturity_years with a fresh RangeIndex.
    """
    if prefer_live:
        try:
            df = _fetch_live_curve()
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all fallback
            if verbose:
                print(
                    f"[jgb_curve_loader] Live MOF pull failed ({exc!r}); "
                    "trying local cache.",
                    file=sys.stderr,
                )
        else:
            try:
                _write_cache(df)
            except Exception as exc:  # noqa: BLE001 - cache write must not break a good pull
                if verbose:
                    print(
                        f"[jgb_curve_loader] Could not update cache ({exc!r}); "
                        "continuing with the live curve.",
                        file=sys.stderr,
                    )
            if verbose:
                print(
                    "[jgb_curve_loader] Loaded LIVE curve from MOF.", file=sys.stderr
                )
            return df

        if CACHE_PATH.exists():
            try:
                df, fetched_utc, age_days = _read_cache()
            except Exception as exc:  # noqa: BLE001 - bad cache -> fall through to snapshot
                if verbose:
                    print(
                        f"[jgb_curve_loader] Cache present but unusable ({exc!r}); "
                        "falling back to the embedded snapshot.",
                        file=sys.stderr,
                    )
            else:
                if verbose:
                    print(
                        f"[jgb_curve_loader] Loaded CACHED curve from {fetched_utc} "
                        f"({age_days} day(s) old) -- last successful live pull on "
                        "this machine.",
                        file=sys.stderr,
                    )
                return df

    df = _snapshot_curve_dataframe()
    if verbose:
        why = "no live pull and no usable cache" if prefer_live else "prefer_live=False"
        print(
            f"[jgb_curve_loader] Loaded SNAPSHOT curve: real MOF par curve for "
            f"{SNAPSHOT_DATE}, served unmodified ({why}). A fixed past date -- it "
            "may be stale; see docs/phase_1_documentation.md §5.",
            file=sys.stderr,
        )
    return df


if __name__ == "__main__":
    curve = load_jgb_curve()
    print()
    print("JGB par yield curve:")
    print(curve.to_string(index=False, formatters={"yield": lambda y: f"{y:.4%}"}))

    is_monotonic = curve["yield"].is_monotonic_increasing
    front_to_back_upward = curve["yield"].iloc[0] < curve["yield"].iloc[-1]
    print()
    print(f"Strictly monotonic increasing (every tenor > previous tenor): {is_monotonic}")
    print(f"Upward-sloping overall (short end < long end): {front_to_back_upward}")
    if not is_monotonic and front_to_back_upward:
        print(
            "Note: a real JGB curve is not required to be strictly monotonic -- "
            "local inversions at the long end (e.g. 20s/30s, 25s/30s) are a known, "
            "real feature driven by insurer/pension duration-extension demand and "
            "supply technicals, not a data error."
        )
