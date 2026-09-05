"""
pca.py

Phase 4B: Principal Component Analysis (PCA) of JGB curve movements --
finding the small number of recurring shapes that explain most of how the
WHOLE curve actually moves day to day, instead of tracking every tenor's
risk as if it moved independently of every other tenor.

INPUT IS DAILY CHANGES, NOT YIELD LEVELS -- and this choice matters. Yield
LEVELS are strongly non-stationary (today's 10Y yield is close to
yesterday's, which is close to the day before) and highly correlated
across tenors for a reason that has nothing to do with risk: the whole
curve drifts with the level of rates over time. PCA on levels would
mostly just recover that drift as "PC1" -- a trend, not a risk factor. A
risk model cares about the covariance structure of MOVEMENTS: on a day
rates move, which tenors moved together and by how much. Daily first
differences are what actually feed into a P&L distribution, which is the
reason to compute PCA on them at all. compute_curve_pca() takes the
levels history load_jgb_curve_history() returns and differences it
internally (once, via .diff()) rather than asking every caller to
remember to do this themselves.

FITTED, NOT CALIBRATED. An earlier stage of planning this project assumed
this covariance structure would have to be hand-calibrated from plausible
assumptions, since no real curve history was available yet. That's no
longer true: Phase 4A's historical loader supplies real MOF data, so
every number this module reports -- the loadings, the explained variance,
the component volatilities -- is estimated directly from real observed
yield changes. Nothing here is an assumption dressed up as a result.

HAND-ROLLED SVD, NOT scikit-learn. This module does exactly ONE
eigendecomposition, on one small (n_obs x ~15) matrix. numpy's
np.linalg.svd already does this correctly, is already a dependency of
pandas (already in the project's dependency graph regardless), and
finishes in well under a second at this size. Pulling in scikit-learn --
a large library built for training/fitting pipelines, cross-validation,
many other estimators -- for one function call would be the heavy-
dependency-for-one-feature tradeoff this project deliberately avoided for
matplotlib too (docs/phase_3c_documentation.md §1.4), just more so:
matplotlib at least renders the one PNG this project's charts need;
scikit-learn's PCA would earn its keep only if this project were about to
run many more estimators, which it isn't. Revisit this if that changes.

REQUIRES A COMPLETE (NaN-free) HISTORY. load_jgb_curve_history's default
ragged-tenor policy already guarantees this for any window; this function
does not attempt to repair or work around missing data itself (see "What
would change this design" in docs/phase_4b_documentation.md for a
pairwise-complete-covariance approach that was built, tested, and
deliberately not kept -- the reasoning is documented there, not carried
as live complexity here).

SIGN CONVENTION. A PCA loading vector's sign is mathematically arbitrary
-- np.linalg.svd(X) and np.linalg.svd(-X) along one component both
decompose X correctly, just with every sign flipped on that component (and
its scores). Left alone, the sign that happens to come out is an artifact
of the solver, not a fact about the data, and can even flip between
otherwise-similar runs. This module fixes it deterministically: for each
component, flip its sign (and the matching column of U) so its
LARGEST-MAGNITUDE tenor loading is positive. This is the same rule scikit-
learn's internal `svd_flip` applies by default; it doesn't assert an
economic direction (e.g. "PC1 should mean yields up") -- it just makes
the output reproducible run to run. Once fixed, the actual sign PATTERN
across tenors is reported as an empirical finding (see
compute_curve_pca's docstring and the module's own tests) rather than
forced to match a textbook shape -- a Litterman-Scheinkman-style
level/slope/curvature reading is conventional for a sovereign curve, not
guaranteed for this one.

Reads history through data.jgb_curve_history_loader; computes no curve
data of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from data.jgb_curve_history_loader import load_jgb_curve_history

# ~2 years back from the loader's latest available date. Chosen to sit
# ENTIRELY after the BOJ's March 2024 exit from yield curve control (YCC):
# a window reaching further back would blend in years where short- and
# medium-tenor yields were deliberately pinned by policy, understating the
# volatility a CURRENT risk model should assume. The tradeoff, stated
# plainly: a 2-year window has ~500 daily observations against 15 tenors
# -- still comfortably enough for a stable covariance estimate (n_obs >>
# n_tenors) -- versus a 5-year window's ~1,250+, which would be more
# statistically comfortable but would dilute today's regime with the
# suppressed one. This project picks representativeness over sample size;
# see docs/phase_4b_documentation.md for the numbers behind this choice.
DEFAULT_PCA_LOOKBACK_YEARS = 2.0

DEFAULT_N_COMPONENTS = 3


@dataclass(frozen=True)
class CurvePCAResult:
    """One PCA fit's full result -- frozen, a snapshot of one history
    window. Recompute rather than mutate if the window or n_components
    changes.

    tenors : the tenor grid PCA was actually run on -- whatever
        load_jgb_curve_history's ragged-tenor policy retained for this
        window (ascending, float years).
    n_observations : number of daily-change rows PCA was fit on (one
        fewer than the number of dates in the source window, since a
        first difference consumes the first row).
    n_components : how many top components this result keeps (the
        eigendecomposition itself runs on the full tenor grid; only the
        top n_components are kept here, per the `n_components` argument
        to compute_curve_pca).
    explained_variance : absolute variance of each kept component's own
        score series, decimal-yield^2 units, sample variance (ddof=1) --
        NOT a share; explained_variance_ratio is the share.
    explained_variance_ratio : each kept component's share of TOTAL
        variance across the FULL tenor grid (i.e. across all possible
        components, not just the kept ones) -- explained_variance_ratio_all
        sums to exactly 1.0; this is a slice of it.
    explained_variance_ratio_all : the full spectrum, one entry per tenor
        -- kept for transparency (e.g. checking the top few really do
        dominate, not just asserted).
    loadings : DataFrame, index 1..n_components (name "component"),
        columns = tenors (name "maturity_years") -- each row a unit-norm
        eigenvector: how much that component moves each tenor, relative
        to the others, for a one-unit move in the component's own score.
    """

    lookback_years: float | None
    window_start: str
    window_end: str
    tenors: np.ndarray
    n_observations: int
    n_components: int
    explained_variance: np.ndarray
    explained_variance_ratio: np.ndarray
    explained_variance_ratio_all: np.ndarray
    loadings: pd.DataFrame

    @property
    def component_std(self) -> np.ndarray:
        """Standard deviation of each kept component's own daily score
        series, decimal-yield units -- i.e. what "one standard deviation
        of this factor" actually means in real yield-change terms.
        sqrt(explained_variance); not stored separately so it can't drift
        out of sync with it (docs/phase_3c_documentation.md §1.2's same
        reasoning for computing a derived figure on the fly)."""
        return np.sqrt(self.explained_variance)

    @property
    def cumulative_explained_variance_ratio(self) -> np.ndarray:
        """Running total of explained_variance_ratio -- cumulative_explained_variance_ratio[-1]
        is "how much of total curve-change variance the kept components
        explain together"."""
        return np.cumsum(self.explained_variance_ratio)

    def implied_yield_shock(self, component: int) -> pd.Series:
        """The yield-change vector (decimal, one entry per tenor)
        corresponding to a +1-standard-deviation move in principal
        component `component` (1-indexed, 1..n_components) --
        component_std times that component's own unit-norm loading
        vector. This IS the real-units shock Phase 4C multiplies a
        KRD/DV01 vector against to price a factor's exposure; nothing
        about a "1-std move" is recomputed there.
        """
        if not (1 <= component <= self.n_components):
            raise ValueError(
                f"component must be between 1 and {self.n_components}, got {component}"
            )
        loading_row = self.loadings.loc[component]
        std = self.component_std[component - 1]
        return (loading_row * std).rename(f"PC{component}_1std_yield_shock")


