"""
portfolio_loader.py

Single source of truth for the project's bond portfolio. Every downstream
phase (pricing in /models, Key Rate Duration and DV01 in Phase 3, the
Phase 8 Streamlit dashboard) reads the portfolio through load_portfolio()
rather than hardcoding bond definitions into individual modules.

Default data: config/portfolio.json.

ILLUSTRATIVE DATA NOTICE
------------------------
The coupon rates in the default config/portfolio.json are ILLUSTRATIVE --
chosen to sit near current tenor yields (see data/jgb_curve_loader.py) so
each bond prices near par under the loaded curve. They do NOT correspond to
real outstanding JGB issues (real issues would carry ISINs, actual coupon
schedules, and actual amounts outstanding). Substituting real ISINs and
their actual coupons would be a credibility upgrade for a later iteration;
that substitution belongs in the Phase 6 validation report's assumptions
section, alongside the other known-limitation writeups from Phase 1
(docs/phase_1_documentation.md section 4).

Output contract: load_portfolio() returns a list[Bond], one entry per bond,
in the order given in the config file. Each Bond has:
    - name          : str, unique within the portfolio
    - maturity_years: float, > 0 -- always resolved to a number (see below),
                      whichever of maturity_years / maturity_date the config
                      entry actually specified
    - coupon_rate   : float, decimal (0.020 == 2.0%), in [0, MAX_PLAUSIBLE_COUPON_RATE]
    - face_value    : float, > 0
    - weight        : float, >= 0; weights across the portfolio sum to 1.0
    - isin          : str | None -- optional, unvalidated beyond non-empty
    - issue_date    : str | None -- optional, ISO "YYYY-MM-DD"
    - maturity_date : str | None -- optional, ISO "YYYY-MM-DD"
    - tenor_class   : str | None -- optional free-text label (e.g. "20Y")

SCHEMA FORWARD-COMPATIBILITY (real-issue portfolios, later phase)
------------------------------------------------------------------
A later phase adds a JGB issue reference module (real MOF issuance data) and
a Streamlit dashboard letting a user build a portfolio from real outstanding
issues instead of illustrative ones. This module's schema and loader are
built to accept that data now, so no migration is needed then:

- `isin`, `issue_date`, `maturity_date`, `tenor_class` are all OPTIONAL,
  per-bond, alongside the original fields. They may be absent, as they are
  throughout the illustrative default portfolio -- absence is not an error,
  and every consumer of Bond must treat them as possibly None.
- A bond's maturity may be given EITHER as `maturity_years` directly (the
  illustrative case: a round number chosen by hand) OR as `maturity_date`
  (the real-issue case: an actual redemption date), from which remaining
  years is computed relative to a valuation date. If `maturity_date` is
  present, it wins and its derived years is what `Bond.maturity_years`
  holds; if only `maturity_years` is present, that value is used as-is; if
  both are present they must agree (within MATURITY_CONSISTENCY_TOLERANCE_YEARS)
  or loading raises; if neither is present, loading raises. See
  `_resolve_maturity`.
- `load_portfolio` takes an optional `valuation_date` (default: today) used
  for that maturity_date -> years computation, so a pinned run with a real-
  issue portfolio is reproducible months later on any machine -- the same
  reproducibility concern `prefer_live=False` addresses for the curve loader.
- Deliberately NOT built yet: the issue reference module itself, any MOF
  issuance fetching, and any ISIN format validation beyond "non-empty" --
  only the schema and this loader are made ready to accept that data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Default portfolio file, sitting next to this module.
DEFAULT_PORTFOLIO_PATH = Path(__file__).with_name("portfolio.json")

# A JGB coupon above this is far outside anything plausible for the current
# rate environment and is almost certainly a percent/decimal unit error
# (e.g. "2.0" meant as 2% but read as 200%). Same spirit as the yield
# plausibility band in data/jgb_curve_loader.py -- catch a units mistake at
# load time rather than let it silently inflate every downstream price.
MAX_PLAUSIBLE_COUPON_RATE = 0.20  # 20%, decimal

# Tolerance for the portfolio weights summing to 1.0, to absorb float
# representation error from JSON parsing -- not a modelling choice.
WEIGHT_SUM_TOLERANCE = 1e-6

# When a bond specifies BOTH maturity_years and maturity_date, the two must
# agree to within this many years or loading raises -- catches a config
# entry where one field was updated (e.g. a corrected maturity_date) and the
# other was not. Set loosely enough (~18 days) to tolerate maturity_years
# being a hand-rounded number (e.g. "20") against a date-derived value that
# is never exactly round (19.98 years), but tight enough to still catch a
# real mismatch (a maturity_date a full year off from maturity_years).
MATURITY_CONSISTENCY_TOLERANCE_YEARS = 0.05

# Day-count convention for deriving years-to-maturity from maturity_date and
# a valuation date: simple ACT/365.25 (calendar days / average year length).
# This is a deliberately coarse approximation, not a bond-market day-count
# convention (ACT/ACT, 30/360, etc.) -- the only downstream use of the
# result is as a continuous "years" input to curve interpolation (Part B),
# which already treats maturity as a real number, not a schedule of dates.
# A precise day-count matters for accrued interest and settlement mechanics,
# which are out of scope for both this loader and the Part B pricing engine.
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class Bond:
    """One portfolio holding. Immutable -- a loaded portfolio is a fixed input
    for a given pricing/KRD run; edit config/portfolio.json (or point at an
    alternative file) and reload rather than mutating a loaded Bond.

    isin / issue_date / maturity_date / tenor_class are OPTIONAL and default
    to None -- populated for a real-issue portfolio, absent for the
    illustrative default. maturity_years is always populated: it is either
    taken directly from the config or derived from maturity_date (see
    _resolve_maturity), so every consumer can keep reading it unconditionally
    regardless of which form the source config used."""

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
    """Normalize the valuation_date argument to a date.

    None -> today (the common, non-reproducible case: "what would this
    portfolio's maturities be as of right now"). A date is passed through.
    A str is parsed as ISO "YYYY-MM-DD" -- the same format maturity_date /
    issue_date use, so a caller pinning a validation run can pass the same
    kind of string everywhere.
    """
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
    """Resolve one bond's maturity_years, per the rules in the module docstring.

    Returns (maturity_years, maturity_date_str_or_None). maturity_date wins
    when present -- its derived years is what's returned -- because it is
    the more authoritative form (an actual redemption date) once real-issue
    data is in play; maturity_years is then just a consistency check on it.
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
    path : optional path to an alternative portfolio JSON file, in the same
        shape as config/portfolio.json (a top-level "bonds" list of objects
        with name/coupon_rate/face_value/weight plus a maturity spec).
        Defaults to DEFAULT_PORTFOLIO_PATH. This is the hook tests and the
        Phase 8 dashboard use to supply their own portfolio without touching
        the default file or this module.
    valuation_date : optional date (or ISO "YYYY-MM-DD" string) used to turn
        a bond's maturity_date into a remaining-years figure. Defaults to
        today. Pass an explicit date for a reproducible run -- the same
        reason validation runs pin `prefer_live=False` on the curve loader
        rather than depending on "whatever today is."

    Raises
    ------
    ValueError : the file is missing required fields, or fails validation
        (see _resolve_maturity and _validate_portfolio) -- weights not
        summing to 1.0, a negative face value, a non-positive maturity, an
        implausible coupon rate, an unparseable or inconsistent maturity
        spec, or a malformed optional date field.
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
    portfolio. Structural checks (positive face value, positive maturity,
    plausible coupon, well-formed optional fields) run per bond; the
    weight-sum check runs once, across the whole portfolio, since it is only
    meaningful in aggregate.

    maturity_date, if present, was already parsed by _resolve_maturity
    (which runs before this, in load_portfolio) -- it is not re-parsed here.
    issue_date is validated here since nothing upstream needed to touch it."""
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
