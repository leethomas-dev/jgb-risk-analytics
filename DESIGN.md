---
name: JGB Risk Analytics
description: An information-dense financial-terminal dashboard for JGB interest-rate risk, not a marketed product surface.
colors:
  risk-accent: "#22d3ee"
  bg-base: "#0b0f17"
  bg-panel: "#121a29"
  bg-sidebar: "#0d1420"
  border-neutral: "rgba(255, 255, 255, 0.14)"
  border-neutral-hover: "rgba(255, 255, 255, 0.35)"
  text-primary: "#e6edf3"
  text-muted: "rgba(230, 237, 243, 0.6)"
  data-gain: "#2a78d6"
  data-loss: "#e34948"
  data-normal: "#4C72B0"
  data-ultra-long: "#C44E52"
  data-coupon: "#2a78d6"
  data-principal: "#eb6834"
typography:
  display:
    fontFamily: "Space Grotesk, sans-serif"
    fontWeight: 700
    letterSpacing: "0.01em"
  mono-value:
    fontFamily: "JetBrains Mono, monospace"
    fontWeight: 600
    fontSize: "1.9rem"
  mono-label:
    fontFamily: "JetBrains Mono, monospace"
    fontSize: "0.72rem"
    fontWeight: 400
    letterSpacing: "0.06em"
  mono-body:
    fontFamily: "JetBrains Mono, monospace"
    fontWeight: 400
rounded:
  none: "0px"
spacing:
  card-padding: "0.75rem 1rem 0.5rem 1rem"
  button-padding: "0.5rem 1.25rem"
components:
  metric-card:
    backgroundColor: "{colors.bg-panel}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.none}"
    padding: "{spacing.card-padding}"
  button-primary:
    backgroundColor: "transparent"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.none}"
    padding: "{spacing.button-padding}"
  button-primary-hover:
    backgroundColor: "{colors.border-neutral-hover}"
  alert-error:
    backgroundColor: "{colors.bg-panel}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.none}"
---

# Design System: JGB Risk Analytics

## Overview

**Creative North Star: "The Risk-Desk Screen"**

This dashboard reads as an internal bank risk screen — a Bloomberg
terminal or Refinitiv Eikon panel, not a marketed product. The person
in front of it is a JGB risk-desk analyst or model validator reading
numbers, not a visitor being sold something. Every design decision
follows from that: figures must align and scan in monospace, structure
comes from borders and density rather than whitespace and shadow, and
the one accent color in the system is spent exclusively on signaling
risk (a validation error, eventually a concentration flag) — never on
decoration.

The system was corrected from an earlier direction that used dual-tone
gradients, glow shadows, and rounded cards — the visual vocabulary of a
generated SaaS dashboard. Those tells are explicitly rejected here, not
merely avoided by omission: gradients, glassmorphism, shadows-as-default,
and a second decorative accent color are named anti-patterns for this
project, not just unused options.

**Key Characteristics:**
- Monospace numerics everywhere a figure appears, so columns and card
  values stay scannable at a glance.
- One accent color, spent only on risk signals (validation errors today;
  concentration flags once that logic exists) — never on headings,
  buttons, or default chrome.
- Borders separate surfaces; shadows do not.
- Flat, minimal-to-no corner rounding.
- Density over whitespace: no hero sections, no generous card gaps.

## Colors

Two families: a near-monochrome dark UI palette plus a single reserved
accent, and a separate set of semantic **data colors** used only inside
charts (Plotly/matplotlib traces) to encode gain/loss, normal-vs-ultra-long,
and coupon-vs-principal. The data colors are not decorative UI accents and
are exempt from the one-accent rule below — they're a validated categorical
encoding, already checked against the project's own dataviz palette
standard.

### Primary
- **Risk Accent** (`#22d3ee`): the system's only accent. Reserved for
  risk-signal contexts — currently the portfolio-validation error banner
  (`st.error`) — and never applied to headings, buttons, metric values, or
  any other default-state chrome.

