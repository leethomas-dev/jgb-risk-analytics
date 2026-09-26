"""
Tests for models/pc_scores.py.

Covers: units (raw scores in decimal, standardized scores dimensionless
and separate), the completeness/reconstruction property (summing ALL
components' scores against the full eigenbasis reproduces the original
change vector exactly), the sqrt(eigenvalue) verification the phase brief
calls for explicitly, the sign convention (inherited from Phase 4B, not
reinvented), the percentile helper, tenor-grid-mismatch validation, and
genericity across ragged (different-sized) tenor sets.

All curve/history loading uses prefer_live=False for determinism. Exact
figures below are pinned to data/jgb_curve_history_snapshot.csv's fixed
vintage (fetched 2026-09-04) -- see docs/phase_4d_documentation.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_history_loader import load_jgb_curve_history
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca
from models.pc_scores import PCScoreResult, compute_pc_scores


def _history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS) -> pd.DataFrame:
    return load_jgb_curve_history(lookback_years=lookback_years, prefer_live=False, verbose=False)


def _pca_and_scores(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS):
    history = _history(lookback_years)
    pca_result = compute_curve_pca(history)
    return history, pca_result, compute_pc_scores(history, pca_result)


# --------------------------------------------------------------------------
# Output contract / units
# --------------------------------------------------------------------------


def test_returns_pc_score_result():
    _, _, result = _pca_and_scores()
    assert isinstance(result, PCScoreResult)


def test_scores_indexed_one_date_per_daily_change():
    history, _, result = _pca_and_scores()
    assert len(result.scores) == len(history) - 1


def test_scores_have_one_column_per_kept_component():
    _, pca_result, result = _pca_and_scores()
    assert list(result.scores.columns) == [f"PC{i}" for i in range(1, pca_result.n_components + 1)]


def test_standardized_scores_have_distinctly_named_columns():
    _, pca_result, result = _pca_and_scores()
    assert list(result.standardized_scores.columns) == [
        f"PC{i}_std" for i in range(1, pca_result.n_components + 1)
    ]
    # Never overlapping with the raw scores' own column names.
    assert set(result.scores.columns).isdisjoint(result.standardized_scores.columns)


def test_standardized_scores_equal_raw_score_over_component_std():
    _, pca_result, result = _pca_and_scores()
    for i in range(1, pca_result.n_components + 1):
        expected = result.scores[f"PC{i}"] / pca_result.component_std[i - 1]
        np.testing.assert_allclose(result.standardized_scores[f"PC{i}_std"].to_numpy(), expected.to_numpy())


def test_full_scores_has_one_column_per_tenor():
    history, _, result = _pca_and_scores()
    assert result.full_scores.shape[1] == history.shape[1]


# --------------------------------------------------------------------------
# The sqrt(eigenvalue) verification -- the phase brief's own correctness check
# --------------------------------------------------------------------------


def test_score_std_equals_sqrt_eigenvalue():
    _, pca_result, result = _pca_and_scores()
    for i in range(1, pca_result.n_components + 1):
        observed_std = result.scores[f"PC{i}"].std(ddof=1)
        assert observed_std == pytest.approx(pca_result.component_std[i - 1], rel=1e-9)


def test_standardized_score_std_is_one():
    # Direct consequence of the check above -- restated in the units a
    # standardized score is actually meant to be read in.
    _, pca_result, result = _pca_and_scores()
    for i in range(1, pca_result.n_components + 1):
        assert result.standardized_scores[f"PC{i}_std"].std(ddof=1) == pytest.approx(1.0, rel=1e-9)


# --------------------------------------------------------------------------
# Completeness / reconstruction -- requires the FULL eigenbasis, not just
# the top few kept components
# --------------------------------------------------------------------------


def test_full_scores_reconstruct_the_original_change_vector():
    history, pca_result, result = _pca_and_scores()
    reconstructed = result.full_scores.to_numpy() @ pca_result.full_loadings.to_numpy()
    original = history.sort_index().diff().dropna(how="any")[pca_result.full_loadings.columns].to_numpy()
    np.testing.assert_allclose(reconstructed, original, atol=1e-10)


def test_full_loadings_is_a_complete_orthonormal_basis():
    _, pca_result, _ = _pca_and_scores()
    basis = pca_result.full_loadings.to_numpy()
    n_tenors = basis.shape[0]
    np.testing.assert_allclose(basis @ basis.T, np.eye(n_tenors), atol=1e-10)


# --------------------------------------------------------------------------
# Sign convention -- inherited from Phase 4B's full eigenbasis, not
# reinvented here
# --------------------------------------------------------------------------


def test_full_loadings_sign_convention_holds_for_every_component():
    # test_pca.py already checks this for the top 3 kept components;
    # this extends the same check to EVERY component in the full basis,
    # since compute_pc_scores' completeness property depends on all of
    # them, not just the top few.
    _, pca_result, _ = _pca_and_scores()
    for component in pca_result.full_loadings.index:
        row = pca_result.full_loadings.loc[component]
        assert row.iloc[np.argmax(np.abs(row.to_numpy()))] > 0


def test_a_positive_pc1_day_means_the_same_direction_every_time():
    # PC1's dominant loadings are all positive-sign (Phase 4B's own
    # Litterman-Scheinkman check) -- so a positive PC1 score should
    # correspond to a day where most tenors moved up, not down, checked
    # directly rather than assumed from the sign convention alone.
    history, pca_result, result = _pca_and_scores()
    changes = history.sort_index().diff().dropna(how="any")
    up_days = result.scores["PC1"] > result.scores["PC1"].quantile(0.9)
    down_days = result.scores["PC1"] < result.scores["PC1"].quantile(0.1)
    assert changes.loc[up_days].mean().mean() > changes.loc[down_days].mean().mean()


# --------------------------------------------------------------------------
# The percentile helper
# --------------------------------------------------------------------------


def test_percentile_of_the_maximum_score_is_100():
    _, _, result = _pca_and_scores()
    max_date = result.scores["PC1"].idxmax()
    assert result.percentile(max_date, 1) == pytest.approx(100.0)


def test_percentile_of_the_minimum_score_is_near_zero():
    _, _, result = _pca_and_scores()
    min_date = result.scores["PC1"].idxmin()
    assert result.percentile(min_date, 1) == pytest.approx(100.0 / len(result.scores), abs=0.5)


def test_percentile_rejects_out_of_range_component():
    _, pca_result, result = _pca_and_scores()
    with pytest.raises(ValueError, match="component"):
        result.percentile(result.scores.index[0], 0)
    with pytest.raises(ValueError, match="component"):
        result.percentile(result.scores.index[0], pca_result.n_components + 1)


def test_percentile_rejects_a_date_not_in_the_series():
    _, _, result = _pca_and_scores()
    with pytest.raises(ValueError, match="date"):
        result.percentile("1900-01-01", 1)


# --------------------------------------------------------------------------
# Tenor alignment -- explicit validation, not silent misalignment
# --------------------------------------------------------------------------


def test_rejects_a_history_whose_tenor_grid_does_not_match_the_pca_fit():
    history_2y = _history(2.0)
    pca_result_2y = compute_curve_pca(history_2y)
    history_25y = _history(25.0)  # a genuinely different retained tenor set
    assert set(history_25y.columns) != set(history_2y.columns)
    with pytest.raises(ValueError, match="tenor grid"):
        compute_pc_scores(history_25y, pca_result_2y)


# --------------------------------------------------------------------------
# Genericity across ragged (different-sized) historical tenor sets
# --------------------------------------------------------------------------


def test_works_on_a_window_with_a_different_retained_tenor_count():
    # 21 years crosses 40Y's 2007-11-06 introduction date, so this window
    # retains 14 tenors, not the default window's 15 -- confirms nothing
    # here hardcodes a specific tenor count.
    history_21y = _history(21.0)
    assert history_21y.shape[1] != _history(2.0).shape[1]
    pca_result_21y = compute_curve_pca(history_21y)
    result = compute_pc_scores(history_21y, pca_result_21y)
    assert result.full_scores.shape[1] == history_21y.shape[1]
    for i in range(1, result.n_components + 1):
        observed_std = result.scores[f"PC{i}"].std(ddof=1)
        assert observed_std == pytest.approx(pca_result_21y.component_std[i - 1], rel=1e-9)
