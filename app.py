"""
app.py

Phase 4.7: a minimal Streamlit dashboard over this project's existing
analytics modules -- six phases of analytics that, before this, only ever
produced terminal output and PNGs in outputs/.

THE UI LAYER STAYS THIN. Every number and chart below comes from a
function models/ or data/ already expose -- nothing here recomputes a
financial quantity. Two small, additive accessors were added elsewhere so
this file wouldn't have to reach into anything private to get what it
needs:

  - data/jgb_curve_loader.py: load_jgb_curve_with_source() (new) -- like
    load_jgb_curve(), but also reports which tier (live/cache/snapshot)
    served the curve and the date it reflects, so this dashboard can
    disclose data freshness directly rather than only logging it to
    stderr. load_jgb_curve() itself is unchanged (verified: the project's
    full pre-existing test suite still passes).
  - models/ultra_long_profile.py: NORMAL_COLOR / ULTRA_LONG_COLOR (new,
    promoted from two inline hex literals) -- so this file's Plotly chart
    uses the exact same palette as that module's own matplotlib chart,
    rather than a second, independently-chosen one.

The rest -- portfolio weight editing, table joins, chart data shaping -- is
presentation logic only: reading already-computed results and laying them
out. See docs/phase_4_7_documentation.md for the full account, including
the caching strategy and what's deliberately out of scope.
"""

from __future__ import annotations

import json
import os
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.portfolio_loader import Bond, load_portfolio
from data.jgb_curve_history_loader import load_jgb_curve_history
from data.jgb_curve_loader import CurveSource, load_jgb_curve_with_source
from models.bond_analytics import bond_analytics_portfolio
from models.bond_pricing import dirty_price_portfolio
from models.bootstrap import bootstrap_zero_curve
from models.cash_flow_ladder import COUPON_COLOR, PRINCIPAL_COLOR, compute_cash_flow_ladder
from models.curve_fitting import fit_nelson_siegel, fit_svensson
from models.dv01 import dv01_portfolio
from models.factor_exposure import compute_portfolio_factor_exposure
from models.pca import DEFAULT_PCA_LOOKBACK_YEARS, compute_curve_pca
from models.ultra_long_profile import NORMAL_COLOR, ULTRA_LONG_COLOR, compute_ultra_long_profile

GITHUB_URL = "https://github.com/leethomas-dev/jgb-risk-analytics"

# Configurable, not hardcoded (per the phase brief's Part B instruction):
# PCA over the full historical series is the most memory-hungry step this
# app runs. If Streamlit Community Cloud's ~1GB free-tier ceiling ever
# makes the default 2-year window too heavy, shorten it by setting this
# env var in the app's deployment settings -- no code change needed. See
# docs/phase_4_7_documentation.md "the memory constraint".
PCA_LOOKBACK_YEARS = float(
    os.environ.get("JGB_DASHBOARD_PCA_LOOKBACK_YEARS", DEFAULT_PCA_LOOKBACK_YEARS)
)

# Freshest-available data, degrading gracefully to cache/snapshot on
# failure -- both loaders already do this internally (Phase 1 / 4A), so
# there is nothing extra to handle here for "no assumption a live fetch
# will succeed" (Part B). This is a deliberate divergence from the test
# suite's own prefer_live=False convention: reproducibility matters for a
# validation run, not for a dashboard whose whole point is showing today's
# risk, with the source tier disclosed either way (section 2, below).
PREFER_LIVE = True

st.set_page_config(page_title="JGB Risk Analytics", layout="wide", page_icon="\U0001F4C8")

# ---------------------------------------------------------------------------
# Visual theme -- CSS only, layered on top of .streamlit/config.toml's base
# dark theme. Cosmetic, and deliberately kept separate from every analytics
# call above/below it: nothing in this block reads or touches a computed
# number. A scope expansion past Phase 4.7's original "no custom CSS/
# animation" brief, done at the user's explicit request -- see
# docs/phase_4_7_documentation.md.
#
# [data-testid="..."] selectors below are Streamlit's internal DOM hooks,
# not a public/versioned API -- they can change in a future Streamlit
# release, in which case this block just silently stops matching (no error,
# app still fully functional, just less styled). Same caveat applies to the
# anime.js block at the very end of this file, which targets the same hooks.
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=JetBrains+Mono:wght@400;600&display=swap');

