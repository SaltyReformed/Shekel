# UI/UX Polish Audit (2026-07-08)

The cross-page consistency and polish register for the Fable 5 overhaul. Seeded by the developer's
own walkthrough (`ui_ux_observations.md`, 2026-07-08); extended the same evening by a ten-surface
screenshot audit (dashboard, grid, recurring, accounts cockpit, salary + salary edit, retirement +
pensions, all four analytics tabs, settings + MFA, and the loan / investment / cash detail pages as
a sibling-consistency set), each shot in both themes at desktop and mobile via
`tests/manual/shoot.py`, plus a token-layer contrast analysis of `theme-steel-ink.css`.

This document is the source of truth for the polish pass. Every developer observation is
dispositioned in Section 1; systemic root causes in Section 2; per-page findings in Section 3; open
design decisions (developer gates - nothing in Section 4 gets built without a ruling) in Section 4;
proposed build order in Section 5.

**Audit caveat (resolved):** `shoot.py` originally set `data-bs-theme` without dispatching
`shekel:theme-changed`, so Chart.js charts kept dark-theme colors in light-mode captures (confirmed
twice, and confirmed NOT to be an app bug - the real toggle re-themes correctly). Fixed 2026-07-08
(Wave 0): the harness now dispatches the event exactly as `app.js` does and waits out the chart
rebuild. The affected findings were re-shot and re-verified; outcomes are inlined at RC7 / P-DB2.

---

## 1. Disposition of the developer's observations

Every item from `ui_ux_observations.md`, with verdict and where it is handled below.

| # | Observation | Verdict | Handled |
|---|------------|---------|---------|
| O1 | Muted gray text hard to read | CONFIRMED, quantified | RC4 |
| O2 | Some text too small | CONFIRMED, systemic | RC5 |
| O3 | Muted gray buttons don't read as buttons | CONFIRMED (worst: ghost buttons on cards) | D5 |
| O4 | Shekel wordmark needs a unique font | CONFIRMED (plain UI sans today) | D2 |
| O5 | White vs off-white light backgrounds inconsistent | CONFIRMED, root cause found | RC1 |
| O6 | Are breadcrumbs necessary? | Redundant on navbar pages, needed on detail pages | D1 |
| O7 | Dashboard breadcrumb | CONFIRMED (one-item, links nowhere) | D1 |
| O8 | Timeline text cramped | CONFIRMED + a real overlap bug | P-DB1, P-DB6 |
| O9 | Chart/timeline divider unnecessary | CONFIRMED (decorative `.pulse-bracket` flare) | P-DB3 |
| O10 | Grid: no alternating rows, hard to track | CONFIRMED; prior Loop A rejected banding | D3 |
| O11 | Grid: parent/child category invisible | CONFIRMED (child only in hover tooltip) | D4 |
| O12 | Grid: "Projected E..." cut off | CONFIRMED; systemic (~12 labels truncate) | P-GR2 |
| O13 | Grid: square corners | CONFIRMED | P-GR3 |
| O14 | Recurring: columns misaligned across sections | CONFIRMED (three independent tables) | P-RC3 |
| O15 | Recurring: Monthly toggle square corner | CONFIRMED, root cause found | P-RC2 |
| O16 | Recurring: totals blend into headers | CONFIRMED; mostly caused by P-RC1 | P-RC1 |
| O17 | Accounts: too many cards, busy | CONFIRMED (14 equal-weight surfaces) | D6 |
| O18 | Accounts: sections hard to distinguish | CONFIRMED (11px muted labels only) | D6 |
| O19 | Accounts: bar segment without legend | CONFIRMED; NOT a data bug (empty track) | P-AC1 |
| O20 | Salary: raise banner repeats every paycheck | CONFIRMED; service-layer root cause | P-SA1 |
| O21 | Edit Salary Profile not rethemed | CONFIRMED (residue, not wholesale) | P-SA2 |
| O22 | Salary Path chart unlabeled axes | CONFIRMED (deliberate sparkline config) | P-SA4 |
| O23 | Pension Profiles not rethemed | CONFIRMED + a real mobile overflow bug | P-RT1 |
| O24 | Retirement income bar blues too close | CONFIRMED (~1.6:1 in dark) | P-RT2 |
| O25 | Analytics: dead space at top | CONFIRMED (~80px reserved by hidden spinner) | P-AN1 |
| O26 | Analytics: bright blue tabs off-palette | CONFIRMED (`#0D6EFD` both themes) | RC2 |
| O27 | Analytics: tabs vs tab header redundant | CONFIRMED (title appears up to 5 times) | P-AN2 |
| O28 | Analytics: tabs don't read as tabs | CONFIRMED (structural too: URL never changes) | P-AN2 |
| O29 | Calendar: almost-cockpit | CONFIRMED (no hero; scattered month nav) | P-AN3 |
| O30 | Calendar: remove CSV export | Located (routes + service + 2 buttons) | P-AN4 |
| O31 | Spending: not quite cockpit | PARTLY (hero band is cockpit; below is not) | D7 |
| O32 | Where It Went disjointed | CONFIRMED (4 encodings pile into right 30%) | D7 |
| O33 | Top Movers + Where It Went should merge | CONFIRMED (every mover % appears twice) | D7 |
| O34 | Spending: text where a chart would do | CONFIRMED | D7 |
| O35 | Statements: color to distinguish sections | CONFIRMED achromatic today; options mapped | D8 |
| O36 | Taxes: chart with levers? | Assessed; recommend NO | D9 |
| O37 | Settings not rethemed | CONFIRMED (raw Bootstrap throughout) | P-ST* |
| O38 | Settings layout inconsistent | CONFIRMED (bare h5+hr forms, no form_card) | P-ST* |

