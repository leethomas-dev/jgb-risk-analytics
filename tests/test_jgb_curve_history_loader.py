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


# --------------------------------------------------------------------------
# Current-month top-up (_extend_with_recent_rows). The network pull is
# replaced with synthetic rows and the cache is pointed at tmp_path, so
# these never touch MOF or the real machine-local cache.
# --------------------------------------------------------------------------

import data.jgb_curve_history_loader as hl  # noqa: E402


def _rows(dates, value=0.01, columns=(1.0, 2.0, 3.0, 4.0, 5.0)) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="date")
    df = pd.DataFrame(value, index=idx, columns=pd.Index(list(columns), name="maturity_years"))
    return df


@pytest.fixture
def base_history():
    """A 'historical file' ending on Mon 2026-08-31, the last trading day of August."""
    return _rows(pd.bdate_range("2026-08-03", "2026-08-31"))


@pytest.fixture
def recent_env(tmp_path, monkeypatch):
    monkeypatch.setattr(hl, "RECENT_CACHE_PATH", tmp_path / "recent.csv")
    fresh = {"df": None}

    def fake_fetch():
        if fresh["df"] is None:
            raise ConnectionError("offline")
        return fresh["df"]

    monkeypatch.setattr(hl, "_fetch_current_month_rows", fake_fetch)
    return fresh


def test_month_complete_only_on_its_last_weekday():
    assert hl._month_is_complete_through(pd.Timestamp("2026-08-31"))
    assert not hl._month_is_complete_through(pd.Timestamp("2026-09-24"))  # Fri 9/25 and 9/28-30 remain
    assert hl._month_is_complete_through(pd.Timestamp("2026-12-30"))  # Dec 31 is a market holiday


def test_current_month_rows_are_appended(base_history, recent_env):
    recent_env["df"] = _rows(pd.bdate_range("2026-09-01", "2026-09-24"), value=0.02)
    out, n, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert n == len(pd.bdate_range("2026-09-01", "2026-09-24"))
    assert out.index.max() == pd.Timestamp("2026-09-24")
    assert out.index.is_monotonic_increasing and not out.index.duplicated().any()


def test_rows_survive_in_cache_when_the_pull_later_fails(base_history, recent_env):
    recent_env["df"] = _rows(pd.bdate_range("2026-09-01", "2026-09-10"), value=0.02)
    hl._extend_with_recent_rows(base_history, "live", verbose=False)
    recent_env["df"] = None  # offline now
    out, n, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert n == len(pd.bdate_range("2026-09-01", "2026-09-10"))
    assert out.loc["2026-09-10"].iloc[0] == pytest.approx(0.02)


def test_complete_previous_month_from_cache_bridges_into_the_new_month(base_history, recent_env):
    # Seen all of September before the month ended; now it's October and
    # MOF's historical file still stops at August.
    recent_env["df"] = _rows(pd.bdate_range("2026-09-01", "2026-09-30"))
    hl._extend_with_recent_rows(base_history, "live", verbose=False)
    recent_env["df"] = _rows(pd.bdate_range("2026-10-01", "2026-10-05"))
    out, _, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert out.index.max() == pd.Timestamp("2026-10-05")
    assert pd.Timestamp("2026-09-30") in out.index


def test_incomplete_previous_month_holds_back_the_new_month(base_history, recent_env):
    # Last saw September on the 24th -- 9/25..9/30 were never captured, so
    # October must NOT be appended (that would be a fake 1-week "daily" change).
    recent_env["df"] = _rows(pd.bdate_range("2026-09-01", "2026-09-24"))
    hl._extend_with_recent_rows(base_history, "live", verbose=False)
    recent_env["df"] = _rows(pd.bdate_range("2026-10-01", "2026-10-05"))
    out, _, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert out.index.max() == pd.Timestamp("2026-09-24")


def test_a_skipped_month_is_never_bridged(base_history, recent_env):
    recent_env["df"] = _rows(pd.bdate_range("2026-10-01", "2026-10-05"))
    out, n, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert n == 0 and out.index.max() == pd.Timestamp("2026-08-31")


def test_fresh_rows_win_over_cached_rows_for_the_same_date(base_history, recent_env):
    recent_env["df"] = _rows(["2026-09-01"], value=0.02)
    hl._extend_with_recent_rows(base_history, "live", verbose=False)
    recent_env["df"] = _rows(["2026-09-01", "2026-09-02"], value=0.03)
    out, _, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert out.loc["2026-09-01"].iloc[0] == pytest.approx(0.03)


def test_row_missing_a_tenor_is_skipped_not_appended(base_history, recent_env):
    fresh = _rows(pd.bdate_range("2026-09-01", "2026-09-03"))
    fresh.loc["2026-09-03", 5.0] = np.nan
    recent_env["df"] = fresh
    out, n, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert n == 2 and not out.isna().any().any()


