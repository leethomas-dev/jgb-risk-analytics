"""
key_rate_duration.py

Key Rate Duration (KRD): per-tenor price sensitivity. Bump ONE grid tenor's
yield up AND down by bump_size, hold every other grid tenor fixed, reprice
both through the existing price_bond() pipeline, and measure the resulting
% price change as a CENTRAL (two-sided) finite difference:

    KRD_k = -(1/P) * (P_up_k - P_down_k) / (2 * bump_size)

P is the unbumped base price, used only to normalize the price change into
a duration-like units; P_up_k / P_down_k are that same bond repriced with
tenor k's yield bumped by +bump_size and -bump_size respectively, all other
tenors untouched. A central (two-sided) difference was chosen over a
one-sided (forward) difference -- see "WHY CENTRAL, NOT ONE-SIDED" below.

Reads the portfolio via config.portfolio_loader.load_portfolio() and prices
via models.bond_pricing.price_bond() -- this module hardcodes neither a bond
list nor a curve, and does no curve interpolation of its own.

BUMP SHAPE: a point bump at the grid level, which becomes a TRIANGULAR
("tent") bump in the interpolated curve -- not a special case coded here,
but a direct, deliberate consequence of reusing curve_yield_at() unchanged.

curve_yield_at() (models/bond_pricing.py) linearly interpolates between
adjacent grid tenors. So bumping only row k's yield -- leaving rows k-1 and
k+1 untouched -- changes the INTERPOLATED curve like this (true whether the
bump added is +bump_size or -bump_size -- the two legs of the central
difference below each produce the same tent shape, just mirrored):

    - at tenor t_k itself: the full bump_size
    - ramping linearly down to 0 at t_{k-1} and at t_{k+1}
    - unchanged (0) beyond t_{k-1} on the left / t_{k+1} on the right
    - EXCEPT at the grid's own ends: bumping t_0 (the shortest tenor) or
      t_{n-1} (the longest) also moves every maturity beyond that end, at
      the FULL bump_size, because curve_yield_at extrapolates flat off the
      end tenors' own yields (its own documented policy) -- so the endpoint
      "tent" has one sloped side (toward its interior neighbor) and one
      flat side (extending to +/- infinity in maturity), rather than two
      sloped sides.

This is the standard tent/triangular KRD construction, not the alternative
("pure point shift": bump only the exact grid maturity and nothing else,
leaving every other maturity -- including ones a hair away -- fully
unaffected). The triangular form was chosen because:

    1. It is what curve_yield_at's own linear interpolation already
       produces from a one-line point bump -- getting the alternative
       (a true Dirac-style point shift, affecting price only if a cash flow
       lands at EXACTLY t_k) would require bypassing or special-casing
       curve_yield_at's interpolation, which this module deliberately does
       not do: KRD should measure sensitivity to the same curve model
       price_bond actually prices against, not a different one invented
       only for this metric.
    2. The tent functions are a partition of unity: at every maturity m,
       summing all tenors' tent weights gives exactly 1 (interior points
       are covered by exactly two overlapping ramps that sum to 1; points
       beyond either end are covered by that end's flat extension, which is
       1 on its own). Bumping every tenor by the same bump_size is
       therefore equivalent to one true parallel shift of bump_size at
       every maturity -- which is exactly why summing a bond's KRDs across
       all tenors approximates its effective (parallel-shift) duration
       (see effective_duration_bond() and the test
       test_sum_of_krds_approximates_effective_duration). A pure point-shift
       bump has no such property: a cash flow that never lands on an exact
       grid tenor would be invisible to every single-tenor bump, and the
       KRDs would undercount and fail to sum to the bond's real duration --
       the exact divergence this design avoids.

WHY CENTRAL, NOT ONE-SIDED: a one-sided (forward) difference,
KRD_k = -(1/P) * (P_bumped_k - P_base) / bump_size, is the more obvious
formula and was the other candidate. It was rejected in favor of the
central difference above because a one-sided difference carries a
first-order truncation error term in bump_size that a central difference
cancels algebraically -- central differencing is a strictly more accurate
local-derivative estimate for the same bump_size, at the cost of one extra
reprice per tenor (P_down_k, in addition to P_up_k, rather than reusing the
single shared P_base). key_rate_duration_bond() and effective_duration_bond()
both use this same central-difference methodology, on purpose: the
sum-of-KRDs-vs-effective-duration sanity check
(test_sum_of_krds_approximates_effective_duration; see also
docs/phase_3a_documentation.md §2) only isolates a bond's genuine convexity
-- the reason the sum and the benchmark aren't bit-identical even with a
consistent method -- if both sides of that comparison use the same
finite-difference method. Mixing a central-difference KRD sum against a
one-sided effective-duration benchmark (or vice versa) would reintroduce a
truncation-error mismatch between them that has nothing to do with the
property actually being checked.

No assumption is made about how many tenors the curve has or which ones --
same non-fixed-tenor-grid contract as data/jgb_curve_loader.py (Phase 1
§4.5) and models/bond_pricing.py (Phase 2B §1.1): KRD tenors are read off
whatever curve the caller passes in, at call time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_bond

# 1 basis point, decimal (matches the curve's own decimal yield convention --
# Phase 1's output contract, "0.0288, not 2.88"). Small enough that the
# finite-difference KRD is a good local derivative estimate; large enough
# that float noise in price_bond's discounting doesn't dominate the
# difference (P_bumped - P_base) computed at this scale.
DEFAULT_BUMP_SIZE = 0.0001


def _sorted_tenors_and_curve(curve: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    """Defensively re-sort curve by maturity_years (same reasoning as
    curve_yield_at's own re-sort -- don't trust the caller's row order) and
    return (tenors, sorted_curve) with a fresh RangeIndex, so bumping row k
    by positional index always means "the k-th tenor in ascending order."
    """
    curve_sorted = curve.sort_values("maturity_years").reset_index(drop=True)
    return curve_sorted["maturity_years"].to_numpy(), curve_sorted


def _bump_curve_at(curve_sorted: pd.DataFrame, row_index: int, bump_size: float) -> pd.DataFrame:
    """Return a copy of curve_sorted with bump_size added to exactly one
    row's yield. bump_size may be negative -- the central difference in
    key_rate_duration_bond calls this once with +bump_size and once with
    -bump_size at the same row_index, and each call independently produces
    the same triangular tent shape (mirrored in sign) documented in the
    module docstring; this one-line point bump is the entire mechanism
    behind it, on either leg."""
    bumped = curve_sorted.copy()
    bumped.loc[row_index, "yield"] = bumped.loc[row_index, "yield"] + bump_size
    return bumped


def key_rate_duration_bond(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> pd.Series:
    """Per-tenor Key Rate Duration for one bond.

    Bumps each grid tenor's yield by +bump_size AND -bump_size in turn (all
    other tenors held fixed each time), reprices both via price_bond, and
    returns the central (two-sided) finite difference

        KRD_k = -(1/P_base) * (P_up_k - P_down_k) / (2 * bump_size)

    as a pandas Series indexed by the curve's own maturity_years (ascending
    -- see _sorted_tenors_and_curve), one entry per grid tenor. P_base (the
    unbumped price) is used only to normalize the price change; it is not
    part of either bumped leg. The tenor set is whatever `curve` actually
    contains at call time -- 12 points for the Phase 1 snapshot, 15 for the
    live/cache grid, or any other grid a caller supplies (Phase 1 §4.5's
    non-fixed-tenor-grid contract).

    Central, not one-sided -- see "WHY CENTRAL, NOT ONE-SIDED" in the module
    docstring: this costs one extra reprice per tenor versus a one-sided
    difference (P_down_k, in addition to P_up_k) in exchange for cancelling
    the leading truncation error a one-sided formula would carry.

    A tenor far from every one of this bond's cash flows -- outside the
    support of that tenor's own tent (see module docstring) -- contributes
    a KRD of ~0, by construction: bumping it, in either direction, doesn't
    move the interpolated yield at any of this bond's cash-flow times at
    all.

    Raises whatever price_bond raises for a bad face_value / maturity_years
    / freq / empty curve (input validation is not duplicated here), plus
    ValueError if bump_size is not positive.
    """
    if bump_size <= 0:
        raise ValueError(f"bump_size must be positive, got {bump_size}")

    tenors, curve_sorted = _sorted_tenors_and_curve(curve)
    base_price = price_bond(face_value, coupon_rate, maturity_years, curve_sorted, freq=freq)

    krds = np.empty(len(tenors))
    for k in range(len(tenors)):
        up_curve = _bump_curve_at(curve_sorted, k, bump_size)
        down_curve = _bump_curve_at(curve_sorted, k, -bump_size)
        up_price = price_bond(face_value, coupon_rate, maturity_years, up_curve, freq=freq)
        down_price = price_bond(face_value, coupon_rate, maturity_years, down_curve, freq=freq)
        krds[k] = -(up_price - down_price) / (2.0 * base_price * bump_size)

    return pd.Series(krds, index=pd.Index(tenors, name="maturity_years"), name="krd")


def effective_duration_bond(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> float:
    """One bond's effective (parallel-shift) duration, computed the same
    central-finite-difference way as key_rate_duration_bond -- bump EVERY
    grid tenor by +bump_size at once, and separately by -bump_size at once
    (two true parallel shifts, since the tent functions are a partition of
    unity -- module docstring), reprice both, and

        D_eff = -(1/P_base) * (P_shifted_up - P_shifted_down) / (2 * bump_size)

    This exists specifically as the independent benchmark
    key_rate_duration_bond's per-tenor output is checked against: summing
    a bond's KRDs across all tenors should approximate this number (see
    test_sum_of_krds_approximates_effective_duration). It is not itself a
    new pricing formula -- it is price_bond, called on two uniformly shifted
    curves, exactly like every other function in this module. Uses the same
    central-difference methodology as key_rate_duration_bond deliberately --
    see "WHY CENTRAL, NOT ONE-SIDED" in the module docstring for why the two
    must stay on the same method.
    """
    if bump_size <= 0:
        raise ValueError(f"bump_size must be positive, got {bump_size}")

    _, curve_sorted = _sorted_tenors_and_curve(curve)
    base_price = price_bond(face_value, coupon_rate, maturity_years, curve_sorted, freq=freq)

    shifted_up = curve_sorted.copy()
    shifted_up["yield"] = shifted_up["yield"] + bump_size
    shifted_up_price = price_bond(face_value, coupon_rate, maturity_years, shifted_up, freq=freq)

    shifted_down = curve_sorted.copy()
    shifted_down["yield"] = shifted_down["yield"] - bump_size
    shifted_down_price = price_bond(face_value, coupon_rate, maturity_years, shifted_down, freq=freq)

    return -(shifted_up_price - shifted_down_price) / (2.0 * base_price * bump_size)


def key_rate_duration_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> pd.DataFrame:
    """Per-bond and portfolio-level Key Rate Duration.

    Takes an already-loaded portfolio and curve rather than loading them
    itself, mirroring price_portfolio (models/bond_pricing.py §1.3) --
    trivially testable against in-memory fixtures, and reusable unchanged
    against a curve Phase 3 callers hold and repeatedly mutate elsewhere.

    Returns a DataFrame with one row per bond (indexed by bond name, in
    portfolio order) plus a final "portfolio_total" row, and one column per
    curve tenor (the curve's own maturity_years, ascending). Each bond's row
    is that bond's key_rate_duration_bond(...) Series. The total row is the
    market-value-weighted sum across bonds,

        KRD_portfolio,k = sum_i weight_i * KRD_i,k

    -- the standard portfolio-KRD aggregation (a weighted average of each
    holding's own KRD, weighted by portfolio allocation), and the same
    weight convention price_portfolio already uses for a portfolio-level
    weighted price (Phase 2B §1.3): weights are guaranteed by
    load_portfolio() to sum to 1.0, so this weighted sum needs no separate
    notional or normalization step.
    """
    tenors, curve_sorted = _sorted_tenors_and_curve(curve)

    per_bond = {
        bond.name: key_rate_duration_bond(
            bond.face_value, bond.coupon_rate, bond.maturity_years, curve_sorted, freq=freq, bump_size=bump_size
        ).to_numpy()
        for bond in portfolio
    }

    df = pd.DataFrame.from_dict(per_bond, orient="index", columns=pd.Index(tenors, name="maturity_years"))

    weights = np.array([bond.weight for bond in portfolio])
    df.loc["portfolio_total"] = (df.to_numpy() * weights[:, None]).sum(axis=0)

    return df


if __name__ == "__main__":
    curve = load_jgb_curve()
    portfolio = load_portfolio()

    krd_table = key_rate_duration_portfolio(portfolio, curve)

    print()
    print("Key Rate Duration by tenor (years of duration per bond/portfolio row):")
    print(krd_table.to_string(float_format=lambda v: f"{v:6.3f}"))

    print()
    print("Sanity check -- sum of each bond's KRDs vs. its effective (parallel-shift) duration:")
    for bond in portfolio:
        krd_sum = float(krd_table.loc[bond.name].sum())
        eff_dur = effective_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve)
        gap = krd_sum - eff_dur
        print(
            f"  {bond.name:>8s}  sum(KRD) = {krd_sum:6.3f}   "
            f"effective duration = {eff_dur:6.3f}   gap = {gap:+.5f}"
        )
