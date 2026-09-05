"""
Tests for models/pca.py.

Covers: the output contract (variance ratios sum correctly, loadings are
unit-norm, sign convention is deterministic); an independent numpy-based
reference PCA (not merely "it ran"); the Litterman-Scheinkman sanity check
(level / slope / curvature) against the real committed snapshot at the
project's chosen 2-year lookback window; and input validation.

All curve loading uses prefer_live=False for determinism. The exact
loadings/variance-ratio figures below are pinned to
data/jgb_curve_history_snapshot.csv's fixed vintage (fetched 2026-09-04);
see docs/phase_4b_documentation.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_history_loader import load_jgb_curve_history
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca


def _history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS) -> pd.DataFrame:
    return load_jgb_curve_history(lookback_years=lookback_years, prefer_live=False, verbose=False)


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------


def test_returns_curve_pca_result():
    result = compute_curve_pca(_history())
    assert isinstance(result, CurvePCAResult)


def test_n_observations_is_one_less_than_history_rows():
    history = _history()
    result = compute_curve_pca(history)
    assert result.n_observations == len(history) - 1


def test_default_keeps_top_3_components():
    result = compute_curve_pca(_history())
    assert result.n_components == 3
    assert len(result.loadings) == 3
    assert len(result.explained_variance) == 3
    assert len(result.explained_variance_ratio) == 3


def test_loadings_are_unit_norm():
    result = compute_curve_pca(_history())
    norms = np.linalg.norm(result.loadings.to_numpy(), axis=1)
    np.testing.assert_allclose(norms, 1.0)


def test_explained_variance_ratio_all_sums_to_one():
    result = compute_curve_pca(_history())
    assert result.explained_variance_ratio_all.sum() == pytest.approx(1.0)


def test_explained_variance_ratio_is_a_slice_of_the_full_spectrum():
    result = compute_curve_pca(_history())
    np.testing.assert_allclose(
        result.explained_variance_ratio, result.explained_variance_ratio_all[:3]
    )


def test_cumulative_explained_variance_ratio_is_monotonic_and_matches_cumsum():
    result = compute_curve_pca(_history())
    cum = result.cumulative_explained_variance_ratio
    assert np.all(np.diff(cum) >= 0)
    np.testing.assert_allclose(cum, np.cumsum(result.explained_variance_ratio))


def test_sign_convention_largest_magnitude_loading_is_positive():
    result = compute_curve_pca(_history())
    for component in result.loadings.index:
        row = result.loadings.loc[component]
        assert row.iloc[np.argmax(np.abs(row.to_numpy()))] > 0


def test_component_std_is_sqrt_of_explained_variance():
    result = compute_curve_pca(_history())
    np.testing.assert_allclose(result.component_std, np.sqrt(result.explained_variance))


def test_tenors_match_the_history_grid():
    history = _history()
    result = compute_curve_pca(history)
    np.testing.assert_array_equal(result.tenors, history.columns.to_numpy(dtype=float))


# --------------------------------------------------------------------------
# Independent reference check
# --------------------------------------------------------------------------


def test_matches_an_independent_numpy_covariance_eigendecomposition():
    # Recomputed via np.cov + np.linalg.eigh (a different numpy code path
    # from compute_curve_pca's SVD) rather than calling compute_curve_pca's
    # own internals a second time -- a genuine independent check.
    history = _history()
    changes = history.diff().dropna(how="any").to_numpy()

    cov = np.cov(changes, rowvar=False, ddof=1)
    eigvals, eigvecs = np.linalg.eigh(cov)  # ascending order
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    for i in range(eigvecs.shape[1]):
        flip_idx = np.argmax(np.abs(eigvecs[:, i]))
        if eigvecs[flip_idx, i] < 0:
            eigvecs[:, i] *= -1

    result = compute_curve_pca(history, n_components=3)
    ratio_ref = eigvals / eigvals.sum()

    np.testing.assert_allclose(result.explained_variance, eigvals[:3], rtol=1e-6)
    np.testing.assert_allclose(result.explained_variance_ratio, ratio_ref[:3], rtol=1e-6)
    np.testing.assert_allclose(
        result.loadings.to_numpy(), eigvecs[:, :3].T, rtol=1e-6, atol=1e-8
    )


def test_implied_yield_shock_equals_std_times_loading():
    result = compute_curve_pca(_history())
    for component in (1, 2, 3):
        shock = result.implied_yield_shock(component)
        expected = result.loadings.loc[component] * result.component_std[component - 1]
        np.testing.assert_allclose(shock.to_numpy(), expected.to_numpy())


def test_implied_yield_shock_rejects_out_of_range_component():
    result = compute_curve_pca(_history())
    with pytest.raises(ValueError, match="component"):
        result.implied_yield_shock(0)
    with pytest.raises(ValueError, match="component"):
        result.implied_yield_shock(result.n_components + 1)


# --------------------------------------------------------------------------
# Litterman-Scheinkman sanity check, at the project's chosen default window
# --------------------------------------------------------------------------


def test_pc1_has_consistent_sign_across_all_tenors_level():
    result = compute_curve_pca(_history())
    pc1 = result.loadings.loc[1]
    assert (pc1 > 0).all() or (pc1 < 0).all()


def test_pc2_has_opposite_sign_short_vs_long_end_slope():
    result = compute_curve_pca(_history())
    pc2 = result.loadings.loc[2]
    assert np.sign(pc2.iloc[0]) != np.sign(pc2.iloc[-1])


def test_pc3_shows_a_belly_vs_wings_pattern_curvature():
    result = compute_curve_pca(_history())
    pc3 = result.loadings.loc[3]
    mid_idx = len(pc3) // 2
    assert np.sign(pc3.iloc[0]) == np.sign(pc3.iloc[-1])  # wings agree
    assert np.sign(pc3.iloc[mid_idx]) != np.sign(pc3.iloc[0])  # belly opposes them


def test_top_3_components_capture_the_large_majority_of_variance():
    # Conventional expectation for a sovereign curve (Litterman-Scheinkman);
    # a loose bound, not pinned to the exact ~97.5% observed on this
    # snapshot, so a re-anchored snapshot doesn't break this test.
    result = compute_curve_pca(_history())
    assert result.cumulative_explained_variance_ratio[-1] > 0.90


def test_pc1_explains_the_most_variance_of_the_three():
    result = compute_curve_pca(_history())
    assert result.explained_variance_ratio[0] > result.explained_variance_ratio[1]
    assert result.explained_variance_ratio[1] > result.explained_variance_ratio[2]


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_rejects_history_with_missing_values():
    history = _history().copy()
    history.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="missing values"):
        compute_curve_pca(history)


def test_rejects_fewer_than_two_dates():
    history = _history().iloc[:1]
    with pytest.raises(ValueError, match="at least 2 dates"):
        compute_curve_pca(history)


def test_rejects_n_components_out_of_range():
    history = _history()
    n_tenors = history.shape[1]
    with pytest.raises(ValueError, match="n_components"):
        compute_curve_pca(history, n_components=0)
    with pytest.raises(ValueError, match="n_components"):
        compute_curve_pca(history, n_components=n_tenors + 1)


def test_rejects_too_few_observations_relative_to_tenors():
    history = _history()
    n_tenors = history.shape[1]
    tiny_history = history.iloc[: n_tenors // 2]
    with pytest.raises(ValueError, match="observation"):
        compute_curve_pca(tiny_history)
