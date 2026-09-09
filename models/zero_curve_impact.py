"""
zero_curve_impact.py

Phase 4.5C: two deliverables that both build on Parts A/B, kept in one
module because both are "take already-validated pieces and recombine
them" work, computing no new sensitivity of their own -- the same
pattern models/ultra_long_profile.py and models/factor_exposure.py
already use for their own slice-and-recombine jobs.

PART 1 -- QUANTIFYING THE PAR-YIELD SIMPLIFICATION'S COST. Phase 2B
discounted every cash flow at the curve's own quoted rate directly, never
a genuinely bootstrapped zero rate -- a named simplification carried
through Phase 3A/3B/4C (docs/phase_2b_documentation.md §3.2). Parts A and
B of this phase built the machinery to do better; this part turns
models.bond_pricing.price_bond's new dual-basis capability (see that
module's own docstring) into an actual measurement: reprice the standard
portfolio, and compute its effective duration and DV01, BOTH ways, and
report the difference. See compute_par_vs_zero_impact() and
docs/phase_4_5c_documentation.md §2 for the real numbers -- the gap
turns out to be small at the front end and dramatic at the long end
(a 2Y bond's price moves half a basis point; a 40Y bond's moves over
770bp), tracking the SAME mechanism Part A's own coupon-effect check
already found (docs/phase_4_5a_documentation.md §5): the further out a
cash flow sits, the more a curve's own quoted rate at that maturity
diverges from a genuinely bootstrapped zero rate there.

WHY A NEW MODULE, NOT ADDED TO bond_pricing.py / key_rate_duration.py /
dv01.py. Those modules each compute ONE thing (a price, a KRD, a DV01);
this module computes a COMPARISON across two curve bases of each --
recombination, not new sensitivity, the same reasoning
ultra_long_profile.py and factor_exposure.py were kept as their own
modules rather than folded into the pricing/KRD/DV01 code they slice.

THE ALIGNMENT PROBLEM, AND WHY IT'S SIMPLER HERE THAN PHASE 4C's. A par
curve and its bootstrapped zero curve have DIFFERENT native tenor grids
(the par curve's own ~12-15 quoted tenors vs. the zero curve's ~80-point
semiannual grid, docs/phase_4_5a_documentation.md). Two of this module's
three comparisons need no alignment at all: price_bond and
effective_duration_bond/dv01_bond each return ONE SCALAR per bond,
regardless of which grid priced it. Only the PER-TENOR KRD/DV01
breakdown needs a shared grid -- _aligned_zero_curve() builds one by
interpolating the zero curve's own zero_rate onto the PAR curve's native
tenors (models.bootstrap.zero_rate_at), the same reconciliation choice
Phase 4C's _curve_aligned_to_pca_grid made for a different pair of grids
(interpolate onto the target grid, not restrict to an intersection) --
for the same reason: it keeps every one of the par curve's own tenors in
the comparison rather than dropping whichever ones the zero curve's
coarser native grid didn't happen to share.

PART 2 -- DO NS/SVENSSON'S LOADINGS RESEMBLE PCA'S EIGENVECTORS?
Nelson-Siegel's three beta terms are conventionally read as level, slope,
and curvature -- the same reading PCA's own first three components get
(models.pca's own docstring). One is a functional form IMPOSED on the
curve; the other is a statistical decomposition ESTIMATED from real
curve-change history. compare_ns_shapes_to_pca() checks whether they
actually look alike: evaluate NS's own already-fitted basis functions
(1, f1(m, tau), f2(m, tau) -- models.curve_fitting.hump_factors, at the
SAME tau models.curve_fitting.fit_nelson_siegel already found) at PCA's
own tenor grid, normalize each to unit norm, and compare to PCA's own
unit-norm loading vectors via cosine similarity -- the standard way to
compare two loading/eigenvector SHAPES, distinct from the Pearson
correlation models.diebold_li.compare_to_pca uses for its own, different
comparison (two TIME SERIES that should co-move, not two static shapes).

SIGN. Unlike PCA's loading sign (mathematically arbitrary --
models.pca's own docstring), NS's basis functions have no sign ambiguity
of their own: f1(m, tau) = (1-exp(-m/tau))/(m/tau) is strictly positive
for any m, tau > 0, not a solver artifact. Only PCA's side of the
comparison can flip sign arbitrarily, so the same sign_aligned_*
treatment used throughout this project (docs/phase_4b_documentation.md
§1.3, docs/phase_4_5b_dl_documentation.md §4) is applied here too:
sign_aligned_cosine_similarity = abs(raw_cosine_similarity).

FINDING, REPORTED AS OBSERVED, NOT ASSUMED (docs/phase_4_5c_documentation.md
§3 for the full account): beta0's shape (a flat, all-ones vector) matches
PC1 closely -- PCA's own dominant "level" factor turns out to be close to
flat over this tenor range. beta1 and beta2 do NOT resemble PC2/PC3
closely under Nelson-Siegel's OWN best-fit tau (~34 years, Part B) --
because a tau that large produces basis functions that barely decay
across a 1-40Y grid at all, so beta1's "slope" shape stays positive and
high everywhere instead of swinging sign the way PC2 empirically does,
and beta2's "hump" never turns over within the visible tenor range. This
is checked to be tau-dependent, not a fixed property of Nelson-Siegel
itself: Svensson's much smaller tau1 (~2.9 years, Part B) produces a
visibly closer (though still partial) match to PC2. Neither is claimed
to be "correct" -- see the module docstring caution against overclaiming,
and models.diebold_li's own, structurally different (fixed-tau,
TIME-SERIES) comparison, which finds a much stronger slope
correspondence (0.955) under ITS OWN, smaller, historically-chosen tau --
itself informative evidence that tau size, not the Nelson-Siegel
functional form per se, drives how "slope-like" or "curvature-like" its
components actually look next to PCA's empirically-extracted ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe -- this module only ever saves PNGs, matching every other chart module here
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from data.jgb_curve_loader import load_jgb_curve
from models.bootstrap import DEFAULT_BOOTSTRAP_FREQ, bootstrap_zero_curve, zero_rate_at
from models.bond_pricing import price_bond
from models.curve_fitting import (
    NelsonSiegelFitResult,
    SvenssonFitResult,
    fit_nelson_siegel,
    fit_svensson,
    hump_factors,
)
from models.dv01 import DEFAULT_BUMP_SIZE, dv01_bond, dv01_by_tenor_portfolio
from models.key_rate_duration import effective_duration_bond, key_rate_duration_portfolio
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca

_OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"
CURVE_COMPARISON_CHART_PATH = _OUTPUTS_DIR / "par_zero_fitted_curve.png"
LOADINGS_COMPARISON_CHART_PATH = _OUTPUTS_DIR / "ns_vs_pca_loadings.png"


# ---------------------------------------------------------------------------
# Part 1: par-vs-zero pricing impact
# ---------------------------------------------------------------------------


def _aligned_zero_curve(par_curve: pd.DataFrame, zero_curve: pd.DataFrame) -> pd.DataFrame:
    """zero_curve's own zero_rate, interpolated onto par_curve's native
    tenor grid (models.bootstrap.zero_rate_at) -- see module docstring
    "THE ALIGNMENT PROBLEM". Used only for the per-tenor KRD/DV01
    comparison below; the scalar (price / effective-duration / DV01)
    comparisons use each curve's own native grid directly and need no
    alignment at all.
    """
    par_tenors = par_curve["maturity_years"].to_numpy(dtype=float)
    aligned_rates = zero_rate_at(zero_curve, par_tenors)
    aligned = pd.DataFrame({"maturity_years": par_tenors, "zero_rate": aligned_rates})
    return aligned.sort_values("maturity_years").reset_index(drop=True)


@dataclass(frozen=True)
class ParVsZeroImpactResult:
    """Frozen snapshot of one portfolio repriced against one par curve and
    its own bootstrapped zero curve.

    par_curve / zero_curve : the two curves actually used -- zero_curve is
        bootstrap_zero_curve(par_curve), on its own native (~80-point)
        grid.
    aligned_zero_curve : zero_curve's zero_rate reindexed onto par_curve's
        own tenors (module docstring) -- what the *_by_tenor_zero tables
        below were actually priced against, so the two curve bases can be
        compared column-for-column.
    per_bond : one row per bond -- par/zero price, effective duration, and
        DV01, plus each pair's difference. Every figure here comes from a
        single scalar-returning function (price_bond,
        effective_duration_bond, dv01_bond), so no alignment was needed to
        build it (module docstring).
    krd_by_tenor_par / krd_by_tenor_zero, dv01_by_tenor_par /
        dv01_by_tenor_zero : the full key_rate_duration_portfolio /
        dv01_by_tenor_portfolio tables under each basis -- the _zero
        tables run against aligned_zero_curve, so both members of each
        pair share identical columns.
    """

    par_curve: pd.DataFrame
    zero_curve: pd.DataFrame
    aligned_zero_curve: pd.DataFrame
    per_bond: pd.DataFrame
    krd_by_tenor_par: pd.DataFrame
    krd_by_tenor_zero: pd.DataFrame
    dv01_by_tenor_par: pd.DataFrame
    dv01_by_tenor_zero: pd.DataFrame

    def _weighted(self, column: str) -> float:
        return float((self.per_bond["weight"] * self.per_bond[column]).sum())

    @property
    def portfolio_price_par(self) -> float:
        return self._weighted("par_price")

    @property
    def portfolio_price_zero(self) -> float:
        return self._weighted("zero_price")

    @property
    def portfolio_duration_par(self) -> float:
        return self._weighted("par_duration")

    @property
    def portfolio_duration_zero(self) -> float:
        return self._weighted("zero_duration")

    @property
    def portfolio_dv01_par(self) -> float:
        return self._weighted("par_dv01")

    @property
    def portfolio_dv01_zero(self) -> float:
        return self._weighted("zero_dv01")


def compute_par_vs_zero_impact(
    portfolio: list[Bond],
    par_curve: pd.DataFrame,
    freq: int = DEFAULT_BOOTSTRAP_FREQ,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> ParVsZeroImpactResult:
    """Reprice `portfolio` against `par_curve` and its own bootstrapped
    zero curve, computing price, effective duration, and DV01 both ways
    for every bond, plus the aligned per-tenor KRD/DV01 tables.

    No new sensitivity math -- every number comes from
    models.bond_pricing.price_bond, models.key_rate_duration.
    effective_duration_bond/key_rate_duration_portfolio, or
    models.dv01.dv01_bond/dv01_by_tenor_portfolio, called once per curve
    basis (module docstring).
    """
    zero_curve = bootstrap_zero_curve(par_curve, freq=freq)
    aligned_zero = _aligned_zero_curve(par_curve, zero_curve)

    rows = []
    for bond in portfolio:
        args = (bond.face_value, bond.coupon_rate, bond.maturity_years)
        par_price = price_bond(*args, par_curve, freq=freq)
        zero_price = price_bond(*args, zero_curve, freq=freq)
        par_duration = effective_duration_bond(*args, par_curve, freq=freq, bump_size=bump_size)
        zero_duration = effective_duration_bond(*args, zero_curve, freq=freq, bump_size=bump_size)
        par_dv01 = dv01_bond(*args, par_curve, freq=freq, bump_size=bump_size)
        zero_dv01 = dv01_bond(*args, zero_curve, freq=freq, bump_size=bump_size)

        rows.append(
            {
                "name": bond.name,
                "weight": bond.weight,
                "maturity_years": bond.maturity_years,
                "par_price": par_price,
                "zero_price": zero_price,
                "price_diff_bp": (zero_price - par_price) / par_price * 10000.0,
                "par_duration": par_duration,
                "zero_duration": zero_duration,
                "duration_diff": zero_duration - par_duration,
                "par_dv01": par_dv01,
                "zero_dv01": zero_dv01,
                "dv01_diff": zero_dv01 - par_dv01,
                "dv01_diff_pct": (zero_dv01 - par_dv01) / par_dv01 * 100.0,
            }
        )
    per_bond = pd.DataFrame(rows)

    krd_by_tenor_par = key_rate_duration_portfolio(portfolio, par_curve, freq=freq, bump_size=bump_size)
    krd_by_tenor_zero = key_rate_duration_portfolio(portfolio, aligned_zero, freq=freq, bump_size=bump_size)
    dv01_by_tenor_par = dv01_by_tenor_portfolio(portfolio, par_curve, freq=freq, bump_size=bump_size)
    dv01_by_tenor_zero = dv01_by_tenor_portfolio(portfolio, aligned_zero, freq=freq, bump_size=bump_size)

    return ParVsZeroImpactResult(
        par_curve=par_curve,
        zero_curve=zero_curve,
        aligned_zero_curve=aligned_zero,
        per_bond=per_bond,
        krd_by_tenor_par=krd_by_tenor_par,
        krd_by_tenor_zero=krd_by_tenor_zero,
        dv01_by_tenor_par=dv01_by_tenor_par,
        dv01_by_tenor_zero=dv01_by_tenor_zero,
    )


def plot_par_zero_fitted_curve(
    par_curve: pd.DataFrame,
    zero_curve: pd.DataFrame,
    ns_result: NelsonSiegelFitResult | None = None,
    sv_result: SvenssonFitResult | None = None,
    output_path: str | Path = CURVE_COMPARISON_CHART_PATH,
) -> Path:
    """Par curve, bootstrapped zero curve, and (if supplied) the
    NS/Svensson parametric fits, all on one axis -- the chart the phase
    brief asks for, tying Parts A/B together visually."""
    fig, ax = plt.subplots(figsize=(11, 6))

    par_sorted = par_curve.sort_values("maturity_years")
    ax.plot(
        par_sorted["maturity_years"], par_sorted["yield"] * 100.0,
        "o-", label="Par curve (input)", color="#4C72B0", linewidth=1.5, markersize=5,
    )

    zero_sorted = zero_curve.sort_values("maturity_years")
    ax.plot(
        zero_sorted["maturity_years"], zero_sorted["zero_rate"] * 100.0,
        "-", label="Bootstrapped zero curve", color="#C44E52", linewidth=1.5,
    )

    fine_grid = np.linspace(max(0.1, par_sorted["maturity_years"].min()), par_sorted["maturity_years"].max(), 300)
    if ns_result is not None:
        ax.plot(fine_grid, ns_result.rate_at(fine_grid) * 100.0, "--", label="Nelson-Siegel fit", color="#55A868")
    if sv_result is not None:
        ax.plot(fine_grid, sv_result.rate_at(fine_grid) * 100.0, "--", label="Svensson fit", color="#8172B2")

    ax.set_xlabel("Maturity (years)")
    ax.set_ylabel("Rate (%)")
    ax.set_title("Par curve vs. bootstrapped zero curve vs. parametric fits")
    ax.legend()
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


# ---------------------------------------------------------------------------
# Part 2: NS/Svensson loading shapes vs. PCA loadings
# ---------------------------------------------------------------------------

# (beta name, PCA component) pairs this project reads as level/slope/
# curvature -- the same three-way reading models.pca and
# models.curve_fitting's own docstrings already use.
_LOADING_PAIRS = [("beta0", 1), ("beta1", 2), ("beta2", 3)]


def _ns_shape_vectors(tau: float, tenors: np.ndarray) -> dict[str, np.ndarray]:
    """Nelson-Siegel's three basis functions (1, f1, f2 -- module
    docstring), evaluated at `tenors` and L2-normalized to unit vectors,
    the same normalization CurvePCAResult.loadings' own rows already use
    -- so the two are directly comparable via cosine similarity."""
    f1, f2 = hump_factors(tau, tenors)
    raw = {"beta0": np.ones_like(tenors), "beta1": f1, "beta2": f2}
    return {name: vec / np.linalg.norm(vec) for name, vec in raw.items()}


def compare_ns_shapes_to_pca(
    ns_result: NelsonSiegelFitResult,
    pca_result: CurvePCAResult,
) -> pd.DataFrame:
    """Cosine similarity between Nelson-Siegel's (already-fitted) basis
    function shapes and PCA's own loading vectors, evaluated at PCA's own
    tenor grid -- module docstring "PART 2" for the method and the sign
    handling.

    Returns a DataFrame, one row per (beta, PCA component) pair actually
    available, columns [beta, pca_component, raw_cosine_similarity,
    sign_aligned_cosine_similarity].
    """
    shapes = _ns_shape_vectors(ns_result.tau, pca_result.tenors)
    rows = []
    for beta_name, component in _LOADING_PAIRS:
        if component not in pca_result.loadings.index:
            continue
        pca_vec = pca_result.loadings.loc[component].to_numpy()
        raw_cosine = float(np.dot(shapes[beta_name], pca_vec))
        rows.append(
            {
                "beta": beta_name,
                "pca_component": component,
                "raw_cosine_similarity": raw_cosine,
                "sign_aligned_cosine_similarity": abs(raw_cosine),
            }
        )
    return pd.DataFrame(rows)


def plot_loadings_comparison(
    ns_result: NelsonSiegelFitResult,
    pca_result: CurvePCAResult,
    comparison: pd.DataFrame,
    output_path: str | Path = LOADINGS_COMPARISON_CHART_PATH,
) -> Path:
    """The three paired (NS shape, PCA loading) vectors over the tenor
    axis, sign-aligned per `comparison`, one panel per pair."""
    shapes = _ns_shape_vectors(ns_result.tau, pca_result.tenors)
    labels = {"beta0": "level", "beta1": "slope", "beta2": "curvature"}
    by_beta = comparison.set_index("beta")

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharex=True)
    for ax, (beta_name, label) in zip(axes, labels.items()):
        row = by_beta.loc[beta_name]
        component = int(row["pca_component"])
        sign = 1.0 if row["raw_cosine_similarity"] >= 0 else -1.0

        ax.plot(pca_result.tenors, shapes[beta_name], "o-", label=f"NS {beta_name} shape", color="#55A868")
        ax.plot(
            pca_result.tenors, pca_result.loadings.loc[component].to_numpy() * sign,
            "s--", label=f"PC{component} loading (sign-aligned)", color="#4C72B0",
        )
        ax.set_title(f"{beta_name} ({label}) vs. PC{component}\ncosine = {row['sign_aligned_cosine_similarity']:.3f}")
        ax.set_xlabel("Maturity (years)")
        ax.legend(fontsize=8)
    axes[0].set_ylabel("Unit-norm loading")
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    par_curve = load_jgb_curve()
    portfolio = load_portfolio()

    print()
    print("=" * 78)
    print("PART 1 -- PAR VS. ZERO-CURVE PRICING IMPACT")
    print("=" * 78)
    impact = compute_par_vs_zero_impact(portfolio, par_curve)
    print(
        impact.per_bond.to_string(
            index=False,
            columns=[
                "name", "weight", "par_price", "zero_price", "price_diff_bp",
                "par_duration", "zero_duration", "duration_diff",
                "par_dv01", "zero_dv01", "dv01_diff_pct",
            ],
            formatters={
                "weight": lambda w: f"{w:.2%}",
                "par_price": lambda v: f"{v:.4f}",
                "zero_price": lambda v: f"{v:.4f}",
                "price_diff_bp": lambda v: f"{v:+8.2f}bp",
                "par_duration": lambda v: f"{v:.4f}",
                "zero_duration": lambda v: f"{v:.4f}",
                "duration_diff": lambda v: f"{v:+.4f}",
                "par_dv01": lambda v: f"{v:.6f}",
                "zero_dv01": lambda v: f"{v:.6f}",
                "dv01_diff_pct": lambda v: f"{v:+7.2f}%",
            },
        )
    )
    print()
    print(f"Portfolio price:     par = {impact.portfolio_price_par:.4f}   zero = {impact.portfolio_price_zero:.4f}   "
          f"diff = {(impact.portfolio_price_zero - impact.portfolio_price_par):+.4f}")
    print(f"Portfolio duration:  par = {impact.portfolio_duration_par:.4f}   zero = {impact.portfolio_duration_zero:.4f}   "
          f"diff = {(impact.portfolio_duration_zero - impact.portfolio_duration_par):+.4f}")
    print(f"Portfolio DV01:      par = {impact.portfolio_dv01_par:.6f}   zero = {impact.portfolio_dv01_zero:.6f}   "
          f"diff = {(impact.portfolio_dv01_zero - impact.portfolio_dv01_par):+.6f} "
          f"({(impact.portfolio_dv01_zero - impact.portfolio_dv01_par) / impact.portfolio_dv01_par:+.2%})")

    ns_result = fit_nelson_siegel(impact.zero_curve)
    sv_result = fit_svensson(impact.zero_curve)
    curve_chart = plot_par_zero_fitted_curve(par_curve, impact.zero_curve, ns_result, sv_result)
    print(f"\nCurve comparison chart saved to: {curve_chart}")

    print()
    print("=" * 78)
    print("PART 2 -- NS LOADING SHAPES VS. PCA LOADINGS")
    print("=" * 78)
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS)
    pca_result = compute_curve_pca(history)
    comparison = compare_ns_shapes_to_pca(ns_result, pca_result)
    print(f"(Nelson-Siegel fitted tau = {ns_result.tau:.3f} years)")
    print(comparison.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))

    loadings_chart = plot_loadings_comparison(ns_result, pca_result, comparison)
    print(f"\nLoadings comparison chart saved to: {loadings_chart}")
