"""
ultra_long_profile.py

The Japan-specific angle of this project: a focused view on the ultra-long
(20Y+) segment of the curve. Ultra-long JGB demand -- insurers and pension
funds extending duration at the long end, against thin issuance -- has
been a persistent, real market theme, and the illustrative portfolio
(Phase 2A) deliberately puts ~40% of its weight in the 20Y/30Y/40Y bonds
specifically so this metric has something real to show.

This module computes NO new sensitivity of its own. It takes the
portfolio-level Key Rate Duration table (models.key_rate_duration.
key_rate_duration_portfolio, Phase 3A) and the portfolio-level per-tenor
DV01 table (models.dv01.dv01_by_tenor_portfolio, Phase 3B) -- both already
built and validated -- and splits each one's "portfolio_total" row into
two groups by tenor: "ultra-long" (maturity >= a threshold) and
everything else. It is a slice-and-sum over numbers Phase 3A/3B already
produced, not a fourth bump-and-reprice calculation.

WHY A THRESHOLD, NOT A HARDCODED TENOR LIST: "which tenors count as
ultra-long" is answered by applying ULTRA_LONG_THRESHOLD_YEARS (>= 20Y) to
whatever tenor grid the curve actually returns at call time -- the same
non-fixed-tenor-grid contract every other module in this project follows
(Phase 1 §4.5, Phase 2B §1.1, Phase 3A §3.2, Phase 3B §3.2). A hardcoded
list like [20, 25, 30, 40] would silently stop including a tenor the
moment the curve's source tier changed to one that doesn't quote it, or
silently miss a new one a future source added -- exactly the failure mode
that contract exists to prevent. Concretely: the Phase 1 embedded
snapshot's grid classifies {20, 30, 40} as ultra-long; the live/cache
grid's classifies {20, 25, 30, 40} -- two different sets, both correct,
because both are the threshold applied fresh to that grid.

Reads the portfolio via config.portfolio_loader.load_portfolio() and the
curve via data.jgb_curve_loader.load_jgb_curve() -- this module hardcodes
neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe: this module only ever saves a PNG,
# never shows an interactive window, so a GUI backend is never needed and
# would be a spurious dependency (and a possible crash) on a machine with
# no display -- e.g. a CI runner or a validator's sandboxed environment.
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from models.dv01 import DEFAULT_BUMP_SIZE, dv01_by_tenor_portfolio
from models.key_rate_duration import key_rate_duration_portfolio

# The Part C requirement's own example threshold, and the conventional
# cutoff in JGB market commentary for "ultra-long" as opposed to merely
# "long" (which usually means 10Y+): 20Y is where insurer/pension
# duration-extension demand and BOJ purchase technicals are most commonly
# discussed as a distinct segment. Also matches the illustrative
# portfolio's own design -- its 20Y/30Y/40Y bonds are exactly the ones
# meant to populate this segment (Phase 2A §1.1: "The curve extends to 40Y
# specifically to support the Ultra-Long Duration Profile metric").
DEFAULT_ULTRA_LONG_THRESHOLD_YEARS = 20.0

# outputs/ sits next to models/ at the repo root, resolved from this
# module's own location rather than the caller's working directory -- the
# same pattern DEFAULT_PORTFOLIO_PATH uses in config/portfolio_loader.py
# and CACHE_PATH uses in data/jgb_curve_loader.py.
DEFAULT_CHART_PATH = Path(__file__).resolve().parent.parent / "outputs" / "ultra_long_dv01_profile.png"


@dataclass(frozen=True)
class UltraLongProfile:
    """The ultra-long segment's share of portfolio-level interest rate
    risk, at both the KRD (percentage-terms) and DV01 (currency-terms)
    level. Frozen -- a computed profile is a snapshot of one portfolio
    against one curve at one threshold; recompute rather than mutate if
    any of those change, the same reasoning config/portfolio_loader.py's
    Bond is frozen for.

    threshold_years : the maturity cutoff used (>= counts as ultra-long).
    tenors : every tenor on the curve's own grid, ascending.
    ultra_long_tenors : the subset of `tenors` that met the threshold --
        printed explicitly because, per the module's non-fixed-grid
        design, this set is not knowable ahead of time from the threshold
        alone; it depends on which tenors the curve actually quoted.
    krd_by_tenor / dv01_by_tenor : the portfolio's total (weighted) KRD /
        DV01 at each tenor -- the same "portfolio_total" rows
        key_rate_duration_portfolio / dv01_by_tenor_portfolio already
        produce, carried through unchanged.
    krd_total / dv01_total : sum across ALL tenors -- approximates the
        portfolio's overall (parallel-shift) duration / DV01, to the same
        precision Phase 3A §2 / Phase 3B §2 already establish for a single
        bond (the portfolio total is a weighted sum of per-bond figures
        that each carry that same small, explained gap).
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
        and the number Part C's own requirements name as "the number a
        risk committee would actually ask for"."""
        return self.dv01_ultra_long / self.dv01_total


def compute_ultra_long_profile(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    threshold_years: float = DEFAULT_ULTRA_LONG_THRESHOLD_YEARS,
    freq: int = 2,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> UltraLongProfile:
    """Build the ultra-long profile for one portfolio against one curve.

    Calls key_rate_duration_portfolio (Phase 3A) and dv01_by_tenor_portfolio
    (Phase 3B) exactly once each, takes their "portfolio_total" rows, and
    splits each row's tenor columns at `threshold_years`. No pricing,
    bumping, or aggregation logic is reimplemented here -- see the module
    docstring for why.
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
    the ultra-long segment (>= threshold_years) visually distinguished by
    color, saved as a PNG to `output_path`. Returns the resolved Path.

    `metric`: "dv01" (default) or "krd". DV01 was chosen as the default
    over KRD because it is the currency-terms figure -- the one Part C's
    own requirements frame the interpretive summary around ("what fraction
    of total DV01 comes from beyond 20Y"), and the more concrete number a
    risk committee would actually look at on a chart. `metric="krd"`
    produces the same chart shape against the percentage-terms figures
    instead, for a caller who wants that view.
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
