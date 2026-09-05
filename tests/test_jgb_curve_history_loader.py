"""
Tests for data/jgb_curve_history_loader.py.

Covers: the output contract (decimal yields, sorted ascending tenor
columns, DatetimeIndex ascending, no NaN); lookback-window date-range
filtering; the ragged-tenor policy's behavior at three windows chosen to
exercise it concretely against the real committed snapshot (all tenors
complete, two dropped by a tenor-introduction boundary, and most dropped
by the full-history window); and input validation.

All curve loading uses prefer_live=False for determinism -- the snapshot
file (data/jgb_curve_history_snapshot.csv) is a fixed, versioned vintage
(fetched 2026-09-04, 1974-09-24 to 2026-08-31), so exact tenor/row counts
below are pinned to it and would need updating if that file is re-anchored
to a newer pull (see docs/phase_4a_documentation.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_history_loader import (
    MAX_PLAUSIBLE_YIELD_HISTORY,
    MIN_PLAUSIBLE_YIELD_HISTORY,
    _parse_mof_history_csv,
    load_jgb_curve_history,
)

SNAPSHOT_FIRST_DATE = pd.Timestamp("1974-09-24")
SNAPSHOT_LAST_DATE = pd.Timestamp("2026-08-31")
ALL_15_TENORS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0]


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------


def test_output_is_datetime_indexed_ascending_no_duplicates():
    df = load_jgb_curve_history(lookback_years=2.0, prefer_live=False, verbose=False)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert not df.index.duplicated().any()


def test_output_columns_are_ascending_tenor_years():
    df = load_jgb_curve_history(lookback_years=2.0, prefer_live=False, verbose=False)
    tenors = list(df.columns)
    assert tenors == sorted(tenors)
    assert df.columns.name == "maturity_years"


def test_output_has_no_missing_values():
    for lookback in (2.0, 25.0, None):
        df = load_jgb_curve_history(lookback_years=lookback, prefer_live=False, verbose=False)
        assert not df.isna().any().any()


def test_yields_are_decimal_not_percent():
    # A real JGB yield as a decimal is a small number (well under 1.0);
    # if the /100 conversion were skipped this would be ~100x too large.
    df = load_jgb_curve_history(lookback_years=2.0, prefer_live=False, verbose=False)
    assert df.to_numpy().max() < 1.0
    assert df.to_numpy().min() > MIN_PLAUSIBLE_YIELD_HISTORY - 0.01


def test_all_values_within_the_historical_plausibility_band():
    df = load_jgb_curve_history(lookback_years=None, prefer_live=False, verbose=False)
    assert df.to_numpy().min() >= MIN_PLAUSIBLE_YIELD_HISTORY
    assert df.to_numpy().max() <= MAX_PLAUSIBLE_YIELD_HISTORY


# --------------------------------------------------------------------------
# Lookback window: date-range filtering
# --------------------------------------------------------------------------


def test_lookback_years_none_returns_full_available_history():
    df = load_jgb_curve_history(lookback_years=None, prefer_live=False, verbose=False)
    assert df.index.min() == SNAPSHOT_FIRST_DATE
    assert df.index.max() == SNAPSHOT_LAST_DATE
    assert df.attrs["lookback_years"] is None


def test_lookback_years_restricts_the_date_range():
    df_2y = load_jgb_curve_history(lookback_years=2.0, prefer_live=False, verbose=False)
    df_25y = load_jgb_curve_history(lookback_years=25.0, prefer_live=False, verbose=False)

    assert df_2y.index.max() == SNAPSHOT_LAST_DATE
    assert df_25y.index.max() == SNAPSHOT_LAST_DATE
    assert df_2y.index.min() > df_25y.index.min()
    assert len(df_2y) < len(df_25y)


def test_lookback_window_is_anchored_to_the_sources_own_latest_date():
    # Not "today" -- a prefer_live=False run must be reproducible regardless
    # of when it's actually executed.
    df = load_jgb_curve_history(lookback_years=1.0, prefer_live=False, verbose=False)
    expected_cutoff = SNAPSHOT_LAST_DATE - pd.Timedelta(days=1.0 * 365.25)
    assert df.index.min() >= expected_cutoff
    assert (df.index.min() - expected_cutoff).days <= 4  # lands on the nearest business day


def test_non_positive_lookback_years_raises():
    with pytest.raises(ValueError, match="lookback_years"):
        load_jgb_curve_history(lookback_years=0, prefer_live=False, verbose=False)
    with pytest.raises(ValueError, match="lookback_years"):
        load_jgb_curve_history(lookback_years=-5, prefer_live=False, verbose=False)


# --------------------------------------------------------------------------
# Ragged-tenor policy -- three windows chosen to exercise it concretely
# against the real committed snapshot (see module docstring's reasoning
# and docs/phase_4a_documentation.md for the underlying MOF tenor
# introduction dates: 10Y/20Y 1986, 15Y 1991, 30Y 1999, 25Y 2004, 40Y 2007).
# --------------------------------------------------------------------------


def test_short_window_retains_all_15_tenors():
    # A 2-year window starting in 2024 is well after every tenor's
    # introduction (the latest, 40Y, was 2007-11-06) and well clear of the
    # 1978-1980 gap in 1Y/2Y/3Y -- nothing should be dropped.
    df = load_jgb_curve_history(lookback_years=2.0, prefer_live=False, verbose=False)
    assert list(df.columns) == ALL_15_TENORS
    assert df.attrs["dropped_tenors"] == []
    assert df.attrs["retained_tenors"] == ALL_15_TENORS


def test_25_year_window_drops_tenors_introduced_after_its_start():
    # A 25-year window starts ~2001-08, before 25Y (2004-03-22) and 40Y
    # (2007-11-06) existed -- both must be dropped for the WHOLE window,
    # not interpolated. 30Y (1999-09-02) is already active, so it survives.
    df = load_jgb_curve_history(lookback_years=25.0, prefer_live=False, verbose=False)
    assert 25.0 not in df.columns
    assert 40.0 not in df.columns
    assert 30.0 in df.columns
    assert set(df.attrs["dropped_tenors"]) == {25.0, 40.0}


def test_full_history_window_drops_most_tenors_including_a_genuine_gap_case():
    # The full 1974-2026 window is the sharpest illustration of the policy:
    # every tenor introduced after 1974 (10Y, 15Y, 20Y, 25Y, 30Y, 40Y) is
    # dropped for not covering the window's start -- AND 1Y/2Y/3Y, present
    # since 1974, are ALSO dropped, because they have a genuine multi-month
    # reporting gap in 1978-1980 somewhere inside this window. Only tenors
    # with zero missing days across the entire 52-year span survive.
    df = load_jgb_curve_history(lookback_years=None, prefer_live=False, verbose=False)
    assert list(df.columns) == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    assert set(df.attrs["dropped_tenors"]) == {1.0, 2.0, 3.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0}


def test_retention_report_is_visible_on_the_returned_object():
    # The policy's outcome must be readable from the function's own return
    # value, not just printed -- a caller building on this shouldn't have
    # to re-derive which tenors survived.
    df = load_jgb_curve_history(lookback_years=25.0, prefer_live=False, verbose=False)
    assert df.attrs["window_start"] == df.index.min().date().isoformat()
    assert df.attrs["window_end"] == df.index.max().date().isoformat()
    assert df.attrs["source_tier"] == "snapshot"
    assert isinstance(df.attrs["rows_dropped_for_residual_gaps"], (int, np.integer))


def test_dropped_and_retained_tenors_partition_the_original_grid():
    df = load_jgb_curve_history(lookback_years=25.0, prefer_live=False, verbose=False)
    retained = set(df.attrs["retained_tenors"])
    dropped = set(df.attrs["dropped_tenors"])
    assert retained & dropped == set()
    assert retained | dropped == set(ALL_15_TENORS)
    assert retained == set(df.columns)


# --------------------------------------------------------------------------
# Duplicate-date handling in the shared parser -- resolved (keep last), and
# reported rather than silently dropped, the same "nothing silent" standard
# the ragged-tenor policy holds itself to. Never observed against a real MOF
# pull, so exercised here against a small synthetic file built to trigger it.
# --------------------------------------------------------------------------


def _synthetic_mof_csv(n_rows: int = 260, duplicate_last_date: bool = False) -> str:
    """A minimal but structurally valid MOF-style CSV: a title line, the
    real 'Date,...' header, then n_rows of business-day rows across 5
    tenors (MIN_TENORS_FOR_VALID_PULL) -- enough to clear
    MIN_ROWS_FOR_VALID_PULL. If duplicate_last_date, the final date is
    repeated once more with different values, to see which one survives."""
    dates = pd.bdate_range("2020-01-01", periods=n_rows)
    lines = ["Interest Rate (synthetic),,,,,", "Date,1Y,2Y,3Y,4Y,5Y"]
    for i, d in enumerate(dates):
        row_values = [f"{0.5 + 0.001 * i:.3f}"] * 5
        lines.append(f"{d.strftime('%Y/%m/%d')}," + ",".join(row_values))
    if duplicate_last_date:
        # Same date as the last row, but with distinguishable values.
        lines.append(f"{dates[-1].strftime('%Y/%m/%d')}," + ",".join(["9.999"] * 5))
    return "\n".join(lines)


def test_duplicate_date_in_raw_csv_keeps_the_last_occurrence():
    text = _synthetic_mof_csv(duplicate_last_date=True)
    df = _parse_mof_history_csv(text)
    assert not df.index.duplicated().any()
    assert df.loc[df.index.max()].iloc[0] == pytest.approx(9.999 / 100.0)


def test_duplicate_date_is_logged_when_verbose(capsys):
    text = _synthetic_mof_csv(duplicate_last_date=True)
    _parse_mof_history_csv(text, verbose=True)
    assert "duplicate date" in capsys.readouterr().err.lower()


def test_no_duplicate_log_when_none_present(capsys):
    text = _synthetic_mof_csv(duplicate_last_date=False)
    _parse_mof_history_csv(text, verbose=True)
    assert "duplicate date" not in capsys.readouterr().err.lower()


def test_duplicate_date_is_silent_when_not_verbose(capsys):
    text = _synthetic_mof_csv(duplicate_last_date=True)
    _parse_mof_history_csv(text, verbose=False)
    assert capsys.readouterr().err == ""
