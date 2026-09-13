"""
factor_exposure.py

Phase 4C: projects the portfolio's existing KRD/DV01 profile (Phase
3A/3B) onto the PCA risk factors Phase 4B estimated, to show which of
those factors the portfolio is actually exposed to. Computes no new
sensitivity of its own -- same "recombine what's already validated"
pattern ultra_long_profile.py uses for its own split.

THE QUESTION THIS ANSWERS: if factor c (PC1, PC2, ...) moves by its own
+1 standard deviation, what happens to this portfolio's value? Answered
in both units this project already uses -- percent (from the KRD vector)
and currency per 100 face value (from the DV01 vector, matching Phase
3B's convention exactly, not inventing a new one).

TENOR ALIGNMENT -- the problem Phase 4C's brief calls out explicitly.
key_rate_duration_portfolio computes a KRD value at EVERY tenor on
whatever curve it's given (see models/key_rate_duration.py's own
docstring: "no assumption... KRD tenors are read off whatever curve the
caller passes in"). Phase 4B's PCA loadings live on a DIFFERENT grid --
whatever load_jgb_curve_history's ragged-tenor policy retained for its
lookback window, which need not match load_jgb_curve()'s grid at all
(confirmed directly: the Phase 1 snapshot's 12-tenor grid includes
sub-1-year bills {0.083, 0.25, 0.5} that Phase 4A's grid never has, while
Phase 4A's 15-tenor grid includes {4, 6, 8, 9, 15, 25} that Phase 1's
snapshot never has -- fewer than half the tenors are shared).

Two ways to reconcile this were available, and only one keeps every
number meaningful:

  (a) Restrict to the intersection of the two grids, discarding whatever
      each side has that the other doesn't.
  (b) Recompute KRD/DV01 directly on the PCA's OWN grid, by building a
      curve whose tenor points are exactly the PCA tenors -- yield
      LEVELS interpolated from load_jgb_curve()'s curve via
      bond_pricing.curve_yield_at(), the same interpolation price_bond
      already relies on for every cash flow that doesn't land exactly on
      a curve grid point.

(a) throws away real information (a portfolio bond's KRD at a tenor nulled
out by the intersection isn't wrong, it's just gone) and, worse, KRD
values aren't safe to interpolate directly even for tenors kept on
"the other side" -- a KRD is a tent-shaped sensitivity tied to its own
grid's specific neighboring points (models/key_rate_duration.py's own
docstring), not a smooth function of maturity that interpolation would
recover correctly.

(b) has neither problem: key_rate_duration_portfolio already supports an
arbitrary tenor grid by design (nothing to change there), and the only
interpolation involved is of the curve's own yield LEVELS -- which is
exactly what curve_yield_at is for, not a new kind of approximation. This
module uses (b): _curve_aligned_to_pca_grid() builds that curve, and
every KRD/DV01 number this module reports is computed fresh against it,
never carried over from Phase 3A/3B's own native-grid output. (Their
native-grid numbers remain valid on their own terms -- this is a
different, PCA-grid-specific computation, not a correction of theirs.)

LINEAR (KRD-BASED) ESTIMATE, VALIDATED AGAINST AN EXACT REPRICE. The
exposure figure here is Sigma_k KRD_k * shock_k -- a first-order,
duration-based estimate of a MULTI-tenor curve move, the standard
parametric approach to sizing a factor's P&L (RiskMetrics-style
parametric VaR works the same way: sensitivity times factor shock, summed
across risk factors). It is an approximation -- ignores the convexity a
full reprice under the actual shocked curve would capture -- so __main__
checks it against exactly that: build the shocked curve, reprice the
whole portfolio via price_portfolio, and compare. See docs/
phase_4c_documentation.md for the actual numbers; the gap is small at
these shock sizes (single-digit basis points), consistent with a
linear approximation being reasonable at this magnitude, not merely
assumed to be.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe -- see ultra_long_profile.py's own
# identical reasoning; this module only ever saves a PNG.
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_loader import load_jgb_curve
from data.jgb_curve_history_loader import load_jgb_curve_history
from models.bond_pricing import curve_yield_at, price_portfolio
from models.dv01 import dv01_by_tenor_portfolio
from models.key_rate_duration import DEFAULT_BUMP_SIZE, key_rate_duration_portfolio
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca

DEFAULT_CHART_PATH = Path(__file__).resolve().parent.parent / "outputs" / "factor_exposure.png"

# Diverging pair (gain / loss) from this project's validated dataviz
# palette -- blue<->red poles that read as opposite, the right encoding
# for a signed P&L figure (a "which group" categorical pair, like
# ultra_long_profile.py's blue/red, would be the wrong job here: every
# bar in this chart answers the same question -- gain or loss -- just
# with a different sign).
COLOR_GAIN = "#2a78d6"
COLOR_LOSS = "#e34948"


def _curve_aligned_to_pca_grid(curve: pd.DataFrame, pca_tenors: np.ndarray) -> tuple[pd.DataFrame, np.ndarray]:
    """Build a curve whose tenor grid is EXACTLY pca_tenors, by
    interpolating `curve`'s own yield levels via curve_yield_at (see
    module docstring §"tenor alignment" for why this is the right
    reconciliation, not a restriction to the two grids' intersection).

    Returns (aligned_curve, extrapolated_tenors) -- the second element is
    whichever pca_tenors fall outside curve's own [min, max] tenor range,
    where curve_yield_at's flat extrapolation (not interpolation) is what
    actually produced that point's yield. Empty on every grid this
    project's own loaders currently produce (Phase 4A's grid is a subset
    of Phase 1's own live/snapshot range), but checked and reported
    rather than assumed, the same standard the rest of this project holds
    itself to.
    """
    curve_min = float(curve["maturity_years"].min())
    curve_max = float(curve["maturity_years"].max())
    extrapolated = pca_tenors[(pca_tenors < curve_min) | (pca_tenors > curve_max)]

    aligned_yields = curve_yield_at(curve, pca_tenors)
    aligned = pd.DataFrame({"maturity_years": pca_tenors, "yield": aligned_yields})
    aligned = aligned.sort_values("maturity_years").reset_index(drop=True)
    return aligned, extrapolated


@dataclass(frozen=True)
class FactorExposure:
    """One PCA component's portfolio exposure -- a linear (KRD/DV01-based)
    estimate of the P&L from a +1 standard deviation move in that
    component, in both units this project already uses.

    component : 1-indexed PCA component number (matches CurvePCAResult's
        own 1-indexing).
    explained_variance_ratio : that component's own share of curve-change
        variance (carried over from the CurvePCAResult this was computed
        against, for a reader who only wants this table).
    pct_pnl : Sigma_k -KRD_k * shock_k -- fractional portfolio price
        change (e.g. -0.0029 == -0.29%).
    dollar_pnl : Sigma_k -DV01_k * (shock_k / bump_size) -- the same P&L
        in currency per 100 face value, Phase 3B's own DV01 convention.
        Computed independently from the DV01 vector, not derived as
        pct_pnl * base_price -- see compute_portfolio_factor_exposure's
        docstring for why the two aren't expected to match exactly.
    """

    component: int
    explained_variance_ratio: float
    pct_pnl: float
    dollar_pnl: float


@dataclass(frozen=True)
class PortfolioFactorExposureResult:
    """Full Phase 4C result -- frozen, a snapshot of one portfolio against
    one curve and one PCA fit.

    pca_tenors : the PCA loadings' own tenor grid (Phase 4A/4B's, not
        necessarily Phase 1's).
    aligned_curve : the curve actually used for every KRD/DV01 number
        below -- Phase 1's curve, reindexed onto pca_tenors (see module
        docstring). Kept on the result for transparency: a reader can see
        exactly what was priced against, not just trust it happened.
    extrapolated_tenors : which pca_tenors (if any) fell outside Phase
        1's own curve range and so used flat extrapolation, not
        interpolation, to get a yield level. Empty on this project's real
        data (checked, see _curve_aligned_to_pca_grid).
    krd_by_tenor / dv01_by_tenor : the portfolio's total KRD / DV01 on
        aligned_curve's grid -- NOT the same numbers Phase 3A/3B report
        on Phase 1's native grid (a different, denser or sparser grid
        splits the same total risk differently across tenors; see
        docs/phase_4c_documentation.md for the actual gap).
    base_price : portfolio weighted price on aligned_curve, currency per
        100 face value -- Phase 2B's own pricing convention.
    exposures : one FactorExposure per component in the PCA fit, in
        component order.
    """

    pca_lookback_years: float | None
    pca_window_start: str
    pca_window_end: str
    pca_tenors: np.ndarray
    aligned_curve: pd.DataFrame
    extrapolated_tenors: np.ndarray
    krd_by_tenor: pd.Series
    dv01_by_tenor: pd.Series
    base_price: float
    exposures: list[FactorExposure]

    def dominant_component(self) -> FactorExposure:
        """The FactorExposure with the largest |dollar_pnl| -- "which
        factor actually drives this portfolio's risk," the question
        Phase 4C's brief asks __main__ to answer."""
        return max(self.exposures, key=lambda e: abs(e.dollar_pnl))


