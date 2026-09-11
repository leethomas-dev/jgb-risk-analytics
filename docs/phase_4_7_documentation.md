# Phase 4.7 Documentation — `app.py` (Streamlit dashboard) + deployment readiness

## In plain English

Six phases of analytics in this project have, until now, only ever
produced terminal output and PNG files in `outputs/` — invisible to
anyone who doesn't clone the repo and run Python. This part puts a face
on all of it: a single web page where a viewer can see the portfolio's
risk numbers, edit how much is invested in each bond and watch the
numbers update, and see the same charts this project has been computing
since Phase 3, live and interactive instead of a static image. It also
makes sure that page actually works once it's deployed somewhere other
than this laptop — a different computer, with no files already sitting
on disk and no guarantee its internet connection can reach Japan's
Ministry of Finance.

---

## Technical details

**File:** `app.py` (repo root) — the dashboard. **Also touched:**
`data/jgb_curve_loader.py` (one new function), `models/ultra_long_profile.py`
(two new constants), `requirements.txt`, `.streamlit/config.toml` (new).

**What it is not:** a new analytics module. `app.py` computes nothing —
every number and chart comes from a function `models/` or `data/` already
exposed before this phase, or from one of two small, additive accessors
this phase added because nothing existing quite fit (§1).

---

## 1. The thin-UI-layer principle, and how it was maintained

The phase brief's own rule: every number and chart the dashboard shows
must come from a function the analytics modules already expose — nothing
recomputed or reimplemented in `app.py`. Concretely, in each of the six
main-panel sections:

1. **Headline metrics** — `models.dv01.dv01_portfolio` (DV01, aggregated
   the same way `dv01.py`'s own `__main__` does:
   `(df.weight * df.dv01).sum()`), `models.bond_analytics.bond_analytics_portfolio`
   (`.loc["portfolio_total", "modified_duration"/"convexity"]`),
   `models.ultra_long_profile.compute_ultra_long_profile`
   (`.dv01_ultra_long_share`).
2. **Yield curve** — `data.jgb_curve_loader.load_jgb_curve_with_source`
   (new, below), `models.bootstrap.bootstrap_zero_curve`,
   `models.curve_fitting.fit_nelson_siegel` / `fit_svensson`.
3. **Portfolio table** — `models.bond_pricing.dirty_price_portfolio`,
   `models.bond_analytics.bond_analytics_portfolio`,
   `models.dv01.dv01_portfolio`, joined on bond name (a `pd.merge` — data
   reshaping for display, not a new calculation).
4. **Key rate duration** — `compute_ultra_long_profile`'s own
   `.krd_by_tenor` / `.tenors` / `.ultra_long_tenors` (no separate call to
   `key_rate_duration_portfolio` needed — `compute_ultra_long_profile`
   already computed it internally and exposes it).
5. **PCA factors** — `models.pca.compute_curve_pca`'s `.loadings`,
   `models.factor_exposure.compute_portfolio_factor_exposure`'s
   `.exposures`.
