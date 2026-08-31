# Phase 2A Documentation — `config/portfolio.json` + `config/portfolio_loader.py`

Single source of truth for the project's bond portfolio. Every downstream
phase (pricing in `/models`, Key Rate Duration / DV01 / the ultra-long
duration profile in Phase 3, the Phase 8 Streamlit dashboard) reads the
portfolio by calling `load_portfolio()`. Downstream code never hardcodes a
bond list — it gets one table, the same way `/models` never hardcodes a
curve and instead calls `load_jgb_curve()` (see `phase_1_documentation.md`).

**Files in this phase:**

1. `config/portfolio.json` — the portfolio data: six named bonds, each an
   explicit object (not a positional array), plus a `meta` block carrying
   the illustrative-data disclaimer and a schema note.
2. `config/portfolio_loader.py` — `Bond` (a frozen dataclass), `load_portfolio()`
   (public entry point), `_resolve_maturity()` / `_resolve_valuation_date()` /
   `_years_between()` (maturity resolution), `_validate_portfolio()` (the
   validation gate).

**Output contract:** `load_portfolio(path=None, valuation_date=None)` returns
`list[Bond]`, one entry per bond, in file order. Each `Bond` has `name: str`,
`maturity_years: float` (> 0, always resolved — see §2), `coupon_rate: float`
(decimal, e.g. `0.020` for 2.0%), `face_value: float` (> 0), `weight: float`
(>= 0, sums to 1.0 across the portfolio), and four **optional** fields that
default to `None`: `isin: str | None`, `issue_date: str | None` (ISO
`YYYY-MM-DD`), `maturity_date: str | None` (ISO `YYYY-MM-DD`), `tenor_class:
str | None`. `Bond` is frozen — a loaded portfolio is a fixed input for a
given run; edit the config file and reload rather than mutating a `Bond` in
place.

---

## 1. What each piece does, and why it was built that way

### 1.1 `config/portfolio.json` — the portfolio definition

**What it does**

Holds `meta` (a disclaimer, a `schema_notes` pointer, and `last_updated`)
and `bonds`, a list of six objects — 2Y, 5Y, 10Y, 20Y, 30Y, 40Y — each with
`name`, `maturity_years`, `coupon_rate`, `face_value`, `weight`. Coupons run
1.0% at 2Y up to 3.8% at 40Y; face value is 100 throughout; weights are
0.15 / 0.20 / 0.25 / 0.15 / 0.15 / 0.10, summing to 1.00. The curve extends
to 40Y specifically to support the Ultra-Long Duration Profile metric
planned for Phase 3. None of the six entries uses the optional
`isin`/`issue_date`/`maturity_date`/`tenor_class` fields the schema also
accepts (§2) — the illustrative portfolio specifies maturity the simple way,
directly as `maturity_years`.

**Why it was built this way**

- **Named objects, not a bare array of positional values.** A row like
  `[10, 0.02, 100, 0.25]` is unreadable without cross-referencing this
  document or the loader; `{"name": "JGB_10Y", "maturity_years": 10, ...}`
  is self-describing and hand-editable without consulting anything. This
  matters concretely in Phase 8: the dashboard's whole premise is a person
  editing this file's shape interactively.
- **A `meta.disclaimer` field, not just a comment in the loader.** JSON has
  no comment syntax, and the disclaimer needs to travel with the data even
  if someone opens `portfolio.json` on its own without reading the loader.
  The fuller version of the same disclaimer lives in
  `portfolio_loader.py`'s module docstring, so it is visible from either
  file. `meta.schema_notes` does the same job for the optional real-issue
  fields (§2): a human editing this file by hand should be able to discover
  `isin`/`maturity_date`/etc. from the file itself, not just from reading
  loader source.
- **`last_updated` as a sibling field to the disclaimer**, mirroring
  `SNAPSHOT_DATE` sitting next to `SNAPSHOT_CURVE_PCT` in Phase 1 — if this
  file is ever revised (e.g. real ISINs substituted in), there is one place
  the "as of" date lives.
- **`face_value = 100` for every bond.** A conventional normalization; it
  keeps prices directly comparable across bonds as "price per 100 face"
  without a scaling step, and keeps the weighted portfolio math in Part B
  (weight × price) reading as a simple allocation.

