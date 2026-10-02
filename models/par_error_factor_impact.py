"""
par_error_factor_impact.py

Special Phase B: does the par error (models/par_error_check.py) change the
FACTOR results -- PCA, factor exposure, factor P&L -- that Phase 5 will
consume?

WHY A FIXED BIAS WOULDN'T TEST ANYTHING. PCA runs on daily CHANGES, and a
bias that's the same every day cancels out of a change exactly. The real
par error isn't fixed: it's the coupon effect of the bonds MOF uses at
each grid year, and that depends on each day's curve.

THE TEST. Build a coupon-corrected copy of the curve history and re-run
everything on it:

1. At grid years MOF builds from older issues, its yield is the yield of
   a real bond with coupon MOF_GRID_COUPONS[tenor] (measured from JSDA,
   2026-08-31 -- see docs/special_phase_b_documentation.md §4). Grid
   years built from new issues are treated as par.
2. For each day, find the par curve under which THAT bond yields MOF's
   number (repeat until every bond is within 0.01bp: price the bond on
   the bootstrapped zero curve, see how far its yield misses, shift the
   par yield by the miss). Near-par grid points barely move; 15Y and 25Y move most.
3. Re-run PCA, factor exposure and factor P&L on the corrected history
   and compare with the originals.

This is a TEST INPUT, not a replacement curve: the coupons are held fixed
across the window (MOF's actual issues roll over time), and the jumps when
MOF switches issues aren't modelled. It measures only the smooth,
curve-dependent part of the error.

TWO CHANNELS. PCA loadings come from daily changes (where the error
partly cancels). Factor exposure and P&L also reprice the portfolio on
the curve's LEVEL (where it does not). compare_factor_results() reports
both, and splits exposure into "PCA changed" and "curve level changed".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from models.bootstrap import bootstrap_zero_curve, discount_factor_at
from models.factor_exposure import compute_portfolio_factor_exposure
from models.factor_pnl_attribution import compute_factor_pnl_attribution
from models.pc_scores import compute_pc_scores
from models.pca import compute_curve_pca

# Coupons of the OLDER issues MOF's selection rule picks at each grid year
# (the issues maturing nearest it), from JSDA's 2026-08-31 quotes; coupons
# are public issue terms. Grid years built from each class's NEWEST issue
# (2, 5, 10, 20, 30, 40Y) are left out: a new issue's coupon is set near
# the yield of the day, so it stays close to par as yields move. Holding
# today's 3.7-4.0% coupons fixed back to 2024, when yields were far lower,
# would invent a premium-bond correction that never existed.
MOF_GRID_COUPONS = {
    1.0: 0.0090, 3.0: 0.0055, 4.0: 0.0125, 6.0: 0.0020, 7.0: 0.0060,
    8.0: 0.0100, 9.0: 0.0160, 15.0: 0.0045, 25.0: 0.0070,
}

# Stop once every corrected grid bond yields within 0.01bp of MOF's number.
CONVERGENCE_TOL = 1e-6
MAX_STEPS = 100

# Decided before running (docs/special_phase_b_documentation.md §4): the
# error is "material" for factor results if any of these is exceeded.
MATERIAL_LOADING_COSINE = 0.99          # loading shape: cosine similarity below this
MATERIAL_VARIANCE_SHARE_PP = 2.0        # explained-variance share moves by more (pp)
MATERIAL_EXPOSURE_CHANGE = 0.10         # a factor's +1 std P&L moves by more (relative)
MATERIAL_ATTRIBUTION_SHARE = 0.10       # median daily attribution change vs median |daily P&L|


def _grid_bond(coupon: float, maturity: float) -> tuple[np.ndarray, np.ndarray]:
    # Same schedule as models.bootstrap.price_via_zero_curve: semiannual,
    # built back from maturity.
    n = max(1, round(maturity * 2))
    times = maturity - (n - np.arange(1, n + 1)) / 2.0
    flows = np.full(n, coupon * 100.0 / 2.0)
    flows[-1] += 100.0
    return times, flows


def _bond_yield(times: np.ndarray, flows: np.ndarray, price: float, guess: float) -> float:
    """Semiannual yield for a price, by Newton's method -- the same answer
    as models.bootstrap.implied_ytm, fast enough to run thousands of times."""
    y = guess
    for _ in range(50):
        discount = (1.0 + y / 2.0) ** (-2.0 * times)
        value = np.sum(flows * discount) - price
        slope = -np.sum(flows * times * discount / (1.0 + y / 2.0))
        step = value / slope
        y -= step
        if abs(step) < 1e-12:
            break
    return y


def coupon_corrected_curve(par_curve: pd.DataFrame, coupons: dict[float, float] = MOF_GRID_COUPONS) -> pd.DataFrame:
    """One day's curve, with each grid yield replaced by the par yield
    under which a bond with that grid's coupon yields the original
    number. Tenors without a coupon are left alone. Raises if the
    correction doesn't settle within MAX_STEPS."""
    observed = par_curve.set_index("maturity_years")["yield"].astype(float)
    corrected = observed.copy()
    bonds = {t: _grid_bond(coupons[t], t) for t in observed.index if t in coupons}
    for _ in range(MAX_STEPS):
        zero_curve = bootstrap_zero_curve(corrected.rename("yield").reset_index())
        worst = 0.0
        for tenor, (times, flows) in bonds.items():
            price = float(np.sum(flows * discount_factor_at(zero_curve, times)))
            miss = _bond_yield(times, flows, price, observed[tenor]) - observed[tenor]
            corrected[tenor] -= miss
            worst = max(worst, abs(miss))
        if worst < CONVERGENCE_TOL:
            return corrected.rename("yield").reset_index()
    raise RuntimeError(f"coupon correction did not settle in {MAX_STEPS} steps")