def test_rows_now_in_the_historical_file_are_pruned_from_the_cache(base_history, recent_env):
    recent_env["df"] = _rows(pd.bdate_range("2026-08-27", "2026-09-02"))
    hl._extend_with_recent_rows(base_history, "live", verbose=False)
    cached = hl._read_recent_cache(verbose=False)
    assert cached.index.min() > base_history.index.max()


def test_cache_round_trips_exact_yields(tmp_path, monkeypatch):
    monkeypatch.setattr(hl, "RECENT_CACHE_PATH", tmp_path / "recent.csv")
    df = _rows(["2026-09-01"], value=0.01527)
    hl._write_recent_cache(df)
    back = hl._read_recent_cache(verbose=False)
    assert back.iloc[0].tolist() == pytest.approx([0.01527] * 5, abs=1e-12)


def test_no_fetch_and_no_cache_leaves_history_unchanged(base_history, recent_env):
    out, n, _ = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert n == 0 and out.equals(base_history)


def test_prefer_live_false_never_tops_up(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("top-up must not run when prefer_live=False")

    monkeypatch.setattr(hl, "_extend_with_recent_rows", boom)
    h = load_jgb_curve_history(lookback_years=2, prefer_live=False, verbose=False)
    assert h.index.max() == SNAPSHOT_LAST_DATE
    assert h.attrs["recent_rows_appended"] == 0


def test_status_reports_a_failed_pull(base_history, recent_env):
    _, _, status = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert status == {"current_month_pull": "failed", "held_back": None}


def test_status_reports_held_back_rows(base_history, recent_env):
    recent_env["df"] = _rows(pd.bdate_range("2026-09-01", "2026-09-24"))
    hl._extend_with_recent_rows(base_history, "live", verbose=False)
    recent_env["df"] = _rows(pd.bdate_range("2026-10-01", "2026-10-05"))
    _, _, status = hl._extend_with_recent_rows(base_history, "live", verbose=False)
    assert status == {"current_month_pull": "ok", "held_back": ("2026-10-01", "2026-10-05")}


def test_prefer_live_false_reports_the_top_up_as_skipped():
    h = load_jgb_curve_history(lookback_years=2, prefer_live=False, verbose=False)
    assert h.attrs["current_month_pull"] == "skipped"
    assert h.attrs["recent_rows_held_back"] is None


# --------------------------------------------------------------------------
# missing_recent_days_note -- the dashboard's "what's missing and why" text.
# --------------------------------------------------------------------------

from datetime import date  # noqa: E402


def _history_ending(last: str, **attrs) -> pd.DataFrame:
    h = _rows(pd.bdate_range("2026-08-03", last))
    h.attrs.update({"source_tier": "live", "current_month_pull": "ok", "recent_rows_held_back": None})
    h.attrs.update(attrs)
    return h


def test_no_note_when_history_reaches_the_latest_published_day():
    h = _history_ending("2026-09-24")
    assert hl.missing_recent_days_note(h, date(2026, 9, 24), date(2026, 9, 26)) is None


def test_no_note_over_a_weekend_when_mof_cannot_be_reached():
    h = _history_ending("2026-09-25")  # Friday; today is Sunday
    assert hl.missing_recent_days_note(h, None, date(2026, 9, 27)) is None


def test_note_names_the_missing_range_and_the_failed_pull():
    h = _history_ending("2026-08-31", current_month_pull="failed")
    note = hl.missing_recent_days_note(h, date(2026, 9, 24), date(2026, 9, 26))
    assert "2026-09-01 to 2026-09-24" in note
    assert "stop at 2026-08-31" in note
    assert "current-month download from MOF failed" in note


def test_note_says_when_it_could_not_check_mof():
    h = _history_ending("2026-08-31", current_month_pull="failed")
    note = hl.missing_recent_days_note(h, None, date(2026, 9, 24))
    assert "2026-09-01 to 2026-09-24" in note and "couldn't be reached" in note


def test_note_explains_held_back_rows():
    h = _history_ending("2026-09-24", recent_rows_held_back=("2026-10-01", "2026-10-05"))
    note = hl.missing_recent_days_note(h, date(2026, 10, 5), date(2026, 10, 6))
    assert "2026-09-25 to 2026-10-05" in note
    assert "2026-10-01 to 2026-10-05 are saved on this machine but held back" in note


def test_note_mentions_a_fallback_history_tier():
    h = _history_ending("2026-08-31", source_tier="snapshot", current_month_pull="failed")
    note = hl.missing_recent_days_note(h, date(2026, 9, 24), date(2026, 9, 26))
    assert "saved snapshot copy" in note


def test_unchecked_range_ends_on_a_business_day_not_a_weekend():
    h = _history_ending("2026-08-31", current_month_pull="failed")
    note = hl.missing_recent_days_note(h, None, date(2026, 9, 26))  # a Saturday
    assert "2026-09-01 to 2026-09-25" in note