---

### 1.2 `Bond` (frozen dataclass)

**What it does**

Five required, typed fields plus four optional fields defaulting to `None`
— all described in the output contract above. `@dataclass(frozen=True)`.

**Why it was built this way**

- **A dataclass, not a plain dict or a DataFrame row.** Every downstream
  consumer wants named-attribute access (`bond.coupon_rate`, not
  `bond["coupon_rate"]` or a positional column lookup), and a dataclass
  gives that with a real `__repr__` for free — useful when a KRD or pricing
  bug needs printing a bond mid-debug.
- **Frozen.** A `Bond` is a validated, fixed input for one run. Making it
  immutable rules out a class of bug where the pricing or KRD code
  (Phase 3, which reprices repeatedly against a bumped curve) accidentally
  mutates a shared `Bond` instead of the curve — the curve is the thing
  meant to move; the bond definitions are not.
- **Not a pandas DataFrame.** The curve loader returns a DataFrame because
  its consumers do vectorized numeric work (interpolation, bumping) over a
  homogeneous `[maturity_years, yield]` grid. A portfolio is a short,
  heterogeneous list of named holdings edited by hand — a list of typed
  objects is the more direct fit, and it costs nothing to `list(...)` into
  a DataFrame later if a specific model wants one.
- **Four optional fields default to `None`, appended after the five
  required ones.** Dataclass fields with defaults must follow those
  without, so the required fields keep their original order and every
  existing positional/keyword construction of a `Bond` still works
  unchanged. Every consumer must treat the four as possibly absent —
  there is no sentinel string like `""` standing in for "not provided,"
  which would be easy to forget to check for.
- **`maturity_years` is always populated, regardless of which form the
  source config used.** Downstream code (Part B pricing, Phase 3 KRD) reads
  `bond.maturity_years` unconditionally; it never needs to branch on
  whether the original entry specified years or a date. `maturity_date`,
  when present, is kept alongside it only for display/audit purposes (e.g.
  a future dashboard showing an actual redemption date), not because
  pricing needs it.

---

### 1.3 `load_portfolio(path=None, valuation_date=None)`

**What it does**

Resolves `path` (or `DEFAULT_PORTFOLIO_PATH = config/portfolio.json` if
`None`) and `valuation_date` (or today, if `None`), reads the JSON, and for
each entry in `bonds`: parses the required fields, calls `_resolve_maturity`
to get `maturity_years` (§2), reads the four optional fields via `.get(...)`
(so their absence is not an error), and builds a `Bond`. Runs
`_validate_portfolio` over the full list and returns it. A missing or
non-numeric required field, or a maturity spec that is missing/inconsistent/
unparseable, raises `ValueError` naming the file and the offending bond's
index; a missing file raises `FileNotFoundError` from the underlying
`open()`.

**Why it was built this way**

- **`path: str | Path | None = None`, not an environment variable or a
  module-level constant a caller reassigns.** Same reasoning as
  `prefer_live` on `load_jgb_curve()`: the callers that need a different
  portfolio — tests, and eventually the Phase 8 dashboard letting a user
  edit the portfolio interactively — pass it explicitly at the call site,
  where the choice is visible in the calling code. This is the hook that
  makes the dashboard possible without touching this module.
- **`valuation_date: str | date | None = None`, the same pattern as
  `path`.** A caller pins it explicitly when reproducibility matters (a
  validation run) and leaves it as today's date otherwise. See §2.3 for why
  this exists at all.
- **`DEFAULT_PORTFOLIO_PATH` derived via `Path(__file__).with_name(...)`**,
  the same pattern `CACHE_PATH` uses in the curve loader — resolves next to
  the module regardless of the caller's working directory.
- **Per-field coercion (`float(raw["coupon_rate"])`, etc.) inside a
  `try/except KeyError` / `except (TypeError, ValueError)`.** Converts a
  malformed config (`"coupon_rate"` missing, or a string that doesn't parse
  as a number) into one `ValueError` that names the file and the bond index,
  rather than a bare `KeyError`/`TypeError` pointing at a line inside the
  loader. A config error should read like a config error.