---

## 2. Root causes (fix once, repair many pages)

### RC1. Light mode has no `.card` skin [bug]

`base.css:155` skins `.card` only under `[data-bs-theme="dark"]`. In light mode Bootstrap's default
`--bs-card-bg: var(--bs-body-bg)` applies, so every default card body renders at `#EFEDE8` - the
page background - while hand-tokened surfaces (hero bands, `.pulse-canvas`) correctly use
`--shekel-surface` `#FBFAF7`. Pixel-verified independently on accounts, settings, MFA, loan, and
investment. This is the mechanism behind O5: light mode's card/page hierarchy is inverted relative
to dark, and cards survive on a 1.34:1 hairline border alone.

**Fix:** add the light-mode `.card` block (surface + border tokens) beside the dark one.

### RC2. Bootstrap compiled literals leak through the token layer [bug]

`theme-steel-ink.css` remaps `--bs-primary`, but Bootstrap 5.3 compiles `#0D6EFD` as a literal into
several component variables, so the remap never reaches them. Confirmed leaks, identical in both
themes:

- Analytics tab pills, active state (`.nav-pills` active bg) - O26.
- Grid mobile This-Period/Plan pill.
- Settings sidebar `.list-group-item.active` (light) and mobile section pills (both themes).
  Companion bug: the dark list-group skin overrides `.active` entirely, so the selected settings
  section is invisible in dark mode.
- Loan "Pay off sooner" pill toggle and `form-range` slider thumb.

**Fix:** skin `.nav-pills` (`--bs-nav-pills-link-active-bg`), `.list-group-item.active`, and
`.form-range` (thumb/track) with tokens in `base.css`, per theme. One shared-component fix repairs
four pages and closes the "two different blues on one screen" class of defect.

### RC3. Raw Bootstrap contextual classes off-palette [consistency]

Steel Ink commits the accent as the only non-money chroma and the money trio to money state only.
Templates still using raw Bootstrap contextual classes break both, and several are unreadable or
misleading:

- `btn-outline-warning` (raw `#FFC107`): recurring archive buttons (40+ per view; the class appears
  in 10 templates), accounts "Payoff Strategies", settings "Log Out All Other Sessions" + ~30
  category archive buttons (roughly 1.4:1 on light paper - illegible). Convention decision at D5.
- `bg-info` / `text-info` cyan: salary pre-tax badges, recurring envelope badges, calendar "3rd
  paycheck" badge, loan allocation bar segment.
- `bg-success` / `text-danger` raw green/red where the Steel trio should be: statements (`#dc3545` /
  `#198754` instead of `--shekel-danger` / `--shekel-done`), loan "4 confirmed" badge, loan
  allocation bar.
- `table-light` thead strips: glaring white bands with black text inside dark cards on salary edit
  (raises + deductions tables) and pension profiles.
- Bootstrap default magenta `code` color (`#D63384`): MFA manual key is the loudest color on an
  achromatic page.

**Fix:** template-by-template replacement with token classes (plus a `code` color override in
`base.css`). Mechanical except the archive-button convention (D5).

### RC4. `--shekel-text-muted` fails AA where it is actually used [bug]

