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
| O6 | Are breadcrumbs necessary? | FIXED 2026-07-11 (S10; deleted app-wide, sub-pages get a Back button) | D1 |
| O7 | Dashboard breadcrumb | FIXED 2026-07-11 (S10; deleted, navbar Dashboard pill is the way back) | D1 |
| O8 | Timeline text cramped | CONFIRMED + a real overlap bug | P-DB1, P-DB6 |
| O9 | Chart/timeline divider unnecessary | CONFIRMED (decorative `.pulse-bracket` flare) | P-DB3 |
| O10 | Grid: no alternating rows, hard to track | CONFIRMED; prior Loop A rejected banding | D3 |
| O11 | Grid: parent/child category invisible | CONFIRMED (child only in hover tooltip) | D4 |
| O12 | Grid: "Projected E..." cut off | CONFIRMED; systemic (~12 labels truncate) | P-GR2 |
| O13 | Grid: square corners | CONFIRMED | P-GR3 |
| O14 | Recurring: columns misaligned across sections | CONFIRMED (three independent tables) | P-RC3 |
| O15 | Recurring: Monthly toggle square corner | FIXED 2026-07-10 (S4) | P-RC2 |
| O16 | Recurring: totals blend into headers | FIXED 2026-07-10 (S4, via P-RC1) | P-RC1 |
| O17 | Accounts: too many cards, busy | CONFIRMED (14 equal-weight surfaces) | D6 |
| O18 | Accounts: sections hard to distinguish | CONFIRMED (11px muted labels only) | D6 |
| O19 | Accounts: bar segment without legend | CONFIRMED; NOT a data bug (empty track) | P-AC1 |
| O20 | Salary: raise banner repeats every paycheck | FIXED 2026-07-10 (S3) | P-SA1 |
| O21 | Edit Salary Profile not rethemed | FIXED 2026-07-11 (S7) | P-SA2 |
| O22 | Salary Path chart unlabeled axes | FIXED 2026-07-11 (S7) | P-SA4 |
| O23 | Pension Profiles not rethemed | FIXED 2026-07-11 (S7; incl. the mobile overflow) | P-RT1 |
| O24 | Retirement income bar blues too close | FIXED 2026-07-11 (S7, per ruled D12) | P-RT2 |
| O25 | Analytics: dead space at top | FIXED 2026-07-10 (S5) | P-AN1 |
| O26 | Analytics: bright blue tabs off-palette | CONFIRMED (`#0D6EFD` both themes) | RC2 |
| O27 | Analytics: tabs vs tab header redundant | FIXED 2026-07-10 (S5; h4 dropped) + 2026-07-11 (S10; breadcrumb deleted) | P-AN2 |
| O28 | Analytics: tabs don't read as tabs | FIXED (D13 in S3; visual in Wave 1 RC2) | P-AN2 |
| O29 | Calendar: almost-cockpit | FIXED 2026-07-10 (S5) | P-AN3 |
| O30 | Calendar: remove CSV export | FIXED 2026-07-10 (S3) | P-AN4 |
| O31 | Spending: not quite cockpit | PARTLY (hero band is cockpit; below is not) | D7 |
| O32 | Where It Went disjointed | CONFIRMED (4 encodings pile into right 30%) | D7 |
| O33 | Top Movers + Where It Went should merge | CONFIRMED (every mover % appears twice) | D7 |
| O34 | Spending: text where a chart would do | CONFIRMED | D7 |
| O35 | Statements: color to distinguish sections | CONFIRMED achromatic today; options mapped | D8 |
| O36 | Taxes: chart with levers? | Assessed; recommend NO | D9 |
| O37 | Settings not rethemed | FIXED 2026-07-11 (S11) | P-ST* |
| O38 | Settings layout inconsistent | FIXED 2026-07-11 (S11; form_card throughout) | P-ST* |

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

- **P-DB1 [bug - FIXED 2026-07-09, commit 95a1820a]** "Today" label overlaps the "Anytime this
  period" row - `.street__today-label` is absolutely positioned into the shelf row with no reserved
  space (`dashboard.css:326`); the amount under it is illegible. Both themes, desktop. Fixed by
  reserving the hanging label's lane: a 1.25rem bottom margin on `.street__axis`.
- **P-DB2 [bug, re-verified]** Chart axis spine / gridlines - RC7 (confirmed post-fix: spine present
  in light only, gridlines invisible in light only).
- **P-DB3 [polish - FIXED 2026-07-09, commit ec6d864a]** `.pulse-bracket` SVG flare between chart
  and timeline renders as a skewed gray sliver (light mode: a smear that reads as an artifact).
  Developer already wants it gone (O9): removed; the street now carries the tier gap itself.
- **P-DB4 [polish - resolved 2026-07-09]** Timeline station meta is 11px muted for the
  highest-urgency signal (OVERDUE); the left overdue cluster stacks four stations on one dot (O8).
  Partly resolved by RC4/RC5; the cluster layout half closed with the P-DB6 fix.
- **P-DB5 [decision -> D10 - BUILT 2026-07-09]** Position tracks: the big right-aligned figure is
  the destination (or $0), while the real answer sits in a small mid-bar label; also the only
  uncarded tier on the page. Built as ruled in S2 (commit 0c703538; debt-track test refit 9e3d6dce
  in an Opus context per the model discipline).
- **P-DB6 [polish - FIXED 2026-07-09, commit e52d1bb1]** Group OVERDUE tag renders under the last
  item of a multi-item day only, so it reads as if only that item is overdue. Fixed: the tag heads
  the station's stack as a day-level flag; the "+N more" overflow stays the footer line.
- **P-DB7 [decision -> D11 - FIXED]** The dashed amber threshold line is unlabeled, sits on the
  y-min so it reads as chrome, and borrows the credit token for a non-credit meaning. Color half
  landed in S1 (warning token); label half FIXED 2026-07-09, commit 4756043d: an inline Chart.js
  plugin paints "Low balance $N" at the line's right end in the shared warning ink.

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
- **P-GR5 [polish - FIXED 2026-07-09, commit 584ff3a6]** Mobile header states the same period three
  times (heading, subtitle, jump-select). **RULED 2026-07-09: delete the muted subtitle**
  (`grid/_mobile_this_period.html:113-115`) - it is the only one of the three with no function; the
  heading stays as the navigation label, the jump-select stays as fast-travel. Builds in S12 Loop B.
- **P-GR6 [bug-adjacent, found during S1 - FIXED 2026-07-09, commit 584ff3a6]** The SOLID
  `.badge-done` / `.badge-credit` pills (`grid.css` STATUS BADGES block; render bare in CC-payback
  rows and the mobile cards - the in-chip variant flattens to a glyph) set `color: #fff` on the raw
  state color: white-on-green is ~2.2:1 and white-on-violet ~3.1:1 at 11px bold, far under AA.
  Pre-existing (white-on-amber was 2.5:1); needs the chip treatment (tinted bg + state-colored text)
  or dark ink. Rides with S12's grid table-grammar session.
- Striping and category hierarchy are decisions: D3, D4 - RULED AND BUILT 2026-07-09 (S12 Loop B,
  commit 584ff3a6: category spine + whisper zebra + boosted headers + header-text pins +
  current-period highlight removal; the ruling record is inline at D3).

### Recurring

- **P-RC1 [bug - FIXED 2026-07-10 (S4), commit 475aeaaa]** Dark mode loses the income/expense banner
  tints entirely: the shared dark `.card-header` skin (`base.css:161`, specificity 0,2,0) beats the
  single-class `.recurring-banner--*` rules, so all three banners render neutral raised gray. This
  is most of O16. (Light was "correct" at audit time, but Wave 1's light `.card-header` skin
  extended the same clobber to light, so both themes were flat by S4.) Fixed by re-asserting each
  tint at matching two-class specificity (`[data-bs-theme] .recurring-banner--*`, both themes at
  once); recurring.css loads after base.css so the equal-specificity tie resolves in the banner's
  favor -- the documented `components.css:140-145` mechanism.
- **P-RC2 [bug - FIXED 2026-07-10 (S4), commit 5a035f24]** Monthly toggle square corner (O15): the
  hidden CSRF input was the first child of the `.btn-group`, so Bootstrap's
  `:not(.btn-check) + .btn` sibling rule stripped the first button's left radius
  (`templates/list.html:30-47`). Fixed by wrapping the two buttons in an inner `.btn-group` with the
  CSRF input as its sibling (still inside the form for the no-JS submit), so the outer corners round
  as one pill.
- **P-RC3 [consistency - FIXED 2026-07-09, commit d9d15028]** Income / Expenses / Transfers are
  three independent auto-layout tables, so no column lines up across sections (O14; measured up to
  280px drift). Needs a shared column contract (fixed widths or a single table grammar).
- **P-RC4 [consistency - FIXED 2026-07-10 (S4), commit 475aeaaa]** Envelope/companion badges used
  cyan `bg-info-subtle` and money-green `bg-success-subtle` for non-money flags (RC3). Fixed: both
  ride a token `.recurring-flag` chip (accent, the only non-money chroma; grid status-chip shape),
  told apart by icon + title rather than color.
- **P-RC5 [polish - FIXED 2026-07-10 (S4), commit 475aeaaa]** Light-mode toolbar search/sort
  interiors sampled identical to the page background; only a hairline marked them as fields. Fixed:
  the bare-page search + sort interiors are filled with `--shekel-surface` and a strong border so
  they read as raised fields in both themes.
- **Archive button amber (RC3 / D5): FIXED 2026-07-10 (S4), commit ad90dcfb.** The active-row
  archive button keeps `btn-outline-warning`, now skinned to `--shekel-warning` (deep, legible
  amber) by the app-wide base.css token skin; the archived-drawer delete rides the matching
  `btn-outline-danger` skin. See the D5 note in Section 4 for the app-wide skin.
- **Restore/unarchive button money-green (RC3, D5 extension): FIXED 2026-07-10 (S4 follow-up).** The
  drawer's "unarchive" control used `btn-outline-success` (money green on a non-money, reversible
  action). Developer ruling: restore is a benign action in the same quiet tier as Edit, so it
  becomes neutral `btn-outline-secondary`, keeping color reserved for the amber-caution and
  red-danger controls. Swept app-wide across all five restore/reactivate controls (recurring drawer
  x2, settings categories, settings companion reactivate, savings archived accounts); convention
  added to `fable5-design-language.md`.

### Accounts (Net Worth Cockpit)

