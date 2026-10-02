"""
par_error_check.py

Special Phase B: MEASURES the error from treating MOF's curve as a par
curve, instead of only bounding it (docs/phase_4_5a_documentation.md §5).

The test: if the bootstrapped zero curve were right, it would price the
real bonds MOF built its curve from at their market prices. So each JGB
in one day's JSDA file is priced off the zero curve for the same date,
using its own coupon and real coupon dates, and compared with JSDA's
quote.

Every gap is split in two (all in bp of yield, model minus market):

    total_gap_bp = coupon_effect_bp + off_curve_bp

- off_curve_bp: MOF's curve yield at the bond's maturity minus the bond's
  own market yield -- how far the bond sits off MOF's smooth curve. Not
  caused by the par assumption; it's MOF's spline and bond-specific
  richness/cheapness.
- coupon_effect_bp: the rest. What the par assumption does to a bond with
  THIS coupon. Zero for a bond whose coupon equals the curve yield (Part
  A's own self-consistency check); grows as the coupon moves away from it.

Both yields come from the same yield function here, so convention
differences between this code and JSDA's own yield formula cancel.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from models.bond_pricing import curve_yield_at
from models.bootstrap import bootstrap_zero_curve, discount_factor_at

DAYS_PER_YEAR = 365.0  # JGB accrued interest and this check's time axis: Actual/365

# MOF's methodology maps each grid year to the bond class it takes yields
# from (docs/phase_4_5a_documentation.md §1).
_GRID_CLASS = [(2, 2), (5, 5), (10, 10), (20, 20), (30, 30), (40, 40)]


def _grid_class(grid_year: float) -> int:
    for upper, maturity_class in _GRID_CLASS:
        if grid_year <= upper:
            return maturity_class
    raise ValueError(f"grid year {grid_year} beyond MOF's 40Y grid")


def _add_months(day: date, months: int, coupon_day: int) -> date:
    total = day.year * 12 + (day.month - 1) + months
    return date(total // 12, total % 12 + 1, coupon_day)


def coupon_dates(maturity_date: date, coupon_day: int, settlement: date) -> tuple[date, list[date]]:
    """(last coupon date on or before settlement, remaining coupon dates
    after settlement up to and including maturity), stepping back from
    maturity six months at a time."""
    remaining = []
    current = maturity_date
    while current > settlement:
        remaining.append(current)
        current = _add_months(current, -6, coupon_day)
    return current, remaining[::-1]


def _cash_flows(bond: pd.Series, settlement: date) -> tuple[date, list[date], np.ndarray]:
    last, dates = coupon_dates(bond["maturity_date"], bond["coupon_day"], settlement)
    flows = np.full(len(dates), bond["coupon"] * 100.0 / 2.0)
    flows[-1] += 100.0
    return last, dates, flows


def accrued(bond: pd.Series, settlement: date) -> float:
    last, _, _ = _cash_flows(bond, settlement)
    return bond["coupon"] * 100.0 * (settlement - last).days / DAYS_PER_YEAR


def compound_yield(bond: pd.Series, clean_price: float, settlement: date) -> float:
    """Semiannual-compound yield to maturity for a clean price, solved by
    bisection (price falls as yield rises, so the root is unique)."""
    _, dates, flows = _cash_flows(bond, settlement)
    times = np.array([(d - settlement).days / DAYS_PER_YEAR for d in dates])
    target = clean_price + accrued(bond, settlement)
    low, high = -0.05, 0.30
    for _ in range(200):
        mid = (low + high) / 2.0
        if np.sum(flows / (1.0 + mid / 2.0) ** (2.0 * times)) > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def price_on_zero_curve(bond: pd.Series, zero_curve: pd.DataFrame, curve_date: date, settlement: date) -> float:
    """Clean price at settlement, discounting each real cash flow on the
    zero curve (time measured from curve_date) and rolling forward to
    settlement."""
    _, dates, flows = _cash_flows(bond, settlement)
    times = np.array([(d - curve_date).days / DAYS_PER_YEAR for d in dates])
    settle_time = (settlement - curve_date).days / DAYS_PER_YEAR
    dirty = np.sum(flows * discount_factor_at(zero_curve, times)) / discount_factor_at(zero_curve, settle_time)
    return float(dirty - accrued(bond, settlement))


def mof_selected_issues(jgbs: pd.DataFrame, curve_date: date, grid_years) -> pd.DataFrame:
    """The bonds MOF's methodology would use at each grid year: the class's
    newest issue, plus the issues maturing nearest the grid year on each
    side. One row per (grid_year, issue); `role` says why it was picked."""
    remaining = jgbs["maturity_date"].map(lambda d: (d - curve_date).days / DAYS_PER_YEAR)
    picks = []
    for grid in grid_years:
        in_class = jgbs.loc[jgbs["maturity_class"] == _grid_class(grid)]
        years = remaining.loc[in_class.index]
        chosen = {in_class["issue_number"].idxmax(): "on-the-run"}
        below, above = years.loc[years <= grid], years.loc[years > grid]
        if not below.empty:
            chosen.setdefault(below.idxmax(), "nearest below")
        if not above.empty:
            chosen.setdefault(above.idxmin(), "nearest above")
        picks += [{"grid_year": grid, "row": i, "role": role} for i, role in chosen.items()]
    picks = pd.DataFrame(picks)
    return picks.join(jgbs, on="row").drop(columns="row")


def measure_par_error(
    par_curve: pd.DataFrame, jgbs: pd.DataFrame, curve_date: date, settlement: date
) -> pd.DataFrame:
    """Per-bond gaps for every JGB maturing between 1Y and the curve's
    longest tenor. Columns add: years, model_price, price_gap (model -
    market, per 100), market_yield, model_yield, total_gap_bp,
    off_curve_bp, coupon_effect_bp, coupon_minus_yield_bp."""
    zero_curve = bootstrap_zero_curve(par_curve)
    longest = float(par_curve["maturity_years"].max())
    out = jgbs.copy()
    out["years"] = out["maturity_date"].map(lambda d: (d - curve_date).days / DAYS_PER_YEAR)
    out = out.loc[(out["years"] >= 1.0) & (out["years"] <= longest)].copy()

    out["model_price"] = [price_on_zero_curve(b, zero_curve, curve_date, settlement) for _, b in out.iterrows()]
    out["price_gap"] = out["model_price"] - out["clean_price"]
    out["market_yield"] = [compound_yield(b, b["clean_price"], settlement) for _, b in out.iterrows()]
    out["model_yield"] = [compound_yield(b, b["model_price"], settlement) for _, b in out.iterrows()]
    mof_yield = curve_yield_at(par_curve, out["years"].to_numpy())
    out["total_gap_bp"] = (out["model_yield"] - out["market_yield"]) * 10000.0
    out["off_curve_bp"] = (mof_yield - out["market_yield"]) * 10000.0
    out["coupon_effect_bp"] = out["total_gap_bp"] - out["off_curve_bp"]
    out["coupon_minus_yield_bp"] = (out["coupon"] - out["market_yield"]) * 10000.0
    return out