Measured (WCAG 2.x): dark `#757C88` = 4.31:1 on surface, 3.97:1 on surface-raised; light `#6E737D` =
4.07:1 on page bg, 3.99:1 on raised. AA for normal text requires 4.5:1, and muted text is almost
always also the smallest text (captions, chip labels, section micro-labels), so these are the sizes
where the threshold is strictest. This is O1, and auditors flagged it on every page.

**Fix (proposed values, computed to pass on all three backgrounds):** dark `#848B97` (4.87-5.63:1),
light `#63686F` (4.71-5.38:1). Both stay clearly quieter than the secondary tier. Related marginal
case: light accent on the page background is 4.13:1 (links on bare paper); nudging light
`--shekel-accent` to `#25719F` reaches 4.55:1 while staying visually Steel Blue - included in the
token fix unless the developer objects to the brand shift.

### RC5. No type-scale floor [consistency]

Dozens of sub-0.8rem sizes across the stylesheets with extremes of `0.5rem` (8px, analytics),
`0.55rem` and `0.6rem` (grid), `0.6rem` (components), plus a `.fs-mini` utility at `0.65rem`. This
is O2. The grid legitimately runs micro-typography, but 8-10px is below any comfortable floor.

**Fix:** establish a floor of `0.6875rem` (11px) app-wide; sweep everything below it up. The handful
of grid mobile cases below the floor get individually re-judged during the sweep.

### RC6. `shoot.py` does not re-theme charts [tooling bug - FIXED 2026-07-08]

See the audit caveat above. Fixed as Wave 0: `shoot.py` now dispatches `shekel:theme-changed`
(mirroring `app.js`) after setting the theme attributes, and the settle wait covers the
destroy-and-recreate chart rebuild in `chart_theme.js`. Verified by re-shooting the dashboard: the
light capture now renders the light-mode accent line and re-themed ticks.

### RC7. Chart theme defaults incomplete [bug - re-verified 2026-07-08]

`chart_theme.js` `mergeThemeDefaults` themes `ticks` and `grid` but never `scale.border`, and the
light `gridColor` (`rgba(0,0,0,0.08)`) is near-invisible on paper. Post-RC6 re-shoot confirms both
halves, at lower severity than the contaminated capture suggested: light mode draws a solid y-axis
spine (samples near `#4A4E57` - a hard, untokenized line and the only one on the plot) while dark
mode draws none, and light mode shows effectively zero horizontal gridlines while dark mode's are
clear. One sub-symptom dissolved: the "dark dashes between the amber threshold dashes" was mostly
the stale-theme artifact; the threshold line renders uniformly post-fix.

**Fix:** theme `scale.border` explicitly per theme (recommend borderless in both, matching dark) and
raise the light gridline alpha to a visible token-derived value.

---

## 3. Page findings register

Tags: [bug] broken behavior or rendering; [consistency] violates the committed design language;
[polish] worth fixing, low stakes. Root-caused items reference Section 2. Decisions reference
Section 4.

### Dashboard

- **P-DB1 [bug]** "Today" label overlaps the "Anytime this period" row - `.street__today-label` is
  absolutely positioned into the shelf row with no reserved space (`dashboard.css:326`); the amount
  under it is illegible. Both themes, desktop.
- **P-DB2 [bug, re-verified]** Chart axis spine / gridlines - RC7 (confirmed post-fix: spine present
  in light only, gridlines invisible in light only).
- **P-DB3 [polish]** `.pulse-bracket` SVG flare between chart and timeline renders as a skewed gray
  sliver (light mode: a smear that reads as an artifact). Developer already wants it gone (O9):
  remove.
- **P-DB4 [polish]** Timeline station meta is 11px muted for the highest-urgency signal (OVERDUE);
  the left overdue cluster stacks four stations on one dot (O8). Partly resolved by RC4/RC5; the
  cluster layout itself is P-DB6.
- **P-DB5 [decision -> D10]** Position tracks: the big right-aligned figure is the destination (or
  $0), while the real answer sits in a small mid-bar label; also the only uncarded tier on the page.
- **P-DB6 [polish]** Group OVERDUE tag renders under the last item of a multi-item day only, so it
  reads as if only that item is overdue.
- **P-DB7 [decision -> D11]** The dashed amber threshold line is unlabeled, sits on the y-min so it
  reads as chrome, and borrows the credit token for a non-credit meaning.

### Grid