- **P-AC1 [bug-adjacent - RULED 2026-07-11 (Loop A round 1): C1 diverging stream + milestone flags
  replaces the bar AND the trend chart]** The "mystery segment" (O19) is the unfilled remainder of
  the liability half of the diverging allocation bar, styled `--shekel-border-subtle` - dark enough
  to read as a fourth data series whose width happens to encode net worth. The presentational fix
  (recede the empty track + make the center tick visible) was scoped and mocked in S9 (three track
  candidates, both themes, real geometry). Developer ruling on seeing the annotated bar: the
  diverging bar is the wrong idiom -- it re-encodes net worth (already the band's hero figure) as an
  ambiguous empty gap and does not label which side is which, so recoloring the gap does not make it
  self-explaining. P-AC1 was deferred from S9 for a redesign-vs-remove decision. No bar change
  shipped in S9. (Scope note: this is the bar only; the liability CELL balances were a separate
  finding, P-AC4 below, and shipped.) **Loop A (2026-07-11, Fable).** On review the developer
  WIDENED the scope: the bar and the trend chart's Assets-and-Liabilities mode carry the same
  information, and neither communicates it -- the bar has composition without time and lacks the
  context to interpret it; the chart has totals without composition and its honest range makes it
  near-useless for a loan-holder (decision 11 gives them an empty history tail, and "All" reaches
  only the ~2-year rolling window, over which a mortgage-heavy distribution shifts ~4 points).
  Scoping rulings (pre-mock): ONE element replaces BOTH the bar and the whole trend chart; it must
  carry composition over time AND the net trajectory; granularity = category groups; mock BOTH
  ranges (2-year engine-real and a decades horizon) to judge whether 2 years is too static. Loop A
  round 1 (real dev data, both themes, both ranges; palette computed via the dataviz validator --
  ordinal accent pair passes all checks both themes; the categorical triple passes CVD dark 12.4 and
  sits at the legal-with-secondary-encoding floor light 11.8, satisfied by fixed stacking order +
  2px surface gaps + legend) presented four candidates: C1 diverging stream (asset bands stacked up
  the D12 ramp, liability band below zero in danger, net line riding the difference), C2 ledger
  columns, C3 split panel (net panel + 100% share stream; forced split -- a percent and a dollar
  scale cannot share one axis), W milestone-flag overlay (loan payoffs, net-worth crossings,
  debt-free; D11 structural markers, accent + text, never amber). **RULING: C1 with the W flags,
  defaulting to the HORIZON view. Range control = a two-mode toggle (`2 years` biweekly engine-real
  / `Horizon` annual long-horizon producer). The 6/13/26/All picker and the Net-vs-split series
  toggle RETIRE with this element. Net line in number ink** ("the number is the hero" applied to the
  line; the accent already colors the asset bands). Retires with the build: the diverging bar
  (`nw-alloc__*` structure; the shared `.alloc-bar` shell stays -- the loan page consumes it),
  `compute_allocation` and `_serialize_allocation_bar`, and `net_worth_cockpit.js`'s view/horizon
  logic. Build splits per the established pattern -- Loop B P1 (Opus): the long-horizon annual
  producer (loan schedules to payoff plus per-account growth params) and the per-category
  composition split of the existing 2-year series; Loop B P2 (Fable): the page element. Supersedes
  accounts_audit.md rebuild decisions 7 and 8 (amendment recorded there).
  **Loop B P1 model forks RULED 2026-07-11 (worked examples on real data):** (1) Retirement and
  Investment bands REUSE the /retirement engine -- `build_projection_context` +
  `project_retirement_accounts` over synthetic biweekly periods to the horizon end
  (`growth_engine.generate_projection_periods` takes any end date; the P2b probe seam
  `project_accounts_with_batch` takes an arbitrary axis), sampled annually. One engine, so the
  cockpit band equals /retirement by construction. Worked check: at 2049-12-31 the real engine
  projects Roth `$292,890.53` + 401(k) `$772,724.86` + Trad IRA `$122,130.44` = `$1,187,745.83` --
  vs `$800k` for a constant-contribution sketch (no raise-aware employer match, no caps) and `$3.0M`
  for a fitted-growth-rate extrapolation; the deltas are why inventing a parallel model was
  rejected. (2) Asset band = per-account params: property at its annual appreciation rate, interest
  accounts compounding at APY, plain cash held flat -- every figure traceable to a param the account
  carries. (3) Horizon domain = last loan payoff + 1 year, rounded to year end (2049 on current
  data), adapting as loans change; loan-free users get a fixed 10-year fallback. (4) Milestone flags
  = each loan payoff, debt-free, and every `$500k` net-worth crossing inside the domain, flag count
  capped for lane readability. **As-mocked C1 anatomy (the P2 visual reference; approved mock +
  generator + real-data series + engine probe preserved at `~/projects/shekel_theme/pac1_loop_a/`,
  keep until Loop B ships):** band fills at ~30% opacity (danger ~22%) so they stay quiet at panel
  scale, each band's top boundary stroked 1.75px in its own solid color, the internal asset-band
  boundary underlaid with a ~3.5px surface stroke (the 2px-gap mark spec applied to areas); the zero
  line re-emphasized in `--shekel-border-strong` over the fills; net line 2.25px
  `--shekel-number-ink`, solid to the Today marker then 6-5 dashed at 0.85 opacity, endpoint dot
  plus a `$1.50M`-style direct label in the right margin; legend = existing swatch/label/value
  grammar plus a line-sample swatch for Net worth; milestone flags = surface-raised chips (subtle
  border, secondary ink) on two staggered lanes with a dashed accent drop-line to the zero axis;
  hairline solid gridlines, sparse x labels, axis/money labels in mono at 11.5px or larger
  (JetBrains Mono's dotted zero reads as an 8 below that -- found in render review). Hover (P2): the
  existing Chart.js crosshair tooltip listing every band + net at the hovered point. Scale-up: five
  groups = four ramp bands above zero + danger below, same gaps and legend.
  **Loop B P1 BUILT 2026-07-12 (Opus; data producers plus route serialization; on dev).** One
  id-based category classifier (`_display.account_category_key`) now backs both the grid grouping
  and the split; `compute_net_worth_series` emits a reconciling `composition` band map.
  `_horizon.build_horizon` (via `compute_dashboard_data`) builds the annual composition, net
  trajectory, and milestones: retirement/investment bands reuse the /retirement engine at a constant
  employer base (so they match the 2-year band and the ruled oracle), the asset band grows each
  account by its own param via one `project_balance` call, and the liability band covers every
  liability (loans amortize through the new fenced `net_worth_kernel.loan_owed_at_dates`;
  non-amortizing debts stay flat). The horizon wires into `compute_dashboard_data`, and
  `savings._serialize_net_worth_chart` emits one Chart.js payload for both ranges (the prior 2-year
  keys unchanged, plus additive `composition` and a nested `horizon`). Verified to the cent on real
  dev data: the retirement band at 2049 equals `project_retirement_accounts` (the ruled oracle), the
  horizon's today point equals the hero, the composition reconciles to net, and the liability
  amortizes to zero at payoff. Full suite green, pylint 10.00/10, independent adversarial review
  clean after fixes (H1 non-loan liability inclusion, M1 the standalone projects only non-engine
  accounts; H2 confirmed the constant base is correct). Next = Loop B P2 (the Fable page element).
  **Loop B P2 BUILT 2026-07-12 (Opus 4.8, developer-ruled to build the visual layer too; on dev).**
  The page element: `net_worth_cockpit.js` rewritten to a Chart.js stacked-area stream (asset bands
  up from zero on the D12 accent ramp, the liability band negated below zero in danger, the net line
  in its own stack group so it rides the difference at its raw value, solid-into-dashed with the
  Today marker via the shared `splitSegment` / `todayMarkerPlugin`), a `2 years` / `Horizon` range
  toggle (Horizon default; delegated clicks survive the balanceChanged swap and the chosen range is
  re-asserted on the fresh controls), and two inline canvas plugins: the end-of-line net label and
  the milestone flags (two staggered lanes with dashed accent drop-lines, positioned via a new
  server-computed fractional `x` from `savings._milestone_axis_x`). `_cockpit.html` swaps the bar
  block + old controls for the toggle, the canvas, and a server-rendered legend (each present band's
  today subtotal + a Net-worth line sample, via the money macro); `accounts.css` retires
  `nw-alloc`/`nw-chart-controls`, adds the `--nw-band-*` color tokens (the chart resolves each
  token, including the color-mix ramp stops, to rgba via a 1x1-canvas resolver so Chart.js gets rgba
  the way every other chart does), the range toggle, the legend, and the taller `.nw-stream`.
  RETIRED root-and-branch: `compute_allocation`, `_serialize_allocation_bar`, the `allocation`
  context key, and their tests (the shared `.alloc-bar` component stays -- the loan page consumes
  it). Live-verified on real dev data in BOTH themes: net worth `$236,184.51`, milestones land where
  the P1 oracle predicted (Van '29 / 500k '33 / 1M '42 / 1.5M '47 / Debt-free '48), the legend
  reconciles to the cent (Assets `$358,034.92` plus Retirement `$71,091.15` minus Liabilities
  `$192,941.56` equals the hero), the range toggle + theme re-render are clean, zero console errors.
  Full suite 7340 green, pylint 10.00/10, biome + djlint clean, independent adversarial review
  addressed (color-mix to an rgba resolver; a stray `.nw-chart-controls` consumer in analytics
  re-homed to `.spend-chart-controls`; a per-band legend/chart reconciliation test added).
  **REPORTED SEAM -- INVESTIGATED 2026-07-12 AND WITHDRAWN; a REAL, different defect was found and
  fixed underneath it.** The earlier note here claimed the cockpit had two disagreeing loan
  producers -- `current_balance` (called "the anchor") versus the `2 years` band's `balance_at` map
  (called "the contractual amortization schedule") -- diverging when a loan's stored anchor was a
  stale true-up. **Every load-bearing claim in it was false**, and it was written (and expanded,
  commit `278490df`) ten days AFTER the loan read switch retired the anchor read, re-asserting a
  pre-read-switch mental model without re-verifying it. For the record, traced firsthand:
  `current_balance` is the LEDGER sum, not the anchor (`loan_resolution.py:94` ->
  `confirmed_loan_balance_at`; on real data the Mortgage's anchor was `$177,829.83` while
  `current_balance` was `$177,277.97`); the `2 years` map is a SPLICE -- the confirmed ledger for
  every BEGUN period, the schedule only for the future
  (`net_worth_kernel._build_amortizing_balance_map`); and the two CANNOT diverge on a stale anchor,
  because `confirmed_loan_balance_map[P]` and `confirmed_loan_balance_at(P.start_date)` are "the
  same sum by construction" (`loan_posting_service/_reader.py:120-128`). A stale true-up is posted
  INTO the ledger as a TRUEUP correction, so both producers see it identically.

  **The real defect (found while disproving the above, and fixed).** The loan engine derived a
  payment's contractual DUE DATE from its PAY-PERIOD START (`monthly_due_date`), instead of reading
  the payment's own stored `transactions.due_date`. That derivation rests on an assumption
  `monthly_due_date`'s own docstring stated -- "the payment's pay period was chosen to contain that
  due date" -- which **breaks whenever a payment is settled LATE**, a routine event (weekends,
  holidays). A late payment sits in the NEXT biweekly period, so the derivation returns the
  FOLLOWING month's installment: the operator's July mortgage payment was reported as an August one.
  Worse, that stamped a CONFIRMED schedule row with a FUTURE date, and since the ledger books by pay
  period while the schedule walks by civil date, every date-basis balance walk then disagreed with
  the ledger. Three live consequences, all reproduced on real data: the payment history showed a
  phantom August payment; the `2 years` liability band plotted the Mortgage RISING
  `$177,277.97 -> $177,554.69 -> $177,277.97` (a mortgage that grows, understating net worth); and
  `balance_at.balance_at` -- the fenced seam scalar feeding year-end debt progress -- read
  `$177,554.69` against the card's `$177,277.97`. So the audit's verdict was exactly inverted: the
  Horizon band was fine, and the `2 years` band it blessed as consistent was the broken one.

  **Fix (shipped in this arc).** (1) ROOT: one shared derivation,
  `loan_loaders.loan_payment_due_date`, reads the payment's stored `due_date` -- the installment
  identity -- while the pay period keeps its own job as the CASH basis (which period booked it, the
  basis the ledger sums on). `PaymentRecord` and the new `ConfirmedPayment` now carry BOTH dates so
  the two can never be conflated again; redistribution shifts only the DUE date and no longer
  overwrites `payment_date` (which had been silently DROPPING genuinely-made payments from the
  replay, and was the root of the accepted ledger-vs-resolver divergence recorded as review M7 /
  Step-4 M2 -- now CLOSED). (2) Migration `c4e91a7b2d38` backfills the 5 legacy loan-payment shadows
  whose stored `due_date` was a pay-period start. (3) GUARD: the past is now read from the genesis
  ledger and never re-derived from the schedule -- a schedule carries PAYMENT rows only, so it
  structurally cannot see a balance TRUE-UP, which had left the Van Loan's scalar $3.94 above what
  it owed. Confirmed rows no longer participate in any balance-at-T walk; the future is
  `current_balance` minus UNCONFIRMED scheduled principal
  (`account_projection.forward_balance_at_date`). (4) The cross-page oracle gained a late-paid
  fixture, a true-up-after-last-payment case, and a "a loan can never GROW" monotonicity invariant
  -- all four proven to FAIL against the old code. Verified on real data: ledger == card == seam
  scalar == `2 years` band == Horizon, to the cent, on both real loans. NEXT = developer acceptance
  drive + the Loop-B dev->main prod ship.
- **P-AC2 [consistency - FIXED 2026-07-10 (S13 Loop B)]** "Payoff Strategies" is
  `btn-outline-warning` - RC3. Fixed structurally: the D6-F rebuild replaced the button with an
  accent link in the Liabilities group-card footer.
- **P-AC3 [polish - FIXED 2026-07-11 (S7, commit 04b98f62)]** Asset vs Retirement bar blues were
  adjacent same-hue mixes (`rgb(70,147,190)` vs `rgb(58,117,151)`) - same genus as P-RT2. Fixed per
  ruled D12's second arm (a 3:1 step is impossible four times in one hue): 2px surface gaps now
  carry the boundary between same-hue neighbours, and the ramp's per-theme color-mix stops (dark
  100/76/56/42, light 100/80/64/49 accent mixes, theme-scoped custom properties) pass the dataviz
  ordinal ramp checks - monotone lightness, adjacent OKLCH dL >= 0.06, deepest step >= 2:1 on the
  surface. Legend labels + per-segment title tooltips remain the identity channel.
- **P-AC4 [consistency - FIXED 2026-07-11 (S9)]** Liability red applied to chip, subtotal, and bar
  segment but not to the loan card balances themselves - one quantity, two treatments on one screen.
  Fixed: the id-based `is_liability_account` classifier now sets an `is_liability` flag on each
  account's projection dict in `_project_one_account` (the single per-account builder), consumed by
  BOTH render paths (the grid include and the `compute_account_balance_cell` Cancel/409-revert
  producer, which reads the same computed value), so a reverted liability cell keeps its ink.
  `_cockpit_balance.html` applies a new `.acct-card__num--liability` token (danger ink) keyed on the
  account's category, never the figure's sign (a loan/credit-card owed balance is a positive number,
  the same display-keyed-on-category rule the subtotal already uses). Tests: two service
  (`compute_account_balance_cell` flags loan True / Checking False) + two route (the standalone cell
  renders the class for a loan, omits it for an asset). The "Liabilities" card banner and
  credit-card glyph carry the non-color signal.
- **P-AC5 [consistency - FIXED 2026-07-10 (S13 Loop B)]** Savings Goals + Emergency Fund keep
  pre-cockpit anatomy (legacy progress-bar cards, accent-colored money, badge grammar) below the new
  cockpit cards. Fixed: both re-housed into the "Savings" group card per the D6-F ruling (goals as
  blocks in the card body, coverage as the card's footer line).
- **P-AC6 [polish - FIXED 2026-07-10 (S13 Loop B)]** Property card dead space (no sparkline by
  design; row-stretch leaves an empty middle). Fixed structurally: the F-cell anatomy has no
  stretched middle; the Property cell carries the folded equity caption instead.
- Busy-ness / weak section separation: D6.

### Salary

- **P-SA1 [bug, service layer - FIXED 2026-07-10 (S3)]** Raise banner repeated on every paycheck of
  the raise month (O20): the calculator badges `raise_event` on every period of a raise month, so
  the cockpit anatomy banner reappeared on each. Fixed by collapsing the banner to the run's first
  paycheck: `paycheck_calculator._get_raise_event` promoted to public `get_raise_event`; a shared
  `salary_cockpit_service.raise_run_starts(current, prev)` seam extracted (the pairs-indexed
  `_is_raise_run_start` now delegates to it, reused from the "next raise" chip); `cockpit`'s
  `_anatomy_context` computes the predecessor's event via `get_raise_event` and passes `raise_event`
  only on the run start (`_anatomy.html` unchanged). Tests: `TestRaiseRunStarts` (7) + two anatomy
  route tests (banner shows on run start, hidden on continuation).
  **RELATED SURFACE FIXED 2026-07-10 (separate commit):** `salary/projection.html` also badged every
  period of a run (`sal-badge-raise` + `sal-row-raise` per row); the ledger now flags only each
  run's first paycheck via `salary_cockpit_service.raise_run_start_period_ids(pairs)` (route
  computes the set of run-start period ids; the template checks `period.id in` it). Same genus as
  the banner, a table surface.
- **P-SA2 [consistency - FIXED 2026-07-11 (S7, commit 252aa11c)]** Edit-profile residue (O21):
  `table-light` thead strips (RC3), `bg-info` pre-tax badges + `text-info` icons (RC3), bare `h4`
  title over a header-less first card (stacked-card layout itself is the documented form_card
  exception - kept). Fixed: raises/deductions tables adopt the shared `.table-token` head with
  right-aligned mono amounts; the timing badge became the shared flag-chip (accent pre-tax / neutral
  post-tax, keyed on the timing ID); the recurring icon moved to `text-accent`; the same-genus solid
  `bg-success` Calibrated badge became the cockpit's `.sal-cal` marker; the first card gained a
  Profile header. **Same-genus residue swept 2026-07-11 (S7 follow-up, developer-directed; commits
  67c6b21f calibrate-confirm, 58ce5aec settings pay-periods + archived categories, bfe3e7a9
  debt-strategy results x2):** the five remaining `table-light` heads moved onto `.table-token`,
  making the class extinct in templates, so Wave 1's `.table-light` CSS skin was retired as dead
  code (cf78ed25; `.table-dark` stays - the grid uses it). Live-verified both themes: the pay-period
  list, the debt calculate flow, and the calibrate form walked to its read-only confirm page; the
  archived-categories table renders only with archived rows (none on dev), so its render is pinned
  by the categories route tests.
- **P-SA3 [bug - FIXED 2026-07-11 (S7, commit 252aa11c)]** Mobile edit page: action row collapsed
  into a cramped jumble (Cancel orphaned under Update Profile); row-action delete buttons clipped at
  the viewport edge behind an unindicated scroll. Fixed: the action row wraps with flex gaps
  (Update+Cancel stay one unit, the three nav links wrap as another); both tables' action cells pin
  to the right edge via the new components.css `.table-sticky-actions` (a right-hand mirror of the
  grid's sticky-col) so row controls stay reachable while data columns slide beneath - the sliding
  itself indicates the scroll.
- **P-SA4 [consistency - FIXED 2026-07-11 (S7, commit 8e225382)]** Salary Path chart had zero axes
  by explicit sparkline config while being a half-width card whose line IS the answer (O22). Fixed
  as specified: minimal labeled axes - y ticks at the data's start/end salaries (afterBuildTicks
  pins them to the data extremes, not the padded bounds; a flat path gets one tick), first/last
  period dates on x (align inner so edge labels never clip), no grid; chart region 120px -> 140px so
  the axes do not squeeze the line. JS-only, as predicted - the data was already client-side.
- **P-SA5 [polish - FIXED 2026-07-11 (S7, commit 8e225382)]** Cockpit right column dead band between
  Salary path and Calibrated strip. Root cause: the two were loose grid siblings, and the tall
  deduction list (grid-row: span 2) stretched the row heights apart. Fixed: they are one flex stack
  (`.sal-right-col`) in grid column 2.
- **P-SA6 [polish - FIXED 2026-07-11 (S7, commit 8e225382)]** Composition-bar legend listed a
  "Post-tax" entry whose segment was an invisible 0.6% sliver (same genus as P-AC1 / P-RT4). Fixed:
  segments carry an 8px min-width floor (the S14 capped-bars trade - the legend holds the exact
  figures), and a zero-value component renders NO segment at all (the retirement meter's rule), so
  the floor can never paint a mark for $0.00.
- **[S7 ruling 2026-07-11 on the S1 amber flag (commit 8e225382)]** Post-tax composition/deduction
  marks are series identity with no money state to borrow: they moved off the caution amber onto a
  per-theme achromatic neutral (`--sal-post-tint`, text-secondary/surface mixes; CVD-adjacency
  validated against the tax segment at protan dE 19.5 dark / 13.2 light, >= 3:1 on surface),
  matching the P-SA2 timing chips (accent pre-tax / neutral post-tax everywhere). The projection
  ledger's third-paycheck row tint and star moved from amber to accent: a third paycheck is an
  informational event marker, not a caution (D11), and the staircase chart's event dots and the
  anatomy card's info banner were already accent - one concept, one color. The hero band's
  3rd-paycheck chip keeps the done ink: a positive money delta, the same grammar as the raise chip.

### Retirement

- **P-RT1 [bug + consistency - FIXED 2026-07-11 (S7, commit 27518f93)]** Pension Profiles page
  (O23): `table-light` thead (RC3), legacy card grammar, left-aligned proportional figures; PLUS a
  real mobile bug - the table had no responsive wrapper, so the thead strip and action buttons
  rendered outside the card and forced horizontal page scroll. Fixed: table-responsive wrapper, the
  token head, right-aligned mono numerics, and the action column pinned via `.table-sticky-actions`.
  **DRY extraction in the same commit:** the token thead grammar existed twice (`.retire-table` in
  retirement.css, `.sal-ledger` in salary.css); with a third consumer it moved to components.css as
  `.table-token` (the .section-banner precedent) - the retirement accounts table and projection
  ledger consume it and re-shot pixel-identical.
- **P-RT2 [consistency - FIXED 2026-07-11 (S7, commit 04b98f62)]** Income in Retirement stacked bar:
  Pension `#2878A8` vs Withdrawals `#4A9ECC`, ~1.6:1 in dark (O24). Fixed per ruled D12's luminance
  arm: dark keeps pension `#2878A8` and lifts withdrawals to `#B0D8F0` (3.21:1, each fill >= 3.4:1
  on surface); light keeps withdrawals `#3E92C2` and deepens pension to `#113A5C` (3.42:1). Pension
  reads as the deep ink blue and withdrawals as the light steel blue in BOTH themes; the ratios are
  recorded in the retirement.css ramp comment.
- **P-RT3 [consistency - FIXED 2026-07-09 (S1)]** "none linked" warning used the credit money token
  deliberately (`retirement.css:95`). Fixed: `.retire-flag` is accent - the flags mark setup gaps
  (actionable, two are links), not money states and not low-balance cautions.
- **P-RT4 [polish - FIXED 2026-07-11 (S7, commit 2dc59e37)]** "Uncovered" legend row had no swatch
  while its siblings did. Fixed: it carries a swatch in the meter's own track tint (one shared color
  definition - the uncovered remainder IS the track) with a hairline border keeping the near-surface
  tint legible.
- **[note, found during S1 - RULED + LANDED 2026-07-11 (S7, commit 8e225382)]** Salary's post-tax
  composition/deduction segments and the third-paycheck row tint were categorical series colors
  riding the caution amber. S7 ruling: post-tax -> per-theme achromatic neutral; third-paycheck
  markers -> accent. Full rationale recorded at the Salary section's S7-ruling entry.
- **P-RT5 [polish - FIXED 2026-07-11 (S7, commit 2dc59e37)]** "Close the Gap" hero said
  +$7 while the stepper said +$6.71 - rounding put principle 2 on a hair trigger. Fixed: the hero
  renders cents, so hero, stepper prefill, and outcome line all show the producer's same round_money
  value (no service change).

### Analytics (shell)

- **P-AN1 [bug]** ~80px of permanent dead space: the idle `#tab-spinner` (`py-3`) is hidden with
  `opacity: 0`, so it reserves layout space between the pills and every tab (O25). Hide it without
  reserving space (absolute overlay or `display` toggle honoring `.htmx-request`).
  **FIXED 2026-07-10 (S5, commit d2f06fd7):** the spinner is an absolute overlay pinned to a
  position-relative tab-body wrapper; the initial auto-load div also dropped its `hx-indicator` (its
  own placeholder spinner already shows, so page load no longer stacked two spinners).
- **P-AN2 [consistency + decision -> D13]** Tab treatment (O26/O27/O28): active pill raw blue (RC2);
  five-deep title stack (navbar + breadcrumb + h4 + pill + tab heading); pills read as buttons; URL
  never changes on switch and direct tab GETs redirect to the shell which auto-loads Calendar.
  Visual fix is RC2 + collapsing the redundant headings; making tabs navigational (push-url) is D13.
  **FIXED 2026-07-10 (S5, commit d2f06fd7):** the page-level "Analytics" h4 removed (the navbar
  active state and the pills carry the identity; the per-tab h5 stays as the basis-chip anchor). RC2
  landed in Wave 1, D13 in S3; the breadcrumb layer retired app-wide 2026-07-11 in S10 (D1).
- **P-AN7 [polish]** Mobile: pill row clips "Taxes" to "Taxe" with no scroll affordance.
  **FIXED 2026-07-10 (S5, commit d2f06fd7):** `.shekel-scroll-pills` mobile horizontal padding 1rem
  -> 0.65rem so the four analytics pills fit a 390px viewport; longer pill rows keep the swipe
  scroll.

### Analytics - Calendar

- **P-AN3 [consistency]** Almost-cockpit (O29): five equal-weight chips, no hero; month nav is a
  full-width scattered row unlike any other page (and unlike Spending's own picker - three month-nav
  idioms exist inside one Analytics page). Consolidate on one shared picker component; give the
  strip a hero. **FIXED 2026-07-10 (S5, commit f4ee04cc):** one pulse canvas (nw-sky) with a balance
  hero - Balance today inside the current month, Month end otherwise - the remaining chips, and the
  flow strip; the new `analytics/_picker_macros.html` `period_picker` (chevrons + fixed-width label
  as page chrome) is the ONE idiom, adopted by calendar month, calendar year, and spending
  (`.spend-month-label` generalized to `.analytics-picker-label`). One redesign-coupled test pin
  refit (`calendar-summary` -> nw-sky anatomy).
- **P-AN4 [approved removal - FIXED 2026-07-10 (S3)]** CSV export (O30) removed root-and-branch: the
  two calendar buttons, the `format=csv` branch + the `_csv_response` helper + the
  `csv_export_service`/`make_response` imports in `analytics.py`, the whole `csv_export_service.py`
  module, and `test_csv_export_service.py` all deleted; the stale `csv_export_service` mention in
  `year_end_summary_service/__init__.py` corrected. `format=csv` is now an inert query arg (renders
  the normal calendar / the D13 shell). `TestCsvExport` rewritten to `TestCsvExportRetired`; C-30
  IDOR CSV tests reworked onto the non-HTMX path.
- **P-AN5 [bug]** Payday cell tint is translucent over the grid-gap backdrop, not the cell surface
  (`analytics.css:95-97`), so light-mode paydays render flat putty-gray and read as disabled. Use an
  opaque `color-mix` with the surface. **FIXED 2026-07-10 (S5, commit be341f4f):**
  `color-mix(in srgb, var(--shekel-accent) 8%, var(--shekel-surface))`.
- **P-AN6 [decision -> D11]** Trough-day balance renders danger red purely for being the lowest day,
  even at $1,979 against a $500 threshold - the only red figure on the board, screaming "problem"
  where none exists. **FIXED 2026-07-10 (S5, commit be341f4f), per the D11 ruling:** the calendar
  adopts the grid's thresholds - danger only when an end-of-day balance is NEGATIVE, warning
  (`--low` cell ink, new `pulse-chip--warning` variant) when below the threshold but positive,
  nothing for merely being the trough. The flow strip serializes a server-computed `trough_state` so
  the dot/label ink is picked without client-side money comparison; a healthy trough renders as a
  muted marker. The below-threshold trough test pin refit to warning (developer-confirmed by the D11
  ruling) plus a new negative-trough danger pin.
- **P-AN8 [polish]** Calendar notation (`~`, `*`, `PAY`) has no on-screen legend; `*` meaning is
  tooltip-only and unreachable on touch. **FIXED 2026-07-10 (S5, commit be341f4f):** legend line
  under the grid names PAY / asterisk / tilde / paid-check.
- **P-AN9 [polish]** Mobile month-nav cramped (cyan badge wedged between title and buttons).
  **FIXED 2026-07-10 (S5, commit f4ee04cc):** the cyan badge retired; the 3rd-paycheck note rides
  the scope caption line as italic month metadata (the Spending in-progress caption pattern).
- **Year view token residue [consistency - closeout item 5]** The 12-month year overview
  (`_calendar_year.html`) still carried raw Bootstrap `border-success` / `border-danger` card edges,
  `text-success`/`text-danger` income/expense/net figures, and cyan `bg-info` "3rd check" badges,
  flagged out-of-register in the S5 as-built. **FIXED 2026-07-11:** same-genus token sweep - the
  income/expense/net figures (per-card and the annual totals) adopt the calendar money-state classes
  `calendar-day-income` / `calendar-day-expense` (matching the month sibling, so the Month/Year
  toggle stays consistent); the net-sign card border moves onto new
  `.card.calendar-month-card--surplus` / `--deficit` token modifiers (`--shekel-done` /
  `--shekel-danger`, specificity-matched to the `[data-bs-theme] .card` rule so they win on source
  order); the "3rd check" badge adopts the shared accent `.flag-chip` per D11 + the S7 event-marker
  ruling (a 3rd-paycheck month is a structural marker, not a caution). Template + CSS only, no
  figures changed; both themes re-shot and computed colors verified against the tokens.

### Analytics - Spending

- **P-AN10 [decision -> D7]** Where It Went + Top Movers restructure (O31-O34): share-bar tracks
  mostly empty (largest fill 40%); four encodings pile into the right 30% with ragged alignment;
  every Top Mover delta already on screen in the list; three text answers to one "what changed"
  question. Redesign fork - Loop A. **RULED 2026-07-10: see the D7 resolution** (direction A cockpit
  form; builds in S14 Loop B). **FIXED 2026-07-10 (S14 Loop B, commits 5bbcf9e7 + 0f57cdaf):** built
  as ruled - see the D7 as-built record.
- **P-AN11 [polish]** `"VS MAY $2,509.82"` reads as May's total but is the absolute delta,
  disambiguated only by a muted caption - caption/figure agreement in spirit.
  **Fix form ruled 2026-07-10 (D7):** the chip shows the signed delta with the prior month's total
  in the caption; builds in S14 Loop B. **FIXED 2026-07-10 (S14 Loop B, commit 5bbcf9e7):** both
  hero chips show the SIGNED delta; the vs-prior caption states the prior month's total (the
  `"-41.6% - May total $6,036.73"` form) and the vs-average caption states the average itself.

