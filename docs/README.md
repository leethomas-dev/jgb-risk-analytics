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

The project-wide fallback re-anchoring policy (§5 of the Phase 1 doc) and
the reasoning for why it does or doesn't apply to a given phase's hardcoded
data live in that phase's own doc, not restated here.
