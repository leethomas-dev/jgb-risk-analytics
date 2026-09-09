"""
Tests for models/bond_pricing.py.

Covers: linear interpolation between curve tenor points; the flat
extrapolation policy, exercised directly and through price_bond; an
analytic check against the closed-form flat-yield bond price formula
(validates discounting independently of curve shape); par/premium/discount
sanity checks; genuine grid-independence against BOTH the 12-point
snapshot and a 15-point live/cache-shaped fixture (not merely asserted);
input validation; and portfolio-level pricing via the config loader.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import _MOF_TENOR_COLUMNS, load_jgb_curve
from models.bond_pricing import (
    _coupon_boundaries,
    accrued_interest,
    accrued_interest_portfolio,
    curve_yield_at,
    dirty_price,
    dirty_price_portfolio,
    price_bond,
    price_portfolio,
)


def _flat_curve(y: float, tenors=(0.5, 1, 2, 5, 10, 20, 30, 40)) -> pd.DataFrame:
    return pd.DataFrame({"maturity_years": list(tenors), "yield": [y] * len(tenors)})


def _closed_form_flat_yield_price(face, coupon_rate, maturity_years, flat_yield, freq):
    """Standard flat-yield bond price: coupon annuity + discounted redemption.
    Independent of price_bond -- computed directly from the textbook formula,
    not by re-deriving price_bond's own cash-flow-schedule logic."""
    c = face * coupon_rate / freq
    y = flat_yield / freq
    n = round(maturity_years * freq)
    if y == 0:
        return c * n + face
    return c * (1 - (1 + y) ** (-n)) / y + face * (1 + y) ** (-n)


def _build_live_grid_fixture() -> pd.DataFrame:
    """A 15-point curve shaped exactly like the live/cache tenor grid (Phase 1
    §4.5): the real _MOF_TENOR_COLUMNS tenor set (1Y..10Y, 15Y, 20Y, 25Y, 30Y,
    40Y, no sub-year points), with synthetic-but-monotonic yields -- not real
    MOF data, since this fixture's only job is to have a different point
    count and tenor set than the snapshot for the grid-independence test."""
    tenors = sorted(_MOF_TENOR_COLUMNS.values())
    yields = [0.005 + 0.00075 * t for t in tenors]
    return pd.DataFrame({"maturity_years": tenors, "yield": yields})


# --------------------------------------------------------------------------
# curve_yield_at: interpolation
# --------------------------------------------------------------------------


def test_interpolates_linearly_between_two_known_points():
    curve = pd.DataFrame({"maturity_years": [5.0, 10.0], "yield": [0.02, 0.03]})
    assert curve_yield_at(curve, 7.5) == pytest.approx(0.025)


def test_returns_exact_yield_at_a_known_tenor_point():
    curve = pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0], "yield": [0.01, 0.02, 0.03]})
    assert curve_yield_at(curve, 5.0) == pytest.approx(0.02)


def test_curve_yield_at_accepts_array_input():
    curve = pd.DataFrame({"maturity_years": [5.0, 10.0], "yield": [0.02, 0.03]})
    result = curve_yield_at(curve, np.array([5.0, 7.5, 10.0]))
    np.testing.assert_allclose(result, [0.02, 0.025, 0.03])


def test_handles_an_unsorted_curve_defensively():
    curve = pd.DataFrame({"maturity_years": [10.0, 1.0, 5.0], "yield": [0.03, 0.01, 0.02]})
    assert curve_yield_at(curve, 7.5) == pytest.approx(0.025)


# --------------------------------------------------------------------------
# curve_yield_at: extrapolation policy (direct test, per requirement)
# --------------------------------------------------------------------------


def test_extrapolation_is_flat_below_shortest_tenor():
    curve = pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0], "yield": [0.01, 0.02, 0.03]})
    # Flat at the shortest tenor's yield -- NOT a linear continuation of the
    # 1Y-5Y slope, which would give a lower (here, even more extreme) value.
    assert curve_yield_at(curve, 0.25) == pytest.approx(0.01)
    assert curve_yield_at(curve, 0.0001) == pytest.approx(0.01)