def compute_curve_pca(
    history: pd.DataFrame,
    n_components: int = DEFAULT_N_COMPONENTS,
) -> CurvePCAResult:
    """Fit PCA on daily first differences of a JGB curve history.

    Parameters
    ----------
    history : a curve history as returned by
        data.jgb_curve_history_loader.load_jgb_curve_history -- date-
        indexed, decimal yields, no missing values. Differenced
        internally (see module docstring for why levels aren't used
        directly); the caller supplies levels, not pre-differenced data.
    n_components : how many top (by explained variance) components to
        keep in the returned result. Must be between 1 and the number of
        tenors in `history`.

    Returns
    -------
    CurvePCAResult

    Raises
    ------
    ValueError : `history` has missing values (run it through
        load_jgb_curve_history first -- this function does not repair
        ragged data itself), fewer than 2 dates (no differences to
        compute), n_components out of range, or too few resulting
        observations relative to the number of tenors for a meaningful
        covariance estimate (n_observations must exceed n_tenors).
    """
    if history.isna().any().any():
        raise ValueError(
            "history contains missing values -- run it through "
            "load_jgb_curve_history's ragged-tenor policy first"
        )
    if len(history) < 2:
        raise ValueError(
            f"need at least 2 dates to compute a single daily change, got {len(history)}"
        )

    tenors = history.columns.to_numpy(dtype=float)
    n_tenors = len(tenors)
    if not (1 <= n_components <= n_tenors):
        raise ValueError(
            f"n_components must be between 1 and {n_tenors} (the number of tenors "
            f"in history), got {n_components}"
        )

    changes = history.sort_index().diff().dropna(how="any")
    X = changes.to_numpy()
    n_obs = X.shape[0]
    if n_obs <= n_tenors:
        raise ValueError(
            f"only {n_obs} daily-change observation(s) for {n_tenors} tenors -- PCA "
            "needs meaningfully more observations than tenors for a stable covariance "
            "estimate; widen the lookback window"
        )

    Xc = X - X.mean(axis=0)

    # SVD rather than eigendecomposing the covariance matrix (X^T @ X)
    # directly: numerically more stable, since forming X^T @ X squares the
    # matrix's condition number before decomposing it. Same eigenvectors/
    # eigenvalues either way.
    U, S, Vt = np.linalg.svd(Xc, full_matrices=False)

    # Deterministic sign convention -- see module docstring.
    for i in range(Vt.shape[0]):
        flip_idx = np.argmax(np.abs(Vt[i]))
        if Vt[i, flip_idx] < 0:
            Vt[i] *= -1
            U[:, i] *= -1

    eigenvalues_all = (S**2) / (n_obs - 1)  # sample variance (ddof=1), matches np.cov's default
    ratio_all = eigenvalues_all / eigenvalues_all.sum()

    loadings = pd.DataFrame(
        Vt[:n_components],
        index=pd.RangeIndex(1, n_components + 1, name="component"),
        columns=pd.Index(tenors, name="maturity_years"),
    )

    return CurvePCAResult(
        lookback_years=history.attrs.get("lookback_years"),
        window_start=str(history.index.min().date()),
        window_end=str(history.index.max().date()),
        tenors=tenors,
        n_observations=n_obs,
        n_components=n_components,
        explained_variance=eigenvalues_all[:n_components],
        explained_variance_ratio=ratio_all[:n_components],
        explained_variance_ratio_all=ratio_all,
        loadings=loadings,
    )


