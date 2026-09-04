# Phase 3C Documentation — `models/ultra_long_profile.py`

## In plain English

This part answers one specific question, in both percentage and currency
terms: "how much of our interest-rate risk sits in bonds that still have
20 years or more left before they're paid back?" That question matters a
lot for Japanese government bonds specifically, because the very-long-end
of the market (20 to 40 years) has been a genuine, widely-discussed area
of investor attention — large institutions like insurance companies and
pension funds have been buying heavily at that end, for reasons tied to
their own long-dated obligations, and that buying pressure shows up as
real, distinct price behavior in that segment. Knowing your _overall_
interest-rate risk isn't enough for a portfolio like this — you need to
know _where_ it's concentrated. This part draws a clear line at 20 years,
adds up how much risk sits on each side of that line, and produces a chart
so the split is visible at a glance, not just a table of numbers.

---

## Technical details

The Japan-specific angle of this project: a focused view on the
ultra-long (20Y+) segment of the curve. Ultra-long JGB demand — insurers
and pension funds extending duration at the long end, against thin
issuance — has been a persistent, real market theme, and the illustrative
portfolio (Phase 2A) deliberately puts ~40% of its weight in the
20Y/30Y/40Y bonds specifically so this metric has something real to show.
Reads the portfolio via `config.portfolio_loader.load_portfolio()`
(Phase 2A) and the curve via `data.jgb_curve_loader.load_jgb_curve()`
(Phase 1); computes no new sensitivity of its own — it splits the
`portfolio_total` rows Phase 3A's `key_rate_duration_portfolio()` and
Phase 3B's `dv01_by_tenor_portfolio()` already produce.

**File in this part:** `models/ultra_long_profile.py` — `UltraLongProfile`
(a frozen dataclass), `compute_ultra_long_profile()` (builds the profile),
`plot_ultra_long_profile()` (saves the chart), `__main__` (loads the real
portfolio and curve, prints the interpretive summary, saves the chart).

**Output contract:** `compute_ultra_long_profile(portfolio, curve,
threshold_years=20.0, freq=2, bump_size=0.0001) -> UltraLongProfile`, a
frozen dataclass with `threshold_years`, `tenors`, `ultra_long_tenors`,
`krd_by_tenor` / `dv01_by_tenor` (the portfolio-level Series), `krd_total`
/ `dv01_total`, `krd_ultra_long` / `dv01_ultra_long`, and the derived
properties `krd_ultra_long_share` / `dv01_ultra_long_share` (fractions,
0 to 1). `plot_ultra_long_profile(profile, output_path=..., metric="dv01")
-> Path`, saving a PNG and returning the path it wrote to.

---

## 1. What each piece does, and why it was built that way

### 1.1 The threshold decision: 20Y, derived from the runtime grid, not a hardcoded list

**What it does**

`DEFAULT_ULTRA_LONG_THRESHOLD_YEARS = 20.0`, a single module constant.
`compute_ultra_long_profile` applies it as `tenors >= threshold_years`
against whatever tenor grid `curve` actually contains at call time, and
records the resulting set as `ultra_long_tenors` on the returned profile.

**Why it was built this way**

