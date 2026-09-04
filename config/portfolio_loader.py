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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
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


def _resolve_valuation_date(valuation_date: str | date | None) -> date:
    """Normalize the valuation_date argument to a date: None -> today, a
    date is passed through, a str is parsed as ISO "YYYY-MM-DD"."""
    if valuation_date is None:
        return date.today()
    if isinstance(valuation_date, date):
        return valuation_date
    return date.fromisoformat(str(valuation_date))


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

    maturity_date_str = str(raw["maturity_date"])
    try:
        maturity_dt = date.fromisoformat(maturity_date_str)
    except ValueError as exc:
        raise ValueError(
            f"{source}: bond {name!r} (index {index}) has an unparseable "
            f"maturity_date {maturity_date_str!r} (expected ISO YYYY-MM-DD): {exc}"
        ) from exc
    derived_years = _years_between(as_of, maturity_dt)

    if has_years and abs(stated_years - derived_years) > MATURITY_CONSISTENCY_TOLERANCE_YEARS:
        raise ValueError(
            f"{source}: bond {name!r} (index {index}) is inconsistent: "
            f"maturity_years={stated_years} but maturity_date {maturity_date_str} "
            f"implies {derived_years:.4f} years as of valuation date "
            f"{as_of.isoformat()} (tolerance {MATURITY_CONSISTENCY_TOLERANCE_YEARS} years)"
        )

    return derived_years, maturity_date_str


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
    valuation_date : optional date (or ISO string) that a maturity_date is
        measured from. Defaults to today; pass an explicit date for a
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
        issue_date = raw.get("issue_date")
        tenor_class = raw.get("tenor_class")

        bonds.append(
            Bond(
                name=name,
                maturity_years=maturity_years,
                coupon_rate=coupon_rate,
                face_value=face_value,
                weight=weight,
                isin=str(isin) if isin is not None else None,
                issue_date=str(issue_date) if issue_date is not None else None,
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
    aggregate. maturity_date was already parsed by _resolve_maturity, so
    it isn't re-parsed here; issue_date is checked here instead."""
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
        if b.issue_date is not None:
            try:
                issue_dt = date.fromisoformat(b.issue_date)
            except ValueError as exc:
                raise ValueError(
                    f"{source}: bond {b.name!r} has an unparseable issue_date "
                    f"{b.issue_date!r} (expected ISO YYYY-MM-DD): {exc}"
                ) from exc
            if b.maturity_date is not None:
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
