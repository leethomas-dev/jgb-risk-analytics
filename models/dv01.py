"""
dv01.py

DV01 ("dollar value of 01"): currency-terms price sensitivity to a 1bp
move in yield -- the same sensitivity Key Rate Duration measures in
percentage terms, rescaled into currency so it reads directly as a hedge
size.

    DV01           = Price * ModifiedDuration * 0.0001
    DV01_k (tenor) = Price * KRD_k           * 0.0001

Neither duration nor pricing is recomputed here -- this is a thin
currency-unit conversion layer over bond_pricing.price_bond() and
key_rate_duration's existing KRD/duration functions.

UNITS: every DV01 here is in the same units price_bond already uses --
currency per 100 face value, never scaled to a real position size. Chosen
because every other number in this project already uses that convention
(no conversion for a reader to track), and because scaling to a "real"
position would just be inventing a second made-up number alongside the
portfolio's already-illustrative coupons and weights. A real trading book
would apply an actual position size on top of this.

Own module, not folded into key_rate_duration.py, matching this project's
one-file-per-deliverable pattern -- keeps that module focused on
percentage-terms sensitivity only.
"""

from __future__ import annotations

import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_bond
from models.key_rate_duration import (
    DEFAULT_BUMP_SIZE,
    effective_duration_bond,
    key_rate_duration_bond,
)


def dv01_bond(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> float:
    """One bond's DV01: Price * ModifiedDuration * bump_size.

    Two existing numbers multiplied together, nothing recomputed.
    bump_size does double duty: it's the numerical step
    effective_duration_bond uses internally, and, by definition, the
    shock size this DV01 represents -- its 1bp default is what makes this
    a genuine "DV01" rather than the value of some other-sized move.

    Raises whatever price_bond / effective_duration_bond raise for a bad
    input.
    """
    price = price_bond(face_value, coupon_rate, maturity_years, curve, freq=freq)
    modified_duration = effective_duration_bond(
        face_value, coupon_rate, maturity_years, curve, freq=freq, bump_size=bump_size
    )
    return price * modified_duration * bump_size


def dv01_by_tenor_bond(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> pd.Series:
    """One bond's DV01, broken out by curve tenor: Price * KRD_k *
    bump_size, one value per tenor -- the currency analogue of
    key_rate_duration_bond's KRD vector, and the number that actually
    sizes a hedge ("how much of a 10Y hedge do I need to offset this
    bond's 10Y exposure"), which a duration figure alone doesn't tell
    you.

    Price is one price_bond() call, shared across every tenor. Because
    this is just KRD rescaled by the same two constants at every tenor,
    summing this Series across tenors inherits the same accuracy
    key_rate_duration_bond's own tenor sum has relative to overall
    duration -- no new error from the rescaling itself.
    """
    price = price_bond(face_value, coupon_rate, maturity_years, curve, freq=freq)
    krd = key_rate_duration_bond(face_value, coupon_rate, maturity_years, curve, freq=freq, bump_size=bump_size)
    return (price * krd * bump_size).rename("dv01")


def dv01_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> pd.DataFrame:
    """Per-bond DV01 across a portfolio, mirroring price_portfolio's shape
    (one row per bond, no total row) rather than
    key_rate_duration_portfolio's, since it answers a per-bond question
    ("what is each bond's own DV01"). A portfolio-level total is just as
    computable from this table as price_portfolio's weighted price is:
    `(df.weight * df.dv01).sum()`.

    dv01_by_tenor_portfolio (below) makes the opposite shape choice on
    purpose, since it answers a per-tenor question instead.
    """
    rows = [
        {
            "name": bond.name,
            "maturity_years": bond.maturity_years,
            "coupon_rate": bond.coupon_rate,
            "weight": bond.weight,
            "price": price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq),
            "modified_duration": effective_duration_bond(
                bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq, bump_size=bump_size
            ),
            "dv01": dv01_bond(
                bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq, bump_size=bump_size
            ),
        }
        for bond in portfolio
    ]
    return pd.DataFrame(
        rows,
        columns=["name", "maturity_years", "coupon_rate", "weight", "price", "modified_duration", "dv01"],
    )


def dv01_by_tenor_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> pd.DataFrame:
    """Per-bond and portfolio-level DV01 by tenor -- mirrors
    key_rate_duration_portfolio's shape: one row per bond plus a
    "portfolio_total" row (each bond's weight times its own DV01,
    summed), one column per tenor. The table that sizes tenor-specific
    hedges at the portfolio level: how many currency units the whole book
    gains or loses if only, say, the 20Y point moves 1bp.
    """
    per_bond = {
        bond.name: dv01_by_tenor_bond(
            bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq, bump_size=bump_size
        )
        for bond in portfolio
    }
    df = pd.DataFrame(per_bond).T
    df.columns.name = "maturity_years"

    weights = pd.Series({bond.name: bond.weight for bond in portfolio})
    df.loc["portfolio_total"] = df.mul(weights, axis=0).sum(axis=0)

    return df


if __name__ == "__main__":
    curve = load_jgb_curve()
    portfolio = load_portfolio()

    per_bond = dv01_portfolio(portfolio, curve)
    portfolio_dv01 = float((per_bond["weight"] * per_bond["dv01"]).sum())

    print()
    print("DV01 by bond (currency per 100 face value):")
    print(
        per_bond.to_string(
            index=False,
            formatters={
                "coupon_rate": lambda c: f"{c:.3%}",
                "weight": lambda w: f"{w:.2%}",
                "price": lambda p: f"{p:.4f}",
                "modified_duration": lambda d: f"{d:.4f}",
                "dv01": lambda d: f"{d:.6f}",
            },
        )
    )
    print()
    print(f"Portfolio-level weighted DV01 (currency per 100 face value): {portfolio_dv01:.6f}")

    dv01_table = dv01_by_tenor_portfolio(portfolio, curve)
    print()
    print("DV01 by tenor (currency per 100 face value, per bond/portfolio row):")
    print(dv01_table.to_string(float_format=lambda v: f"{v:8.6f}"))

    print()
    print("Sanity check -- DV01 formula (Price * ModifiedDuration * bump_size) vs. a")
    print("direct one-sided +1bp parallel bump-and-reprice:")
    for bond in portfolio:
        price = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        formula = dv01_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)

        bumped_curve = curve.copy()
        bumped_curve["yield"] = bumped_curve["yield"] + DEFAULT_BUMP_SIZE
        price_up = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, bumped_curve)
        direct = price - price_up

        gap_pct = (formula - direct) / direct
        print(
            f"  {bond.name:>8s}  formula = {formula:.6f}   "
            f"direct bump = {direct:.6f}   gap = {gap_pct:+.4%}"
        )
