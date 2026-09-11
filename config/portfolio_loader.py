"""
portfolio_loader.py

Single source of truth for the project's bond portfolio. Every downstream
module reads the portfolio through load_portfolio() rather than
hardcoding bond definitions.

Default data: config/portfolio.json.

ILLUSTRATIVE DATA NOTICE: the default portfolio's coupon rates are made
up -- chosen to sit near current tenor yields so each bond prices near
par under the loaded curve. They do NOT correspond to real outstanding
JGB issues.

Output contract: load_portfolio() returns a list[Bond], one entry per
bond, in config-file order. Each Bond has name, maturity_years (always
resolved to a number -- see below), coupon_rate (decimal), face_value,
weight (sums to 1.0 across the portfolio), plus four optional fields
(isin, issue_date, maturity_date, tenor_class) defaulting to None.

SCHEMA FORWARD-COMPATIBILITY (real bonds, later phase): the schema
already accepts real-issue data, so a future version needs no migration.
The four optional fields may be absent (as in the illustrative default)
-- every consumer must treat them as possibly None. A bond's maturity may
be given either directly as `maturity_years`, or as `maturity_date` (an
actual redemption date, from which years-remaining is computed); if both
are given they must roughly agree or loading raises. `load_portfolio`
takes an optional `valuation_date` (default: today) that this computation
is measured from, so a pinned run stays reproducible. Not built yet: the
real-issue data source itself, or ISIN format validation beyond
"non-empty" -- only the schema and loader are ready to accept that data.

DATE FORMATS (issue_date, maturity_date, valuation_date): ISO YYYY-MM-DD
is the canonical form and what Bond always stores, but the loader also
accepts a set of unambiguous alternatives -- see _parse_date -- such as
YYYY/MM/DD, YYYY.MM.DD, YYYYMMDD, a spelled-out month (04-Mar-2025,
March 4, 2025), Japanese numeric dates (2025年3月4日, fullwidth digits
like ２０２５年３月４日 included), or a Japanese era date (令和7年3月4日,
平成元年1月8日) -- see _JAPANESE_ERAS for the supported eras and their
Gregorian start dates. Any accepted alternative is normalized to ISO on
load, so every downstream consumer only ever sees YYYY-MM-DD. Numeric
DD/MM/YYYY and MM/DD/YYYY forms are deliberately NOT accepted: they're
ambiguous with each other whenever the day is <=12 (03/04/2025 is it
Mar 4 or Apr 3?), and a wrong guess would silently corrupt maturity_years
rather than raise -- safer to reject and ask for an unambiguous form than
to guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# Default portfolio file, sitting next to this module.
DEFAULT_PORTFOLIO_PATH = Path(__file__).with_name("portfolio.json")

# A coupon above this is almost certainly a percent/decimal unit error
# (e.g. "2.0" meant as 2% but read as 200%) rather than a real JGB rate.
MAX_PLAUSIBLE_COUPON_RATE = 0.20  # 20%, decimal

# Tolerance for the portfolio weights summing to 1.0, absorbing float
# representation error from JSON parsing.
WEIGHT_SUM_TOLERANCE = 1e-6

# If a bond specifies BOTH maturity_years and maturity_date, they must
# agree within this many years or loading raises -- catches a config entry
# where one field was updated and the other wasn't. Loose enough to tolerate
# a hand-rounded maturity_years against a never-quite-round date-derived
# value, tight enough to catch a real mismatch.
MATURITY_CONSISTENCY_TOLERANCE_YEARS = 0.05

# Simple ACT/365.25 approximation (calendar days / average year length) for
# turning a maturity_date into years-remaining -- not a precise bond-market
# day-count convention. Deliberately coarse: the only downstream use is as
# a "years" input to curve interpolation, which is itself only a handful
# of discrete points, so day-count precision here wouldn't add accuracy.
# A real day-count convention matters for accrued interest and settlement,
# which are out of scope for this loader and the pricing engine.
DAYS_PER_YEAR = 365.25

# Non-ISO date formats _parse_date also accepts, tried in this order after
# ISO YYYY-MM-DD fails. Every format here is unambiguous by construction --
# the year is 4 digits and either leads (YYYY/MM/DD-style) or the month is
# spelled out as a name rather than a number -- so there's no MM/DD vs.
# DD/MM guesswork. Deliberately NOT included: any numeric D?D-M?M-YYYY or
# M?M-D?D-YYYY style format, since those are ambiguous with each other.
_ADDITIONAL_DATE_FORMATS = (
    "%Y/%m/%d",   # 2025/03/04
    "%Y.%m.%d",   # 2025.03.04
    "%Y%m%d",     # 20250304
    "%d-%b-%Y",   # 04-Mar-2025
    "%d %b %Y",   # 04 Mar 2025
    "%d-%B-%Y",   # 04-March-2025
    "%d %B %Y",   # 04 March 2025
    "%b %d, %Y",  # Mar 4, 2025
    "%b %d %Y",   # Mar 4 2025
    "%B %d, %Y",  # March 4, 2025
    "%B %d %Y",   # March 4 2025
    "%Y-%b-%d",   # 2025-Mar-04
    "%Y %B %d",   # 2025 March 04
    "%Y年%m月%d日",  # 2025年3月4日 -- Japanese era-less numeric date; the
                     # kanji separators pin year/month/day order regardless
                     # of zero-padding, so this is unambiguous too.
)

# Fullwidth digits (U+FF10-FF19, e.g. "２０２５") -> ASCII "0"-"9". Applied to
# every date string before any parsing attempt, since fullwidth numerals
# show up in text pasted from Japanese PDFs/Excel/legacy systems and would
# otherwise fail every format above even though the date itself is
# unambiguous once normalized.
_FULLWIDTH_TO_ASCII_DIGITS = str.maketrans({chr(0xFF10 + i): str(i) for i in range(10)})

# Japanese era (gengou) start dates, newest first, used by
# _parse_japanese_era_date to convert dates like "令和7年3月4日" or
# "平成28年3月4日" to Gregorian. Each era's own year 1 is written 元年
# rather than 1年 -- handled separately, see _JP_ERA_DATE_RE. Era boundaries
# matter for validation: a candidate date must fall within [this era's
# start, next-newer era's start), otherwise the era name and year disagree
# with each other (e.g. a mistyped era for the year given).
_JAPANESE_ERAS: tuple[tuple[str, date], ...] = (
    ("令和", date(2019, 5, 1)),   # Reiwa
    ("平成", date(1989, 1, 8)),   # Heisei
    ("昭和", date(1926, 12, 25)),  # Showa
    ("大正", date(1912, 7, 30)),  # Taisho
    ("明治", date(1868, 1, 25)),  # Meiji
)

_JP_ERA_DATE_RE = re.compile(
    "^(" + "|".join(name for name, _ in _JAPANESE_ERAS) + r")(元|\d{1,2})年(\d{1,2})月(\d{1,2})日$"
)


def _parse_japanese_era_date(text: str, *, error_prefix: str) -> date | None:
    """Parse a Japanese era date such as "令和7年3月4日" or "平成元年1月8日".
    Returns None (not a ValueError) if text doesn't look like an era date
    at all, so callers can fall through to "no format matched"; raises
    ValueError if it looks like one but the year/date is invalid for that
    era (wrong era for the year, or a calendar date like Feb 30)."""
    match = _JP_ERA_DATE_RE.match(text)
    if match is None:
        return None
    era_name, era_year_token, month_str, day_str = match.groups()

    era_index = next(i for i, (name, _) in enumerate(_JAPANESE_ERAS) if name == era_name)
    era_start = _JAPANESE_ERAS[era_index][1]
    era_end = _JAPANESE_ERAS[era_index - 1][1] - timedelta(days=1) if era_index > 0 else None

    era_year = 1 if era_year_token == "元" else int(era_year_token)
    gregorian_year = era_start.year + era_year - 1

    try:
        candidate = date(gregorian_year, int(month_str), int(day_str))
    except ValueError as exc:
        raise ValueError(
            f"{error_prefix}: {text!r} is not a valid calendar date: {exc}"
        ) from exc

    if candidate < era_start or (era_end is not None and candidate > era_end):
        era_range = f"{era_start.isoformat()} to {era_end.isoformat() if era_end else 'present'}"
        raise ValueError(
            f"{error_prefix}: {text!r} resolves to {candidate.isoformat()}, which "
            f"falls outside the {era_name} era ({era_range}) -- check the era name "
            "and year"
        )
    return candidate


@dataclass(frozen=True)
class Bond:
    """One portfolio holding. Immutable -- edit config/portfolio.json (or
    point at an alternative file) and reload rather than mutating a
    loaded Bond.

    isin / issue_date / maturity_date / tenor_class are OPTIONAL, default
    None. maturity_years is always populated -- either taken directly
    from the config or derived from maturity_date (see _resolve_maturity)
    -- so every consumer can read it unconditionally either way."""

    name: str
    maturity_years: float
    coupon_rate: float
    face_value: float
    weight: float
    isin: str | None = None
    issue_date: str | None = None
    maturity_date: str | None = None
    tenor_class: str | None = None


def _parse_date(raw: str, *, error_prefix: str) -> date:
    """Parse a date string, accepting ISO YYYY-MM-DD plus the unambiguous
    alternatives in _ADDITIONAL_DATE_FORMATS and Japanese era dates (see
    _parse_japanese_era_date). Fullwidth digits are normalized to ASCII
    first, so fullwidth input works with every format, not just the
    kanji ones. Raises ValueError (message starting with error_prefix) if
    raw matches nothing -- this is where numeric DD/MM/YYYY and MM/DD/YYYY
    forms get turned away, since they're ambiguous with each other and a
    wrong guess would silently corrupt the resulting maturity_years rather
    than raise."""
    text = str(raw).strip().translate(_FULLWIDTH_TO_ASCII_DIGITS)
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _ADDITIONAL_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    era_date = _parse_japanese_era_date(text, error_prefix=error_prefix)
    if era_date is not None:
        return era_date
    raise ValueError(
        f"{error_prefix}: expected ISO YYYY-MM-DD, or an unambiguous "
        "alternative such as YYYY/MM/DD, YYYY.MM.DD, YYYYMMDD, a "
        "spelled-out month (e.g. 04-Mar-2025, March 4, 2025), or a "
        "Japanese era date (e.g. 令和7年3月4日). Numeric DD/MM/YYYY or "
        "MM/DD/YYYY forms are not accepted -- they're ambiguous with each "
        "other and a wrong guess would silently corrupt the resulting "
        "maturity_years."
    )


def _resolve_valuation_date(valuation_date: str | date | None) -> date:
    """Normalize the valuation_date argument to a date: None -> today, a
    date is passed through, a str is parsed via _parse_date (ISO
    YYYY-MM-DD or one of its unambiguous alternatives)."""
    if valuation_date is None:
        return date.today()
    if isinstance(valuation_date, date):
        return valuation_date
    return _parse_date(
        str(valuation_date),
        error_prefix=f"load_portfolio: unparseable valuation_date {valuation_date!r}",
    )


def _years_between(start: date, end: date) -> float:
    """Years from start to end under the ACT/365.25 approximation (DAYS_PER_YEAR)."""
    return (end - start).days / DAYS_PER_YEAR


def _resolve_maturity(
    raw: dict, *, name: str, as_of: date, source: Path, index: int
) -> tuple[float, str | None]:
    """Resolve one bond's maturity_years, per the rules in the module
    docstring. Returns (maturity_years, maturity_date_str_or_None) --
    maturity_date wins when present (the more authoritative form);
    maturity_years then just serves as a consistency check on it.
    """
    has_years = raw.get("maturity_years") is not None
    has_date = raw.get("maturity_date") is not None

    if not has_years and not has_date:
        raise ValueError(
            f"{source}: bond {name!r} (index {index}) specifies neither "
            "'maturity_years' nor 'maturity_date' -- exactly one is required"
        )

    stated_years: float | None = None
    if has_years:
        try:
            stated_years = float(raw["maturity_years"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{source}: bond {name!r} (index {index}) has a non-numeric "
                f"maturity_years: {exc}"
            ) from exc

    if not has_date:
        return stated_years, None  # type: ignore[return-value]

    maturity_date_raw = str(raw["maturity_date"])
    maturity_dt = _parse_date(
        maturity_date_raw,
        error_prefix=(
            f"{source}: bond {name!r} (index {index}) has an unparseable "
            f"maturity_date {maturity_date_raw!r}"
        ),
    )
    derived_years = _years_between(as_of, maturity_dt)

    if has_years and abs(stated_years - derived_years) > MATURITY_CONSISTENCY_TOLERANCE_YEARS:
        raise ValueError(
            f"{source}: bond {name!r} (index {index}) is inconsistent: "
            f"maturity_years={stated_years} but maturity_date {maturity_date_raw} "
            f"implies {derived_years:.4f} years as of valuation date "
            f"{as_of.isoformat()} (tolerance {MATURITY_CONSISTENCY_TOLERANCE_YEARS} years)"
        )

    # Normalized to ISO regardless of the input format, so every downstream
    # consumer (including _validate_portfolio's ordering check) only ever
    # sees YYYY-MM-DD.
    return derived_years, maturity_dt.isoformat()


def load_portfolio(
    path: str | Path | None = None,
    valuation_date: str | date | None = None,
) -> list[Bond]:
    """Load, validate, and return the portfolio as a list of Bond objects.

    Parameters
    ----------
    path : optional path to an alternative portfolio JSON file (same shape
        as config/portfolio.json). Defaults to DEFAULT_PORTFOLIO_PATH --
        this is the hook a test, or a future dashboard, uses to supply its
        own portfolio without touching the default file.
    valuation_date : optional date (or a date string -- ISO YYYY-MM-DD or
        an unambiguous alternative, see _parse_date) that a maturity_date
        is measured from. Defaults to today; pass an explicit date for a
        reproducible run.

    Raises
    ------
    ValueError : missing required fields or failed validation -- weights
        not summing to 1.0, a negative face value, a non-positive
        maturity, an implausible coupon, or an inconsistent maturity spec.
    """
    portfolio_path = Path(path) if path is not None else DEFAULT_PORTFOLIO_PATH
    as_of = _resolve_valuation_date(valuation_date)

    with open(portfolio_path) as fh:
        payload = json.load(fh)

    bonds_raw = payload.get("bonds")
    if not bonds_raw:
        raise ValueError(f"{portfolio_path}: no non-empty 'bonds' list found")

    bonds: list[Bond] = []
    for i, raw in enumerate(bonds_raw):
        try:
            name = str(raw["name"])
            coupon_rate = float(raw["coupon_rate"])
            face_value = float(raw["face_value"])
            weight = float(raw["weight"])
        except KeyError as exc:
            raise ValueError(
                f"{portfolio_path}: bond at index {i} is missing field {exc}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{portfolio_path}: bond at index {i} has a non-numeric field: {exc}"
            ) from exc

        maturity_years, maturity_date_str = _resolve_maturity(
            raw, name=name, as_of=as_of, source=portfolio_path, index=i
        )

        isin = raw.get("isin")
        issue_date_raw = raw.get("issue_date")
        tenor_class = raw.get("tenor_class")

        # Normalized to ISO here (same as maturity_date in _resolve_maturity)
        # so Bond.issue_date is always YYYY-MM-DD regardless of input format.
        issue_date_str: str | None = None
        if issue_date_raw is not None:
            issue_date_str = _parse_date(
                str(issue_date_raw),
                error_prefix=(
                    f"{portfolio_path}: bond {name!r} (index {i}) has an "
                    f"unparseable issue_date {issue_date_raw!r}"
                ),
            ).isoformat()

        bonds.append(
            Bond(
                name=name,
                maturity_years=maturity_years,
                coupon_rate=coupon_rate,
                face_value=face_value,
                weight=weight,
                isin=str(isin) if isin is not None else None,
                issue_date=issue_date_str,
                maturity_date=maturity_date_str,
                tenor_class=str(tenor_class) if tenor_class is not None else None,
            )
        )

    _validate_portfolio(bonds, portfolio_path)
    return bonds


def _validate_portfolio(bonds: list[Bond], source: Path) -> None:
    """Raise ValueError unless bonds form a plausible, internally-consistent
    portfolio. Per-bond structural checks run first; the weight-sum check
    runs once, across the whole list, since it's only meaningful in
    aggregate. issue_date and maturity_date were already parsed and
    normalized to ISO during loading (_resolve_maturity for maturity_date,
    the main load loop for issue_date), so they're guaranteed valid here --
    this only checks the ordering between them."""
    if not bonds:
        raise ValueError(f"{source}: portfolio is empty")

    names = [b.name for b in bonds]
    if len(names) != len(set(names)):
        raise ValueError(f"{source}: duplicate bond names: {names}")

    for b in bonds:
        if b.face_value <= 0:
            raise ValueError(
                f"{source}: bond {b.name!r} has non-positive face_value "
                f"({b.face_value})"
            )
        if b.maturity_years <= 0:
            raise ValueError(
                f"{source}: bond {b.name!r} has non-positive maturity_years "
                f"({b.maturity_years}) -- if this came from maturity_date, "
                "the bond has already matured as of the valuation date"
            )
        if b.coupon_rate < 0:
            raise ValueError(
                f"{source}: bond {b.name!r} has negative coupon_rate "
                f"({b.coupon_rate})"
            )
        if b.coupon_rate > MAX_PLAUSIBLE_COUPON_RATE:
            raise ValueError(
                f"{source}: bond {b.name!r} has coupon_rate {b.coupon_rate} above "
                f"the plausibility ceiling {MAX_PLAUSIBLE_COUPON_RATE} (20%) -- "
                "likely a percent/decimal unit error (e.g. 2.0 instead of 0.020)"
            )
        if b.weight < 0:
            raise ValueError(
                f"{source}: bond {b.name!r} has negative weight ({b.weight})"
            )
        if b.isin is not None and not b.isin.strip():
            raise ValueError(f"{source}: bond {b.name!r} has an empty isin string")
        if b.issue_date is not None and b.maturity_date is not None:
            issue_dt = date.fromisoformat(b.issue_date)
            maturity_dt = date.fromisoformat(b.maturity_date)
            if issue_dt >= maturity_dt:
                raise ValueError(
                    f"{source}: bond {b.name!r} has issue_date {b.issue_date} "
                    f"on or after its maturity_date {b.maturity_date}"
                )

    weight_sum = sum(b.weight for b in bonds)
    if abs(weight_sum - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"{source}: portfolio weights sum to {weight_sum}, expected 1.0 "
            f"(tolerance {WEIGHT_SUM_TOLERANCE})"
        )


if __name__ == "__main__":
    portfolio = load_portfolio()
    print(f"Loaded {len(portfolio)} bond(s) from {DEFAULT_PORTFOLIO_PATH}:")
    for b in portfolio:
        extras = [
            f"{label}={value}"
            for label, value in (
                ("isin", b.isin),
                ("tenor_class", b.tenor_class),
                ("maturity_date", b.maturity_date),
            )
            if value is not None
        ]
        suffix = "  " + " ".join(extras) if extras else ""
        print(
            f"  {b.name:>8s}  {b.maturity_years:5.1f}Y  "
            f"coupon {b.coupon_rate:6.3%}  weight {b.weight:6.2%}  "
            f"face {b.face_value:.2f}{suffix}"
        )
    print(f"  {'total weight':>8s}: {sum(b.weight for b in portfolio):.2%}")
