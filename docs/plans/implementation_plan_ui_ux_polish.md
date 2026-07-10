# Implementation Plan: UI/UX Polish Pass (Sessions and Model Assignment)

Companion to `docs/design/ui_ux_polish_audit.md` (the SoT for findings, root causes, and the D1-D14
rulings recorded 2026-07-09). This plan regroups the remaining work into logical sessions, each
sized and assigned a model, so Fable context goes where it buys the most. Waves may be worked out of
order; the true dependencies are listed per session and in the ordering notes at the end.

Plan written 2026-07-09. Done before this plan: Wave 0 (tooling), Wave 1 (system CSS, commits
7892b433 -> c2e95d9d), Wave 2 grid slice (P-GR1/P-GR2/P-GR3 + dead-rule cleanup, commits cdd57441 ->
aceb87af).

## Model policy

- **Fable:** visual, template, and CSS work - anything whose value is generating or judging design
  alternatives (Loop A rounds, hero/composition restructures, token color calls).
- **Opus:** required for any edit to `app/services/`, `app/routes/`, or test assertions (CLAUDE.md
  model discipline). Also well suited to visual work that is fully specified and mechanical - class
  swaps, pattern ports, sweeps - where design judgment is already spent.
- Sessions marked "Fable preferred, Opus capable" degrade gracefully: Opus executes the spec; defer
  the flagged judgment items inside those sessions if running on Opus.

## Session protocol (every session)

1. Load the `shekel-design` skill; read the register section for the touched surface.
2. Baseline screenshots before, re-shoot after (`tests/manual/shoot.py`, both themes; mobile
   viewport when the surface renders there). Scripted checks for scroll/hover states.
3. Targeted tests per change; full suite once as the session's final gate.
4. Logical commits per finding; update the register (mark items FIXED with date + commits).
5. Model discipline inside a session: if a Fable session needs a `services/`/`routes/`/test-
   assertion edit, that edit happens in an Opus context, not inline.

## Sessions

### S1. Token round: the amber fork (D5 + D11) -- FABLE (high value), size S

The one true "do this first": it gates the archive sweep, P-DB7, P-AN6, and the grid balance-low
color. Scope: land P-RT3 and P-DT4 first (they remove the last non-money borrowings of the credit
token); mint `--shekel-warning` (amber keeps the caution role) and recolor `--shekel-credit` (violet
candidate ~#A371F7 dark / ~#8250DF light, AA-verified); ratify on real-page mockups (grid chips +
balance-low, recurring, statements); land the token block + chip/badge consumers. Archive convention
lands here as a written rule (warning-amber outline archive, danger outline delete); the per-page
button sweeps execute in the page sessions.

### S2. Dashboard bundle (P-DB1, P-DB3, P-DB6, D10, P-DB7) -- FABLE (high value), size M

Today-label overlap fix, pulse-bracket removal, group-OVERDUE tag placement, the D10 track inversion
(current progress becomes the hero figure, destination the caption, tier carded), and the threshold
line in warning color with a label (after S1). D10 and P-DB1 are composition work - this is a Fable
session; P-DB3/P-DB7 alone would be Opus-fine but do not split them out.
**SESSION COMPLETE 2026-07-09** (commits ec6d864a -> 4756043d + the Opus debt-track test refit
9e3d6dce; full suite 7311 green; both themes + mobile re-shot before/after). Landed: bracket removed
with the street taking the tier gap; a reserved lane for the hanging Today label; OVERDUE as a
day-level header over the station stack (+N-more stays the footer); the D10 inversion (current
figure in number-ink as the hero, destination caption, percent-only rail label, carded tier, mobile
stacks label/hero/rail); "Low balance $N" painted at the threshold line's right end in warning ink.
Bonus resolution: P-DB4's remaining half (the cluster layout) closed with P-DB6.

### S3. Opus backend session (P-SA1, P-AN4, D13) -- OPUS (required), size M

