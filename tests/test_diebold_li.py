"""
Tests for models/diebold_li.py.

Covers: fixed-tau OLS betas matching an independent closed-form normal-
equations solution; exact parameter recovery from a noiseless curve
generated FROM known Nelson-Siegel parameters; the historical fit's
output contract and per-date RMSE; the poor-fit-date flag actually
picking out a real, non-trivial minority of dates on real data (not the
whole sample, not none of it); working against a curve history with a
tenor set that isn't the project's usual 15-tenor default (no hardcoded
tenor list); the tau sensitivity sweep showing real, asymmetric
sensitivity; and the PCA comparison, including sign-alignment handling.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_history_loader import load_jgb_curve_history
from models.curve_fitting import hump_factors
from models.diebold_li import (
    DEFAULT_TAU,
    DieboldLiHistoryResult,
    DieboldLiPCAComparison,
    compare_to_pca,
    fit_diebold_li_cross_section,
    fit_diebold_li_history,
    plot_beta_time_series,
    plot_pca_comparison,
    tau_sensitivity,
)
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca


def _default_history() -> pd.DataFrame:
    return load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)


def _synthetic_ragged_history() -> pd.DataFrame:
    """A small, fast, deliberately NON-15-tenor history (5 dates, 6
    tenors, none of which match this project's usual default grid) --
    proves the fitting code makes no assumption about tenor count or
    values, without needing a real multi-thousand-row historical window
    just to get a different tenor set (the real loader only offers that
    via a much longer, much slower lookback -- docs/phase_4a_documentation.md
    §1.3's table; a synthetic fixture gets the same coverage in
    milliseconds)."""
    tenors = [0.5, 2.5, 6.0, 11.0, 22.0, 35.0]
    dates = pd.date_range("2020-01-01", periods=5, freq="7D")
    rng = np.random.default_rng(0)
    base = np.array([0.008, 0.012, 0.017, 0.021, 0.028, 0.031])
    data = base + rng.normal(scale=0.0005, size=(5, 6))
    return pd.DataFrame(data, index=dates, columns=tenors)


# --------------------------------------------------------------------------
# fixed-tau OLS: matches an independent closed-form solution
# --------------------------------------------------------------------------


def test_ols_betas_match_independent_normal_equations():
    tenors = np.array([1.0, 2.0, 5.0, 10.0, 20.0, 30.0])
    rates = np.array([0.011, 0.014, 0.019, 0.024, 0.032, 0.037])
    tau = 5.0

    betas, fitted, rmse_bp = fit_diebold_li_cross_section(tenors, rates, tau=tau)

    f1, f2 = hump_factors(tau, tenors)
    X = np.column_stack([np.ones_like(tenors), f1, f2])
    expected_betas = np.linalg.inv(X.T @ X) @ X.T @ rates  # independent closed-form OLS

    np.testing.assert_allclose(betas, expected_betas, atol=1e-10)
    np.testing.assert_allclose(fitted, X @ expected_betas, atol=1e-10)


def test_ols_fit_is_the_least_squares_minimizer_not_just_a_solution():
    # A genuine least-squares minimizer must beat any nearby perturbation
    # of the betas on the same design matrix -- checked directly rather
    # than only trusting the normal-equations algebra above.
    tenors = np.array([1.0, 3.0, 7.0, 15.0, 25.0, 40.0])
    rates = np.array([0.010, 0.016, 0.021, 0.030, 0.036, 0.038])
    tau = 8.0
    betas, fitted, _ = fit_diebold_li_cross_section(tenors, rates, tau=tau)
    sse = np.sum((rates - fitted) ** 2)

    f1, f2 = hump_factors(tau, tenors)
    X = np.column_stack([np.ones_like(tenors), f1, f2])
    rng = np.random.default_rng(1)
    for _ in range(20):
        perturbed = betas + rng.normal(scale=0.01, size=3)
        perturbed_sse = np.sum((rates - X @ perturbed) ** 2)
        assert perturbed_sse >= sse - 1e-12


# --------------------------------------------------------------------------
# Recovers known Nelson-Siegel parameters from a noiseless synthetic curve
# --------------------------------------------------------------------------


def test_recovers_known_parameters_from_a_noiseless_curve():
    tenors = np.array([0.5, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40], dtype=float)
    true_beta0, true_beta1, true_beta2, true_tau = 0.03, -0.012, 0.018, 6.0
    f1, f2 = hump_factors(true_tau, tenors)
    rates = true_beta0 + true_beta1 * f1 + true_beta2 * f2

    betas, fitted, rmse_bp = fit_diebold_li_cross_section(tenors, rates, tau=true_tau)

    assert betas[0] == pytest.approx(true_beta0, abs=1e-10)
    assert betas[1] == pytest.approx(true_beta1, abs=1e-10)
    assert betas[2] == pytest.approx(true_beta2, abs=1e-10)
    assert rmse_bp == pytest.approx(0.0, abs=1e-6)


def test_recovery_degrades_gracefully_with_the_wrong_fixed_tau():
    # Fitting with a tau that DOESN'T match the curve's true generating
    # tau should NOT recover the true betas exactly -- confirms the exact
    # recovery above isn't a trivial always-true identity.
    tenors = np.array([0.5, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40], dtype=float)
    true_beta0, true_beta1, true_beta2, true_tau = 0.03, -0.012, 0.018, 6.0
    f1, f2 = hump_factors(true_tau, tenors)
    rates = true_beta0 + true_beta1 * f1 + true_beta2 * f2

    betas, _, rmse_bp = fit_diebold_li_cross_section(tenors, rates, tau=25.0)
    assert rmse_bp > 0.5  # a real, non-trivial misfit
    assert not np.allclose(betas, [true_beta0, true_beta1, true_beta2], atol=1e-4)


# --------------------------------------------------------------------------
# fit_diebold_li_history: output contract, on real data
# --------------------------------------------------------------------------


def test_returns_the_result_type():
    result = fit_diebold_li_history(_default_history())
    assert isinstance(result, DieboldLiHistoryResult)


def test_betas_are_date_indexed_matching_the_input_history():
    history = _default_history()
    result = fit_diebold_li_history(history)
    assert list(result.betas.columns) == ["beta0", "beta1", "beta2"]
    assert list(result.betas.index) == list(history.index)
    assert result.tau == DEFAULT_TAU


def test_tenors_are_read_from_the_history_not_hardcoded():
    history = _default_history()
    result = fit_diebold_li_history(history)
    assert set(result.tenors.tolist()) == set(history.columns.tolist())


def test_median_rmse_is_within_a_stated_tolerance_on_the_default_window():
    # Real measured value (docs/phase_4_5b_dl_documentation.md) is ~5.47bp.
    result = fit_diebold_li_history(_default_history())
    assert result.rmse_bp.median() < 8.0


# --------------------------------------------------------------------------
# poor_fit_dates: flags a real, non-trivial minority -- not everything,
# not nothing
# --------------------------------------------------------------------------


def test_poor_fit_dates_is_a_genuine_minority_of_the_sample():
    result = fit_diebold_li_history(_default_history())
    poor = result.poor_fit_dates()
    assert 0 < len(poor) < len(result.betas) // 2


def test_poor_fit_dates_all_exceed_the_stated_threshold():
    result = fit_diebold_li_history(_default_history())
    rmse = result.rmse_bp
    threshold = rmse.median() * 2.0
    poor = result.poor_fit_dates(multiple=2.0)
    assert (rmse.loc[poor] > threshold).all()
    assert (rmse.drop(poor) <= threshold).all()


def test_a_stricter_multiple_flags_fewer_or_equal_dates():
    result = fit_diebold_li_history(_default_history())
    lenient = result.poor_fit_dates(multiple=1.5)
    strict = result.poor_fit_dates(multiple=3.0)
    assert len(strict) <= len(lenient)


# --------------------------------------------------------------------------
# Works across a curve history with a different tenor set (ragged-tenor
# awareness -- no hardcoded list)
# --------------------------------------------------------------------------


def test_fits_cleanly_on_a_non_default_tenor_set():
    history = _synthetic_ragged_history()
    result = fit_diebold_li_history(history, tau=5.0)
    assert set(result.tenors.tolist()) == set(history.columns.tolist())
    assert result.betas.shape == (5, 3)
    assert np.isfinite(result.betas.to_numpy()).all()
    assert np.isfinite(result.rmse_bp.to_numpy()).all()


# --------------------------------------------------------------------------
# tau_sensitivity: real, and asymmetric, sensitivity
# --------------------------------------------------------------------------


def test_tau_sensitivity_returns_one_row_per_candidate():
    grid = np.array([1.0, 5.0, 10.0, 30.0])
    sweep = tau_sensitivity(_default_history(), tau_grid=grid)
    assert len(sweep) == len(grid)
    np.testing.assert_allclose(sweep["tau"].to_numpy(), grid)


def test_a_too_small_tau_fits_much_worse_than_a_near_optimal_one():
    # Real finding (docs/phase_4_5b_dl_documentation.md §2): fit quality is
    # highly sensitive to an overly small tau, much less sensitive to an
    # overly large one -- checked directly on real data.
    sweep = tau_sensitivity(_default_history(), tau_grid=np.array([0.5, DEFAULT_TAU]))
    too_small = sweep.loc[sweep["tau"] == 0.5, "median_rmse_bp"].iloc[0]
    near_optimal = sweep.loc[sweep["tau"] == DEFAULT_TAU, "median_rmse_bp"].iloc[0]
    assert too_small > near_optimal * 3


def test_default_tau_is_close_to_the_swept_empirical_optimum():
    grid = np.geomspace(1.0, 30.0, 30)
    sweep = tau_sensitivity(_default_history(), tau_grid=grid)
    best_rmse = sweep["median_rmse_bp"].min()
    default_rmse = tau_sensitivity(_default_history(), tau_grid=np.array([DEFAULT_TAU]))["median_rmse_bp"].iloc[0]
    assert default_rmse < best_rmse * 1.1  # within 10% of the swept optimum


# --------------------------------------------------------------------------
# compare_to_pca: sign alignment and the real correlation pattern
# --------------------------------------------------------------------------


def test_compare_to_pca_returns_one_row_per_available_component():
    history = _default_history()
    dl_result = fit_diebold_li_history(history)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, dl_result, pca_result)
    assert isinstance(comparison, DieboldLiPCAComparison)
    assert len(comparison.pairs) == 3
    assert list(comparison.pairs["beta"]) == ["beta0", "beta1", "beta2"]
    assert list(comparison.pairs["pca_component"]) == [1, 2, 3]


def test_sign_aligned_correlation_is_the_absolute_raw_correlation():
    history = _default_history()
    dl_result = fit_diebold_li_history(history)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, dl_result, pca_result)
    np.testing.assert_allclose(
        comparison.pairs["sign_aligned_correlation"].to_numpy(),
        comparison.pairs["raw_correlation"].abs().to_numpy(),
    )


def test_sign_aligned_correlations_are_valid_correlation_magnitudes():
    history = _default_history()
    dl_result = fit_diebold_li_history(history)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, dl_result, pca_result)
    aligned = comparison.pairs["sign_aligned_correlation"]
    assert (aligned >= 0).all()
    assert (aligned <= 1.0 + 1e-9).all()


def test_slope_pair_shows_the_strongest_correspondence():
    # Real finding (docs/phase_4_5b_dl_documentation.md §3): beta1 (slope)
    # vs PC2 correlates far more strongly than beta0 vs PC1 or beta2 vs
    # PC3 on this project's default window -- checked directly, not
    # assumed uniform.
    history = _default_history()
    dl_result = fit_diebold_li_history(history)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, dl_result, pca_result)
    by_beta = comparison.pairs.set_index("beta")["sign_aligned_correlation"]
    assert by_beta["beta1"] > by_beta["beta0"]
    assert by_beta["beta1"] > by_beta["beta2"]
    assert by_beta["beta1"] > 0.8  # a real, strong correspondence


def test_beta_changes_and_pca_scores_share_the_same_dates():
    history = _default_history()
    dl_result = fit_diebold_li_history(history)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, dl_result, pca_result)
    assert list(comparison.beta_changes.index) == list(comparison.pca_scores.index)


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------


def test_plot_beta_time_series_saves_a_real_png(tmp_path):
    result = fit_diebold_li_history(_default_history())
    output_path = tmp_path / "betas.png"
    returned = plot_beta_time_series(result, output_path=output_path)
    assert returned == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_plot_pca_comparison_saves_a_real_png(tmp_path):
    history = _default_history()
    dl_result = fit_diebold_li_history(history)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, dl_result, pca_result)
    output_path = tmp_path / "comparison.png"
    returned = plot_pca_comparison(comparison, output_path=output_path)
    assert returned == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_plot_creates_parent_directories(tmp_path):
    result = fit_diebold_li_history(_default_history())
    nested_path = tmp_path / "nested" / "dir" / "betas.png"
    plot_beta_time_series(result, output_path=nested_path)
    assert nested_path.exists()
