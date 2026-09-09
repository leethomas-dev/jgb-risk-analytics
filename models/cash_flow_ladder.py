"""
cash_flow_ladder.py

Phase 4.6C: a maturity profile of when the portfolio's cash flows
actually land -- every coupon and principal payment across all holdings,
aggregated by calendar date, in both nominal (undiscounted) and present-
value terms. Computes no new pricing of its own: reuses
bond_pricing.cash_flow_schedule (Phase 2B/4.6A, the exact schedule
price_bond itself prices against) for each bond's cash-flow timing, and
bond_pricing.discount_factors_at (Phase 4.6C, factored out of price_bond
for this exact reuse) for present-valuing each one -- the same
curve-basis auto-detection (par or zero curve) price_bond itself uses.

UNITS: "per 100 face value, weighted by each bond's own portfolio
weight" -- i.e. every bond's cash flows (already per its own 100 face
value, config/portfolio.json) are scaled by that bond's `weight` before
being summed across the portfolio. This is the SAME aggregation
price_portfolio's own portfolio-level price already uses
((df.weight * df.price).sum()) and the same one key_rate_duration_portfolio
/ dv01_by_tenor_portfolio use for their own "portfolio_total" rows -- not
a new convention introduced here. As with every other portfolio-level
figure in this project, this is NOT a real position size (Phase 2A's
portfolio is illustrative; Phase 3B §1.1's own units disclaimer applies
here identically).

WHY CALENDAR DATES, NOT JUST "YEARS FROM NOW": a cash flow ladder's whole
point is showing WHEN money actually lands -- each bond's cash-flow times
(bond_pricing.cash_flow_schedule) are converted to real calendar dates the
same way Phase 4.6A's accrued_interest() already does
(valuation_date + round(years * DAYS_PER_YEAR) days,
config.portfolio_loader.DAYS_PER_YEAR) -- reimplemented as the same
one-line formula here rather than importing bond_pricing's private
_coupon_boundaries(), which returns period BOUNDARIES for accrued-
interest lookups (including the valuation-date anchor itself), a
different shape than the plain per-cash-flow date list this module
needs. This mirrors Phase 4.6A's own choice to write a small,
self-contained _resolve_date() rather than import config.portfolio_loader's
private _resolve_valuation_date -- small, well-precedented duplication
over a private cross-module dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe: this module only ever saves a PNG.
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.portfolio_loader import DAYS_PER_YEAR, Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import cash_flow_schedule, discount_factors_at
from models.ultra_long_profile import DEFAULT_ULTRA_LONG_THRESHOLD_YEARS

# outputs/ sits next to models/ at the repo root, resolved from this
# module's own location rather than the caller's working directory --
# same pattern as models.ultra_long_profile.DEFAULT_CHART_PATH.
_OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"

# The validated categorical pair (dataviz skill: blue/orange, slots 1-2,
# the fixed adjacent order) -- coupon first (the recurring, smaller
# payment), principal second (the one-off, larger redemption), stacked in
# that order in every chart this module produces.
COUPON_COLOR = "#2a78d6"
PRINCIPAL_COLOR = "#eb6834"

LADDER_COLUMNS = [
    "date", "years_from_valuation",
    "coupon_nominal", "principal_nominal", "total_nominal",
    "coupon_pv", "principal_pv", "total_pv",
]


def _resolve_date(value: str | date | None) -> date:
    """None -> today, a date passes through, a str is parsed as ISO --
    the same small, self-contained normalization Phase 4.6A's
    bond_pricing._resolve_date already uses, for the same reason (module
    docstring): avoid a private cross-module import for three lines of
    logic."""
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _bond_cash_flows(bond: Bond, valuation_date: date, curve: pd.DataFrame, freq: int) -> pd.DataFrame:
    """One bond's own cash flows -- real calendar dates, coupon and
    principal kept SEPARATE (never pre-summed), nominal and PV, already
    scaled by this bond's own portfolio weight (module docstring
    "UNITS")."""
    n_periods, cash_flow_times = cash_flow_schedule(bond.maturity_years, freq)
    dates = [valuation_date + timedelta(days=round(t * DAYS_PER_YEAR)) for t in cash_flow_times]

    coupon = np.full(n_periods, bond.face_value * bond.coupon_rate / freq) * bond.weight
    principal = np.zeros(n_periods)
    principal[-1] = bond.face_value * bond.weight  # only the final period redeems face value

    discount_factors = discount_factors_at(curve, cash_flow_times, freq=freq)

    return pd.DataFrame(
        {
            "date": dates,
            "coupon_nominal": coupon,
            "principal_nominal": principal,
            "coupon_pv": coupon * discount_factors,
            "principal_pv": principal * discount_factors,
        }
    )


@dataclass(frozen=True)
class CashFlowLadderResult:
    """The portfolio's full cash flow ladder -- one row per distinct
    payment date across every holding. Frozen -- a snapshot of one
    portfolio against one curve as of one valuation date; recompute
    rather than mutate if any of those change.

    ladder : columns LADDER_COLUMNS (module constant) -- date,
        years_from_valuation, coupon/principal/total in both nominal and
        present-value terms, ascending by date. "total" = coupon +
        principal at that date (module docstring "UNITS" for what the
        currency figures mean).
    threshold_years : the maturity cutoff used for the ultra-long share
        properties below -- reuses models.ultra_long_profile's own
        constant by default (Phase 3C), so this module's "ultra-long"
        means the same 20-year convention that phase already established,
        not a second, independently-chosen threshold.
    """

    valuation_date: date
    freq: int
    threshold_years: float
    ladder: pd.DataFrame

    @property
    def total_nominal(self) -> float:
        return float(self.ladder["total_nominal"].sum())

    @property
    def total_pv(self) -> float:
        return float(self.ladder["total_pv"].sum())

    @property
    def ultra_long_nominal_share(self) -> float:
        """Fraction (0 to 1) of total NOMINAL cash flow landing at or
        beyond threshold_years from the valuation date."""
        mask = self.ladder["years_from_valuation"] >= self.threshold_years
        return float(self.ladder.loc[mask, "total_nominal"].sum() / self.total_nominal)

    @property
    def ultra_long_pv_share(self) -> float:
        """The same split in PRESENT-VALUE terms -- discounting shrinks a
        far-future payment far more than a near one, so this is expected
        to sit BELOW the nominal share, not equal to it (§2 of the
        accompanying doc measures exactly how much)."""
        mask = self.ladder["years_from_valuation"] >= self.threshold_years
        return float(self.ladder.loc[mask, "total_pv"].sum() / self.total_pv)


def compute_cash_flow_ladder(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    freq: int = 2,
    valuation_date: str | date | None = None,
    threshold_years: float = DEFAULT_ULTRA_LONG_THRESHOLD_YEARS,
) -> CashFlowLadderResult:
    """Aggregate every bond's cash flows, across the whole portfolio, by
    calendar date -- coupon and principal kept separate throughout, in
    both nominal and present-value terms (module docstring).

    Takes an already-loaded portfolio and curve, mirroring price_portfolio
    / key_rate_duration_portfolio -- no hidden file/network I/O.
    """
    valuation = _resolve_date(valuation_date)
    per_bond_frames = [_bond_cash_flows(bond, valuation, curve, freq) for bond in portfolio]

    all_flows = pd.concat(per_bond_frames, ignore_index=True)
    ladder = (
        all_flows.groupby("date", as_index=False)[
            ["coupon_nominal", "principal_nominal", "coupon_pv", "principal_pv"]
        ]
        .sum()
        .sort_values("date")
        .reset_index(drop=True)
    )
    ladder["years_from_valuation"] = ladder["date"].apply(lambda d: (d - valuation).days / DAYS_PER_YEAR)
    ladder["total_nominal"] = ladder["coupon_nominal"] + ladder["principal_nominal"]
    ladder["total_pv"] = ladder["coupon_pv"] + ladder["principal_pv"]
    ladder = ladder[LADDER_COLUMNS]

    return CashFlowLadderResult(
        valuation_date=valuation, freq=freq, threshold_years=threshold_years, ladder=ladder
    )


def plot_cash_flow_ladder(
    result: CashFlowLadderResult,
    output_path: str | Path | None = None,
    basis: str = "nominal",
) -> Path:
    """Save a stacked-bar cash flow ladder to `output_path` (default
    outputs/cash_flow_ladder_{basis}.png) -- one bar per CALENDAR YEAR
    (not per exact payment date), coupon and principal stacked in that
    order, principal visually distinguished by color (module docstring's
    "COUPON_COLOR" / "PRINCIPAL_COLOR", validated via the dataviz skill's
    palette checker for CVD-safe adjacency).

    WHY BUCKET BY YEAR FOR THE CHART, WHEN compute_cash_flow_ladder()
    ITSELF REPORTS BY EXACT DATE: the portfolio's longest bond alone has
    80 semiannual payment dates over 40 years -- a bar per exact date
    would be too dense to read the SHAPE the phase brief actually asks
    for ("where cash flows concentrate"). Annual buckets are the standard
    convention for a cash flow ladder chart precisely because the
    question is about concentration over time, not any one exact date.
    The underlying `result.ladder` DataFrame keeps full date-level
    granularity regardless of how the chart buckets it -- aggregation
    happens only here, for display, never upstream.

    basis : "nominal" or "pv" -- which of the two reported bases to chart.
    """
    if basis not in ("nominal", "pv"):
        raise ValueError(f"basis must be 'nominal' or 'pv', got {basis!r}")

    if output_path is None:
        output_path = _OUTPUTS_DIR / f"cash_flow_ladder_{basis}.png"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ladder = result.ladder.copy()
    ladder["year"] = pd.to_datetime(ladder["date"]).dt.year
    coupon_col, principal_col = f"coupon_{basis}", f"principal_{basis}"
    yearly = ladder.groupby("year")[[coupon_col, principal_col]].sum()

    fig, ax = plt.subplots(figsize=(13, 5))
    # A thin white edge between the two stacked segments (and between
    # neighboring bars) stands in for the 2px surface gap the dataviz
    # skill calls for between touching marks -- matplotlib has no native
    # gap primitive for stacked bars, so an edgecolor matching the figure
    # background achieves the same visual separation.
    ax.bar(yearly.index, yearly[coupon_col], label="Coupon", color=COUPON_COLOR, width=0.8, edgecolor="white", linewidth=0.6)
    ax.bar(
        yearly.index, yearly[principal_col], bottom=yearly[coupon_col], label="Principal",
        color=PRINCIPAL_COLOR, width=0.8, edgecolor="white", linewidth=0.6,
    )

    basis_label = "nominal (undiscounted)" if basis == "nominal" else "present value"
    ax.set_xlabel("Year")
    ax.set_ylabel("Cash flow (per 100 face value, portfolio-weighted)")
    ax.set_title(f"Portfolio cash flow ladder -- {basis_label}")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.3, linewidth=0.5)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    curve = load_jgb_curve()
    portfolio = load_portfolio()

    result = compute_cash_flow_ladder(portfolio, curve)

    print()
    print("Cash flow ladder (per 100 face value, portfolio-weighted; first and last 5 dates):")
    print(pd.concat([result.ladder.head(5), result.ladder.tail(5)]).to_string(
        index=False,
        formatters={
            "years_from_valuation": lambda y: f"{y:5.1f}",
            "coupon_nominal": lambda v: f"{v:7.4f}",
            "principal_nominal": lambda v: f"{v:7.4f}",
            "total_nominal": lambda v: f"{v:7.4f}",
            "coupon_pv": lambda v: f"{v:7.4f}",
            "principal_pv": lambda v: f"{v:7.4f}",
            "total_pv": lambda v: f"{v:7.4f}",
        },
    ))

    print()
    print(f"Total nominal cash flow (per 100 face value, portfolio-weighted): {result.total_nominal:.4f}")
    print(f"Total present value:                                             {result.total_pv:.4f}")
    print(
        f"Share landing at/beyond {result.threshold_years:.0f}Y -- "
        f"nominal: {result.ultra_long_nominal_share:.1%}   "
        f"present value: {result.ultra_long_pv_share:.1%}"
    )

    nominal_chart = plot_cash_flow_ladder(result, basis="nominal")
    pv_chart = plot_cash_flow_ladder(result, basis="pv")
    print()
    print(f"Charts saved to {nominal_chart} and {pv_chart}")

    print()
    print(
        "Interpretation: the portfolio's cash flows concentrate at its six bonds' own\n"
        f"redemption dates, three of which ({result.threshold_years:.0f}Y+) sit in the ultra-long\n"
        f"segment Phase 3C already found carries a disproportionate share of this\n"
        f"portfolio's interest-rate risk ({result.ultra_long_nominal_share:.0%} of nominal cash flow here,\n"
        f"vs. only {result.ultra_long_pv_share:.0%} once discounted back to today -- a reminder that\n"
        "'concentrated risk' and 'concentrated cash' are related but different claims,\n"
        "since discounting shrinks a far-future payment far more than a near one)."
    )