- **Structural parsing and semantic validation are two separate functions**
  (`load_portfolio` builds `Bond`s; `_validate_portfolio` checks them),
  mirroring the curve loader's split between `_standardize_curve` and
  `_validate_curve_df`. One definition of "a valid portfolio," reusable if
  a second entry point (e.g. a dashboard "save" action) needs to validate
  before writing back to `portfolio.json`. The one exception is maturity
  resolution (§2): it needs `valuation_date`, which `_validate_portfolio`
  does not receive, so it runs in `load_portfolio` via `_resolve_maturity`
  rather than being folded into the validator.
- **Raises rather than returning a sentinel or a partially-built list.**
  A bad weight sum or a negative face value is a modelling-breaking error,
  not a degraded-but-usable case the way a stale curve snapshot is — there
  is no fallback tier for portfolio data, so failing loudly and immediately
  is correct here (contrast with `load_jgb_curve`'s live → cache → snapshot
  chain, which exists precisely because _market data_ has a meaningful
  degraded-but-usable tier and a hand-edited local config does not).

---

### 1.4 `_validate_portfolio(bonds, source)`

**What it does**

Raises `ValueError` unless: the list is non-empty, names are unique, every
`face_value > 0`, every `maturity_years > 0`, every `coupon_rate` is in
`[0, MAX_PLAUSIBLE_COUPON_RATE]`, every `weight >= 0`,
`sum(weight) == 1.0` within `WEIGHT_SUM_TOLERANCE`, every `isin` (if
present) is non-empty, every `issue_date` (if present) parses as an ISO
date, and — if both `issue_date` and `maturity_date` are present — the
issue date is strictly before the maturity date.

**Why it was built this way**

- **`MAX_PLAUSIBLE_COUPON_RATE = 0.20` (20%, decimal).** Directly analogous
  to `MIN_PLAUSIBLE_YIELD` / `MAX_PLAUSIBLE_YIELD` in the curve loader: a
  band wide enough to never reject a real JGB coupon, but tight enough to
  catch the specific, likely mistake of leaving a rate in percent form
  (`2.0` meant as "2%" but read as a 200% coupon) or another unit slip.
  20% was picked as comfortably above any real JGB coupon while still being
  an order of magnitude below a percent-left-undivided value in this
  portfolio's range (coupons here run 1.0%–3.8%, i.e. 0.010–0.038 decimal;
  a percent-unit bug would show up as 1.0–3.8, all well past the ceiling).
- **Weight-sum check runs once, over the whole list, not per bond.** Unlike
  face value, maturity, and coupon — which are properties of one bond in
  isolation — "the portfolio is fully allocated" is only meaningful in
  aggregate, so it is checked once after the per-bond loop rather than
  threaded through it.
- **`WEIGHT_SUM_TOLERANCE = 1e-6`**, not exact equality. JSON numbers
  round-trip through `float`, so `0.15 + 0.20 + 0.25 + 0.15 + 0.15 + 0.10`
  can differ from `1.0` in the last bit. The tolerance absorbs float
  representation error without being loose enough to hide a real
  misallocation (a portfolio off by even 0.1% weight is still caught).
- **Duplicate-name check.** Bond `name` is how a human (and, later, a
  dashboard) refers to a specific holding; a silent duplicate would make
  per-bond output ambiguous ("which `JGB_10Y`?") without any structural
  invalidity that a type check would catch.
- **`isin` gets a non-empty check, nothing more.** Real ISIN format
  validation (12 characters, ISO 6166 check-digit) is deliberately not
  implemented — the issue reference module that will actually populate this
  field is a later phase's job, and it is the natural place to validate the
  format of data it produces. Here, only "not an empty/whitespace string"
  is checked, catching the most likely hand-editing slip.
- **`issue_date` parse-checked here, `maturity_date` parse-checked in
  `_resolve_maturity` (§2), not here.** `maturity_date` must already have
  parsed successfully by the time a `Bond` exists — resolving
  `maturity_years` requires parsing it earlier, in `load_portfolio`.
  Re-parsing it here would be redundant. `issue_date` has no equivalent
  upstream consumer, so this is its only parse check.
