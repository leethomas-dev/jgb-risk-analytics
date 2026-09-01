"""
Tests for models/dv01.py.

Covers: the DV01 formula against a direct one-sided bump-and-reprice (the
Part B requirement -- a real, independent check, not a tautology, since
the formula uses effective_duration_bond's CENTRAL difference while the
direct check uses a genuinely one-sided bump); currency units (DV01 must
match price_bond's own "currency per 100 face" convention, never a raw
percentage); per-tenor DV01 as KRD rescaled (sign, shape, and consistency
with dv01_bond); and portfolio-level aggregation, both per-bond (no baked-
in total row, mirroring price_portfolio) and per-tenor (baked-in total
row, mirroring key_rate_duration_portfolio).

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.portfolio_loader import load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_bond
from models.dv01 import (
    dv01_bond,
    dv01_by_tenor_bond,
    dv01_by_tenor_portfolio,
    dv01_portfolio,
)
from models.key_rate_duration import DEFAULT_BUMP_SIZE, effective_duration_bond


def _flat_curve(y: float, tenors=(1, 2, 5, 10, 20, 30, 40)) -> pd.DataFrame:
    return pd.DataFrame({"maturity_years": list(tenors), "yield": [y] * len(tenors)})


# --------------------------------------------------------------------------
# The Part B requirement: formula-DV01 vs. a direct 1bp bump-and-reprice
# --------------------------------------------------------------------------


def _direct_bump_dv01(face_value, coupon_rate, maturity_years, curve, freq=2, bump_size=DEFAULT_BUMP_SIZE):
    """Independent reference DV01: price now, minus price after a genuine
    one-sided +bump_size parallel shift -- computed without calling
    dv01_bond, effective_duration_bond, or key_rate_duration_bond, so this
    is a real check against the formula, not a restatement of it."""
    price_base = price_bond(face_value, coupon_rate, maturity_years, curve, freq=freq)
    bumped = curve.copy()
    bumped["yield"] = bumped["yield"] + bump_size
    price_up = price_bond(face_value, coupon_rate, maturity_years, bumped, freq=freq)
    return price_base - price_up


def test_dv01_formula_matches_direct_bump_for_default_portfolio():
    # rel=5e-3 (0.5%) -- comfortably above the largest observed gap (~0.15%
    # at 40Y, docs/phase_3b_documentation.md §2) but still a real check:
    # a sign error, a units error (e.g. forgetting to multiply by
    # bump_size), or a duration bug would blow through this by orders of
    # magnitude, not fractions of a percent.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    for bond in portfolio:
        formula = dv01_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        direct = _direct_bump_dv01(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert formula == pytest.approx(direct, rel=5e-3), (
            f"{bond.name}: formula={formula} vs direct={direct}"
        )


def test_dv01_formula_slightly_exceeds_direct_bump_due_to_convexity():
    # Not just "close" -- specifically on the expected SIDE of direct: the
    # duration-only formula ignores convexity, which cushions the real
    # price drop from a yield increase, so formula-DV01 should be a touch
    # LARGER than the true bumped price change, not smaller or random.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    for bond in portfolio:
        formula = dv01_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        direct = _direct_bump_dv01(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert formula > direct


def test_dv01_gap_grows_with_maturity_like_convexity_does():
    # Convexity grows with maturity, so the formula/direct gap should too
    # -- same qualitative pattern as the KRD-sum/effective-duration gap in
    # Phase 3A (docs/phase_3a_documentation.md §2).
    curve = load_jgb_curve(prefer_live=False)
    short = dv01_bond(100.0, 0.01, 2.0, curve) - _direct_bump_dv01(100.0, 0.01, 2.0, curve)
    long = dv01_bond(100.0, 0.038, 40.0, curve) - _direct_bump_dv01(100.0, 0.038, 40.0, curve)
    assert long > short


# --------------------------------------------------------------------------
# Currency units: DV01 must live in price_bond's own units, not percentage
# --------------------------------------------------------------------------


def test_dv01_bond_matches_price_times_duration_times_bump_size():
    curve = _flat_curve(0.02)
    price = price_bond(100.0, 0.03, 10.0, curve)
    duration = effective_duration_bond(100.0, 0.03, 10.0, curve)
    expected = price * duration * DEFAULT_BUMP_SIZE
    assert dv01_bond(100.0, 0.03, 10.0, curve) == pytest.approx(expected)


def test_dv01_is_currency_scaled_not_a_bare_percentage():
    # A sanity magnitude check: DV01 (currency per 100 face, for a 1bp
    # move) should be a small fraction of price -- roughly
    # price * duration * 0.0001 -- not confusable with a duration (years,
    # order 1-40) or a KRD (dimensionless-ish, order 0-1) if a units bug
    # dropped the bump_size or the price multiplication.
    curve = _flat_curve(0.02)
    price = price_bond(100.0, 0.03, 10.0, curve)
    dv01 = dv01_bond(100.0, 0.03, 10.0, curve)
    assert 0.0 < dv01 < price  # a 1bp move can't be worth more than the whole price


def test_dv01_positive_for_a_normal_long_only_bond():
    curve = _flat_curve(0.02)
    assert dv01_bond(100.0, 0.03, 10.0, curve) > 0.0


# --------------------------------------------------------------------------
# Per-tenor DV01 (the hedge-ratio table)
# --------------------------------------------------------------------------


def test_dv01_by_tenor_bond_matches_krd_rescaled():
    curve = load_jgb_curve(prefer_live=False)
    from models.key_rate_duration import key_rate_duration_bond

    price = price_bond(100.0, 0.02, 10.0, curve)
    krd = key_rate_duration_bond(100.0, 0.02, 10.0, curve)
    expected = price * krd * DEFAULT_BUMP_SIZE

    dv01_by_tenor = dv01_by_tenor_bond(100.0, 0.02, 10.0, curve)
    np.testing.assert_allclose(dv01_by_tenor.to_numpy(), expected.to_numpy())


def test_dv01_by_tenor_bond_indexed_by_curves_own_tenors():
    curve = load_jgb_curve(prefer_live=False)
    dv01_by_tenor = dv01_by_tenor_bond(100.0, 0.02, 10.0, curve)
    assert list(dv01_by_tenor.index) == sorted(curve["maturity_years"].tolist())


def test_sum_of_dv01_by_tenor_approximates_dv01_bond():
    # Same relationship Phase 3A validates for KRD/effective-duration
    # (docs/phase_3a_documentation.md §2) -- inherited here because DV01
    # is KRD rescaled by the same two constants at every tenor, so no new
    # error is introduced by the rescaling.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    for bond in portfolio:
        dv01_by_tenor = dv01_by_tenor_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        total = dv01_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert dv01_by_tenor.sum() == pytest.approx(total, rel=1e-5)


# --------------------------------------------------------------------------
# Portfolio-level aggregation
# --------------------------------------------------------------------------


def test_dv01_portfolio_returns_one_row_per_bond_no_total_row():
    # Mirrors price_portfolio's shape (no baked-in total row) -- contrast
    # with dv01_by_tenor_portfolio below, which mirrors
    # key_rate_duration_portfolio (baked-in total row) instead.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    df = dv01_portfolio(portfolio, curve)
    assert list(df["name"]) == [b.name for b in portfolio]
    assert "portfolio_total" not in df["name"].values


def test_dv01_portfolio_columns():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    df = dv01_portfolio(portfolio, curve)
    assert list(df.columns) == [
        "name", "maturity_years", "coupon_rate", "weight", "price", "modified_duration", "dv01",
    ]


def test_dv01_portfolio_matches_dv01_bond_per_row():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    df = dv01_portfolio(portfolio, curve)
    for bond, dv01 in zip(portfolio, df["dv01"]):
        expected = dv01_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        assert dv01 == pytest.approx(expected)


def test_dv01_portfolio_weighted_total_is_computable_and_plausible():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    df = dv01_portfolio(portfolio, curve)
    total = float((df["weight"] * df["dv01"]).sum())
    assert 0.0 < total < 1.0  # small fraction of a ~100-per-100-face portfolio, for a 1bp move


def test_dv01_by_tenor_portfolio_rows_are_bond_names_plus_total():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = dv01_by_tenor_portfolio(portfolio, curve)
    assert list(table.index) == [b.name for b in portfolio] + ["portfolio_total"]


def test_dv01_by_tenor_portfolio_columns_match_curve_tenors():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = dv01_by_tenor_portfolio(portfolio, curve)
    assert list(table.columns) == sorted(curve["maturity_years"].tolist())


def test_dv01_by_tenor_portfolio_total_row_is_weight_weighted_sum():
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()
    table = dv01_by_tenor_portfolio(portfolio, curve)

    expected_total = np.zeros(len(table.columns))
    for bond in portfolio:
        expected_total += bond.weight * table.loc[bond.name].to_numpy()

    np.testing.assert_allclose(table.loc["portfolio_total"].to_numpy(), expected_total)


def test_dv01_by_tenor_portfolio_total_matches_dv01_portfolio_weighted_total():
    # The two portfolio-level views (per-bond dv01_portfolio and per-tenor
    # dv01_by_tenor_portfolio) should agree once fully aggregated -- same
    # underlying numbers, two different breakdowns.
    curve = load_jgb_curve(prefer_live=False)
    portfolio = load_portfolio()

    per_bond = dv01_portfolio(portfolio, curve)
    weighted_total = float((per_bond["weight"] * per_bond["dv01"]).sum())

    by_tenor = dv01_by_tenor_portfolio(portfolio, curve)
    tenor_total = float(by_tenor.loc["portfolio_total"].sum())

    assert weighted_total == pytest.approx(tenor_total, rel=1e-5)
