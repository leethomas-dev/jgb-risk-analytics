"""
bond_pricing.py

Curve-based bond pricing: values a bond as the sum of its cash flows, each
discounted at the yield the curve implies for THAT cash flow's own maturity
-- not one flat yield applied to the whole bond:

    Price = sum_i [ CF_i / (1 + y(t_i)/freq)^(freq * t_i) ]

y(t_i) comes from curve_yield_at(), which interpolates between the curve's
own tenor points and extrapolates FLAT beyond them (see its docstring).
Semiannual coupons (freq=2) by default, parameterized.

Reads the portfolio and curve through their own loaders -- never hardcodes
either. Cheap to call repeatedly against a modified curve on purpose:
KRD, DV01, and the ultra-long profile all work by bumping one curve row
and calling price_bond again, many times per bond, so this function does
no per-call setup beyond building one bond's cash flow schedule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve


def curve_yield_at(curve: pd.DataFrame, maturity_years):
    """Interpolate (or extrapolate) curve['yield'] to an arbitrary maturity.

    maturity_years may be a scalar or an array-like; price_bond calls this
    once with a whole cash-flow-time array rather than once per cash flow.

    WITHIN the curve's range: straight-line interpolation between the two
    surrounding points. Makes no assumption about how many tenors the
    curve has or which ones -- reindexing onto a fixed tenor set would
    silently break the moment the curve's source changed.

    OUTSIDE that range: FLAT extrapolation -- the nearest known yield is
    held constant rather than continuing the curve's slope, which could
    otherwise produce an implausible or negative rate. Not a hypothetical
    edge case here: the live/cached curve's shortest tenor is 1 year, but
    every semiannual bond's first coupon lands at 0.5 years -- so this
    branch fires on the very first cash flow of every bond whenever that
    data source is used.
    """
    curve_sorted = curve.sort_values("maturity_years")
    tenors = curve_sorted["maturity_years"].to_numpy()
    yields = curve_sorted["yield"].to_numpy()
    return np.interp(maturity_years, tenors, yields, left=yields[0], right=yields[-1])


def price_bond(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
) -> float:
    """Price a bond by discounting each cash flow at the curve yield for
    that cash flow's own maturity (see curve_yield_at).

    Cash flow schedule: `freq` payments per year, built BACKWARD from
    maturity_years so the final payment lands exactly there. A maturity
    that isn't an exact multiple of 1/freq (e.g. derived from a real
    redemption date) just gets one shorter first period, not an error.

    Parameters
    ----------
    face_value : redemption amount, must be > 0.
    coupon_rate : annual coupon, decimal (0.02 == 2%). May be 0.
    maturity_years : years to maturity, must be > 0.
    curve : a par-yield curve DataFrame with columns [maturity_years,
        yield] (yield decimal), any tenor grid, any number of rows >= 1.
    freq : coupon payments per year (2 = semiannual, the JGB default).

    Returns
    -------
    float : price per `face_value` of face amount (~100 for a bond
        priced near par on a face value of 100).
    """
    if face_value <= 0:
        raise ValueError(f"face_value must be positive, got {face_value}")
    if maturity_years <= 0:
        raise ValueError(f"maturity_years must be positive, got {maturity_years}")
    if freq <= 0:
        raise ValueError(f"freq must be positive, got {freq}")
    if curve.empty:
        raise ValueError("curve is empty -- cannot price against it")

    # max(1, ...) guards an extremely short maturity from producing an
    # empty schedule (pricing to 0) instead of one terminal payment.
    n_periods = max(1, round(maturity_years * freq))
    period_index = np.arange(1, n_periods + 1)
    cash_flow_times = maturity_years - (n_periods - period_index) / freq

    coupon_payment = face_value * coupon_rate / freq
    cash_flows = np.full(n_periods, coupon_payment, dtype=float)
    cash_flows[-1] += face_value  # final period also redeems face value

    yields_at_flows = curve_yield_at(curve, cash_flow_times)
    discount_factors = (1.0 + yields_at_flows / freq) ** (freq * cash_flow_times)
    return float(np.sum(cash_flows / discount_factors))


def price_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int = 2,
) -> pd.DataFrame:
    """Price every bond in `portfolio` against one `curve`.

    Takes an already-loaded portfolio and curve rather than loading them
    itself -- no hidden file/network I/O, so this is easy to test and
    reusable against a curve a caller repeatedly bumps and reprices.

    Returns a DataFrame with one row per bond, in portfolio order: name,
    maturity_years, coupon_rate, weight, price. A portfolio-level weighted
    price is (df.weight * df.price).sum() -- valid directly because
    load_portfolio() already guarantees weights sum to 1.0.
    """
    rows = [
        {
            "name": bond.name,
            "maturity_years": bond.maturity_years,
            "coupon_rate": bond.coupon_rate,
            "weight": bond.weight,
            "price": price_bond(
                bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq
            ),
        }
        for bond in portfolio
    ]
    return pd.DataFrame(rows, columns=["name", "maturity_years", "coupon_rate", "weight", "price"])


if __name__ == "__main__":
    curve = load_jgb_curve()
    portfolio = load_portfolio()
    results = price_portfolio(portfolio, curve)

    print()
    print("Bond pricing (per 100 face value):")
    print(
        results.to_string(
            index=False,
            formatters={
                "coupon_rate": lambda c: f"{c:.3%}",
                "weight": lambda w: f"{w:.2%}",
                "price": lambda p: f"{p:.4f}",
            },
        )
    )

    portfolio_price = float((results["weight"] * results["price"]).sum())
    print()
    print(f"Portfolio-level weighted price (per 100 face value): {portfolio_price:.4f}")
