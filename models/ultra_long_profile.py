"""
ultra_long_profile.py

The Japan-specific angle of this project: a focused view on the
ultra-long (20Y+) segment of the curve -- a persistent, real market theme
(insurers and pension funds extending duration against thin issuance),
which is why the illustrative portfolio deliberately puts ~40% of its
weight in 20Y/30Y/40Y bonds.

Computes NO new sensitivity of its own -- takes the portfolio-level KRD
table (key_rate_duration.key_rate_duration_portfolio) and DV01 table
(dv01.dv01_by_tenor_portfolio), both already validated, and splits each
one's "portfolio_total" row into "ultra-long" (maturity >= a threshold)
and everything else. A slice-and-sum, not a new bump-and-reprice
calculation.

WHY A THRESHOLD, NOT A HARDCODED TENOR LIST: which tenors count as
ultra-long is decided by applying the threshold (>= 20Y) to whatever
tenor grid the curve actually returns at call time, not a fixed list --
same non-fixed-tenor-grid pattern as every other module here. Concretely:
this project's snapshot data source classifies {20, 30, 40} as
ultra-long; its live data source classifies {20, 25, 30, 40} -- different
sets, both correct, because both are the threshold applied fresh to that
grid. A hardcoded list would have gotten one of the two wrong.

Reads the portfolio and curve through their own loaders; hardcodes
neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe: this module only ever saves a PNG,
# never opens a window, so a GUI backend isn't needed and could crash on
# a machine with no display (a CI runner, a validator's sandbox).
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.dv01 import DEFAULT_BUMP_SIZE, dv01_by_tenor_portfolio
from models.key_rate_duration import key_rate_duration_portfolio

# The conventional cutoff in JGB market commentary for "ultra-long" as
# opposed to merely "long" (10Y+) -- matches the illustrative portfolio's
# own design, whose 20Y/30Y/40Y bonds exist to populate this segment.
DEFAULT_ULTRA_LONG_THRESHOLD_YEARS = 20.0

# outputs/ sits next to models/ at the repo root, resolved from this
# module's own location rather than the caller's working directory.
DEFAULT_CHART_PATH = Path(__file__).resolve().parent.parent / "outputs" / "ultra_long_dv01_profile.png"


@dataclass(frozen=True)
class UltraLongProfile:
    """The ultra-long segment's share of portfolio-level interest rate
    risk, in both KRD (percentage) and DV01 (currency) terms. Frozen -- a
    snapshot of one portfolio against one curve at one threshold; recompute
    rather than mutate if any of those change.

    threshold_years : the maturity cutoff used (>= counts as ultra-long).
    tenors : every tenor on the curve's own grid, ascending.
    ultra_long_tenors : the subset of `tenors` that met the threshold --
        not knowable from the threshold alone, since it depends on which
        tenors the curve actually quoted, so it's stored explicitly.
    krd_by_tenor / dv01_by_tenor : the portfolio's total KRD / DV01 at
        each tenor.
    krd_total / dv01_total : sum across all tenors.
    krd_ultra_long / dv01_ultra_long : sum restricted to `ultra_long_tenors`.
    """

    threshold_years: float
    tenors: np.ndarray
    ultra_long_tenors: np.ndarray
    krd_by_tenor: pd.Series
    dv01_by_tenor: pd.Series
    krd_total: float
    dv01_total: float
    krd_ultra_long: float
    dv01_ultra_long: float

    @property
    def krd_ultra_long_share(self) -> float:
        """Fraction (0 to 1) of total portfolio KRD sitting at or beyond
        threshold_years."""
        return self.krd_ultra_long / self.krd_total

    @property
    def dv01_ultra_long_share(self) -> float:
        """Fraction (0 to 1) of total portfolio DV01 sitting at or beyond
        threshold_years -- the currency-terms version of the same split,
        the number a risk committee would actually ask for."""
        return self.dv01_ultra_long / self.dv01_total


def compute_ultra_long_profile(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    threshold_years: float = DEFAULT_ULTRA_LONG_THRESHOLD_YEARS,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> UltraLongProfile:
    """Build the ultra-long profile for one portfolio against one curve.

    Calls key_rate_duration_portfolio and dv01_by_tenor_portfolio once
    each, takes their "portfolio_total" rows, and splits the tenor
    columns at `threshold_years`. No pricing or aggregation logic is
    reimplemented here.
    """
    krd_table = key_rate_duration_portfolio(portfolio, curve, freq=freq, bump_size=bump_size)
    dv01_table = dv01_by_tenor_portfolio(portfolio, curve, freq=freq, bump_size=bump_size)

    krd_row = krd_table.loc["portfolio_total"]
    dv01_row = dv01_table.loc["portfolio_total"]

    tenors = krd_row.index.to_numpy(dtype=float)
    is_ultra_long = tenors >= threshold_years
    ultra_long_tenors = tenors[is_ultra_long]

    return UltraLongProfile(
        threshold_years=threshold_years,
        tenors=tenors,
        ultra_long_tenors=ultra_long_tenors,
        krd_by_tenor=krd_row,
        dv01_by_tenor=dv01_row,
        krd_total=float(krd_row.sum()),
        dv01_total=float(dv01_row.sum()),
        krd_ultra_long=float(krd_row[is_ultra_long].sum()),
        dv01_ultra_long=float(dv01_row[is_ultra_long].sum()),
    )


def plot_ultra_long_profile(
    profile: UltraLongProfile,
    output_path: str | Path = DEFAULT_CHART_PATH,
    metric: str = "dv01",
) -> Path:
    """Bar chart of portfolio DV01 (or KRD) contribution by tenor, with
    the ultra-long segment (>= threshold_years) colored differently,
    saved as a PNG to `output_path`. Returns the resolved Path.

    `metric`: "dv01" (default) or "krd". DV01 defaults on since it's the
    more concrete, currency figure a risk committee would actually look
    at; `metric="krd"` produces the same chart against the percentage
    figures instead.
    """
    if metric not in ("dv01", "krd"):
        raise ValueError(f"metric must be 'dv01' or 'krd', got {metric!r}")

    series = profile.dv01_by_tenor if metric == "dv01" else profile.krd_by_tenor
    is_ultra_long = profile.tenors >= profile.threshold_years

    colors = ["#4C72B0" if not ul else "#C44E52" for ul in is_ultra_long]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar([str(t) for t in profile.tenors], series.to_numpy(), color=colors)

    ax.set_xlabel("Tenor (years)")
    ax.set_ylabel(
        "Portfolio DV01 (currency per 100 face, per 1bp)"
        if metric == "dv01"
        else "Portfolio KRD (years)"
    )
    ax.set_title(
        f"Portfolio {'DV01' if metric == 'dv01' else 'KRD'} contribution by tenor\n"
        f"Ultra-long (>= {profile.threshold_years:.0f}Y) segment highlighted"
    )

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color="#4C72B0", label=f"< {profile.threshold_years:.0f}Y"),
            Patch(color="#C44E52", label=f">= {profile.threshold_years:.0f}Y (ultra-long)"),
        ]
    )
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)

    return output_path


if __name__ == "__main__":
    curve = load_jgb_curve()
    portfolio = load_portfolio()

    profile = compute_ultra_long_profile(portfolio, curve)

    print()
    print(f"Ultra-long threshold: >= {profile.threshold_years:.0f}Y")
    print(f"Curve tenors: {list(profile.tenors)}")
    print(f"Ultra-long tenors on this grid: {list(profile.ultra_long_tenors)}")

    print()
    print("Portfolio KRD by tenor (years):")
    print(profile.krd_by_tenor.to_string(float_format=lambda v: f"{v:8.4f}"))
    print()
    print("Portfolio DV01 by tenor (currency per 100 face, per 1bp):")
    print(profile.dv01_by_tenor.to_string(float_format=lambda v: f"{v:8.6f}"))

    chart_path = plot_ultra_long_profile(profile)

    print()
    print("=" * 72)
    print("INTERPRETIVE SUMMARY")
    print("=" * 72)
    print(
        f"Total portfolio KRD (sum across all tenors):  {profile.krd_total:8.4f} years"
    )
    print(
        f"  of which ultra-long (>= {profile.threshold_years:.0f}Y):        "
        f"{profile.krd_ultra_long:8.4f} years  "
        f"({profile.krd_ultra_long_share:.1%} of total)"
    )
    print(
        f"Total portfolio DV01 (sum across all tenors):  {profile.dv01_total:8.6f} per 100 face"
    )
    print(
        f"  of which ultra-long (>= {profile.threshold_years:.0f}Y):        "
        f"{profile.dv01_ultra_long:8.6f} per 100 face  "
        f"({profile.dv01_ultra_long_share:.1%} of total)"
    )
    print()
    print(
        f"-> {profile.dv01_ultra_long_share:.0%} of this portfolio's total interest-rate risk "
        f"(DV01) sits in tenors of {profile.threshold_years:.0f} years or longer -- "
        f"driven by its 20Y/30Y/40Y holdings' combined weight allocation."
    )
    print(f"Chart saved to: {chart_path}")
