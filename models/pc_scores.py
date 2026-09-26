"""
pc_scores.py

Phase 4D, Section 1: projects each day's yield-CHANGE vector onto the PCA
factors Phase 4B already fit, producing a date-indexed time series of how
big a move happened along each recurring pattern (level/slope/curvature)
on any given day.

    score_i(t) = eigenvector_i . daily_change_vector(t)

UNITS -- read this before anything else. `history` (from
data.jgb_curve_history_loader.load_jgb_curve_history) is DECIMAL yields
(0.015 == 1.5%), and the eigenvectors this module dots against are
UNIT-NORM (models.pca's own convention). A dot product of a unit vector
against a decimal-yield-CHANGE vector is itself in DECIMAL YIELD-CHANGE
units -- NOT a standard-deviation count, NOT basis points. `scores` is
labeled this way everywhere in this module and its docs. A STANDARDIZED
version (score / sqrt(eigenvalue), dimensionless -- "how many standard
deviations away from typical") is also provided, as `standardized_scores`
-- a SEPARATE field with its own distinct column names ("PC1_std", not
"PC1"), so the two are never confusable in output.

RAW, NOT MEAN-CENTERED -- and why that's safe. models.pca.compute_curve_pca
mean-centers the change matrix internally before its SVD (its own
docstring explains why: level/slope/curvature are patterns in DEVIATIONS
from the average daily change, not the average move itself). This module
deliberately does NOT re-center: score_i(t) is the projection of the RAW
daily change onto eigenvector_i, exactly as this phase's brief states the
formula. This does not corrupt the verification the brief asks for --
adding back the (constant, day-independent) mean shifts every score in a
component's series by the SAME constant, which changes a distribution's
CENTER but never its STANDARD DEVIATION, so `scores.std()` still equals
`pca_result.component_std` to numerical precision (checked directly in
__main__ and in this module's own tests, not merely assumed). Using the
raw projection is also what makes the completeness check below possible
at all: it lets scores sum back to the exact ORIGINAL (uncentered) change
vector, not a centered approximation of it.

COMPLETENESS / RECONSTRUCTION. models.pca's `full_loadings` (ALL n_tenors
components, not just the top few Phase 4B keeps) is a complete ORTHONORMAL
basis for the tenor space: for any change vector x(t), summing
(eigenvector_i . x(t)) * eigenvector_i across every one of the n_tenors
components reconstructs x(t) exactly. This module exposes `full_scores`
(every component's score, not just the top few) specifically so that
check is possible from this module's own public output -- see
test_pc_scores.py's completeness test. `scores` (the headline output)
stays limited to the top n_components `pca_result` kept, matching the
phase brief's "PC1/PC2/PC3" request; `full_scores` is there for anyone
who wants the whole basis.

SIGN CONVENTION -- reused from Phase 4B, not reinvented. models.pca
already fixes each component's sign deterministically (each component's
LARGEST-MAGNITUDE tenor loading is positive -- models/pca.py's own module
docstring). Because this module's scores are computed against that SAME
already-signed eigenbasis, "a positive PC1 day" means the same real curve
movement every time this is run against the same PCA fit. Phase 4D's
brief asks for a convention to be fixed and documented -- the one already
in force satisfies that without this module picking a second, competing
rule of its own.

TENOR ALIGNMENT. `history`'s own tenor grid must exactly match
`pca_result.tenors`. Unlike a yield LEVEL (which curve_yield_at can safely
interpolate between quoted tenors, the technique Phase 4C uses for its own
grid mismatch), a PCA loading is a set of per-tenor coefficients tied to
specific tenor IDENTITIES -- there is no valid way to interpolate a CHANGE
vector onto a different grid and dot it against loadings fit on another
one. Different lookback windows can retain different tenor sets (Phase
4A's ragged-tenor policy) -- checked explicitly here, raising a clear
error on a mismatch rather than silently misaligning columns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from data.jgb_curve_history_loader import load_jgb_curve_history
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca


@dataclass(frozen=True)
class PCScoreResult:
    """One PCA fit's daily score series -- frozen, a snapshot of one
    (history, pca_result) pair.

    pca_lookback_years / window_start / window_end : carried over from
        `pca_result`, for a reader who only wants this result.
    n_components : how many components `scores` / `standardized_scores`
        carry -- matches pca_result.n_components.
    tenors : the tenor grid this was computed on (== pca_result.tenors).
    scores : date-indexed DataFrame, columns "PC1".."PCn" -- DECIMAL
        yield-change units (see module docstring). score_i(t) = that
        component's eigenvector dotted with the RAW day-over-day change
        vector, NOT a standard-deviation count.
    standardized_scores : same date index, columns "PC1_std".."PCn_std" --
        dimensionless (score / component_std). A SEPARATE object from
        `scores`, with distinctly-named columns, so the two are never
        mixed up.
    full_scores : date-indexed DataFrame, ALL n_tenors components (not
        just the top n_components kept) -- decimal units, same convention
        as `scores`. Exists so the completeness/reconstruction property
        (module docstring) is checkable from this module's own public
        output.
    component_std : decimal-yield units, sqrt(eigenvalue) per KEPT
        component -- carried from pca_result.component_std, the
        denominator standardized_scores divides by.
    """

    pca_lookback_years: float | None
    window_start: str
    window_end: str
    n_components: int
    tenors: np.ndarray
    scores: pd.DataFrame
    standardized_scores: pd.DataFrame
    full_scores: pd.DataFrame
    component_std: np.ndarray

    def percentile(self, date, component: int) -> float:
        """Percentile (0-100) of `date`'s RAW score within this
        component's own historical distribution across the full `scores`
        sample -- e.g. 90.0 means that day's move on this factor was
        bigger than 90% of the sample's daily moves. Signed, not absolute:
        a very negative day sits at a LOW percentile, the ordinary meaning
        of the word, not "90th percentile of magnitude"."""
        if not (1 <= component <= self.n_components):
            raise ValueError(
                f"component must be between 1 and {self.n_components}, got {component}"
            )
        col = f"PC{component}"
        ts = pd.Timestamp(date)
        if ts not in self.scores.index:
            raise ValueError(f"{date} is not one of this score series' dates")
        value = self.scores.loc[ts, col]
        return float((self.scores[col] <= value).mean() * 100.0)


def compute_pc_scores(history: pd.DataFrame, pca_result: CurvePCAResult) -> PCScoreResult:
    """Project every daily change in `history` onto `pca_result`'s own
    eigenbasis.

    Parameters
    ----------
    history : a curve history as returned by
        data.jgb_curve_history_loader.load_jgb_curve_history -- date-
        indexed, decimal yield LEVELS, no missing values. Differenced
        internally (same reasoning as models.pca.compute_curve_pca: the
        caller supplies levels, not pre-differenced data). Its tenor grid
        must exactly match `pca_result.tenors` (see module docstring "tenor
        alignment") -- typically the SAME history object the PCA fit was
        run on, though a longer window with an identical retained tenor
        set works too (nothing here assumes the two windows are the same
        length, only that their tenor grids match).
    pca_result : a models.pca.compute_curve_pca(...) result, fit on a
        history whose tenor grid matches `history`'s own.

    Returns
    -------
    PCScoreResult

    Raises
    ------
    ValueError : `history`'s tenor grid does not match `pca_result.tenors`,
        or `pca_result.full_loadings` is None (a CurvePCAResult built
        directly rather than through compute_curve_pca, which always
        populates it -- see CurvePCAResult's own docstring).
    """
    if pca_result.full_loadings is None:
        raise ValueError(
            "pca_result.full_loadings is None -- compute_pc_scores needs the "
            "FULL eigenbasis (every component, not just the top few kept), "
            "which models.pca.compute_curve_pca always populates. Pass a "
            "result from compute_curve_pca, not one constructed directly "
            "without full_loadings."
        )
    history_tenors = history.columns.to_numpy(dtype=float)
    if not np.array_equal(np.sort(history_tenors), np.sort(pca_result.tenors)):
        raise ValueError(
            "history's tenor grid does not match pca_result's own tenor grid -- "
            f"history has {sorted(history_tenors)}, pca_result has "
            f"{sorted(pca_result.tenors.tolist())}. A PCA loading is a set of "
            "per-tenor coefficients tied to specific tenor identities, not a "
            "smooth function of maturity -- pass the history the PCA fit was "
            "actually run on (or a window with an identical retained tenor set)."
        )

    changes = history.sort_index().diff().dropna(how="any")
    changes = changes[pca_result.full_loadings.columns]  # match full_loadings' own tenor order

    full_scores_arr = changes.to_numpy() @ pca_result.full_loadings.to_numpy().T
    n_tenors = full_scores_arr.shape[1]
    full_scores = pd.DataFrame(
        full_scores_arr,
        index=changes.index,
        columns=[f"PC{i}" for i in range(1, n_tenors + 1)],
    )
    full_scores.index.name = "date"

    n_components = pca_result.n_components
    scores = full_scores.iloc[:, :n_components].copy()
    component_std = pca_result.component_std
    standardized_scores = pd.DataFrame(
        {
            f"PC{i}_std": scores[f"PC{i}"] / component_std[i - 1]
            for i in range(1, n_components + 1)
        },
        index=scores.index,
    )

    return PCScoreResult(
        pca_lookback_years=pca_result.lookback_years,
        window_start=pca_result.window_start,
        window_end=pca_result.window_end,
        n_components=n_components,
        tenors=pca_result.tenors,
        scores=scores,
        standardized_scores=standardized_scores,
        full_scores=full_scores,
        component_std=component_std,
    )


if __name__ == "__main__":
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False)
    pca_result = compute_curve_pca(history)
    result = compute_pc_scores(history, pca_result)

    print()
    print(
        f"PC scores: {len(result.scores)} daily observations, "
        f"{result.window_start} to {result.window_end}"
    )
    print(
        "UNITS: `scores` are DECIMAL yield-change units (0.0001 == 1bp). "
        "`standardized_scores` are dimensionless (score / sqrt(eigenvalue)). "
        "Never confuse the two."
    )

    print()
    print("Verification -- score std should equal sqrt(eigenvalue) (component_std):")
    for i in range(1, result.n_components + 1):
        observed_std = result.scores[f"PC{i}"].std(ddof=1)
        expected_std = result.component_std[i - 1]
        gap_bp = (observed_std - expected_std) * 10000
        print(
            f"  PC{i}: observed std = {observed_std * 10000:8.4f}bp   "
            f"sqrt(eigenvalue) = {expected_std * 10000:8.4f}bp   gap = {gap_bp:+.6f}bp"
        )

    print()
    print("Completeness check -- summing ALL components' score*loading reconstructs the original change vector:")
    reconstructed = result.full_scores.to_numpy() @ pca_result.full_loadings.to_numpy()
    original = (
        history.sort_index().diff().dropna(how="any")[pca_result.full_loadings.columns].to_numpy()
    )
    max_abs_gap = float(np.max(np.abs(reconstructed - original)))
    print(f"  max |reconstructed - original| across the full sample: {max_abs_gap:.2e}")

    print()
    latest_date = result.scores.index[-1]
    print(f"Most recent day's scores/percentiles ({latest_date.date()}):")
    for i in range(1, result.n_components + 1):
        s = result.scores.loc[latest_date, f"PC{i}"]
        pct = result.percentile(latest_date, i)
        print(f"  PC{i}: {s * 10000:+7.2f}bp   ({pct:5.1f}th percentile of the sample)")
