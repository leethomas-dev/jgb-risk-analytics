"""
Tests for models/cash_flow_ladder.py.

Covers: the ladder's dates are ascending with no duplicates; a real,
independent cross-check that total present value equals the portfolio's
own weighted clean price (price_portfolio, Phase 2B) -- the same number
computed two structurally different ways; total nominal principal exactly
equals sum(weight * face_value) across the portfolio, a hard invariant
regardless of curve or coupon; per-date aggregation actually sums
contributions from every bond sharing that date, not just one; the
present-value ultra-long share sits below the nominal one (discounting
shrinks a far-future payment more than a near one); and the chart
function's basic contract (file written, bad basis rejected).

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_portfolio
from models.cash_flow_ladder import (
    LADDER_COLUMNS,
    CashFlowLadderResult,
    compute_cash_flow_ladder,
    plot_cash_flow_ladder,
)

_VAL_DATE = date(2026, 1, 1)


@pytest.fixture(scope="module")
def curve():
    return load_jgb_curve(prefer_live=False)


@pytest.fixture(scope="module")
def portfolio():
    return load_portfolio()


# --------------------------------------------------------------------------
# Structural properties of the ladder
# --------------------------------------------------------------------------


def test_ladder_has_the_documented_columns(curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve, valuation_date=_VAL_DATE)
    assert list(result.ladder.columns) == LADDER_COLUMNS


def test_ladder_dates_are_ascending_with_no_duplicates(curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve, valuation_date=_VAL_DATE)
    dates = result.ladder["date"]
    assert list(dates) == sorted(dates)
    assert dates.is_unique


def test_ladder_covers_from_the_first_coupon_to_the_longest_bonds_maturity(curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve, valuation_date=_VAL_DATE)
    longest = max(b.maturity_years for b in portfolio)
    assert result.ladder["years_from_valuation"].max() == pytest.approx(longest, abs=0.01)
    assert result.ladder["years_from_valuation"].min() == pytest.approx(0.5, abs=0.01)


# --------------------------------------------------------------------------
# The real cross-check: total PV == the portfolio's own weighted clean price
# --------------------------------------------------------------------------


def test_total_present_value_equals_the_portfolios_weighted_clean_price(curve, portfolio):
    # Two structurally independent computations of the same quantity: the
    # sum of every discounted future cash flow (this module, built from
    # cash_flow_schedule + discount_factors_at) vs. price_portfolio's own
    # weighted price (built from price_bond, which sums the same discounted
    # cash flows internally but never exposes them individually). Agreement
    # here is a genuine, non-circular check -- not the same code path run
    # twice.
    result = compute_cash_flow_ladder(portfolio, curve)
    prices = price_portfolio(portfolio, curve)
    weighted_clean_price = float((prices["weight"] * prices["price"]).sum())
    assert result.total_pv == pytest.approx(weighted_clean_price, rel=1e-9)


# --------------------------------------------------------------------------
# Nominal totals: a hard, curve-independent invariant
# --------------------------------------------------------------------------


def test_total_nominal_principal_equals_weight_weighted_face_value(curve, portfolio):
    # Independent of the curve or any coupon rate: every bond redeems its
    # own face value exactly once, scaled by its own portfolio weight.
    result = compute_cash_flow_ladder(portfolio, curve)
    expected = sum(b.weight * b.face_value for b in portfolio)
    assert result.ladder["principal_nominal"].sum() == pytest.approx(expected)


def test_total_nominal_coupon_matches_an_independent_per_bond_sum(curve, portfolio):
    from models.bond_pricing import cash_flow_schedule

    result = compute_cash_flow_ladder(portfolio, curve, valuation_date=_VAL_DATE)
    expected = 0.0
    for bond in portfolio:
        n_periods, _ = cash_flow_schedule(bond.maturity_years, freq=2)
        expected += bond.weight * n_periods * (bond.face_value * bond.coupon_rate / 2)
    assert result.ladder["coupon_nominal"].sum() == pytest.approx(expected)


def test_first_payment_date_aggregates_every_bonds_own_first_coupon(curve, portfolio):
    # All six bonds share freq=2 and the same valuation date, so every one
    # of them pays its first coupon at the same 0.5-year mark -- the
    # aggregated coupon_nominal at that date must be the sum of ALL SIX
    # bonds' own contributions, not just one.
    result = compute_cash_flow_ladder(portfolio, curve, valuation_date=_VAL_DATE)
    first_row = result.ladder.iloc[0]
    expected = sum(b.weight * b.face_value * b.coupon_rate / 2 for b in portfolio)
    assert first_row["coupon_nominal"] == pytest.approx(expected)
    assert first_row["principal_nominal"] == 0.0  # no bond matures at 0.5Y


# --------------------------------------------------------------------------
# Ultra-long share: nominal vs. present value
# --------------------------------------------------------------------------


def test_ultra_long_shares_are_fractions_between_zero_and_one(curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve)
    assert 0.0 < result.ultra_long_nominal_share < 1.0
    assert 0.0 < result.ultra_long_pv_share < 1.0


def test_present_value_ultra_long_share_is_below_the_nominal_share(curve, portfolio):
    # On an upward-sloping curve, discounting shrinks a far-future (20Y+)
    # payment more than a near one -- the PV-weighted concentration in the
    # ultra-long segment must sit below the raw nominal concentration
    # there. A real, checked direction, not assumed.
    result = compute_cash_flow_ladder(portfolio, curve)
    assert result.ultra_long_pv_share < result.ultra_long_nominal_share


def test_ultra_long_share_uses_the_same_threshold_as_phase_3c_by_default(curve, portfolio):
    from models.ultra_long_profile import DEFAULT_ULTRA_LONG_THRESHOLD_YEARS

    result = compute_cash_flow_ladder(portfolio, curve)
    assert result.threshold_years == DEFAULT_ULTRA_LONG_THRESHOLD_YEARS


# --------------------------------------------------------------------------
# CashFlowLadderResult properties are consistent with the underlying table
# --------------------------------------------------------------------------


def test_total_properties_match_the_ladder_dataframes_own_sums(curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve)
    assert result.total_nominal == pytest.approx(float(result.ladder["total_nominal"].sum()))
    assert result.total_pv == pytest.approx(float(result.ladder["total_pv"].sum()))


# --------------------------------------------------------------------------
# Charting
# --------------------------------------------------------------------------


def test_plot_cash_flow_ladder_writes_a_file(tmp_path, curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve)
    output_path = tmp_path / "ladder.png"
    returned_path = plot_cash_flow_ladder(result, output_path=output_path, basis="nominal")
    assert returned_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_plot_cash_flow_ladder_accepts_pv_basis(tmp_path, curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve)
    output_path = tmp_path / "ladder_pv.png"
    plot_cash_flow_ladder(result, output_path=output_path, basis="pv")
    assert output_path.exists()


def test_plot_cash_flow_ladder_rejects_an_unknown_basis(curve, portfolio):
    result = compute_cash_flow_ladder(portfolio, curve)
    with pytest.raises(ValueError, match="basis"):
        plot_cash_flow_ladder(result, basis="bogus")
