"""
bond_pricing.py

Curve-based bond pricing: values a bond as the sum of its cash flows, each
discounted at the yield the curve implies for THAT cash flow's own maturity
-- not one flat yield applied to the whole bond:

    Price = sum_i [ CF_i / (1 + y(t_i)/freq)^(freq * t_i) ]

y(t_i) comes from curve_yield_at(), which interpolates between the curve's
own tenor points and deliberately extrapolates FLAT beyond them (see its
docstring for why). Semiannual coupons (freq=2) by default, parameterized.

Reads the portfolio via config.portfolio_loader.load_portfolio() and the
curve via data.jgb_curve_loader.load_jgb_curve() -- this module never
hardcodes a bond list or a curve.

Cheap to call repeatedly against a modified curve: price_bond does no
per-call setup beyond building this one bond's cash flow schedule (at most
freq * 40 = 80 rows for the longest illustrative bond), and caches nothing
tied to a specific curve instance. That is deliberate: Phase 3 adds Key Rate
Duration, DV01, and an ultra-long duration profile, all computed by bumping
one tenor's yield and repricing -- the intended usage is "bump curve['yield']
at one row, call price_bond again," many times per bond, and this function
is built so that loop stays cheap without any Phase-3-specific plumbing
added now.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve


def curve_yield_at(curve: pd.DataFrame, maturity_years):
    """Interpolate (or extrapolate) curve['yield'] to an arbitrary maturity.

    maturity_years may be a scalar or an array-like of maturities; the
    return type follows numpy.interp's own (a numpy scalar or an ndarray),
    so this doubles as the vectorized lookup price_bond uses to evaluate an
    entire cash flow schedule in one call.

    WITHIN the curve's tenor range: linear interpolation between the two
    bracketing points (numpy.interp). This makes no assumption about how
    many tenors the curve has or which specific ones they are -- correct by
    construction whether the curve is the 12-point embedded snapshot or the
    15-point live/cache grid (data/jgb_curve_loader.py, Phase 1 doc §4.5);
    reindexing onto a fixed tenor set here would silently break the moment
    the curve's actual source tier changed.

    OUTSIDE that range (a cash flow shorter than the curve's shortest tenor,
    or longer than its longest): FLAT extrapolation -- the boundary yield is
    held constant, rather than linearly continuing the curve's terminal
    slope. This is a deliberate policy, not numpy.interp's incidental
    default (achieved here by passing left=/right= explicitly, so the
    behavior is spelled out rather than inherited by omission):

    - Continuing the observed slope past the last (or first) quoted tenor
      can produce an implausible or even negative yield the further out it
      is extrapolated -- e.g. continuing a steep front-end slope out to a
      50Y synthetic tenor, or a downward-sloping short end out past 1M.
      Flat extrapolation cannot do that: every extrapolated yield it
      returns is one the curve actually quoted somewhere.
    - This is not a hypothetical edge case for this project. The live/cache
      grid's shortest tenor is 1Y (no sub-year points at all -- Phase 1
      §4.5's _MOF_TENOR_COLUMNS starts at 1Y). Every semiannual bond's
      FIRST coupon cash flow lands at t=0.5, below that grid's shortest
      tenor -- so under the live/cache tier, this branch is exercised on
      every single bond in the portfolio, not just a hypothetical
      ultra-short or ultra-long one.
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
    """Price a bond by discounting each cash flow at the curve yield
    interpolated to that cash flow's own maturity (see curve_yield_at).

    Cash flow schedule: `freq` payments per year, generated BACKWARD from
    maturity_years in steps of 1/freq -- so the final payment (coupon +
    face value) lands exactly at maturity_years, and earlier payments are
    spaced 1/freq apart before it. A maturity_years that is not an exact
    multiple of 1/freq (e.g. one derived from a real maturity_date --
    config/portfolio_loader.py's SCHEMA FORWARD-COMPATIBILITY note) simply
    gets a single short front "stub" period rather than an error; this
    keeps price_bond usable unchanged once a real-issue portfolio exists,
    without a day-count-precise settlement/accrual model, which is out of
    scope here (config's docs, Phase 2A §3.3/§3.4) and for this function.

    Parameters
    ----------
    face_value : redemption amount, must be > 0.
    coupon_rate : annual coupon, decimal (0.02 == 2%). May be 0 (a
        zero-coupon bond is a valid, if degenerate, input).
    maturity_years : years to maturity, must be > 0.
    curve : a par-yield curve DataFrame with columns [maturity_years,
        yield] (yield decimal), as returned by load_jgb_curve() -- any
        tenor grid, any number of rows >= 1.
    freq : coupon payments per year (2 = semiannual, the market-standard
        JGB convention and this function's default; 1 = annual, 4 =
        quarterly, etc.).

    Returns
    -------
    float : the bond's price per `face_value` of face amount (e.g. ~100 for
        a bond priced near par on a face value of 100).
    """
    if face_value <= 0:
        raise ValueError(f"face_value must be positive, got {face_value}")
    if maturity_years <= 0:
        raise ValueError(f"maturity_years must be positive, got {maturity_years}")
    if freq <= 0:
        raise ValueError(f"freq must be positive, got {freq}")
    if curve.empty:
        raise ValueError("curve is empty -- cannot price against it")

    # max(1, ...) floors an extremely short maturity (maturity_years * freq
    # rounding to 0) at a single terminal payment, rather than an empty
    # schedule pricing to 0 -- a degenerate case, not expected in practice
    # (the shortest illustrative bond is 2Y), but the guard costs nothing.
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
    itself -- no hidden file/network I/O here, so this stays trivially
    testable with in-memory fixtures and reusable for Phase 3's bump-and-
    reprice loop (bump `curve` once, call this again). Loading is the
    caller's job: see __main__ below for the normal-usage pattern.

    Returns a DataFrame with one row per bond, in portfolio order: name,
    maturity_years, coupon_rate, weight, price. A portfolio-level weighted
    price is (df.weight * df.price).sum() -- valid directly because
    load_portfolio() already guarantees weights sum to 1.0
    (config/portfolio_loader.py's WEIGHT_SUM_TOLERANCE check).
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