### Neutral
- **Base** (`#0b0f17`): the app's page background. Flat — no radial or
  linear gradient.
- **Panel** (`#121a29`): metric cards and chart backgrounds.
- **Sidebar** (`#0d1420`): the sidebar background. Flat — no gradient.
- **Border** (`rgba(255, 255, 255, 0.14)`): the only separator device —
  used on card, sidebar, and button edges. Brightens to
  `rgba(255, 255, 255, 0.35)` on hover as the sole interactive cue.
- **Text** (`#e6edf3`): primary text and metric values.
- **Text — Muted** (`rgba(230, 237, 243, 0.6)`): metric labels and
  captions, set apart from values by both weight and opacity, not color.

### Data (chart-only, not UI accent)
- **Gain** (`#2a78d6`) / **Loss** (`#e34948`) — `models/factor_exposure.py`.
- **Normal** (`#4C72B0`) / **Ultra-Long** (`#C44E52`) —
  `models/ultra_long_profile.py`.
- **Coupon** (`#2a78d6`) / **Principal** (`#eb6834`) —
  `models/cash_flow_ladder.py`.
- **Curve — Quoted** (`#e6edf3`, neutral) / **Curve — Bootstrapped Zero**
  (`#a78bfa`) / **Curve — Fitted** (`#f59e0b`) — `app.py`'s yield-curve
  chart, three lines with no gain/loss-style semantic meaning, just
  mutual distinction. The quoted curve deliberately uses the neutral text
  color rather than the reserved accent (`#22d3ee`) — see the Reserved
  Accent Rule below.

### Named Rules
**The Reserved Accent Rule.** The risk accent appears in exactly one
family of contexts — something is wrong or something is concentrated —
and nowhere else. A screen with the accent on a heading, a button, or a
default-state metric is a bug in this system, not a variant of it.

**The One Voice, Many Data Colors Rule.** The one-accent rule governs UI
chrome only. Chart series keep their own validated semantic palette
(gain/loss, normal/ultra-long, coupon/principal) — those colors encode
data, not brand, and are unaffected by the Reserved Accent Rule.

## Typography

**Display Font:** Space Grotesk (with sans-serif fallback) — page and
section headings (`h1`/`h2`/`h3`) only.
**Body/Mono Font:** JetBrains Mono (with monospace fallback) — every
number, metric value, metric label, chart label, and button.

**Character:** A plain geometric sans marks structure (section titles);
everything a reader might need to compare, scan, or align falls back to
monospace. Streamlit's own base theme (`.streamlit/config.toml`,
`font = "monospace"`) already makes monospace the page default; the
Google Fonts import pins the exact face (JetBrains Mono) rather than
leaving it to the browser's generic monospace.

### Hierarchy
- **Headline** (700, default `h2`/`h3` size, tight tracking): section
  titles ("1. Headline risk metrics", etc.) — the only sans-serif text in
  the system.
