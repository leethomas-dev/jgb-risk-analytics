"""
bootstrap.py

Phase 4.5A: extracts a zero-coupon (spot) discount curve from the observed
JGB curve -- the missing piece this project has carried as a named
simplification since Phase 2B (docs/phase_2b_documentation.md §3.2):
price_bond discounts every cash flow at the curve's own quoted rate for
that cash flow's maturity, not a yield implied by a genuinely
no-arbitrage zero curve. This module builds that zero curve. (Wiring it
into price_bond as an optional discounting basis is Part C, not built here.)

WHAT THE INPUT CURVE ACTUALLY IS, VERIFIED AGAINST MOF'S OWN METHODOLOGY
-- NOT A PAR CURVE. MOF's own published methodology (`outline-e.pdf`,
linked from https://www.mof.go.jp/english/policy/jgbs/reference/
interest_rate/qa.htm, the Q&A page off the same reference-rate page Phase
1's loader targets) is explicit: for each maturity grid point, MOF selects
specific outstanding benchmark JGB issues trading nearest that maturity,
takes their PREVAILING SECONDARY-MARKET YIELDS (whatever coupon and price
those bonds actually trade at -- never assumed to be 100), and fits a
CUBIC SPLINE through those yield-to-maturity points to read off a
"constant maturity" rate. No step in that process prices anything to par,
and no bootstrapping is involved. This is a fitted YTM curve on real,
non-par-priced bonds, not a par curve.

Bootstrapping requires a par curve -- the par-pricing equation is what
supplies the known "price = 100" side of each step. Treating MOF's fitted
YTM series as if it were a par curve (i.e. as if each tenor's rate were
the coupon that would price a bond of that maturity to exactly 100) is
therefore a SIMPLIFICATION, not a mechanical fact about the input data.
It is also a standard and widely used one in practice (constant-maturity
government yield series are routinely bootstrapped this way, e.g. the
same treatment commonly applied to US Treasury CMT rates) -- kept here
deliberately rather than restructured, per the tradeoff below.

THE MECHANISM -- THE COUPON EFFECT. Two bonds of the same maturity but
different coupons have different YTMs even off one identical, correctly-
built zero curve, because their cash flow timing differs: a low-coupon
bond returns more of its value at the far end of the curve (closer to a
zero-coupon bond), while a high-coupon bond returns more of it earlier.
On a curve that isn't flat, that shifts each bond's own YTM away from the
"par" rate at its maturity, in a direction and magnitude driven by the
curve's own slope and curvature there (a perfectly flat curve produces
zero coupon effect -- see test_coupon_effect_is_zero_on_a_flat_curve).
This matters MORE than usual for JGBs specifically: many outstanding
issues carry near-zero coupons left over from the 2016-2024 ZIRP/YCC era
and now trade at deep discounts, so the coupon dispersion across real
benchmark issues MOF actually samples is wide -- exactly the condition
under which this simplification's error is largest.

coupon_effect_sensitivity() (below) quantifies this directly: for each
input tenor, it prices a hypothetical zero-coupon bond and a hypothetical
high-coupon bond of that SAME maturity off the SAME bootstrapped zero
curve, solves each one's own implied YTM, and reports the gap against the
input MOF rate in basis points -- a direct estimate of how far a real
bond's YTM could plausibly diverge from the "par rate" this module
assumes, purely from its own coupon being far from that assumed par
coupon. See docs/phase_4_5a_documentation.md for the measured results;
the gap is small at the front end and grows sharply in the ultra-long
segment (20Y-40Y) -- the part of the curve this project's portfolio is
most concentrated in.

THE PRINCIPLED ALTERNATIVE, not built here: fit Nelson-Siegel/Svensson
(or bootstrap) directly against observed prices of real, individual
benchmark JGB issues -- never assuming any bond prices at par. This is
blocked on a data source this project doesn't have yet: a MOF issue
reference module supplying real outstanding issues with their real
coupons and secondary-market prices. Until that exists, this
simplification is the practical option, used with its error quantified
rather than left unmeasured.

METHOD -- iterative bootstrapping. A par bond, by construction, prices to
its own face value under its own quoted yield. Working from the shortest
maturity outward, each step's par-pricing equation has exactly one
unknown -- the discount factor for that maturity's own final cash flow --
because every earlier cash flow's discount factor was already solved at a
previous, shorter step. Solving that one linear equation, maturity by
maturity, is "bootstrapping." No numerical optimizer is needed: at each
step there is one equation and one unknown, solved in closed form.

Three design decisions below, each with a real, checked effect on the
result -- not just a formality to note in passing.

1. CASH FLOW GRID: semiannual, out to the input curve's own longest
   maturity. MOF quotes par yields at ~12-15 discrete tenors, but
   bootstrapping needs a par yield at every coupon date the pricing engine
   actually uses -- semiannual, matching price_bond's own freq=2 default.
   This module interpolates the input par curve onto that finer grid using
   bond_pricing.curve_yield_at() -- THE SAME straight-line-interpolate /
   flat-extrapolate function price_bond already uses for every cash flow
   -- rather than inventing a second interpolation rule. Two reasons:
   (a) consistency -- the self-consistency test below reprices the
   original par bonds off this zero curve and expects them back at par;
   that check only isolates a real bootstrap error if the curve-shape
   assumption feeding the bootstrap is the SAME one price_bond itself
   uses elsewhere, not a second, competing model of the curve's shape;
   (b) it's already validated, tested code -- no new interpolation logic
   to get wrong.

   THIS CHOICE IS MATERIAL, not a formality: MOF's own grid has genuine
   10-year gaps in the ultra-long region (10Y -> 20Y -> 30Y -> 40Y).
   Straight-line interpolation assumes zero curvature across each such
   gap -- it cannot see, and will not reproduce, any real hump or bow the
   true curve has between quoted points, so the bootstrapped zero curve's
   20Y-40Y shape is only as good as that assumption. A curvature-aware
   method (e.g. a cubic spline, or the parametric fit in Part B) would
   shape that region differently; Part B's Nelson-Siegel/Svensson fit is
   this project's principled way of asking whether the data actually
   supports curvature there (docs/phase_4_5b_documentation.md).

2. SHORT END (< 1 year): used directly as zero rates, not bootstrapped.
   Money-market instruments (MOF's 1M/3M/6M tenors) pay no intermediate
   coupon -- there is nothing to bootstrap through. Any input tenor below
   MONEY_MARKET_CUTOFF_YEARS is carried into the output as its own zero
   rate, unchanged. This is not a bolted-on special case: the bootstrap
   recursion below, run at the very FIRST semiannual grid point (0.5Y,
   with no prior period to net out), reduces algebraically to exactly
   this same assignment (zero_rate == the quoted par yield there) --
   checked directly in tests/test_bootstrap.py rather than merely
   asserted. It also matches the convention price_bond itself already
   uses: Phase 2B discounts a single cash flow directly off the curve's
   own quoted rate at that maturity (docs/phase_2b_documentation.md
   §1.1/§3.2) -- for a single cash flow with no earlier coupon, a "par
   yield" and a "zero yield" are the same number under that convention.

3. COMPOUNDING: semiannual (freq=2, parameterized, not hardcoded), matching
   bond_pricing.price_bond's own default exactly. A mismatch between the
   compounding convention used to BUILD this zero curve and the one used
   to discount against it later would be a silent, hard-to-find error --
   called out explicitly here because it is exactly the class of bug the
   self-consistency test below exists to catch.

SELF-CONSISTENCY TEST (tests/test_bootstrap.py) -- the critical one: build
one par bond per curve-quoted tenor (coupon = that tenor's own quoted
rate, TREATED as a par yield -- see above), then reprice it by
discounting its cash flows off THIS module's bootstrapped zero curve
instead. Every one must come back to face value (100) to a tight
tolerance. Because every curve-quoted tenor in this project's real data is
a whole or half year, its cash-flow times land EXACTLY on the semiannual
bootstrap grid -- so this is not an approximate check that could pass by
being "close enough": the equation used to SOLVE the discount factor at a
curve-quoted tenor IS that same bond's own par-pricing equation. A failure
here would mean the bootstrap arithmetic itself is wrong, not a rounding
or interpolation artifact.

WHAT THAT TEST DOES NOT SHOW -- THE KNOCK-ON. This is a test of INTERNAL
CONSISTENCY: given the par-curve assumption as an input, does the
bootstrap arithmetic correctly invert it? It passes by construction for
any input treated as a par curve, correct or not, because the discount
factors were themselves solved FROM that same assumption -- it cannot,
even in principle, detect that the assumption is a simplification of what
MOF actually publishes. That is a separate question, about INPUT FIDELITY
rather than arithmetic correctness, and it is what
coupon_effect_sensitivity() and docs/phase_4_5a_documentation.md's
sensitivity results address instead. Do not read the self-consistency
test passing as validation of the par-curve assumption itself.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import curve_yield_at

# Semiannual -- matches bond_pricing.price_bond's own default. See module
# docstring, design decision 3: this MUST match whatever freq price_bond
# is later called with against this zero curve.
DEFAULT_BOOTSTRAP_FREQ = 2

# A cash flow maturity below this is a money-market instrument (no
# intermediate coupon), used directly as a zero rate rather than
# bootstrapped -- see module docstring, design decision 2.
MONEY_MARKET_CUTOFF_YEARS = 1.0


def _semiannual_grid(max_maturity_years: float, freq: int) -> np.ndarray:
    """Every coupon date from one period out to the curve's own longest
    maturity: 1/freq, 2/freq, ..., n_periods/freq. n_periods is rounded to
    the nearest whole period the same way price_bond does, so the grid's
    last point matches the curve's actual longest quoted tenor even when
    that tenor isn't an exact multiple of 1/freq."""
    n_periods = max(1, round(max_maturity_years * freq))
    return np.arange(1, n_periods + 1) / freq


