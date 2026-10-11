"""
pca_par_vs_zero.py

Regenerates the figures in docs/phase_4b_documentation.md §1.8: PCA fitted on
MOF's par yields (what the project uses) vs. on bootstrapped zero rates, on
the committed data (prefer_live=False), and the portfolio's +1 std P&L under
each factor set.

Par factors are applied the project's way (shock the par curve, re-bootstrap,
reprice). Zero factors are applied to the zero curve directly, with the
15-tenor shock interpolated straight-line onto the bootstrap grid. Zero
components are sign-aligned to the par ones so the two can be read side by
side.

Run from the repo root:  python -m validation.pca_par_vs_zero
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.portfolio_loader import load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from data.jgb_curve_loader import load_jgb_curve
from models.bond_pricing import price_portfolio
from models.bootstrap import bootstrap_zero_curve, bootstrap_zero_curve_history
from models.factor_exposure import compute_portfolio_factor_exposure
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca


def _roughness_bp(history: pd.DataFrame) -> pd.Series:
    """RMS second difference across neighbouring tenors of daily changes, in
    bp -- how jagged a typical day's move is at each interior tenor."""
    d = history.sort_index().diff().dropna().to_numpy() * 1e4
    second = d[:, 2:] - 2 * d[:, 1:-1] + d[:, :-2]
    return pd.Series(np.sqrt((second**2).mean(axis=0)), index=history.columns[1:-1])


def _portfolio_value(portfolio, curve: pd.DataFrame) -> float:
    table = price_portfolio(portfolio, curve)
    return float((table["weight"] * table["price"]).sum())


def main() -> None:
    history = load_jgb_curve_history(
        lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False, verbose=False
    )
    zero_history = bootstrap_zero_curve_history(history)
    par_pca, zero_pca = compute_curve_pca(history), compute_curve_pca(zero_history)
    tenors = history.columns.to_numpy(dtype=float)
    components = list(par_pca.loadings.index)
    signs = {k: np.sign(np.dot(par_pca.loadings.loc[k], zero_pca.loadings.loc[k])) or 1.0 for k in components}

    print(f"Window: {history.index.min().date()} to {history.index.max().date()}, {len(history)} dates x {len(tenors)} tenors")
    print("\nComponent   variance share (par / zero)   std bp (par / zero)   loading cosine")
    for k in components:
        cosine = abs(float(np.dot(par_pca.loadings.loc[k], zero_pca.loadings.loc[k])))
        print(
            f"  PC{k}        {par_pca.explained_variance_ratio[k - 1]:6.1%} / {zero_pca.explained_variance_ratio[k - 1]:6.1%}"
            f"            {par_pca.component_std[k - 1] * 1e4:5.2f} / {zero_pca.component_std[k - 1] * 1e4:5.2f}"
            f"          {cosine:.4f}"
        )

    shocks = pd.DataFrame(index=tenors)
    for k in components:
        shocks[f"par PC{k}"] = par_pca.loadings.loc[k].to_numpy() * par_pca.component_std[k - 1] * 1e4
        shocks[f"zero PC{k}"] = signs[k] * zero_pca.loadings.loc[k].to_numpy() * zero_pca.component_std[k - 1] * 1e4
    print("\n+1 std shock by tenor, bp:")
    print(shocks.round(2).to_string())

    rough = pd.DataFrame({"par": _roughness_bp(history), "zero": _roughness_bp(zero_history)})
    rough["zero/par"] = rough["zero"] / rough["par"]
    print("\nRoughness of daily changes (RMS second difference across tenors), bp:")
    print(rough.round(2).T.to_string())

    portfolio = load_portfolio()
    today = load_jgb_curve(prefer_live=False, verbose=False)
    par_pnl = {e.component: e.dollar_pnl for e in compute_portfolio_factor_exposure(portfolio, today, par_pca).exposures}
    zero_curve = bootstrap_zero_curve(today)
    base = _portfolio_value(portfolio, zero_curve)
    zero_pnl = {}
    for k in components:
        shock = signs[k] * zero_pca.loadings.loc[k].to_numpy() * zero_pca.component_std[k - 1]
        shocked = zero_curve.copy()
        shocked["zero_rate"] = shocked["zero_rate"] + np.interp(shocked["maturity_years"], tenors, shock)
        zero_pnl[k] = _portfolio_value(portfolio, shocked) - base

    print("\n+1 std factor P&L per 100 face: par factors (shock par, re-bootstrap) | zero factors (shock zero)")
    for k in components:
        print(f"  PC{k}: {par_pnl[k]:+.4f} | {zero_pnl[k]:+.4f}")
    par_total = float(np.sqrt(sum(v**2 for v in par_pnl.values())))
    zero_total = float(np.sqrt(sum(v**2 for v in zero_pnl.values())))
    print(f"  Combined 1 std (sqrt sum of squares): {par_total:.4f} | {zero_total:.4f} ({zero_total / par_total - 1:+.1%})")


if __name__ == "__main__":
    main()