def test_extrapolation_is_flat_above_longest_tenor():
    curve = pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0], "yield": [0.01, 0.02, 0.03]})
    # Flat at the longest tenor's yield -- NOT a linear continuation of the
    # 5Y-10Y slope.
    assert curve_yield_at(curve, 20.0) == pytest.approx(0.03)
    assert curve_yield_at(curve, 100.0) == pytest.approx(0.03)


def test_extrapolation_would_diverge_from_flat_if_linear_continuation_were_used():
    # Guards against a future accidental switch to linear extrapolation:
    # the flat and linearly-continued values are meaningfully different for
    # this curve, so this test cannot pass by coincidence.
    curve = pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0], "yield": [0.01, 0.02, 0.03]})
    slope_per_year = (0.03 - 0.02) / (10.0 - 5.0)
    linear_continuation_at_20 = 0.03 + slope_per_year * (20.0 - 10.0)
    assert linear_continuation_at_20 != pytest.approx(0.03)
    assert curve_yield_at(curve, 20.0) == pytest.approx(0.03)


def test_price_bond_uses_flat_extrapolation_beyond_curve_range():
    # A 20Y bond priced against a curve quoted only to 10Y: every cash flow
    # past 10Y must be discounted at the curve's flat-extrapolated 10Y
    # yield. Verified against an independent hand-rolled price using that
    # exact policy, not by calling back into price_bond's own machinery.
    curve = pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0], "yield": [0.01, 0.02, 0.03]})
    face, coupon_rate, maturity, freq = 100.0, 0.03, 20.0, 2

    n = round(maturity * freq)
    expected = 0.0
    for i in range(1, n + 1):
        t = maturity - (n - i) / freq
        cf = face * coupon_rate / freq + (face if i == n else 0.0)
        y = 0.03 if t > 10.0 else np.interp(t, [1.0, 5.0, 10.0], [0.01, 0.02, 0.03])
        expected += cf / (1 + y / freq) ** (freq * t)

    assert price_bond(face, coupon_rate, maturity, curve, freq=freq) == pytest.approx(expected)


# --------------------------------------------------------------------------
# Analytic check: flat curve vs. closed-form bond pricing formula
# --------------------------------------------------------------------------


def test_matches_closed_form_flat_yield_price_semiannual():
    face, coupon_rate, maturity, freq = 100.0, 0.03, 10.0, 2
    flat_yield = 0.025
    curve = _flat_curve(flat_yield)
    price = price_bond(face, coupon_rate, maturity, curve, freq=freq)
    expected = _closed_form_flat_yield_price(face, coupon_rate, maturity, flat_yield, freq)
    assert price == pytest.approx(expected)


def test_matches_closed_form_flat_yield_price_annual_frequency():
    # Confirms freq is genuinely parameterized, not hardcoded to 2.
    face, coupon_rate, maturity, freq = 100.0, 0.03, 5.0, 1
    flat_yield = 0.02
    curve = _flat_curve(flat_yield)
    price = price_bond(face, coupon_rate, maturity, curve, freq=freq)
    expected = _closed_form_flat_yield_price(face, coupon_rate, maturity, flat_yield, freq)
    assert price == pytest.approx(expected)


def test_matches_closed_form_flat_yield_price_quarterly_frequency():
    face, coupon_rate, maturity, freq = 100.0, 0.04, 3.0, 4
    flat_yield = 0.03
    curve = _flat_curve(flat_yield)
    price = price_bond(face, coupon_rate, maturity, curve, freq=freq)
    expected = _closed_form_flat_yield_price(face, coupon_rate, maturity, flat_yield, freq)
    assert price == pytest.approx(expected)


# --------------------------------------------------------------------------
# Sanity checks: par / premium / discount
# --------------------------------------------------------------------------


def test_par_coupon_prices_at_face_value():
    face, flat_yield = 100.0, 0.02
    curve = _flat_curve(flat_yield)
    price = price_bond(face, flat_yield, 10.0, curve)  # coupon == yield exactly
    assert price == pytest.approx(face, abs=1e-6)


def test_coupon_above_prevailing_yield_prices_at_premium():
    curve = _flat_curve(0.02)
    price = price_bond(100.0, 0.05, 10.0, curve)
    assert price > 100.0