def bootstrap_zero_curve(
    par_curve: pd.DataFrame,
    freq: int = DEFAULT_BOOTSTRAP_FREQ,
) -> pd.DataFrame:
    """Bootstrap a zero-coupon (spot) curve, TREATING the input curve as a
    par yield curve -- see module docstring for what MOF's curve actually
    is (a fitted YTM series on real benchmark issues) and why treating it
    as par is a deliberate, quantified simplification rather than a fact
    about the data.

    Parameters
    ----------
    par_curve : a curve DataFrame, columns [maturity_years, yield] (yield
        decimal) -- the same shape load_jgb_curve() returns and
        price_bond() consumes, TREATED as a par curve by this function
        (module docstring). Any tenor grid, any number of rows >= 1 -- no
        assumption is made about which maturities it quotes (same
        principle every other module in this project already follows).
    freq : coupon frequency per year, used both to build the bootstrap
        grid and to compound the resulting zero rates. Must match whatever
        `freq` a caller later discounts against this same zero curve with
        (module docstring, design decision 3).

    Returns
    -------
    pd.DataFrame, columns [maturity_years, discount_factor, zero_rate],
    sorted ascending by maturity_years:
      - one row per semiannual grid point from 1/freq out to the input
        curve's own longest maturity (bootstrapped);
      - plus one row for each input tenor below MONEY_MARKET_CUTOFF_YEARS
        that doesn't already coincide with a grid point (carried through
        directly -- design decision 2).

    discount_factor : DF(t) such that a currency unit paid at t is worth
    DF(t) today. zero_rate : the freq-compounded spot rate implied by that
    discount factor, decimal.

    Raises
    ------
    ValueError : par_curve is empty, or freq is not positive.
    """
    if par_curve.empty:
        raise ValueError("par_curve is empty -- cannot bootstrap from it")
    if freq <= 0:
        raise ValueError(f"freq must be positive, got {freq}")

    curve_sorted = par_curve.sort_values("maturity_years").reset_index(drop=True)
    max_maturity = float(curve_sorted["maturity_years"].max())

    grid = _semiannual_grid(max_maturity, freq)
    grid_par_yields = curve_yield_at(curve_sorted, grid)

    # Bootstrap recursion: DF_n solves this maturity's own par-pricing
    # equation, given every earlier grid point's DF (cumulative_df) already
    # solved. See module docstring for the algebra and why n=1 (the first,
    # sub-1-year point) collapses to design decision 2's short-end rule.
    discount_factors = np.empty(len(grid))
    cumulative_df = 0.0
    for n in range(len(grid)):
        coupon = grid_par_yields[n] / freq
        discount_factors[n] = (1.0 - coupon * cumulative_df) / (1.0 + coupon)
        cumulative_df += discount_factors[n]

    zero_rates = freq * (discount_factors ** (-1.0 / (freq * grid)) - 1.0)

    bootstrapped = pd.DataFrame(
        {"maturity_years": grid, "discount_factor": discount_factors, "zero_rate": zero_rates}
    )

    money_market = curve_sorted.loc[curve_sorted["maturity_years"] < MONEY_MARKET_CUTOFF_YEARS]
    money_market = money_market.loc[~money_market["maturity_years"].isin(grid)]
    if not money_market.empty:
        mm_maturities = money_market["maturity_years"].to_numpy()
        mm_yields = money_market["yield"].to_numpy()
        money_market = pd.DataFrame(
            {
                "maturity_years": mm_maturities,
                "discount_factor": (1.0 + mm_yields / freq) ** (-freq * mm_maturities),
                # Direct assignment, not a computation -- design decision 2.
                "zero_rate": mm_yields,
            }
        )
        result = pd.concat([money_market, bootstrapped], ignore_index=True)
    else:
        result = bootstrapped

    return result.sort_values("maturity_years").reset_index(drop=True)


