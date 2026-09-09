"""
Tests for models/curve_fitting.py.

Covers: the output contract for both models; goodness of fit against the
real snapshot curve, within a stated RMSE tolerance (not exact
reproduction -- a 3-4 parameter model isn't expected to hit an 80-point
curve exactly); the flat-curve analytic case (should fit exactly, for
either model, at any tau); the "long-run level" / "short rate" parameter
interpretation, checked as a limiting-value identity rather than merely
asserted; convergence being genuinely CHECKED (both a real converging
case and a real non-converging case, not a flag that's always True by
construction); Svensson fitting at least as well as Nelson-Siegel on real
data; residuals_bp / residuals_at_original_tenors; and input validation.

All curve loading uses prefer_live=False for determinism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.jgb_curve_loader import _MOF_TENOR_COLUMNS, load_jgb_curve
from models.bootstrap import bootstrap_zero_curve
from models.curve_fitting import (
    TAU_MAX,
    TAU_MIN,
    NelsonSiegelFitResult,
    SvenssonFitResult,
    fit_nelson_siegel,
    fit_svensson,
    residuals_at_original_tenors,
    residuals_bp,
)


def _snapshot_zero_curve() -> pd.DataFrame:
    curve = load_jgb_curve(prefer_live=False, verbose=False)
    return bootstrap_zero_curve(curve)


def _flat_zero_curve(rate: float = 0.02, n_points: int = 10) -> pd.DataFrame:
    tenors = np.linspace(0.5, 40.0, n_points)
    return pd.DataFrame({"maturity_years": tenors, "zero_rate": np.full(n_points, rate)})


def _pathological_linear_zero_curve() -> pd.DataFrame:
    """A perfectly LINEAR-in-maturity curve -- the tenor grid MOF's live/
    cache source actually uses (_MOF_TENOR_COLUMNS: 1Y-40Y, no sub-year
    points), but with synthetic, exactly affine yields. A hump-shaped
    basis function (Nelson-Siegel/Svensson) can only approximate an
    unbounded linear trend by pushing tau toward infinity -- this is a
    genuine, deliberately difficult case used to prove the convergence
    check actually fires (test_convergence_flag_actually_triggers_on_a_
    hard_case), not a claim about real MOF curve shapes."""
    tenors = sorted(_MOF_TENOR_COLUMNS.values())
    yields = [0.005 + 0.00075 * t for t in tenors]
    curve = pd.DataFrame({"maturity_years": tenors, "yield": yields})
    return bootstrap_zero_curve(curve)


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------


def test_fit_nelson_siegel_returns_the_result_type():
    result = fit_nelson_siegel(_snapshot_zero_curve())
    assert isinstance(result, NelsonSiegelFitResult)


def test_fit_svensson_returns_the_result_type():
    result = fit_svensson(_snapshot_zero_curve())
    assert isinstance(result, SvenssonFitResult)


def test_fitted_arrays_match_the_input_curve_length():
    zero_curve = _snapshot_zero_curve()
    ns = fit_nelson_siegel(zero_curve)
    sv = fit_svensson(zero_curve)
    assert len(ns.tenors) == len(ns.target_rates) == len(ns.fitted_rates) == len(zero_curve)
    assert len(sv.tenors) == len(sv.target_rates) == len(sv.fitted_rates) == len(zero_curve)


def test_all_parameters_are_finite():
    zero_curve = _snapshot_zero_curve()
    ns = fit_nelson_siegel(zero_curve)
    sv = fit_svensson(zero_curve)
    assert np.isfinite([ns.beta0, ns.beta1, ns.beta2, ns.tau]).all()
    assert np.isfinite([sv.beta0, sv.beta1, sv.beta2, sv.beta3, sv.tau1, sv.tau2]).all()


def test_tau_parameters_stay_within_the_documented_bounds():
    zero_curve = _snapshot_zero_curve()
    ns = fit_nelson_siegel(zero_curve)
    sv = fit_svensson(zero_curve)
    assert TAU_MIN <= ns.tau <= TAU_MAX
    assert TAU_MIN <= sv.tau1 <= TAU_MAX
    assert TAU_MIN <= sv.tau2 <= TAU_MAX
    assert sv.tau2 > sv.tau1  # the ordering this module's grid search enforces


# --------------------------------------------------------------------------
# Goodness of fit on real data
# --------------------------------------------------------------------------


def test_nelson_siegel_rmse_is_within_a_stated_tolerance_on_the_snapshot_curve():
    # Real measured value (docs/phase_4_5b_documentation.md) is ~5.95bp;
    # a generous tolerance catches a real regression without pinning to
    # the exact float.
    result = fit_nelson_siegel(_snapshot_zero_curve())
    assert result.rmse_bp < 10.0


def test_svensson_rmse_is_within_a_stated_tolerance_on_the_snapshot_curve():
    # Real measured value is ~3.20bp.
    result = fit_svensson(_snapshot_zero_curve())
    assert result.rmse_bp < 6.0


def test_svensson_fits_at_least_as_well_as_nelson_siegel():
    # Svensson nests Nelson-Siegel (beta3=0 recovers it exactly), so its
    # grid-search optimum should never be meaningfully worse -- checked
    # directly against real data, not assumed from the model algebra alone.
    zero_curve = _snapshot_zero_curve()
    ns = fit_nelson_siegel(zero_curve)
    sv = fit_svensson(zero_curve)
    assert sv.rmse_bp < ns.rmse_bp


# --------------------------------------------------------------------------
# Analytic case: a flat curve fits exactly, for either model, at any tau
# --------------------------------------------------------------------------


def test_nelson_siegel_fits_a_flat_curve_exactly():
    result = fit_nelson_siegel(_flat_zero_curve(0.02))
    assert result.rmse_bp == pytest.approx(0.0, abs=1e-6)
    assert result.beta0 == pytest.approx(0.02, abs=1e-8)
    assert result.beta1 == pytest.approx(0.0, abs=1e-6)
    assert result.beta2 == pytest.approx(0.0, abs=1e-6)


def test_svensson_fits_a_flat_curve_exactly():
    result = fit_svensson(_flat_zero_curve(0.02))
    assert result.rmse_bp == pytest.approx(0.0, abs=1e-6)
    assert result.beta0 == pytest.approx(0.02, abs=1e-8)


# --------------------------------------------------------------------------
# Parameter interpretation: beta0 (long-run level) / beta0+beta1 (short
# rate) as LIMITING VALUES of rate_at, not just labels on the dataclass
# --------------------------------------------------------------------------


def test_beta0_is_the_long_run_level_limit():
    result = fit_nelson_siegel(_snapshot_zero_curve())
    # f1(m, tau) ~ tau/m for large m -- not exactly zero at any finite m,
    # so a very large (not infinite) maturity plus a correspondingly loose
    # tolerance is what "the limit" means in floating point.
    very_long_rate = result.rate_at(1e10)
    assert very_long_rate == pytest.approx(result.beta0, abs=1e-6)


def test_beta0_plus_beta1_is_the_short_rate_limit():
    result = fit_nelson_siegel(_snapshot_zero_curve())
    very_short_rate = result.rate_at(1e-6)  # m/tau -> 0
    assert very_short_rate == pytest.approx(result.beta0 + result.beta1, abs=1e-4)


def test_svensson_beta0_is_the_long_run_level_limit():
    result = fit_svensson(_snapshot_zero_curve())
    very_long_rate = result.rate_at(1e10)
    assert very_long_rate == pytest.approx(result.beta0, abs=1e-6)


def test_rate_at_reproduces_fitted_rates_on_the_original_grid():
    zero_curve = _snapshot_zero_curve()
    ns = fit_nelson_siegel(zero_curve)
    recomputed = ns.rate_at(ns.tenors)
    np.testing.assert_allclose(recomputed, ns.fitted_rates, atol=1e-10)


# --------------------------------------------------------------------------
# Convergence: checked and reported, not assumed
# --------------------------------------------------------------------------


def test_converges_on_the_real_snapshot_curve():
    zero_curve = _snapshot_zero_curve()
    ns = fit_nelson_siegel(zero_curve)
    sv = fit_svensson(zero_curve)
    assert ns.converged is True
    assert sv.converged is True


def test_convergence_flag_actually_triggers_on_a_hard_case():
    # A flag that is always True by construction proves nothing -- this
    # exercises the actual failure path against a deliberately difficult
    # (perfectly linear-in-maturity) curve, per module docstring
    # "CONVERGENCE".
    hard_curve = _pathological_linear_zero_curve()
    ns = fit_nelson_siegel(hard_curve)
    assert ns.converged is False
    assert ns.tau == pytest.approx(TAU_MAX)


# --------------------------------------------------------------------------
# Grid independence -- fits cleanly against both real grid shapes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build_curve",
    [
        lambda: bootstrap_zero_curve(load_jgb_curve(prefer_live=False)),
        _pathological_linear_zero_curve,
    ],
)
def test_fits_cleanly_without_error_on_both_grid_shapes(build_curve):
    # Convergence itself is data-shape-dependent (the test above already
    # covers a genuine non-convergent case) -- what must hold universally
    # is that fitting completes and returns finite, well-formed results.
    zero_curve = build_curve()
    ns = fit_nelson_siegel(zero_curve)
    sv = fit_svensson(zero_curve)
    assert np.isfinite(ns.fitted_rates).all()
    assert np.isfinite(sv.fitted_rates).all()
    assert isinstance(ns.converged, bool)
    assert isinstance(sv.converged, bool)


# --------------------------------------------------------------------------
# residuals_bp / residuals_at_original_tenors
# --------------------------------------------------------------------------


def test_residuals_bp_matches_a_direct_computation():
    result = fit_nelson_siegel(_snapshot_zero_curve())
    resid = residuals_bp(result)
    expected = (result.target_rates - result.fitted_rates) * 10000.0
    np.testing.assert_allclose(resid.to_numpy(), expected)
    np.testing.assert_allclose(resid.index.to_numpy(), result.tenors)


def test_residuals_at_original_tenors_is_a_subset_restricted_to_mof_tenors():
    curve = load_jgb_curve(prefer_live=False)
    zero_curve = bootstrap_zero_curve(curve)
    result = fit_nelson_siegel(zero_curve)

    all_resid = residuals_bp(result)
    original_resid = residuals_at_original_tenors(result, curve)

    assert len(original_resid) == len(curve)  # every MOF tenor -- all of them are grid points
    assert len(original_resid) < len(all_resid)  # a genuine subset (most grid points are interpolated)
    for maturity in curve["maturity_years"]:
        assert original_resid[maturity] == pytest.approx(all_resid[maturity])


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def test_missing_zero_rate_column_rejected():
    par_shaped = pd.DataFrame({"maturity_years": [1, 2, 5, 10], "yield": [0.01, 0.02, 0.03, 0.04]})
    with pytest.raises(ValueError, match="zero_rate"):
        fit_nelson_siegel(par_shaped)
    with pytest.raises(ValueError, match="zero_rate"):
        fit_svensson(par_shaped)


def test_too_few_points_rejected_for_nelson_siegel():
    tiny_curve = pd.DataFrame({"maturity_years": [1, 2, 5], "zero_rate": [0.01, 0.02, 0.03]})
    with pytest.raises(ValueError, match="at least 4"):
        fit_nelson_siegel(tiny_curve)


def test_too_few_points_rejected_for_svensson():
    small_curve = pd.DataFrame(
        {"maturity_years": [1, 2, 5, 10, 20], "zero_rate": [0.01, 0.02, 0.03, 0.04, 0.05]}
    )
    with pytest.raises(ValueError, match="at least 6"):
        fit_svensson(small_curve)