- **P-GR1 [bug - FIXED 2026-07-09]** Sticky month-band header had `opacity: 0.85` on the whole
  element (`grid.css:145`), so scrolled rows ghosted through it legibly (clearest at the sticky
  corner cell in a scrolled-state capture). Both themes. Fixed by dimming the text via `color-mix`
  toward the header bg on a fully opaque cell.
- **P-GR2 [bug - FIXED 2026-07-09]** `table-layout: fixed` + the colspan month-band first row
  defeated every declared column width: all 14 columns split the viewport equally, which (i)
  collapsed the label column - the O12 truncation plus ~10 more truncated labels, and (ii) shipped
  broken 1Y/2Y presets: 27-53 equal columns with NO horizontal scroll, amounts clipped to "$...".
  The most user-visible layout bug of the audit. Fixed with a `<colgroup>` column contract (200px
  label + 150/110/80px periods keyed off grid-wide/medium/compact); wide presets now widen the table
  and scroll horizontally under the sticky label column (verified scrolled: scrollWidth 2280 = 200 +
  26x80 on 1Y, labels pinned, full "Projected End Balance" label).
- **P-GR3 [consistency - FIXED 2026-07-09]** Square corners on the grid wrapper while every
  neighboring surface is rounded (O13). Wrapper now clips at `--bs-border-radius`.
- **P-GR4 [consistency]** Mobile This-Period/Plan pill raw blue - RC2.
- **P-GR5 [polish]** Mobile header states the same period three times (heading, subtitle,
  jump-select).
- Striping and category hierarchy are decisions: D3, D4.

### Recurring

- **P-RC1 [bug]** Dark mode loses the income/expense banner tints entirely: the shared dark
  `.card-header` skin (`base.css:161`, specificity 0,2,0) beats the single-class
  `.recurring-banner--*` rules, so all three banners render neutral raised gray. Light mode is
  correct. This is most of O16. The codebase already documents the dark-scoped two-class fix pattern
  at `components.css:140-145`.
- **P-RC2 [bug]** Monthly toggle square corner (O15): the hidden CSRF input is the first child of
  the `.btn-group`, so Bootstrap's sibling selector strips the first button's left radius
  (`templates/list.html:30-47`). Move the input out of the sibling chain.
- **P-RC3 [consistency]** Income / Expenses / Transfers are three independent auto-layout tables, so
  no column lines up across sections (O14; measured up to 280px drift). Needs a shared column
  contract (fixed widths or a single table grammar).
- **P-RC4 [consistency]** Envelope/companion badges use cyan `bg-info-subtle` and money-green
  `bg-success-subtle` for non-money flags - RC3.
- **P-RC5 [polish]** Light-mode toolbar search/sort interiors sample identical to the page
  background; only a hairline marks them as fields.
- Archive button amber: RC3 / D5.

### Accounts (Net Worth Cockpit)

- **P-AC1 [bug-adjacent]** The "mystery segment" (O19) is the unfilled remainder of the liability
  half of the diverging allocation bar, styled `--shekel-border-subtle` - dark enough to read as a
  fourth data series whose width happens to encode net worth. Presentational fix: make the track
  visibly a track (or label the gap), and make the center tick visible.
- **P-AC2 [consistency]** "Payoff Strategies" is `btn-outline-warning` - RC3.
- **P-AC3 [polish]** Asset vs Retirement bar blues are adjacent same-hue mixes (`rgb(70,147,190)` vs
  `rgb(58,117,151)`) - same genus as P-RT2; fix together (D12 covers the ramp policy).
- **P-AC4 [consistency]** Liability red applied to chip, subtotal, and bar segment but not to the
  loan card balances themselves - one quantity, two treatments on one screen.
- **P-AC5 [consistency]** Savings Goals + Emergency Fund keep pre-cockpit anatomy (legacy
  progress-bar cards, accent-colored money, badge grammar) below the new cockpit cards.
- **P-AC6 [polish]** Property card dead space (no sparkline by design; row-stretch leaves an empty
  middle).
- Busy-ness / weak section separation: D6.

### Salary

- **P-SA1 [bug, service layer]** Raise banner repeats on every paycheck of the raise month (O20):
  `salary_cockpit_service.py:151-153` sets `raise_event` on every period; templates render it
  verbatim. A run-collapsing helper (`_is_raise_run_start`) already exists for the "next raise" chip
  - reuse that seam. OPUS session (financial-logic file).
