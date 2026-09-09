"""
Tests for models/factor_exposure.py.

Covers: the tenor-alignment policy (KRD/DV01 recomputed on PCA's own
grid, not the native curve grid; extrapolation detected when it would
occur); the linear P&L estimate against an independent recomputation and
against an exact reprice; the dominant-component finder; and the chart
output.

All curve/history loading uses prefer_live=False for determinism. Exact
figures below are pinned to data/jgb_curve_history_snapshot.csv's fixed
vintage (fetched 2026-09-04) and the Phase 1 curve snapshot -- see
docs/phase_4c_documentation.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from data.jgb_curve_history_loader import load_jgb_curve_history
from models.bond_pricing import price_portfolio
from models.dv01 import DEFAULT_BUMP_SIZE, dv01_by_tenor_portfolio
from models.key_rate_duration import key_rate_duration_portfolio
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca
from models.factor_exposure import (
    FactorExposure,
    PortfolioFactorExposureResult,
    _curve_aligned_to_pca_grid,
    compute_portfolio_factor_exposure,
    plot_factor_exposure,
)


def _pca_result():
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    return compute_curve_pca(history)


def _curve():
    return load_jgb_curve(prefer_live=False, verbose=False)


def _portfolio():
    return load_portfolio()


# --------------------------------------------------------------------------
# Tenor alignment -- the main design problem this module solves
# --------------------------------------------------------------------------


def test_curve_and_pca_grids_genuinely_differ_on_real_data():
    # If this ever stops being true, the alignment machinery below is
    # untested against the actual problem it exists for.
    curve_tenors = set(_curve()["maturity_years"])
    pca_tenors = set(_pca_result().tenors)
    assert curve_tenors != pca_tenors
    assert not pca_tenors.issubset(curve_tenors)
    assert not curve_tenors.issubset(pca_tenors)


def test_krd_and_dv01_are_indexed_on_the_pca_grid_not_the_curve_grid():
    pca_result = _pca_result()
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), pca_result)
    np.testing.assert_array_equal(result.krd_by_tenor.index.to_numpy(dtype=float), pca_result.tenors)
    np.testing.assert_array_equal(result.dv01_by_tenor.index.to_numpy(dtype=float), pca_result.tenors)


def test_aligned_curve_yields_match_direct_interpolation_of_the_source_curve():
    from models.bond_pricing import curve_yield_at

    curve = _curve()
    pca_tenors = _pca_result().tenors
    aligned, extrapolated = _curve_aligned_to_pca_grid(curve, pca_tenors)

    expected = curve_yield_at(curve, np.sort(pca_tenors))
    np.testing.assert_allclose(aligned["yield"].to_numpy(), expected)
    assert list(aligned["maturity_years"]) == sorted(pca_tenors)


def test_no_extrapolation_needed_on_this_projects_real_grids():
    # Checked directly: Phase 4A's tenor range (1Y-40Y) sits entirely
    # inside Phase 1's curve range on both real data sources.
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    assert len(result.extrapolated_tenors) == 0


def test_extrapolation_is_detected_when_the_curve_is_narrower_than_the_pca_grid():
    # Synthetic case: a curve that doesn't cover the PCA grid's full
    # range, to prove the detection actually fires rather than always
    # reporting empty by construction.
    narrow_curve = pd.DataFrame({"maturity_years": [2.0, 5.0, 10.0], "yield": [0.01, 0.015, 0.02]})
    pca_tenors = np.array([1.0, 2.0, 5.0, 10.0, 20.0])
    _, extrapolated = _curve_aligned_to_pca_grid(narrow_curve, pca_tenors)
    assert set(extrapolated) == {1.0, 20.0}


# --------------------------------------------------------------------------
# The linear P&L estimate -- independent recomputation and exact-reprice check
# --------------------------------------------------------------------------


def test_pct_pnl_matches_independent_krd_dot_shock_recomputation():
    pca_result = _pca_result()
    curve = _curve()
    portfolio = _portfolio()
    result = compute_portfolio_factor_exposure(portfolio, curve, pca_result)

    aligned, _ = _curve_aligned_to_pca_grid(curve, pca_result.tenors)
    krd_row = key_rate_duration_portfolio(portfolio, aligned).loc["portfolio_total"]

    for e in result.exposures:
        shock = pca_result.implied_yield_shock(e.component).reindex(krd_row.index)
        expected = -float((krd_row * shock).sum())
        assert e.pct_pnl == pytest.approx(expected)


def test_dollar_pnl_matches_independent_dv01_dot_shock_recomputation():
    pca_result = _pca_result()
    curve = _curve()
    portfolio = _portfolio()
    result = compute_portfolio_factor_exposure(portfolio, curve, pca_result)

    aligned, _ = _curve_aligned_to_pca_grid(curve, pca_result.tenors)
    dv01_row = dv01_by_tenor_portfolio(portfolio, aligned).loc["portfolio_total"]

    for e in result.exposures:
        shock = pca_result.implied_yield_shock(e.component).reindex(dv01_row.index)
        expected = -float((dv01_row * (shock / DEFAULT_BUMP_SIZE)).sum())
        assert e.dollar_pnl == pytest.approx(expected)


def test_dollar_and_pct_pnl_agree_closely_since_bonds_price_near_par():
    # Not expected to be bit-identical (see compute_portfolio_factor_exposure's
    # docstring: KRD is weighted by portfolio weight, DV01 by each bond's
    # own price) -- but the portfolio's bonds all price near par by
    # construction, so the ABSOLUTE gap should be small (a relative check
    # would be meaningless for PC3, whose P&L is itself near zero).
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    for e in result.exposures:
        implied_dollar = e.pct_pnl * result.base_price
        assert abs(e.dollar_pnl - implied_dollar) < 0.01  # < 1 cent per 100 face


def test_linear_estimate_matches_exact_reprice_within_a_tight_tolerance():
    # The real accuracy check for the linear (KRD-based) approximation --
    # not asserted, computed: build the actual shocked curve and reprice
    # the whole portfolio, then compare.
    pca_result = _pca_result()
    curve = _curve()
    portfolio = _portfolio()
    result = compute_portfolio_factor_exposure(portfolio, curve, pca_result)

    base = float(
        (price_portfolio(portfolio, result.aligned_curve)["weight"]
         * price_portfolio(portfolio, result.aligned_curve)["price"]).sum()
    )
    for e in result.exposures:
        shock = pca_result.implied_yield_shock(e.component).reindex(result.krd_by_tenor.index)
        shocked_curve = result.aligned_curve.copy()
        shocked_curve["yield"] = shocked_curve["yield"] + shock.to_numpy()
        shocked_price = float(
            (price_portfolio(portfolio, shocked_curve)["weight"]
             * price_portfolio(portfolio, shocked_curve)["price"]).sum()
        )
        exact_pct = (shocked_price - base) / base
        assert e.pct_pnl == pytest.approx(exact_pct, abs=0.001)  # within 10bp of price


# --------------------------------------------------------------------------
# Result shape and the dominant-component finder
# --------------------------------------------------------------------------


def test_returns_one_exposure_per_pca_component():
    pca_result = _pca_result()
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), pca_result)
    assert len(result.exposures) == pca_result.n_components
    assert [e.component for e in result.exposures] == list(range(1, pca_result.n_components + 1))


def test_explained_variance_ratio_carried_over_from_pca_result():
    pca_result = _pca_result()
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), pca_result)
    for e in result.exposures:
        assert e.explained_variance_ratio == pytest.approx(
            pca_result.explained_variance_ratio[e.component - 1]
        )


def test_dominant_component_is_the_largest_absolute_dollar_pnl():
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    dominant = result.dominant_component()
    assert all(abs(dominant.dollar_pnl) >= abs(e.dollar_pnl) for e in result.exposures)


def test_pc1_dominates_on_this_projects_real_data():
    # A concrete, checked finding (not assumed): PC1 explains the most
    # variance AND its loadings stay elevated through the portfolio's
    # ultra-long concentration (docs/phase_4c_documentation.md), so it
    # should dominate the $ P&L too.
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    assert result.dominant_component().component == 1


# --------------------------------------------------------------------------
# Chart output
# --------------------------------------------------------------------------


def test_plot_saves_a_real_png_file(tmp_path):
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    output_path = tmp_path / "chart.png"
    returned_path = plot_factor_exposure(result, output_path=output_path)
    assert returned_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 1000


def test_plot_creates_parent_directories(tmp_path):
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    nested_path = tmp_path / "nested" / "dir" / "chart.png"
    plot_factor_exposure(result, output_path=nested_path)
    assert nested_path.exists()


def test_plot_rejects_unknown_metric(tmp_path):
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    with pytest.raises(ValueError, match="metric"):
        plot_factor_exposure(result, output_path=tmp_path / "chart.png", metric="bogus")


def test_plot_pct_metric_also_saves(tmp_path):
    result = compute_portfolio_factor_exposure(_portfolio(), _curve(), _pca_result())
    output_path = tmp_path / "pct_chart.png"
    plot_factor_exposure(result, output_path=output_path, metric="pct")
    assert output_path.exists()
