# Phase 2A Documentation — `config/portfolio.json` + `config/portfolio_loader.py`

## In plain English

This part defines the group of bonds being studied: six example Japanese
government bonds with different repayment lengths (2, 5, 10, 20, 30, and
40 years), different interest rates, and different-sized shares of the
overall holding. These are realistic, made-up examples for demonstrating
the tools in this project — not a real investor's actual holdings, and
the documentation says so plainly wherever this data appears. Every
calculation elsewhere in the project reads this same list, rather than
each part inventing its own, so there's exactly one place to change which
bonds are being studied.

---

## Technical details

`load_portfolio()` is the single source of truth for the project's bond
list — every phase reads the portfolio through it rather than hardcoding
one.

**Files:** `config/portfolio.json` (the data — six named bonds, plus a
disclaimer and schema notes) and `config/portfolio_loader.py` (`Bond`, a
frozen dataclass; `load_portfolio()`, the public entry point;
`_resolve_maturity()` / `_validate_portfolio()`, the resolution and
validation logic; `_parse_date()` / `_parse_japanese_era_date()`, the date
parsing described in §2).

**Output contract:** `load_portfolio(path=None, valuation_date=None)`
returns `list[Bond]`. Each `Bond` has `name`, `maturity_years` (always
resolved to a number — see §2), `coupon_rate` (decimal), `face_value`,
`weight` (all bonds' weights sum to 1.0), `freq` (coupon payments per
year, always resolved to a positive int — defaults to 2/semiannual when a
bond's entry doesn't specify one — see §2.1), plus four optional fields
defaulting to `None`: `isin`, `issue_date`, `maturity_date`, `tenor_class`.
`Bond` is immutable — edit the config file and reload rather than
modifying a loaded `Bond`.

---

## 1. What each piece does, and why

### 1.1 `config/portfolio.json`

Six bonds (2Y–40Y), coupons from 1.0% to 3.8%, face value 100 throughout,
weights 15/20/25/15/15/10% (summing to 100%). None of the six use the
optional real-bond fields (§2) — maturity is given directly as a number of
years.

**Why:** each bond is a readable, named object rather than a bare list of
numbers, so the file is self-explanatory and hand-editable without
consulting other documentation — important later if a dashboard lets
someone edit it directly. `face_value = 100` for every bond is a
normalization, not a real position size: it keeps every bond's price
directly comparable and keeps the weighted-portfolio math simple.

### 1.2 `Bond` (frozen)

A typed, immutable record — chosen over a plain dictionary so every
consumer gets named-attribute access (`bond.coupon_rate`), and immutable
so nothing downstream (which reprices repeatedly against a changing
curve) can accidentally edit a bond instead of the curve. The four
optional fields exist for a future version of this project using real
bond data (§2); every consumer already treats them as possibly absent.

### 1.3 `load_portfolio(path=None, valuation_date=None)`

Reads the JSON, resolves each bond's maturity (§2), and validates the
whole list. A missing or malformed required field raises an error naming
the file and the specific bond; a missing file raises a standard
file-not-found error.

**Why `path` and `valuation_date` are parameters, not hardcoded:** a
caller that needs a different portfolio (a test, or eventually a
dashboard) passes it explicitly at the call site — the same reasoning
behind `load_jgb_curve()`'s `prefer_live` flag. Unlike the curve loader,
this function *raises* on a bad input rather than falling back to
something usable — a broken portfolio config is a genuine error, not
degraded-but-usable market data, so there's no fallback tier to drop to.

### 1.4 `_validate_portfolio`

Checks: no duplicate names, every face value and maturity positive, every
coupon inside a plausible band (0–20%, wide enough for any real JGB, tight
enough to catch a rate mistakenly left in percent form), every weight
non-negative and summing to 1.0 (within a small floating-point tolerance),
and that any optional dates present are valid and consistent.

---

## 2. Built to accept real bonds later (not used yet)

A later version of this project may replace the illustrative bonds with
real ones — real ISINs, real coupons, real maturity dates. The schema
already supports that without any code changes:

- **`isin` / `issue_date` / `maturity_date` / `tenor_class`** are all
  optional and unused today. Their absence is normal, not degraded input.
- **A bond's maturity can be given either as a plain number of years, or
  as an actual maturity date** — if a date is given, the loader computes
  years-remaining from it automatically (and if *both* are given, it
  checks they roughly agree, raising an error if they don't — silently
  picking one would hide a real data mistake).
- **`valuation_date`** (defaulting to today) is what "years remaining" is
  measured from — pinning it explicitly makes a validation run
  reproducible on any date, the same reason `load_jgb_curve()`'s
  `prefer_live=False` exists.
