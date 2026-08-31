"""
Tests for config/portfolio_loader.py.

Covers: the default config loads and validates cleanly, each validation rule
(weight sum, negative face value, non-positive maturity, implausible coupon,
duplicate names, missing field) actually rejects a bad file rather than
silently accepting it, and the schema-forward-compatibility surface for a
future real-issue portfolio: optional isin/issue_date/maturity_date/
tenor_class fields, maturity_date -> maturity_years derivation against a
valuation_date, and the maturity_years/maturity_date consistency check.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from config.portfolio_loader import (
    DEFAULT_PORTFOLIO_PATH,
    Bond,
    MATURITY_CONSISTENCY_TOLERANCE_YEARS,
    WEIGHT_SUM_TOLERANCE,
    load_portfolio,
)

EXPECTED_BONDS = {
    "JGB_2Y": (2.0, 0.010, 0.15),
    "JGB_5Y": (5.0, 0.015, 0.20),
    "JGB_10Y": (10.0, 0.020, 0.25),
    "JGB_20Y": (20.0, 0.030, 0.15),
    "JGB_30Y": (30.0, 0.035, 0.15),
    "JGB_40Y": (40.0, 0.038, 0.10),
}


def _write_portfolio(tmp_path, bonds: list[dict]) -> str:
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"bonds": bonds}))
    return str(path)


def _bond(**overrides) -> dict:
    base = {
        "name": "TEST",
        "maturity_years": 10,
        "coupon_rate": 0.02,
        "face_value": 100,
        "weight": 1.0,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Default config
# --------------------------------------------------------------------------


def test_default_config_loads():
    portfolio = load_portfolio()
    assert len(portfolio) == len(EXPECTED_BONDS)
    seen = {b.name for b in portfolio}
    assert seen == set(EXPECTED_BONDS)


def test_default_config_matches_expected_fields():
    portfolio = load_portfolio()
    by_name = {b.name: b for b in portfolio}
    for name, (maturity, coupon, weight) in EXPECTED_BONDS.items():
        b = by_name[name]
        assert b.maturity_years == pytest.approx(maturity)
        assert b.coupon_rate == pytest.approx(coupon)
        assert b.weight == pytest.approx(weight)
        assert b.face_value == pytest.approx(100.0)


def test_default_config_weights_sum_to_one():
    portfolio = load_portfolio()
    assert sum(b.weight for b in portfolio) == pytest.approx(1.0, abs=WEIGHT_SUM_TOLERANCE)


def test_default_portfolio_path_points_at_real_file():
    assert DEFAULT_PORTFOLIO_PATH.exists()
    assert DEFAULT_PORTFOLIO_PATH.name == "portfolio.json"


def test_bonds_are_immutable():
    b = load_portfolio()[0]
    with pytest.raises(Exception):
        b.weight = 0.5  # type: ignore[misc]


# --------------------------------------------------------------------------
# Alternative config path
# --------------------------------------------------------------------------


def test_alternative_config_path(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [
            _bond(name="A", weight=0.6),
            _bond(name="B", weight=0.4),
        ],
    )
    portfolio = load_portfolio(alt_path)
    assert {b.name for b in portfolio} == {"A", "B"}


# --------------------------------------------------------------------------
# Validation failures
# --------------------------------------------------------------------------


def test_weights_not_summing_to_one_rejected(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(name="A", weight=0.5), _bond(name="B", weight=0.6)],
    )
    with pytest.raises(ValueError, match="sum to"):
        load_portfolio(alt_path)


def test_negative_face_value_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(face_value=-100)])
    with pytest.raises(ValueError, match="face_value"):
        load_portfolio(alt_path)


def test_zero_face_value_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(face_value=0)])
    with pytest.raises(ValueError, match="face_value"):
        load_portfolio(alt_path)


def test_non_positive_maturity_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(maturity_years=0)])
    with pytest.raises(ValueError, match="maturity_years"):
        load_portfolio(alt_path)


def test_negative_maturity_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(maturity_years=-5)])
    with pytest.raises(ValueError, match="maturity_years"):
        load_portfolio(alt_path)


def test_implausible_coupon_rate_rejected(tmp_path):
    # 2.0 meant as "2%" but left as a raw percent number -- a unit error the
    # loader must catch rather than silently pricing a 200% coupon bond.
    alt_path = _write_portfolio(tmp_path, [_bond(coupon_rate=2.0)])
    with pytest.raises(ValueError, match="coupon_rate"):
        load_portfolio(alt_path)


def test_negative_coupon_rate_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(coupon_rate=-0.01)])
    with pytest.raises(ValueError, match="coupon_rate"):
        load_portfolio(alt_path)


def test_negative_weight_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(weight=-0.1)])
    with pytest.raises(ValueError, match="weight"):
        load_portfolio(alt_path)


def test_duplicate_names_rejected(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(name="DUP", weight=0.5), _bond(name="DUP", weight=0.5)],
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_portfolio(alt_path)


def test_missing_field_rejected(tmp_path):
    bad_bond = _bond()
    del bad_bond["coupon_rate"]
    alt_path = _write_portfolio(tmp_path, [bad_bond])
    with pytest.raises(ValueError, match="missing field"):
        load_portfolio(alt_path)


def test_empty_bonds_list_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [])
    with pytest.raises(ValueError, match="bonds"):
        load_portfolio(alt_path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_portfolio(str(tmp_path / "does_not_exist.json"))


# --------------------------------------------------------------------------
# Schema forward-compatibility: optional fields absent by default
# --------------------------------------------------------------------------


def test_default_config_optional_fields_are_none():
    # The illustrative default portfolio uses maturity_years only -- none of
    # the real-issue fields should appear, and their absence must not error.
    for b in load_portfolio():
        assert b.isin is None
        assert b.issue_date is None
        assert b.maturity_date is None
        assert b.tenor_class is None


def test_optional_fields_round_trip_when_present(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [
            _bond(
                isin="JP1103571Q05",
                issue_date="2016-09-20",
                tenor_class="10Y",
                weight=1.0,
            )
        ],
    )
    b = load_portfolio(alt_path)[0]
    assert b.isin == "JP1103571Q05"
    assert b.issue_date == "2016-09-20"
    assert b.tenor_class == "10Y"


def test_empty_isin_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(isin="   ")])
    with pytest.raises(ValueError, match="isin"):
        load_portfolio(alt_path)


def test_unparseable_issue_date_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(issue_date="not-a-date")])
    with pytest.raises(ValueError, match="issue_date"):
        load_portfolio(alt_path)


def test_issue_date_on_or_after_maturity_date_rejected(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=None, maturity_date="2030-01-01", issue_date="2030-01-01")],
    )
    with pytest.raises(ValueError, match="on or after"):
        load_portfolio(alt_path)


# --------------------------------------------------------------------------
# Schema forward-compatibility: maturity_date -> maturity_years derivation
# --------------------------------------------------------------------------


def test_maturity_date_only_derives_years(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=None, maturity_date="2036-08-31", weight=1.0)],
    )
    b = load_portfolio(alt_path, valuation_date="2026-08-31")[0]

    expected_years = (date(2036, 8, 31) - date(2026, 8, 31)).days / 365.25
    assert b.maturity_years == pytest.approx(expected_years)
    assert b.maturity_years == pytest.approx(10.0, abs=0.01)
    assert b.maturity_date == "2036-08-31"


def test_maturity_date_derivation_uses_valuation_date_param(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=None, maturity_date="2030-01-01", weight=1.0)],
    )
    b_early = load_portfolio(alt_path, valuation_date="2020-01-01")[0]
    b_late = load_portfolio(alt_path, valuation_date="2025-01-01")[0]
    # Same bond, same file -- different valuation_date must yield different
    # remaining years, and the earlier valuation date must see more of them.
    assert b_early.maturity_years > b_late.maturity_years
    assert b_early.maturity_years == pytest.approx(10.0, abs=0.01)
    assert b_late.maturity_years == pytest.approx(5.0, abs=0.01)


def test_maturity_years_used_when_only_field_present():
    # Unchanged pre-existing behavior: no maturity_date at all.
    portfolio = load_portfolio()
    assert portfolio[0].maturity_years > 0
    assert portfolio[0].maturity_date is None


def test_consistent_maturity_years_and_date_accepted(tmp_path):
    # ~10 years out from a fixed valuation date, matching maturity_years=10
    # within tolerance.
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=10, maturity_date="2036-08-25", weight=1.0)],
    )
    b = load_portfolio(alt_path, valuation_date="2026-08-31")[0]
    assert b.maturity_years == pytest.approx(10.0, abs=0.1)


def test_inconsistent_maturity_years_and_date_rejected(tmp_path):
    # maturity_years says 10Y but maturity_date is ~20 years out.
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=10, maturity_date="2046-08-31", weight=1.0)],
    )
    with pytest.raises(ValueError, match="inconsistent"):
        load_portfolio(alt_path, valuation_date="2026-08-31")


def test_maturity_mismatch_within_tolerance_is_not_an_error(tmp_path):
    # A few days' worth of drift should NOT trip the consistency check.
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=10, maturity_date="2036-09-05", weight=1.0)],
    )
    b = load_portfolio(alt_path, valuation_date="2026-08-31")[0]
    derived = (date(2036, 9, 5) - date(2026, 8, 31)).days / 365.25
    assert abs(derived - 10.0) < MATURITY_CONSISTENCY_TOLERANCE_YEARS
    assert b.maturity_years == pytest.approx(derived)


def test_neither_maturity_years_nor_maturity_date_rejected(tmp_path):
    alt_path = _write_portfolio(tmp_path, [_bond(maturity_years=None)])
    with pytest.raises(ValueError, match="neither"):
        load_portfolio(alt_path)


def test_unparseable_maturity_date_rejected(tmp_path):
    alt_path = _write_portfolio(
        tmp_path, [_bond(maturity_years=None, maturity_date="31-08-2036")]
    )
    with pytest.raises(ValueError, match="maturity_date"):
        load_portfolio(alt_path)


def test_valuation_date_accepts_date_object(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=None, maturity_date="2036-08-31", weight=1.0)],
    )
    b = load_portfolio(alt_path, valuation_date=date(2026, 8, 31))[0]
    assert b.maturity_years == pytest.approx(10.0, abs=0.01)


def test_valuation_date_defaults_to_today(tmp_path):
    alt_path = _write_portfolio(
        tmp_path,
        [_bond(maturity_years=None, maturity_date="2036-08-31", weight=1.0)],
    )
    b = load_portfolio(alt_path)[0]
    expected = (date(2036, 8, 31) - date.today()).days / 365.25
    assert b.maturity_years == pytest.approx(expected)
