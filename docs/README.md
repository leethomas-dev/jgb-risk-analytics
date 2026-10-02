# jgb-risk-analytics documentation

Per-phase design documentation: what each phase built and why, its data
provenance, known limitations (for the SR 11-7 validation report in
`/validation`), and what would change the design later. Code comments cover
_what_ a function does; these docs cover _why_ it was built that way, so a
model validator can review a decision without re-deriving it from the diff.

| Phase | Doc                                                      | Delivers                                                                                                                                  | Status |
| ----- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| 1     | [`phase_1_documentation.md`](phase_1_documentation.md)   | `data/jgb_curve_loader.py` — `load_jgb_curve()`, the par yield curve input for every downstream model                                     | Done   |
| 2A    | [`phase_2a_documentation.md`](phase_2a_documentation.md) | `config/portfolio.json` + `config/portfolio_loader.py` — `load_portfolio()`, the portfolio input for pricing/KRD                          | Done   |
| 2B    | [`phase_2b_documentation.md`](phase_2b_documentation.md) | `models/bond_pricing.py` — `price_bond()` / `price_portfolio()`, curve-based bond pricing                                                 | Done   |
| 3A    | [`phase_3a_documentation.md`](phase_3a_documentation.md) | `models/key_rate_duration.py` — `key_rate_duration_bond()` / `key_rate_duration_portfolio()`, per-tenor Key Rate Duration                 | Done   |
| 3B    | [`phase_3b_documentation.md`](phase_3b_documentation.md) | `models/dv01.py` — `dv01_bond()` / `dv01_by_tenor_bond()` / `dv01_portfolio()` / `dv01_by_tenor_portfolio()`, currency-terms DV01         | Done   |
| 3C    | [`phase_3c_documentation.md`](phase_3c_documentation.md) | `models/ultra_long_profile.py` — `compute_ultra_long_profile()` / `plot_ultra_long_profile()`, the 20Y+ segment's share of portfolio risk | Done   |
| 4A    | [`phase_4a_documentation.md`](phase_4a_documentation.md) | `data/jgb_curve_history_loader.py` — `load_jgb_curve_history()`, a date-indexed time series of JGB curves with an explicit ragged-tenor policy | Done   |
| 4B    | [`phase_4b_documentation.md`](phase_4b_documentation.md) | `models/pca.py` — `compute_curve_pca()`, PCA risk factors (level/slope/curvature) fitted on real JGB curve-change history | Done   |
| 4C    | [`phase_4c_documentation.md`](phase_4c_documentation.md) | `models/factor_exposure.py` — `compute_portfolio_factor_exposure()`, the portfolio's %/currency P&L exposure to each PCA factor | Done   |
| 4.5A  | [`phase_4_5a_documentation.md`](phase_4_5a_documentation.md) | `models/bootstrap.py` — `bootstrap_zero_curve()`, a zero-coupon (spot) discount curve bootstrapped from the observed par curve | Done   |
| 4.5B  | [`phase_4_5b_documentation.md`](phase_4_5b_documentation.md) | `models/curve_fitting.py` — `fit_nelson_siegel()` / `fit_svensson()`, parametric (4- and 6-parameter) fits to the bootstrapped zero curve | Done   |
| 4.5B-DL | [`phase_4_5b_dl_documentation.md`](phase_4_5b_dl_documentation.md) | `models/diebold_li.py` — `fit_diebold_li_history()`, fixed-tau dynamic Nelson-Siegel beta time series, compared against Phase 4B's PCA factors | Done   |
| 4.5C  | [`phase_4_5c_documentation.md`](phase_4_5c_documentation.md) | `models/bond_pricing.py`/`key_rate_duration.py` (extended) + `models/zero_curve_impact.py` — optional zero-curve discounting, its quantified par-vs-zero pricing impact, and NS-vs-PCA loading comparison | Done   |
| 4.6A  | [`phase_4_6a_documentation.md`](phase_4_6a_documentation.md) | `models/day_count.py` (new) + `models/bond_pricing.py` (extended) — `accrued_interest()` / `dirty_price()`, settlement-date-aware clean/dirty pricing on an Actual/365 day count | Done   |
| 4.6B  | [`phase_4_6b_documentation.md`](phase_4_6b_documentation.md) | `models/bond_analytics.py` — `yield_to_maturity()` / `macaulay_duration()` / `modified_duration()` / `convexity()`, cross-checked against Phase 3A's effective duration and a Taylor-approximation reprice test | Done   |
| 4.6C  | [`phase_4_6c_documentation.md`](phase_4_6c_documentation.md) | `models/cash_flow_ladder.py` — `compute_cash_flow_ladder()` / `plot_cash_flow_ladder()`, the portfolio's coupon/principal cash flows by date, nominal and present value | Done   |
| 4.7   | [`phase_4_7_documentation.md`](phase_4_7_documentation.md) | `app.py` — a Streamlit dashboard over the existing analytics modules, plus deployment readiness for Streamlit Community Cloud | Done   |
| 4D    | [`phase_4d_documentation.md`](phase_4d_documentation.md) | `models/pc_scores.py` — `compute_pc_scores()`, daily PCA factor scores; `models/factor_pnl_attribution.py` — `compute_factor_pnl_attribution()`, daily factor P&L attribution with a reported residual | Done   |
| 4E    | [`phase_4e_documentation.md`](phase_4e_documentation.md) | `data/jgb_curve_history_loader.py` (extended) — `_extend_with_recent_rows()`, tops up the curve history with MOF's current-month file, cached locally across month rollovers; `app.py` (extended) — a Refresh market data button | Done   |
| Special A | [`special_phase_a_documentation.md`](special_phase_a_documentation.md) | `models/bond_pricing.py` (`basis`, default `"zero"`) + `models/factor_exposure.py` (`compare_factor_exposure_bases()`) — every price and risk figure discounts on the bootstrapped zero curve (bump par, re-bootstrap, reprice), after the par basis was found to understate slope risk ~45%; par-vs-zero factor monitor | Done   |
| Special B | [`special_phase_b_documentation.md`](special_phase_b_documentation.md) | `models/pca.py` (`describe_level_shape()`), `models/curve_fitting.py` (`leave_one_tenor_out()`), `models/par_error_check.py` + `data/jsda_reference_loader.py` (`measure_par_error()`) — PC1 described from its loadings, not as a parallel shift; out-of-sample NS-vs-Svensson test; par-curve simplification measured against JSDA bond prices | In progress |

