"""
key_rate_duration.py

Key Rate Duration (KRD): per-tenor price sensitivity. Bump ONE curve
tenor's yield up AND down by bump_size, hold every other tenor fixed,
reprice both through price_bond(), and measure the % price change as a
central (two-sided) difference:

    KRD_k = -(1/P) * (P_up_k - P_down_k) / (2 * bump_size)

Reads the portfolio and prices via their own loaders -- never hardcodes
either, and does no curve interpolation of its own.

WORKS ON EITHER A PAR CURVE OR A BOOTSTRAPPED ZERO CURVE (Phase 4.5C
addition), unchanged for existing callers: _rate_column() reads which
rate column a curve actually has ('yield' or 'zero_rate') instead of
_bump_curve_at/effective_duration_bond hardcoding 'yield' -- a par curve
still always resolves to 'yield', the original literal, so every existing
test's code path is untouched bit-for-bit. See
models/bond_pricing.py's own module docstring for the matching
auto-detection there, and docs/phase_4_5c_documentation.md §1 for the
full account.

BUMP SHAPE: bumping one curve row becomes a TRIANGULAR ("tent") shape in
the interpolated curve, automatically -- not special-cased here, just a
consequence of price_bond's own straight-line interpolation between grid
points. Bump row k's yield, leave its neighbors untouched, and the
interpolated curve ramps from 0 at the tenor before k, up to the full
bump at k, back to 0 at the tenor after -- because that's what
straight-line interpolation between an unmoved point and a moved one
does. At the two ends of the grid the tent has one sloped side and one
flat side instead of two sloped sides, since rates beyond the curve's
range are held flat.

The alternative (a bump affecting *only* the exact tenor, nothing else)
was rejected: it would require bypassing price_bond's own interpolation,
measuring sensitivity to a different rate model than the one actually
used to price the bond. The tent shape also has a useful property: at
every maturity, all tenors' tent weights sum to exactly 1 -- so bumping
every tenor by the same amount is equivalent to one true whole-curve
shift, which is why summing a bond's KRDs across tenors approximates its
overall duration (see effective_duration_bond and
test_sum_of_krds_approximates_effective_duration). The rejected
alternative wouldn't have this property.

WHY A CENTRAL (TWO-SIDED) DIFFERENCE, NOT ONE-SIDED: a one-sided version
(bump up only, compare to the unbumped price) is simpler but carries a
larger, avoidable numerical error for the same bump size; the two-sided
version above cancels most of it for one extra reprice per tenor. Both
key_rate_duration_bond() and effective_duration_bond() use this same
two-sided method deliberately -- the sanity check between them
(test_sum_of_krds_approximates_effective_duration; see also
docs/phase_3a_documentation.md §2) only cleanly isolates a bond's real
convexity if both sides use an identical method; mixing methods would
blur that signal with a numerical-error mismatch instead.

No assumption is made about how many tenors the curve has or which ones
-- KRD tenors are read off whatever curve the caller passes in, at call
time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_bond

# 1 basis point, decimal (matches the curve's own decimal convention).
# Small enough for an accurate local sensitivity estimate; large enough
# that floating-point noise doesn't dominate the price difference.
DEFAULT_BUMP_SIZE = 0.0001


def _sorted_tenors_and_curve(curve: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    """Re-sort curve by maturity_years (don't trust the caller's row
    order) and return (tenors, sorted_curve), so bumping row k always
    means "the k-th tenor in ascending order."
    """
    curve_sorted = curve.sort_values("maturity_years").reset_index(drop=True)
    return curve_sorted["maturity_years"].to_numpy(), curve_sorted


def _rate_column(curve: pd.DataFrame) -> str:
    """Which column holds this curve's own rate -- 'yield' for a par
    curve, 'zero_rate' for a bootstrapped zero curve (models.bootstrap.
    bootstrap_zero_curve()'s output shape). Phase 4.5C addition: lets
    every bump site below work on EITHER curve shape, unchanged for any
    existing par-curve caller (a 'yield'-column curve always resolves to
    'yield', the original hardcoded literal, so behavior for every
    existing test is bit-for-bit identical -- see
    docs/phase_4_5c_documentation.md §1). price_bond itself auto-detects
    the same way (models/bond_pricing.py module docstring).
    """
    if "yield" in curve.columns:
        return "yield"
    if "zero_rate" in curve.columns:
        return "zero_rate"
    raise ValueError(
        f"curve has neither a 'yield' nor a 'zero_rate' column: {list(curve.columns)}"
    )


def _bump_curve_at(curve_sorted: pd.DataFrame, row_index: int, bump_size: float) -> pd.DataFrame:
    """Return a copy of curve_sorted with bump_size added to exactly one
    row's rate (module docstring's tent shape -- 'yield' for a par curve,
    'zero_rate' for a zero curve, see _rate_column). bump_size may be
    negative -- key_rate_duration_bond calls this once with each sign at
    the same row_index for its two-sided difference; each call produces
    the tent shape described in the module docstring, mirrored in sign."""
    rate_col = _rate_column(curve_sorted)
    bumped = curve_sorted.copy()
    bumped.loc[row_index, rate_col] = bumped.loc[row_index, rate_col] + bump_size
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

    Bumps each curve tenor's yield up and down in turn, reprices both via
    price_bond, and returns the resulting central-difference sensitivity
    as a pandas Series, indexed by the curve's own tenors -- one entry per
    tenor the curve actually has (see module docstring for why this isn't
    a fixed list, and for the "why central, not one-sided" reasoning).

    A tenor far from every one of this bond's cash flows contributes a
    KRD of ~0, by construction -- bumping it doesn't move the rate at any
    of this bond's cash-flow times at all.

    Raises whatever price_bond raises for a bad input, plus ValueError if
    bump_size is not positive.
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
    """One bond's whole-curve (parallel-shift) duration, using the same
    central-difference method as key_rate_duration_bond -- but shifting
    EVERY tenor at once, both up and down, rather than one at a time.

    Exists specifically as the independent benchmark
    key_rate_duration_bond's tenor-by-tenor sum is checked against (see
    test_sum_of_krds_approximates_effective_duration) -- not a new pricing
    formula, just price_bond called on two uniformly shifted curves. Uses
    the same two-sided method deliberately, so the two numbers stay
    comparable (module docstring).
    """
    if bump_size <= 0:
        raise ValueError(f"bump_size must be positive, got {bump_size}")

    _, curve_sorted = _sorted_tenors_and_curve(curve)
    rate_col = _rate_column(curve_sorted)
    base_price = price_bond(face_value, coupon_rate, maturity_years, curve_sorted, freq=freq)

    shifted_up = curve_sorted.copy()
    shifted_up[rate_col] = shifted_up[rate_col] + bump_size
    shifted_up_price = price_bond(face_value, coupon_rate, maturity_years, shifted_up, freq=freq)

    shifted_down = curve_sorted.copy()
    shifted_down[rate_col] = shifted_down[rate_col] - bump_size
    shifted_down_price = price_bond(face_value, coupon_rate, maturity_years, shifted_down, freq=freq)

    return -(shifted_up_price - shifted_down_price) / (2.0 * base_price * bump_size)


def key_rate_duration_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int | None = None,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> pd.DataFrame:
    """Per-bond and portfolio-level Key Rate Duration.

    Takes an already-loaded portfolio and curve, mirroring price_portfolio
    -- easy to test, and reusable against a curve a caller repeatedly
    mutates elsewhere.

    freq : None (default) prices each bond at its OWN bond.freq; an
    explicit int overrides every bond to that one shared frequency (see
    models.bond_pricing.price_portfolio's own docstring for why that
    override exists -- e.g. required when curve is a zero curve, which
    must be priced against at the freq it was bootstrapped with).

    Returns a DataFrame: one row per bond (by name, portfolio order) plus
    a final "portfolio_total" row, one column per curve tenor. The total
    row is each bond's weight times its own KRD, summed -- the same
    weighting price_portfolio already uses for a portfolio-level price.
    """
    tenors, curve_sorted = _sorted_tenors_and_curve(curve)

    per_bond = {
        bond.name: key_rate_duration_bond(
            bond.face_value, bond.coupon_rate, bond.maturity_years, curve_sorted,
            freq=freq if freq is not None else bond.freq, bump_size=bump_size,
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
