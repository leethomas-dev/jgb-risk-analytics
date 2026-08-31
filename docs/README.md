# jgb-risk-analytics documentation

Per-phase design documentation: what each phase built and why, its data
provenance, known limitations (for the SR 11-7 validation report in
`/validation`), and what would change the design later. Code comments cover
*what* a function does; these docs cover *why* it was built that way, so a
model validator can review a decision without re-deriving it from the diff.

| Phase | Doc | Delivers | Status |
| ----- | --- | -------- | ------ |
| 1 | [`phase_1_documentation.md`](phase_1_documentation.md) | `data/jgb_curve_loader.py` — `load_jgb_curve()`, the par yield curve input for every downstream model | Done |
| 2A | [`phase_2a_documentation.md`](phase_2a_documentation.md) | `config/portfolio.json` + `config/portfolio_loader.py` — `load_portfolio()`, the portfolio input for pricing/KRD | Done |
| 2B | [`phase_2b_documentation.md`](phase_2b_documentation.md) | `models/bond_pricing.py` — `price_bond()` / `price_portfolio()`, curve-based bond pricing | Done |

The project-wide fallback re-anchoring policy (§6 of the Phase 1 doc) and
the reasoning for why it does or doesn't apply to a given phase's hardcoded
data live in that phase's own doc, not restated here.