def test_coupon_below_prevailing_yield_prices_at_discount():
    curve = _flat_curve(0.05)
    price = price_bond(100.0, 0.02, 10.0, curve)
    assert price < 100.0


def test_zero_coupon_bond_prices_below_face():
    curve = _flat_curve(0.03)
    price = price_bond(100.0, 0.0, 10.0, curve)
    assert 0.0 < price < 100.0


# --------------------------------------------------------------------------
# Grid independence -- genuinely tested against BOTH real grid shapes
# --------------------------------------------------------------------------


def test_snapshot_and_live_grid_fixtures_have_the_documented_shapes():
    # Sanity-checks the fixtures themselves before relying on them below.
    snapshot_curve = load_jgb_curve(prefer_live=False)
    live_like_curve = _build_live_grid_fixture()
    assert len(snapshot_curve) == 12
    assert len(live_like_curve) == 15


@pytest.mark.parametrize("build_curve", [lambda: load_jgb_curve(prefer_live=False), _build_live_grid_fixture])
def test_price_bond_correct_against_both_grid_shapes(build_curve):
    """The same bond, priced through the same price_bond code path, against
    the 12-point snapshot grid and the 15-point live/cache-shaped grid --
    verified each time against an independently computed expected price
    (not merely "it ran without crashing")."""
    curve = build_curve()
    face, coupon_rate, maturity, freq = 100.0, 0.025, 17.0, 2  # 17Y: falls
    # between different neighbor pairs on each grid (10/20 on the snapshot,
    # 15/20 on the live-shaped grid) -- exercises each grid's own points.

    price = price_bond(face, coupon_rate, maturity, curve, freq=freq)

    n = round(maturity * freq)
    tenors = curve.sort_values("maturity_years")["maturity_years"].to_numpy()
    yields = curve.sort_values("maturity_years")["yield"].to_numpy()
    expected = 0.0
    for i in range(1, n + 1):
        t = maturity - (n - i) / freq
        cf = face * coupon_rate / freq + (face if i == n else 0.0)
        y = np.interp(t, tenors, yields, left=yields[0], right=yields[-1])
        expected += cf / (1 + y / freq) ** (freq * t)

    assert price == pytest.approx(expected)


def test_yield_at_exact_shared_tenor_matches_both_grids_own_quote():
    # 10Y is an exact tenor point on BOTH grids -- no interpolation error
    # should occur, on either one, regardless of how many other points
    # surround it.
    snapshot_curve = load_jgb_curve(prefer_live=False)
    live_like_curve = _build_live_grid_fixture()
    for curve in (snapshot_curve, live_like_curve):
        row = curve.loc[curve["maturity_years"] == 10.0].iloc[0]
        assert curve_yield_at(curve, 10.0) == pytest.approx(row["yield"])


def test_first_coupon_requires_extrapolation_on_live_grid_but_not_snapshot():
    # Concrete illustration of the extrapolation-is-not-hypothetical point
    # from curve_yield_at's docstring: the live-shaped grid's shortest tenor
    # is 1Y, so a bond's first semiannual coupon (t=0.5) is below it and
    # must hit the flat-extrapolation branch; the snapshot grid's shortest
    # tenor is 1M, well below 0.5, so the same lookup interpolates normally.
    live_like_curve = _build_live_grid_fixture()
    snapshot_curve = load_jgb_curve(prefer_live=False)
    assert live_like_curve["maturity_years"].min() == 1.0
    assert snapshot_curve["maturity_years"].min() < 0.5

    shortest_live_yield = live_like_curve.sort_values("maturity_years")["yield"].iloc[0]
    assert curve_yield_at(live_like_curve, 0.5) == pytest.approx(shortest_live_yield)
    assert curve_yield_at(snapshot_curve, 0.5) != pytest.approx(
        snapshot_curve.sort_values("maturity_years")["yield"].iloc[0]
    )


# --------------------------------------------------------------------------
# Zero-curve discounting basis (Phase 4.5C addition) -- auto-detected from
# the curve's own columns; every test above uses a 'yield'-column curve
# and is unaffected.
# --------------------------------------------------------------------------