- **Validation runs unconditionally on every load, not only for the default
  file.** The alternative-path hook (§1.3) exists so tests and (eventually)
  a dashboard can supply their own portfolio; those are exactly the paths
  most likely to carry a hand-editing mistake, so they get the same gate as
  the shipped default, not a lighter one.

---

## 2. Schema forward-compatibility: real-issue portfolios (later phase)

A later phase adds a JGB issue reference module (real MOF issuance data)
and extends the Phase 8 Streamlit dashboard to let a user build a portfolio
from real outstanding issues instead of illustrative ones. This section
covers the design put in place now, in Phase 2A, so that later phase needs
no migration of the schema or this loader — only the addition of the
reference module and dashboard themselves, which are explicitly **not**
built here.

### 2.1 Optional per-bond fields: `isin`, `issue_date`, `maturity_date`, `tenor_class`

All four are optional, independently of one another, on every bond. Absent
in the illustrative default portfolio; populated for a real-issue one. The
loader treats absence as the normal case, not as degraded input — there is
no warning or log line for a bond that omits them, matching how the curve
loader doesn't warn about a tenor a source simply doesn't provide (see
Phase 1 §4.5 — no fixed grid is assumed there either). `tenor_class` in particular is stored as an opaque label
(`"20Y"`), never used to derive `maturity_years` or bucket a bond
internally — deriving KRD buckets or PCA pillars from a hard-coded tenor
set is exactly the mistake Phase 1's non-fixed-tenor-grid contract exists
to prevent, and this loader does not reintroduce it under a different name.

### 2.2 Maturity as `maturity_years` OR `maturity_date`

Implemented in `_resolve_maturity`. Rules, in order:

1. Neither field present → raise (`ValueError`, "specifies neither").
2. `maturity_date` present, `maturity_years` absent → derive years from the
   date (§2.3) and use that.
3. `maturity_years` present, `maturity_date` absent → use it as given
   (the illustrative-portfolio case, unchanged from before this schema
   extension).
4. Both present → derive years from `maturity_date`, and additionally
   check that value against the stated `maturity_years` within
   `MATURITY_CONSISTENCY_TOLERANCE_YEARS`. Agree → use the derived value.
   Disagree → raise, naming both values and where they came from.

**Why it was built this way**

- **`maturity_date` wins when both are present**, rather than the two
  being averaged or `maturity_years` taking precedence. Once real-issue
  data is in play, an actual redemption date is the more authoritative
  number; `maturity_years` in that case is only useful as a hand-entered
  cross-check, so the derived value — not the cross-check — is what
  `Bond.maturity_years` ends up holding.
- **A consistency check instead of silently preferring one field.** If a
  future config entry hand-edits `maturity_years` without updating
  `maturity_date` (or vice versa) after an issue's terms change, silently
  picking one field would hide the discrepancy. Raising surfaces it at
  load time, before it reaches pricing.
- **`MATURITY_CONSISTENCY_TOLERANCE_YEARS = 0.05`** (about 18 days).
  Loose enough that a hand-rounded `maturity_years` (e.g. `20`) against a
  date-derived value that is never exactly round (e.g. `19.98`) is not
  flagged; tight enough that a real mismatch — a `maturity_date` a full
  year or more off from the stated `maturity_years` — still is. Not
  currently exercised by the shipped default portfolio, since none of its
  six bonds specifies `maturity_date`.
- **The "both present" branch is exercised by both real-portfolio entries
  and by a maintenance mistake, so it had to raise rather than warn.**
  There is no fallback tier for a bad portfolio value (§1.3) — the same
  reasoning that makes `_validate_portfolio` raise rather than warn applies
  here too.

### 2.3 `valuation_date` parameter and day-count convention

`load_portfolio(path=None, valuation_date=None)` accepts an optional
`valuation_date` (a `date`, or an ISO `YYYY-MM-DD` string), defaulting to
`date.today()`. It is the reference point `_resolve_maturity` measures
`maturity_date` against.

`_years_between(start, end)` computes `(end - start).days / DAYS_PER_YEAR`,
with `DAYS_PER_YEAR = 365.25` — a simple ACT/365.25 approximation, not a
bond-market day-count convention (ACT/ACT, 30/360, etc.).