:root {
  --accent-cyan: #22d3ee;
  --accent-violet: #a78bfa;
  --bg-panel: #121a29;
  --border-glow: rgba(34, 211, 238, 0.28);
}

[data-testid="stAppViewContainer"] {
  background: radial-gradient(circle at 12% -10%, #14213d 0%, #0b0f17 45%, #05070c 100%);
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0d1420 0%, #0a0e17 100%);
  border-right: 1px solid var(--border-glow);
}

h1, h2, h3 {
  font-family: 'Space Grotesk', sans-serif !important;
  background: linear-gradient(90deg, var(--accent-cyan), var(--accent-violet));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent !important;
  letter-spacing: 0.01em;
  animation: jgbFadeSlideIn 0.6s ease-out both;
}

/* Pure CSS entrance -- deliberately NOT driven by the anime.js iframe at
   the bottom of the page (see the block near the end of this file). A
   browser throttles/pauses requestAnimationFrame inside an iframe that
   isn't currently in the viewport; on first load that iframe is below the
   fold, so a JS-driven opacity:0->1 fade gets stuck at 0 until a scroll
   brings the iframe into view and un-throttles it. A CSS @keyframes
   animation runs on the compositor thread instead and isn't subject to
   that -- it always plays on paint, regardless of scroll position. */
[data-testid="stMetric"] {
  background: var(--bg-panel);
  border: 1px solid var(--border-glow);
  border-radius: 12px;
  padding: 1rem 1rem 0.7rem 1rem;
  box-shadow: 0 0 24px rgba(34, 211, 238, 0.08);
  transition: transform 0.25s ease, box-shadow 0.25s ease;
  animation: jgbFadeSlideIn 0.5s ease-out both;
}
[data-testid="stMetric"]:hover {
  transform: translateY(-3px);
  box-shadow: 0 0 32px rgba(34, 211, 238, 0.24);
}
[data-testid="stMetricValue"] {
  font-family: 'JetBrains Mono', monospace !important;
  color: var(--accent-cyan) !important;
}

.stButton button {
  background: linear-gradient(90deg, var(--accent-cyan), var(--accent-violet));
  color: #05070c;
  border: none;
  font-weight: 600;
  transition: box-shadow 0.25s ease;
}
.stButton button:hover {
  box-shadow: 0 0 18px rgba(167, 139, 250, 0.55);
}

@keyframes jgbFadeSlideIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
""",
    unsafe_allow_html=True,
)


def _style_fig(fig: go.Figure) -> go.Figure:
    """Cosmetic only: lay the dashboard's dark theme over a Plotly figure
    (background/gridline/font). Never touches a trace's own data or any of
    the project's validated semantic colors (COLOR_GAIN/COLOR_LOSS,
    NORMAL_COLOR/ULTRA_LONG_COLOR, COUPON_COLOR/PRINCIPAL_COLOR) -- those
    stay exactly as validated elsewhere in the project (docs/*.md, the
    dataviz skill's palette checker); this only restyles the chart chrome
    around them."""
    fig.update_layout(
        template="plotly_dark",
        # SOLID, not transparent. A transparent paper_bgcolor lets whatever
        # sits behind the chart's own DOM node show through -- and that
        # turned out not to reliably be this dashboard's dark page
        # background in every rendering context, leaving the bright
        # (dark-background-tuned) line colors on a white surface with poor
        # contrast. An opaque color makes the chart's own background a
        # guarantee, not a hope resting on the surrounding page's CSS.
        paper_bgcolor="#0d1420",
        plot_bgcolor="#121a29",
        font=dict(family="JetBrains Mono, monospace", color="#e6edf3"),
        # Plotly's own default (namelength=15) truncates a hover label's
        # trace name with an ellipsis -- e.g. "Par curve (quoted)" showing
        # as "Par curve (q...". -1 means "never truncate": show the full
        # name every time, regardless of length.
        hoverlabel=dict(namelength=-1, font=dict(family="JetBrains Mono, monospace")),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.08)", zerolinecolor="rgba(255,255,255,0.15)")
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.08)", zerolinecolor="rgba(255,255,255,0.15)")
    return fig


# ---------------------------------------------------------------------------
# Cached loaders -- the expensive paths (network fetches, PCA's SVD, the
# NS/Svensson grid searches) are cached so dragging a weight slider doesn't
# re-run any of them. See docs/phase_4_7_documentation.md "caching strategy"
# for what recomputes on every rerun instead (the portfolio-dependent
# analytics below -- cheap, and must reflect a live edit immediately).
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Fetching the JGB par curve...")
def _cached_curve_source(prefer_live: bool) -> CurveSource:
    return load_jgb_curve_with_source(prefer_live=prefer_live, verbose=False)


@st.cache_data(show_spinner="Loading JGB curve history (slow on a cold start)...")
def _cached_history(lookback_years: float, prefer_live: bool) -> pd.DataFrame:
    return load_jgb_curve_history(
        lookback_years=lookback_years, prefer_live=prefer_live, verbose=False
    )


@st.cache_data(show_spinner="Fitting PCA risk factors...")
def _cached_pca(history: pd.DataFrame):
    return compute_curve_pca(history)


@st.cache_data(show_spinner="Bootstrapping the zero curve...")
def _cached_bootstrap(curve: pd.DataFrame) -> pd.DataFrame:
    return bootstrap_zero_curve(curve)


@st.cache_data(show_spinner="Fitting Nelson-Siegel / Svensson...")
def _cached_curve_fits(zero_curve: pd.DataFrame):
    return fit_nelson_siegel(zero_curve), fit_svensson(zero_curve)


# ---------------------------------------------------------------------------
# Portfolio: read through the Phase 2A loader, never a hardcoded bond list.
# Weight edits are re-validated through THAT SAME loader (load_portfolio),
# never a reimplemented check -- see load_edited_portfolio below.
# ---------------------------------------------------------------------------

BASE_PORTFOLIO: list[Bond] = load_portfolio()


def _portfolio_json_payload(bonds: list[Bond], weights: dict[str, float]) -> dict:
    """Build a config/portfolio.json-shaped payload from `bonds`, with each
    bond's weight replaced by `weights[bond.name]`. Pure data reshaping --
    no validation happens here; that's load_portfolio's job (below)."""
    entries = []
    for b in bonds:
        entry = {
            "name": b.name,
            "maturity_years": b.maturity_years,
            "coupon_rate": b.coupon_rate,
            "face_value": b.face_value,
            "weight": weights[b.name],
        }
        for field in ("isin", "issue_date", "maturity_date", "tenor_class"):
            value = getattr(b, field)
            if value is not None:
                entry[field] = value
        entries.append(entry)
    return {"portfolio_name": "dashboard_edited_portfolio", "bonds": entries}


def load_edited_portfolio(weights: dict[str, float]) -> tuple[list[Bond] | None, str | None]:
    """Write an edited-weights portfolio to a temp file and load it through
    config.portfolio_loader.load_portfolio -- the REAL Phase 2A validation
    (weights summing to 1.0 within tolerance, no negative weight, etc.),
    never reimplemented here. Returns (bonds, None) on success, or
    (None, error_message) if load_portfolio rejects the input."""
    payload = _portfolio_json_payload(BASE_PORTFOLIO, weights)
    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        return load_portfolio(path=tmp_path), None
    except ValueError as exc:
        return None, str(exc)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Sidebar: portfolio weight editing + reset, and a link back to the repo.
# ---------------------------------------------------------------------------

st.sidebar.title("JGB Risk Analytics")
st.sidebar.markdown(f"[View source on GitHub]({GITHUB_URL})")
st.sidebar.divider()
st.sidebar.subheader("Portfolio weights")
st.sidebar.caption(
    "Edit the Weight % column below. A table (st.data_editor) was chosen over "
    "one number input per bond so maturity and coupon stay visible as context "
    "right next to the weight being changed, in one compact control."
)

if "weights" not in st.session_state:
    st.session_state.weights = {b.name: b.weight for b in BASE_PORTFOLIO}


def _reset_weights() -> None:
    st.session_state.weights = {b.name: b.weight for b in BASE_PORTFOLIO}


editor_source = pd.DataFrame(
    [
        {
            "Bond": b.name,
            "Maturity (Y)": b.maturity_years,
            # Rounded here, not just via the column's display format below --
            # e.g. 0.035 * 100.0 == 3.5000000000000004 in binary floating
            # point, and st.data_editor shows that raw value once a cell is
            # focused/edited, regardless of the format string. Rounding the
            # underlying number avoids the artifact instead of only hiding it.
            "Coupon %": round(b.coupon_rate * 100.0, 4),
            "Weight %": round(st.session_state.weights[b.name] * 100.0, 4),
        }
        for b in BASE_PORTFOLIO
    ]
)

edited = st.sidebar.data_editor(
    editor_source,
    hide_index=True,
    width="stretch",
    key="portfolio_editor",
    column_config={
        "Bond": st.column_config.TextColumn(disabled=True),
        "Maturity (Y)": st.column_config.NumberColumn(disabled=True, format="%.0f"),
        "Coupon %": st.column_config.NumberColumn(disabled=True, format="%.3f"),
        "Weight %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=0.5, format="%.2f"),
    },
)

for _, row in edited.iterrows():
    st.session_state.weights[row["Bond"]] = round(float(row["Weight %"]) / 100.0, 6)

weight_sum = sum(st.session_state.weights.values())
st.sidebar.caption(f"Weights sum to **{weight_sum:.2%}** (must total 100%).")
st.sidebar.button("Reset to default weights", on_click=_reset_weights, width="stretch")

portfolio, portfolio_error = load_edited_portfolio(st.session_state.weights)

# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

st.title("JGB Portfolio Risk Analytics")
st.caption(
    "Interest-rate risk analytics on an illustrative Japanese Government Bond "
    "portfolio -- not a real fund's holdings. Six bonds (2Y-40Y); edit their "
    "weights in the sidebar."
)

if portfolio_error:
    st.error(
        "The edited portfolio is invalid, so the analytics below are showing "
        f"the last valid state instead:\n\n**{portfolio_error}**\n\n"
        "Fix the weights in the sidebar (they must sum to 100%) to update them."
    )
    st.stop()

curve_source = _cached_curve_source(PREFER_LIVE)
curve = curve_source.curve

history = _cached_history(PCA_LOOKBACK_YEARS, PREFER_LIVE)
pca_result = _cached_pca(history)

zero_curve = _cached_bootstrap(curve)
ns_fit, sv_fit = _cached_curve_fits(zero_curve)

# Which parametric fit is shown as "the fitted curve": whichever actually
# has the lower RMSE on THIS curve (Phase 4.5B's own method -- measure, don't
# assume a winner), preferring a converged fit over a non-converged one
# regardless of RMSE, since a non-converged fit's parameters are not
# trustworthy even if its RMSE happens to look good
# (docs/phase_4_5b_documentation.md §5/§8.4). Re-derived against whatever
# curve is actually loaded, rather than hardcoding the snapshot curve's own
# "Svensson wins" finding -- a live curve can behave differently (§5's own
# live-data anecdote).
if sv_fit.converged and (not ns_fit.converged or sv_fit.rmse_bp <= ns_fit.rmse_bp):
    better_fit, better_name = sv_fit, "Svensson"
elif ns_fit.converged:
    better_fit, better_name = ns_fit, "Nelson-Siegel"
else:
    better_fit, better_name = sv_fit, "Svensson (neither fit cleanly converged)"

dv01_df = dv01_portfolio(portfolio, curve)
portfolio_dv01 = float((dv01_df["weight"] * dv01_df["dv01"]).sum())

analytics_df = bond_analytics_portfolio(portfolio, curve)
portfolio_mod_dur = float(analytics_df.loc["portfolio_total", "modified_duration"])
portfolio_convexity = float(analytics_df.loc["portfolio_total", "convexity"])

ultra_long = compute_ultra_long_profile(portfolio, curve)

# --- 1. Headline metrics -----------------------------------------------
st.header("1. Headline risk metrics")
c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Portfolio DV01",
    f"{portfolio_dv01:.4f}",
    help="Currency per 100 face value, for a 0.01% (1bp) move in rates. Not a real position size -- the portfolio is illustrative.",
)
c2.metric("Modified duration", f"{portfolio_mod_dur:.2f} yrs")
c3.metric("Convexity", f"{portfolio_convexity:.1f}")
c4.metric("Risk beyond 20Y (DV01 share)", f"{ultra_long.dv01_ultra_long_share:.1%}")
st.caption(
    "DV01: how many currency units the portfolio gains or loses if every rate moves by 0.01%. "
    "Duration: roughly the % price move for a 1% rate move. Convexity: a correction that matters more "
    "for a bigger rate move. Last figure: how much of the portfolio's total interest-rate risk sits in "
    "bonds due in 20 years or more -- the 'ultra-long' segment that's a defining feature of the JGB market."
)

# --- 2. Yield curve -------------------------------------------------------
st.header("2. The yield curve: quoted, bootstrapped, and fitted")
tier_note = (
    " (a fixed past date -- see the project's fallback re-anchoring policy)"
    if curve_source.source_tier == "snapshot"
    else ""
)
st.caption(f"Curve as of **{curve_source.as_of}** &nbsp;|&nbsp; source: **{curve_source.source_tier}**{tier_note}")

fine_grid = np.linspace(float(zero_curve["maturity_years"].min()), float(zero_curve["maturity_years"].max()), 300)

# Explicit per-trace colors, matching this dashboard's own theme accents --
# not left to Plotly's default color cycle, whose third color (~#00cc96, a
# pale green) is genuinely low-contrast against a white background. None of
# these three lines carry a semantic meaning (gain/loss, ultra-long/normal)
# the way this project's other validated chart colors do elsewhere in
# app.py, so they're just defined here rather than repurposing one of those.
CURVE_PAR_COLOR = "#22d3ee"  # cyan -- matches --accent-cyan
CURVE_ZERO_COLOR = "#a78bfa"  # violet -- matches --accent-violet
CURVE_FIT_COLOR = "#f59e0b"  # amber -- deliberately not green; reads on light or dark

fig_curve = go.Figure()
fig_curve.add_trace(
    go.Scatter(
        x=curve["maturity_years"], y=curve["yield"] * 100, mode="markers+lines",
        name="Par curve (quoted)", marker=dict(size=7, color=CURVE_PAR_COLOR),
        line=dict(color=CURVE_PAR_COLOR),
    )
)
fig_curve.add_trace(
    go.Scatter(
        x=zero_curve["maturity_years"], y=zero_curve["zero_rate"] * 100, mode="lines",
        name="Bootstrapped zero curve", line=dict(dash="dot", color=CURVE_ZERO_COLOR),
    )
)
fig_curve.add_trace(
    go.Scatter(
        x=fine_grid, y=better_fit.rate_at(fine_grid) * 100, mode="lines",
        name=f"{better_name} fit ({better_fit.rmse_bp:.2f}bp RMSE)",
        line=dict(color=CURVE_FIT_COLOR),
    )
)
fig_curve.update_layout(
    xaxis_title="Maturity (years)", yaxis_title="Yield (%)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    margin=dict(t=10),
)
st.plotly_chart(_style_fig(fig_curve), width="stretch")
st.caption(
    "Three views of the same market: the rates the government actually publishes (par curve), a more "
    "theoretically precise version stripped of a coupon-timing distortion (bootstrapped zero curve), and "
    "a smooth mathematical description of that zero curve's shape, picked between two candidate models by "
    "whichever actually fits this curve better."
)

# --- 3. Portfolio table ----------------------------------------------------
st.header("3. Portfolio holdings")
dirty_df = dirty_price_portfolio(portfolio, curve)
per_bond_analytics = analytics_df.loc[analytics_df.index != "portfolio_total", ["name", "ytm"]]
table = dirty_df.merge(per_bond_analytics, on="name").merge(
    dv01_df[["name", "modified_duration", "dv01"]], on="name"
)
table = table.rename(
    columns={
        "name": "Bond", "maturity_years": "Maturity (Y)", "coupon_rate": "Coupon",
        "weight": "Weight", "clean_price": "Clean price", "accrued_interest": "Accrued",
        "dirty_price": "Dirty price", "ytm": "YTM", "modified_duration": "Modified duration", "dv01": "DV01",
    }
)
st.dataframe(
    table.style.format(
        {
            "Maturity (Y)": "{:.0f}", "Coupon": "{:.3%}", "Weight": "{:.2%}",
            "Clean price": "{:.4f}", "Accrued": "{:.5f}", "Dirty price": "{:.4f}",
            "YTM": "{:.3%}", "Modified duration": "{:.4f}", "DV01": "{:.6f}",
        }
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Clean price is the quoted price; dirty price is what a buyer actually pays, including interest "
    "accrued since the last coupon. Accrued interest is shown as of today (no separate settlement date is "
    "set here), so it is zero and dirty price equals clean price. Modified duration shown here is the "
    "curve-based sensitivity DV01 is itself built from; a second, yield-based duration measure exists in "
    "this project and differs slightly at the long end (see the docs for why)."
)

# --- 4. Key rate duration ---------------------------------------------------
st.header("4. Key rate duration: where the portfolio's rate sensitivity sits")
krd_tenors = ultra_long.tenors
krd_values = ultra_long.krd_by_tenor.to_numpy()
krd_colors = [ULTRA_LONG_COLOR if t >= ultra_long.threshold_years else NORMAL_COLOR for t in krd_tenors]
fig_krd = go.Figure(go.Bar(x=[f"{t:g}Y" for t in krd_tenors], y=krd_values, marker_color=krd_colors))
fig_krd.update_layout(xaxis_title="Tenor", yaxis_title="Portfolio KRD (years)", margin=dict(t=10))
st.plotly_chart(_style_fig(fig_krd), width="stretch")
st.caption(
    f"Each bar shows how much the portfolio's value would move if only that one interest rate point moved "
    f"by 1%, holding every other rate fixed. Red bars ({ultra_long.threshold_years:.0f}Y and beyond) are "
    "the 'ultra-long' segment."
)

# --- 5. PCA factors ----------------------------------------------------
st.header("5. What moves the curve: PCA risk factors")
st.caption(
    f"Estimated from {pca_result.n_observations} daily curve changes, "
    f"{pca_result.window_start} to {pca_result.window_end}."
)

exposure = compute_portfolio_factor_exposure(portfolio, curve, pca_result)
PC_LABELS = {1: "PC1 (often ‘level’)", 2: "PC2 (often ‘slope’)", 3: "PC3 (often ‘curvature’)"}

pc_cols = st.columns(len(exposure.exposures))
for col, exp in zip(pc_cols, exposure.exposures):
    col.metric(
        PC_LABELS.get(exp.component, f"PC{exp.component}"),
        f"{exp.dollar_pnl:+.4f} /100 face",
        delta=f"{exp.pct_pnl:+.3%}",
        help=(
            f"{exp.explained_variance_ratio:.1%} of historical curve-change variance. P&L shown is for a "
            "+1 standard-deviation move in this factor alone, holding the others fixed."
        ),
    )

fig_pca = go.Figure()
for component in pca_result.loadings.index:
    fig_pca.add_trace(
        go.Scatter(
            x=pca_result.tenors, y=pca_result.loadings.loc[component], mode="lines+markers",
            name=PC_LABELS.get(component, f"PC{component}"),
        )
    )
fig_pca.update_layout(
    xaxis_title="Tenor (years)", yaxis_title="Loading",
    legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=10),
)
st.plotly_chart(_style_fig(fig_pca), width="stretch")
st.caption(
    "These lines show HOW each recurring pattern moves the curve, estimated from real history: PC1 "
    "typically moves every tenor the same direction (a parallel shift); PC2 typically moves short and "
    "long tenors in opposite directions (the curve steepens or flattens); PC3 typically bends the middle "
    "of the curve against both ends. The cards above show what a typical-sized move in each pattern would "
    "do to this portfolio's value, in currency (main figure) and percent (small figure below it)."
)

# --- 6. Cash flow ladder -----------------------------------------------
st.header("6. Cash flow ladder: when the money actually arrives")
ladder_result = compute_cash_flow_ladder(portfolio, curve)
ladder = ladder_result.ladder.copy()
ladder["year"] = pd.to_datetime(ladder["date"]).dt.year
yearly = ladder.groupby("year")[["coupon_nominal", "principal_nominal"]].sum().reset_index()

fig_ladder = go.Figure()
fig_ladder.add_trace(go.Bar(x=yearly["year"], y=yearly["coupon_nominal"], name="Coupon", marker_color=COUPON_COLOR))
fig_ladder.add_trace(
    go.Bar(x=yearly["year"], y=yearly["principal_nominal"], name="Principal", marker_color=PRINCIPAL_COLOR)
)
fig_ladder.update_layout(
    barmode="stack", xaxis_title="Year", yaxis_title="Cash flow (per 100 face, portfolio-weighted)",
    margin=dict(t=10),
)
st.plotly_chart(_style_fig(fig_ladder), width="stretch")
st.caption(
    f"Every coupon and principal payment this portfolio is scheduled to receive, grouped by year. The tall "
    f"spikes are each bond's redemption. {ladder_result.ultra_long_nominal_share:.0%} of the raw future "
    f"cash lands 20+ years out, but once discounted back to today's money that share drops to "
    f"{ladder_result.ultra_long_pv_share:.0%} -- a distant payment is worth much less today than its face "
    "amount."
)

# ---------------------------------------------------------------------------
# anime.js pass -- a count-up animation on every metric card's number.
# Purely decorative: this reads text already rendered by the metric cards
# above (never a computed number itself) and animates it in place. Loaded
# via st.iframe (a raw HTML string) because Streamlit's own st.markdown
# strips <script> tags -- this is the one place actual JS can run.
#
# DELIBERATELY DOES NOT ANIMATE OPACITY/ENTRANCE HERE -- that used to live
# in this block (fading each card in from opacity:0) and caused a real,
# reproducible bug: this iframe sits at the very bottom of the page, so on
# first load it's below the fold, and a browser throttles/pauses
# requestAnimationFrame inside an iframe that isn't in the viewport. The
# opacity:0 start was applied immediately (synchronously), but the rAF-
# driven interpolation back to opacity:1 never got a chance to run until a
# scroll brought this iframe into view -- "blank until I scroll" exactly.
# The entrance fade now lives in the CSS block near the top of this file
# instead (a @keyframes animation, which runs on the compositor thread and
# isn't subject to iframe rAF throttling at all). The count-up below has a
# 150ms delay before it starts, so if IT ever gets throttled the same way,
# the worst case is the original, correct Streamlit-rendered number just
# sits there un-animated -- never invisible, never wrong.
#
# st.iframe renders a SAME-ORIGIN sandboxed iframe, so the script reaches
# the real page through window.parent.document rather than animating its
# own (otherwise-empty) iframe. That -- and the data-testid selectors
# themselves -- are undocumented Streamlit internals, not a stable public
# API: wrapped in try/except and re-run a few times on a short delay
# (iframe mount timing vs. the main app's own render isn't guaranteed), so
# a mismatch just means nothing animates, never a crash. height=1 -- this
# component has no visible content of its own.
# ---------------------------------------------------------------------------
st.iframe(
    """
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js"></script>
<script>
(function () {
  function run() {
    try {
      var doc = window.parent.document;
      var cards = doc.querySelectorAll('[data-testid="stMetric"]');
      var fresh = [];
      cards.forEach(function (el) {
        if (el.dataset.jgbAnimated === "1") return;
        el.dataset.jgbAnimated = "1";
        fresh.push(el);
      });
      if (!fresh.length) return;

      fresh.forEach(function (card) {
        var valueEl = card.querySelector('[data-testid="stMetricValue"]');
        if (!valueEl) return;
        var text = valueEl.textContent.trim();
        var match = text.match(/-?[0-9]+(\\.[0-9]+)?/);
        if (!match) return;
        var target = parseFloat(match[0]);
        var decimals = (match[0].split(".")[1] || "").length;
        var prefix = text.slice(0, match.index);
        var suffix = text.slice(match.index + match[0].length);
        var counter = { val: 0 };
        anime({
          targets: counter,
          val: target,
          duration: 1100,
          delay: 150,
          easing: "easeOutExpo",
          update: function () {
            valueEl.textContent = prefix + counter.val.toFixed(decimals) + suffix;
          },
        });
      });
    } catch (err) {
      // Best-effort visual polish only -- see the comment above this block.
    }
  }
  run();
  setTimeout(run, 250);
  setTimeout(run, 700);
})();
</script>

<!-- motion.dev (https://motion.dev/, formerly Framer Motion) -- a spring-
     physics "settle" pop on each chart/table as it scrolls into view.
     Uses window.parent.IntersectionObserver (the PARENT window's own
     constructor, not this iframe's) so the trigger is driven by the
     browser's native intersection scheduling rather than this iframe's
     own requestAnimationFrame loop -- but Motion's animate() call itself
     still runs via THIS iframe's rAF once triggered, so it inherits the
     same throttling risk the anime.js entrance fade above used to have
     were it hiding content. The fix here is different from that one:
     rather than starting the target at opacity:0 and relying on JS to
     reveal it (exactly the pattern that caused the earlier "blank until
     scroll" bug), this animates a SCALE bounce on an element that is
     ALREADY fully visible via Streamlit's own normal render, at every
     point from before this script even runs to after it finishes. Worst
     case if Motion fails to load or the animation never completes: the
     chart just sits there at its normal, fully-visible scale -- never
     invisible, never broken, only ever less decorated. -->
<script src="https://cdn.jsdelivr.net/npm/motion@13.2.0/dist/motion.js"></script>
<script>
(function () {
  function runReveal() {
    try {
      var parentWin = window.parent;
      var doc = parentWin.document;
      if (typeof Motion === "undefined" || !Motion.animate || !parentWin.IntersectionObserver) return;

      var targets = doc.querySelectorAll('[data-testid="stPlotlyChart"], [data-testid="stDataFrame"]');
      if (!targets.length) return;

      var observer = new parentWin.IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            var el = entry.target;
            if (el.dataset.jgbRevealed === "1") return;
            el.dataset.jgbRevealed = "1";
            observer.unobserve(el);
            Motion.animate(
              el,
              { transform: ["scale(0.985)", "scale(1.006)", "scale(1)"] },
              { type: "spring", stiffness: 140, damping: 14 }
            );
          });
        },
        { root: null, threshold: 0.2 }
      );

      targets.forEach(function (el) {
        if (el.dataset.jgbRevealObserved === "1") return;
        el.dataset.jgbRevealObserved = "1";
        observer.observe(el);
      });
    } catch (err) {
      // Best-effort visual polish only -- see the comment above this block.
    }
  }
  runReveal();
  setTimeout(runReveal, 300);
  setTimeout(runReveal, 800);
})();
</script>
""",
    height=1,
)
