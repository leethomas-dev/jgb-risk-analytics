"""
day_count.py

Day count convention support for accrued interest (Phase 4.6A). A day
count convention answers two related questions: how many days sit between
two calendar dates, and what year-fraction that span represents. Both
answers depend on the convention -- a 30/360 convention, for instance,
treats every month as having 30 days, so it would count days differently
than this module's ACT/365 does, not just apply a different denominator.

ONLY ACT/365 IS IMPLEMENTED, because it's the only one this project needs
(JGBs use it -- see docs/phase_4_6a_documentation.md §1 for how that was
verified, and the one point that couldn't be verified from a primary
source). The module is still structured as a small registry keyed by a
convention name, specifically so a second convention could be added later
(one function, one registry entry) without changing this module's public
functions or any caller of them -- not because a second convention is
needed now.

Two related but distinct outputs, both used by models/bond_pricing.py's
accrued_interest():

  - day_count(start, end, convention): the ACTUAL number of calendar days
    between two dates under `convention`. For ACT/365 this is just
    (end - start).days -- the "Actual" in the name means real calendar
    days are counted, with no 30-day-month approximation. This is what
    accrued_interest() uses for both "days elapsed since the last coupon"
    and "days in the current coupon period" -- the phase brief's own
    formula (coupon amount x days_elapsed / days_in_period).

  - year_fraction(start, end, convention): ACT/365's own standalone
    definition of a year fraction -- day_count(...) / 365, a FIXED
    denominator that does NOT switch to 366 in a leap year. This is what
    distinguishes "Actual/365 Fixed" (JGBs; also called "Actual/365
    (Japanese)" in some references) from "Actual/Actual (ISDA)", which
    splits a period spanning a leap year into a leap-year portion (/366)
    and a non-leap-year portion (/365). Not used by accrued_interest()
    itself (which prorates within a period, per the brief's formula
    above) -- provided as the convention's own general-purpose year
    fraction, and to make the leap-year behavior directly testable.
"""

from __future__ import annotations

from datetime import date

# The only convention implemented -- see module docstring.
ACT_365 = "ACT/365"

# Each convention's fixed denominator for year_fraction(). Also doubles as
# the registry of supported convention names.
_YEAR_LENGTH_DAYS = {
    ACT_365: 365.0,
}


def _validate_convention(convention: str) -> None:
    if convention not in _YEAR_LENGTH_DAYS:
        raise ValueError(
            f"Unsupported day count convention {convention!r}; supported: "
            f"{sorted(_YEAR_LENGTH_DAYS)}"
        )


def day_count(start: date, end: date, convention: str = ACT_365) -> int:
    """Actual number of calendar days from start to end, under `convention`.

    ACT/365 counts real calendar days -- no special-casing at all, since
    "Actual" is precisely the absence of a 30/360-style approximation. A
    future convention that DOES special-case day counting would add its
    own branch here without changing this function's signature or any
    caller of it.
    """
    _validate_convention(convention)
    if end < start:
        raise ValueError(f"end date {end} precedes start date {start}")
    return (end - start).days


def year_fraction(start: date, end: date, convention: str = ACT_365) -> float:
    """Year fraction from start to end, under `convention`.

    ACT/365: day_count(start, end) / 365 -- a FIXED denominator, never 366,
    even when the span crosses a leap day (module docstring). A leap-year
    span therefore gives a year_fraction slightly ABOVE 1.0 for a
    calendar-year span, not exactly 1.0 -- this is the convention's own
    defined behavior, not an approximation error.
    """
    _validate_convention(convention)
    return day_count(start, end, convention) / _YEAR_LENGTH_DAYS[convention]