**Why it was built this way**

- **`valuation_date` as an explicit parameter, not "always today."**
  Without it, a portfolio holding real issues would give a different
  `maturity_years` — and therefore a different price — every time it is
  loaded, breaking reproducibility for a pinned SR 11-7 validation run.
  This is the same concern `prefer_live=False` addresses for the curve
  loader (§1.5 of the Phase 1 doc): the callers that need a fixed result
  (tests, validation runs) pass an explicit value at the call site.
- **Accepts both a `date` object and an ISO string.** A caller already
  holding a `datetime.date` (e.g. another part of a validation script)
  passes it directly; a caller reading a date from a config file or CLI
  argument passes the same string format `maturity_date`/`issue_date`
  already use, so one date format is used everywhere in this module.
- **ACT/365.25, not a real bond-market day-count convention (ACT/365 fixed
  — the actual JGB accrual convention — ACT/ACT ICMA, 30/360, etc.), and
  deliberately so.** Those conventions exist to answer a specific
  question: _how much interest has accrued between two coupon dates, to
  the day_ — a real cash amount that must be exact because a real payment
  is settled against it. That is not the question `_resolve_maturity` is
  answering. It answers _roughly how many years from now does this bond's
  final cash flow land_, purely so Part B's curve interpolation has a
  number to look up against `curve.maturity_years`. The curve it looks up
  against has no sub-day resolution of its own — it is 12–15 discrete
  points (Phase 1 §4.5), linearly interpolated — so a day-count convention
  that is exact to the day would be precision the consumer cannot use: the
  interpolation error from the coarse tenor grid dominates any day-count
  choice by orders of magnitude. Put differently, this is not a day-count
  problem in the market sense at all; it only looks like one because both
  involve turning a date span into a number. ACT/365.25 was picked as the
  simplest convention-shaped calculation that turns a date into a plausible
  year count — one line, no calendar-aware leap-year logic needed, because
  365.25 is already the long-run average Gregorian year length — not as a
  stand-in for a market convention this loader chose not to implement.
  Adopting a real convention here would buy no accuracy where it's used
  and would import a modelling choice (which convention, and why) that
  belongs to accrued-interest and settlement mechanics instead, which are
  out of scope for both this loader and Part B pricing (§3.3). Flagged as
  a known approximation in §3.4 rather than presented as exact.

---

## 3. Known limitations (for the SR 11-7 validation report)

### 3.1 Illustrative coupons and weights, not a real portfolio

No bond in the _default_ `portfolio.json` corresponds to a real outstanding
JGB issue — there are no ISINs, no real coupon schedules, and the weights
are not derived from any actual fund's holdings, AUM, or benchmark. The
schema now accepts real-issue fields (§2), but nothing populates them yet;
this remains a limitation of the _shipped data_, not of the loader. A
reviewer should not read portfolio-level dollar or duration figures from
the default file as representative of a real book's exposure.

Mitigant: flagged in `portfolio.json`'s own `meta.disclaimer` and in the
`portfolio_loader.py` module docstring, so the limitation travels with the
data rather than living only in this document. Tracked as a Phase 6
validation-report assumptions-section item.

### 3.2 Weights are a fixed static allocation, not scenario-driven

There is exactly one portfolio file and it represents one fixed allocation.
There is no support yet for multiple named portfolios (e.g. "duration-matched"
vs. "barbell") or for a portfolio that varies by scenario.

Mitigant: the `path` parameter on `load_portfolio()` already supports
pointing at an arbitrary alternative file; Phase 8's dashboard is expected
to use exactly this hook to let a user build and load alternative
portfolios interactively, rather than requiring a code change here.

### 3.3 No settlement-date or accrued-interest modeling

`maturity_years` (however it was resolved — §2) is the only timing
information a `Bond` passes to pricing. `issue_date`, when present, is
stored on the `Bond` but is not read by anything: not by `_resolve_maturity`
(which only looks at `maturity_date`/`maturity_years`), and not by Part B
pricing. There is no day-count convention applied to coupon accrual, and no
settlement-date offset between "today" and the first cash flow a bondholder
would actually receive.

