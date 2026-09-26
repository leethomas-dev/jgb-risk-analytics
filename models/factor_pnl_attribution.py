"""
factor_pnl_attribution.py

Phase 4D, Section 2: attributes one day's REALIZED portfolio P&L to the
PCA factors Phase 4B found and Phase 4C already sized for this portfolio,
using Phase 4D's own daily PC scores (models.pc_scores) for "how big did
each factor actually move that day."

    attributed P&L from factor i = (portfolio sensitivity to factor i,
                                     per unit of RAW score)
                                    * score_i for that day

"Portfolio sensitivity per unit of raw score" is NOT re-derived here --
it's Phase 4C's own +1-standard-deviation FactorExposure (pct_pnl /
dollar_pnl), rescaled from "per 1 std" to "per unit of decimal-yield
score" by dividing by that component's own component_std (sqrt of its
eigenvalue: FactorExposure.pct_pnl is exactly the P&L for a shock of size
component_std, so dividing by component_std gives the P&L per unit of raw
score, and multiplying that by the day's ACTUAL score gives that day's
attributed P&L). This keeps every KRD/DV01 number this module ultimately
depends on exactly what Phase 4C already validated -- nothing is
recomputed from a KRD vector directly in this file.

TWO CURVES, TWO SPECIFIC DATES -- not "the curve" and "today". A daily
attribution needs the actual change BETWEEN two specific days.
compute_factor_pnl_attribution takes an `as_of_date` (one of
models.pc_scores's own score dates -- a date with a genuine prior row to
change FROM) and builds both days' curves directly from `history`'s own
rows, on EXACTLY the PCA's own tenor grid -- no separate interpolation
onto Phase 1's live curve grid is needed here (unlike Phase 4C), because a
historical curve row already lives on the same grid the PCA loadings do.

THE RESIDUAL IS THE POINT, NOT AN AFTERTHOUGHT. The three attributed
components are a LINEAR, three-factor estimate; actual portfolio P&L is
computed by an EXACT reprice off both days' real curves
(models.bond_pricing.price_portfolio), and the gap between the two is
reported as `residual_pct` / `residual_dollar` -- never netted away or
hidden. By construction, attributed_pct.sum() + residual_pct ==
actual_pct_pnl exactly (see this module's own implementation, below) -- a
persistently large residual is a genuine finding about the three-factor
model's limits, not a bug to explain away (see
docs/phase_4d_documentation.md for the actual measured size).

Reads portfolio/curve history through their own loaders/modules; computes
no new sensitivity of its own (same "recombine what's already validated"
pattern models/factor_exposure.py and models/ultra_long_profile.py use).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from models.bond_pricing import price_portfolio
from models.factor_exposure import compute_portfolio_factor_exposure
from models.key_rate_duration import DEFAULT_BUMP_SIZE
from models.pc_scores import PCScoreResult, compute_pc_scores
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, CurvePCAResult, compute_curve_pca


def _curve_from_history_row(history: pd.DataFrame, date) -> pd.DataFrame:
    """A Phase-1-shaped curve DataFrame (columns maturity_years, yield)
    built directly from one row of `history` -- already on history's own
    tenor grid (ascending, per load_jgb_curve_history's contract), no
    interpolation involved."""
    row = history.loc[date]
    return (
        pd.DataFrame(
            {
                "maturity_years": history.columns.to_numpy(dtype=float),
                "yield": row.to_numpy(dtype=float),
            }
        )
        .sort_values("maturity_years")
        .reset_index(drop=True)
    )


@dataclass(frozen=True)
class FactorPnLAttribution:
    """One day's factor P&L attribution -- frozen, a snapshot for one
    SPECIFIC pair of dates.

    as_of_date / previous_date : the exact two dates this attribution's
        change is between (ISO strings) -- a daily attribution is only
        meaningful paired with which two days it's the change BETWEEN;
        there is no genuine "today" when running off cached/historical
        data.
    n_components : how many factors this covers -- matches pca_result's.
    scores : that day's raw PC scores (decimal yield-change units, same
        convention as models.pc_scores), index "PC1".."PCn".
    attributed_pct / attributed_dollar : pd.Series, index = component
        number (1..n_components) -- factor i's contribution to the day's
        P&L. pct is a fraction of portfolio value; dollar is currency per
        100 face value (Phase 3B's DV01 convention).
    residual_pct / residual_dollar : actual repriced P&L minus the sum of
        the attributed components -- the part the linear three-factor
        model does NOT explain. Reported, never hidden.
    actual_pct_pnl / actual_dollar_pnl : the REAL portfolio P&L, from an
        EXACT reprice off both days' curves (not a linear estimate).
        attributed_*.sum() + residual_* == actual_*_pnl exactly, by
        construction.
    base_price : portfolio weighted price on previous_date's curve
        (currency per 100 face) -- the denominator for the pct figures.
    """

    as_of_date: str
    previous_date: str
    n_components: int
    scores: pd.Series
    attributed_pct: pd.Series
    attributed_dollar: pd.Series
    residual_pct: float
    residual_dollar: float
    actual_pct_pnl: float
    actual_dollar_pnl: float
    base_price: float


def compute_factor_pnl_attribution(
    portfolio: list[Bond],
    history: pd.DataFrame,
    pca_result: CurvePCAResult,
    score_result: PCScoreResult,
    as_of_date,
    freq: int | None = None,
    bump_size: float = DEFAULT_BUMP_SIZE,
) -> FactorPnLAttribution:
    """Attribute the portfolio's P&L for the change ending on `as_of_date`
    to `pca_result`'s components, using `score_result`'s own scores.

    Parameters
    ----------
    portfolio : as returned by config.portfolio_loader.load_portfolio.
    history : the SAME history `score_result` was computed from (its
        tenor grid must match pca_result.tenors -- models.pc_scores
        already enforces this at score-computation time; this function
        additionally needs `history`'s own yield LEVELS, not just the
        scores, to build both days' curves).
    pca_result : the PCA fit `score_result` and `history` are both
        consistent with.
    score_result : a models.pc_scores.compute_pc_scores(history,
        pca_result) result.
    as_of_date : one of score_result.scores' own dates -- a date with a
        real prior row in `history` to change FROM.
    freq, bump_size : passed through to compute_portfolio_factor_exposure
        and price_portfolio unchanged.

    Returns
    -------
    FactorPnLAttribution

    Raises
    ------
    ValueError : `as_of_date` is not one of score_result.scores' dates.
    """
    as_of_ts = pd.Timestamp(as_of_date)
    if as_of_ts not in score_result.scores.index:
        raise ValueError(
            f"{as_of_date} is not a valid attribution date -- it must be one of "
            "score_result.scores' own dates (a date with a real prior row to "
            "change FROM; the earliest date in the underlying history has none)."
        )

    sorted_index = history.sort_index().index
    loc = sorted_index.get_loc(as_of_ts)
    previous_ts = sorted_index[loc - 1]

    curve_prev = _curve_from_history_row(history, previous_ts)
    curve_now = _curve_from_history_row(history, as_of_ts)

    # Sensitivities computed off the PREVIOUS day's curve (start-of-period
    # greeks applied to the realized move) -- the standard convention for
    # duration-based daily P&L attribution.
    exposure = compute_portfolio_factor_exposure(
        portfolio, curve_prev, pca_result, freq=freq, bump_size=bump_size
    )

    scores_today = score_result.scores.loc[as_of_ts]
    components = list(range(1, pca_result.n_components + 1))

    attributed_pct = pd.Series(index=components, dtype=float, name="attributed_pct")
    attributed_dollar = pd.Series(index=components, dtype=float, name="attributed_dollar")
    for e in exposure.exposures:
        std = pca_result.component_std[e.component - 1]
        score = float(scores_today[f"PC{e.component}"])
        attributed_pct[e.component] = (e.pct_pnl / std) * score
        attributed_dollar[e.component] = (e.dollar_pnl / std) * score

    price_table_prev = price_portfolio(portfolio, curve_prev, freq=freq)
    base_price = float((price_table_prev["weight"] * price_table_prev["price"]).sum())
    price_table_now = price_portfolio(portfolio, curve_now, freq=freq)
    now_price = float((price_table_now["weight"] * price_table_now["price"]).sum())

    actual_dollar_pnl = now_price - base_price
    actual_pct_pnl = actual_dollar_pnl / base_price

    residual_pct = actual_pct_pnl - float(attributed_pct.sum())
    residual_dollar = actual_dollar_pnl - float(attributed_dollar.sum())

    return FactorPnLAttribution(
        as_of_date=str(as_of_ts.date()),
        previous_date=str(previous_ts.date()),
        n_components=pca_result.n_components,
        scores=scores_today.loc[[f"PC{c}" for c in components]],
        attributed_pct=attributed_pct,
        attributed_dollar=attributed_dollar,
        residual_pct=residual_pct,
        residual_dollar=residual_dollar,
        actual_pct_pnl=actual_pct_pnl,
        actual_dollar_pnl=actual_dollar_pnl,
        base_price=base_price,
    )


if __name__ == "__main__":
    portfolio = load_portfolio()
    history = load_jgb_curve_history(lookback_years=DEFAULT_PCA_LOOKBACK_YEARS, prefer_live=False)
    pca_result = compute_curve_pca(history)
    score_result = compute_pc_scores(history, pca_result)

    latest_date = score_result.scores.index[-1]
    result = compute_factor_pnl_attribution(portfolio, history, pca_result, score_result, latest_date)

    print()
    print(f"Factor P&L attribution: {result.previous_date} -> {result.as_of_date}")
    print(f"Base price ({result.previous_date}, per 100 face): {result.base_price:.4f}")
    print()
    for c in range(1, result.n_components + 1):
        print(
            f"  PC{c}: score = {result.scores[f'PC{c}'] * 10000:+7.2f}bp   "
            f"attributed = {result.attributed_dollar[c]:+9.4f} /100 face   ({result.attributed_pct[c]:+.4%})"
        )
    print(f"  Residual:   {result.residual_dollar:+9.4f} /100 face   ({result.residual_pct:+.4%})")
    print(f"  Actual P&L: {result.actual_dollar_pnl:+9.4f} /100 face   ({result.actual_pct_pnl:+.4%})")

    print()
    check = result.attributed_dollar.sum() + result.residual_dollar
    print(f"Sanity check -- attributed + residual == actual: {check:.6f} vs {result.actual_dollar_pnl:.6f}")