- **The date math is a simple approximation** (calendar days ÷ 365.25),
  not an exact bond-market day-count convention. That's deliberate: the
  only thing this number feeds into is looking a maturity up against a
  curve of at most 15 discrete points — a day-count-exact answer would be
  more precise than anything downstream can actually use. A real
  settlement/accrued-interest calculation, if ever needed, is separate,
  unbuilt work (§4).
- **`issue_date` and `maturity_date` accept more than plain ISO
  `YYYY-MM-DD`.** The loader also recognizes a set of alternative formats
  that are unambiguous on their own — `YYYY/MM/DD`, a spelled-out month
  (`04-Mar-2025`), Japanese numeric dates (`2025年3月4日`, including
  fullwidth digits), and Japanese era dates (`令和7年3月4日`,
  `平成元年1月8日` — covering Meiji through Reiwa, with the era's own
  first year written `元年`). Whatever format is given, the stored value
  is always normalized to ISO, so every downstream consumer keeps reading
  plain `YYYY-MM-DD`. **Deliberately not accepted:** numeric `DD/MM/YYYY`
  or `MM/DD/YYYY` — for a day ≤ 12 these are ambiguous with each other
  (`03/04/2025` — March 4th or April 3rd?), and guessing wrong would
  silently corrupt `maturity_years` rather than raise an error, so the
  loader rejects them and asks for an unambiguous form instead.

### 2.1 `freq` — unlike the fields above, this one is already wired through

Every other field in this section is schema-ready but unused today. `freq`
(coupon payments per year) is different: every pricing function in this
project — `price_bond`/`price_portfolio`, Key Rate Duration, DV01, the
cash flow ladder, bond analytics — reads **each bond's own** `bond.freq`
rather than one frequency shared by the whole portfolio. A portfolio can
mix payment frequencies (e.g. an annual-pay bond alongside semiannual
JGBs) and each bond prices correctly at its own.

- **Defaults to 2 (semiannual)** when a bond's config entry doesn't
  specify one — matching the JGB market standard, and matching this
  project's illustrative portfolio, which never sets it explicitly.
- **Every portfolio-level function still accepts an explicit `freq`
  override** (`price_portfolio(portfolio, curve, freq=2)`, etc.) that
  forces every bond in the portfolio to that one shared frequency instead
  of each bond's own. This exists because at least one consumer *needs*
  it: `models/zero_curve_impact.py`'s `compute_par_vs_zero_impact`
  requires every bond to be discounted at the *same* frequency the zero
  curve it's comparing against was itself bootstrapped with — per-bond
  freq would be actively wrong there, not just unused (see that module's
  own docstring).
- **Validated the same way `coupon_rate` is:** non-positive or above a
  plausibility ceiling (12, monthly) both raise, catching a data-entry
  mistake rather than silently accepting an implausible frequency.

---

## 3. Known limitations (for the SR 11-7 validation report)

**3.1 Not a real portfolio.** No bond corresponds to a real outstanding
JGB — no ISINs, no real coupon schedules, and the weights aren't derived
from any real fund's holdings. Flagged in the config file's own
disclaimer and the loader's docstring, so the limitation travels with the
data.

**3.2 One fixed allocation, not scenario-driven.** There's only one
portfolio file. The `path` parameter already supports pointing at an
alternative one; a future dashboard is expected to use exactly that hook.

**3.3 No settlement date or accrued interest.** `maturity_years` is the
only timing information passed to pricing — `issue_date`, even when
present, isn't read by anything. In scope for a future phase, not this
one.

**3.4 The date math is an approximation by design**, not a missed detail
— see §2. It only activates once a real `maturity_date` is supplied; the
shipped illustrative portfolio never exercises it.

---

## 4. What would change this design

**Substituting real bonds** needs no loader change — the schema already
accepts real fields (§2). What's missing is a real data source (pulling
actual outstanding JGB terms from MOF) and a way to build a portfolio from
them, both unbuilt and out of scope here.

**Multiple/scenario portfolios** would use the same `path` parameter this
loader already exposes — no rework anticipated.

---

## 5. Relationship to the fallback re-anchoring policy

Phase 1's policy (its doc, §5) — refresh any fallback data before
external use — **doesn't apply the same way here**. The curve snapshot is
a dated observation of something that moves daily; refreshing it to a
newer date is well-defined. The portfolio's coupons and weights aren't an
observation of anything — they were chosen once, illustratively, and
there's no "fresher" version to re-anchor to. What this file actually
owes is a **one-time upgrade** (real ISINs and real coupons, §4), not a
periodic refresh — tracked in the same disclaimer and docstring.