- **P-SA2 [consistency]** Edit-profile residue (O21): `table-light` thead strips (RC3), `bg-info`
  pre-tax badges + `text-info` icons (RC3), bare `h4` title over a header-less first card
  (stacked-card layout itself is the documented form_card exception - keep).
- **P-SA3 [bug]** Mobile edit page: action row collapses into a cramped jumble (Cancel orphaned
  under Update Profile); row-action delete buttons clip at the viewport edge behind an unindicated
  scroll.
- **P-SA4 [consistency]** Salary Path chart has zero axes by explicit sparkline config while being a
  half-width card whose line IS the answer (O22): add minimal labeled axes (start/end y ticks,
  first/last x dates) rather than full grid - keeps the sparkline feel and satisfies "labeled axes
  when the trend is the answer".
- **P-SA5 [polish]** Cockpit right column dead band between Salary path and Calibrated strip.
- **P-SA6 [polish]** Composition-bar legend lists a "Post-tax" entry whose segment is an invisible
  0.6% sliver (same genus as P-AC1 / P-RT4: legend entries without discernible marks).

### Retirement

- **P-RT1 [bug + consistency]** Pension Profiles page (O23): `table-light` thead (RC3), legacy card
  grammar, left-aligned proportional figures; PLUS a real mobile bug - the table has no responsive
  wrapper, so the thead strip and action buttons render outside the card and force horizontal page
  scroll. The Add form half already uses form_card correctly.
- **P-RT2 [consistency]** Income in Retirement stacked bar: Pension `#2878A8` vs Withdrawals
  `#4A9ECC`, ~1.6:1 in dark (O24). Fix the pair in `retirement.css:17-24`; policy at D12.
- **P-RT3 [consistency]** "none linked" warning uses the credit money token deliberately
  (`retirement.css:95`) - swap to accent or muted+icon per the money-state discipline.
- **P-RT4 [polish]** "Uncovered" legend row has no swatch while its siblings do.
- **P-RT5 [polish]** "Close the Gap" hero says +$7 while the stepper says +$6.71 - rounding puts
  principle 2 on a hair trigger; render both at the same precision.

### Analytics (shell)

- **P-AN1 [bug]** ~80px of permanent dead space: the idle `#tab-spinner` (`py-3`) is hidden with
  `opacity: 0`, so it reserves layout space between the pills and every tab (O25). Hide it without
  reserving space (absolute overlay or `display` toggle honoring `.htmx-request`).
- **P-AN2 [consistency + decision -> D13]** Tab treatment (O26/O27/O28): active pill raw blue (RC2);
  five-deep title stack (navbar + breadcrumb + h4 + pill + tab heading); pills read as buttons; URL
  never changes on switch and direct tab GETs redirect to the shell which auto-loads Calendar.
  Visual fix is RC2 + collapsing the redundant headings; making tabs navigational (push-url) is D13.
- **P-AN7 [polish]** Mobile: pill row clips "Taxes" to "Taxe" with no scroll affordance.

### Analytics - Calendar

- **P-AN3 [consistency]** Almost-cockpit (O29): five equal-weight chips, no hero; month nav is a
  full-width scattered row unlike any other page (and unlike Spending's own picker - three month-nav
  idioms exist inside one Analytics page). Consolidate on one shared picker component; give the
  strip a hero.
- **P-AN4 [approved removal]** CSV export (O30): buttons in `_calendar_month.html:54-56` and
  `_calendar_year.html:18-20`, the `format=csv` branch in `analytics.py:162-187`, and
  `csv_export_service.export_calendar_csv`. Route/service removal = OPUS session.
- **P-AN5 [bug]** Payday cell tint is translucent over the grid-gap backdrop, not the cell surface
  (`analytics.css:95-97`), so light-mode paydays render flat putty-gray and read as disabled. Use an
  opaque `color-mix` with the surface.
- **P-AN6 [decision -> D11]** Trough-day balance renders danger red purely for being the lowest day,
  even at $1,979 against a $500 threshold - the only red figure on the board, screaming "problem"
  where none exists.
- **P-AN8 [polish]** Calendar notation (`~`, `*`, `PAY`) has no on-screen legend; `*` meaning is
  tooltip-only and unreachable on touch.
- **P-AN9 [polish]** Mobile month-nav cramped (cyan badge wedged between title and buttons).

### Analytics - Spending

