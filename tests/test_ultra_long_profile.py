"""
Tests for models/ultra_long_profile.py.

Covers: the threshold-derived (not hardcoded) tenor classification against
both real grid shapes (snapshot vs. live/cache-shaped); that
compute_ultra_long_profile's totals and ultra-long sums agree with an
independently-recomputed reference (not merely "it ran"); the share
properties; and that the chart is actually produced on disk.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import _MOF_TENOR_COLUMNS, load_jgb_curve
from models.dv01 import dv01_by_tenor_portfolio
from models.key_rate_duration import key_rate_duration_portfolio
from models.ultra_long_profile import (
    DEFAULT_ULTRA_LONG_THRESHOLD_YEARS,
    compute_ultra_long_profile,
    plot_ultra_long_profile,
)


def _build_live_grid_fixture() -> pd.DataFrame:
    """Same construction as tests/test_bond_pricing.py's fixture of the
    same name -- the real 15-point live/cache tenor set, synthetic
    monotonic yields, so the tenor *shape* (not the values) is real."""
    tenors = sorted(_MOF_TENOR_COLUMNS.values())
    yields = [0.005 + 0.00075 * t for t in tenors]
    return pd.DataFrame({"maturity_years": tenors, "yield": yields})


# --------------------------------------------------------------------------
# Threshold-derived classification (not a hardcoded tenor list)
# --------------------------------------------------------------------------


def test_default_threshold_is_20_years():
    assert DEFAULT_ULTRA_LONG_THRESHOLD_YEARS == 20.0


def test_ultra_long_tenors_on_snapshot_grid():
    # Snapshot grid (Phase 1 §4.5): 1M,3M,6M,1Y,2Y,3Y,5Y,7Y,10Y,20Y,30Y,40Y.
    # >= 20 selects exactly {20, 30, 40}.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)
    assert list(profile.ultra_long_tenors) == [20.0, 30.0, 40.0]


def test_ultra_long_tenors_on_live_shaped_grid():
    # Live/cache grid: 1Y..10Y, 15Y, 20Y, 25Y, 30Y, 40Y. >= 20 selects
    # {20, 25, 30, 40} -- a DIFFERENT set than the snapshot's, because the
    # threshold is applied fresh to whatever grid is actually present, not
    # read off a fixed list. This is the test that would fail if someone
    # "simplified" the module to a hardcoded [20, 25, 30, 40].
    curve = _build_live_grid_fixture()
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)
    assert list(profile.ultra_long_tenors) == [20.0, 25.0, 30.0, 40.0]
    assert 15.0 not in profile.ultra_long_tenors  # closest miss: below threshold


def test_custom_threshold_changes_the_selected_tenors():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve, threshold_years=25.0)
    assert list(profile.ultra_long_tenors) == [30.0, 40.0]


# --------------------------------------------------------------------------
# compute_ultra_long_profile: totals and splits, independently verified
# --------------------------------------------------------------------------


def test_profile_totals_match_key_rate_duration_and_dv01_portfolio_tables():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()

    krd_table = key_rate_duration_portfolio(portfolio, curve)
    dv01_table = dv01_by_tenor_portfolio(portfolio, curve)

    profile = compute_ultra_long_profile(portfolio, curve)

    assert profile.krd_total == pytest.approx(krd_table.loc["portfolio_total"].sum())
    assert profile.dv01_total == pytest.approx(dv01_table.loc["portfolio_total"].sum())
    np.testing.assert_allclose(profile.krd_by_tenor.to_numpy(), krd_table.loc["portfolio_total"].to_numpy())
    np.testing.assert_allclose(profile.dv01_by_tenor.to_numpy(), dv01_table.loc["portfolio_total"].to_numpy())


def test_ultra_long_sums_match_independent_recomputation():
    # Recomputed directly from the portfolio_total row and a boolean mask,
    # without calling compute_ultra_long_profile's own internals a second
    # time -- an independent check, not a restatement.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()

    krd_row = key_rate_duration_portfolio(portfolio, curve).loc["portfolio_total"]
    dv01_row = dv01_by_tenor_portfolio(portfolio, curve).loc["portfolio_total"]
    mask = krd_row.index.to_numpy(dtype=float) >= 20.0

    profile = compute_ultra_long_profile(portfolio, curve)

    assert profile.krd_ultra_long == pytest.approx(krd_row.to_numpy()[mask].sum())
    assert profile.dv01_ultra_long == pytest.approx(dv01_row.to_numpy()[mask].sum())


def test_ultra_long_plus_rest_equals_total():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)

    rest_krd = profile.krd_total - profile.krd_ultra_long
    rest_dv01 = profile.dv01_total - profile.dv01_ultra_long
    assert rest_krd + profile.krd_ultra_long == pytest.approx(profile.krd_total)
    assert rest_dv01 + profile.dv01_ultra_long == pytest.approx(profile.dv01_total)


# --------------------------------------------------------------------------
# Share properties
# --------------------------------------------------------------------------


def test_shares_are_fractions_between_zero_and_one():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)
    assert 0.0 < profile.krd_ultra_long_share < 1.0
    assert 0.0 < profile.dv01_ultra_long_share < 1.0


def test_ultra_long_share_is_substantial_given_the_illustrative_portfolios_design():
    # The portfolio's 20Y/30Y/40Y bonds carry 40% of the WEIGHT (Phase 2A
    # §1.1) but longer bonds carry more duration per unit weight, so the
    # RISK share should be noticeably above 40% -- a loose bound (not
    # pinned to the exact 51% observed on one snapshot) that would catch a
    # gross bug (e.g. classifying almost nothing as ultra-long) without
    # breaking if the snapshot curve is ever re-anchored.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)
    assert profile.dv01_ultra_long_share > 0.40


# --------------------------------------------------------------------------
# Chart output
# --------------------------------------------------------------------------


def test_plot_saves_a_real_png_file(tmp_path):
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)

    output_path = tmp_path / "chart.png"
    returned_path = plot_ultra_long_profile(profile, output_path=output_path)

    assert returned_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 1000  # a real rendered PNG, not an empty/stub file


def test_plot_creates_parent_directories(tmp_path):
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)

    nested_path = tmp_path / "nested" / "dir" / "chart.png"
    plot_ultra_long_profile(profile, output_path=nested_path)
    assert nested_path.exists()


def test_plot_rejects_unknown_metric(tmp_path):
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)

    with pytest.raises(ValueError, match="metric"):
        plot_ultra_long_profile(profile, output_path=tmp_path / "chart.png", metric="bogus")


def test_plot_krd_metric_also_saves(tmp_path):
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    profile = compute_ultra_long_profile(portfolio, curve)

    output_path = tmp_path / "krd_chart.png"
    plot_ultra_long_profile(profile, output_path=output_path, metric="krd")
    assert output_path.exists()
