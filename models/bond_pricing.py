"""
bond_pricing.py

Curve-based bond pricing: values a bond as the sum of its cash flows, each
discounted at the rate the curve implies for THAT cash flow's own maturity
-- not one flat rate applied to the whole bond.

TWO DISCOUNTING BASES, AUTO-DETECTED FROM THE CURVE'S OWN COLUMNS (Phase
4.5C addition). This project carried a named simplification from Phase 2B
through Phase 4C: every price/KRD/DV01 number discounted off the curve's
own quoted PAR-like rate directly, never a genuinely bootstrapped
zero-coupon rate (docs/phase_2b_documentation.md §3.2, resolved in part by
Phase 4.5A -- docs/phase_4_5a_documentation.md). That zero curve now
exists (models.bootstrap.bootstrap_zero_curve); this module can discount
against it directly:

  - PAR basis (the original, default, UNCHANGED behavior): `curve` has a
    'yield' column. Each cash flow's rate comes from curve_yield_at(),
    which interpolates between the curve's own tenor points and
    extrapolates FLAT beyond them (see its docstring):

        Price = sum_i [ CF_i / (1 + y(t_i)/freq)^(freq * t_i) ]

  - ZERO basis (new): `curve` has a 'zero_rate' column instead -- i.e. a
    models.bootstrap.bootstrap_zero_curve() result, passed in exactly as
    returned, no wrapper needed. Each cash flow's discount factor comes
    from models.bootstrap.discount_factor_at() (interpolated zero rate,
    freq-compounded) instead:

        Price = sum_i [ CF_i * DF(t_i) ]

  price_bond() picks the basis by inspecting `curve.columns` -- not a new
  parameter. This means EVERY existing caller (this module's own
  price_portfolio, and every downstream module -- key_rate_duration.py,
  dv01.py, ultra_long_profile.py, factor_exposure.py) already supports
  zero-curve discounting automatically, with NO code changes of their own,
  simply by being handed a bootstrap_zero_curve() result instead of a par
  curve -- because every one of them already treats `curve` as an opaque
  DataFrame it passes through, per this project's own long-standing
  "no assumption about what the curve looks like" principle
  (docs/phase_1_documentation.md §3.5). key_rate_duration.py's bump helper
  needed one small, backward-compatible generalization (a rate-COLUMN
  lookup instead of a hardcoded 'yield' literal) to bump either shape;
  bond_pricing.py and dv01.py needed no changes beyond this function
  itself. See docs/phase_4_5c_documentation.md §1 for the full account,
  including why this is column-detection rather than a new parameter, and
  the CRITICAL constraint it was built under: every existing test for
  price_bond/price_portfolio/KRD/DV01 passes UNCHANGED, because a
  'yield'-column curve takes the exact, untouched original code path.

Reads the portfolio and curve through their own loaders -- never hardcodes
either. Cheap to call repeatedly against a modified curve on purpose:
KRD, DV01, and the ultra-long profile all work by bumping one curve row
and calling price_bond again, many times per bond, so this function does
no per-call setup beyond building one bond's cash flow schedule.

SETTLEMENT, DAY COUNT, CLEAN/DIRTY PRICE (Phase 4.6A addition). price_bond
itself is UNCHANGED -- signature and behavior both -- and continues to
return what this project now documents explicitly as the CLEAN price: the
sum of discounted future cash flows, on the implicit assumption (built
into the cash-flow schedule below) that "now" is exactly a coupon date, so
zero interest has yet accrued. Two new functions build on it without
touching it: accrued_interest() (coupon prorated by elapsed days over days
in the current coupon period, per a day count convention -- ACT/365 for
JGBs, models/day_count.py) and dirty_price() (= price_bond() +
accrued_interest(), what settlement actually pays). See
docs/phase_4_6a_documentation.md for why settlement/accrued live in two
new functions rather than as new parameters on price_bond itself, and for
the coupon-date edge cases (on a coupon date, before a bond's first
coupon, and the ex-coupon question) handled explicitly there.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from config.portfolio_loader import DAYS_PER_YEAR, Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models import day_count


# PAR basis only -- the zero-basis equivalent is
# models.bootstrap.discount_factor_at(), dispatched to from
# discount_factors_at() below rather than living in this file.
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


def cash_flow_schedule(maturity_years: float, freq: int) -> tuple[int, np.ndarray]:
    """The coupon schedule price_bond itself prices against: n_periods
    payments, spaced 1/freq years apart, counted BACKWARD from maturity so
    the final one lands exactly there (module docstring). Factored out of
    price_bond unchanged (Phase 4.6A) so accrued_interest() can build real
    calendar coupon dates from the EXACT same periods a bond is actually
    priced on, rather than a second, independently-derived schedule that
    could drift out of sync with it.

    max(1, ...) guards an extremely short maturity from producing an empty
    schedule (pricing to 0) instead of one terminal payment.
    """
    n_periods = max(1, round(maturity_years * freq))
    period_index = np.arange(1, n_periods + 1)
    cash_flow_times = maturity_years - (n_periods - period_index) / freq
    return n_periods, cash_flow_times


def discount_factors_at(curve: pd.DataFrame, times, freq: int = 2) -> np.ndarray:
    """Discount factor(s) for arbitrary time(s), auto-detected from
    `curve`'s own columns -- exactly price_bond's own "TWO DISCOUNTING
    BASES" logic (module docstring), factored out here (Phase 4.6C) so any
    OTHER module needing a bond's own per-cash-flow discount factors (e.g.
    models/cash_flow_ladder.py, present-valuing each payment) shares this
    SAME logic rather than re-deriving it a third time.

    Always returns a TRUE discount factor -- i.e. `price = cash_flow *
    discount_factor` for either basis, never a divide-by for one and a
    multiply-by for the other. This is a genuine (if small) restructuring
    of price_bond's own inline branch, not a copy of it: the original par-
    basis code computed `(1+y/freq)**(freq*t)` and DIVIDED cash flows by
    it; here that same quantity is inverted once so callers on both bases
    always just multiply. price_bond itself was updated to call this and
    multiply -- confirmed, via the full pre-existing test suite passing
    unchanged, to produce bit-for-bit the same prices as before.
    """
    if "zero_rate" in curve.columns:
        # Local import: avoids a module-level circular import, since
        # models.bootstrap itself imports curve_yield_at from this module
        # -- the same deferred-import pattern models.bootstrap.implied_ytm
        # already uses for the reverse direction.
        from models.bootstrap import discount_factor_at

        return discount_factor_at(curve, times, freq=freq)

    yields_at_times = curve_yield_at(curve, times)
    return 1.0 / (1.0 + yields_at_times / freq) ** (freq * np.asarray(times))


def price_bond(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
) -> float:
    """Price a bond by discounting each cash flow at the rate the curve
    implies for that cash flow's own maturity.

    Cash flow schedule: `freq` payments per year, built BACKWARD from
    maturity_years so the final payment lands exactly there. A maturity
    that isn't an exact multiple of 1/freq (e.g. derived from a real
    redemption date) just gets one shorter first period, not an error.

    Parameters
    ----------
    face_value : redemption amount, must be > 0.
    coupon_rate : annual coupon, decimal (0.02 == 2%). May be 0.
    maturity_years : years to maturity, must be > 0.
    curve : EITHER a par-yield curve, columns [maturity_years, yield]
        (yield decimal) -- the original, default basis, discounted via
        curve_yield_at() -- OR a bootstrapped zero curve, columns
        [maturity_years, zero_rate] (a models.bootstrap.
        bootstrap_zero_curve() result, passed straight through),
        discounted via models.bootstrap.discount_factor_at() instead. The
        basis is auto-detected from which column is present (module
        docstring "TWO DISCOUNTING BASES") -- any tenor grid, any number
        of rows >= 1, same as before either way.
    freq : coupon payments per year (2 = semiannual, the JGB default).
        For the zero-curve basis, MUST match the freq the zero curve was
        itself bootstrapped with (docs/phase_4_5a_documentation.md §1.4).

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

    n_periods, cash_flow_times = cash_flow_schedule(maturity_years, freq)

    coupon_payment = face_value * coupon_rate / freq
    cash_flows = np.full(n_periods, coupon_payment, dtype=float)
    cash_flows[-1] += face_value  # final period also redeems face value

    discount_factors = discount_factors_at(curve, cash_flow_times, freq=freq)
    return float(np.sum(cash_flows * discount_factors))