- **P-AN10 [decision -> D7]** Where It Went + Top Movers restructure (O31-O34): share-bar tracks
  mostly empty (largest fill 40%); four encodings pile into the right 30% with ragged alignment;
  every Top Mover delta already on screen in the list; three text answers to one "what changed"
  question. Redesign fork - Loop A.
- **P-AN11 [polish]** "VS MAY $2,509.82" reads as May's total but is the absolute delta,
  disambiguated only by a muted caption - caption/figure agreement in spirit.

### Analytics - Statements

- **P-AN12 [consistency]** Negative Net Income is raw `text-danger` and "In balance" raw
  `bg-success` instead of the Steel trio (RC3); sibling conventions also disagree (Income Statement
  colors its negative result, Balance Sheet leaves negative Equity lines plain).
- **P-AN13 [decision -> D8]** Section tinting (O35).
- **P-AN14 [polish]** Window label duplicated verbatim under the period select.
- **P-AN15 [polish]** ~1,200px of empty leader between labels and amounts at 1440px - constrain the
  statement body width or the amount column.

### Analytics - Taxes

- **P-AN16 [bug]** Refund/owed money-state coloring is dead in the derivation ledger: Bootstrap's
  `.table>:not(caption)>*>*` color (0,1,1) beats `.tax-refund`/`.tax-owed` (0,1,0), so refund rows
  render plain - and if the estimate flips to owed, totals will NOT go red. Specificity fix in
  `analytics.css:281` region.
- **P-AN17 [polish]** Assumptions card stretched ~500px past content by `h-100` pairing with the
  always-open 7-field checkpoint form; collapse the form behind its summary.
- **P-AN18 [polish]** Two ambiguous captions: hero ends "...modeled after" (reads truncated);
  Effective-rate chip is combined fed+NC but captioned only "of Box 1 wages" next to a
  federal-bracket chip.
- Lever chart: D9 (recommend no).

### Settings (full retheme - the one page-scale rebuild)

- **P-ST1 [bug]** Dark mode: selected sidebar section invisible (RC2 companion; `base.css:213`).
- **P-ST2 [consistency]** Raw `#0D6EFD` active states beside the Steel accent Save button (RC2).
- **P-ST3 [consistency]** No form_card anywhere: General/Security are bare `h5` + `hr` + fields on
  the page background; MFA setup is a card but titles in the body.
- **P-ST4 [bug]** `btn-outline-warning` controls ~1.4:1 on light paper (RC3 / D5).
- **P-ST5 [consistency]** Magenta `code` manual key (RC3).
- **P-ST6 [polish]** "Active Sessions" heading crammed against the 2FA button (missing separator);
  full-width Save Settings button unlike the app's normal-width primaries; empty strength-meter
  track stripe before input; categories page chroma noise (30 gray/yellow/red icon triplets, archive
  vs delete distinguished by color+tooltip only); categories right rail empty below the small Add
  card.

### Detail pages (loan / investment / cash) - sibling consistency

Overall verdict: structurally consistent siblings (identical header pattern, shared
pulse-canvas/chip vocabulary, one ShekelChart grammar). Divergences:

- **P-DT1 [bug]** Loan pay-off lever pill + range slider raw blue in both themes (RC2).
- **P-DT2 [consistency]** Loan Payment Allocation bar + "4 confirmed" badge use raw
  `bg-success/bg-warning/bg-info` (RC3).
- **P-DT3 [consistency]** Investment hero caption inherits `font-mono` (nested inside the mono hero
  div); loan and cash captions are sans - one-line template fix.
- **P-DT4 [consistency]** Retirement marker in `growth_chart.js` borrows the credit token - same
  discipline as P-RT3.
- **P-DT5 [polish]** Casing drift: "Payment Allocation" Title Case; save buttons read "Update
  parameters" / "Update Parameters" / "Save parameters" across the three siblings.
- **P-DT6 [polish]** Investment ends on a half-width row with an empty right column.
- **P-DT7 [polish]** ARM tag inside the rate chip is 10px amber-on-amber-tint (RC5 floor case).
- **P-DT8 [decision -> D14]** Three anchor-recording idioms: investment = click-to-edit hero, loan =
  form card, cash = no on-page way at all.
- **P-DT9 [polish]** Loan's "View full amortization schedule" floats alone at page bottom and reads
  as a footnote, not a control (O3 instance).

---

## 4. Decision register (developer gates - nothing here builds without a ruling)