Raise-event run-collapse in `salary_cockpit_service` (reuse the `_is_raise_run_start` seam); CSV
export removal root-and-branch (routes, service, buttons, tests); analytics tabs become real
navigation (hx-push-url, direct tab GETs render their tab). Independent of every Fable session; can
run any time. Note: P-AN6 needs NO route plumbing - `analytics.py` already passes
`low_balance_threshold` - so the calendar threshold work is display-side in S5. Splittable into
two smaller Opus sessions (salary | analytics) if context is tight.
**SESSION COMPLETE 2026-07-10** (run on Fable 5, which supersedes Opus in capability; full suite
7297 green, pylint 10.00/10). P-SA1: banner collapsed to the run start via a shared
`raise_run_starts` seam + public `get_raise_event`; the anatomy template is unchanged. P-AN4: the
calendar CSV removed root-and-branch (2 buttons, route branch + `_csv_response` + imports,
`csv_export_service.py` + `test_csv_export_service.py` deleted, stale `year_end` comment fixed);
`format=csv` is now inert. D13: pills push their URL; direct (non-HTMX) tab GETs render the shell
with that tab active (guard-before-render preserves the IDOR 404); the auto-load moved to the
`#tab-content` spinner so the first fetch never pushes (Back-button safe). Flagged out-of-scope:
`salary/projection.html` badges every period of a raise run (same genus as P-SA1, different table
surface). NOT yet committed.

### S4. Recurring bundle (P-RC1, P-RC2, P-RC4, P-RC5, archive swap) -- OPUS CAPABLE, size S

