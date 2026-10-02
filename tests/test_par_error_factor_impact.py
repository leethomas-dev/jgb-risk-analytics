"""
Tests for models/par_error_factor_impact.py (Special Phase B item 4).
All on committed data (prefer_live=False); no JSDA file needed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from data.jgb_curve_loader import load_jgb_curve
from models.bootstrap import bootstrap_zero_curve, implied_ytm, price_via_zero_curve
from models.par_error_factor_impact import (
    MOF_GRID_COUPONS,
    compare_factor_results,
    coupon_corrected_curve,
    coupon_corrected_history,
)


def _curve(yields):
    return pd.DataFrame({"maturity_years": [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0], "yield": yields})


def test_flat_curve_needs_no_correction():
    # A flat curve has no coupon effect, whatever the coupon.
    curve = _curve([0.02] * 7)
    corrected = coupon_corrected_curve(curve)
    assert np.allclose(corrected["yield"], curve["yield"], atol=1e-9)


def test_only_older_issue_grid_years_move_and_they_move_down():
    curve = _curve([0.01, 0.015, 0.025, 0.032, 0.036, 0.040, 0.041])
    shift = (curve["yield"] - coupon_corrected_curve(curve)["yield"]).to_numpy() * 10000.0
    moved = dict(zip(curve["maturity_years"], shift))
    for tenor in (5.0, 10.0, 20.0, 30.0):  # newest-issue grid years: treated as par
        assert moved[tenor] == 0.0
    assert moved[15.0] > 1.0 and moved[25.0] > 1.0  # low-coupon issues on a rising curve


def test_corrected_curve_reprices_the_grid_bonds_at_mofs_yields():
    # Checked with models.bootstrap's own yield solver, not this module's.
    curve = load_jgb_curve(prefer_live=False, verbose=False)
    zero_curve = bootstrap_zero_curve(coupon_corrected_curve(curve))
    observed = curve.set_index("maturity_years")["yield"]
    for tenor, coupon in MOF_GRID_COUPONS.items():
        price = price_via_zero_curve(100.0, coupon, tenor, zero_curve)
        assert abs(implied_ytm(100.0, coupon, tenor, price) - observed[tenor]) * 10000.0 < 0.01


def test_history_correction_matches_the_single_day_one():
    history = load_jgb_curve_history(lookback_years=0.1, prefer_live=False, verbose=False)
    corrected = coupon_corrected_history(history)
    last = pd.DataFrame({"maturity_years": history.columns.astype(float), "yield": history.iloc[-1].to_numpy()})
    assert np.allclose(corrected.iloc[-1].to_numpy(), coupon_corrected_curve(last)["yield"].to_numpy())


def test_factor_results_on_the_default_window():
    # Pinned in docs/special_phase_b_documentation.md §4 (attribution over
    # the last 20 days here, to keep the test quick; the doc uses all).
    history = load_jgb_curve_history(lookback_years=2.0, prefer_live=False, verbose=False)
    impact = compare_factor_results(
        load_portfolio(), history, load_jgb_curve(prefer_live=False, verbose=False), attribution_days=20
    )
    assert (impact.loadings["cosine"] > 0.99).all()
    change = impact.exposure["both_corrected"] / impact.exposure["original"] - 1.0
    assert 0.07 < change.loc[2] < 0.09  # PC2 +7.9%: the largest move, mostly via the PCA
    pca_only = impact.exposure["pca_corrected"] / impact.exposure["original"] - 1.0
    curve_only = impact.exposure["curve_corrected"] / impact.exposure["original"] - 1.0
    assert abs(pca_only.loc[2]) > 5 * abs(curve_only.loc[2])
    assert not any(impact.material[k] for k in ("loading_shape", "variance_share", "exposure"))
    assert impact.attribution["date"].nunique() == 20