- **D1. Breadcrumb policy.** Recommend: delete breadcrumbs on navbar-level pages (dashboard, grid,
  recurring, accounts, salary, retirement, analytics, settings - where they restate the active nav
  pill), keep them on sub-pages (detail pages, forms, schedule) where they are the only way back.
  ~24 templates, mechanical once ruled.
- **D2. Brand wordmark font.** Today: coin PNG + plain Inter. CSP requires a vendored woff2 (Inter +
  JetBrains Mono are already vendored, so the pipeline exists). Recommend a short Loop A: 3-4
  candidate faces on the navbar + auth logo-gate mockup.
- **D3. Grid row tracking.** Prior Loop A (grid_audit decision 7) rejected banding in favor of hover
  row-tint; developer still feels the pain reading without the mouse. Options: (a) keep hover only;
  (b) subtle zebra banding; (c) hairline group separators every N rows; (d) sticky row-label hover
  pairing. Recommend revisiting via Loop A mockups on the real grid - this is the app's core surface
  and O10 is a reading-comfort regression the hover tint does not cover.
- **D4. Grid category hierarchy (O11).** Child category exists only in a hover tooltip. Options: (a)
  two-line row label (child in muted subline); (b) indented child rows under parent group rows; (c)
  status quo + a visible affordance. Recommend (a) - preserves density, makes the data visible, no
  structural change.
- **D5. Archive/destructive button convention.** `btn-outline-warning` amber is used for
  archive/deactivate in 10+ templates, is off-palette, and is illegible on light paper. Recommend:
  neutral outline + archive icon for archive/deactivate; `--shekel-danger` outline reserved for true
  deletes; amber never used on controls (credit state only).
- **D6. Accounts cockpit de-busying (O17/O18).** Options: (a) tinted/ruled group banners (the
  recurring vocabulary, neutralized) + demote per-card Transfer buttons to the kebab; (b) collapse
  account cards to dense list rows per group, keeping cards only for the hero and summaries; (c)
  both. Recommend Loop A with (a) and (b) as directions; (a) is the smaller change and directly
  answers O18.
- **D7. Spending tab restructure (O31-O34).** Merge Where It Went + Top Movers into one
  change-focused visual (bar + delta, movers as sort/filter of the same rows), cap the share bars,
  and lead with a chart card per cockpit grammar. Redesign fork - needs Loop A; recommend scheduling
  it as its own screen rebuild after the mechanical waves.
- **D8. Statement section distinction (O35).** Options: (a) tint only the Income Statement with the
  existing income/expense banner tokens, Balance Sheet gets stronger typographic headers; (b) add a
  neutral accent-tinted section-banner variant used by ALL statement sections
  (Assets/Liabilities/Equity get structure-color, not state-color); (c) typography only. Recommend
  (b): honors "money-state colors mean money state" (green/red stay on figures), gives the developer
  the recurring-page feel they asked for, and adds exactly one token pair.
- **D9. Taxes lever chart (O36).** Recommend NO. The tab is a scalar (refund) plus its derivation
  and a calibration input; there is no trajectory to bend, and sliders would fight the tab's
  measured-vs-modeled honesty stance. Cockpit consistency is already carried by the hero band. The
  right future chart is estimate-convergence across saved checkpoints, once checkpoint history
  exists. (Independent auditor conclusion; developer to ratify.)
- **D10. Dashboard position tracks.** The hero-sized figure is the destination (or $0 for debt
  payoff); the actual answer is a small mid-bar label; the tier is the page's only uncarded surface.
  Recommend: make current-progress the big figure, destination the caption, and card the tier like
  its neighbors.
- **D11. Non-money warning color policy.** Two symptoms: the dashboard threshold line borrows credit
  amber and is unlabeled (P-DB7); the calendar trough day borrows danger red for a healthy balance
  (P-AN6). Recommend one rule: money-state tokens ONLY when the state is true (negative,
  over-budget, credit); thresholds/markers use accent or muted with a text label; the trough figure
  colors red only when below the threshold.
- **D12. Chart series-ramp policy (O24 + P-AC3).** Same-hue accent mixes are indistinguishable in
  dark. Recommend: adjacent series must differ by >=3:1 luminance or use a second achromatic
  treatment (pattern/gap + direct labels), applied to the retirement pair and the accounts
  allocation bar together.
- **D13. Analytics tabs as navigation.** Visual fixes (RC2, heading collapse) are decided by the
  design language; the structural half - hx-push-url so each tab is a real URL and direct GETs load
  their own tab - changes route behavior. Recommend yes, in the Opus wave.
