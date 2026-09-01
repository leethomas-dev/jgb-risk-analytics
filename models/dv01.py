"""
dv01.py

DV01 ("dollar value of 01"): currency-terms price sensitivity to a 1bp
move in yield -- the same underlying sensitivity Key Rate Duration
(models/key_rate_duration.py) measures in percentage/duration terms,
rescaled into currency units so it can be read directly as a hedge size.

    DV01          = Price * ModifiedDuration * 0.0001
    DV01_k (tenor)  = Price * KRD_k           * 0.0001

ModifiedDuration is models.key_rate_duration.effective_duration_bond(...)
(a parallel-shift, central-difference modified duration -- already exactly
the quantity this formula calls for); KRD_k is
models.key_rate_duration.key_rate_duration_bond(...)'s per-tenor output.
Neither duration nor pricing is recomputed here -- this module is a thin
currency-unit conversion layer over Phase 2B's price_bond and Phase 3A's
KRD/effective-duration, not a new sensitivity calculation.

UNITS: every DV01 in this module is in the SAME units as price_bond's own
output -- currency per 100 face value (JPY, for JGBs), never scaled to a
real notional or assets-under-management figure. This is a deliberate
choice, not the only one available (per-bond, or notional-scaled, were the
named alternatives): it costs nothing (price_bond, price_portfolio, and
key_rate_duration_portfolio already all report "per 100 face" without any
notional scaling -- Phase 2A §1.1 explains why face_value=100 was chosen
as the illustrative convention in the first place), and it means every
number in this module is directly comparable to the price and KRD figures
already produced by this project, with no unit conversion a reader has to
track. A real trading book would scale this by actual position notional;
this project's portfolio is illustrative (Phase 2A's own disclaimer) and
has no such figure to scale by.

Reads the portfolio via config.portfolio_loader.load_portfolio() and prices
via models.bond_pricing.price_bond() / models.key_rate_duration -- this
module hardcodes neither a bond list nor a curve, and repeats no pricing or
differencing logic of its own.

WHY ITS OWN MODULE, NOT FOLDED INTO key_rate_duration.py: this project has
kept one file per phase-part deliverable since Phase 2B (bond_pricing.py)
and Phase 3A (key_rate_duration.py), each with its own 1:1 documentation
file -- DV01 is Part B of Phase 3, a separate deliverable with its own
requirements (currency units, a distinct validation test) sitting on TOP of
both bond_pricing.py and key_rate_duration.py rather than beside either
one. Keeping it separate also keeps key_rate_duration.py focused on
percentage-terms sensitivity only, and leaves room for a future currency-
scaled metric (e.g. hedge notionals) to land here without touching Part A.
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
    """One bond's DV01: the currency price change for a bump_size move in
    yield, via the closed-form modified-duration formula

        DV01 = Price * ModifiedDuration * bump_size

    Price is price_bond(...) (currency per 100 face -- see module
    docstring); ModifiedDuration is effective_duration_bond(...), the same
    central-difference parallel-shift duration Phase 3A already computes
    and validates. bump_size defaults to 1bp (DEFAULT_BUMP_SIZE), which is
    what makes this a genuine "DV01" (dollar value of 01, i.e. of ONE
    basis point specifically) rather than the dollar value of some other
    shock size -- bump_size does double duty here: it is both the
    numerical differencing step effective_duration_bond uses internally,
    and, by definition, the shock size this DV01 figure represents. Pass a
    different value deliberately if you want the dollar value of a
    different-sized shock; the result is no longer "DV01" in the
    traditional sense if you do.

    Raises whatever price_bond / effective_duration_bond raise for a bad
    input (not duplicated here).
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
    """One bond's DV01, broken out by curve tenor -- the currency analogue
    of key_rate_duration_bond's percentage-terms KRD vector, and the
    number that actually converts into a hedge size: "how many currency
    units does this bond gain or lose if just the 10Y point moves 1bp"
    tells you how much of a 10Y hedge instrument you'd need, in a way a
    duration figure alone does not.

        DV01_k = Price * KRD_k * bump_size

    Price is price_bond(...) (one call, shared across every tenor -- the
    bond's price does not depend on which tenor you're asking about);
    KRD_k is key_rate_duration_bond(...)'s per-tenor Series. Returned as a
    pandas Series with the same maturity_years index KRD uses (Phase 3A
    §1.1's non-fixed-tenor-grid contract applies identically here -- the
    tenor set is whatever `curve` contains at call time).

    Because DV01_k is just KRD_k rescaled by the same two constants
    (Price and bump_size) at every tenor, summing this Series across
    tenors approximates the bond's total dv01_bond(...) to the same
    precision key_rate_duration_bond's per-tenor sum approximates
    effective_duration_bond (Phase 3A §2) -- the rescaling is linear and
    introduces no new error of its own.
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
    """Per-bond DV01 across a portfolio, mirroring price_portfolio's own
    shape (models/bond_pricing.py §1.3) rather than
    key_rate_duration_portfolio's: one row per bond, in portfolio order,
    columns [name, maturity_years, coupon_rate, weight, price,
    modified_duration, dv01] -- no baked-in total row.

    That choice, not the alternative: dv01_portfolio is structurally the
    direct DV01 extension of price_portfolio (same columns, same one-
    row-per-bond shape, two new columns appended) -- it answers "what is
    each bond's own DV01," the same kind of question price_portfolio
    answers for price. A portfolio-level total is exactly as computable
    from this DataFrame as price_portfolio's own portfolio-level weighted
    price already is: `(df.weight * df.dv01).sum()`, valid directly
    because load_portfolio() guarantees weights sum to 1.0 (Phase 2A
    §1.4) -- the same weight convention Phase 2B and Phase 3A both already
    rely on, so no new aggregation rule is introduced here.

    dv01_by_tenor_portfolio (below) is the other DV01 aggregation this
    module provides, and DOES bake in a portfolio_total row -- because it
    mirrors key_rate_duration_portfolio instead, for the matching reason:
    it answers a per-TENOR question, not a per-bond one.
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
    """Per-bond and portfolio-level DV01, broken out by curve tenor --
    the currency analogue of key_rate_duration_portfolio, and mirroring
    its shape exactly (models/key_rate_duration.py §1.4): one row per
    bond (indexed by name, portfolio order) plus a final "portfolio_total"
    row, one column per curve tenor.

        DV01_portfolio,k = sum_i weight_i * DV01_i,k

    Same weighted-sum aggregation key_rate_duration_portfolio already
    uses for KRD, applied here to DV01's currency-terms values instead of
    KRD's percentage-terms ones -- no new aggregation rule, just the same
    one reapplied. This is the table that actually sizes tenor-specific
    hedges at the portfolio level: "how many currency units does the
    whole book gain or lose if only the 20Y point moves 1bp."
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