- **Metric Value** (600, JetBrains Mono, 1.9rem — trimmed down from
  Streamlit's larger default for a denser headline-metrics row): the
  number itself — the thing being read.
- **Metric Label** (400, JetBrains Mono, 0.72rem, 0.06em tracking,
  uppercase, muted): the unit/label under a value. Differs from the value
  in weight, size, and color simultaneously, so the two never get
  confused even at a glance.

### Named Rules
**The Numeric Monospace Rule.** Any element that displays a figure — a
metric, a table cell, a chart axis or hover label — renders in JetBrains
Mono. Space Grotesk is for section titles only; it never touches a
number.

## Layout

Single-column main panel with a fixed sidebar (Streamlit's native
layout="wide" shell), numbered sections (1–6) rather than a dashboard
grid of equal-weight cards — the ordering mirrors the six analytics
phases that feed the page. Density comes from the absence of decorative
chrome (no hero section, no marketing copy, no illustration), not from a
tightened grid: existing Streamlit spacing defaults are kept, but nothing
is added that would justify more of it.

## Elevation & Depth

Flat. No shadows anywhere in the system, including on hover — surfaces
are separated by a 1px border, full stop. The one exception is the
portfolio-validation error banner, whose border switches to the reserved
risk accent; that's a color change signaling state, not an elevation
change.

### Named Rules
**The Border-Not-Shadow Rule.** Every surface boundary in this system —
metric card, sidebar, button, alert — is a 1px border. `box-shadow`
does not appear anywhere in `app.py`'s injected CSS.

## Shapes

Square corners throughout (`border-radius: 0`) on metric cards, buttons,
and alerts. No rounding anywhere in the custom CSS layer — a deliberate
break from the earlier 12px-radius cards, chosen to read as terminal
panel dividers rather than app "cards."

## Components

### Buttons
- **Shape:** square (`border-radius: 0`).
- **Default:** transparent background, `{colors.text-primary}` text, 1px
  `{colors.border-neutral}` border, JetBrains Mono, weight 600.
- **Hover:** border and a faint background wash brighten to
  `{colors.border-neutral-hover}` / `rgba(255,255,255,0.04)` — a neutral
  interactive cue, not the reserved accent (the accent is not spent on
  routine interaction).

### Cards (Metric Panels)
- **Corner Style:** square (`border-radius: 0`).
- **Background:** `{colors.bg-panel}`.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px `{colors.border-neutral}`, the only separation device.
- **Internal Padding:** `0.75rem 1rem 0.5rem 1rem` — tightened from the
  previous `1rem 1rem 0.7rem 1rem` for density.
- **Value/Label Hierarchy:** value in Metric Value type, label in Metric
  Label type (see Typography) — weight, size, and color all differ, per
  the project's own hierarchy rule.

### Alerts
- **Style:** `{colors.bg-panel}` background, square corners, 1px border
  in the reserved risk accent (`#22d3ee`) — the only place in the system
  that color is allowed to appear. Currently used for the
  portfolio-weight validation error (`st.error`, "weights must sum to
  100%").

### Data Editor (Sidebar Portfolio Table)
- **Style:** Streamlit's native `st.data_editor`, inheriting the base
  monospace theme; no custom CSS layered on it. Disabled columns (Bond,
  Maturity, Coupon) render read-only; only Weight % is editable.
  Terminal-appropriate as-is — an editable grid of aligned figures needs
  no further styling to fit this system.
- **Editability hint:** folded into the "must total 100%" caption below
  the table (one line, doing two jobs) rather than a separate sentence
  above it.

### Navigation
- No top nav; the sidebar (`{colors.bg-sidebar}` background, 1px
  `{colors.border-neutral}` right border) carries the project title, a
  GitHub link, and the portfolio weight editor. Numbered `h2` section
  headers serve as in-page navigation for the single-column main panel.

## Do's and Don'ts

### Do:
- **Do** render every number — metric, table cell, chart label — in
  JetBrains Mono.
- **Do** separate surfaces with a 1px `{colors.border-neutral}` border.
- **Do** keep the risk accent to risk-signal contexts only (currently:
  the validation error banner). When a concentration threshold check is
  added later, that is the next legitimate use of this color — not a new
  accent.
- **Do** keep chart series on their existing validated semantic palette
  (gain/loss, normal/ultra-long, coupon/principal); that system is
  independent of UI chrome and out of scope for this file's rules.

### Don't:
- **Don't** use a gradient anywhere in the UI chrome — not on text, not
  on buttons, not on a background, regardless of hue pairing.
- **Don't** add a `box-shadow`, glow, or glass/blur effect to any
  element.
- **Don't** round a corner — metric cards, buttons, and alerts stay
  square.
- **Don't** introduce a second decorative accent color. One accent
  exists, and it is reserved (see The Reserved Accent Rule).
- **Don't** add emoji or decorative iconography to a heading or label.
- **Don't** center a hero section or add marketing copy — this is an
  instrument panel, not a landing page.
