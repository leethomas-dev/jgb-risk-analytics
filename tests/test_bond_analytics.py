"""
Tests for models/bond_analytics.py.

Covers: yield-to-maturity root-solving (including a deliberately-broken
solver, monkeypatched in, to prove the post-hoc convergence check
actually fires -- not just that the happy path works); the zero-coupon
Macaulay-duration-equals-maturity identity; modified duration's exact
formula relationship to Macaulay; convexity cross-checked against an
independently-derived closed-form analytic formula; the modified-vs-
effective-duration cross-check on BOTH a flat curve (tight tolerance,
proving no implementation bug) and the real, sloped snapshot curve
(a real, larger, explained gap -- bounded, not ignored); and the
duration+convexity Taylor approximation tracking an actual reprice far
better than duration alone for large yield moves.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_analytics import (
    YTM_VERIFY_TOLERANCE,
    bond_analytics_portfolio,
    convexity,
    macaulay_duration,
    modified_duration,
    yield_to_maturity,
)
from models.bond_pricing import cash_flow_schedule, price_bond
from models.key_rate_duration import DEFAULT_BUMP_SIZE, effective_duration_bond


def _flat_curve(y: float, tenors=(0.25, 1, 2, 5, 10, 20, 30, 40, 100)) -> pd.DataFrame:
    return pd.DataFrame({"maturity_years": list(tenors), "yield": [y] * len(tenors)})


def _analytic_convexity(face, coupon, maturity, y, freq=2) -> float:
    """Closed-form analytic convexity -- an INDEPENDENT derivation (not a
    call into models.bond_analytics.convexity's own central-difference
    code path), used only to cross-check it:

        convexity = (1/P) * sum_i[ t_i * (t_i + 1/freq) * CF_i / (1+y/freq)^(freq*t_i+2) ]
    """
    n_periods, cash_flow_times = cash_flow_schedule(maturity, freq)
    cf = np.full(n_periods, face * coupon / freq)
    cf[-1] += face
    discount = (1.0 + y / freq) ** (freq * cash_flow_times + 2)
    terms = cash_flow_times * (cash_flow_times + 1.0 / freq) * cf / discount
    price = price_bond(face, coupon, maturity, _flat_curve(y), freq=freq)
    return float(terms.sum() / price)


# --------------------------------------------------------------------------
# yield_to_maturity
# --------------------------------------------------------------------------


def test_ytm_of_a_par_bond_equals_its_flat_coupon_rate():
    face, coupon, maturity = 100.0, 0.025, 10.0
    price = price_bond(face, coupon, maturity, _flat_curve(coupon))  # coupon == yield -> par
    ytm = yield_to_maturity(face, coupon, maturity, price)
    assert ytm == pytest.approx(coupon, abs=1e-8)


def test_ytm_recovers_the_flat_rate_used_to_price_a_premium_and_discount_bond():
    for coupon, flat_rate, maturity in [(0.05, 0.02, 10.0), (0.01, 0.03, 20.0), (0.0, 0.025, 5.0)]:
        price = price_bond(100.0, coupon, maturity, _flat_curve(flat_rate))
        ytm = yield_to_maturity(100.0, coupon, maturity, price)
        assert ytm == pytest.approx(flat_rate, abs=1e-8)


def test_ytm_against_the_real_curve_reprices_to_the_original_price():
    # The direction-of-causation check the module docstring describes:
    # price comes FROM the curve first, then ytm is solved FROM that price
    # -- confirm round-tripping through yield_to_maturity and back through
    # price_bond (at a flat curve) returns the same price.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    for bond in portfolio:
        price = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        ytm = yield_to_maturity(bond.face_value, bond.coupon_rate, bond.maturity_years, price)
        reprice = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, _flat_curve(ytm))
        assert reprice == pytest.approx(price, abs=YTM_VERIFY_TOLERANCE)


def test_ytm_raises_a_clear_error_when_the_target_price_is_not_bracketed():
    # A 1-year zero-coupon bond priced ABOVE its own face value has no
    # solution within implied_ytm's search bracket (would need a yield
    # far below its -2% floor) -- must raise, not return a wrong number.
    with pytest.raises(ValueError, match="not bracketed"):
        yield_to_maturity(100.0, 0.0, 1.0, price=150.0)


def test_ytm_verification_actually_fires_on_a_broken_solver(monkeypatch):
    # Proves the post-hoc reprice check is a REAL, independent guard, not
    # just documentation -- monkeypatch the underlying root-finder to
    # return a deliberately wrong yield, and confirm yield_to_maturity
    # catches the resulting reprice mismatch rather than returning it.
    import models.bond_analytics as bond_analytics

    monkeypatch.setattr(bond_analytics, "implied_ytm", lambda *a, **k: 0.5)  # a wrong, unrelated yield
    face, coupon, maturity = 100.0, 0.02, 10.0
    correct_price = price_bond(face, coupon, maturity, _flat_curve(0.02))
    with pytest.raises(RuntimeError, match="did not converge"):
        yield_to_maturity(face, coupon, maturity, correct_price)


# --------------------------------------------------------------------------
# Macaulay duration: the zero-coupon anchor, and the general formula
# --------------------------------------------------------------------------


def test_zero_coupon_macaulay_duration_equals_maturity_exactly():
    # A single cash flow -> its own PV share is always 1.0, for any yield
    # -- the weighted average collapses to that one cash flow's time.
    for maturity in (2.0, 10.0, 17.3, 40.0):
        for ytm in (0.0, 0.01, 0.05, -0.005):
            mac_dur = macaulay_duration(100.0, 0.0, maturity, ytm)
            assert mac_dur == pytest.approx(maturity, abs=1e-9)


def test_macaulay_duration_is_less_than_maturity_for_a_coupon_bond():
    # A coupon bond returns some value before maturity, pulling the
    # weighted-average time below the maturity date itself.
    mac_dur = macaulay_duration(100.0, 0.03, 10.0, 0.03)
    assert 0.0 < mac_dur < 10.0


def test_modified_duration_equals_macaulay_over_one_plus_y_over_freq():
    face, coupon, maturity, ytm, freq = 100.0, 0.025, 10.0, 0.028, 2
    mac_dur = macaulay_duration(face, coupon, maturity, ytm, freq=freq)
    mod_dur = modified_duration(face, coupon, maturity, ytm, freq=freq)
    assert mod_dur == pytest.approx(mac_dur / (1.0 + ytm / freq))


# --------------------------------------------------------------------------
# Convexity: cross-checked against an independent closed-form formula
# --------------------------------------------------------------------------


def test_convexity_matches_an_independent_closed_form_formula():
    for maturity, coupon, ytm in [(2.0, 0.01, 0.014), (10.0, 0.02, 0.024), (40.0, 0.038, 0.035)]:
        numeric = convexity(100.0, coupon, maturity, ytm)
        analytic = _analytic_convexity(100.0, coupon, maturity, ytm)
        assert numeric == pytest.approx(analytic, rel=1e-5), f"maturity={maturity}"


def test_convexity_is_positive_for_an_ordinary_coupon_bond():
    # A standard (non-callable) bond's price is convex in yield -- always
    # positive convexity.
    for maturity in (2.0, 10.0, 40.0):
        assert convexity(100.0, 0.03, maturity, 0.03) > 0.0


# --------------------------------------------------------------------------
# Cross-check: modified duration (analytic, YTM-based) vs. effective
# duration (curve-based, Phase 3A bump-and-reprice) -- these are the real
# tests the phase brief asks for.
# --------------------------------------------------------------------------


def test_modified_matches_effective_duration_closely_on_a_flat_curve():
    # On a FLAT curve, the bond's own YTM equals the curve's rate exactly
    # at every cash flow -- analytic (YTM-based) and curve-based duration
    # measure the same thing, and should agree tightly. This is the
    # control that isolates "is the formula correct" from the real,
    # curve-slope-driven divergence tested below.
    for maturity, coupon in [(2.0, 0.01), (10.0, 0.02), (30.0, 0.035), (40.0, 0.038)]:
        flat_rate = 0.03
        curve = _flat_curve(flat_rate)
        price = price_bond(100.0, coupon, maturity, curve)
        ytm = yield_to_maturity(100.0, coupon, maturity, price)
        mod_dur = modified_duration(100.0, coupon, maturity, ytm)
        eff_dur = effective_duration_bond(100.0, coupon, maturity, curve)
        assert mod_dur == pytest.approx(eff_dur, rel=1e-3), (
            f"maturity={maturity}: modified={mod_dur} vs effective={eff_dur}"
        )


def test_modified_vs_effective_duration_gap_on_the_real_curve_is_bounded_and_grows_with_maturity():
    # Against the REAL (sloped) MOF curve, modified and effective duration
    # are NOT expected to match as tightly as on a flat curve -- they are
    # different sensitivities (module docstring) that coincide only when
    # the curve is flat across a bond's own cash flows. Investigated
    # directly (docs/phase_4_6b_documentation.md §2): the gap is small at
    # the short end and grows to several percent at the long end, tracking
    # the curve's own slope, not an implementation error (confirmed by the
    # flat-curve control above). Bounded here at a level well above the
    # measured real gap, so a genuine regression (e.g. a formula error
    # inflating the gap several-fold) would still be caught.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    gaps = {}
    for bond in portfolio:
        price = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        ytm = yield_to_maturity(bond.face_value, bond.coupon_rate, bond.maturity_years, price)
        mod_dur = modified_duration(bond.face_value, bond.coupon_rate, bond.maturity_years, ytm)
        eff_dur = effective_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        gaps[bond.maturity_years] = abs(mod_dur - eff_dur) / eff_dur

    assert gaps[2.0] < 0.01  # short end: curve is nearly flat across a 2Y bond's own cash flows
    assert gaps[40.0] < 0.15  # long end: a real, larger, but bounded gap
    # The gap should grow with maturity (more curve slope spanned by the
    # bond's own cash flows), not be roughly flat or non-monotonic --
    # checked directly rather than assumed.
    ordered_maturities = sorted(gaps)
    ordered_gaps = [gaps[m] for m in ordered_maturities]
    assert ordered_gaps == sorted(ordered_gaps)


# --------------------------------------------------------------------------
# The Taylor approximation check: duration+convexity should track an
# actual reprice far better than duration alone, for large yield moves.
# --------------------------------------------------------------------------


def test_duration_plus_convexity_tracks_actual_reprice_better_than_duration_alone():
    face, coupon, maturity = 100.0, 0.038, 40.0  # the portfolio's longest bond
    ytm = 0.03
    base_price = price_bond(face, coupon, maturity, _flat_curve(ytm))
    mod_dur = modified_duration(face, coupon, maturity, ytm)
    conv = convexity(face, coupon, maturity, ytm)

    for dy in (0.01, 0.02, -0.02):  # large moves, per the phase brief
        actual_price = price_bond(face, coupon, maturity, _flat_curve(ytm + dy))
        actual_pct = (actual_price - base_price) / base_price

        duration_only = -mod_dur * dy
        duration_and_convexity = -mod_dur * dy + 0.5 * conv * dy**2

        error_duration_only = abs(duration_only - actual_pct)
        error_with_convexity = abs(duration_and_convexity - actual_pct)
        assert error_with_convexity < error_duration_only, f"dy={dy}"


def test_duration_plus_convexity_is_a_tighter_bound_for_a_small_move_too():
    # Even for a small move, adding convexity should not make the
    # approximation WORSE -- confirms the sign/scale of the correction
    # term is right, not just "helps a lot for large moves specifically."
    face, coupon, maturity = 100.0, 0.038, 40.0
    ytm, dy = 0.03, 0.001
    base_price = price_bond(face, coupon, maturity, _flat_curve(ytm))
    mod_dur = modified_duration(face, coupon, maturity, ytm)
    conv = convexity(face, coupon, maturity, ytm)

    actual_price = price_bond(face, coupon, maturity, _flat_curve(ytm + dy))
    actual_pct = (actual_price - base_price) / base_price
    duration_only = -mod_dur * dy
    duration_and_convexity = -mod_dur * dy + 0.5 * conv * dy**2

    assert abs(duration_and_convexity - actual_pct) < abs(duration_only - actual_pct)


# --------------------------------------------------------------------------
# Portfolio-level reporting
# --------------------------------------------------------------------------


def test_bond_analytics_portfolio_returns_one_row_per_bond_plus_a_total():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = bond_analytics_portfolio(portfolio, curve)
    assert list(results.index[:-1]) == list(range(len(portfolio)))
    assert list(results["name"].iloc[:-1]) == [b.name for b in portfolio]
    assert results.index[-1] == "portfolio_total"


def test_bond_analytics_portfolio_price_matches_price_bond_per_row():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = bond_analytics_portfolio(portfolio, curve)
    for bond, price in zip(portfolio, results["price"].iloc[:-1]):
        expected = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert price == pytest.approx(expected)


def test_bond_analytics_portfolio_total_row_is_weight_weighted():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = bond_analytics_portfolio(portfolio, curve)
    per_bond = results.iloc[:-1]
    weights = np.array([b.weight for b in portfolio])

    expected_total_duration = float((per_bond["modified_duration"].to_numpy() * weights).sum())
    assert results.loc["portfolio_total", "modified_duration"] == pytest.approx(expected_total_duration)

    expected_total_price = float((per_bond["price"].to_numpy() * weights).sum())
    assert results.loc["portfolio_total", "price"] == pytest.approx(expected_total_price)


def test_bond_analytics_portfolio_effective_duration_matches_key_rate_duration_module():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    results = bond_analytics_portfolio(portfolio, curve)
    for bond, eff_dur in zip(portfolio, results["effective_duration"].iloc[:-1]):
        expected = effective_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert eff_dur == pytest.approx(expected)