def price_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int | None = None,
) -> pd.DataFrame:
    """Price every bond in `portfolio` against one `curve`.

    Takes an already-loaded portfolio and curve rather than loading them
    itself -- no hidden file/network I/O, so this is easy to test and
    reusable against a curve a caller repeatedly bumps and reprices.

    freq : None (default) prices each bond at its OWN bond.freq -- a
        portfolio can mix payment frequencies across bonds. Pass an
        explicit int to override every bond in the portfolio to that one
        shared frequency instead -- needed, for instance, when discounting
        against a zero curve, which requires the SAME freq it was
        bootstrapped with for every bond priced against it (see
        models.zero_curve_impact's own docstring).

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
                bond.face_value, bond.coupon_rate, bond.maturity_years, curve,
                freq=freq if freq is not None else bond.freq,
            ),
        }
        for bond in portfolio
    ]
    return pd.DataFrame(rows, columns=["name", "maturity_years", "coupon_rate", "weight", "price"])


def _resolve_date(value: str | date | None) -> date:
    """Normalize a settlement/valuation date argument: None -> today, a
    date is passed through, a str is parsed as ISO "YYYY-MM-DD". Mirrors
    config.portfolio_loader's own _resolve_valuation_date (same rules, same
    reason: a pinned run stays reproducible) -- reimplemented locally,
    rather than importing that module's private helper, to keep this
    module's dependency on Phase 2A limited to its public load_portfolio()
    contract."""
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _coupon_boundaries(maturity_years: float, valuation_date: date, freq: int) -> list[date]:
    """Real calendar coupon-period boundary dates for a bond's schedule,
    anchored so that `valuation_date` itself is boundaries[0] -- i.e. "now"
    is treated as exactly a coupon date (the same assumption price_bond's
    own schedule already makes implicitly, module docstring). boundaries[i]
    for i >= 1 is the calendar date of price_bond's i-th cash flow;
    boundaries[-1] is the bond's maturity date.

    Built from the EXACT SAME cash_flow_schedule() price_bond itself uses
    -- accrued interest is always asking about the periods a bond is
    actually priced on, never a second, independently-derived calendar.

    Each cash-flow time (a year offset from valuation_date) is converted to
    a calendar date via DAYS_PER_YEAR (365.25, config.portfolio_loader) --
    the same calendar-approximation constant Phase 2A already uses to turn
    a real maturity_date into years, reused here for the inverse direction,
    rather than a second, differently-tuned approximation. This is a
    modeling simplification, not a claim that real JGB coupon dates fall
    exactly 182 or 183 days apart -- see docs/phase_4_6a_documentation.md
    §1 for what this costs and why it doesn't matter for this project's
    purpose (there are no real per-bond calendar dates to be more precise
    than, since the illustrative portfolio only ever gives maturity_years).
    """
    _, cash_flow_times = cash_flow_schedule(maturity_years, freq)
    return [valuation_date] + [
        valuation_date + timedelta(days=round(t * DAYS_PER_YEAR)) for t in cash_flow_times
    ]


def accrued_interest(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    freq: int = 2,
    valuation_date: str | date | None = None,
    settlement_date: str | date | None = None,
    day_count_convention: str = day_count.ACT_365,
) -> float:
    """Interest accrued on a bond between its current coupon period's start
    and `settlement_date` -- the amount added to the CLEAN price
    (price_bond()) to get the DIRTY price (dirty_price(), below).

    Formula: (coupon per period) x (days elapsed since the period started)
    / (days in the period), both day counts taken under
    `day_count_convention` (default ACT/365 -- JGBs,
    docs/phase_4_6a_documentation.md §1). This is the standard bond-market
    accrual formula, not a year-fraction-of-the-whole-coupon-rate
    computation -- see models/day_count.py's own docstring for why those
    two are different things and this function deliberately uses the
    former.

    Parameters
    ----------
    valuation_date : the date maturity_years is measured FROM -- i.e. the
        same date passed to config.portfolio_loader.load_portfolio()'s own
        `valuation_date` to resolve that maturity_years in the first place.
        Defaults to today. Passing a mismatched valuation_date here (one
        that isn't what the portfolio's maturity_years was actually
        resolved against) desyncs this function's calendar from the
        portfolio's own -- a caller mixing the two should pass the same
        value to both.
    settlement_date : the date accrued interest is measured AS OF -- when
        cash actually changes hands, typically a few business days after
        valuation_date. Defaults to valuation_date (the phase brief's own
        default), which reproduces accrued_interest() == 0.0 exactly, since
        the cash-flow schedule already treats valuation_date as a coupon
        date (module docstring) -- there is nothing yet to accrue at time
        zero. Must be on or after valuation_date, and on or before the
        bond's own maturity date.

    Coupon-date edge cases, handled explicitly (docs/phase_4_6a_documentation.md
    §2 has the full account, including the one edge case -- an ex-coupon
    period -- this project could NOT verify from a primary source):

      - settlement_date exactly ON a coupon date (including valuation_date
        itself, i.e. the default): returns 0.0. Coupon periods are treated
        as half-open ([period_start, period_end)) specifically so landing
        exactly on a boundary always means "a fresh period just started",
        never "the previous period just ended" -- the two would otherwise
        disagree by one full period's coupon.
      - settlement_date before the bond's first coupon (a newly-issued
        bond that hasn't paid one yet): this is period 0
        ([valuation_date, first coupon date)) -- handled by the same
        general logic as any other period, not a separate branch, since
        valuation_date is ALWAYS before the first coupon in this project's
        schedule (module docstring).
      - an ex-coupon / ex-dividend period (a short window immediately
        before a coupon date during which the seller, not the buyer, keeps
        that coupon): NOT implemented. This project could not verify from
        a primary MOF/JSDA source whether JGBs use one. The assumption
        made instead -- stated here rather than silently picked -- is that
        JGBs trade cum-coupon throughout the period (no ex-coupon window),
        consistent with settlement via Japan's dematerialized, real-time
        DVP book-entry system (no paper-registrar "books closed" period to
        motivate one) and with most other electronically-settled sovereign
        bond markets. `docs/phase_4_6a_documentation.md` §2 records exactly
        what was checked and why this couldn't be confirmed outright.
    """
    if face_value <= 0:
        raise ValueError(f"face_value must be positive, got {face_value}")
    if maturity_years <= 0:
        raise ValueError(f"maturity_years must be positive, got {maturity_years}")
    if freq <= 0:
        raise ValueError(f"freq must be positive, got {freq}")

    valuation = _resolve_date(valuation_date)
    settlement = _resolve_date(settlement_date) if settlement_date is not None else valuation
    if settlement < valuation:
        raise ValueError(
            f"settlement_date {settlement} precedes valuation_date {valuation}"
        )

    boundaries = _coupon_boundaries(maturity_years, valuation, freq)
    if settlement > boundaries[-1]:
        raise ValueError(
            f"settlement_date {settlement} is after the bond's maturity date {boundaries[-1]}"
        )
    if settlement == boundaries[-1]:
        return 0.0  # redemption day itself -- the final period has just closed

    for period_start, period_end in zip(boundaries[:-1], boundaries[1:]):
        if period_start <= settlement < period_end:
            break
    else:  # pragma: no cover -- unreachable: the two checks above bracket every case
        raise AssertionError("settlement_date fell outside every coupon period")

    days_elapsed = day_count.day_count(period_start, settlement, day_count_convention)
    days_in_period = day_count.day_count(period_start, period_end, day_count_convention)
    coupon_per_period = face_value * coupon_rate / freq
    return coupon_per_period * (days_elapsed / days_in_period)


def dirty_price(
    face_value: float,
    coupon_rate: float,
    maturity_years: float,
    curve: pd.DataFrame,
    freq: int = 2,
    valuation_date: str | date | None = None,
    settlement_date: str | date | None = None,
    day_count_convention: str = day_count.ACT_365,
) -> float:
    """Dirty price: what settlement actually pays. = price_bond() (the
    quoted, clean price) + accrued_interest() (this module's own two new
    functions, both above) -- computed by calling each independently and
    adding the results, not by re-deriving either inside this function."""
    clean = price_bond(face_value, coupon_rate, maturity_years, curve, freq=freq)
    accrued = accrued_interest(
        face_value,
        coupon_rate,
        maturity_years,
        freq=freq,
        valuation_date=valuation_date,
        settlement_date=settlement_date,
        day_count_convention=day_count_convention,
    )
    return clean + accrued


def accrued_interest_portfolio(
    portfolio: list[Bond],
    freq: int | None = None,
    valuation_date: str | date | None = None,
    settlement_date: str | date | None = None,
    day_count_convention: str = day_count.ACT_365,
) -> pd.DataFrame:
    """accrued_interest() for every bond in `portfolio`. Same row shape and
    ordering convention as price_portfolio(): one row per bond, in
    portfolio order, no total row -- a caller weights it the same way,
    (df.weight * df.accrued_interest).sum().

    freq : None (default) uses each bond's OWN bond.freq; an explicit int
    overrides every bond to that one shared frequency (see
    price_portfolio's own docstring for why that override exists)."""
    rows = [
        {
            "name": bond.name,
            "maturity_years": bond.maturity_years,
            "coupon_rate": bond.coupon_rate,
            "weight": bond.weight,
            "accrued_interest": accrued_interest(
                bond.face_value,
                bond.coupon_rate,
                bond.maturity_years,
                freq=freq if freq is not None else bond.freq,
                valuation_date=valuation_date,
                settlement_date=settlement_date,
                day_count_convention=day_count_convention,
            ),
        }
        for bond in portfolio
    ]
    return pd.DataFrame(
        rows, columns=["name", "maturity_years", "coupon_rate", "weight", "accrued_interest"]
    )


def dirty_price_portfolio(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int | None = None,
    valuation_date: str | date | None = None,
    settlement_date: str | date | None = None,
    day_count_convention: str = day_count.ACT_365,
) -> pd.DataFrame:
    """price_portfolio() and accrued_interest_portfolio() combined into one
    table: one row per bond, columns [name, maturity_years, coupon_rate,
    weight, clean_price, accrued_interest, dirty_price]. Computed by
    joining the two existing functions' own output on bond name/order
    (both already iterate `portfolio` in the same order), not by
    reimplementing either calculation.

    freq : forwarded unchanged to both -- None (default) means each bond
    prices and accrues at its own bond.freq; an explicit int overrides
    every bond to that one shared frequency."""
    clean = price_portfolio(portfolio, curve, freq=freq)
    accrued = accrued_interest_portfolio(
        portfolio,
        freq=freq,
        valuation_date=valuation_date,
        settlement_date=settlement_date,
        day_count_convention=day_count_convention,
    )
    result = clean.rename(columns={"price": "clean_price"})
    result["accrued_interest"] = accrued["accrued_interest"]
    result["dirty_price"] = result["clean_price"] + result["accrued_interest"]
    return result


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

    print()
    print("Clean vs. dirty price, settled 10 days after valuation (per 100 face value):")
    dirty_results = dirty_price_portfolio(
        portfolio, curve, settlement_date=date.today() + timedelta(days=10)
    )
    print(
        dirty_results.to_string(
            index=False,
            formatters={
                "coupon_rate": lambda c: f"{c:.3%}",
                "weight": lambda w: f"{w:.2%}",
                "clean_price": lambda p: f"{p:.4f}",
                "accrued_interest": lambda a: f"{a:.5f}",
                "dirty_price": lambda p: f"{p:.4f}",
            },
        )
    )