Special phases are cross-cutting changes made between numbered phases.
Earlier docs keep the figures that were true when they were written and
point to the special phase that changed them.

## Changes to earlier phases

Commits that revised a phase after it was first built, oldest first. The
phase docs describe each phase as it stands; this list says what changed
later and where it's recorded. `git show <commit>` is the full detail.

| Date | Commit | Phases affected | What changed | Recorded in |
| --- | --- | --- | --- | --- |
| 2026-09-01 | `a17b477` | 1, 2A, 2B, 3A | Plain-English summary added to the top of each doc | Those docs |
| 2026-09-04 | `3ff2dbb` | 1, 2A, 2B, 3A–3C | Docs and docstrings condensed (per its commit message, no intended behaviour change) | — |
| 2026-09-06 | `cc5aa50` | 4A | History loader logs duplicate MOF dates (code and tests only); rejected pairwise/EM ragged-tenor alternatives documented | `phase_4a` §1.3 |
| 2026-09-11 | `5c2fc62` | 4.7 | Dashboard layout and styling; `DESIGN.md` added | `phase_4_7`, `DESIGN.md` |
| 2026-09-11 | `0837d04` | 2A | `issue_date` / `maturity_date` accept unambiguous non-ISO formats (`YYYY/MM/DD`, spelled-out months, Japanese numeric and era dates), stored as ISO; ambiguous `DD/MM` / `MM/DD` rejected | `phase_2a` §2 |
| 2026-09-13 | `7dfbe52` | 2A, 2B, 3A–3C, 4C, 4.5C, 4.6B–C | `Bond.freq` added (default 2); every pricing and risk function prices each bond at its own frequency, with an explicit `freq` override kept (4.5C needs one shared frequency) | `phase_2a` §2.1, `phase_2b` §1.3 |
| 2026-09-27 | `72e438e` | 1 (figures in 3A–3C, 4C, 4.5A–C, 4.6B–C) | Phase 1 snapshot replaced by MOF's 15-tenor curve for 2026-08-31 (the old 12-tenor curve kept as a test fixture); affected doc figures re-baselined in place; Nelson-Siegel now fails to converge on the snapshot | `phase_1` §1.2 |
| 2026-09-27 | `b410761` | 2B–4.7 | **Special Phase A:** zero-curve discounting as the default basis, par-vs-zero factor monitor. Earlier docs keep their par figures plus a header note | `special_phase_a` §5A |
| 2026-10-02 | `5a41358`, `a4b933a` | 4B, 4.5B, 4.7 | **Special Phase B:** PC1 caption generated from its loadings (no longer "a parallel shift"); leave-one-tenor-out NS-vs-Svensson test | `special_phase_b` §1–2 |

The project-wide fallback re-anchoring policy (§5 of the Phase 1 doc) and
the reasoning for why it does or doesn't apply to a given phase's hardcoded
data live in that phase's own doc, not restated here.