if __name__ == "__main__":
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS)
    result = compute_curve_pca(history)

    print()
    print(
        f"PCA window: {result.window_start} to {result.window_end} "
        f"({result.n_observations} daily changes, {len(result.tenors)} tenors: "
        f"{[float(t) for t in result.tenors]})"
    )

    print()
    print("Explained variance:")
    for i in range(result.n_components):
        print(
            f"  PC{i + 1}: {result.explained_variance_ratio[i]:6.2%} of total variance   "
            f"(1 std = {result.component_std[i] * 10000:5.2f} bp)   cumulative: "
            f"{result.cumulative_explained_variance_ratio[i]:6.2%}"
        )
    print(
        f"  (top {result.n_components} vs. the conventional expectation that level + "
        "slope + curvature capture the large majority of curve variance)"
    )

    print()
    print("Tenor loadings (unit-norm eigenvectors, one row per component):")
    print(result.loadings.to_string(float_format=lambda v: f"{v:6.3f}"))

    print()
    print("=" * 72)
    print("SANITY CHECK -- Litterman-Scheinkman conventional shapes")
    print("(conventional for a sovereign curve, not guaranteed -- reporting what")
    print(" was actually found, not forcing these labels)")
    print("=" * 72)

    pc1 = result.loadings.loc[1]
    pc1_consistent_sign = bool((pc1 > 0).all() or (pc1 < 0).all())
    print(f"PC1 ('level'?): consistent sign across all {len(pc1)} tenors -- {pc1_consistent_sign}")

    if result.n_components >= 2:
        pc2 = result.loadings.loc[2]
        short_end, long_end = pc2.iloc[0], pc2.iloc[-1]
        pc2_opposite_ends = bool(np.sign(short_end) != np.sign(long_end))
        print(
            f"PC2 ('slope'?): short end ({pc2.index[0]:.0f}Y = {short_end:+.3f}) vs. "
            f"long end ({pc2.index[-1]:.0f}Y = {long_end:+.3f}) opposite sign -- "
            f"{pc2_opposite_ends}"
        )

    if result.n_components >= 3:
        pc3 = result.loadings.loc[3]
        mid_idx = len(pc3) // 2
        belly = pc3.iloc[mid_idx]
        wings_same_sign = bool(np.sign(pc3.iloc[0]) == np.sign(pc3.iloc[-1]))
        belly_opposes_wings = bool(np.sign(belly) != np.sign(pc3.iloc[0]))
        print(
            f"PC3 ('curvature'?): wings ({pc3.index[0]:.0f}Y = {pc3.iloc[0]:+.3f}, "
            f"{pc3.index[-1]:.0f}Y = {pc3.iloc[-1]:+.3f}) same sign -- {wings_same_sign}; "
            f"belly ({pc3.index[mid_idx]:.0f}Y = {belly:+.3f}) opposes them -- "
            f"{belly_opposes_wings}"
        )