def zero_rate_at(zero_curve: pd.DataFrame, maturity_years):
    """Interpolate (or extrapolate) zero_curve['zero_rate'] to an
    arbitrary maturity -- the same straight-line-interpolate /
    flat-extrapolate policy as bond_pricing.curve_yield_at, applied to the
    zero curve instead of the par curve, for the same reason: no
    assumption about which maturities the zero curve happens to have rows
    at. maturity_years may be a scalar or array-like.
    """
    curve_sorted = zero_curve.sort_values("maturity_years")
    tenors = curve_sorted["maturity_years"].to_numpy()
    rates = curve_sorted["zero_rate"].to_numpy()
    return np.interp(maturity_years, tenors, rates, left=rates[0], right=rates[-1])


def discount_factor_at(
    zero_curve: pd.DataFrame,
    maturity_years,
    freq: int = DEFAULT_BOOTSTRAP_FREQ,
):
    """Discount factor for an arbitrary maturity_years, computed from the
    INTERPOLATED zero rate (zero_rate_at) and freq-compounding -- not by
    interpolating the discount_factor column directly. Keeps this
    consistent with how a future zero-curve-based price_bond path (Part C)
    would look up a rate for a cash flow that doesn't land exactly on the
    zero curve's own grid: one interpolation rule (on the rate), not two.
    """
    z = zero_rate_at(zero_curve, maturity_years)
    t = np.asarray(maturity_years, dtype=float)
    return (1.0 + z / freq) ** (-freq * t)