- **D14. Anchor-recording idiom on detail pages.** Three siblings, three gestures (P-DT8). Recommend
  standardizing on the investment click-to-edit hero (the most discoverable), added to loan and cash
  in a later pass; not urgent.

---

## 5. Proposed build order

- **Wave 0 - fix the instrument. DONE 2026-07-08.** RC6 fixed (`shoot.py` theme event + rebuild
  wait); P-DB2 / RC7 re-shot and re-verified (see RC7). Bonus finding while unblocking the per-edit
  gate: the hook layer resolved `pylint` from ambient PATH, so a session launched without the venv
  on PATH turned every fail-closed Python gate into a hard block; `_hooklib.sh` now prepends the
  repo venv bin so hooks always run the pinned toolchain.
- **Wave 1 - system fixes (Fable, CSS/tokens; one session; DONE 2026-07-09, commits 7892b433 ->
  c2e95d9d, full suite 7311 green).** Verified by re-shoot: light cards pop (accounts), dark
  settings selection visible, analytics pill on-palette, salary-edit thead strips dark, chart spine
  gone + light gridlines visible. Bonus RC2 member found during verification: checked checkbox/radio
  (#0d6efd literal) - skinned in c2e95d9d.** RC1 light `.card` skin; RC2 component skins (+ P-ST1
  active-state bug); RC4 muted token bump + light accent nudge; RC5 type floor sweep; RC7 chart
  theme completion; RC3 CSS-layer slice: the `table-light` skin, `--bs-code-color`, and the semantic
  `*-subtle` / `*-text-emphasis` / base-hex remaps (alerts, subtle badges). The `*-rgb` vars are
  deliberately NOT remapped: `text-bg-*` compiles `color:#fff !important` tuned to Bootstrap's
  darker hues, so remapping would drop dark success toasts to ~2.5:1. The solid `bg-*` / `text-*`
  usages therefore stay template swaps in their Wave 2 page sessions (P-AN12, P-SA2, P-RC4, P-DT2).
  Every page improves before any page is redesigned.
- **Wave 2 - page bug fixes (Fable, template/page CSS).** Grid P-GR1/P-GR2/P-GR3 (DONE 2026-07-09,
  commits cdd57441 -> a9c0e98c; verified by baseline/after screenshots in both themes plus a
  scripted scrolled-state check; grid route tests 213 green, full suite 7311 green; follow-up the
  same day: removed grid.css's dead sub-768px rules for the desktop-only table - it is d-none below
  md, so .grid-table/.sticky-col/.row-label-col/.grid-wrapper/.txn-cell/.period-btn-group had no
  mobile render path; mobile and desktop shots pixel-identical before/after); grid P-GR5 (mobile
  header period triplication) was assigned to no wave - added here 2026-07-09, still open, needs a
  which-copy-stays call; dashboard P-DB1/P-DB3/P-DB6; recurring P-RC1/P-RC2/P-RC5; analytics
  P-AN1/P-AN5/P-AN7/P-AN14/P-AN16; retirement P-RT1 (incl. mobile overflow)/P-RT4/P-RT5; salary
  P-SA2/P-SA3/P-SA6; detail P-DT3/P-DT5/P-DT7; accounts P-AC1/P-AC4/P-AC6.
- **Wave 3 - service/route work (OPUS session).** P-SA1 raise-event collapse; P-AN4 CSV removal
  root-and-branch; D13 tab push-url if ratified; P-SA4 axis data if it needs route changes.
- **Wave 4 - ruled decisions and screen rebuilds.** D1 breadcrumb sweep; D5 archive convention
  sweep; D8 statement banners; D10/D11/D12 as ruled; settings retheme (P-ST*, a mini Loop B); then
  the Loop A explorations: D2 wordmark, D3 grid tracking, D4 category hierarchy, D6 accounts
  de-busying, D7 spending restructure; P-RC3 recurring column contract.

Model discipline throughout: Fable for visual/template/CSS; Opus for anything in `app/services/`,
`app/routes/`, or test assertions.

**Audit limits:** static screenshots only - hover, focus, and HTMX in-flight states were not
exercised; per-finding scripted checks can cover those during fixes. Sub-pages not shot: remaining
settings sections (pay-periods, tax, account-types, companions), property detail, debt strategy,
transfers/templates forms, auth pages (excluded by decision). The `ui_ux_observations.md` file is
fully superseded by Section 1 of this document.
