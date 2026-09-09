"""
Tests for models/day_count.py.

Covers: ACT/365 counts real calendar days (no 30/360-style approximation);
year_fraction() uses a FIXED /365 denominator, including across a leap
year (the specific behavior that distinguishes "Actual/365 Fixed" from
"Actual/Actual (ISDA)"); and basic input validation.
"""

from __future__ import annotations

from datetime import date

import pytest

from models.day_count import ACT_365, day_count, year_fraction


# --------------------------------------------------------------------------
# day_count(): actual calendar days
# --------------------------------------------------------------------------


def test_day_count_counts_actual_calendar_days():
    assert day_count(date(2026, 1, 1), date(2026, 1, 31)) == 30


def test_day_count_across_a_month_boundary_uses_the_real_month_length():
    # February 2026 has 28 days (not a leap year) -- ACT/365 must reflect
    # that real length, not a 30-day approximation.
    assert day_count(date(2026, 2, 1), date(2026, 3, 1)) == 28


def test_day_count_is_zero_for_the_same_date():
    assert day_count(date(2026, 6, 15), date(2026, 6, 15)) == 0


def test_day_count_rejects_end_before_start():
    with pytest.raises(ValueError, match="precedes"):
        day_count(date(2026, 6, 15), date(2026, 6, 1))


def test_day_count_rejects_an_unsupported_convention():
    with pytest.raises(ValueError, match="Unsupported day count convention"):
        day_count(date(2026, 1, 1), date(2026, 2, 1), convention="30/360")


# --------------------------------------------------------------------------
# year_fraction(): ACT/365 Fixed -- a constant /365 denominator, including
# across a leap year (the hand-computed case the phase brief asks for).
# --------------------------------------------------------------------------


def test_year_fraction_of_a_half_year_span():
    # 2026 is not a leap year: Jan 1 -> Jul 1 is 181 actual days.
    assert year_fraction(date(2026, 1, 1), date(2026, 7, 1)) == pytest.approx(181 / 365)


def test_year_fraction_across_a_leap_year_uses_366_actual_days_over_a_fixed_365():
    # 2024 IS a leap year (confirmed: divisible by 4, not a century
    # exception) -- Jan 1 2024 -> Jan 1 2025 spans 366 actual calendar
    # days. ACT/365 Fixed divides by a CONSTANT 365 regardless, so the
    # resulting year fraction is 366/365 (~1.00274), strictly ABOVE 1.0 --
    # not exactly 1.0, which is what distinguishes this convention (the one
    # JGBs use) from Actual/Actual (ISDA), which would give exactly 1.0
    # here via its 366-denominator leap-year branch.
    start, end = date(2024, 1, 1), date(2025, 1, 1)
    assert (end - start).days == 366  # hand-computed day count, checked directly
    assert year_fraction(start, end) == pytest.approx(366 / 365)
    assert year_fraction(start, end) > 1.0


def test_year_fraction_default_convention_is_act_365():
    assert year_fraction(date(2026, 1, 1), date(2026, 4, 1)) == pytest.approx(
        year_fraction(date(2026, 1, 1), date(2026, 4, 1), convention=ACT_365)
    )