Dark banner-tint specificity fix (two-class pattern documented at components.css:140-145), CSRF
input out of the btn-group sibling chain, RC3 badge swaps, toolbar field interiors, archive buttons
to the S1 convention. All mechanical once S1 lands. Good Opus candidate to save Fable context. P-RC3
(the cross-section column contract) is NOT here - it needs table-grammar judgment; it rides with
S12's grid work (same table-design headspace). **SESSION COMPLETE 2026-07-10** (run on Opus; full
suite 7301 green, Biome CSS clean; both themes + mobile re-shot before/after via a git-stash
baseline). Commits: ad90dcfb (D5 app-wide button skin), 5a035f24 (P-RC2 toggle corner), 475aeaaa
(P-RC1 banner tints + P-RC4 flag chips + P-RC5 toolbar fields). P-RC1: both themes were flat (Wave
1's light `.card-header` skin extended the dark clobber), re-asserted at two-class specificity.
P-RC4: envelope/companion flags moved off cyan/money-green onto the accent `.recurring-flag` chip.
P-RC5: bare-page search/sort interiors filled to `--shekel-surface`. Archive swap (D5) landed
app-wide as a `base.css` token skin of `btn-outline-warning` / `btn-outline-danger` (the
`btn-outline-primary` pattern) rather than a recurring-only rule, so the settings pages'
not-yet-swept warning/danger buttons (S9/S11) already render Steel tokens -- a pure fidelity change
flagged in the register's D5 note. Out-of-scope: the archived-drawer "unarchive" button still uses
money-green (`btn-outline-success`) for a non-money restore; left as-is (D5 does not cover restore).

### S5. Analytics shell + calendar + taxes bundle -- FABLE preferred, OPUS capable, size M

Shell: P-AN1 spinner dead space, P-AN2 heading collapse (visual half), P-AN7 mobile pill clip.
Calendar: P-AN5 payday tint, P-AN6 trough-red only below threshold (threshold already in context),
P-AN8 notation legend, P-AN9 mobile nav, P-AN3 calendar hero + ONE shared month-picker idiom. Taxes:
P-AN16 specificity, P-AN17 collapse form, P-AN18 captions. The judgment item is P-AN3 (cockpit hero
plus picker consolidation) - defer it if running on Opus. Sequence with S3 (shared shell templates):
either order, not simultaneous.

### S6. Statements restructure (D8; subsumes P-AN12/13/14/15) -- FABLE (high value), size M

Ruled hybrid: Net Income becomes the Income Statement hero and the Balance Sheet totals its hero;
income/expense sections tint with existing banner tokens; Assets/Liabilities/Equity get the neutral
accent-tinted structure banners; money-state green/red stays on figures. Hero composition is the
point - spend Fable here. Data is already on the page; if any figure needs re-plumbing, that slice
is Opus per discipline.

### S7. Retirement + salary visual bundle -- FABLE preferred, OPUS capable, size M

Retirement: P-RT1 pension-profiles retheme (form_card idiom exists) + the mobile overflow bug, the
P-RT2/P-AC3 ramp fix per ruled D12 (>=3:1 luminance, applied to the retirement pair and the accounts
allocation bar together), P-RT4 legend swatch, P-RT5 precision agreement. Salary: P-SA2 edit residue
swaps, P-SA3 mobile action row, P-SA4 minimal sparkline axes (Chart.js config; likely JS-only -
verify data is already client-side), P-SA5 dead band, P-SA6 legend sliver. Mostly pattern
application; P-SA3 and the D12 value choice carry the judgment.

### S8. Detail pages + anchor idiom (P-DT2/3/5/6/7/9, D14) -- OPUS recommended, size S-M

RC3 swaps on the loan allocation bar and badges, mono-caption one-liner, casing normalization,
half-width row, ARM tag floor case, amortization link promoted to a real control, and the D14 port
of the investment click-to-edit hero to loan and cash. Cash currently has no on-page anchor
recording, so the port may touch a route/form - Opus fits the whole session; the pattern being
copied already exists on investment.

### S9. Accounts bundle (P-AC1, P-AC4, P-AC6, P-AC2, D6 kebab move) -- FABLE preferred, size S

Make the allocation-bar track read as a track (and the center tick visible), liability red on the
loan card balances, property-card dead space, archive button per S1, and the ruled Transfer -> kebab
demotion. P-AC5 (legacy goals/EF card anatomy) deliberately deferred to S13's Loop B, since D6 may
reshape that whole section. P-AC1 is the judgment item.
**Scope shrunk by S13's build (2026-07-10):** the kebab move, P-AC2, and P-AC6 landed with the D6-F
cell rebuild. Remaining here: P-AC1 (allocation-bar track + center tick), P-AC4 (liability red on
the cell balances), and the archive-button sweep (the cockpit has no on-card archive button left -
verify the account edit form and archived list against the S1 convention).

### S10. Breadcrumb removal + back buttons (D1) -- OPUS recommended, size M

Inventory first: every sub-page whose only way back is the breadcrumb (forms, schedule, settings
sub-pages, detail pages) gets a top-right "Back to <parent>" button on the amortization-schedule
pattern; then delete breadcrumbs everywhere (~24 templates). Fully specified, pattern exists - save
Fable. Run it when no other session is mid-flight (it brushes many templates).

### S11. Settings retheme (P-ST3-P-ST6, mini Loop B) -- FABLE preferred, OPUS workable, size M-L

The one page-scale rebuild outside the Loop A queue: form_card adoption for General/Security/MFA,
sidebar/section layout, Save button normalization, strength-meter and categories-page cleanup,
archive buttons per S1. The form_card idiom is established, so Opus can execute most of it; the
page-level composition (sidebar rhythm, section order) is where Fable helps. P-ST1/P-ST2/P-ST4 color
halves already landed in Wave 1/S1.

### S12. Loop A: grid tracking + category hierarchy (D3+D4) -- FABLE (highest value), size L

**Loop A COMPLETE + RULED 2026-07-09** (seven candidates on the real 6M compact grid; ruling and
data findings recorded inline at D3/D4 in the audit register). Ruled form: category spine (child
header rows only for 2+ row categories, plain names for singletons, uniform indent, boosted group
headers) + whisper zebra that steps DARKER in dark mode (hover lightens, so the two never collide).
P-GR5 ruled the same day: delete the mobile subtitle line.
**LOOP B BUILT 2026-07-09 - SESSION COMPLETE** (commits 584ff3a6 grid + d9d15028 recurring; full
suite 7311 green; both themes + mobile + 1Y-scrolled re-shot). Landed: category spine (item headers
for 2+ row categories on desktop AND the mobile lists, uniform indent, boosted group headers,
redundant Income header dropped), whisper zebra (darker; hover/cursor beat bands by source order, no
!important), current-period highlight removed (classes kept as JS semantic hooks), header-text pins
under horizontal scroll (fixed a pre-existing banner/group gap too), P-GR5 subtitle deletion, P-GR6
tinted badge chips, P-RC3 shared recurring column contract (th tracks pixel-identical across the
three sections). One test refitted in an Opus context per the model discipline.

### S16. Token round: dark neutral ladder + ink hierarchy (D15) -- FABLE (high value), size M

**DONE 2026-07-09** (ruling + values recorded at D15 in the audit register and in
fable5-design-language.md "Graphite revision"; full suite 7311 green). Graphite ladder ratified from
four AA-verified candidates on the live grid + dashboard; dark danger/credit lifted; number ink +
mono 500 + label/caption re-rank landed app-wide. Light neutrals unchanged. Two additions to S12
Loop B came out of the round: the ruled removal of the current-period column highlight, and
re-judging nothing else - the D3+D4 spine ruling stands as recorded.

### S13. Loop A: accounts de-busying (D6) -- FABLE (highest value), size M-L

Directions around tinted/ruled group banners and card-weight reduction; dense list rows are ruled
OUT. Loop B build includes P-AC5 (goals/EF cards to the chosen anatomy).
**Loop A COMPLETE + RULED 2026-07-10** (two rounds on the live-DB page via a direction-switching
viewer + artifact decision sheet). Round 1 (banners / banners+quiet / panels / ledger rules) was
rejected - restyling did not reduce the fourteen equal-weight surfaces - and round 2's structural
axis was ruled: **direction F "group cells"** (one card per category, tinted banner header, accounts
as chip-cells; the three derivative cards - Debt Summary, Home Equity, EF Coverage - folded into
their groups). Full ruling inline at D6 in the audit register.
**LOOP B BUILT 2026-07-10 - SESSION COMPLETE** (savings/_cockpit.html + savings/dashboard.html +
accounts.css; template + CSS only, no service changes - equity matched by account id from the
existing `property_equity` context). Fourteen surfaces -> five (hero + four group cards); P-AC5
(goals/EF re-housed into the Savings group card), P-AC2 (Payoff Strategies amber outline -> foot
link), and P-AC6 (property dead space) land here; S9's "D6 kebab move" is subsumed. Seven
redesign-coupled test assertions (old Debt Summary / EF Coverage headings) refitted in an Opus
context per the model discipline, plus the matching negative-marker test.

### S14. Loop A: spending restructure (D7; subsumes P-AN10/P-AN11) -- FABLE (highest value), size L

**LOOP A COMPLETE + RULED 2026-07-10** (four chart-led candidates on real June 2026 data in a
direction-switching viewer; ruling, data findings, and the Loop B build order recorded inline at D7
in the audit register). Ruled form: direction A "months lead" in cockpit form - one pulse canvas
(hero + chips + trailing-12 emphasis month chart, no chart card), month picker as page chrome with
click-to-navigate bars, merged ledger with a By size / By change lens on a MONTH-OVER-MONTH basis,
Top Movers card deleted, sparklines dropped, capped bars, P-AN11 signed-delta chip fix,
singleton-group collapse, zero-month rows, surprises rail unchanged.
**Loop B next, two slices in order:** (1) OPUS data slice - monthly-totals series + prior-month
per-category totals (incl. zero-current rows); retire the tab's per-period trend consumers
(ItemTrend display, movers, sparkline serializer). (2) FABLE page build - template + analytics.css +
chart wiring per the D7 record.

### S15. Loop A: brand wordmark (D2) -- FABLE, size S

3-4 vendored-woff2 candidates on the navbar + auth logo-gate mockup; tiny build after ruling (font
files + @font-face + two templates).
**DONE 2026-07-10 - two Loop A rounds + build; ruling inline at D2 in the audit register.** Ruled:
Besley 700 (round 1 rejected whole; round 2 ran sturdier faces against a Fraunces reference).
Landed: Besley latin/latin-ext vendored (+28 KB) through scripts/vendor_google_fonts.py - the
script's pinned URL now also carries the S16 JetBrains Mono 500 stop so a future re-run cannot drop
the money-figure weight - plus `.shekel-wordmark` (base.css) on the navbar brand, drawer title, and
the four auth logo gates. Live-verified on the dev app (login gate both themes + mobile; computed
font Besley 700 confirmed); full suite 7311 green.

## Where to spend Fable context (priority order)

1. **S12 grid Loop A (D3+D4)** - the app's core surface; irreplaceable design-generation work.
2. **S1 token round** - small, pure color judgment, unblocks four sessions.
3. **S14 spending Loop A (D7)** - full-screen redesign from scratch.
4. **S13 accounts Loop A (D6)** - direction generation with one direction already ruled out.
5. **S2 dashboard bundle** - D10 hero inversion + P-DB1 layout composition.
6. **S6 statements restructure** - two hero compositions.
7. **S15 wordmark Loop A** - small but pure taste.
8. **S11 settings retheme** - composition helps, but the idiom is established.
9. **S5's P-AN3 only** (calendar hero) - pull just this item into any Fable session if S5 runs on
   Opus.

Everything else - S3 (Opus required), S4, S7, S8, S10, and the mechanical remainder of S5 - runs
well on Opus with the register as the spec.

## Ordering constraints (the only real ones)

- S1 before S2 (P-DB7), S4 (archive), S5 (P-AN6), S9 (P-AC2); everything else is order-free.
- S3 and S5 share the analytics shell templates: either order, not simultaneous.
- S10 brushes ~24 templates: run it solo, between other sessions.
- Loop B builds follow their Loop A rulings; a Loop A round with an unanswered question is a STOP,
  not a license to build.
- Full-suite runs are the session gate; do not run two suites concurrently (shared test DB).