- **20Y is both the Part C requirements' own example and the conventional
  cutoff in JGB market commentary** for "ultra-long" as distinct from
  merely "long" (which usually means 10Y+) — the point at which
  insurer/pension duration-extension demand and BOJ purchase technicals
  are most commonly discussed as their own segment. It also matches the
  illustrative portfolio's own design: its 20Y/30Y/40Y bonds are exactly
  the ones meant to populate this segment (Phase 2A §1.1 — "The curve
  extends to 40Y specifically to support the Ultra-Long Duration Profile
  metric planned for Phase 3").
- **A threshold applied fresh to the runtime grid, never a hardcoded
  tenor list.** This is the Part C requirement stated explicitly, and it
  is the same non-fixed-tenor-grid contract every other module in this
  project follows (Phase 1 §4.5, Phase 2B §1.1, Phase 3A §3.2, Phase 3B
  §3.2). Concretely, the two real grid shapes this project's curve loader
  can return classify _differently_: the Phase 1 embedded snapshot's grid
  (1M…40Y, 12 points) puts `{20, 30, 40}` at or beyond the threshold; the
  live/cache grid (1Y…40Y, 15 points, includes 25Y) puts `{20, 25, 30,
40}`. Both are correct for their own grid — a hardcoded `[20, 25, 30,
40]` would have silently miscounted the snapshot case (including a
  tenor, 25Y, that grid doesn't even have), and a hardcoded `[20, 30, 40]`
  would have silently dropped 25Y the moment the live/cache tier served
  the curve. `test_ultra_long_tenors_on_snapshot_grid` and
  `test_ultra_long_tenors_on_live_shaped_grid` check both cases directly
  against the real tenor sets, not a stand-in.
- **`threshold_years` is a plain parameter with a sensible default**, not
  a hardwired constant with no override — the same "callers who need
  something different pass it explicitly" pattern `prefer_live`,
  `bump_size`, and `path` all follow elsewhere in this project.
  `test_custom_threshold_changes_the_selected_tenors` exercises this.

### 1.2 `UltraLongProfile` (frozen dataclass)

**What it does**

Bundles the whole result of one computation — the threshold used, every
tenor on the curve, which of them counted as ultra-long, the full
per-tenor KRD/DV01 Series, and four scalar totals — plus two derived
`@property` methods for the ultra-long share of each.

**Why it was built this way**

- **Frozen, mirroring `Bond`** (Phase 2A §1.2): a computed profile is a
  snapshot of one portfolio against one curve at one threshold. Treating
  it as immutable rules out a class of bug where code downstream (a
  future dashboard, say) accidentally edits a cached profile instead of
  recomputing it after an input changes.
- **`ultra_long_tenors` is stored explicitly on the result**, not left for
  a caller to re-derive, specifically because — per §1.1 — it is _not_
  knowable from the threshold alone without also knowing which grid was
  used. Printing it directly in `__main__` (§1.5) makes the non-fixed-grid
  behavior visible on every real run, not just inside a test.
- **Two `@property` methods for the shares, not two more stored fields.**
  `krd_ultra_long_share` and `dv01_ultra_long_share` are pure derived
  ratios (`krd_ultra_long / krd_total`, `dv01_ultra_long / dv01_total`) —
  computing them on access rather than storing them keeps the dataclass's
  stored state to exactly the numbers that were actually measured, with
  no risk of a stored ratio silently going stale relative to its own
  numerator/denominator.

### 1.3 `compute_ultra_long_profile(portfolio, curve, threshold_years=20.0, freq=2, bump_size=0.0001)`

**What it does**

Calls `key_rate_duration_portfolio` (Phase 3A) and `dv01_by_tenor_portfolio`
(Phase 3B) exactly once each, takes each one's `portfolio_total` row,
splits its tenor columns at `threshold_years`, and returns the sums on
both sides as an `UltraLongProfile`.

**Why it was built this way**

- **No pricing, bumping, or aggregation logic is reimplemented here.**
  This module computes zero new sensitivities — it is a slice-and-sum
  over numbers Phase 3A and Phase 3B already produced and validated (the
  sum-of-KRDs-vs-effective-duration check, Phase 3A §2; the
  formula-vs-direct-bump DV01 check, Phase 3B §2). Rebuilding either
  calculation a second way here would either duplicate that validation
  work or risk a second implementation quietly drifting from the first —
  the same reasoning `dv01_bond` already applied to reusing
  `effective_duration_bond` instead of re-deriving duration (Phase 3B
  §1.2).
- **Operates on the `portfolio_total` row, not a per-bond breakdown.**
  The Part C requirement is specifically "the ultra-long tenor
  contribution to _portfolio_ KRD and DV01" — a portfolio-level question.
  The full per-bond tables remain available from `key_rate_duration_portfolio`
  / `dv01_by_tenor_portfolio` directly for a caller who wants that finer
  view; this module doesn't duplicate them.
- **`krd_total` / `dv01_total` are the sum across every tenor, not the
  bond-level total duration/DV01 computed a third way.** Because they are
  literally `portfolio_total.sum()`, they inherit the same small,
  explained gap Phase 3A §2 and Phase 3B §2 establish for a single bond
  (each carried through the portfolio's own weighted sum) — not a new
  source of imprecision introduced here.

### 1.4 `plot_ultra_long_profile(profile, output_path=..., metric="dv01")`

**What it does**

A `matplotlib` bar chart — one bar per curve tenor, height equal to the
portfolio's DV01 (or KRD, if `metric="krd"`) at that tenor, colored blue
below the threshold and red at or above it, with a legend and a title
naming the threshold. Saves as a PNG to `output_path` (default:
`outputs/ultra_long_dv01_profile.png`) and returns the resolved `Path`.

**Why it was built this way**

- **`matplotlib`, a new dependency for this project** (added to
  `requirements.txt`; nothing before Phase 3C needed to render a chart).
  Used with the `"Agg"` backend explicitly, set before importing
  `pyplot` — this module only ever saves a file, never opens an
  interactive window, so a GUI backend is not just unnecessary but a
  possible source of a crash on a machine with no display (a CI runner, a
  validator's sandboxed environment). Setting `"Agg"` up front makes that
  a deliberate choice rather than whatever backend happens to be
  auto-detected.
- **DV01 chosen as the default metric over KRD.** Part C's own
  requirements name DV01 specifically for the interpretive summary
  ("what fraction of total DV01 comes from beyond 20Y") and frame it as
  "the number a risk committee would actually ask for" — a currency
  figure is the more concrete, directly-actionable one to put on a chart
  a non-technical reader might see. `metric="krd"` produces the identical
  chart shape against the percentage-terms figures instead, for a caller
  who wants that view; the parameter exists rather than shipping two
  separate near-duplicate functions.
- **Color, not a separate subplot, distinguishes the ultra-long
  segment.** The Part C requirement asks for the segment to be "visually
  distinguished" on one chart, not necessarily split into two — a single
  bar chart with a two-color legend answers "where is the risk, and how
  much of it crosses the line" in one glance, which is the actual
  question this metric exists to answer.
- **`output_path` is a parameter with a sensible default, not a hardwired
  path** — the same pattern as `path` on `load_portfolio` (Phase 2A) and
  `CACHE_PATH`/`DEFAULT_PORTFOLIO_PATH`'s own `Path(__file__)`-relative
  resolution. This is what lets the test suite write to a temporary
  directory (`tests/test_ultra_long_profile.py`'s `tmp_path` fixture)
  instead of overwriting the real `outputs/` file on every test run, and
  what would let a future caller (e.g. a Phase 8 dashboard) choose where
  a generated chart lands.
- **`output_path.parent.mkdir(parents=True, exist_ok=True)`** before
  saving, so a caller pointing at a not-yet-existing directory (a fresh
  clone's `outputs/`, or a nested temp path in a test) doesn't hit a
  file-not-found error from `matplotlib` itself.
- **`outputs/*.png` is gitignored** (already covered by this project's
  existing `.gitignore` pattern) — a generated chart is machine- and
  run-specific output, not source, the same reasoning that keeps Phase
  1's curve cache out of version control (Phase 1 §1.4).

### 1.5 `__main__`

**What it does**

Loads the curve and portfolio with real, non-pinned defaults, prints the
curve's tenors and which of them counted as ultra-long, prints the full
per-tenor KRD and DV01 tables, saves the chart, then prints the
interpretive summary: total KRD and DV01, the ultra-long share of each,
and one plain-language sentence naming the DV01 share.

**Why it was built this way**

- **Prints the interpretive summary on every real run**, matching Phase
  3A §1.5 and Phase 3B §1.6's own reasoning: the Part C requirement
  frames this as "the number a risk committee would actually ask for," so
  it belongs in the direct output of running the module, not only
  recoverable by reading a returned dataclass's fields in a REPL.
- **Prints `ultra_long_tenors` explicitly before anything else**, for the
  same reason it's stored on the dataclass (§1.2): the set is grid-
  dependent, and surfacing it up front means a reader sees immediately
  which tenors this particular run classified as ultra-long, rather than
  having to infer it from which chart bars turned red.

---

## 2. The finding: ~40% weight, ~51% risk — and why that gap is real, not a bug

A real run against the live MOF curve and the default illustrative
portfolio (`python -m models.ultra_long_profile`) shows:

```
Total portfolio KRD (sum across all tenors):   10.1970 years
  of which ultra-long (>= 20Y):           5.2086 years  (51.1% of total)
Total portfolio DV01 (sum across all tenors):  0.096267 per 100 face
  of which ultra-long (>= 20Y):         0.049305 per 100 face  (51.2% of total)
```

The portfolio's 20Y/30Y/40Y bonds hold **40%** of the portfolio's
_weight_ (0.15 + 0.15 + 0.10, `config/portfolio.json`) — but they account
for roughly **51%** of its total _risk_. This gap is expected, and worth
being able to explain rather than just report:

**Duration grows with maturity, but weight is allocated by value, not by
risk.** A bond's contribution to total portfolio duration/DV01 is
`weight_i × duration_i` (Phase 3A §1.4's aggregation formula) — so a bond
with the _same_ weight as another, but a _longer_ maturity, contributes
proportionally more risk, because `duration_i` itself is larger. Concretely,
in the illustrative portfolio's own numbers (Phase 3A's `__main__` output):
the 2Y bond's duration is about `1.97`; the 40Y bond's is about `19.44` —
roughly ten times larger, for a bond with only twenty times the maturity
(duration grows with maturity but sub-linearly, since a longer bond's
final, largest cash flow is discounted more heavily). A portfolio that
puts equal _value_ weight in a long bond and a short bond is therefore
never risk-neutral between the two — the long bond dominates the risk
picture far more than its weight alone would suggest. That is exactly
what this section's numbers show, and it is the mechanical reason
"40% of weight" becomes "51% of risk," not a computation quirk specific
to this module.

---

## 3. Known limitations (for the SR 11-7 validation report)

### 3.1 Inherits every caveat already named for KRD and DV01

This module performs no pricing or bump-and-reprice of its own — every
number here passes through unchanged from `key_rate_duration_portfolio`
(Phase 3A) and `dv01_by_tenor_portfolio` (Phase 3B). It therefore inherits
their limitations without adding new ones of the same kind:

- **No zero curve — sensitivities are computed off par yields directly**
  (Phase 2B §3.2, restated for KRD in Phase 3A §3.1 and for DV01 in Phase
  3B §3.1). The ultra-long share reported here is the ultra-long share of
  a _par-yield-approximated_ sensitivity, not a rigorously zero-curve-
  discounted one.
- **The tenor grid is not fixed** (Phase 1 §4.5, restated in Phase 3A
  §3.2 and Phase 3B §3.2) — and this module is the one place in the
  project where that variability directly changes _which tenors count as
  ultra-long_ (§1.1), not merely which columns a table happens to have.
- **The illustrative portfolio's weights and coupons are not real**
  (Phase 2A §3.1) — the 40%-weight/51%-risk finding (§2) is a genuine
  property of this specific illustrative allocation and curve, not a
  claim about any real fund's actual ultra-long exposure.

### 3.2 The 20Y threshold is a convention, not a precise market-structural boundary

Nothing in the JGB market draws an exact, universally-agreed line at
"20.0 years" separating "long" from "ultra-long" — it is a commonly-used
round-number convention in market commentary, not a discontinuity in how
the curve or investor behavior actually works. A bond at 19.9 years and
one at 20.1 years are economically almost identical, but this module's
threshold classifies them on opposite sides. This is inherent to any
threshold-based segmentation and is not something a different threshold
value would fix — it would just move where the same boundary effect
occurs. `threshold_years` is exposed as a parameter specifically so a
reader can re-run the split at a different convention (25Y, for instance)
rather than being locked into one hardcoded choice.

### 3.3 This module's correctness depends entirely on Phase 3A and 3B's own validation

Because `compute_ultra_long_profile` performs no bump-and-reprice
calculation of its own, there is no independent formula-vs-direct-bump
style check to run _within this module_ (Phase 3A §2, Phase 3B §2 already
cover the actual sensitivity math). What this module's own tests check
instead is that the split and the sums are computed correctly _given_
Phase 3A/3B's already-validated numbers — `test_ultra_long_sums_match_independent_recomputation`
recomputes the split independently (a fresh boolean mask over the same
`portfolio_total` row) rather than calling
`compute_ultra_long_profile`'s own logic a second time, so an arithmetic
or indexing bug in this module specifically would still be caught. A bug
in the underlying KRD or DV01 calculation itself would not be caught
here — it would already have been caught (or not) by Phase 3A/3B's own
tests before it ever reaches this module.

---

## 4. What would change this design

### 4.1 A configurable threshold surfaced outside the function signature

`threshold_years` is currently a Python parameter, defaulted from a
module constant — a caller changes it by passing a different value at
the call site. If a future Phase 8 dashboard wanted a user to adjust this
interactively, the change would be additive: read the value from wherever
the dashboard keeps its inputs and pass it through unchanged; no change
to `compute_ultra_long_profile` or `plot_ultra_long_profile` themselves
would be needed, since both already accept it as an ordinary argument.

### 4.2 A real zero-curve bootstrap

Same "swap behind a stable interface" property as every module beneath
this one (Phase 1 §5.1, Phase 2B §5.1, Phase 3A §4.1, Phase 3B §4.1): if
`key_rate_duration_portfolio` and `dv01_by_tenor_portfolio` were ever
computed off a bootstrapped zero curve instead of the par curve, this
module would need no changes at all — it only ever reads their
`portfolio_total` rows, whatever curve produced them.

---

## 5. Relationship to the fallback re-anchoring policy

`models/ultra_long_profile.py` introduces no new hardcoded or fallback
market data — `DEFAULT_ULTRA_LONG_THRESHOLD_YEARS` (20 years) is a
methodology/convention constant, not an observation of anything that
moves, the same category `DEFAULT_BUMP_SIZE` falls into in Phase 3A. The
curve and portfolio it reads both come through the Phase 1 and Phase 2A
loaders. The project-wide re-anchoring policy (Phase 1 doc §6) therefore
has no new instance to govern here, the same conclusion Phase 2B, Phase
3A, and Phase 3B's own docs each reached for their modules.