def compute_portfolio_factor_exposure(
    portfolio: list[Bond],
    curve: pd.DataFrame,
    pca_result: CurvePCAResult,
    freq: int | None = None,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> PortfolioFactorExposureResult:
    """Project `portfolio`'s KRD/DV01 profile onto `pca_result`'s
    components.

    Parameters
    ----------
    portfolio : as returned by config.portfolio_loader.load_portfolio.
    curve : today's curve (data.jgb_curve_loader.load_jgb_curve) -- the
        yield LEVELS this reindexes onto pca_result's own tenor grid (see
        module docstring). Not `pca_result`'s own history -- that's yield
        CHANGES, already consumed when `pca_result` was fit.
    pca_result : a models.pca.compute_curve_pca(...) result.
    freq : passed through to key_rate_duration_portfolio /
        dv01_by_tenor_portfolio / price_portfolio unchanged -- None
        (default) means each bond is priced at its own bond.freq; an
        explicit int overrides every bond to that one shared frequency
        (see models.bond_pricing.price_portfolio's own docstring).
    bump_size : passed through to key_rate_duration_portfolio /
        dv01_by_tenor_portfolio unchanged.

    Returns
    -------
    PortfolioFactorExposureResult

    Note on pct_pnl vs. dollar_pnl not agreeing exactly with
    (pct_pnl * base_price): dv01_by_tenor_portfolio weights each bond's
    DV01 by that bond's OWN price, while key_rate_duration_portfolio
    weights each bond's KRD by portfolio weight alone (a percentage
    doesn't carry a price to weight by). The two portfolio_total rows are
    therefore each other's price-weighted-average version, not identical
    up to a single scalar -- expect the two P&L figures to be close
    (every bond here prices near par by construction, see
    config/portfolio.json's own disclaimer) but not bit-identical. Both
    are reported because both are real, independently-computed numbers,
    not one derived from the other.
    """
    pca_tenors = pca_result.tenors
    aligned_curve, extrapolated_tenors = _curve_aligned_to_pca_grid(curve, pca_tenors)

    krd_row = key_rate_duration_portfolio(portfolio, aligned_curve, freq=freq, bump_size=bump_size).loc[
        "portfolio_total"
    ]
    dv01_row = dv01_by_tenor_portfolio(portfolio, aligned_curve, freq=freq, bump_size=bump_size).loc[
        "portfolio_total"
    ]
    base_price = float(
        (
            price_portfolio(portfolio, aligned_curve, freq=freq)
            .assign(weighted=lambda df: df["weight"] * df["price"])["weighted"]
        ).sum()
    )

    exposures = []
    for component in range(1, pca_result.n_components + 1):
        shock = pca_result.implied_yield_shock(component).reindex(krd_row.index)
        pct_pnl = -float((krd_row * shock).sum())
        dollar_pnl = -float((dv01_row * (shock / bump_size)).sum())
        exposures.append(
            FactorExposure(
                component=component,
                explained_variance_ratio=float(pca_result.explained_variance_ratio[component - 1]),
                pct_pnl=pct_pnl,
                dollar_pnl=dollar_pnl,
            )
        )

    return PortfolioFactorExposureResult(
        pca_lookback_years=pca_result.lookback_years,
        pca_window_start=pca_result.window_start,
        pca_window_end=pca_result.window_end,
        pca_tenors=pca_tenors,
        aligned_curve=aligned_curve,
        extrapolated_tenors=extrapolated_tenors,
        krd_by_tenor=krd_row,
        dv01_by_tenor=dv01_row,
        base_price=base_price,
        exposures=exposures,
    )


def plot_factor_exposure(
    result: PortfolioFactorExposureResult,
    output_path: str | Path = DEFAULT_CHART_PATH,
    metric: str = "dollar",
) -> Path:
    """Bar chart of each component's +1-std P&L impact, gain/loss colored
    (see module-level COLOR_GAIN/COLOR_LOSS), saved as a PNG. Returns the
    resolved Path.

    `metric`: "dollar" (default, currency per 100 face -- the more
    concrete figure a risk committee would look at, same default choice
    plot_ultra_long_profile makes) or "pct".
    """
    if metric not in ("dollar", "pct"):
        raise ValueError(f"metric must be 'dollar' or 'pct', got {metric!r}")

    values = np.array(
        [e.dollar_pnl if metric == "dollar" else e.pct_pnl for e in result.exposures]
    )
    labels = [
        f"PC{e.component}\n({e.explained_variance_ratio:.1%} of variance)" for e in result.exposures
    ]
    colors = [COLOR_GAIN if v >= 0 else COLOR_LOSS for v in values]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(labels, values, color=colors)
    ax.axhline(0, color="#52514e", linewidth=0.8)

    ax.set_ylabel(
        "P&L for a +1 std factor move (currency per 100 face)"
        if metric == "dollar"
        else "P&L for a +1 std factor move (% of portfolio value)"
    )
    ax.set_title("Portfolio factor exposure: +1 standard deviation move per PCA component")

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(color=COLOR_GAIN, label="Gain"),
            Patch(color=COLOR_LOSS, label="Loss"),
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
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS)
    pca_result = compute_curve_pca(history)

    result = compute_portfolio_factor_exposure(portfolio, curve, pca_result)

    print()
    print(
        f"PCA window: {result.pca_window_start} to {result.pca_window_end} "
        f"({len(result.pca_tenors)} tenors: {[float(t) for t in result.pca_tenors]})"
    )
    if len(result.extrapolated_tenors):
        print(
            f"NOTE: {list(result.extrapolated_tenors)} fell outside Phase 1's curve "
            "range and used flat extrapolation for their yield level."
        )
    print(f"Portfolio base price on the PCA-aligned grid (per 100 face): {result.base_price:.4f}")

    print()
    print("Factor exposure -- P&L for a +1 standard deviation move in each component:")
    for e in result.exposures:
        print(
            f"  PC{e.component} ({e.explained_variance_ratio:6.2%} of variance):   "
            f"{e.pct_pnl:+8.4%}   {e.dollar_pnl:+9.4f} per 100 face"
        )

    print()
    print("=" * 72)
    print("SANITY CHECK -- linear (KRD-based) estimate vs. an exact reprice")
    print("=" * 72)
    base_price_table = price_portfolio(portfolio, result.aligned_curve)
    for e in result.exposures:
        shock = pca_result.implied_yield_shock(e.component).reindex(result.krd_by_tenor.index)
        shocked_curve = result.aligned_curve.copy()
        shocked_curve["yield"] = shocked_curve["yield"] + shock.to_numpy()
        shocked_price_table = price_portfolio(portfolio, shocked_curve)
        exact_pct = (
            float((shocked_price_table["weight"] * shocked_price_table["price"]).sum()) - result.base_price
        ) / result.base_price
        print(
            f"  PC{e.component}:  linear = {e.pct_pnl:+.4%}   exact reprice = {exact_pct:+.4%}   "
            f"gap = {(e.pct_pnl - exact_pct):+.4%}"
        )

    chart_path = plot_factor_exposure(result)

    print()
    print("=" * 72)
    print("INTERPRETIVE SUMMARY")
    print("=" * 72)
    dominant = result.dominant_component()
    is_ultra_long = result.krd_by_tenor.index.to_numpy(dtype=float) >= 20.0
    aligned_dv01_ultra_long_share = float(
        result.dv01_by_tenor.to_numpy()[is_ultra_long].sum() / result.dv01_by_tenor.sum()
    )
    print(
        f"-> PC{dominant.component} dominates this portfolio's factor risk: "
        f"{dominant.dollar_pnl:+.4f} per 100 face ({dominant.pct_pnl:+.4%}) for a "
        f"+1 std move, explaining {dominant.explained_variance_ratio:.1%} of curve-change "
        "variance on its own."
    )
    print(
        f"-> {aligned_dv01_ultra_long_share:.0%} of this portfolio's DV01 (on the PCA-aligned "
        "grid) sits at 20Y or beyond -- consistent with Phase 3C's finding that the "
        "portfolio's ultra-long holdings dominate its overall interest-rate risk. PC1's "
        "loadings stay elevated from the belly through the 40Y point (see "
        "docs/phase_4c_documentation.md), so a level move disproportionately hits exactly "
        "the segment this portfolio is already concentrated in -- the two findings agree, "
        "not by construction, but because both are reading the same underlying portfolio."
    )
    print(f"Chart saved to: {chart_path}")