def test_price_bond_auto_detects_a_zero_curve_via_its_column():
    from models.bootstrap import bootstrap_zero_curve

    par_curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(par_curve)
    assert "yield" not in zero_curve.columns
    assert "zero_rate" in zero_curve.columns
    price = price_bond(100.0, 0.02, 10.0, zero_curve)
    assert price > 0  # ran the zero-curve branch, not a KeyError on 'yield'


def test_par_and_zero_bases_generally_give_different_prices():
    from models.bootstrap import bootstrap_zero_curve

    par_curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(par_curve)
    par_price = price_bond(100.0, 0.038, 40.0, par_curve)
    zero_price = price_bond(100.0, 0.038, 40.0, zero_curve)
    assert par_price != pytest.approx(zero_price, rel=1e-3)


def test_zero_basis_reproduces_bootstraps_own_self_consistency_result():
    # price_bond's new zero-curve branch and models.bootstrap's own
    # price_via_zero_curve are two independent call paths to the same
    # underlying discount_factor_at -- confirms they agree, and that
    # price_bond itself (not just bootstrap.py) achieves the
    # self-consistency result docs/phase_4_5a_documentation.md §3 reports.
    from models.bootstrap import bootstrap_zero_curve

    par_curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(par_curve)
    row = par_curve.loc[par_curve["maturity_years"] == 10.0].iloc[0]
    price = price_bond(100.0, float(row["yield"]), 10.0, zero_curve)
    assert price == pytest.approx(100.0, abs=1e-6)


def test_zero_basis_reprices_to_par_consistently_when_freq_matches_end_to_end():
    # Both the zero curve's own bootstrap AND the reprice use the SAME
    # freq -- the scenario that actually matters in practice (models/
    # bond_pricing.py's own docstring: "MUST match the freq the zero
    # curve was itself bootstrapped with"). The isolated compounding-
    # mismatch case is covered directly at the bootstrap level
    # (tests/test_bootstrap.py::test_freq_matching_between_bootstrap_and_
    # reprice_is_required_for_par), not duplicated here.
    from models.bootstrap import bootstrap_zero_curve

    par_curve = load_jgb_curve(prefer_live=False)
    row = par_curve.loc[par_curve["maturity_years"] == 10.0].iloc[0]
    for freq in (1, 2, 4):
        zero_curve = bootstrap_zero_curve(par_curve, freq=freq)
        price = price_bond(100.0, float(row["yield"]), 10.0, zero_curve, freq=freq)
        assert price == pytest.approx(100.0, abs=1e-6), f"freq={freq}"


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_negative_face_value_rejected():
    with pytest.raises(ValueError, match="face_value"):
        price_bond(-100.0, 0.02, 10.0, _flat_curve(0.02))


def test_zero_face_value_rejected():
    with pytest.raises(ValueError, match="face_value"):
        price_bond(0.0, 0.02, 10.0, _flat_curve(0.02))


def test_non_positive_maturity_rejected():
    with pytest.raises(ValueError, match="maturity_years"):
        price_bond(100.0, 0.02, 0.0, _flat_curve(0.02))


def test_non_positive_freq_rejected():
    with pytest.raises(ValueError, match="freq"):
        price_bond(100.0, 0.02, 10.0, _flat_curve(0.02), freq=0)


def test_empty_curve_rejected():
    empty_curve = pd.DataFrame({"maturity_years": [], "yield": []})
    with pytest.raises(ValueError, match="curve is empty"):
        price_bond(100.0, 0.02, 10.0, empty_curve)


# --------------------------------------------------------------------------
# Portfolio-level pricing (via the Part A loader, never a hardcoded list)
# --------------------------------------------------------------------------


def test_price_portfolio_returns_one_row_per_bond_in_order():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = price_portfolio(portfolio, curve)
    assert list(results["name"]) == [b.name for b in portfolio]


def test_price_portfolio_matches_price_bond_per_row():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = price_portfolio(portfolio, curve)
    for bond, price in zip(portfolio, results["price"]):
        expected = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert price == pytest.approx(expected)


def test_default_portfolio_prices_are_plausible():
    # Loose bound: catches gross bugs (sign errors, unit errors, an off-by-
    # 100x from a percent/decimal slip) without pinning to exact numbers
    # that would shift if the snapshot curve or the illustrative coupons
    # are ever revised.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = price_portfolio(portfolio, curve)
    assert results["price"].between(40, 150).all()


