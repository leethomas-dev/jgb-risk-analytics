"""
diebold_li.py

Phase 4.5B addition: Diebold & Li's (2006) DYNAMIC Nelson-Siegel -- fixing
the decay parameter tau at one constant value (rather than re-optimizing
it at every date, as models/curve_fitting.py's cross-sectional fit does),
which makes calibration at every date an ordinary least squares problem:
fast, stable, and guaranteed to converge, with no grid search or
non-linear optimizer needed at all once tau is fixed. That, in turn, is
what makes it practical to run across an entire historical curve series
and get TIME SERIES of beta0, beta1, beta2 -- read conventionally as
level, slope, and curvature, the same reading models.pca.CurvePCAResult
gives its first three principal components. Having both lets Phase 4C
(or a future Part C) compare two independently-derived level/slope/
curvature time series, not just a single cross-sectional snapshot.

WHY A SEPARATE MODULE, NOT AN EXTENSION OF models/curve_fitting.py. That
module's whole design center is the OPPOSITE problem: tau is genuinely
unknown and must be found (carefully, because it's unstable -- a bounded
two-stage grid search, docs/phase_4_5b_documentation.md §4). Here tau is
a fixed, externally-chosen constant and the entire fitting problem
collapses to one linear regression -- bolting that onto curve_fitting.py
would mean one module doing two conceptually different jobs (search for
an unknown non-linear parameter vs. batch-regress with a known one) behind
one set of names, which is exactly the kind of "two different contracts,
one file" situation docs/phase_4a_documentation.md §1.1 already argued
against when a sibling module (not an extension) was chosen for a
similarly-shaped problem. What genuinely IS shared -- the Nelson-Siegel
basis functions themselves -- is imported directly
(models.curve_fitting.hump_factors), not re-derived: one implementation
of the model's math, two different fitting strategies built on top of it.

WHY FIT THE BOOTSTRAPPED ZERO CURVE, NOT THE RAW HISTORICAL PAR SERIES.
Consistent with Part 4.5A/4.5B's own principle (never fit a parametric
curve to a par-treated series directly when a bootstrapped zero curve is
available -- docs/phase_4_5b_documentation.md §2): every date in
data.jgb_curve_history_loader.load_jgb_curve_history's output is first
run through models.bootstrap.bootstrap_zero_curve_history (Phase 4.5A's
own batch extension, added alongside this module) before any Diebold-Li
fitting happens. This means every date's fit inherits Part A's own
measured coupon-effect simplification (docs/phase_4_5a_documentation.md
§1/§5) -- not just "today's" curve, as before, but every historical date.

A DELIBERATE ASYMMETRY WITH THE EXISTING PCA MODULE, DISCLOSED. Phase 4B's
existing models.pca.compute_curve_pca operates directly on
load_jgb_curve_history's raw (par-treated) yield CHANGES -- it predates
Part 4.5 and was not rebuilt on the zero curve here, since doing so would
be exactly the kind of restructuring of already-shipped, tested work this
addition is instructed not to do. The comparison in §3 below therefore
sits across two different bases (Diebold-Li's zero-curve levels vs PCA's
raw-yield changes) in addition to the more obvious differences already
called out there (functional form, sign convention) -- named explicitly
as an added reason not to over-read the comparison, not glossed over.

1. FIXED-TAU OLS FITTING.

Given a fixed tau, y(m) = beta0 + beta1*f1(m,tau) + beta2*f2(m,tau) is
LINEAR in the betas (models.curve_fitting.hump_factors supplies f1, f2)
-- the design matrix depends only on the (fixed) tenor grid and tau, so
the betas that minimize squared error at any one date are a single
closed-form least-squares solve. Because every date within one
load_jgb_curve_history() window shares the SAME tenor grid (that
loader's own ragged-tenor policy already guarantees a complete, tenor-
consistent matrix for its window -- docs/phase_4a_documentation.md §1.3),
the design matrix is built ONCE per fit and every date in the window is
solved in a single vectorized `numpy.linalg.lstsq` call (one matrix, many
right-hand sides) rather than one Python-level regression per date.

2. THE TAU CHOICE -- DERIVED FOR JGBS, NOT COPIED FROM THE ORIGINAL PAPER.

Diebold & Li (2006) fixed tau at the value maximizing the curvature
loading at a 30-month maturity, for US Treasuries -- a market whose
quoted curve mostly runs under 10 years. JGB curves run to 40 years with
genuine, real long-end curvature (docs/phase_3c_documentation.md,
docs/phase_4b_documentation.md), so copying that specific value (or even
its "target maturity" framing) uncritically would center the model's
single hump at a point poorly matched to Japan's own tenor range for no
principled reason. Instead, tau is chosen empirically here: swept across
a bounded grid, fit (via the same closed-form OLS) to every date in the
project's default historical window, and DEFAULT_TAU set to the value
minimizing the MEDIAN per-date RMSE across that whole sample -- median,
not mean, so a handful of genuinely hard dates (§2 below) don't pull the
choice away from what fits typical days well. See tau_sensitivity() and
docs/phase_4_5b_dl_documentation.md §2 for the actual sweep and result
(DEFAULT_TAU = 7.0 years, materially different from the ~34-year tau this
project's OWN cross-sectional fit finds for the single most recent date --
models/curve_fitting.py -- itself an illustration of exactly why a fixed,
historically-representative tau is the more stable choice for a time
series, rather than re-optimizing tau at every date and risking the
per-date instability that module was built to handle carefully).

3. RESPECTS PHASE 4A'S RAGGED-TENOR POLICY.

Nothing here assumes a fixed tenor list -- the tenor grid is read off
`history.columns` at call time, exactly like every other module in this
project reads a curve's tenors at runtime rather than hardcoding them
(docs/phase_4a_documentation.md §1.3, docs/phase_1_documentation.md §3.5).
A different lookback window can (and does, per Phase 4A's own documented
findings) retain a different tenor set entirely; this module fits
whatever grid the window it's given actually has.

4. THE PCA COMPARISON, AND ITS SIGN AMBIGUITY.

A PCA loading vector's sign is mathematically arbitrary (fixed by
models.pca's own "largest-magnitude-positive" convention, which asserts
no economic direction -- docs/phase_4b_documentation.md §1.3). Comparing
a Diebold-Li beta series against a PCA score series can therefore show up
as a strong NEGATIVE correlation purely because of that arbitrary
convention, which would misread as "these measure opposite things" when
they may in fact track each other closely. compare_to_pca() reports BOTH
the raw (signed) correlation and a sign-aligned one (its absolute value,
mathematically identical to flipping the arbitrary-sign series before
correlating) -- explicitly, so a negative raw number is never mistaken
for an economically meaningful inverse relationship.

The comparison also uses CHANGES in beta, not beta LEVELS -- the same
non-stationarity argument models.pca's own module docstring already
makes for why PCA itself is fit on yield CHANGES, not levels: a beta
level series drifts with the general level of rates for reasons that have
nothing to do with which curve-movement pattern occurred on a given day,
while models.pca's component scores are, by construction, already a
day-to-day CHANGE series. Differencing the betas before correlating puts
both sides on the same (stationary, day-to-day-movement) footing; the
un-differenced comparison is not reported because it wouldn't be a fair
comparison of the same thing.

FINDING (see docs/phase_4_5b_dl_documentation.md §3 for the full numbers):
the correspondence is genuinely uneven across the three factors, not
uniformly strong or uniformly weak -- reported as an empirical result
either way, not adjusted to look more or less confirmatory than it is.

5. PLANNED SR 11-7 BENCHMARK MODEL, NOT BUILT HERE.

A smoothing spline (specifically Waggoner 1997, whose roughness penalty
varies by maturity segment -- heavy smoothing at the short end, more
flexibility at the volatile ultra-long end) is the intended BENCHMARK
MODEL this project's eventual Phase 6 SR 11-7 write-up will compare
Nelson-Siegel/Svensson/Diebold-Li against. Recorded here as a planned
future benchmark, per the phase brief -- not built, and no code in this
module depends on it existing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe -- this module only ever saves PNGs, never opens a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data.jgb_curve_history_loader import load_jgb_curve_history
from models.bootstrap import bootstrap_zero_curve_history
from models.curve_fitting import hump_factors
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca

# See module docstring "THE TAU CHOICE" -- derived from tau_sensitivity()
# against the project's default 2-year historical window
# (docs/phase_4_5b_dl_documentation.md §2), not copied from Diebold & Li's
# original US-Treasury-calibrated value. Rounded from the sweep's raw
# optimum (~6.89 years) to a clean, easily-communicated number: the
# median RMSE at 7.0 (5.47bp) is negligibly different from the sweep's
# exact minimum (5.42bp at 6.89) -- see the sensitivity table in the docs
# for how flat that region actually is.
DEFAULT_TAU = 7.0

# tau_sensitivity()'s own default sweep range/resolution -- deliberately
# coarser than curve_fitting.py's cross-sectional grid search (which
# solves one date at a time); this sweeps the SAME tau across an entire
# historical sample in one vectorized OLS call per candidate, so even a
# modest grid covers the space quickly.
TAU_SWEEP_MIN = 0.1
TAU_SWEEP_MAX = 50.0
TAU_SWEEP_POINTS = 200

# A date's fit is flagged as "notably poor" if its RMSE exceeds this
# multiple of the whole sample's median RMSE -- a simple, easily-explained
# rule (module docstring point 2 -- "median, not mean" reasoning extends
# here too: the threshold itself is relative to the typical day, not
# skewed by the very dates it's meant to flag).
POOR_FIT_RMSE_MULTIPLE = 2.0

DEFAULT_BETA_CHART_PATH = Path(__file__).resolve().parent.parent / "outputs" / "diebold_li_betas.png"
DEFAULT_PCA_COMPARISON_CHART_PATH = (
    Path(__file__).resolve().parent.parent / "outputs" / "diebold_li_vs_pca.png"
)


def _design_matrix(tau: float, tenors: np.ndarray) -> np.ndarray:
    f1, f2 = hump_factors(tau, tenors)
    return np.column_stack([np.ones_like(tenors), f1, f2])


def fit_diebold_li_cross_section(
    tenors: np.ndarray,
    rates: np.ndarray,
    tau: float = DEFAULT_TAU,
) -> tuple[np.ndarray, np.ndarray, float]:
    """One date's fixed-tau Nelson-Siegel fit -- ordinary least squares,
    no search. Returns (betas [beta0, beta1, beta2], fitted_rates,
    rmse_bp). Exists as a standalone, single-date function mainly so it
    can be tested directly against an independent closed-form solution
    (tests/test_diebold_li.py) and against curves generated from known
    parameters -- fit_diebold_li_history (below) uses the same
    `_design_matrix` but solves every date in a window in one vectorized
    call rather than looping this function per date.
    """
    tenors = np.asarray(tenors, dtype=float)
    rates = np.asarray(rates, dtype=float)
    X = _design_matrix(tau, tenors)
    betas, *_ = np.linalg.lstsq(X, rates, rcond=None)
    fitted = X @ betas
    rmse_bp = float(np.sqrt(np.mean((rates - fitted) ** 2)) * 10000.0)
    return betas, fitted, rmse_bp


def tau_sensitivity(
    history: pd.DataFrame,
    tau_grid: np.ndarray | None = None,
) -> pd.DataFrame:
    """Sweep candidate tau values and report fit quality across an ENTIRE
    historical sample for each one -- the empirical basis for DEFAULT_TAU
    (module docstring point 2), and the sensitivity check the phase brief
    asks for in its own right.

    Parameters
    ----------
    history : a raw (par-treated) curve history, as from
        data.jgb_curve_history_loader.load_jgb_curve_history -- bootstrapped
        to a zero curve internally (models.bootstrap.
        bootstrap_zero_curve_history) before any tau is tried.
    tau_grid : candidate tau values (years). Defaults to a log-spaced grid
        over [TAU_SWEEP_MIN, TAU_SWEEP_MAX].

    Returns
    -------
    pd.DataFrame, one row per candidate tau, columns [tau, median_rmse_bp,
    mean_rmse_bp] -- median is the more relevant figure for choosing a
    single fixed tau (module docstring), mean is reported alongside for
    transparency.
    """
    if tau_grid is None:
        tau_grid = np.geomspace(TAU_SWEEP_MIN, TAU_SWEEP_MAX, TAU_SWEEP_POINTS)

    zero_history = bootstrap_zero_curve_history(history)
    tenors = zero_history.columns.to_numpy(dtype=float)
    Y = zero_history.to_numpy().T  # tenors x dates

    rows = []
    for tau in tau_grid:
        X = _design_matrix(float(tau), tenors)
        betas, *_ = np.linalg.lstsq(X, Y, rcond=None)
        fitted = X @ betas
        rmse_per_date = np.sqrt(np.mean((Y - fitted) ** 2, axis=0)) * 10000.0
        rows.append(
            {
                "tau": float(tau),
                "median_rmse_bp": float(np.median(rmse_per_date)),
                "mean_rmse_bp": float(np.mean(rmse_per_date)),
            }
        )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class DieboldLiHistoryResult:
    """One fixed-tau Diebold-Li fit run across an entire historical
    window -- frozen, a snapshot of one (history, tau) pair.

    tau : the fixed decay parameter used for every date (module docstring
        point 2) -- NOT searched or re-optimized per date, unlike
        models.curve_fitting's cross-sectional fit.
    tenors : the (single, shared-across-every-date) tenor grid this fit
        ran on, read from the input history at call time (module
        docstring point 3).
    zero_curve_history : the bootstrapped zero rates (models.bootstrap.
        bootstrap_zero_curve_history) every date was actually fit
        against -- not the raw par-treated input.
    betas : date-indexed DataFrame, columns [beta0, beta1, beta2].
    fitted : date-indexed DataFrame, same shape as zero_curve_history --
        this model's own rate at each date/tenor.
    """

    tau: float
    tenors: np.ndarray
    zero_curve_history: pd.DataFrame
    betas: pd.DataFrame
    fitted: pd.DataFrame

    @property
    def rmse_bp(self) -> pd.Series:
        """Per-date RMSE in basis points -- computed on the fly from the
        stored target/fitted arrays (same derived-property reasoning as
        every other frozen result in this project, e.g.
        docs/phase_4b_documentation.md §1.4)."""
        residual = self.zero_curve_history - self.fitted
        return (np.sqrt((residual**2).mean(axis=1)) * 10000.0).rename("rmse_bp")

    def poor_fit_dates(self, multiple: float = POOR_FIT_RMSE_MULTIPLE) -> pd.DatetimeIndex:
        """Dates whose RMSE exceeds `multiple` times the WHOLE SAMPLE's
        median RMSE -- "notably poor," per the phase brief, flagged
        relative to a typical day rather than to an arbitrary absolute bp
        threshold that wouldn't generalize across different curve
        regimes."""
        rmse = self.rmse_bp
        threshold = rmse.median() * multiple
        return rmse.index[rmse > threshold]


def fit_diebold_li_history(
    history: pd.DataFrame,
    tau: float = DEFAULT_TAU,
) -> DieboldLiHistoryResult:
    """Fit fixed-tau Nelson-Siegel to every date in a curve history.

    Parameters
    ----------
    history : a raw (par-treated) curve history, as from
        data.jgb_curve_history_loader.load_jgb_curve_history. Bootstrapped
        to a zero curve internally for every date (module docstring "WHY
        FIT THE BOOTSTRAPPED ZERO CURVE") before fitting.
    tau : the fixed decay parameter, shared across every date (module
        docstring point 2). Defaults to DEFAULT_TAU.

    Returns
    -------
    DieboldLiHistoryResult
    """
    zero_history = bootstrap_zero_curve_history(history)
    tenors = zero_history.columns.to_numpy(dtype=float)
    Y = zero_history.to_numpy().T  # tenors x dates

    X = _design_matrix(tau, tenors)
    betas, *_ = np.linalg.lstsq(X, Y, rcond=None)  # 3 x n_dates, one closed-form solve for all dates
    fitted = X @ betas  # tenors x dates

    betas_df = pd.DataFrame(betas.T, index=history.index, columns=["beta0", "beta1", "beta2"])
    fitted_df = pd.DataFrame(fitted.T, index=zero_history.index, columns=zero_history.columns)

    return DieboldLiHistoryResult(
        tau=tau,
        tenors=tenors,
        zero_curve_history=zero_history,
        betas=betas_df,
        fitted=fitted_df,
    )


@dataclass(frozen=True)
class DieboldLiPCAComparison:
    """Result of comparing a DieboldLiHistoryResult's beta CHANGES against
    a CurvePCAResult's component score CHANGES over their common dates
    (module docstring point 4).

    pairs : one row per (beta, PCA component) pair actually compared
        (beta0<->PC1, beta1<->PC2, beta2<->PC3, whichever the PCA result
        actually has), columns [beta, pca_component, n_obs,
        raw_correlation, sign_aligned_correlation]. sign_aligned_correlation
        is abs(raw_correlation) -- mathematically identical to flipping
        the arbitrarily-signed PCA series before correlating.
    beta_changes / pca_scores : the two aligned, differenced/scored series
        the correlations above were computed from, restricted to their
        common dates -- exposed for plotting (plot_pca_comparison).
    """

    pairs: pd.DataFrame
    beta_changes: pd.DataFrame
    pca_scores: pd.DataFrame


def compare_to_pca(
    history: pd.DataFrame,
    dl_result: DieboldLiHistoryResult,
    pca_result: CurvePCAResult,
) -> DieboldLiPCAComparison:
    """Compare a Diebold-Li beta time series against Phase 4B's PCA
    component scores, over the curve history both were fit from.

    Parameters
    ----------
    history : the SAME raw curve history dl_result and pca_result were
        both fit from -- this function does not re-check that (a caller
        passing mismatched inputs would get a small or empty
        intersection of dates, not a silent wrong answer, since the join
        is by date index).
    dl_result : from fit_diebold_li_history(history, ...).
    pca_result : from models.pca.compute_curve_pca(history, ...).

    Returns
    -------
    DieboldLiPCAComparison
    """
    changes = history.sort_index().diff().dropna(how="any")
    centered = (changes - changes.mean()).to_numpy()
    # pca_result.loadings rows are already the sign-fixed unit-norm
    # eigenvectors models.pca computes (docs/phase_4b_documentation.md
    # §1.3) -- projecting the SAME centered changes onto them reproduces
    # exactly the (correctly signed) per-date component scores
    # compute_curve_pca computes internally via its own SVD, without
    # needing that module to expose them itself.
    scores = pd.DataFrame(
        centered @ pca_result.loadings.to_numpy().T,
        index=changes.index,
        columns=pca_result.loadings.index,
    )

    beta_changes = dl_result.betas.diff().dropna()
    common_dates = beta_changes.index.intersection(scores.index)

    pair_spec = [("beta0", 1), ("beta1", 2), ("beta2", 3)]
    rows = []
    for beta_col, component in pair_spec:
        if component not in scores.columns:
            continue
        b = beta_changes.loc[common_dates, beta_col].to_numpy()
        s = scores.loc[common_dates, component].to_numpy()
        raw_corr = float(np.corrcoef(b, s)[0, 1])
        rows.append(
            {
                "beta": beta_col,
                "pca_component": component,
                "n_obs": len(common_dates),
                "raw_correlation": raw_corr,
                "sign_aligned_correlation": abs(raw_corr),
            }
        )

    return DieboldLiPCAComparison(
        pairs=pd.DataFrame(rows),
        beta_changes=beta_changes.loc[common_dates],
        pca_scores=scores.loc[common_dates],
    )


def plot_beta_time_series(
    result: DieboldLiHistoryResult,
    output_path: str | Path = DEFAULT_BETA_CHART_PATH,
) -> Path:
    """The three beta time series (level/slope/curvature) over the fitted
    window, saved as a PNG."""
    fig, ax = plt.subplots(figsize=(11, 5))
    labels = {"beta0": "beta0 (level)", "beta1": "beta1 (slope)", "beta2": "beta2 (curvature)"}
    for col, label in labels.items():
        ax.plot(result.betas.index, result.betas[col] * 100.0, label=label)
    ax.axhline(0.0, color="black", linewidth=0.6)
    ax.set_xlabel("Date")
    ax.set_ylabel("Parameter value (%)")
    ax.set_title(f"Diebold-Li fixed-tau ($\\tau$={result.tau:.2f}y) Nelson-Siegel parameters over time")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_pca_comparison(
    comparison: DieboldLiPCAComparison,
    output_path: str | Path = DEFAULT_PCA_COMPARISON_CHART_PATH,
) -> Path:
    """The three paired (Diebold-Li beta change, PCA component score)
    series, standardized for visual comparability and sign-aligned per
    `comparison.pairs` (module docstring point 4), saved as a PNG."""
    pair_labels = {"beta0": "level", "beta1": "slope", "beta2": "curvature"}
    pairs_by_beta = comparison.pairs.set_index("beta")

    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    for ax, (beta_col, label) in zip(axes, pair_labels.items()):
        row = pairs_by_beta.loc[beta_col]
        component = int(row["pca_component"])
        sign = 1.0 if row["raw_correlation"] >= 0 else -1.0

        beta_series = comparison.beta_changes[beta_col]
        pca_series = comparison.pca_scores[component] * sign

        beta_std = (beta_series - beta_series.mean()) / beta_series.std()
        pca_std = (pca_series - pca_series.mean()) / pca_series.std()

        ax.plot(beta_std.index, beta_std, label=f"Δ{beta_col}", linewidth=1.2)
        ax.plot(pca_std.index, pca_std, label=f"PC{component} score (sign-aligned)", alpha=0.75, linewidth=1.2)
        ax.set_title(
            f"{beta_col} ({label}) vs. PC{component} -- "
            f"sign-aligned correlation = {row['sign_aligned_correlation']:.3f}"
        )
        ax.set_ylabel("Standardized")
        ax.legend(loc="upper right", fontsize=8)

    axes[-1].set_xlabel("Date")
    fig.autofmt_xdate()
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS)

    print()
    print(f"History window: {history.index.min().date()} to {history.index.max().date()} "
          f"({len(history)} dates, tenors: {[float(t) for t in history.columns]})")

    print()
    print("TAU SENSITIVITY -- median/mean RMSE (bp) across the whole sample, by candidate tau:")
    sensitivity = tau_sensitivity(history)
    best_row = sensitivity.loc[sensitivity["median_rmse_bp"].idxmin()]
    print(f"  Empirical optimum: tau = {best_row['tau']:.3f}  (median RMSE = {best_row['median_rmse_bp']:.3f}bp)")
    print(f"  DEFAULT_TAU = {DEFAULT_TAU} chosen from this sweep (see docs/phase_4_5b_dl_documentation.md §2)")
    for tau_probe in [0.5, 1, 2, 3, 5, DEFAULT_TAU, 10, 15, 20, 30, 40, 50]:
        row = sensitivity.iloc[(sensitivity["tau"] - tau_probe).abs().argmin()]
        print(f"    tau~={row['tau']:6.3f}  median={row['median_rmse_bp']:7.3f}bp  mean={row['mean_rmse_bp']:7.3f}bp")

    print()
    result = fit_diebold_li_history(history)
    print(f"Fitted Diebold-Li (tau={result.tau}) across all {len(result.betas)} dates.")
    print()
    print("Beta time series summary:")
    print(result.betas.describe().to_string(float_format=lambda v: f"{v:+.5f}"))

    print()
    rmse = result.rmse_bp
    print(f"Per-date RMSE: median={rmse.median():.3f}bp  mean={rmse.mean():.3f}bp  "
          f"min={rmse.min():.3f}bp  max={rmse.max():.3f}bp")
    poor_dates = result.poor_fit_dates()
    print(f"Poor-fit dates (RMSE > {POOR_FIT_RMSE_MULTIPLE}x median): {len(poor_dates)} of {len(rmse)}")
    if len(poor_dates):
        worst = rmse.loc[poor_dates].sort_values(ascending=False)
        print("  Worst 5:")
        for date, value in worst.head(5).items():
            print(f"    {date.date()}  {value:.3f}bp")

    beta_chart = plot_beta_time_series(result)
    print(f"\nBeta time series chart saved to: {beta_chart}")

    print()
    print("=" * 78)
    print("COMPARISON TO PHASE 4B'S PCA FACTORS")
    print("=" * 78)
    pca_result = compute_curve_pca(history)
    comparison = compare_to_pca(history, result, pca_result)
    print(comparison.pairs.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))

    comparison_chart = plot_pca_comparison(comparison)
    print(f"\nPCA comparison chart saved to: {comparison_chart}")
