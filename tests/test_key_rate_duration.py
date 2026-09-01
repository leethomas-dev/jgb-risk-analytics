"""
Tests for models/key_rate_duration.py.

Covers: single-bond KRD shape and indexing against the runtime curve grid;
the triangular/tent bump shape (a tenor far from a bond's cash flows gets
~zero KRD; a tenor exactly at a bond's only cash-flow cluster gets ~all of
it); the sum-of-KRDs-approximates-effective-duration sanity check called
out explicitly in the Phase 3 requirements; portfolio-level aggregation
(shape, weighting, and the portfolio_total row); and input validation.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_bond
from models.key_rate_duration import (
    DEFAULT_BUMP_SIZE,
    effective_duration_bond,
    key_rate_duration_bond,
    key_rate_duration_portfolio,
)


def _flat_curve(y: float, tenors=(1, 2, 5, 10, 20, 30, 40)) -> pd.DataFrame:
    return pd.DataFrame({"maturity_years": list(tenors), "yield": [y] * len(tenors)})


# --------------------------------------------------------------------------
# Single-bond KRD: shape, indexing, sign
# --------------------------------------------------------------------------


def test_krd_series_indexed_by_the_curves_own_tenors():
    curve = load_jgb_curve(prefer_live=False)
    krd = key_rate_duration_bond(100.0, 0.02, 10.0, curve)
    assert list(krd.index) == sorted(curve["maturity_years"].tolist())


def test_krd_series_length_matches_curve_grid_size_not_a_fixed_count():
    # Same curve, two different grid sizes (the snapshot's 12 vs. a 7-point
    # flat fixture) -- KRD output length must track the grid, not a
    # hardcoded tenor count.
    snapshot_curve = load_jgb_curve(prefer_live=False)
    krd_snapshot = key_rate_duration_bond(100.0, 0.02, 10.0, snapshot_curve)
    assert len(krd_snapshot) == len(snapshot_curve)

    small_curve = _flat_curve(0.02)
    krd_small = key_rate_duration_bond(100.0, 0.02, 10.0, small_curve)
    assert len(krd_small) == len(small_curve)


def test_krds_are_positive_like_ordinary_duration():
    # Higher yield -> lower price, so -(P_up - P_down)/(2*P*bump) should be
    # positive at every tenor with any cash-flow exposure near it.
    curve = _flat_curve(0.02)
    krd = key_rate_duration_bond(100.0, 0.02, 10.0, curve)
    assert (krd >= 0).all()
    assert krd.sum() > 0


# --------------------------------------------------------------------------
# Triangular bump shape (the Part A design decision, tested directly)
# --------------------------------------------------------------------------


def test_tenor_far_from_bonds_cash_flows_has_near_zero_krd():
    # A 2Y bond's cash flows all land at or before t=2. A tent centered at
    # 30Y has support only on [20, 40] (its immediate grid neighbors) --
    # nowhere near any of this bond's cash-flow times -- so bumping it
    # should leave the price, and therefore the KRD at 30Y, ~unchanged.
    curve = _flat_curve(0.02, tenors=(1, 2, 5, 10, 20, 30, 40))
    krd = key_rate_duration_bond(100.0, 0.02, 2.0, curve)
    assert krd.loc[30.0] == pytest.approx(0.0, abs=1e-8)
    assert krd.loc[40.0] == pytest.approx(0.0, abs=1e-8)


def test_zero_coupon_bonds_single_cash_flow_krd_concentrates_at_neighbors():
    # A zero-coupon bond has exactly one cash flow, at maturity. If that
    # maturity sits exactly on a grid tenor, only that tenor's tent (weight
    # 1 there) touches it -- every other tenor's tent is 0 at that exact
    # point -- so the entire KRD should concentrate on that one tenor.
    curve = _flat_curve(0.02, tenors=(1, 2, 5, 10, 20, 30, 40))
    krd = key_rate_duration_bond(100.0, 0.0, 10.0, curve)
    assert krd.loc[10.0] == pytest.approx(krd.sum(), rel=1e-6)
    other_tenors = [t for t in krd.index if t != 10.0]
    assert krd.loc[other_tenors].abs().max() == pytest.approx(0.0, abs=1e-8)


def test_endpoint_tenor_bump_affects_maturities_beyond_the_grid_end():
    # curve_yield_at extrapolates flat beyond the curve's longest tenor
    # using that tenor's own yield -- so bumping the 40Y row must move the
    # yield (and therefore the price) of a cash flow at, say, t=45, exactly
    # as much as it moves the yield at t=40 itself (module docstring: the
    # endpoint tent's outer side is flat, not sloped-to-zero).
    curve = _flat_curve(0.02, tenors=(1, 2, 5, 10, 20, 30, 40))
    # A 45Y zero-coupon "bond" (synthetic, to isolate one cash flow beyond
    # the grid) should have all of its KRD at the 40Y tenor.
    krd = key_rate_duration_bond(100.0, 0.0, 45.0, curve)
    assert krd.loc[40.0] == pytest.approx(krd.sum(), rel=1e-6)


# --------------------------------------------------------------------------
# Sanity check: sum of KRDs approximates effective duration
# --------------------------------------------------------------------------


def test_sum_of_krds_approximates_effective_duration():
    # Central differencing (both functions) cancels the dominant
    # first-order truncation error a one-sided formula would carry here --
    # the observed gap is ~1e-7 relative or tighter (docs/phase_3a_documentation.md
    # §2), so rel=1e-5 is a meaningful check, not just a loose bound.
    curve = load_jgb_curve(prefer_live=False)
    for maturity, coupon in [(2.0, 0.01), (10.0, 0.02), (30.0, 0.035), (40.0, 0.038)]:
        krd = key_rate_duration_bond(100.0, coupon, maturity, curve)
        eff_dur = effective_duration_bond(100.0, coupon, maturity, curve)
        assert krd.sum() == pytest.approx(eff_dur, rel=1e-5), (
            f"maturity={maturity}: sum(KRD)={krd.sum()} vs effective_duration={eff_dur}"
        )


def test_sum_of_krds_approximates_effective_duration_for_default_portfolio():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    for bond in portfolio:
        krd = key_rate_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        eff_dur = effective_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert krd.sum() == pytest.approx(eff_dur, rel=1e-5)


# --------------------------------------------------------------------------
# effective_duration_bond sanity
# --------------------------------------------------------------------------


def test_effective_duration_roughly_matches_maturity_for_a_low_coupon_long_bond():
    # Loose sanity bound, not a precise pin: duration is always somewhat
    # below maturity, and increasingly so as coupon rises. Catches a sign
    # error or gross scaling bug without hardcoding an exact figure.
    curve = _flat_curve(0.02)
    eff_dur = effective_duration_bond(100.0, 0.005, 20.0, curve)
    assert 0.0 < eff_dur < 20.0


# --------------------------------------------------------------------------
# Portfolio-level KRD
# --------------------------------------------------------------------------


def test_portfolio_krd_rows_are_bond_names_plus_total():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = key_rate_duration_portfolio(portfolio, curve)
    assert list(table.index) == [b.name for b in portfolio] + ["portfolio_total"]


def test_portfolio_krd_columns_match_curve_tenors():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = key_rate_duration_portfolio(portfolio, curve)
    assert list(table.columns) == sorted(curve["maturity_years"].tolist())


def test_portfolio_total_row_is_weight_weighted_sum_of_bond_rows():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = key_rate_duration_portfolio(portfolio, curve)

    expected_total = np.zeros(len(table.columns))
    for bond in portfolio:
        expected_total += bond.weight * table.loc[bond.name].to_numpy()

    np.testing.assert_allclose(table.loc["portfolio_total"].to_numpy(), expected_total)


def test_portfolio_krd_matches_single_bond_krd_per_row():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = key_rate_duration_portfolio(portfolio, curve)
    for bond in portfolio:
        expected = key_rate_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        np.testing.assert_allclose(table.loc[bond.name].to_numpy(), expected.to_numpy())


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_non_positive_bump_size_rejected_on_bond_krd():
    curve = _flat_curve(0.02)
    with pytest.raises(ValueError, match="bump_size"):
        key_rate_duration_bond(100.0, 0.02, 10.0, curve, bump_size=0.0)


def test_non_positive_bump_size_rejected_on_effective_duration():
    curve = _flat_curve(0.02)
    with pytest.raises(ValueError, match="bump_size"):
        effective_duration_bond(100.0, 0.02, 10.0, curve, bump_size=-0.0001)


def test_default_bump_size_is_one_basis_point():
    assert DEFAULT_BUMP_SIZE == pytest.approx(0.0001)
