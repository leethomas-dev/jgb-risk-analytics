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

The project-wide fallback re-anchoring policy (§5 of the Phase 1 doc) and
the reasoning for why it does or doesn't apply to a given phase's hardcoded
data live in that phase's own doc, not restated here.