def coupon_corrected_history(history: pd.DataFrame, coupons: dict[float, float] = MOF_GRID_COUPONS) -> pd.DataFrame:
    """coupon_corrected_curve applied to every date in a curve history
    (dates x tenors, decimal yields)."""
    rows = []
    for _, row in history.iterrows():
        curve = pd.DataFrame({"maturity_years": history.columns.astype(float), "yield": row.to_numpy(dtype=float)})
        rows.append(coupon_corrected_curve(curve, coupons)["yield"].to_numpy())
    return pd.DataFrame(rows, index=history.index, columns=history.columns)


@dataclass(frozen=True)
class FactorImpact:
    """Original vs. coupon-corrected factor results (see module docstring).

    correction_bp : average correction applied per tenor over the window
        (original minus corrected yield, bp).
    loadings : per component -- cosine similarity of the two loading
        vectors (sign-aligned), and both explained-variance shares.
    exposure : per component -- +1 std P&L (currency per 100 face) as it
        stands, with only the PCA corrected, with only today's curve
        corrected, and with both.
    attribution : per day x component -- attributed P&L, original and
        corrected, plus each day's actual P&L.
    material : which of the four pre-set thresholds were exceeded.
    """

    correction_bp: pd.Series
    loadings: pd.DataFrame
    exposure: pd.DataFrame
    attribution: pd.DataFrame
    material: dict[str, bool]


def compare_factor_results(portfolio, history: pd.DataFrame, today: pd.DataFrame, attribution_days: int | None = None) -> FactorImpact:
    corrected_history = coupon_corrected_history(history)
    corrected_today = coupon_corrected_curve(today)
    correction_bp = ((history - corrected_history) * 10000.0).mean()

    pca, pca_c = compute_curve_pca(history), compute_curve_pca(corrected_history)
    cosines = [
        abs(float(np.dot(pca.loadings.loc[k], pca_c.loadings.loc[k])))
        for k in pca.loadings.index
    ]
    loadings = pd.DataFrame({
        "cosine": cosines,
        "variance_share": pca.explained_variance_ratio,
        "variance_share_corrected": pca_c.explained_variance_ratio,
    }, index=pca.loadings.index)

    def exposure_pnl(curve, pca_result):
        return [e.dollar_pnl for e in compute_portfolio_factor_exposure(portfolio, curve, pca_result).exposures]

    exposure = pd.DataFrame({
        "original": exposure_pnl(today, pca),
        "pca_corrected": exposure_pnl(today, pca_c),
        "curve_corrected": exposure_pnl(corrected_today, pca),
        "both_corrected": exposure_pnl(corrected_today, pca_c),
    }, index=pca.loadings.index)

    scores, scores_c = compute_pc_scores(history, pca), compute_pc_scores(corrected_history, pca_c)
    days = scores.scores.index if attribution_days is None else scores.scores.index[-attribution_days:]
    records = []
    for day in days:
        original = compute_factor_pnl_attribution(portfolio, history, pca, scores, day)
        corrected = compute_factor_pnl_attribution(portfolio, corrected_history, pca_c, scores_c, day)
        for k in pca.loadings.index:
            records.append({
                "date": day, "component": k,
                "original": original.attributed_dollar[k],
                "corrected": corrected.attributed_dollar[k],
                "actual": original.actual_dollar_pnl,
            })
    attribution = pd.DataFrame(records)

    exposure_change = (exposure["both_corrected"] - exposure["original"]).abs() / exposure["original"].abs()
    typical_pnl = attribution.drop_duplicates("date")["actual"].abs().median()
    attribution_change = (
        (attribution["corrected"] - attribution["original"]).abs().groupby(attribution["component"]).median() / typical_pnl
    )
    material = {
        "loading_shape": bool((loadings["cosine"] < MATERIAL_LOADING_COSINE).any()),
        "variance_share": bool(
            ((loadings["variance_share_corrected"] - loadings["variance_share"]).abs() * 100 > MATERIAL_VARIANCE_SHARE_PP).any()
        ),
        "exposure": bool((exposure_change > MATERIAL_EXPOSURE_CHANGE).any()),
        "attribution": bool((attribution_change > MATERIAL_ATTRIBUTION_SHARE).any()),
    }
    return FactorImpact(correction_bp, loadings, exposure, attribution, material)


if __name__ == "__main__":
    # Reproduces docs/special_phase_b_documentation.md §4 (committed data).
    from config.portfolio_loader import load_portfolio
    from data.jgb_curve_history_loader import load_jgb_curve_history
    from data.jgb_curve_loader import load_jgb_curve
    from models.pca import DEFAULT_PCA_LOOKBACK_YEARS

    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False)
    impact = compare_factor_results(load_portfolio(), history, load_jgb_curve(prefer_live=False, verbose=False))
    print("Average correction over the window (bp):")
    print(impact.correction_bp.round(2).to_string())
    print("\nPCA loadings, original vs corrected:")
    print(impact.loadings.round(4).to_string())
    print("\n+1 std factor P&L (per 100 face):")
    exposure = impact.exposure.copy()
    for column in ("pca_corrected", "curve_corrected", "both_corrected"):
        exposure[f"{column}_pct"] = (exposure[column] / exposure["original"] - 1.0) * 100.0
    print(exposure.round(4).to_string())
    attribution = impact.attribution
    change = (attribution["corrected"] - attribution["original"]).abs()
    typical = attribution.drop_duplicates("date")["actual"].abs().median()
    print(f"\nDaily attribution change, median per factor, as a share of the median |daily P&L| ({typical:.4f}):")
    print((change.groupby(attribution["component"]).median() / typical).round(4).to_string())
    print("\nMaterial:", impact.material)
