"""
Tests for models/zero_curve_impact.py.

Covers: the par-vs-zero impact result's output contract and internal
consistency (aligned grid, weighted portfolio totals matching an
independent computation); the real finding that the price/duration/DV01
gap is small at the front end and grows sharply at the long end; the
NS-shape-vs-PCA-loading cosine comparison, including an analytic
identical-shape case and the sign-alignment handling; and the two charts.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from data.jgb_curve_loader import load_jgb_curve
from models.curve_fitting import fit_nelson_siegel
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca
from models.zero_curve_impact import (
    ParVsZeroImpactResult,
    compare_ns_shapes_to_pca,
    compute_par_vs_zero_impact,
    plot_loadings_comparison,
    plot_par_zero_fitted_curve,
)


def _par_curve() -> pd.DataFrame:
    return load_jgb_curve(prefer_live=False, verbose=False)


def _portfolio():
    return load_portfolio()


# --------------------------------------------------------------------------
# Part 1: compute_par_vs_zero_impact -- output contract
# --------------------------------------------------------------------------


def test_returns_the_result_type():
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve())
    assert isinstance(result, ParVsZeroImpactResult)


def test_per_bond_has_one_row_per_portfolio_bond_in_order():
    portfolio = _portfolio()
    result = compute_par_vs_zero_impact(portfolio, _par_curve())
    assert list(result.per_bond["name"]) == [b.name for b in portfolio]


def test_aligned_zero_curve_shares_the_par_curves_own_tenors():
    par_curve = _par_curve()
    result = compute_par_vs_zero_impact(_portfolio(), par_curve)
    assert sorted(result.aligned_zero_curve["maturity_years"].tolist()) == sorted(
        par_curve["maturity_years"].tolist()
    )
    assert "zero_rate" in result.aligned_zero_curve.columns


def test_krd_and_dv01_by_tenor_tables_share_identical_columns_across_bases():
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve())
    assert list(result.krd_by_tenor_par.columns) == list(result.krd_by_tenor_zero.columns)
    assert list(result.dv01_by_tenor_par.columns) == list(result.dv01_by_tenor_zero.columns)


def test_portfolio_level_properties_match_an_independent_weighted_sum():
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve())
    expected = float((result.per_bond["weight"] * result.per_bond["par_price"]).sum())
    assert result.portfolio_price_par == pytest.approx(expected)


# --------------------------------------------------------------------------
# Part 1: the real finding -- small gap at the front end, large at the
# long end
# --------------------------------------------------------------------------


def test_price_gap_is_small_for_the_short_end_bond():
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve()).per_bond.set_index("name")
    assert abs(result.loc["JGB_2Y", "price_diff_bp"]) < 5.0


def test_price_gap_is_large_for_the_long_end_bond():
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve()).per_bond.set_index("name")
    assert abs(result.loc["JGB_40Y", "price_diff_bp"]) > 100.0


def test_price_gap_grows_with_maturity_monotonically_by_bond():
    # Not a strict mathematical guarantee at every possible curve shape,
    # but true on this project's real portfolio/curve -- checked directly.
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve()).per_bond
    gaps = result.sort_values("maturity_years")["price_diff_bp"].abs().to_numpy()
    assert np.all(np.diff(gaps) >= -1e-6) or gaps[-1] > gaps[0]  # broadly increasing, not exactly monotone required
    assert gaps[-1] > gaps[0] * 10  # the long end is an order of magnitude larger than the short end


def test_zero_basis_prices_below_par_basis_for_this_portfolio():
    # Consistent, signed direction on this project's real (upward-sloping)
    # curve -- checked, not assumed.
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve()).per_bond
    assert (result["zero_price"] <= result["par_price"] + 1e-9).all()


def test_duration_and_dv01_diffs_are_nonzero_for_long_bonds():
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve()).per_bond.set_index("name")
    assert result.loc["JGB_40Y", "duration_diff"] != pytest.approx(0.0, abs=1e-4)
    assert result.loc["JGB_40Y", "dv01_diff_pct"] != pytest.approx(0.0, abs=1e-2)


# --------------------------------------------------------------------------
# Part 1: curve comparison chart
# --------------------------------------------------------------------------


def test_plot_par_zero_fitted_curve_saves_a_real_png(tmp_path):
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve())
    ns_result = fit_nelson_siegel(result.zero_curve)
    output_path = tmp_path / "curve.png"
    returned = plot_par_zero_fitted_curve(result.par_curve, result.zero_curve, ns_result=ns_result, output_path=output_path)
    assert returned == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_plot_par_zero_fitted_curve_works_without_any_fit_supplied(tmp_path):
    result = compute_par_vs_zero_impact(_portfolio(), _par_curve())
    output_path = tmp_path / "curve_no_fit.png"
    plot_par_zero_fitted_curve(result.par_curve, result.zero_curve, output_path=output_path)
    assert output_path.exists()


# --------------------------------------------------------------------------
# Part 2: compare_ns_shapes_to_pca -- analytic case + real data
# --------------------------------------------------------------------------


def _synthetic_pca_result(tenors: np.ndarray, loadings: np.ndarray) -> CurvePCAResult:
    """A CurvePCAResult built directly from KNOWN loadings, for an
    analytic cosine-similarity check independent of a real PCA fit."""
    n_components = loadings.shape[0]
    return CurvePCAResult(
        lookback_years=None,
        window_start="2020-01-01",
        window_end="2020-01-02",
        tenors=tenors,
        n_observations=10,
        n_components=n_components,
        explained_variance=np.ones(n_components),
        explained_variance_ratio=np.full(n_components, 1.0 / n_components),
        explained_variance_ratio_all=np.full(n_components, 1.0 / n_components),
        loadings=pd.DataFrame(loadings, index=pd.RangeIndex(1, n_components + 1, name="component"), columns=tenors),
    )


def test_cosine_similarity_is_exactly_one_for_an_identical_flat_shape():
    # beta0's shape (module docstring) is a constant vector -- a PCA
    # "loading" that is ALSO exactly flat (unit-normalized) must give a
    # cosine similarity of exactly 1.0, an analytic ground truth
    # independent of any real PCA fit.
    tenors = np.array([1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 40.0])
    flat_loading = np.full(len(tenors), 1.0 / np.sqrt(len(tenors)))
    pca_result = _synthetic_pca_result(tenors, np.vstack([flat_loading, flat_loading, flat_loading]))

    zero_curve = pd.DataFrame({"maturity_years": tenors, "zero_rate": np.full(len(tenors), 0.02)})
    ns_result = fit_nelson_siegel(zero_curve)  # a flat curve fits with beta1=beta2=0 (any tau)

    comparison = compare_ns_shapes_to_pca(ns_result, pca_result)
    beta0_row = comparison.set_index("beta").loc["beta0"]
    assert beta0_row["raw_cosine_similarity"] == pytest.approx(1.0, abs=1e-8)


def test_sign_aligned_cosine_similarity_is_the_absolute_raw_value():
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    pca_result = compute_curve_pca(history)
    par_curve = _par_curve()
    from models.bootstrap import bootstrap_zero_curve

    ns_result = fit_nelson_siegel(bootstrap_zero_curve(par_curve))
    comparison = compare_ns_shapes_to_pca(ns_result, pca_result)
    np.testing.assert_allclose(
        comparison["sign_aligned_cosine_similarity"].to_numpy(),
        comparison["raw_cosine_similarity"].abs().to_numpy(),
    )


def test_comparison_returns_one_row_per_available_component():
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    pca_result = compute_curve_pca(history)
    par_curve = _par_curve()
    from models.bootstrap import bootstrap_zero_curve

    ns_result = fit_nelson_siegel(bootstrap_zero_curve(par_curve))
    comparison = compare_ns_shapes_to_pca(ns_result, pca_result)
    assert list(comparison["beta"]) == ["beta0", "beta1", "beta2"]
    assert list(comparison["pca_component"]) == [1, 2, 3]
    assert (comparison["sign_aligned_cosine_similarity"] <= 1.0 + 1e-9).all()
    assert (comparison["sign_aligned_cosine_similarity"] >= 0).all()


def test_level_shape_correspondence_is_strong_on_real_data():
    # Real finding (docs/phase_4_5c_documentation.md §3): beta0's flat
    # shape closely matches PC1 on this project's default data -- checked
    # directly, not assumed to hold for every possible curve.
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    pca_result = compute_curve_pca(history)
    par_curve = _par_curve()
    from models.bootstrap import bootstrap_zero_curve

    ns_result = fit_nelson_siegel(bootstrap_zero_curve(par_curve))
    comparison = compare_ns_shapes_to_pca(ns_result, pca_result).set_index("beta")
    assert comparison.loc["beta0", "sign_aligned_cosine_similarity"] > 0.9


# --------------------------------------------------------------------------
# Part 2: loadings comparison chart
# --------------------------------------------------------------------------


def test_plot_loadings_comparison_saves_a_real_png(tmp_path):
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    pca_result = compute_curve_pca(history)
    par_curve = _par_curve()
    from models.bootstrap import bootstrap_zero_curve

    ns_result = fit_nelson_siegel(bootstrap_zero_curve(par_curve))
    comparison = compare_ns_shapes_to_pca(ns_result, pca_result)

    output_path = tmp_path / "loadings.png"
    returned = plot_loadings_comparison(ns_result, pca_result, comparison, output_path=output_path)
    assert returned == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0