def price_via_zero_curve(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    zero_curve: pd.DataFrame,
    freq: int = DEFAULT_BOOTSTRAP_FREQ,
) -> float:
    """Price a bond by discounting each cash flow off a bootstrapped ZERO
    curve (via discount_factor_at) instead of price_bond's par-curve
    lookup. Same cash-flow-schedule construction as
    bond_pricing.price_bond (built backward from maturity_years, freq
    payments per year) -- only the discounting source differs. Used by
    the self-consistency check and by coupon_effect_sensitivity below;
    exists as a named function rather than inlined so both share one
    implementation.
    """
    n = max(1, round(maturity_years * freq))
    times = maturity_years - (n - np.arange(1, n + 1)) / freq
    cash_flows = np.full(n, face_value * coupon_rate / freq)
    cash_flows[-1] += face_value
    dfs = discount_factor_at(zero_curve, times, freq=freq)
    return float(np.sum(cash_flows * dfs))


def implied_ytm(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    target_price: float,
    freq: int = DEFAULT_BOOTSTRAP_FREQ,
    tol: float = 1e-10,
    max_iter: int = 100,
) -> float:
    """The single flat yield that, used as bond_pricing.price_bond's
    (flat, one-row) discounting curve, reprices this bond to
    target_price -- the standard definition of a bond's own
    yield-to-maturity. Solved by bisection (bond price is strictly
    decreasing in yield, so the root is unique and bracketed) rather than
    a gradient-based method -- simple, dependency-free (no scipy in this
    project, see docs/phase_4b_documentation.md §1.2 for the same
    no-heavy-dependency-for-one-use reasoning), and robust for the modest
    precision this comparison needs.

    Reuses price_bond itself as the function being inverted -- not a
    second, hand-rolled pricing formula -- so this is guaranteed
    consistent with how every other bond price in this project is
    computed.
    """
    from models.bond_pricing import price_bond  # local import: avoids a

    # module-level cycle (bond_pricing doesn't import bootstrap, but this
    # keeps the dependency direction explicit and one-way).

    def _price_at_flat_yield(y: float) -> float:
        flat_curve = pd.DataFrame({"maturity_years": [0.25, maturity_years + 1.0], "yield": [y, y]})
        return price_bond(face_value, coupon_rate, maturity_years, flat_curve, freq=freq)

    lo, hi = -0.02, 0.30  # wide enough for any plausible JGB yield, including negative-rate years
    f_lo, f_hi = _price_at_flat_yield(lo) - target_price, _price_at_flat_yield(hi) - target_price
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        raise ValueError(
            f"implied_ytm: target_price {target_price} is not bracketed by yields in [{lo}, {hi}] "
            f"for this bond -- price at {lo:.2%} = {f_lo + target_price:.4f}, "
            f"price at {hi:.2%} = {f_hi + target_price:.4f}"
        )

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = _price_at_flat_yield(mid) - target_price
        if abs(f_mid) < tol:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi, f_hi = mid, f_mid
    return 0.5 * (lo + hi)