def test_portfolio_weighted_price_is_computable_and_plausible():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = price_portfolio(portfolio, curve)
    weighted_price = float((results["weight"] * results["price"]).sum())
    assert 40.0 < weighted_price < 150.0


# --------------------------------------------------------------------------
# Settlement, day count, accrued interest, clean/dirty price (Phase 4.6A)
# --------------------------------------------------------------------------

_VAL_DATE = date(2026, 1, 1)  # a fixed, non-leap valuation date for determinism


def test_accrued_is_zero_at_the_default_settlement_date():
    # settlement_date defaults to valuation_date -- the phase brief's own
    # default -- which is exactly a coupon date by construction (module
    # docstring), so nothing has accrued yet.
    ai = accrued_interest(100.0, 0.04, 2.0, valuation_date=_VAL_DATE)
    assert ai == 0.0


def test_accrued_is_zero_exactly_on_a_later_coupon_date():
    # Landing exactly on ANY coupon date -- not just the first -- resets
    # accrued to zero (half-open period convention, accrued_interest()'s
    # own docstring).
    boundaries = _coupon_boundaries(2.0, _VAL_DATE, freq=2)
    third_coupon_date = boundaries[2]
    ai = accrued_interest(100.0, 0.04, 2.0, valuation_date=_VAL_DATE, settlement_date=third_coupon_date)
    assert ai == 0.0


def test_accrued_is_small_and_positive_immediately_after_a_coupon_date():
    boundaries = _coupon_boundaries(2.0, _VAL_DATE, freq=2)
    day_after = boundaries[1] + timedelta(days=1)
    ai = accrued_interest(100.0, 0.04, 2.0, valuation_date=_VAL_DATE, settlement_date=day_after)
    coupon_per_period = 100.0 * 0.04 / 2
    assert 0.0 < ai < coupon_per_period


