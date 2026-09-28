"""
special_phase_a_before_after.py

Regenerates the before/after figures in docs/special_phase_a_documentation.md
§6: every headline number priced on the par basis (before Special Phase A)
and the zero basis (after), on the committed data (prefer_live=False), so the
comparison is reproducible offline.

Run from the repo root:  python -m validation.special_phase_a_before_after
"""

from __future__ import annotations

from config.portfolio_loader import load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from data.jgb_curve_loader import load_jgb_curve
from models.bond_analytics import bond_analytics_portfolio
from models.bond_pricing import PAR_BASIS, ZERO_BASIS, price_portfolio
from models.cash_flow_ladder import compute_cash_flow_ladder
from models.dv01 import dv01_portfolio
from models.factor_exposure import compare_factor_exposure_bases, compute_portfolio_factor_exposure
from models.factor_pnl_attribution import compute_factor_pnl_attribution
from models.key_rate_duration import key_rate_duration_portfolio
from models.pc_scores import compute_pc_scores
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca
from models.ultra_long_profile import compute_ultra_long_profile


def _figures(basis, portfolio, curve, history, pca_result, scores):
    prices = price_portfolio(portfolio, curve, basis=basis)
    dv01 = dv01_portfolio(portfolio, curve, basis=basis)
    krd = key_rate_duration_portfolio(portfolio, curve, basis=basis).loc["portfolio_total"]
    ultra = compute_ultra_long_profile(portfolio, curve, basis=basis)
    exposure = compute_portfolio_factor_exposure(portfolio, curve, pca_result, basis=basis)
    attribution = compute_factor_pnl_attribution(
        portfolio, history, pca_result, scores, scores.scores.index[-1], basis=basis
    )
    ladder = compute_cash_flow_ladder(portfolio, curve, basis=basis)
    analytics = bond_analytics_portfolio(portfolio, curve, basis=basis)

    figures = {
        "portfolio price": float((prices.weight * prices.price).sum()),
        "effective duration (sum KRD)": float(krd.sum()),
        "portfolio DV01": float((dv01.weight * dv01.dv01).sum()),
        "20Y+ share of KRD": ultra.krd_ultra_long_share,
        "20Y+ share of DV01": ultra.dv01_ultra_long_share,
        "value-weighted YTM": float(analytics.loc["portfolio_total", "ytm"]),
        "PV share 20Y+ (cash flow ladder)": ladder.ultra_long_pv_share,
        "4D residual (last day)": attribution.residual_dollar,
        "4D actual P&L (last day)": attribution.actual_dollar_pnl,
    }
    for e in exposure.exposures:
        figures[f"PC{e.component} +1 std P&L per 100 face"] = e.dollar_pnl
    for name, value in zip(prices.name, prices.price):
        figures[f"{name} price"] = float(value)
    for tenor, value in krd.items():
        figures[f"KRD {tenor:g}Y"] = float(value)
    return figures


def main() -> None:
    curve = load_jgb_curve(prefer_live=False, verbose=False)
    portfolio = load_portfolio()
    history = load_jgb_curve_history(
        lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False
    )
    pca_result = compute_curve_pca(history)
    scores = compute_pc_scores(history, pca_result)

    par = _figures(PAR_BASIS, portfolio, curve, history, pca_result, scores)
    zero = _figures(ZERO_BASIS, portfolio, curve, history, pca_result, scores)

    print(f"Committed data: curve snapshot, PCA window {pca_result.window_start} to {pca_result.window_end}")
    print(f"{'figure':40s} {'par (before)':>14s} {'zero (after)':>14s}")
    for key in par:
        print(f"{key:40s} {par[key]:14.6f} {zero[key]:14.6f}")

    print("\nPar-vs-zero factor monitor (§7):")
    print(compare_factor_exposure_bases(portfolio, curve, pca_result).round(4).to_string())


if __name__ == "__main__":
    main()