6. **Cash flow ladder** — `models.cash_flow_ladder.compute_cash_flow_ladder`'s
   `.ladder` (year-bucketed for the chart via the same `groupby` logic
   `plot_cash_flow_ladder` already uses internally — necessary duplication,
   not a new calculation, since Part A's brief specifically asked for a
   Plotly chart, not a reuse of that function's own matplotlib figure).

**Two small, additive accessors were needed**, because the existing
modules had nothing in a directly usable shape for what the dashboard
needed:

- **`data/jgb_curve_loader.py`: `load_jgb_curve_with_source()`** (new) +
  `CurveSource` (new, frozen). `load_jgb_curve()` picks a tier (live /
  cache / snapshot) and only ever *logs* which one to stderr — a
  dashboard needs that fact as data, to disclose it in the UI (the
  project's own fallback re-anchoring policy, §7 of this doc). The
  tier-selection control flow itself was extracted from `load_jgb_curve()`
  into a shared `_load_curve_with_tier()` helper, used by both functions —
  `load_jgb_curve()`'s own signature and behavior are unchanged (confirmed:
  the full pre-existing test suite passes unchanged, before and after).
  `_fetch_live_curve()` was also extended to capture MOF's own published
  date for the row actually used (previously parsed to find the latest
  row, then discarded) — needed to answer "as of when" for the live tier
  specifically, not just "when did this machine last succeed."
- **`models/ultra_long_profile.py`: `NORMAL_COLOR` / `ULTRA_LONG_COLOR`**
  (new, promoted from two inline hex literals already used by
  `plot_ultra_long_profile`). The dashboard's own KRD chart draws the same
  categorical (< 20Y / ≥ 20Y) split as that module's matplotlib chart —
  promoting the literals to named constants means both charts share one
  palette, rather than the dashboard silently picking a second,
  independently-chosen one for the same distinction. Mirrors
  `models.factor_exposure.COLOR_GAIN`/`COLOR_LOSS`'s own earlier promotion
  for the same reason.

**Portfolio weight validation is reused, not reimplemented.** The sidebar
never checks "do the weights sum to 1.0" itself. `load_edited_portfolio()`
builds a `config/portfolio.json`-shaped payload with the edited weights,
writes it to a temp file, and calls
`config.portfolio_loader.load_portfolio(path=tmp_path)` — the real Phase
2A validation, including the exact error message it raises. An invalid
edit surfaces that message directly in the UI (`st.error`) rather than
computing anything against it; the main panel stops there
(`st.stop()`) rather than falling back to something plausible-but-wrong.

---

## 2. Caching strategy

Streamlit reruns the whole script on every interaction (a slider drag, a
button click, an edited table cell). Five loaders are wrapped in
`@st.cache_data`, keyed on their own arguments:

| Cached function | Wraps | Why |
| --- | --- | --- |
| `_cached_curve_source` | `load_jgb_curve_with_source` | Network fetch |
| `_cached_history` | `load_jgb_curve_history` | Network fetch, the slow one (~1.2MB CSV) |
| `_cached_pca` | `compute_curve_pca` | An SVD — cheap here, but depends only on `history`, which is itself cached |
| `_cached_bootstrap` | `bootstrap_zero_curve` | Feeds the curve-fitting step below |
| `_cached_curve_fits` | `fit_nelson_siegel` + `fit_svensson` | Two bounded grid searches (§4.5B docs) — the most CPU-bound step in the whole pipeline |

**What deliberately recomputes on every rerun, uncached:** every
portfolio-dependent call (`dv01_portfolio`, `bond_analytics_portfolio`,
`compute_ultra_long_profile`, `dirty_price_portfolio`,
`compute_portfolio_factor_exposure`, `compute_cash_flow_ladder`). These
must reflect a weight edit immediately, so caching them (keyed on a
`list[Bond]`, which would change on every edit anyway) would buy nothing.
They're also cheap: six bonds, at most a few hundred bump-and-reprice
calls total — comfortably sub-second, confirmed by the dashboard staying
responsive on every edit during development.

---

## 3. Charting library: Plotly, not matplotlib

The phase brief was explicit: don't reuse the matplotlib figures already
written to `outputs/` — those are for the repo and a future landing page,
not this dashboard. Plotly was chosen over Streamlit's own native charts
(`st.line_chart` etc.) for two reasons: hover tooltips (a viewer can read
an exact yield or KRD value at a point, not just eyeball a line) and
multi-series composition (the yield curve section needs three series on
different, independently-shaped x-grids on one axis — Plotly's
`go.Figure`/`add_trace` handles this directly; Streamlit's native charts
expect one shared-index DataFrame).

---

## 4. Sidebar: portfolio weight editing — `st.data_editor`, not per-bond number inputs

Both were viable. `st.data_editor` was chosen so maturity and coupon stay
visible as context in the same compact table as the weight being edited,
rather than six separate number-input widgets stacked down the sidebar
with no shared frame of reference. The tradeoff: reading edited values
back means iterating the returned DataFrame's rows rather than reading
each widget's own return value directly — a small amount of extra glue
code, judged worth it for the more legible layout.

---

## 5. Which parametric fit is shown — measured on THIS curve, not hardcoded

Phase 4.5B found Svensson fits the *committed snapshot* curve better
(3.2bp vs. 5.9bp RMSE) — but that same doc's §5 also found Nelson-Siegel
can fail to converge on a *live* 15-tenor grid lacking sub-year points.
The dashboard doesn't hardcode "always show Svensson": it fits both
(`fit_nelson_siegel`, `fit_svensson`, both cached) against whatever curve
is actually loaded, and picks whichever has the lower `rmse_bp`,
preferring a converged fit over a non-converged one regardless of RMSE
(a non-converged fit's parameters aren't trustworthy even if its residual
happens to look small). The chosen model's name and RMSE are shown
directly in the chart's legend, so which one won on a given day's curve is
visible, not asserted.

---

## 6. Visual theme — a deliberate scope expansion, done at the user's request

The phase brief, as originally given, explicitly excluded this: *"Custom
CSS, animation, or visual polish beyond basic Streamlit theming... If you
find yourself building any of the above, stop."* Mid-build, the user asked
for a "futuristic" look and specifically about anime.js — this was
flagged back explicitly (quoting the brief's own line) before any of it
was built, and proceeded only once the user confirmed they wanted to
override that constraint. Recorded here plainly rather than left to look
like scope crept in unnoticed.

**What was added, and why each piece is where it is:**

- **`.streamlit/config.toml`** — a base dark theme (`base = "dark"`,
  cyan `primaryColor`), so there's no flash of Streamlit's default light
  theme before `app.py`'s own CSS loads on top of it.
- **Injected CSS** (`st.markdown(..., unsafe_allow_html=True)`, top of
  `app.py`) — Google Fonts (Space Grotesk for headers, JetBrains Mono for
  numbers) and a CSS `@keyframes` entrance fade for both headers and
  metric cards. Purely cosmetic — nothing in this block reads or writes a
  computed value.

  **Correction (post-Phase 4.7, before any deploy):** this originally
  also included gradient-text headers, glowing bordered metric cards, and
  a gradient button style — a "futuristic" look, per the request above.
  That was superseded once `DESIGN.md` committed this dashboard to an
  information-dense financial-terminal direction instead (Bloomberg/Eikon
  reference point, not a marketed surface): gradients, glow shadows,
  rounded corners, and a second decorative accent color are that spec's
  named anti-patterns, so `app.py`'s CSS was rewritten to drop them. The
  entrance-fade `@keyframes` and the anime.js/motion.dev pieces below were
  untouched by that pass — see `DESIGN.md` for the current, authoritative
  visual rules; this section stays as the historical record of why custom
  CSS/animation exists in this file at all.
- **`_style_fig()`** — applies the same dark theme to every Plotly chart
  (background, gridlines, font, and `hoverlabel=dict(namelength=-1)` —
  see the hover-truncation bug below). Never touches a trace's own data or
  any of the project's *validated* semantic chart colors
  (`COLOR_GAIN`/`COLOR_LOSS`, `NORMAL_COLOR`/`ULTRA_LONG_COLOR`,
  `COUPON_COLOR`/`PRINCIPAL_COLOR`) — those are left exactly as validated
  elsewhere in the project (the dataviz skill's palette checker, per those
  modules' own docs).
- **anime.js** (loaded via `st.iframe()` with a raw HTML string — the one
  place actual `<script>` execution is possible, since `st.markdown`
  strips script tags) — a count-up animation on each metric card's number.
- **motion.dev** (also via `st.iframe()`, same block) — a spring-physics
  "settle" pop (`scale: 0.985 → 1.006 → 1`) on each chart/table as it
  scrolls into view, triggered via `window.parent.IntersectionObserver`
  (the *parent* window's own constructor, not the iframe's).

**Two real bugs surfaced during this work, and what they taught the final
design** — kept here rather than only in chat history, since both
generalize beyond this one dashboard:

1. **Hover-label truncation.** Plotly's own default
   (`hoverlabel.namelength=15`) truncates a trace name with an ellipsis —
   "Par curve (quoted)" rendered as "Par curve (q...". Fixed by setting
   `namelength=-1` in `_style_fig`, applied to every chart.
2. **Metric cards invisible until scroll.** The first version of the
   anime.js block animated each metric card's entrance
   (`opacity: [0,1]`) from *inside* the iframe. A browser throttles or
   pauses `requestAnimationFrame` in an iframe that isn't in the current
   viewport — and since that iframe sits at the very bottom of the page
   (after section 6), on first load it's below the fold. The `opacity:0`
   start was applied synchronously; the rAF-driven interpolation back to
   `1` never got a chance to run until a scroll brought the iframe into
   view. **The fix, and the design rule that came out of it:** the
   entrance fade now lives entirely in the CSS `@keyframes` block instead
   (runs on the compositor thread, not subject to iframe/rAF throttling
   at all) — and the later motion.dev addition deliberately never starts
   an element invisible either (§ above: it animates a *scale* on an
   element that's fully visible via Streamlit's own normal render at
   every point in the animation). **The general rule applied from here
   on: decorative JS may enhance already-visible content, but must never
   be the only thing standing between real content and it being visible.**
   anime.js's count-up still runs via the same (occasionally-throttled)
   iframe rAF loop — its failure mode is safe by construction instead: a
   150ms delay before the counter starts means if it's ever throttled,
   the number simply sits at its correct, Streamlit-rendered value,
   un-animated, never blank or wrong.
3. **A pale green trace color, and a chart background that wasn't
   reliably dark.** Plotly's default color cycle assigned its third color
   (`#00cc96`, a pale green) to the fitted-curve trace — poor contrast on
   a white background. Explicit per-trace colors were set instead
   (`CURVE_PAR_COLOR`/`CURVE_ZERO_COLOR`/`CURVE_FIT_COLOR`, defined
   locally in `app.py` since none of the three carries a semantic meaning
   the project's other validated colors do). That alone didn't fully fix
   it: `_style_fig` originally used `paper_bgcolor="rgba(0,0,0,0)"`
   (transparent), which reveals whatever sits *behind* the chart's own DOM
   node — and that turned out not to reliably be this dashboard's dark
   page background in every rendering context. Fixed by making the
   chart's own background a **solid, opaque** color instead
   (`paper_bgcolor="#0d1420"`, `plot_bgcolor="#121a29"`) — a guarantee,
   not a hope resting on the surrounding page's CSS cascading correctly.

**`[data-testid="..."]` CSS/JS selectors are Streamlit internals, not a
public API.** Every hook this theme relies on (`stMetric`, `stMetricValue`,
`stSidebar`, `stAppViewContainer`, `stPlotlyChart`, `stDataFrame`) could
change in a future Streamlit release. Every use is wrapped so a mismatch
degrades to "less styled," never a crash: the CSS rules simply stop
matching (no error possible from an unmatched CSS selector), and both JS
blocks wrap their logic in `try`/`catch`.

---

## 7. Deployment readiness (Part B)

### 7.1 `requirements.txt` — audited, not guessed

Every top-level import across `app.py`, `models/`, `data/`, and `config/`
(excluding `tests/`, which Streamlit Cloud never runs) was grepped
directly, not assumed from memory:

```
pandas, numpy, requests, matplotlib   (existing analytics — numpy was
                                        previously an undeclared,
                                        transitive-via-pandas dependency;
                                        now listed explicitly, since it's
                                        imported directly and extensively)
streamlit, plotly                      (this phase's dashboard)
```

Verified by installing into a **brand-new virtualenv** from
`requirements.txt` alone (not this dev machine's existing environment,
which has extra packages already on it) and confirming, from that clean
install: `app.py` runs end-to-end with no import errors, `app.py` also
runs end-to-end under a *simulated total network failure with no
pre-existing cache* (§7.3), and the full 320-test suite passes unchanged.

### 7.2 The memory constraint — measured, not assumed

Streamlit Community Cloud's free tier gives roughly 1GB of RAM. The phase
brief's own working assumption was that PCA over the full historical
series would be the most likely thing to strain that — checked directly
rather than taken on faith, using `resource.getrusage(...).ru_maxrss`
peak-RSS measurements in a fresh interpreter:

| Stage | Peak RSS |
| --- | --- |
| After `streamlit`+`plotly`+`pandas`+`numpy`+`matplotlib` imports alone | ~150MB |
| Full pipeline (current curve, PCA, bootstrap, NS/Svensson fits), `lookback_years=2.0` (default, 486×15 history) | ~186MB |
| Same pipeline, `lookback_years=None` (full 52-year history, 13,290×6 — see `docs/phase_4a_documentation.md`'s ragged-tenor policy for why widening the window *drops* tenor columns) | ~185MB |

**The finding: the brief's assumption doesn't hold up, measured directly.**
Widening the PCA lookback from 2 years to the entire available history
changes peak memory by well under 1%. The curve-history matrix itself is
small in absolute terms (at most a few hundred KB of floats) regardless of
how many rows it has; the ~150MB baseline is **library import overhead**
(streamlit, plotly, pandas, numpy, matplotlib), which is fixed and paid
once per process regardless of any lookback choice. `PCA_LOOKBACK_YEARS`
(`app.py`, env var `JGB_DASHBOARD_PCA_LOOKBACK_YEARS`, defaulting to
`models.pca.DEFAULT_PCA_LOOKBACK_YEARS` = 2.0) is kept configurable per
the brief's own instruction — a real, useful escape hatch if a future
change to this pipeline ever does make history size the bottleneck — but
it is not, today, the lever that actually matters for memory.

**What this measurement does not cover:** a single process's peak RSS for
one script run, not Streamlit's own per-session server overhead multiplied
across many concurrent viewers (each browser tab/websocket connection
Streamlit serves adds its own overhead on top of this baseline, and
`st.cache_data`'s in-memory store holds one entry per distinct call
signature ever seen — bounded here since every cached function's
arguments come from a small, fixed set, not from unbounded user input).
Scaling behavior under concurrent load was not tested; ~186MB for a single
session leaves a wide margin under the ~1GB ceiling for this to be a
practical concern at any realistic viewer count for a portfolio/demo app.

### 7.3 Fresh-environment verification

Checked directly, not assumed:

- **No `outputs/` dependency.** `app.py` never calls any of the
  `plot_*`/PNG-saving functions (`plot_ultra_long_profile`,
  `plot_factor_exposure`, `plot_cash_flow_ladder`, etc.) — confirmed by
  grep. Those functions' own `Path(__file__).resolve().parent.parent /
  "outputs"` default paths are never even evaluated by the dashboard.
- **No absolute paths.** Confirmed by grep across `app.py`.
- **No assumption a live fetch succeeds.** Both `load_jgb_curve_with_source`
  and `load_jgb_curve_history` already handle this internally
  (Phase 1 / 4A's live → cache → snapshot fallback) — nothing extra was
  needed in `app.py` for this. Verified directly, not just inherited on
  faith: `requests.get` was mocked to raise `ConnectionError` on every
  call, **with no pre-existing local cache** (simulating a genuinely fresh
  Streamlit Cloud container, not this dev machine's own warm cache) — the
  app still ran end-to-end, correctly landing on the snapshot tier for
  both the current curve and the history, with the source-tier disclosure
  (§2 of the yield curve section) correctly showing `"snapshot"`.
- **Clean install.** §7.1's fresh-virtualenv install, from
  `requirements.txt` alone.

### 7.4 `.streamlit/config.toml`

Covered in §6 above (folded into the same file as the rest of the visual
theme, since both live in the same file and were built together).

---

## 8. What is deliberately deferred to Phase 8 (not built here)

Per the phase brief's explicit scope boundary:

- **Scenario selection and VaR/Expected Shortfall** — Phase 5 doesn't
  exist yet; nothing in this dashboard anticipates it.
- **MOF issue reference lookup or bond autofill** — the portfolio editor
  only edits *weights* on the existing six illustrative bonds; adding or
  looking up a real bond by ISIN is out of scope here, same boundary
  Phase 2A already drew (`docs/phase_2a_documentation.md` §4).
- **JSON export of the dashboard's own output.**
- **Kokonut UI / Bklit UI** (real React component libraries, not CDN
  scripts) — considered when the user asked about them; both would need a
  full custom Streamlit Component (a separate React/TypeScript/Node build
  pipeline wrapped in a Python package), a materially larger scope
  addition than this phase's brief anticipated. Skipped at the user's own
  choice, not attempted.

---

## 9. Known limitations (for the SR 11-7 validation report)

**9.1 Inherits every limitation already named for the analytics modules
it displays.** `app.py` computes nothing new — every caveat named in
Phases 1 through 4.6C (the par-curve simplification, the ragged-tenor
policy, the illustrative-not-real portfolio, the settlement/accrued-
interest assumptions, etc.) applies identically to what's shown here.

**9.2 `prefer_live=True` by default, unlike this project's test suite.**
Every existing test in this project uses `prefer_live=False` for
reproducibility (`docs/phase_1_documentation.md` §5). The dashboard uses
`prefer_live=True` deliberately instead — a live dashboard's whole point
is showing today's data, with the source tier disclosed either way (§1 of
this doc) — but this is a genuine, disclosed divergence from that
convention, not an oversight.

**9.3 Accrued interest is always shown as zero in the portfolio table.**
No settlement-date control was added to the sidebar (not requested by the
phase brief); `settlement_date` therefore defaults to the valuation date,
at which `accrued_interest()` is exactly `0.0` by construction
(`docs/phase_4_6a_documentation.md` §4). Dirty price equals clean price
in this dashboard for that reason, disclosed directly in the table's own
caption.

**9.4 The portfolio table's "Modified duration" column is the
curve-based (effective) duration, not the analytic (YTM-based) one.**
Chosen deliberately for internal consistency with the DV01 column shown
next to it (`dv01_bond`'s own formula multiplies price by *that* duration
measure, `docs/phase_3b_documentation.md`) — but `bond_analytics_portfolio`
also computes a second, YTM-based modified duration that differs slightly
at the long end (`docs/phase_4_6b_documentation.md` §2); a reader wanting
that figure instead needs to look past this table.

**9.5 The visual theme's CSS/JS selectors are Streamlit internals** (§6)
— not a stable public API, and could silently stop matching in a future
Streamlit release. Degrades to "less styled," never a crash, by design —
but worth a validator's awareness that the *visual* presentation isn't
pinned the way the underlying numbers are.

**9.6 The memory measurement (§7.2) is a single-process, single-session
figure**, not a load test. See §7.2's own caveat.

---

## 10. What would change this design

**A real settlement-date control**, if ever added to the sidebar, would
need no change to the underlying calculation — `dirty_price_portfolio`
already accepts `settlement_date` as a parameter (Phase 4.6A); only a new
sidebar widget and passing its value through would be needed.

**A future MOF issue reference module** (Phase 8), once it exists, would
let the portfolio editor add/replace bonds by real ISIN rather than only
edit weights on the existing illustrative six — additive to the current
`load_edited_portfolio` design, not a rewrite of it (it already
round-trips through the real `config/portfolio.json` schema, which
already supports `isin`/`issue_date`/`maturity_date`/`tenor_class`,
Phase 2A §2).

**If Streamlit Community Cloud's memory ceiling is ever genuinely
strained** by something in this pipeline, §7.2's measurement is the
starting point for finding what's actually responsible — not an assumed
culprit (PCA lookback) that measurement here found isn't it.

---

## 11. Relationship to the fallback re-anchoring policy

This phase adds no new hardcoded market or portfolio data of its own — it
only reads through the existing loaders. It does add one new *disclosure*
surface for the existing policy: `load_jgb_curve_with_source`'s
`source_tier`/`as_of` fields, shown directly in the dashboard's yield
curve section, mean a viewer can now see for themselves whether they're
looking at live data, a machine-local cache, or the committed snapshot —
and, for the snapshot case specifically, that it's a fixed past date
governed by the same re-anchoring obligation named in
`docs/phase_1_documentation.md` §5. This doesn't change the policy; it
makes compliance with it visible to someone who isn't reading the code.