This is in scope for Part B (the pricing engine), not Part A — noted here
only so it is visible from the config layer that no per-bond timing detail
beyond `maturity_years` is available to price against, and that adding
`issue_date` to the schema (§2.1) does not by itself change that.

### 3.4 Maturity-date day count is an approximation, by design, not by oversight

`_years_between` uses ACT/365.25 (§2.3), not any real bond-market day-count
convention (JGBs actually accrue on ACT/365 fixed). This was a deliberate
choice, not a gap: the real conventions exist to price accrued interest
between two coupon dates to the day, which is not what this calculation is
for. It only converts a `maturity_date` into a "years remaining" figure to
look up against a curve that is itself just 12–15 discrete tenor points
(Phase 1 §4.5) — a day-count-exact answer would be more precise than
anything downstream of it can use. For a real-issue portfolio, this means
`maturity_years` derived from `maturity_date` can be off from a
market-convention figure by up to roughly a day per multi-year maturity —
immaterial for curve interpolation and repricing, but a reviewer pricing
against this loader's output should not expect it to match a
day-count-precise vendor calculation to the day, and should not read the
absence of ACT/365 fixed here as an accrued-interest feature that was
missed.

Mitigant: documented here, in `_years_between`'s own comment, and reasoned
through in full in §2.3; the approximation only activates once a real
`maturity_date` is supplied (§3.1) — the shipped illustrative portfolio
does not exercise this path.

---

## 4. What would change this design

### 4.1 Substituting real ISINs

Populating `isin` and `maturity_date` on a real-issue portfolio needs no
loader change — that is the point of §2: the schema and `load_portfolio`
already accept those fields, coerce and validate them, and derive
`maturity_years` from `maturity_date` when it's supplied instead of a
round number. What is **not** built yet, and is explicitly out of scope
for Phase 2A, is the issue reference module that would produce that data
(pulling real outstanding-issue terms from MOF) and the dashboard
workflow for building such a portfolio interactively. When those land, the
change is additive — a new data source feeding the same `portfolio.json`
shape — the same "swap behind a stable interface" property Phase 1's
`load_jgb_curve()` has for a vendor feed (§5.1 of `phase_1_documentation.md`).

### 4.2 Multiple / scenario portfolios (Phase 8)

The dashboard's planned "edit the portfolio interactively" feature needs
exactly the `path` parameter this module already exposes: read the current
file, let a user edit weights/coupons/maturities (or ISIN/date fields, once
real-issue data exists) in a UI, write to a new or temporary path, call
`load_portfolio(that_path, valuation_date=...)` to re-validate and reload.
No rework of the loader is anticipated for this.

---

## 5. Relationship to the Phase 1 fallback re-anchoring policy

`phase_1_documentation.md` §6 states the project-wide policy: any
hardcoded or fallback dataset must be manually re-anchored to a current
value before demo/presentation/interview use, because it is a static
snapshot of something that moves.

**That policy does not apply to `portfolio.json` in the same way, and this
section says so explicitly rather than leaving it ambiguous.** The Phase 1
snapshot curve is a real, dated observation of a market that moves daily —
it decays, and "re-anchor to a fresher print" is a well-defined, repeatable
operation (§5.2, §6 of the Phase 1 doc). The portfolio's coupons and
weights are not an observation of anything that moves; they were chosen
once, illustratively, and there is no "fresher" illustrative coupon to
re-anchor to — running this again next month would not make `JGB_10Y`'s
2.0% coupon more or less correct.

What `portfolio.json` carries instead is a **one-time upgrade obligation,
not a periodic refresh obligation**: replace the illustrative bonds with
real ISINs and their actual coupons (§4.1). That obligation is tracked
in `meta.disclaimer`, in the `portfolio_loader.py` docstring, and — per the
standing instruction that this belongs there — in the Phase 6 validation
report's assumptions section, alongside the Phase 1 known-limitations list.

This is separate from `valuation_date` (§2.3): once a real-issue portfolio
exists, its _derived_ `maturity_years` values genuinely do change with
time in the ordinary way any bond's remaining maturity does, and re-running
`load_portfolio` with a later `valuation_date` is the correct, expected way
to get an up-to-date figure — not a re-anchoring exercise, just the normal
passage of time acting on a real date.
