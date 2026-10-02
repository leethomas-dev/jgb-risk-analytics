"""
Tests for data/jsda_reference_loader.py and models/par_error_check.py
(Special Phase B).

Unit tests use a tiny synthetic JSDA-format file and synthetic bonds, so
they run anywhere. The real-data test needs a hand-downloaded
data/jsda/S260901.csv (gitignored -- JSDA's terms forbid redistribution)
and is skipped without it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_loader import load_jgb_curve
from data.jsda_reference_loader import JSDA_DIR, load_jsda_jgbs
from models.par_error_check import (
    accrued,
    compound_yield,
    coupon_dates,
    measure_par_error,
    mof_selected_issues,
    price_on_zero_curve,
)

CURVE_DATE = date(2026, 8, 31)
SETTLEMENT = date(2026, 9, 1)


def _row(category, code, name, maturity, coupon, compound, price, simple):
    # 29 columns, the JSDA layout; only the ones the loader reads matter.
    fields = ["20260901", category, code, f'"{name}"', maturity, coupon, compound, price, "0.00",
              "03/09", "20", "0", "0", "0", simple] + ["0"] * 14
    return ",".join(fields)


def _write_jsda_file(tmp_path: Path) -> Path:
    lines = [
        _row("01", "013850074", "国庫短期証券1385", "20260907", "99.999", "999.999", "99.98", "0.980"),
        _row("02", "003830067", "長期国債 383", "20360620", "2.7", "2.915", "98.17", "2.940"),
        _row("02", "001870045", "中期国債 187(5)", "20310620", "2.2", "2.206", "99.97", "2.205"),
        _row("02", "000190054", "超長期国債(40)19", "20660320", "3.8", "4.103", "94.08", "4.198"),
        _row("02", "001970069", "超長期国債 197", "20460620", "3.6", "3.700", "98.59", "3.720"),
        _row("02", "000090037", "長期国債 WI-09", "20360620", "99.999", "2.916", "999.99", "999.999"),
        _row("02", "000010099", "GX長期国債1", "20340620", "1.0", "2.800", "90.00", "2.900"),
        _row("10", "007590001", "東京都債 759", "20300620", "1.0", "1.500", "98.00", "1.600"),
    ]
    path = tmp_path / "S260901.csv"
    path.write_bytes("\n".join(lines).encode("shift_jis"))
    return path


def _bond(coupon, maturity_date, clean_price=100.0):
    return pd.Series({
        "issue_code": "x", "name": "x", "maturity_class": 30, "issue_number": 1,
        "maturity_date": maturity_date, "coupon": coupon, "clean_price": clean_price,
        "compound_yield": np.nan, "simple_yield": np.nan, "coupon_months": (3, 9), "coupon_day": 20,
    })


def _flat_zero_curve(rate):
    tenors = np.linspace(0.5, 40.0, 80)
    return pd.DataFrame({"maturity_years": tenors, "zero_rate": np.full(len(tenors), rate)})


def _clean_price_at_yield(bond, y, settlement):
    _, dates = coupon_dates(bond["maturity_date"], bond["coupon_day"], settlement)
    times = np.array([(d - settlement).days / 365.0 for d in dates])
    flows = np.full(len(dates), bond["coupon"] * 50.0)
    flows[-1] += 100.0
    return float(np.sum(flows / (1 + y / 2) ** (2 * times))) - accrued(bond, settlement)


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------


def test_loader_keeps_only_fixed_coupon_jgbs(tmp_path):
    jgbs = load_jsda_jgbs(_write_jsda_file(tmp_path))
    assert set(jgbs["name"]) == {"長期国債 383", "中期国債 187(5)", "超長期国債(40)19", "超長期国債 197"}


def test_loader_reads_class_issue_number_and_units(tmp_path):
    jgbs = load_jsda_jgbs(_write_jsda_file(tmp_path)).set_index("name")
    assert jgbs.loc["中期国債 187(5)", "maturity_class"] == 5
    assert jgbs.loc["超長期国債(40)19", "maturity_class"] == 40
    assert jgbs.loc["超長期国債 197", "maturity_class"] == 20
    assert jgbs.loc["長期国債 383", "issue_number"] == 383
    assert jgbs.loc["長期国債 383", "coupon"] == pytest.approx(0.027)
    assert jgbs.loc["長期国債 383", "compound_yield"] == pytest.approx(0.02915)
    assert jgbs.loc["長期国債 383", "clean_price"] == pytest.approx(98.17)
    assert jgbs.loc["長期国債 383", "maturity_date"] == date(2036, 6, 20)


# --------------------------------------------------------------------------
# Cash flows, yields and pricing
# --------------------------------------------------------------------------


def test_coupon_dates_use_the_real_schedule():
    last, dates = coupon_dates(date(2036, 6, 20), 20, SETTLEMENT)
    assert last == date(2026, 6, 20)
    assert dates[0] == date(2026, 12, 20)
    assert dates[-1] == date(2036, 6, 20)
    assert len(dates) == 20


def test_yield_round_trips_through_price():
    bond = _bond(0.007, date(2051, 3, 20))
    price = _clean_price_at_yield(bond, 0.041, SETTLEMENT)
    assert compound_yield(bond, price, SETTLEMENT) == pytest.approx(0.041, abs=1e-9)


def test_flat_zero_curve_gives_every_coupon_the_same_yield():
    # No coupon effect on a flat curve: the method itself adds none.
    curve = _flat_zero_curve(0.03)
    for coupon in (0.001, 0.03, 0.06):
        bond = _bond(coupon, date(2051, 3, 20))
        model_price = price_on_zero_curve(bond, curve, SETTLEMENT, SETTLEMENT)
        assert compound_yield(bond, model_price, SETTLEMENT) == pytest.approx(0.03, abs=1e-6)


def test_upward_curve_low_coupon_bond_shows_a_coupon_effect_par_bond_does_not():
    # Market yield set exactly on the par curve: a near-par bond should
    # show ~no gap, a deep-discount bond a clearly positive one.
    par_curve = pd.DataFrame({
        "maturity_years": [1.0, 5.0, 10.0, 20.0, 30.0],
        "yield": [0.01, 0.015, 0.025, 0.035, 0.04],
    })
    maturity = date(2051, 9, 20)  # ~25Y
    years = (maturity - CURVE_DATE).days / 365.0
    curve_yield = float(np.interp(years, par_curve["maturity_years"], par_curve["yield"]))

    bonds = []
    for coupon in (curve_yield, 0.005):
        bond = _bond(coupon, maturity)
        bond["clean_price"] = _clean_price_at_yield(bond, curve_yield, SETTLEMENT)
        bonds.append(bond)
    result = measure_par_error(par_curve, pd.DataFrame(bonds), CURVE_DATE, SETTLEMENT)

    near_par, discount = result.iloc[0], result.iloc[1]
    assert abs(near_par["coupon_effect_bp"]) < 1.0
    assert discount["coupon_effect_bp"] > 5.0
    assert abs(discount["off_curve_bp"]) < 0.1  # sits on the curve by construction


def test_selected_issues_include_the_newest_issue_in_the_grid_class(tmp_path):
    jgbs = load_jsda_jgbs(_write_jsda_file(tmp_path))
    picks = mof_selected_issues(jgbs, CURVE_DATE, [10.0, 40.0])
    on_the_run = picks.loc[picks["role"] == "on-the-run"].set_index("grid_year")["name"]
    assert on_the_run.loc[10.0] == "長期国債 383"
    assert on_the_run.loc[40.0] == "超長期国債(40)19"


# --------------------------------------------------------------------------
# Real data (2026-08-31 quotes), skipped without the hand-downloaded file
# --------------------------------------------------------------------------

REAL_FILE = JSDA_DIR / "S260901.csv"


@pytest.mark.skipif(not REAL_FILE.exists(), reason="data/jsda/S260901.csv not downloaded (gitignored)")
def test_measured_par_error_on_real_jsda_quotes():
    # Pinned in docs/special_phase_b_documentation.md §3.
    curve = load_jgb_curve(prefer_live=False, verbose=False)
    jgbs = load_jsda_jgbs(REAL_FILE)
    result = measure_par_error(curve, jgbs, CURVE_DATE, SETTLEMENT)

    # This code's yields reproduce JSDA's own compound yields.
    assert ((result["market_yield"] - result["compound_yield"]).abs() * 10000).median() < 0.5
    # MOF's curve runs through the bonds' yields...
    assert result["off_curve_bp"].abs().median() < 1.5
    # ...near-par bonds are priced right, deep-discount ones are not.
    near_par = result.loc[result["clean_price"].between(98, 102)]
    assert near_par["coupon_effect_bp"].abs().max() < 1.0
    picks = mof_selected_issues(jgbs, CURVE_DATE, [25.0]).merge(
        result[["issue_code", "total_gap_bp"]], on="issue_code"
    )
    assert picks.loc[picks["role"] != "on-the-run", "total_gap_bp"].min() > 30.0