# The "high coupon" scenario in coupon_effect_sensitivity: a multiple of
# the input tenor's own (par-treated) rate, standing in for an older,
# premium-priced JGB. An arbitrary but round, reproducible choice -- not
# fitted to any real issue, since this project has no real-issue price
# data yet (see module docstring, "the principled alternative").
DEFAULT_HIGH_COUPON_MULTIPLE = 2.0


def coupon_effect_sensitivity(
    par_curve: pd.DataFrame,
    freq: int = DEFAULT_BOOTSTRAP_FREQ,
    high_coupon_multiple: float = DEFAULT_HIGH_COUPON_MULTIPLE,
) -> pd.DataFrame:
    """Quantify the coupon-effect error from treating par_curve as a par
    curve (module docstring) -- for each input tenor at or above
    MONEY_MARKET_CUTOFF_YEARS, price a hypothetical ZERO-COUPON bond and a
    hypothetical HIGH-COUPON bond (coupon = high_coupon_multiple times the
    input rate) of that SAME maturity off the SAME bootstrapped zero
    curve, solve each one's own implied YTM (implied_ytm), and compare
    both against the input rate at that tenor.

    A real bond trading at that maturity but away from the assumed par
    coupon would show a YTM gap of roughly this size and direction, purely
    from the coupon effect -- not from anything wrong with the curve
    construction. Zero-coupon JGBs are not hypothetical here: many
    outstanding issues from the 2016-2024 ZIRP/YCC era carry near-zero
    coupons and trade at deep discounts today (module docstring).

    Returns
    -------
    pd.DataFrame, one row per par_curve tenor >= MONEY_MARKET_CUTOFF_YEARS,
    columns [maturity_years, input_rate, zero_coupon_ytm,
    zero_coupon_gap_bp, high_coupon_ytm, high_coupon_gap_bp]. Gaps are
    (scenario_ytm - input_rate) * 10000, in basis points.
    """
    zero_curve = bootstrap_zero_curve(par_curve, freq=freq)
    curve_sorted = par_curve.sort_values("maturity_years").reset_index(drop=True)
    eligible = curve_sorted.loc[curve_sorted["maturity_years"] >= MONEY_MARKET_CUTOFF_YEARS]

    rows = []
    for _, row in eligible.iterrows():
        maturity = float(row["maturity_years"])
        input_rate = float(row["yield"])

        zero_coupon_price = price_via_zero_curve(100.0, 0.0, maturity, zero_curve, freq=freq)
        zero_coupon_ytm = implied_ytm(100.0, 0.0, maturity, zero_coupon_price, freq=freq)

        high_coupon = high_coupon_multiple * input_rate
        high_coupon_price = price_via_zero_curve(100.0, high_coupon, maturity, zero_curve, freq=freq)
        high_coupon_ytm = implied_ytm(100.0, high_coupon, maturity, high_coupon_price, freq=freq)

        rows.append(
            {
                "maturity_years": maturity,
                "input_rate": input_rate,
                "zero_coupon_ytm": zero_coupon_ytm,
                "zero_coupon_gap_bp": (zero_coupon_ytm - input_rate) * 10000.0,
                "high_coupon_ytm": high_coupon_ytm,
                "high_coupon_gap_bp": (high_coupon_ytm - input_rate) * 10000.0,
            }
        )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    curve = load_jgb_curve()
    zero_curve = bootstrap_zero_curve(curve)

    print()
    print("Par curve vs. bootstrapped zero curve, at the input curve's own tenors:")
    comparison = curve.sort_values("maturity_years").copy()
    comparison["zero_rate"] = zero_rate_at(zero_curve, comparison["maturity_years"].to_numpy())
    comparison["zero_minus_par_bp"] = (comparison["zero_rate"] - comparison["yield"]) * 10000
    print(
        comparison.rename(columns={"yield": "par_yield"}).to_string(
            index=False,
            formatters={
                "par_yield": lambda y: f"{y:.4%}",
                "zero_rate": lambda y: f"{y:.4%}",
                "zero_minus_par_bp": lambda b: f"{b:+.2f}bp",
            },
        )
    )

    print()
    print("Full bootstrapped zero curve (semiannual grid + sub-1Y money-market points):")
    print(
        zero_curve.to_string(
            index=False,
            formatters={
                "discount_factor": lambda d: f"{d:.6f}",
                "zero_rate": lambda z: f"{z:.4%}",
            },
        )
    )

    print()
    print("=" * 78)
    print("SELF-CONSISTENCY CHECK -- repricing the original par bonds off the")
    print("bootstrapped zero curve (each should reprice back to ~100)")
    print("=" * 78)
    freq = DEFAULT_BOOTSTRAP_FREQ
    for _, row in curve.sort_values("maturity_years").iterrows():
        maturity = float(row["maturity_years"])
        if maturity < MONEY_MARKET_CUTOFF_YEARS:
            continue  # a money-market "bond" has one cash flow -- not an interesting reprice check
        coupon = float(row["yield"])  # priced at its own curve yield -> par, by definition
        price = price_via_zero_curve(100.0, coupon, maturity, zero_curve, freq=freq)
        print(f"  {maturity:6.3f}Y  coupon={coupon:.4%}  reprice = {price:10.6f}  gap = {price - 100.0:+.2e}")
    print()
    print("(This shows the bootstrap arithmetic is internally consistent with its own")
    print(" par-curve assumption -- it does NOT show that assumption matches what MOF")
    print(" actually publishes. See the coupon-effect check below for that.)")

    print()
    print("=" * 78)
    print("COUPON-EFFECT SENSITIVITY -- how far a real bond's YTM could plausibly")
    print("diverge from this module's assumed 'par rate', purely from its own coupon")
    print("(MOF publishes a fitted YTM curve on real benchmark issues, not a par")
    print(" curve -- see module docstring for the verified source methodology)")
    print("=" * 78)
    sensitivity = coupon_effect_sensitivity(curve)
    print(
        sensitivity.to_string(
            index=False,
            formatters={
                "input_rate": lambda y: f"{y:.4%}",
                "zero_coupon_ytm": lambda y: f"{y:.4%}",
                "zero_coupon_gap_bp": lambda b: f"{b:+7.2f}bp",
                "high_coupon_ytm": lambda y: f"{y:.4%}",
                "high_coupon_gap_bp": lambda b: f"{b:+7.2f}bp",
            },
        )
    )
