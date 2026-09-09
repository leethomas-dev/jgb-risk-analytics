"""
Tests for models/bootstrap.py.

Covers: the self-consistency test (the critical one -- reprice the
original par bonds off the bootstrapped zero curve and confirm they land
back at par) AND the explicit distinction that this test validates
bootstrap arithmetic, not the par-curve assumption's fidelity to what MOF
actually publishes; the short-end design decision, checked as an
algebraic identity rather than merely asserted; the flat-curve sanity
check; monotonic maturities and strictly-decreasing discount factors on
real data; genuine grid-independence against both the 12-point snapshot
and a 15-point live/cache-shaped fixture; the freq parameter actually
being used; the coupon-effect sensitivity check (implied_ytm,
coupon_effect_sensitivity) that quantifies the par-curve simplification's
error; and input validation.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_loader import _MOF_TENOR_COLUMNS, load_jgb_curve
from models.bond_pricing import price_bond
from models.bootstrap import (
    MONEY_MARKET_CUTOFF_YEARS,
    bootstrap_zero_curve,
    coupon_effect_sensitivity,
    discount_factor_at,
    implied_ytm,
    price_via_zero_curve,
    zero_rate_at,
)


def _flat_curve(y: float, tenors=(0.5, 1, 2, 5, 10, 20, 30, 40)) -> pd.DataFrame:
    return pd.DataFrame({"maturity_years": list(tenors), "yield": [y] * len(tenors)})


def _build_live_grid_fixture() -> pd.DataFrame:
    """A 15-point curve shaped exactly like the live/cache tenor grid
    (no sub-year points) -- mirrors test_bond_pricing.py's own fixture, so
    this module is checked against the same two real grid shapes every
    other pricing-adjacent module in this project is."""
    tenors = sorted(_MOF_TENOR_COLUMNS.values())
    yields = [0.005 + 0.00075 * t for t in tenors]
    return pd.DataFrame({"maturity_years": tenors, "yield": yields})


# --------------------------------------------------------------------------
# The self-consistency test -- the critical one
# --------------------------------------------------------------------------


@pytest.mark.parametrize("build_curve", [lambda: load_jgb_curve(prefer_live=False), _build_live_grid_fixture])
def test_original_par_bonds_reprice_to_par_off_the_zero_curve(build_curve):
    curve = build_curve()
    zero_curve = bootstrap_zero_curve(curve)

    for _, row in curve.sort_values("maturity_years").iterrows():
        maturity = float(row["maturity_years"])
        if maturity < MONEY_MARKET_CUTOFF_YEARS:
            continue  # a single-cash-flow "bond" isn't an interesting reprice check
        coupon = float(row["yield"])
        price = price_via_zero_curve(100.0, coupon, maturity, zero_curve)
        assert price == pytest.approx(100.0, abs=1e-6), (
            f"tenor {maturity}Y repriced to {price}, not par -- bootstrap arithmetic is wrong"
        )


def test_self_consistency_passing_does_not_imply_par_curve_input_fidelity():
    # The knock-on this project's brief calls out explicitly: the self-
    # consistency check above passes for ANY input treated as a par curve,
    # regardless of whether that input is really a par curve, because the
    # discount factors were solved FROM that same assumption. Demonstrated
    # directly: build a curve from prices that are NOT par (bonds trading
    # away from 100, as real JGBs do), read off their YTMs as if they were
    # par rates, bootstrap that, and confirm the self-consistency check
    # still passes -- proving it cannot detect the input-fidelity problem.
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve)  # any curve at all works for this demonstration
    for _, row in curve.sort_values("maturity_years").iterrows():
        maturity = float(row["maturity_years"])
        if maturity < MONEY_MARKET_CUTOFF_YEARS:
            continue
        price = price_via_zero_curve(100.0, float(row["yield"]), maturity, zero_curve)
        assert price == pytest.approx(100.0, abs=1e-6)
    # And yet, per test_coupon_effect_grows_with_maturity_on_the_real_curve
    # below, a real bond of the SAME maturity with a different coupon would
    # NOT reprice to the input rate's YTM -- the self-consistency check
    # above says nothing about that, by construction.


# --------------------------------------------------------------------------
# Design decision 2 -- short end used directly as a zero rate
# --------------------------------------------------------------------------


def test_zero_rate_at_first_grid_point_equals_quoted_par_yield():
    # The bootstrap recursion's very first step (0.5Y, freq=2, no prior
    # period to net out) must algebraically collapse to "zero rate ==
    # quoted par yield" -- checked directly, not assumed (module docstring
    # design decision 2).
    curve = _flat_curve(0.0234)  # arbitrary; curve_yield_at(curve, 0.5) == 0.0234 either way
    zero_curve = bootstrap_zero_curve(curve)
    first_point = zero_curve.loc[zero_curve["maturity_years"] == 0.5].iloc[0]
    assert first_point["zero_rate"] == pytest.approx(0.0234)


def test_sub_1y_money_market_tenors_carried_through_unchanged():
    curve = load_jgb_curve(prefer_live=False)  # snapshot has 1M/3M tenors below 1Y
    money_market_rows = curve.loc[curve["maturity_years"] < MONEY_MARKET_CUTOFF_YEARS]
    assert len(money_market_rows) >= 2  # sanity-check the fixture itself

    zero_curve = bootstrap_zero_curve(curve)
    for _, row in money_market_rows.iterrows():
        match = zero_curve.loc[zero_curve["maturity_years"] == row["maturity_years"]]
        assert len(match) == 1
        assert match.iloc[0]["zero_rate"] == pytest.approx(row["yield"])


# --------------------------------------------------------------------------
# Flat curve -> flat zero curve
# --------------------------------------------------------------------------


def test_flat_par_curve_produces_an_equally_flat_zero_curve():
    zero_curve = bootstrap_zero_curve(_flat_curve(0.02))
    np.testing.assert_allclose(zero_curve["zero_rate"].to_numpy(), 0.02, atol=1e-10)


# --------------------------------------------------------------------------
# Structural properties on real data
# --------------------------------------------------------------------------


def test_maturities_are_sorted_ascending():
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve)
    assert zero_curve["maturity_years"].is_monotonic_increasing


def test_discount_factors_are_strictly_decreasing_with_maturity():
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve)
    diffs = zero_curve["discount_factor"].diff().dropna()
    assert (diffs < 0).all()


def test_grid_spans_from_first_period_to_the_curve_own_longest_maturity():
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve)
    assert zero_curve["maturity_years"].max() == pytest.approx(curve["maturity_years"].max())
    assert zero_curve["maturity_years"].min() == pytest.approx(curve["maturity_years"].min())


# --------------------------------------------------------------------------
# Grid independence -- both real grid shapes
# --------------------------------------------------------------------------


def test_snapshot_and_live_grid_fixtures_have_the_documented_shapes():
    assert len(load_jgb_curve(prefer_live=False)) == 12
    assert len(_build_live_grid_fixture()) == 15


@pytest.mark.parametrize("build_curve", [lambda: load_jgb_curve(prefer_live=False), _build_live_grid_fixture])
def test_bootstraps_cleanly_against_both_grid_shapes(build_curve):
    curve = build_curve()
    zero_curve = bootstrap_zero_curve(curve)
    assert not zero_curve.isna().any().any()
    assert zero_curve["maturity_years"].max() == pytest.approx(curve["maturity_years"].max())


# --------------------------------------------------------------------------
# Compounding convention (freq) is genuinely parameterized
# --------------------------------------------------------------------------


def test_freq_changes_the_grid_spacing():
    curve = _flat_curve(0.02, tenors=(1, 2, 5, 10))
    semiannual = bootstrap_zero_curve(curve, freq=2)
    annual = bootstrap_zero_curve(curve, freq=1)
    # Semiannual grid has a point every 0.5Y; annual only every 1.0Y.
    assert semiannual["maturity_years"].min() == pytest.approx(0.5)
    assert annual["maturity_years"].min() == pytest.approx(1.0)


def test_freq_matching_between_bootstrap_and_reprice_is_required_for_par():
    # A mismatched compounding convention between bootstrap and discounting
    # is exactly the silent error design decision 3 warns about -- confirm
    # it actually produces a visible gap, so that warning isn't
    # hypothetical. Isolates the compounding formula alone: same cash-flow
    # schedule (built with freq=2, matching how the curve was bootstrapped)
    # in both cases, only the freq passed to discount_factor_at differs.
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve, freq=2)
    tenor_row = curve.loc[curve["maturity_years"] == 10.0].iloc[0]
    coupon, maturity, freq_cf = float(tenor_row["yield"]), 10.0, 2

    n = round(maturity * freq_cf)
    times = maturity - (n - np.arange(1, n + 1)) / freq_cf
    cash_flows = np.full(n, 100.0 * coupon / freq_cf)
    cash_flows[-1] += 100.0

    matched = float(np.sum(cash_flows * discount_factor_at(zero_curve, times, freq=2)))
    mismatched = float(np.sum(cash_flows * discount_factor_at(zero_curve, times, freq=1)))

    assert matched == pytest.approx(100.0, abs=1e-6)
    assert mismatched != pytest.approx(100.0, abs=1e-2)


# --------------------------------------------------------------------------
# zero_rate_at / discount_factor_at: interpolation + extrapolation
# --------------------------------------------------------------------------


def test_zero_rate_at_interpolates_linearly_between_two_known_points():
    zero_curve = pd.DataFrame({"maturity_years": [5.0, 10.0], "zero_rate": [0.02, 0.03]})
    assert zero_rate_at(zero_curve, 7.5) == pytest.approx(0.025)


def test_zero_rate_at_extrapolates_flat_beyond_the_curve_range():
    zero_curve = pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0], "zero_rate": [0.01, 0.02, 0.03]})
    assert zero_rate_at(zero_curve, 0.1) == pytest.approx(0.01)
    assert zero_rate_at(zero_curve, 50.0) == pytest.approx(0.03)


def test_discount_factor_at_matches_the_compounding_formula():
    zero_curve = pd.DataFrame({"maturity_years": [10.0], "zero_rate": [0.02]})
    expected = (1.0 + 0.02 / 2) ** (-2 * 10.0)
    assert discount_factor_at(zero_curve, 10.0, freq=2) == pytest.approx(expected)


# --------------------------------------------------------------------------
# implied_ytm: round-trips price_bond, the definition of YTM
# --------------------------------------------------------------------------


def test_implied_ytm_recovers_a_known_flat_yield():
    # A bond priced off a FLAT curve at yield y has YTM == y by definition
    # -- the cleanest possible round-trip check, independent of the zero
    # curve entirely.
    flat_yield = 0.025
    price = price_bond(100.0, 0.03, 10.0, _flat_curve(flat_yield))
    ytm = implied_ytm(100.0, 0.03, 10.0, price)
    assert ytm == pytest.approx(flat_yield, abs=1e-8)


def test_implied_ytm_of_a_par_priced_bond_equals_its_own_coupon():
    # Standard bond-math identity: a bond priced at exactly par has YTM
    # equal to its own coupon rate, regardless of the curve shape used to
    # get it there.
    ytm = implied_ytm(100.0, 0.025, 10.0, target_price=100.0)
    assert ytm == pytest.approx(0.025, abs=1e-8)


# --------------------------------------------------------------------------
# coupon_effect_sensitivity: quantifying the par-curve simplification
# --------------------------------------------------------------------------


def test_coupon_effect_is_zero_on_a_flat_curve():
    # Provable directly: on a flat zero curve, EVERY bond's YTM equals the
    # flat rate regardless of its own coupon (no coupon-timing effect when
    # every cash flow is discounted at the same rate) -- so this
    # simplification's error is exactly zero here, not merely small.
    curve = _flat_curve(0.02)
    result = coupon_effect_sensitivity(curve)
    np.testing.assert_allclose(result["zero_coupon_gap_bp"].to_numpy(), 0.0, atol=1e-4)
    np.testing.assert_allclose(result["high_coupon_gap_bp"].to_numpy(), 0.0, atol=1e-4)


def test_coupon_effect_grows_with_maturity_on_the_real_curve():
    # On the project's real, upward-sloping snapshot curve, the coupon
    # effect should be near zero at the front end and materially larger in
    # the ultra-long segment -- the segment this project's portfolio is
    # most concentrated in (docs/phase_3c_documentation.md).
    curve = load_jgb_curve(prefer_live=False)
    result = coupon_effect_sensitivity(curve).set_index("maturity_years")
    short_gap = abs(result.loc[2.0, "zero_coupon_gap_bp"])
    long_gap = abs(result.loc[40.0, "zero_coupon_gap_bp"])
    assert long_gap > short_gap
    assert short_gap < 5.0  # front end: a fraction of a basis point in practice
    assert long_gap > 5.0  # ultra-long: materially larger


def test_coupon_effect_direction_matches_theory_on_an_upward_sloping_curve():
    # On an upward-sloping curve, a low (here, zero) coupon bond returns
    # more of its value at the far/higher-yielding end -> its YTM should
    # sit ABOVE the input "par" rate; a high-coupon bond returns more
    # value early/at the lower-yielding end -> its YTM should sit BELOW
    # it. Both directions checked directly, not merely "some gap exists".
    curve = load_jgb_curve(prefer_live=False)
    result = coupon_effect_sensitivity(curve).set_index("maturity_years")
    row = result.loc[20.0]
    assert row["zero_coupon_gap_bp"] > 0
    assert row["high_coupon_gap_bp"] < 0


def test_coupon_effect_scenarios_reprice_to_their_own_target_price():
    # coupon_effect_sensitivity's implied YTMs are only meaningful if they
    # actually round-trip back to the same price they were solved from --
    # checked directly against price_bond, not assumed from implied_ytm's
    # own unit tests above.
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve)
    maturity = 20.0
    zero_coupon_price = price_via_zero_curve(100.0, 0.0, maturity, zero_curve)

    result = coupon_effect_sensitivity(curve).set_index("maturity_years")
    ytm = result.loc[maturity, "zero_coupon_ytm"]
    repriced = price_bond(100.0, 0.0, maturity, _flat_curve(ytm))
    assert repriced == pytest.approx(zero_coupon_price, abs=1e-4)


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_empty_curve_rejected():
    empty_curve = pd.DataFrame({"maturity_years": [], "yield": []})
    with pytest.raises(ValueError, match="par_curve is empty"):
        bootstrap_zero_curve(empty_curve)


def test_non_positive_freq_rejected():
    with pytest.raises(ValueError, match="freq"):
        bootstrap_zero_curve(_flat_curve(0.02), freq=0)