### Analytics - Statements

- **P-AN12 [consistency]** Negative Net Income is raw `text-danger` and "In balance" raw
  `bg-success` instead of the Steel trio (RC3); sibling conventions also disagree (Income Statement
  colors its negative result, Balance Sheet leaves negative Equity lines plain).
  **FIXED 2026-07-10 (S6, commit 6e41a97e):** one convention for both statements - a contra-natural
  (negative) line or total amount takes `.statement-amount--neg` danger ink beside its minus sign
  (the sheet's negative Opening equity lines now read as the anomalies they are); the net-income
  hero takes the shared verdict ink (`.nw-hero__num--pos/--neg`); the in/out-of-balance verdict is a
  done-/danger-tinted pulse chip. No raw contextual classes remain on the tab.
- **P-AN13 [decision -> D8]** Section tinting (O35). **FIXED 2026-07-10 (S6, commit 6e41a97e)** per
  the D8 ruling - see the D8 as-built record.
- **P-AN14 [polish]** Window label duplicated verbatim under the period select.
  **FIXED 2026-07-10 (S6, commit 6e41a97e):** the bare caption line is deleted; the window label
  captions the Net Income hero (where it anchors the figure), and the empty state names its window
  too - which the balance sheet's as-of empty state already did, and which un-hid a route-test
  assertion that had never verified WHICH window the pay-period fallback chose (see the D8 record).
- **P-AN15 [polish]** ~1,200px of empty leader between labels and amounts at 1440px - constrain the
  statement body width or the amount column. **FIXED 2026-07-10 (S6, commit 6e41a97e):** section
  detail sits in a 44rem `.statement-body` document column; the hero band above stays full width
  (the page's cockpit chrome, like every other tab's band).

### Analytics - Taxes

- **P-AN16 [bug]** Refund/owed money-state coloring is dead in the derivation ledger: Bootstrap's
  `.table>:not(caption)>*>*` color (0,1,1) beats `.tax-refund`/`.tax-owed` (0,1,0), so refund rows
  render plain - and if the estimate flips to owed, totals will NOT go red. Specificity fix in
  `analytics.css:281` region. **FIXED 2026-07-10 (S5, commit 2656c76c):**
  `.tax-ledger td.tax-refund` / `td.tax-owed` two-class selectors (0,2,1) added beside the bare
  classes.
- **P-AN17 [polish]** Assumptions card stretched ~500px past content by `h-100` pairing with the
  always-open 7-field checkpoint form; collapse the form behind its summary.
  **FIXED 2026-07-10 (S5, commit 2656c76c):** form collapses behind an "Update from a pay stub"
  toggle; it opens when no checkpoint is measured yet and on an error re-render. Root-cause
  companion found while verifying the error-open state: the save route's 422 card render was DEAD UI
  app-wide (htmx config drops 4xx bodies; this designed fragment never opted back in) - new
  `tax_checkpoint.js` beforeSwap shim (the retirement assumptions-panel precedent) makes validation
  errors actually swap in. The handled-500 banner path has the same genus but stays non-swapping (an
  unhandled 500 is a full error document); it belongs to the open app-wide 4xx/5xx designed-fragment
  follow-up.
- **P-AN18 [polish]** Two ambiguous captions: hero ends "...modeled after" (reads truncated);
  Effective-rate chip is combined fed+NC but captioned only "of Box 1 wages" next to a
  federal-bracket chip. **FIXED 2026-07-10 (S5, commit 2656c76c):** "modeled after that date"; "fed
  - NC combined, of Box 1 wages".
- Lever chart: D9 (recommend no).

### Settings (full retheme - the one page-scale rebuild)

**SESSION S11 COMPLETE 2026-07-11** (run on Fable 5; commits 96fa5998 shared chip/skins, b90b31b2
general+security, a48450a8 MFA pages, f93540e3 categories, 7951357e section sweeps; full suite 7258
green, pylint 10.00/10, biome + djlint clean). Verified end-to-end on a throwaway server seeded from
the test template (scripted Playwright: meter reveal/re-hide, drawer open/cancel/save round-trip,
archive-to-rail round-trip through the confirm modal, generate-periods 422-overlap + success, full
MFA enrol/disable with pyotp) plus real-data re-shoots of every section on the dev app, both themes
and mobile. Developer-ruled forks (2026-07-11): security = three cards; categories = archive/delete
fold into the edit drawer as labeled buttons; archived list moves to the rail; the un-audited
sections' same-genus sweeps are IN scope.

- **P-ST1 [bug - FIXED 2026-07-09 (Wave 1)]** Dark mode: selected sidebar section invisible (RC2
  companion; `base.css:213`).
- **P-ST2 [consistency - FIXED 2026-07-09 (Wave 1)]** Raw `#0D6EFD` active states beside the Steel
  accent Save button (RC2).
- **P-ST3 [consistency - FIXED 2026-07-11 (S11)]** No form_card anywhere: General/Security were bare
  `h5` + `hr` + fields on the page background; MFA setup was a card but titles in the body. General
  is one form_card (Dashboard & Analytics as a `.form-section`); Security is three form_cards
  (Change Password / Two-Factor Authentication / Sessions); the MFA setup, disable, and backup-codes
  pages adopt form_card with titles in the header. Duplicate per-section `h5`s dropped wherever a
  card header carries the same title (general, security, categories, account-types, pay-periods);
  load-bearing headings (Companions, Tax Configuration, Manage Schedule) stay.
- **P-ST4 [bug - FIXED 2026-07-10 (S4's app-wide D5 skin)]** `btn-outline-warning` controls ~1.4:1
  on light paper (RC3 / D5). S11 rider: the SOLID `.btn-danger` (compiled `#DC3545` literal - MFA
  disable, pay-period reset, confirm modal) gained the matching token skin in `base.css`.
- **P-ST5 [consistency - FIXED 2026-07-09 (Wave 1 `--bs-code-color` remap)]** Magenta `code` manual
  key (RC3). S11 closed the tail: the backup-codes box swapped raw `bg-dark` (codes were dark ink on
  a near-black box in light mode - unreadable) for the raised-surface token box.
- **P-ST6 [polish - FIXED 2026-07-11 (S11)]** All five sub-items: Active Sessions got its own titled
  Sessions card; Save Settings is normal-width; the strength meter ships hidden and reveals on the
  first keystroke (bar re-inked to `strength-bar--` tokens - danger/danger/warning/accent/accent,
  Good vs Strong split by width + label); the categories row triplets collapsed to one quiet Edit
  control with labeled Archive/Delete outline buttons inside the edit drawer (drawer = wrapper div,
  Save submits via the HTML `form` attribute; `categories.js` cancel resolves the drawer by id); the
  archived list moved to the rail under the Add card. DRY companion fix: `_category_row.html` became
  the single row source included per row by the settings loop - the HTMX-fresh-row copy had drifted
  (no archive action, shorter confirm text).
- **S11 sweep of the never-shot sections (O37 tail):** account-types capability badges (raw
  cyan/amber/money-green) -> accent `.flag-chip`s, Built-in neutral; pay-period lock badges re-inked
  via the route display map (Editable = accent, Settled/Posted = caution amber, informational locks
  neutral); the shared generate form adopted form_card (title serves both the settings tab and the
  standalone page); Regenerate left amber for neutral outline per D5 (rebuild, not
  archive/deactivate - the discard-confirm gate carries the caution); reset title
  `text-danger-emphasis`; companion Deactivated badge -> neutral chip. The chip itself was extracted
  from recurring.css to components.css (`.flag-chip` + `--neutral`/`--warning` variants, the
  `.section-banner` precedent); recurring's two flag spans adopted the shared name.

### Detail pages (loan / investment / cash) - sibling consistency

Overall verdict: structurally consistent siblings (identical header pattern, shared
pulse-canvas/chip vocabulary, one ShekelChart grammar). Divergences:

- **P-DT1 [bug]** Loan pay-off lever pill + range slider raw blue in both themes (RC2).
- **P-DT2 [consistency - FIXED 2026-07-11 (S8), commit 6e3f6fad]** Loan Payment Allocation bar + "4
  confirmed" badge used raw `bg-success/bg-warning/bg-info` (RC3). Fixed per the developer's S8
  ruling: the P/I/E bar moved onto the D12 ordinal accent ramp (composition is not a money state);
  the bar shell + segment base + 2px gap + swatch dims + ramp stops were EXTRACTED to components.css
  as `.alloc-bar`/`.alloc-swatch`/`--shekel-alloc-a*` and the cockpit's nw-alloc bar re-pointed at
  them (pixel-diff verified - only Chart.js canvas jitter). In-bar percent labels dropped (the
  legend names, values, and percents every segment). The "Confirmed" / "N confirmed" badges became
  `.badge-done` tint chips - ledger-confirmed IS the settled money state. Consumerless
  `.progress-h-22` retired.
- **P-DT3 [consistency - FIXED 2026-07-11 (S8), commit 2a1591d2]** Investment hero caption inherited
  `font-mono` (nested inside the mono hero div). Root fix at the shared class: `.nw-hero__cap` pins
  the body face + 400 weight (no-op for the already-sans loan/cash captions); the Inter stack is now
  declared once by remapping `--bs-body-font-family` in base.css. Verified by computed style
  (caption = Inter 400).
- **P-DT4 [consistency - FIXED 2026-07-09 (S1)]** Retirement marker in `growth_chart.js` borrowed
  the credit token. Fixed: muted ink - the chart's landmark grammar is gray text inks (the Today
  marker is `textSecondary`), so the farther milestone sits one step quieter, both labeled.
- **P-DT5 [polish - FIXED 2026-07-11 (S8), commit 63c465f0]** Casing drift: "Payment Allocation" ->
  "Payment allocation"; the three sibling save buttons converged on investment's "Save parameters"
  (loan read "Update parameters", cash "Update Parameters"); cash's "Compounding Frequency" label
  dropped to sentence case. Seven redesign-coupled header assertions refitted (string-only).
- **P-DT6 [polish - FIXED 2026-07-11 (S8), commit 20b0ba30]** Investment ended on a half-width row
  with an empty right column. The Parameters card adopted the cash `.acctd-params` measured-card
  idiom with a `--wide` 36rem modifier sized for its two-column field grid.
- **P-DT7 [polish - FIXED pre-S8; verified + closed 2026-07-11]** ARM tag inside the rate chip was
  10px amber-on-amber-tint (RC5 floor case). Already fixed by the Wave 1 RC5 floor sweep (4ff69bb0,
  0.6875rem) and the S1 re-ink to `--shekel-warning` on its 16% tint (472d591c, AA-verified per the
  S1 chip-tint method); verified on the live page this session - no further change needed.
- **P-DT8 [decision -> D14 - RESOLVED 2026-07-11 (S8)]** Three anchor-recording idioms: investment =
  click-to-edit hero, loan = form card, cash = no on-page way at all. See D14 for the as-built.
- **P-DT9 [polish - FIXED 2026-07-11 (S8), commit 229f3a1c]** Loan's "View full amortization
  schedule" floated as a small muted ghost (O3). Promoted to a full-size accent outline
  (`btn-outline-primary`); the footer placement is the locked anatomy and stays.
- **S8 out-of-scope findings (reported, not fixed):** (i) the cockpit offers the shared RAW-anchor
  editor on LOAN cells (`_cockpit.html` includes `_cockpit_balance.html` unconditionally), but
  `anchor_service.apply_anchor_true_up` is a structural no-op for amortizing loans (loans true-up
  through `apply_loan_anchor_true_up`) - saving from a loan cell writes an inert
  `AccountAnchorHistory` row and the displayed resolver balance never moves. The S8 loan hero
  (loan.anchor_form / loan.balance_hero) is the correct pattern to re-point those cells at. (ii) The
  amortization schedule page's per-row `badge bg-success">Confirmed` / `bg-secondary">Projected`
  badges are raw Bootstrap (same RC3 genus, never-audited surface; ~6 test assertions pin the
  markup).

---

## 4. Decision register (developer gates - nothing here builds without a ruling)

Developer rulings recorded 2026-07-09 (inline per item below). Summary: D1, D8, D9, D10, D12, D13,
D14 are ruled and buildable; D2, D3+D4 (combined), D6, D7 are approved for Loop A rounds with
direction notes; D5 + D11 share one open sub-fork - who owns amber (credit vs low-balance warning) -
which needs a token proposal ratified on real-page mockups before their dependent items build.

- **D1. Breadcrumb policy.** Recommend: delete breadcrumbs on navbar-level pages (dashboard, grid,
  recurring, accounts, salary, retirement, analytics, settings - where they restate the active nav
  pill), keep them on sub-pages (detail pages, forms, schedule) where they are the only way back.
  ~24 templates, mechanical once ruled.
  **RULED 2026-07-09: delete breadcrumbs EVERYWHERE, sub-pages included.** The developer prefers the
  amortization-schedule pattern - a top-right "Back to <parent>" button - and the navbar covers
  one-level-deep pages. Scope grows beyond the mechanical sweep: every sub-page whose only way back
  was the breadcrumb gets a Back button added in the same pass (inventory needed before the sweep).
  **BUILT 2026-07-11 (S10, run on Opus 4.8; full suite green, pylint 10.00/10, djlint + biome clean;
  representative pages re-shot both themes + mobile on real dev data - navbar page, loan/investment
  detail heroes, account form, debt-strategy).** Inventory: exactly 24 templates carried breadcrumbs
  (7 navbar-level pages + 16 sub-pages + base.html's block slot). Deletions: the breadcrumb block
  from all 23 content templates, the `{% block breadcrumbs %}` slot + comment from base.html, the
  now dead `.breadcrumb` CSS (base.css) + its header-comment mention, and a stale "before
  breadcrumbs" note in base.html's MFA-nag comment. No JS referenced breadcrumbs; no test asserted
  breadcrumb or Back-link HTML (the one test hit was a docstring false-positive). DRY: one shared
  `back_link(href, label)` macro in the new `app/templates/_nav_macros.html` (the topic-macro-file
  convention) is the single source for the button (style, icon, wording); its output is
  byte-identical to the two pre-existing hand-rolled precedents (`loan/schedule.html` content
  pattern, `loan/setup.html` form pattern). Sub-page placement: content/detail pages carry the
  back_link in their title flex row (title group wrapped left, button right via
  justify-content-between); form_card pages carry it in-column, right-aligned above the card (aligns
  with the card's right edge); the two raw-h4 form pages (salary/form, salary/calibrate_confirm)
  pair the h4 with the button in a justify-content-between row. Labels are the short parent name:
  "Back to Accounts" (savings.dashboard) for the account-area pages, "Back to Loan" (schedule),
  "Back to Recurring", "Back to Salary", "Back to Retirement", "Back to Budget" (pay-period
  generate), "Back to Profile" (salary.edit_profile) for the two calibrate pages. Two developer
  forks ruled pre-build (AskUserQuestion): calibrate/calibrate_confirm point to "Back to Profile"
  (matches the old breadcrumb parent AND the confirm-success redirect at calibration.py:258); and
  the two existing non-standard controls were NORMALIZED (retirement/pension_form "Back" -> "Back to
  Retirement"; salary/projection "Salary cockpit" -> "Back to Salary"). One in-scope de-duplication:
  debt_strategy had an empty-state "Back to Accounts" CTA that the new header button duplicates, so
  the empty-state copy was dropped (one back path). The investment header's conditional "Retirement
  outlook" forward link now sits in a right-side group beside the Back button (its ms-auto dropped).
  Out of scope (flagged, not touched): loan/setup.html keeps its hand-rolled button (no breadcrumb,
  so out of the sweep) - it renders identically and can adopt the macro whenever it is next touched.
- **D2. Brand wordmark font.** Today: coin PNG + plain Inter. CSP requires a vendored woff2 (Inter +
  JetBrains Mono are already vendored, so the pipeline exists). Recommend a short Loop A: 3-4
  candidate faces on the navbar + auth logo-gate mockup. **RULED 2026-07-09: Loop A approved** -
  vendored-woff2 candidates with some uniqueness; decision deferred to the loop.
  **RESOLVED 2026-07-10 (S15 Loop A, two rounds + build): Besley 700.** Round 1 (Space Grotesk 700 /
  Fraunces 600 / Instrument Serif 400 / JetBrains Mono 700 against the Inter baseline, on a
  replica-navbar + auth-gate direction-switching viewer) was rejected whole: the grotesque read as
  Inter, mono was ruled out for the wordmark, Instrument was too tall-and-scrunched, and Fraunces
  only "won by default." Round 2 kept Fraunces as the bar to beat and swung sturdier (Bricolage
  Grotesque 600 / Besley 700 / Lora 600 / Young Serif 400 / DM Serif Display 400); the developer
  ruled **Besley 700** - a Clarendon, the banknote-and-ledger slab genre - with Young Serif the
  runner-up. Built same day: Besley latin + latin-ext vendored (+28 KB) via
  scripts/vendor_google_fonts.py (whose pinned URL now also carries the S16 JetBrains Mono 500 stop
  that had been hand-added to the generated fonts.css - a re-run would have silently dropped the
  money-figure weight), `.shekel-wordmark` in base.css (Besley 700, 1.03em, 0.005em tracking), and
  the span applied to the navbar brand, the mobile drawer title, and all four auth logo gates.
- **D3. Grid row tracking.** Prior Loop A (grid_audit decision 7) rejected banding in favor of hover
  row-tint; developer still feels the pain reading without the mouse. Options: (a) keep hover only;
  (b) subtle zebra banding; (c) hairline group separators every N rows; (d) sticky row-label hover
  pairing. Recommend revisiting via Loop A mockups on the real grid - this is the app's core surface
  and O10 is a reading-comfort regression the hover tint does not cover.
  **RULED 2026-07-09: Loop A approved**, with a developer-added candidate: make the category header
  row more distinctive so it breaks up the grid; noted that a category hierarchy treatment (D4) may
  itself solve tracking - run D3 and D4 as ONE combined Loop A.
  **RESOLVED 2026-07-09 (S12 Loop A): direction f "category spine + whisper zebra."** Seven
  candidates ran on the real 6M compact grid (status quo / boosted group headers only / inline child
  / two-line label / two-line + zebra / spine / spine + zebra; disposable canvas per the visual
  loop, ratified in a live direction-switching viewer). Data findings that shaped the ruling: the
  compact view holds 31 rows, most child categories are singletons whose row name is more specific
  than the category (Geico = Car Insurance), and Family is the pain case - 16 rows spanning 4 child
  categories with runs of 7 and 6. The ruled form: child-category header rows ONLY where a category
  has >= 2 rows (Salary, Payback, Birthday, Spending Money, Subscriptions); singleton rows show the
  plain name - NO subline, NO inline kin (developer modification; the title tooltip keeps the
  category); all row labels share one indented left edge (three ledger levels: group flush, item
  header, rows); group headers boosted (12px, secondary ink, 2px top rule); the redundant "Income"
  group header under the INCOME banner drops; zebra resets per group and the current-period column
  stays continuous. Zebra polarity (developer): stripes step DARKER than the surface in dark mode,
  never lighter; hover lightens - so bands and hover are structurally distinct (the round-2 mockup's
  lighter bands sat too close to the hover tint and swallowed it). Loop B build: template + CSS only
  (RowKey already carries item_name), plus riders P-RC3, P-GR6, P-GR5, and the mobile list adopting
  the same item headers.
- **D4. Grid category hierarchy (O11).** Child category exists only in a hover tooltip. Options: (a)
  two-line row label (child in muted subline); (b) indented child rows under parent group rows; (c)
  status quo + a visible affordance. Recommend (a) - preserves density, makes the data visible, no
  structural change. **RULED 2026-07-09: Loop A approved** (combined with D3, see above).
  **RESOLVED 2026-07-09 (S12 Loop A): see the D3 ruling** - header rows for 2+ row categories,
  nothing for singletons; the two-line label lost to the spine (repetition noise in Family, no
  tracking help).
- **D5. Archive/destructive button convention.** `btn-outline-warning` amber is used for
  archive/deactivate in 10+ templates, is off-palette, and is illegible on light paper. Recommend:
  neutral outline + archive icon for archive/deactivate; `--shekel-danger` outline reserved for true
  deletes; amber never used on controls (credit state only).
  **PARTIAL RULING 2026-07-09: neutral gray REJECTED** - the developer wants a color distinguishable
  from the edit button and from plain gray, and is open to recommendations. Interacts with D11's
  credit-vs-warning split (both decide who owns amber); resolve the two together via one token
  proposal ratified on real-page mockups before the sweep.
  **RESOLVED 2026-07-09 (S1 token round): amber keeps the caution role.** Archive/deactivate =
  `--shekel-warning` amber outline; true delete = `--shekel-danger` outline; amber never appears on
  a control for any other reason. Convention written into `fable5-design-language.md` ("Credit /
  warning split"); the per-page `btn-outline-warning` sweeps execute in the page sessions (S4, S9,
  S11). **SKIN LANDED 2026-07-10 (S4), commit ad90dcfb:** rather than a per-page template swap, the
  convention is implemented as an app-wide token skin of `btn-outline-warning` -> `--shekel-warning`
  and `btn-outline-danger` -> `--shekel-danger` in `base.css` (per theme, mirroring the existing
  `btn-outline-primary` skin). This retires raw `#FFC107` / `#DC3545` everywhere at once (a pure
  fidelity change -- amber stays amber), so the not-yet-swept warning/danger buttons on the settings
  pages (S9/S11) already render Steel tokens; those sessions now only need to reconsider any
  structural/semantic button choices, not re-skin. The grid full-edit popover's local
  `btn-outline-warning` -> credit override is unaffected (source-order win).
  **RESTORE EXTENSION 2026-07-10 (S4 follow-up):** D5 never assigned a color to the inverse action
  (restore / unarchive / reactivate), which had been left on `btn-outline-success` money green --
  the same non-money-on-a-money-color class as the archive amber. Developer ruled restore = neutral
  `btn-outline-secondary` (benign, reversible, same quiet tier as Edit; color stays reserved for the
  caution and danger controls). Swept across all five occurrences (recurring drawer x2, settings
  categories, companion reactivate, savings archived accounts); no new CSS (the class was already
  themed).
- **D6. Accounts cockpit de-busying (O17/O18).** Options: (a) tinted/ruled group banners (the
  recurring vocabulary, neutralized) + demote per-card Transfer buttons to the kebab; (b) collapse
  account cards to dense list rows per group, keeping cards only for the hero and summaries; (c)
  both. Recommend Loop A with (a) and (b) as directions; (a) is the smaller change and directly
  answers O18.
  **RULED 2026-07-09: Loop A approved with (b) dense list rows REJECTED as a direction**; moving the
  per-card Transfer button into the kebab is approved outright.
  **RESOLVED 2026-07-10 (S13 Loop A, two rounds): direction F "group cells."** Round 1 ran four
  chrome restylings on the live-DB page (neutralized banners / banners + quiet cards / hairline-tile
  panels / untinted ledger rules); the developer rejected the round - restyling left fourteen
  equal-weight surfaces fourteen - naming B (banners + quiet cards) best-looking, so B became round
  2's base. Round 2 exercised the structural axis (fold-ins / cells / bands) and the developer ruled
  F: ONE card per category with a tinted banner header (12% accent mix over surface, 3px accent left
  rule, accent title, pluralized labels Assets / Liabilities, subtotal in-band) and each account as
  a chip-cell inside it - the dashboard's stat-chip grammar - carrying the name link with the Setup
  Required / Paid Off badges, the click-to-edit mono balance, one caption line, a muted sparkline
  (55% accent mix), and a quiet corner kebab (Transfer / Edit / Archive; S9's "D6 kebab move" is
  subsumed here). Ratified fold-ins for the three derivative cards: Home Equity becomes an "Equity
  $X - LTV Y%" caption on the Property cell; Debt Summary becomes the Liabilities card's footer line
  (monthly / avg rate / debt-free / DTI badge + the Payoff Strategies link, which retires that
  button's amber outline, P-AC2); Emergency Fund Coverage becomes the Savings group card's footer
  line beneath the goals (the P-AC5 re-housing). The Total Debt row is not repeated in the foot - it
  equals the banner subtotal. Fourteen equal-weight surfaces collapse to five (hero + four group
  cards), page ~35% shorter; bands (G) and fold-ins-only (E) were the losing round-2 candidates.
- **D7. Spending tab restructure (O31-O34).** Merge Where It Went + Top Movers into one
  change-focused visual (bar + delta, movers as sort/filter of the same rows), cap the share bars,
  and lead with a chart card per cockpit grammar. Redesign fork - needs Loop A; recommend scheduling
  it as its own screen rebuild after the mechanical waves. **RULED 2026-07-09: Loop A approved;
  leading with the chart card per cockpit grammar is confirmed as the anchor of every direction.**
  **RESOLVED 2026-07-10 (S14 Loop A): direction A, "months lead," in cockpit form.** Four chart-led
  candidates ran on the real June 2026 data in a direction-switching viewer (months-lead emphasis
  chart / per-group small multiples / diverging vs-prior chart / hero-canvas sparkbars), with the
  live page as a baseline tab. The ruled form:
  - ONE pulse canvas in the dashboard / accounts / salary grammar (hero + chips + chart in one band;
    no chart card, no card-header strip). The month picker is PAGE chrome (header top right,
    whole-tab scope - a developer correction to the round's first cockpit draft, which had parked it
    in-canvas where it read as a chart control); the chart bars double as click-to-navigate (Loop B
    wires it); the in-canvas caption states both ("settled spending by month - click a bar to jump
    to that month").
  - Lead chart: monthly settled totals, trailing-12 window, EMPHASIS form (viewed month in accent,
    other months muted gray), dashed muted 6-mo-avg reference line with a text label (per D11), y
    ticks at 2k/4k/6k, value labels only on the viewed month and its comparison month. Months with
    no settled rows render a small baseline tick (NOT a $0 bar) plus one "settled history begins Mar
    2026" note.
  - Merged ledger ("Where It Went"): the Top Movers card is DELETED; a "By size / By change" lens
    pill toggles one row set. **Change basis is MONTH-OVER-MONTH vs the prior month** (developer
    ruling 2026-07-10). By size keeps the group-to-item ledger with bars CAPPED to the largest row
    (bar length = relative magnitude; the % text stays share-of-total). By change is flat, sorted by
    absolute delta, with a center-zeroed diverging minibar, a signed colored delta, and the viewed
    month's total per row.
  - Per-row sparklines are DROPPED (developer: unnecessary with the bar chart; rows fall to two
    encodings). P-AN11 fix form: the vs-prior chip shows the SIGNED delta with the prior month's
    total in the caption (`"-$2,509.82 / -41.6% - May total $6,036.73"`).
  - Riders, all approved 2026-07-10: categories with prior-month spend but none in the viewed month
    appear as `$0.00` rows under By change (Mortgage, Savings Transfer, Birthday in the June data);
    singleton groups collapse to one row ("Credit Card - Payback"; the grid D3 singleton rule ported
    - today two identical `$784.01` rows render); Estimate Surprises keeps its rail unchanged.
  Data findings that shaped the ruling: the per-period-trend change basis misleads on a month-anchored
  page (real June data: Car Payment listed as a +100.00% Top Mover while June exactly equals May at
  `$531.94`; Mortgage/Rent, the single largest change at `-$1,910.95` to zero - it never settled
  inside June - appeared NOWHERE in Top Movers); settled history is 4 months deep (Mar-Jun 2026), so
  the chart must degrade gracefully; a 5-series stacked-by-group chart was rejected before
  presentation (a one-hue steel ramp fails the dataviz palette validator - adjacent steps read as one
  gray, darkest steps 1.4-2.1:1 on the dark surface - and a multi-hue ramp would break Steel Ink's
  one-chroma commitment); small multiples was the compliant sibling and lost to A on taste. Loop B
  build order: (1) OPUS data slice - expose the monthly-totals series (the hero already computes these
  windows) and prior-month per-category totals for the MoM deltas including zero-current rows; with
  sparklines and Top Movers gone the per-period trend engine has no remaining consumer on this tab
  (ItemTrend display, movers, and the sparkline serializer retire; evaluate for dead code). (2) FABLE
  page slice - template + analytics.css rebuild per the ruled form, chart in the app chart grammar
  with click-to-navigate bars; mobile reflows the ledger to name / delta / amount and compresses the
  chart. Scratch viewer (real data, all four candidates) at
  ~/Documents/spending_directions_mockup.html until Loop B ships. **BUILT 2026-07-10 (S14 Loop B, both
  slices in one Fable 5 session per the S3 precedent; commits 5bbcf9e7 rebuild + 0f57cdaf dead-code
  deletion).** As ruled, with these as-built notes:
  - The producer's trailing-12 series is the ONE source for the chart bars AND the hero's vs-prior /
    vs-average baselines (agreement by construction); a pre-history month is a None point (excluded
    from the average, drawn as a baseline tick) while a tracked zero-spend month counts as a real
    zero. The build preserved the per-window attribution fetch exactly; the same-day follow-up
    3fe12436 then fixed its boundary hole (a bill due in month M funded from a period outside M was
    attributed to NO month window) by selecting on COALESCE(due_date, period start) in SQL across
    all the user's periods - every settled expense now belongs to exactly one calendar window.
  - Ledger deltas are signed MoM DOLLARS on every row (groups sum stopped categories into their
    prior side); "new" badges replace the old percent-of-zero "New"; exact-zero deltas render muted.
    The lens toggle is Bootstrap's pill plugin (both row sets in one render, no round trip).
  - The chart is Chart.js in the app grammar (spending_tab.js): emphasis bar accent + muted 42%
    fills, dashed muted 6-mo-avg line labeled per D11, empty-month baseline ticks + the history
    note, value labels clamped inside the chart area (mobile edge bar), y ticks capped at 5 (2k
    steps on the real data), bar-click navigation via htmx.ajax mirroring the picker buttons.
  - spending_trend_service (module + 42 tests) deleted as dead code (the budget_variance_service
    precedent). Both spotted follow-ups were then FIXED the same day at the developer's direction:
    the orphaned UserSettings.trend_alert_threshold removed root-and-branch with reversible
    migration ec6054b19620 (commit 38163d02; the S11 archive-button sweep note stands, but the
    settings retheme no longer inherits this field), and the htmx settle-phase CSP violation closed
    by dropping "style" from attributesToSettle in the htmx-config meta (commit bf0b6f57;
    live-verified zero violations on the calendar AND spending chart swaps).
- **D8. Statement section distinction (O35).** Options: (a) tint only the Income Statement with the
  existing income/expense banner tokens, Balance Sheet gets stronger typographic headers; (b) add a
  neutral accent-tinted section-banner variant used by ALL statement sections
  (Assets/Liabilities/Equity get structure-color, not state-color); (c) typography only. Recommend
  (b): honors "money-state colors mean money state" (green/red stay on figures), gives the developer
  the recurring-page feel they asked for, and adds exactly one token pair.
  **RULED 2026-07-09: hybrid, and bigger than tinting.** Income Statement sections tint with the
  existing income/expense banner tokens; Assets/Liabilities/Equity get structure-color (the neutral
  accent-tinted banner variant from (b)); AND the tab's hierarchy inverts - Net Income becomes the
  Income Statement's hero instead of a bottom line, and the Balance Sheet totals become its hero.
  This is a statements-tab restructure (hero band + sections), not a class swap.
  **BUILT 2026-07-10 in S6 (commits 0a84f543 prep + 6e41a97e restructure).** As-built record:
  - The "neutral accent-tinted banner variant" needed NO new component: recurring.css already
    carried the identical banner (its transfer variant IS the accent tint), so 0a84f543 extracted it
    to components.css as the shared `.section-banner` family (`--income/--expense/--accent`) and
    folded recurring's private verdict-ink pair into `.nw-hero__num--pos/--neg` (accounts.css)
    - recurring re-shot pixel-identical (0 differing pixels, both themes). Both statements render
  sections through one `statement_section` macro (`_statement_macros.html`).
  - Income Statement band: Net income hero (verdict ink by sign), window-label caption, Income /
    Expenses chips; the three-row control stack (toggle row, selector row, duplicated label) is one
    right-aligned chrome row. Balance Sheet band: Total Assets hero ("as of" caption), Liabilities
    chip in danger ink (money owed, the accounts-cockpit precedent), Equity chip ("incl. retained
    earnings"), Trial balance verdict chip.
  - Explicit consolidation: the in-balance Trial Balance card is GONE - its verdict lives in the
    done-tinted chip captioned "assets = liabilities + equity", and its two figures were fully
    redundant with the band (assets = the hero; L+E = the two chips, equal to assets when closed).
    The figure-by-figure card (both totals + raw ledger net, danger-bordered) renders ONLY when the
    ledger does not close, where the detail localizes which half of the tie-out broke.
  - Test findings while refitting: the "no periods falls back to month" route test never exercised
    that branch - seed_user always carries the 2024 bootstrap anchor period, so the route picks the
    most-recent period; the old "this window" empty-state assertion masked it. The test is renamed
    to what it proves (most-recent-period fallback, pinned via the now-named empty-state window);
    the true no-periods month branch of `_resolve_window_params` remains uncovered by route tests
    (unreachable through existing fixtures - every seeded account requires an anchor period).
- **D9. Taxes lever chart (O36).** Recommend NO. The tab is a scalar (refund) plus its derivation
  and a calibration input; there is no trajectory to bend, and sliders would fight the tab's
  measured-vs-modeled honesty stance. Cockpit consistency is already carried by the hero band. The
  right future chart is estimate-convergence across saved checkpoints, once checkpoint history
  exists. (Independent auditor conclusion; developer to ratify.)
  **RATIFIED 2026-07-09: no lever chart.**
- **D10. Dashboard position tracks.** The hero-sized figure is the destination (or $0 for debt
  payoff); the actual answer is a small mid-bar label; the tier is the page's only uncarded surface.
  Recommend: make current-progress the big figure, destination the caption, and card the tier like
  its neighbors. **RULED 2026-07-09: as recommended.** **BUILT 2026-07-09 in S2** (commit 0c703538):
  current figure in number-ink mono as the hero, the destination of/to figure and arrival dates as
  its caption, rail label percent-only, tier carded; mobile stacks label, hero, rail. The debt-track
  test refit rode in an Opus context (9e3d6dce).
- **D11. Non-money warning color policy.** Two symptoms: the dashboard threshold line borrows credit
  amber and is unlabeled (P-DB7); the calendar trough day borrows danger red for a healthy balance
  (P-AN6). Recommend one rule: money-state tokens ONLY when the state is true (negative,
  over-budget, credit); thresholds/markers use accent or muted with a text label; the trough figure
  colors red only when below the threshold.
  **RULED 2026-07-09, and it goes further than the recommendation:** credit (informational) and
  low-balance (warning) currently share amber on the grid and MUST become different colors; the
  dashboard threshold line matches the low-balance warning color and gets a label; the calendar
  adopts the grid's thresholds and colors for low and negative balances. Open sub-fork (with D5):
  which state keeps amber - needs a token proposal ratified on real-page mockups before the
  dependent page fixes (P-DB7, P-AN6, grid balance-low, archive buttons) build.
  **RESOLVED 2026-07-09 (S1 token round): credit goes violet, warning keeps amber.** Ratified by the
  developer on a three-candidate mockup sheet (violet #A87BF7/#7443CC vs rose vs teal, both themes,
  against the full state set; rose rejected on taste, teal rejected as too close to done-green). All
  values AA-verified on every surface tier plus the chip's own 16% tint; the violet was tuned from
  the plan's ~~A371F7~~#8250DF, which failed the chip tint (dark) and two light tiers. Bonus AA fix:
  the light warning amber deepened `#9A6700 -> #855800` (the old credit value was 4.16:1 on the page
  bg and 3.78:1 on its chip tint - the RC4 failure class). Landed with the split: every caution
  consumer re-pointed to `--shekel-warning` (balance-low, threshold-line inks in
  `dashboard_pulse.js` + `calendar_flow_strip.js`, due-soon, stale anchor, ARM tag, escrow
  scheduled, loan drift-up, salary post-tax/third-paycheck tints, `verdict-pill--credit` renamed
  `--warning` since "Behind" is a pace caution). The label half of P-DB7 landed in S2 (2026-07-09,
  commit 4756043d); the calendar threshold behavior half of P-AN6 landed in S5 (2026-07-10, commit
  be341f4f - see the P-AN6 record).
- **D12. Chart series-ramp policy (O24 + P-AC3).** Same-hue accent mixes are indistinguishable in
  dark. Recommend: adjacent series must differ by >=3:1 luminance or use a second achromatic
  treatment (pattern/gap + direct labels), applied to the retirement pair and the accounts
  allocation bar together. **RULED 2026-07-09: as recommended.**
  **IMPLEMENTED 2026-07-11 (S7, commit 04b98f62):** the two-series retirement pair took the
  luminance arm (dark 3.21:1, light 3.42:1 - values at P-RT2); the four-step allocation ramp took
  the achromatic arm (2px surface gaps + the existing legend/tooltips), since a 3:1 step compounds
  to ~27:1 across four steps - impossible inside one hue - with the stops re-spread per theme to
  pass the dataviz ordinal ramp checks (details at P-AC3).
- **D13. Analytics tabs as navigation.** Visual fixes (RC2, heading collapse) are decided by the
  design language; the structural half - hx-push-url so each tab is a real URL and direct GETs load
  their own tab - changes route behavior. Recommend yes, in the Opus wave.
  **RULED 2026-07-09: as recommended (yes; Opus session).** **IMPLEMENTED 2026-07-10 (S3):** each
  pill carries `hx-push-url="true"` (click pushes its URL); every tab route renders the shell with
  `active_tab` pre-selected on a non-HTMX GET (after its ownership guard, so a cross-user id still
  404s) instead of redirecting to Calendar. The initial auto-load moved OFF the pill ONTO the
  `#tab-content` spinner (`hx-trigger="load"`, no push) so the first fetch never pushes a URL -
  which avoids a Back-button stuck-spinner trap. The internal Statements toggle (income/balance)
  stays sub-navigation, not deep-linked (tab-level scope).
- **D14. Anchor-recording idiom on detail pages.** Three siblings, three gestures (P-DT8). Recommend
  standardizing on the investment click-to-edit hero (the most discoverable), added to loan and cash
  in a later pass; not urgent.
  **RULED 2026-07-09: as recommended - standardize on the click-to-edit hero.**
  **IMPLEMENTED 2026-07-11 (S8, commits 61ad6d52 loan + 640aeb1e cash).** The `.invd-hero-edit`
  affordance was extracted to components.css as `.hero-edit` (investment re-pointed) once the second
  consumer appeared. LOAN: the hero swaps in a loan-specific dated editor (as-of date + balance;
  loan true-ups are dated LoanAnchorEvents) via new HTMX partials `loan.anchor_form` /
  `loan.balance_hero`; a SAVE posts the existing `loan.true_up_balance` redirect flow so the whole
  page re-renders together, and Cancel/Escape revert through the hero partial. Developer-ruled same
  session: the parameters card's "Record balance" form STAYS (keep both surfaces; the "Tracking
  start" form was never in question). CASH: the hero adopts the shared grid anchor editor via a new
  allowlisted `revert=cash` surface (`accounts.cash_balance_hero` is the Cancel/Escape/409 revert
  target); a save fires `balanceChanged` and the page's `#cash-band-region` re-fetches
  `accounts.cash_band` - the WHOLE band (hero, horizon chips, interest chip, trend chart) recomputes
  from the new anchor together, with `cash_detail` and the fragment sharing one
  `_cash_detail_context` builder so the render paths cannot diverge; `account_detail.js` re-creates
  the chart on `htmx:afterSettle`. The success response skips the `#anchor-as-of` OOB for
  `revert=cash` (no singleton target). Both flows live-verified on dev (loan: no-write
  open/prefill/bounds/Escape/Cancel/keyboard; cash: full save round-trip on a throwaway account,
  then archived). The L6 oracle's `data-current-balance` hook stays on the cash hero cell.
- **D15. Dark-theme neutral ladder + ink hierarchy (raised by the developer during S12,
  2026-07-09).** Developer symptoms: "the dark grays everywhere blend in," muted text still reads
  weak after the RC4 lift, grid dollar values stand out less than transaction names, "too many grays
  or not enough contrast between them." Measured causes, all confirmed: (i) SEVEN distinct dark
  surface grays (page/sticky/surface/group-header/raised/header/row-hover) packed into a 1.28:1
  total span - adjacent tiers differ by 1.02-1.07:1, below reliable perception for large fills
  (Material's smallest dark elevation step is ~1.11:1), so the tiers read as one mud; border-subtle
  sits at 1.23:1 on surface, a hairline the eye cannot find. (ii) `--shekel-text-muted` (5.28:1
  dark) is AA-legal but carries load-bearing data - the grid's Due dates - so functional content
  reads de-emphasized. (iii) Ink-hierarchy inversion: row labels are `<th>` = browser-default BOLD
  Inter; amounts are JetBrains Mono 400, same white - the label out-weighs the money figure,
  violating principle 1. The vendored woff2s are variable fonts, so mono 500/600 costs no new
  assets. Direction (needs its own token round, S16, ratified on the real grid + dashboard):
  consolidate to fewer surface tiers with perceptible steps (target >= ~1.10:1 between adjacent
  tiers, top of the range reserved for hover); lift secondary/muted one step and reassign data
  captions (due dates) from muted to secondary; money figures get mono 500 and a brighter number ink
  one step above text-primary, labels drop to 600 - "the number is the hero" applied to ink weight.
  Dark first; light re-derives after. S12 Loop B is NOT blocked: it builds on tokens via vars and
  re-skins automatically when the S16 values land.
  **RESOLVED 2026-07-09 (S16 token round): "Graphite" ratified and LANDED.** Four AA-verified
  candidate ladders ran (Carbon / Deep Field / Graphite / Warm Ink) on the live grid (interactive
  A/B viewer) and live dashboard (per-candidate injected shots, charts re-themed); the developer
  chose Graphite as "easiest to read everything" (page #101216, surface #1B1F26, raised #282E38,
  header #2C323E, hover #374049; zebra room 1.13, hover 1.57, subtle border 1.42, span 1.78). Riders
  ratified and landed with it: dark danger #F85149 -> #FB6D63 and dark credit #A87BF7 -> #B190F8
  (every candidate's raised tier forced the lifts; chip-tint re-passed); the ink hierarchy
  (--shekel-number-ink both themes, .font-mono 500 app-wide via a new variable-font weight stop,
  grid labels 600, Due captions .cell-caption secondary); section banner tints re-based on the new
  surface. Light neutrals unchanged. Values recorded in fable5-design-language.md ("Graphite
  revision"). Full suite 7311 green post-change.
  **Same session ruling: the grid's current-period column highlight is REMOVED** (developer:
  redundant, "no period highlight is needed" - at default view the current period is the first
  column). Implementation rides S12 Loop B (same templates: cur class emission, current-period
  header rule, td.cur CSS, and the current_period_id macro threading).

---

## 5. Proposed build order

**Superseded for sequencing 2026-07-09:** with the D1-D14 rulings recorded, the remaining work is
regrouped into surface-bundled sessions with per-session model assignments in
`docs/plans/historical/implementation_plan_ui_ux_polish.md`. The wave list below remains the record
of what each wave contained and what is already done; the plan governs session order from here.

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