def test_accrued_grows_monotonically_through_a_coupon_period():
    boundaries = _coupon_boundaries(2.0, _VAL_DATE, freq=2)
    period_start, period_end = boundaries[0], boundaries[1]
    days_in_period = (period_end - period_start).days
    offsets = [1, days_in_period // 4, days_in_period // 2, days_in_period - 1]
    accrued_values = [
        accrued_interest(
            100.0, 0.04, 2.0, valuation_date=_VAL_DATE,
            settlement_date=period_start + timedelta(days=d),
        )
        for d in offsets
    ]
    assert accrued_values == sorted(accrued_values)
    assert len(set(accrued_values)) == len(accrued_values)  # strictly increasing, not just non-decreasing


def test_accrued_before_the_first_coupon_of_a_newly_issued_bond():
    # Settlement lands in period 0 -- before the bond's very first coupon
    # -- handled by the same general period-finding logic as any other
    # period (accrued_interest()'s own docstring), not a special case.
    boundaries = _coupon_boundaries(2.0, _VAL_DATE, freq=2)
    mid_first_period = _VAL_DATE + timedelta(days=30)
    assert _VAL_DATE < mid_first_period < boundaries[1]
    ai = accrued_interest(100.0, 0.04, 2.0, valuation_date=_VAL_DATE, settlement_date=mid_first_period)
    assert 0.0 < ai < 100.0 * 0.04 / 2


def test_accrued_at_maturity_date_is_zero():
    boundaries = _coupon_boundaries(2.0, _VAL_DATE, freq=2)
    ai = accrued_interest(100.0, 0.04, 2.0, valuation_date=_VAL_DATE, settlement_date=boundaries[-1])
    assert ai == 0.0


def test_accrued_rejects_settlement_before_valuation_date():
    with pytest.raises(ValueError, match="precedes"):
        accrued_interest(
            100.0, 0.04, 2.0,
            valuation_date=_VAL_DATE,
            settlement_date=_VAL_DATE - timedelta(days=1),
        )


def test_accrued_rejects_settlement_after_maturity():
    boundaries = _coupon_boundaries(2.0, _VAL_DATE, freq=2)
    with pytest.raises(ValueError, match="after the bond's maturity date"):
        accrued_interest(
            100.0, 0.04, 2.0,
            valuation_date=_VAL_DATE,
            settlement_date=boundaries[-1] + timedelta(days=1),
        )


def test_accrued_interest_day_counting_reflects_the_extra_day_in_a_leap_year():
    # 2024 IS a leap year. A single-period (6-month) bond valued on
    # 2024-01-01 and settled on 2024-03-01 spans Feb 29 -- the elapsed-days
    # count must include that extra day. Hand-computed via Python's own
    # date arithmetic (an independent computation, not a re-derivation of
    # accrued_interest's own logic) and cross-checked against the
    # documented ACT/365 formula (coupon_per_period x days_elapsed /
    # days_in_period).
    valuation, settlement = date(2024, 1, 1), date(2024, 3, 1)
    hand_computed_days_elapsed = (settlement - valuation).days
    assert hand_computed_days_elapsed == 60  # 31 (Jan) + 29 (Feb, leap year)

    face, coupon, maturity, freq = 100.0, 0.04, 0.5, 2
    boundaries = _coupon_boundaries(maturity, valuation, freq)
    days_in_period = (boundaries[1] - boundaries[0]).days

    ai = accrued_interest(face, coupon, maturity, freq=freq, valuation_date=valuation, settlement_date=settlement)
    expected = (face * coupon / freq) * (hand_computed_days_elapsed / days_in_period)
    assert ai == pytest.approx(expected)


def test_dirty_minus_clean_equals_accrued():
    curve = load_jgb_curve(prefer_live=False)
    boundaries = _coupon_boundaries(10.0, _VAL_DATE, freq=2)
    settlement = boundaries[0] + timedelta(days=45)

    clean = price_bond(100.0, 0.025, 10.0, curve)
    ai = accrued_interest(100.0, 0.025, 10.0, valuation_date=_VAL_DATE, settlement_date=settlement)
    dirty = dirty_price(
        100.0, 0.025, 10.0, curve, valuation_date=_VAL_DATE, settlement_date=settlement
    )
    assert dirty - clean == pytest.approx(ai)


def test_dirty_price_equals_clean_price_at_the_default_settlement_date():
    # No settlement_date passed -> reproduces price_bond's own clean price
    # exactly, since accrued is 0 at the default (module docstring).
    curve = load_jgb_curve(prefer_live=False)
    clean = price_bond(100.0, 0.025, 10.0, curve)
    dirty = dirty_price(100.0, 0.025, 10.0, curve)
    assert dirty == pytest.approx(clean)


def test_accrued_interest_rejects_non_positive_inputs_like_price_bond_does():
    with pytest.raises(ValueError, match="face_value"):
        accrued_interest(-100.0, 0.02, 10.0)
    with pytest.raises(ValueError, match="maturity_years"):
        accrued_interest(100.0, 0.02, 0.0)


# --------------------------------------------------------------------------
# Portfolio-level accrued interest and dirty price
# --------------------------------------------------------------------------


def test_accrued_interest_portfolio_returns_one_row_per_bond_in_order():
    portfolio = load_portfolio()
    settlement = date.today() + timedelta(days=10)
    results = accrued_interest_portfolio(portfolio, settlement_date=settlement)
    assert list(results["name"]) == [b.name for b in portfolio]
    assert (results["accrued_interest"] >= 0.0).all()


def test_accrued_interest_portfolio_matches_accrued_interest_per_row():
    portfolio = load_portfolio()
    settlement = date.today() + timedelta(days=10)
    results = accrued_interest_portfolio(portfolio, settlement_date=settlement)
    for bond, ai in zip(portfolio, results["accrued_interest"]):
        expected = accrued_interest(
            bond.face_value, bond.coupon_rate, bond.maturity_years, settlement_date=settlement
        )
        assert ai == pytest.approx(expected)


def test_dirty_price_portfolio_equals_clean_plus_accrued_per_row():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    settlement = date.today() + timedelta(days=10)
    results = dirty_price_portfolio(portfolio, curve, settlement_date=settlement)
    assert list(results["name"]) == [b.name for b in portfolio]
    pd.testing.assert_series_equal(
        results["dirty_price"],
        results["clean_price"] + results["accrued_interest"],
        check_names=False,
    )
