"""
Tests for models/factor_pnl_attribution.py.

Covers: the additivity invariant (attributed + residual == actual repriced
P&L, exactly, by construction), a zero-change day producing zero
attribution and zero residual, portfolio weights changing the attribution,
the as-of/previous date bookkeeping, and date validation.

Real-data tests use prefer_live=False for determinism, pinned to
data/jgb_curve_history_snapshot.csv's fixed vintage (fetched 2026-09-04)
-- see docs/phase_4d_documentation.md. The zero-change-day and
weight-sensitivity tests use small synthetic histories/portfolios instead,
so they don't depend on a real day in the snapshot happening to have an
exactly-zero move.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from models.bond_pricing import price_portfolio
from models.pc_scores import compute_pc_scores
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca
from models.factor_pnl_attribution import FactorPnLAttribution, compute_factor_pnl_attribution


def _real_setup():
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    pca_result = compute_curve_pca(history)
    score_result = compute_pc_scores(history, pca_result)
    portfolio = load_portfolio()
    return portfolio, history, pca_result, score_result


def _synthetic_history(rows: dict, tenors: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    df = pd.DataFrame(list(rows.values()), index=dates, columns=tenors)
    df.columns.name = "maturity_years"
    df.index.name = "date"
    return df


def _synthetic_bond(name: str, maturity_years: float, weight: float = 1.0) -> Bond:
    return Bond(name=name, maturity_years=maturity_years, coupon_rate=0.02, face_value=100.0, weight=weight)


# --------------------------------------------------------------------------
# Real-data checks
# --------------------------------------------------------------------------


def test_returns_factor_pnl_attribution():
    portfolio, history, pca_result, score_result = _real_setup()
    result = compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, score_result.scores.index[-1])
    assert isinstance(result, FactorPnLAttribution)


def test_attributed_plus_residual_equals_actual_repriced_pnl():
    portfolio, history, pca_result, score_result = _real_setup()
    for as_of in score_result.scores.index[-10:]:  # a handful of real days, not just the last one
        result = compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, as_of)
        assert float(result.attributed_pct.sum()) + result.residual_pct == pytest.approx(result.actual_pct_pnl)
        assert float(result.attributed_dollar.sum()) + result.residual_dollar == pytest.approx(result.actual_dollar_pnl)


def test_actual_pnl_matches_an_independent_direct_reprice():
    portfolio, history, pca_result, score_result = _real_setup()
    as_of = score_result.scores.index[-1]
    result = compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, as_of)

    sorted_idx = history.sort_index().index
    loc = sorted_idx.get_loc(as_of)
    previous = sorted_idx[loc - 1]

    def _curve(date):
        row = history.loc[date]
        return pd.DataFrame({"maturity_years": history.columns.to_numpy(dtype=float), "yield": row.to_numpy(dtype=float)})

    price_prev = float((price_portfolio(portfolio, _curve(previous))["weight"] * price_portfolio(portfolio, _curve(previous))["price"]).sum())
    price_now = float((price_portfolio(portfolio, _curve(as_of))["weight"] * price_portfolio(portfolio, _curve(as_of))["price"]).sum())

    assert result.actual_dollar_pnl == pytest.approx(price_now - price_prev)
    assert result.base_price == pytest.approx(price_prev)


def test_as_of_and_previous_date_are_consecutive_retained_dates():
    portfolio, history, pca_result, score_result = _real_setup()
    as_of = score_result.scores.index[-1]
    result = compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, as_of)

    sorted_idx = history.sort_index().index
    loc = sorted_idx.get_loc(as_of)
    assert result.previous_date == str(sorted_idx[loc - 1].date())
    assert result.as_of_date == str(as_of.date())


def test_rejects_a_date_that_is_not_a_valid_score_date():
    portfolio, history, pca_result, score_result = _real_setup()
    with pytest.raises(ValueError, match="valid attribution date"):
        compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, "1900-01-01")


# --------------------------------------------------------------------------
# Zero-change day -- synthetic, so it's not left to chance whether a real
# day in the snapshot happens to have an exactly-zero move
# --------------------------------------------------------------------------


def test_zero_change_day_produces_zero_attribution_and_zero_residual():
    tenors = [1.0, 2.0, 5.0, 10.0]
    rows = {
        i: [0.01 + 0.001 * i, 0.012 + 0.0009 * i, 0.015 + 0.0008 * i, 0.02 + 0.0007 * i]
        for i in range(8)
    }
    # Make the last row identical to the second-to-last: zero change on the final day.
    rows[7] = rows[6]
    history = _synthetic_history(rows, tenors)

    pca_result = compute_curve_pca(history)
    score_result = compute_pc_scores(history, pca_result)
    portfolio = [_synthetic_bond("B1", 10.0, weight=1.0)]

    last_date = score_result.scores.index[-1]
    assert (score_result.scores.loc[last_date] == 0).all()

    result = compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, last_date)
    np.testing.assert_allclose(result.attributed_pct.to_numpy(), 0.0, atol=1e-12)
    np.testing.assert_allclose(result.attributed_dollar.to_numpy(), 0.0, atol=1e-12)
    assert result.residual_pct == pytest.approx(0.0, abs=1e-12)
    assert result.residual_dollar == pytest.approx(0.0, abs=1e-12)
    assert result.actual_pct_pnl == pytest.approx(0.0, abs=1e-12)
    assert result.actual_dollar_pnl == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------
# Changing portfolio weights changes the attribution
# --------------------------------------------------------------------------


def test_a_different_portfolio_produces_a_different_attribution():
    tenors = [1.0, 2.0, 5.0, 10.0, 20.0]
    rng = np.random.default_rng(0)
    base = np.array([0.01, 0.015, 0.02, 0.025, 0.03])
    rows = {0: base}
    for i in range(1, 10):
        rows[i] = rows[i - 1] + rng.normal(0, 0.0005, size=len(tenors))
    history = _synthetic_history(rows, tenors)

    pca_result = compute_curve_pca(history)
    score_result = compute_pc_scores(history, pca_result)
    as_of = score_result.scores.index[-1]

    short_heavy = [_synthetic_bond("SHORT", 1.0, weight=1.0)]
    long_heavy = [_synthetic_bond("LONG", 20.0, weight=1.0)]

    result_short = compute_factor_pnl_attribution(short_heavy, history, pca_result, score_result, as_of)
    result_long = compute_factor_pnl_attribution(long_heavy, history, pca_result, score_result, as_of)

    assert not np.allclose(result_short.attributed_dollar.to_numpy(), result_long.attributed_dollar.to_numpy())
    # The long-maturity portfolio should be far more sensitive to PC1 (level)
    # in absolute terms than the short one, holding the same day's move fixed.
    assert abs(result_long.attributed_dollar[1]) > abs(result_short.attributed_dollar[1])
