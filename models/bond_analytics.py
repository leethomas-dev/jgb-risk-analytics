"""
bond_analytics.py

Phase 4.6B: the standard reported bond analytics -- yield to maturity,
Macaulay/modified duration, and convexity. Every other risk number this
project has built so far (KRD, DV01, PCA factor exposure) is sophisticated
machinery most bond desks don't compute for themselves; these are the
opposite -- table-stakes numbers that lead every bond page on a market
data terminal, absent here until now.

DIRECTION OF CAUSATION, STATED EXPLICITLY. Everywhere else in this
project, a bond's price is computed FROM the curve (bond_pricing.price_bond).
Here that runs the other way: yield_to_maturity() takes an
ALREADY-COMPUTED price and solves for the single flat rate that would
have produced it -- a REPORTING CONVENTION for describing a price that
was already determined by the curve, not a new input to anything
upstream. Nothing else in this project ever takes a YTM as an input.

WHICH PRICE YTM IS SOLVED AGAINST: THE CLEAN PRICE (bond_pricing.price_bond's
own output) -- not the dirty price (bond_pricing.dirty_price(), Phase
4.6A). Reasoning: price_bond's cash-flow schedule (bond_pricing.
cash_flow_schedule) -- and therefore the flat-yield reprice this module
inverts, via models.bootstrap.implied_ytm -- is anchored at t=0 = "a
coupon just occurred", exactly the clean price's own assumption. Real-
market practice conventionally solves YTM against the DIRTY price instead,
discounting from the actual settlement date with a genuine fractional
first coupon period. This project's dirty_price() does NOT rebuild the
cash-flow schedule from settlement_date to do that -- it adds a
separately prorated accrued_interest() on top of the SAME t=0-anchored
schedule (docs/phase_4_6a_documentation.md §3) -- so solving this
module's flat-yield equation against the dirty price would mix two
different schedule assumptions into one root-find, which would be a
worse-hidden error than being explicit about using the clean price. See
docs/phase_4_6b_documentation.md §1 for the full account of this disclosed
simplification.

MACAULAY / MODIFIED DURATION AND CONVEXITY ARE ANALYTIC (YTM-BASED), A
DIFFERENT SENSITIVITY CONCEPT FROM PHASE 3A'S effective_duration_bond
(which bumps the real, sloped CURVE, each cash flow discounted at its OWN
curve-implied rate). The two coincide almost exactly when the curve is
flat across a bond's cash flows, and genuinely diverge -- a real, explained
effect, not a bug -- as the curve's actual slope grows across the bond's
own span of cash flows (docs/phase_4_6b_documentation.md §2 has the
measured gap on this project's real curve, and the flat-curve control
that isolates it from an implementation error). Convexity is computed the
SAME way every other sensitivity in this project already is -- central-
difference bump-and-reprice via price_bond, reusing
key_rate_duration.DEFAULT_BUMP_SIZE for the bump -- applied to the bond's
own solved yield via a flat 2-point curve, so duration, convexity, and the
Taylor-approximation check below all describe movement in the SAME single
variable (module docstring's §2/§3 in the accompanying doc has the full
reasoning and the measured Taylor-approximation improvement).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import cash_flow_schedule, price_bond, price_portfolio
from models.bootstrap import implied_ytm
from models.key_rate_duration import DEFAULT_BUMP_SIZE, effective_duration_bond

# How far a solved yield's own reprice may sit from the target price before
# yield_to_maturity() refuses to return it (module docstring: "report
# non-convergence rather than a silently wrong number"). Absolute, in the
# same price units as price_bond's own output (per 100 face value) --
# several orders of magnitude looser than implied_ytm's own internal
# tol=1e-10, so this only ever fires on a genuine failure, not routine
# floating-point noise.
YTM_VERIFY_TOLERANCE = 1e-6


def _flat_curve(rate: float, maturity_years: float) -> pd.DataFrame:
    """A 2-point flat curve spanning a bond's own cash flows, at a single
    rate -- the discounting basis for every analytic (YTM-based) measure
    in this module. Mirrors models.bootstrap.implied_ytm's own internal
    curve construction (same 0.25-year floor, same "past maturity" upper
    point) so the curve this module reprices against for its own
    verification (§ yield_to_maturity) is built the identical way."""
    return pd.DataFrame({"maturity_years": [0.25, maturity_years + 1.0], "yield": [rate, rate]})


def yield_to_maturity(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    price: float,
    freq: int = 2,
) -> float:
    """The single flat yield that reprices this bond's cash flows to
    `price` -- this project's yield-to-maturity. Solved against the CLEAN
    price (module docstring: "WHICH PRICE").

    Root-solve via models.bootstrap.implied_ytm -- reused rather than a
    second, independent bisection implementation, since it already does
    exactly this (invert price_bond by bisection over a bracket wide
    enough for any plausible JGB yield, including negative-rate years).
    Bond price is strictly decreasing in yield, so the bracket's root, if
    one exists inside it, is unique.

    NON-CONVERGENCE, REPORTED RATHER THAN SILENTLY WRONG (module
    docstring): implied_ytm itself already raises ValueError if `price`
    isn't bracketed by its search range -- that propagates unchanged, with
    its own clear message, rather than being caught and hidden here. On
    top of that, this function independently REPRICES at the solved yield
    (via price_bond, a completely separate call path from implied_ytm's
    own internal bisection loop) and raises RuntimeError if that reprice
    doesn't land within YTM_VERIFY_TOLERANCE of `price` -- a genuine
    second check, not a restatement of implied_ytm's own internal
    tolerance, so a future bug in either function's bracket-building logic
    would be caught here even if it didn't trip implied_ytm's own checks.
    """
    ytm = implied_ytm(face_value, coupon_rate, maturity_years, price, freq=freq)

    reprice = price_bond(face_value, coupon_rate, maturity_years, _flat_curve(ytm, maturity_years), freq=freq)
    if abs(reprice - price) > YTM_VERIFY_TOLERANCE:
        raise RuntimeError(
            f"yield_to_maturity did not converge: solved ytm={ytm:.8f} reprices to "
            f"{reprice:.8f}, which is {abs(reprice - price):.2e} away from the target "
            f"price {price:.8f} (tolerance {YTM_VERIFY_TOLERANCE:.1e})"
        )
    return ytm


def macaulay_duration(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    ytm: float,
    freq: int = 2,
) -> float:
    """Macaulay duration: the weighted-average time to a bond's cash
    flows, weights being each cash flow's own present-value share --
    discounted at the single flat `ytm`, not the curve (module docstring).

    For a zero-coupon bond (coupon_rate=0.0), this is EXACTLY
    maturity_years, for any ytm -- there is only one cash flow, so its PV
    share is 1.0 regardless of the discount rate, and the "weighted
    average" collapses to that one cash flow's own time
    (test_zero_coupon_macaulay_duration_equals_maturity_exactly).
    """
    n_periods, cash_flow_times = cash_flow_schedule(maturity_years, freq)
    coupon_payment = face_value * coupon_rate / freq
    cash_flows = np.full(n_periods, coupon_payment, dtype=float)
    cash_flows[-1] += face_value

    present_values = cash_flows / (1.0 + ytm / freq) ** (freq * cash_flow_times)
    price = present_values.sum()
    return float((cash_flow_times * present_values).sum() / price)


def modified_duration(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    ytm: float,
    freq: int = 2,
) -> float:
    """Modified duration = Macaulay duration / (1 + ytm/freq) -- the
    textbook adjustment converting a time-weighted average (Macaulay, in
    years) into a first-order PRICE sensitivity (% price change per unit
    yield change)."""
    mac_dur = macaulay_duration(face_value, coupon_rate, maturity_years, ytm, freq=freq)
    return mac_dur / (1.0 + ytm / freq)


def convexity(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    ytm: float,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> float:
    """Convexity: the second-order price sensitivity to `ytm`, via a
    central difference on price_bond -- the SAME bump-and-reprice
    technique (and, by default, the SAME bump_size) this project already
    uses for KRD/DV01/effective_duration_bond (module docstring), applied
    here to the bond's own flat yield rather than a curve tenor:

        convexity = (P(y+h) + P(y-h) - 2*P(y)) / (P(y) * h^2)

    A closed-form analytic convexity formula exists and was NOT used here
    -- the central-difference version was chosen for methodological
    consistency with every other sensitivity in this project, all of which
    are bump-and-reprice, not closed-form derivatives. The two are cross-
    checked directly in tests/test_bond_analytics.py against an
    independently-derived analytic formula (not this module's own code
    path) -- they agree to ~1e-6 relative or tighter at the default
    bump_size, confirming the central-difference choice costs no real
    accuracy here.
    """
    base_curve = _flat_curve(ytm, maturity_years)
    up_curve = _flat_curve(ytm + bump_size, maturity_years)
    down_curve = _flat_curve(ytm - bump_size, maturity_years)

    p0 = price_bond(face_value, coupon_rate, maturity_years, base_curve, freq=freq)
    p_up = price_bond(face_value, coupon_rate, maturity_years, up_curve, freq=freq)
    p_down = price_bond(face_value, coupon_rate, maturity_years, down_curve, freq=freq)

    return (p_up + p_down - 2.0 * p0) / (p0 * bump_size**2)


def bond_analytics_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int = 2,
) -> pd.DataFrame:
    """Yield, duration, and convexity for every bond in `portfolio`,
    against one `curve` -- plus a `portfolio_total` row, weighted the same
    way as key_rate_duration_portfolio / dv01_by_tenor_portfolio (each
    bond's own config weight, load_portfolio()'s own guarantee that these
    sum to 1.0).

    Columns: name, maturity_years, coupon_rate, weight, price (clean),
    ytm, macaulay_duration, modified_duration, effective_duration (the
    Phase 3A curve-based cross-check, computed alongside for direct
    comparison -- module docstring's "MACAULAY / MODIFIED DURATION..."),
    convexity.

    The portfolio_total row's ytm is a value-weighted AVERAGE of the
    bonds' own individual yields -- a common market approximation for "the
    portfolio's yield", not a rigorously derived single discount rate for
    the whole book (no such single rate generally exists for a multi-bond
    portfolio). Duration and convexity total rows are the standard
    value-weighted portfolio duration/convexity (the same linear
    weighting price_portfolio/dv01_portfolio already use elsewhere).
    """
    rows = []
    for bond in portfolio:
        price = price_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq)
        ytm = yield_to_maturity(bond.face_value, bond.coupon_rate, bond.maturity_years, price, freq=freq)
        mac_dur = macaulay_duration(bond.face_value, bond.coupon_rate, bond.maturity_years, ytm, freq=freq)
        mod_dur = mac_dur / (1.0 + ytm / freq)
        eff_dur = effective_duration_bond(bond.face_value, bond.coupon_rate, bond.maturity_years, curve, freq=freq)
        conv = convexity(bond.face_value, bond.coupon_rate, bond.maturity_years, ytm, freq=freq)
        rows.append(
            {
                "name": bond.name,
                "maturity_years": bond.maturity_years,
                "coupon_rate": bond.coupon_rate,
                "weight": bond.weight,
                "price": price,
                "ytm": ytm,
                "macaulay_duration": mac_dur,
                "modified_duration": mod_dur,
                "effective_duration": eff_dur,
                "convexity": conv,
            }
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "name", "maturity_years", "coupon_rate", "weight", "price", "ytm",
            "macaulay_duration", "modified_duration", "effective_duration", "convexity",
        ],
    )
    weights = df["weight"].to_numpy()
    numeric_cols = ["ytm", "macaulay_duration", "modified_duration", "effective_duration", "convexity"]
    totals = {col: float((df[col].to_numpy() * weights).sum()) for col in numeric_cols}
    totals["price"] = float((df["price"].to_numpy() * weights).sum())
    df.loc["portfolio_total", list(totals)] = totals
    return df


if __name__ == "__main__":
    curve = load_jgb_curve()
    portfolio = load_portfolio()

    results = bond_analytics_portfolio(portfolio, curve)
    print()
    print("Bond analytics: yield, duration, convexity (per 100 face value):")
    print(
        results.to_string(
            formatters={
                "coupon_rate": lambda c: f"{c:.3%}" if pd.notna(c) else "",
                "weight": lambda w: f"{w:.2%}" if pd.notna(w) else "",
                "price": lambda p: f"{p:.4f}",
                "ytm": lambda y: f"{y:.4%}",
                "macaulay_duration": lambda d: f"{d:.4f}" if pd.notna(d) else "",
                "modified_duration": lambda d: f"{d:.4f}",
                "effective_duration": lambda d: f"{d:.4f}",
                "convexity": lambda c: f"{c:.2f}",
            }
        )
    )

    print()
    print("Cross-check -- modified duration (analytic, YTM-based) vs. effective duration")
    print("(curve-based, bump-and-reprice, Phase 3A). Both are legitimate but DIFFERENT")
    print("sensitivities; they coincide on a flat curve and diverge with real curve slope")
    print("(docs/phase_4_6b_documentation.md §2 has the flat-curve control):")
    for _, row in results.drop("portfolio_total").iterrows():
        gap = row["modified_duration"] - row["effective_duration"]
        rel = gap / row["effective_duration"]
        print(
            f"  {row['name']:>8s}  modified={row['modified_duration']:7.4f}  "
            f"effective={row['effective_duration']:7.4f}  gap={gap:+.4f} ({rel:+.2%})"
        )

    print()
    print("Taylor approximation check -- duration alone vs. duration+convexity, tracking an")
    print("actual reprice, for the longest bond in the portfolio under a range of yield moves:")
    longest = max(portfolio, key=lambda b: b.maturity_years)
    base_price = price_bond(longest.face_value, longest.coupon_rate, longest.maturity_years, curve)
    ytm = yield_to_maturity(longest.face_value, longest.coupon_rate, longest.maturity_years, base_price)
    mod_dur = modified_duration(longest.face_value, longest.coupon_rate, longest.maturity_years, ytm)
    conv = convexity(longest.face_value, longest.coupon_rate, longest.maturity_years, ytm)
    print(f"  {longest.name}: ytm={ytm:.4%}  modified_duration={mod_dur:.4f}  convexity={conv:.2f}")
    for dy in (0.001, 0.01, 0.02, -0.02):
        actual = price_bond(longest.face_value, longest.coupon_rate, longest.maturity_years, _flat_curve(ytm + dy, longest.maturity_years))
        actual_pct = (actual - base_price) / base_price
        dur_only = -mod_dur * dy
        dur_conv = -mod_dur * dy + 0.5 * conv * dy**2
        print(
            f"    dy={dy:+.3f}  actual={actual_pct:+.5%}  "
            f"duration-only={dur_only:+.5%} (err={abs(dur_only - actual_pct):.5%})  "
            f"duration+convexity={dur_conv:+.5%} (err={abs(dur_conv - actual_pct):.5%})"
        )
