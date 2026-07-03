# Salary Section Audit

Per-surface diagnosis of the `/salary` section for the Fable 5 UI/UX overhaul, produced with the
`shekel-design` skill (Step 1.2: no audit existed for this screen). Salary is the next target in the
overhaul's provisional order (`overhaul_plan.md`, "Remaining screens").

Audited: 2026-07-03, on `dev`-derived branch state, against live dev data (prod-clone).

## Method and scope

Every route in `app/routes/salary/` and every template in `app/templates/salary/` was read in full.
The section was rendered live (dev app, saved Playwright session, dark desktop) on real data: the
profile list, the edit form, the current-period breakdown, the projection table, and the calibration
form. Figures on the rendered pages were cross-checked by hand (deduction sums, third-paycheck skip
amounts, raise steps) and the surprising $0.00 federal withholding was traced to its stored source.
Scope is the salary section itself plus its inbound links; the paycheck calculator service internals
were NOT re-audited (its outputs reconciled by hand on the rendered pages, and calibration
provenance was verified in the DB).

The developer's presenting complaint, verbatim in spirit: navigation between the salary pages is
clunky and does not match the rest of the app; a dedicated page listing salary profiles is probably
unnecessary (usually there is exactly one profile) but multi-profile capability must remain.

## The screen's job

Answer, at a glance: **what does my paycheck actually look like, and where is it heading?**
Concretely: what lands in my account every two weeks (net), what is taken out and why (gross ->
deductions -> taxes -> net), when does it change (raises, third-paycheck months, inflation-stepped
deductions), and are the projections trustworthy (calibrated against a real stub or estimated from
brackets). Secondary jobs: maintain the inputs that drive every income transaction on the grid
(profile, W-4, raises, deductions) and calibrate projections from a pay stub.

This section is upstream of everything: the profile's linked template generates the income
transactions the grid, dashboard, balance projections, and retirement readiness all consume.

## Source of truth

| Piece | Where |
| ----- | ----- |
| Blueprint (one bp, package split) | `app/routes/salary/` (`_bp.py`, re-export in `__init__.py`) |
| Profile CRUD | `app/routes/salary/profiles.py` |
| Raises + deductions (HTMX) | `app/routes/salary/items.py` |
| Breakdown + projection views | `app/routes/salary/views.py` |
| Calibration flow | `app/routes/salary/calibration.py` |
| Tax config POSTs (+ GET redirect) | `app/routes/salary/tax_config.py` |
| Templates | `app/templates/salary/*.html` (9 files) |
| Calculator | `app/services/paycheck_calculator.py`, `calibration_service.py`, `tax_config_service.py` |

URL map as built (all under the one `salary_bp`):

```text
/salary                              list_profiles (nav landing)
/salary/new                          new_profile -> form.html
/salary  POST                        create_profile -> redirect edit
/salary/<id>/edit                    edit_profile -> form.html (the real hub)
/salary/<id>  POST                   update_profile
/salary/<id>/delete  POST            delete_profile (soft: is_active=False)
/salary/<id>/breakdown               breakdown_current -> redirect to current period
/salary/<id>/breakdown/<period_id>   breakdown
/salary/<id>/projection              projection
/salary/<id>/calibrate  GET/POST     calibrate_form / calibrate_preview
/salary/<id>/calibrate/confirm POST  calibrate_confirm
/salary/<id>/calibrate/delete POST   calibrate_delete
/salary/tax-config GET               redirect -> settings.show(section="tax")
/salary/tax-config POST              update_tax_config (form lives in settings)
/salary/fica-config POST             update_fica_config (form lives in settings)
raise/deduction add/edit/delete      HTMX partial swaps into the edit page sections
```

## Live data snapshot (2026-07-03, dev = prod-clone)

- 1 salary profile: "Data Manager", $91,675.00, 26 periods/yr, MFJ, NC, active.
- 2 raises: COLA +3.00% recurring each Jul; Merit +2.50% recurring each Jan.
- 12 deductions: 11 flat 24x/yr (skip 3rd paycheck), 1 percentage 26x/yr (State Retirement 6%). Zero
  use annual caps, inflation stepping, or target accounts.
- 1 active calibration (stub dated 2026-03-26): actual federal withholding of $0.00 (four qualifying
  children zero it out -- verified in `salary.calibration_overrides`, so the zero federal row on the
  breakdown is honest data, not a bug), state effective 2.98%.
- Current-period net
  $2,562.67; identical on list, breakdown, and projection. Sums hand-verified: pre-tax $713.29 =
  495.39 (four 24x flats) + 217.90 (6% of gross); third-paycheck rows keep only the 26x deduction
  ($217.90) and net $3,065.12.

The list page is a one-row table on real data. Every management action lives two or more clicks away
from the nav entry.

## The core diagnosis: hub-and-spoke, and the hub is the wrong page

The section is six full-page views wired hub-and-spoke, and the nominal hub (`/salary`, the list) is
a one-row table whose only purpose is to fan out to the real content. The de-facto hub is the EDIT
FORM -- the only page that links to everything (breakdown, projection, calibrate, raises,
deductions) -- so the daily read path is: nav -> list -> edit form (a mutation surface) -> the
number you wanted. Meanwhile the rebuilt screens (dashboard, accounts cockpit, account detail,
retirement) are each ONE page: hero number + caption, chip row, chart, cards below, HTMX fragments
editing in place. Salary is the last section still navigating like the v1 app: breadcrumbs +
per-page "Back to X" buttons, a `<select>` + "Go" button for period navigation (full page reload per
period), and read surfaces reachable only through a form page.

## Summary table

| # | Surface | Verdict |
| - | ------- | ------- |
| 1 | `/salary` profile list page | REMOVE as a page; fold switching + management into the cockpit |
| 2 | Edit form page (profile + W-4) | KEEP content; demote from hub to settings surface |
| 3 | Raises section (edit page) | KEEP; already HTMX-in-place; belongs on the main page |
| 4 | Deductions section (edit page) | KEEP; same as raises |
| 5 | Calibration card + 2-step flow | KEEP flow; fix entry point + honesty of the badge |
| 6 | Breakdown page | KEEP content as the centerpiece; fix period navigation |
| 7 | Projection table page | KEEP; make it a first-class sibling, not an edit-form appendix |
| 8 | Tax config surfaces | FIX residue: dead template, dishonest button label |
| 9 | Inbound links (retirement, investment, onboarding) | FIX targets once IA changes |

## Surface 1: Profile list page (`/salary`, `list.html`)

**Should show:** nothing, arguably. With one profile (the overwhelmingly common case, and the actual
case in prod data) a list page is pure indirection: one row, four icon buttons, and a screenful of
empty space. Multi-profile support is a capability requirement, not a landing-page requirement.

**Actually produces:** desktop table (name, filing status, state, annual salary, est. net biweekly +
"Cal" badge, active icon, 4 icon actions) + a mobile card list mirroring it (`list.html:29-181`).
Est. net biweekly runs the full paycheck calculator per active profile on every render
(`profiles.py:74-83`). Inactive profiles render muted, forever: `is_active` is not in
`_PROFILE_UPDATE_FIELDS` (`_helpers.py:55-59`) and no reactivate route exists, so deactivation is
one-way from the UI and the dead row can never leave the list.

**Divergence:** the landing page answers no money question (principle 1: no hero number); it is a
router. The one number it does compute (net biweekly) is the number the section exists to show,
buried in a table cell. Header has a "Tax Config" button that silently leaves the section (see
Surface 8).

**Verdict: REMOVE** as a page. `/salary` should land on the salary content itself for the primary
active profile, with a switcher when more than one profile exists (see "Rebuild direction"). Decide
at Gate A what happens to inactive profiles (reactivate action vs hidden).

## Surface 2: Profile form page (`form.html`, top half)

**Should show:** the profile's identity + tax inputs (name, salary, periods/yr, filing status,
state, W-4 step 3/4 fields) with save.

**Actually produces:** exactly that, correctly (optimistic-lock version pin, schema validation,
template amount sync, transaction regeneration on save -- `profiles.py:276-360`). Layout is plain
Bootstrap cards, pre-Steel-Ink idiom but functional. Same template serves create (fields only) and
edit (plus raises/deductions/calibration sections below).

**Divergence:** not the content -- the ROLE. This mutation form is the section's only full hub:
"View Breakdown" / "View Projection" buttons live here (`form.html:157-168`), raises/deductions
/calibration only render here. Daily reads route through an edit form. On the rebuilt screens the
equivalent surface is a settings card or slide-down off the cockpit (accounts precedent: goal_form,
pension_form, the account edit-form danger zone).

**Verdict: KEEP** the form content; demote it to `/salary/<id>/edit` settings surface reached from
the cockpit. Deactivate (and any future reactivate/delete) belongs in a danger zone here, matching
the accounts pattern (accounts_audit decision 12).

## Surface 3: Raises section (`_raises_section.html`)

**Should show:** scheduled raises and their effect timing; add/edit/remove.

**Actually produces:** correct table (type, effective month/year, %/flat, recurring flag) with HTMX
add/edit/delete swapping the section in place -- the one part of salary already behaving like the
rebuilt app. Live data: COLA Jul +3% and Merit Jan +2.5% render correctly and the projection shows
the steps at the right periods (hand-checked: $91,675 -> $94,425.25 at Jul 2026 = exactly +3%).

**Divergence:** none functionally. Cosmetics only (pre-token table styling; the add form is a dense
7-column row).

**Verdict: KEEP**, restyle in place, relocate onto the main page (it is content the daily surface
should carry, not an edit-form appendix).

## Surface 4: Deductions section (`_deductions_section.html`)

**Should show:** per-paycheck withholdings: timing (pre/post-tax), method, amount, frequency
(26/24/12), cap, inflation stepping, target account linkage.

**Actually produces:** correct and complete; same HTMX in-place pattern as raises. Timing badge uses
`deduction_timing_id` against injected ID constants and calc method uses `calc_method_id` (IDs for
logic, names for display -- compliant). Target-account select feeds retirement contributions.

**Divergence:** none functionally. The 12-row table is dense but this is a power-user app; density
is fine. Same cosmetic notes as raises.

**Verdict: KEEP**, restyle in place, relocate with raises.

## Surface 5: Calibration (card on edit page + `calibrate.html` + `calibrate_confirm.html`)

**Should show:** whether projections are grounded in a real pay stub, and a safe flow to
(re)calibrate: enter stub actuals -> preview derived effective rates -> confirm.

**Actually produces:** a sound flow. Server re-derives rates from stored actuals and treats
posted-rate mismatch as tampering (`calibration.py` module docstring); preview page shows the
derived rates before commit; delete removes the override. The "Calibrated" badge and stub date
render on the edit page and a "Cal" badge on the list.

**Divergence:** entry is buried at the bottom of the edit form. The badge says "Calibrated" but not
what that MEANS for the numbers being shown ("projections use effective rates from your 2026-03-26
stub" appears only on the edit page, not next to the hero numbers that are actually being
calibrated). Principle 2 (figure and caption never disagree) wants the calibration provenance
attached to the net-pay figure wherever it is the hero.

**Verdict: KEEP** the 2-step flow and pages (restyle); surface calibration status as the
caption/chip of the net-pay hero, with the flow linked from there.

## Surface 6: Paycheck breakdown (`breakdown.html`)

**Should show:** one paycheck's full anatomy: gross -> pre-tax -> taxable -> taxes -> post-tax ->
net, plus period context (third-paycheck month, raise event).

**Actually produces:** exactly the right content, verified correct on live data (sums above; the
$0.00 federal is honest calibrated data). Third-paycheck and raise-event callouts are accurate and
useful. This is the best content in the section.

**Divergence:** navigation. Reaching it takes list -> edit -> View Breakdown (or the list's icon
button); moving between periods is a `<select>` of every period (~56 options spanning 2026-2028) + a
"Go" button that does `window.location.href` (`app.js:271-276`) -- a full page load per period, no
prev/next, no keyboard path. The grid and account detail solved this with period chevrons and a
chart. The page is also visually pre-overhaul (plain `table-light` / `table-success` Bootstrap
zebra, no tokens, no hero treatment for net pay -- the ONE number that should be the hero in this
whole section is a table row).

**Verdict: KEEP** the anatomy as the centerpiece of the rebuilt page; replace select+Go with
prev/next period stepping (HTMX fragment swap, grid-style) defaulting to the current period.

## Surface 7: Projection table (`projection.html`)

**Should show:** the forward ledger: per period, gross/deductions/taxes/net with raise events and
third-paycheck markers, ~2 years out.

**Actually produces:** exactly that, correctly (raise steps, third-paycheck rows with only the 26x
deduction, year rollover of tax configs per DH-#30). Period labels link into per-period breakdowns
-- the one good cross-link in the section. Row highlighting (yellow raise rows, blue third-paycheck
rows) predates the token palette and reads as legacy Bootstrap (`table-warning` / `table-info`).

**Divergence:** reachable only via edit form or list icon; no summary framing (e.g. next raise date,
next third paycheck, annual net) -- the user must scan 50+ rows for the events that matter; no
reverse navigation to the breakdown's period from anywhere but row labels.

**Verdict: KEEP** as the section's ledger view (this is the grid-adjacent spreadsheet soul --
appropriate here), reachable directly from the main page; add the summary framing (chips or a small
chart) and retire the legacy row-color idiom for tokens.

## Surface 8: Tax configuration residue

**Actually produces:** `GET /salary/tax-config` redirects to `settings.show(section="tax")`
(`tax_config.py:30-35`); the two POST endpoints stay in the salary bp and the form partial lives in
settings (`settings/_tax_config_sections.html` posts to `salary.update_tax_config` /
`salary.update_fica_config`).

**Divergence, two items:**

1. `app/templates/salary/tax_config.html` is DEAD: no route renders it (the GET redirects). Only a
   comment in the settings partial still mentions it. Delete it (and the stale comment).
2. The list page's "Tax Config" button looks like in-section navigation but silently lands in
   Settings. Wherever the rebuilt page links tax settings, label it honestly ("Tax settings ->
   Settings") and link `settings.show(section='tax')` directly instead of bouncing through the
   redirect.

**Verdict: FIX** (delete dead template; honest direct link). Moving the POST endpoints into the
settings bp is OPTIONAL cleanup -- out of scope unless touched anyway.

## Surface 9: Inbound links (adjacent surfaces)

All inbound links target `salary.list_profiles` or `salary.edit_profile`:

- `base.html:118` nav entry; `base.html:251` onboarding "Set up a salary profile".
- Retirement: assumptions "from salary" caption, two empty-state CTAs, and
  `_retirement_account_table.html:13-15` which deep-links `salary.edit_profile` for the FIRST
  profile (single-profile assumption already encoded there).
- Investment dashboard empty-state CTA (`investment/dashboard.html:110`) and `investment.py:128-136`
  deduction-link helper (edit_profile / list_profiles).

**Verdict:** whatever IA wins, keep `salary.list_profiles` as a working endpoint name or update all
inbound sites (rule 7 trace). The retirement/investment deep links into the deductions section
actually get BETTER if deductions live on the main salary page.

## What the section never answers (unrealized potential)

- **"What is my next paycheck?"** as a hero. The number exists (list cell, breakdown total) but no
  surface leads with it. This is the section's one-question answer and the obvious hero.
- **"When does my pay change next?"** Next raise, next third-paycheck month, next deduction
  inflation step -- all computed, none surfaced except as rows to scan for.
- **Annual framing:** take-home rate exists on the breakdown summary; annual net / per-year totals
  never shown anywhere.
- **A trend picture:** net pay over the horizon is a step function made for a small chart (raise
  steps, third-paycheck spikes); currently only a 50-row table. Chart-first where a trend IS the
  answer is the dashboard's precedent.

## Open questions for Gate A

1. **Landing behavior:** agree `/salary` becomes the salary page itself (cockpit for the primary
   active profile, switcher when >1)? What is "primary" -- lowest `sort_order` (the model already
   has it), most recently viewed, or explicit default flag?
2. **Inactive profiles:** add a reactivate action (new route, Opus scope), or keep one-way
   deactivation and simply stop showing dead rows anywhere but the profile management surface?
3. **Breakdown + projection:** fold BOTH into the one page (breakdown band + collapsible/linked
   ledger), or keep projection as its own URL reached from the cockpit? (Recommendation: keep its
   own URL; a 50-row ledger table deserves a full-width page, like the grid.)
4. **Edit form:** settings page per profile (`/salary/<id>/edit` stays, restyled), or inline
   click-to-edit on the cockpit like the accounts cockpit? (Recommendation: keep the page; the W-4
   block is too form-heavy for click-to-edit, and pension_form/goal_form set the precedent.)
5. **Chart or not** on the salary page: net-per-paycheck step chart over the horizon (Chart.js,
   matching account detail), yes/no?
6. **Multi-profile aggregate:** when 2+ active profiles exist, does the cockpit show a combined
   household hero (sum of nets) with per-profile tabs below, or strictly per-profile with a
   switcher? (Worked example: profiles A $2,562.67 + B $1,200.00 -> combined hero $3,762.67 per
   period vs switcher showing one at a time.)

## Rebuild decisions

Gate A ruled by the developer, 2026-07-03:

1. **Landing IA: salary cockpit.** `/salary` becomes the salary page itself for the primary active
   profile (lowest `sort_order`, then name -- the list page's existing order): net-per- paycheck
   hero with calibration provenance as its caption, chip row (gross, annual, next raise, take-home
   rate), current-period paycheck anatomy with prev/next period stepping, raises + deductions +
   calibration cards, links to the projection ledger and profile settings. The list page is REMOVED.
   A profile switcher renders only when more than one profile exists.
2. **Projection ledger: own page.** `/salary/<id>/projection` stays a full-width page linked from
   the cockpit, gains summary framing (next raise, next third paycheck, annual net) above the table,
   and trades the legacy `table-warning`/`table-info` row colors for tokens.
3. **Multi-profile: switcher only.** The cockpit always presents one profile; the switcher (hidden
   at one profile) selects which, and hosts "New profile". No household aggregate is built until
   real data demands it.
4. **Inactive profiles: reactivate action.** Profile management (edit-form danger zone, accounts
   precedent) lists inactive profiles with a Reactivate action -- new route + transaction
   regeneration, Opus scope. The cockpit and switcher never show inactive profiles.

Standing recommendations adopted with Gate A (not separately questioned): the edit form stays a page
(`/salary/<id>/edit`, restyled as the profile's settings surface with the danger zone); breakdown
period stepping replaces the select+Go navigator; the dead `salary/tax_config.html` template is
deleted and tax-settings links point at `settings.show(section='tax')` directly with honest
labeling; calibration status becomes the hero's caption with the flow linked from there. Whether the
cockpit carries a net-pay step chart is deferred to the Loop A mockup rounds.

### Loop A record: direction locked over three rounds (2026-07-03)

**Round 1** (A "Pay Stub" full 19-row stub / B "Flight Path" hero band + step chart / C "Ledger
Compare" four periods side by side): developer picked B as the base to iterate on, explicitly not
locked.

**Round 2** (B1 band + full stub / B2 band + compare table / B3 "wide sky" slim hero strip +
full-width chart): developer feedback -- B3 wins on cohesiveness with /dashboard and /savings; the
stepped chart's third-paycheck spikes made the merit/COLA raise progression hard to track; the lower
cards should be "more graphical," presentation form open.

**Round 3** (both on the B3 wide-sky layout with the chart fixed: the line is a clean staircase of
REGULAR-paycheck net so raises read instantly, and third paychecks became ringed dots on thin stems
off the line): D1 "waterfall + deduction bars + raise timeline" vs D2 "composition spine + deduction
bars + salary-path staircase". Developer picked **D2 -- LOCKED.**

**The locked page, top to bottom:** slim hero strip (net-per-paycheck hero + calibration caption;
chips: gross, annual salary, next raise, 3rd paycheck delta, take-home rate); full-width net-pay
staircase chart (solid history, dashed projection, Today marker, raise step labels, 3rd-paycheck
lollipop dots, one selective label); "Where this paycheck goes" composition card (stacked spine bar
net/pre-tax/taxes/post-tax with legend figures, period stepper, expandable line items); two-column:
deductions bar-list card (proportional bars
grouped pre/post-tax, per-item amounts for the focused period) | "Salary path" card
(annual-salary staircase sparkline + the raise rules with add/edit); calibration status strip with
re-calibrate/remove. Header: "Salary" + profile tag (switcher when >1 active profile), "Projection
ledger" + "Profile settings" buttons.

Mockups live only in the session scratchpad per the anti-anchoring rule.

## Loop B plan (drafted 2026-07-03)

Branch: `feat/salary-rebuild` off `dev`. Model discipline per the skill: Opus for `app/routes/` /
`app/services/` / test assertions, Fable for templates / CSS / JS.

### P1 -- routes and producers (Opus)

1. `salary.cockpit` GET `/salary` replaces `list_profiles` (endpoint renamed; every inbound
   `url_for('salary.list_profiles')` site updated -- base nav + onboarding, retirement x4,
   investment template + `investment.py` helper, tests). Context: active profiles ordered by
   (`sort_order`, `name`); selected profile via validated `?profile=`; anatomy for the focused
   period (validated `?period=`, default current); chips data (gross, annual, next raise from raise
   schedule, third-paycheck delta, take-home rate); chart series (net per period, ~6 periods back
   through +18 months, `float()` at the JSON boundary only); raises / deductions / calibration
   context. Empty states: no profiles -> create CTA; no periods -> generate-periods blocker.
2. Anatomy fragment GET endpoint for HTMX period stepping (renders the `_anatomy.html` partial;
   owner-checked, 404 policy).
3. `/salary/<id>/breakdown[/<pid>]` become ownership-checked redirect stubs into the cockpit
   (`?profile=&period=`), account-detail precedent. Projection row links repoint.
4. POST `/salary/<id>/reactivate` (inverse of `delete_profile`: profile + template `is_active=True`,
   regenerate transactions, optimistic-lock guard).
5. Projection producer gains summary framing data (next raise, next third paycheck, per-year net
   totals).
6. Delete dead `salary/tax_config.html` + the stale comment reference; `salary.tax_config` GET
   redirect stays for old bookmarks.
7. Tests: cockpit (single + multi-profile + empty states + IDOR), fragment, reactivate, redirect
   stubs, projection summary; full suite green.

### P2 -- cockpit build (Fable), to the locked D2 composition

`salary/cockpit.html` + `salary/_anatomy.html` (the composition card is the swap target for period
stepping), `app/static/css/salary.css` (tokens only), `app/static/js/salary_chart.js` via the
`chart_theme.js` factory. Chart per the lock: staircase of regular-paycheck net (stepped line, solid
history / dashed projection, Today marker, raise step labels), third paychecks as ringed dots on
thin stems OFF the line with one selective label -- dataviz specs: 2px line, >=8px ringed markers,
hairline grid, no legend for the single series. Cards per the lock: composition spine bar (2px
surface gaps between segments; net = `--shekel-done` mix, pre-tax = accent mix, taxes = danger mix,
post-tax = credit mix; legend row carries the figures in text tokens), deductions bar-list,
salary-path sparkline card. P1 must therefore provide: base-net series + third-paycheck events +
raise events + annual- salary staircase points + per-item deduction amounts for the focused period.
Both themes, both viewports; no inline style/script (CSP).

### P3 -- secondary pages (Fable)

Projection page rebuild (summary chips row + token row states replacing
`table-warning`/`table-info`); edit form restyle + danger zone (deactivate + inactive-profile list
with Reactivate); calibrate/confirm restyle. Switcher renders only when >1 active profile.

### P4 -- verification + acceptance

Live drive on dev (both themes/viewports, mutation paths: raise/deduction add-edit-delete, calibrate
cycle, reactivate); screenshot set; full suite + pylint; developer acceptance on real data before
the PR.

## As built (2026-07-03, branch feat/salary-rebuild, pending acceptance)

P1-P3 are BUILT and live-verified on dev (prod-clone data); P4 developer acceptance pending.

- **P1 (Opus subagent):** `salary.cockpit` at `/salary` (endpoint renamed from `list_profiles`; all
  inbound sites updated), pure producers in `app/services/salary_cockpit_service.py`, anatomy
  fragment endpoint (prev/next stepping, OOB deductions), breakdown URLs -> ownership-checked
  redirect stubs, `reactivate_profile` route, projection summary producer, dead `tax_config.html`
  deleted, `list.html` + `breakdown.html` deleted. Two follow-ups landed after live verification:
  `clean_raise_label` (title-case + trailing-zero trim, applied in chips/chart/summary and exposed
  as the `raise_label` Jinja filter in `app/jinja_filters.py`), and raise-event RUN COLLAPSE (the
  calculator marks every period of a raise month; producers now treat the first period of a run of
  identical labels as THE event -- fixing both stacked chart labels and the next-raise chip claiming
  the just-landed July COLA instead of the honest Jan 2027 Merit). Deduction rows sort by amount
  descending within each timing group.
- **P2 (Fable):** cockpit rebuilt on the shared band vocabulary (`.pulse-canvas`/`.nw-sky`/
  `.pulse-chip`/`.pulse-chart`) for /dashboard/savings cohesion; `app/static/css/salary.css`
  (registered in base.html between retirement.css and utilities.css);
  `app/static/js/salary_chart.js` via the ShekelChart factory: stepped "after" staircase, solid
  history / dashed projection split, Today marker, third-paycheck lollipops (ringed dots on accent
  stems, one selective label), run-start raise labels, plus the salary-path sparkline. Composition
  spine + deduction bars render via the CSP-safe `data-progress-pct` mechanism (progress_bar.js
  re-applies after HTMX swaps).
- **P3 (Fable):** projection ledger rebuilt (summary chips: next raise / next 3rd / per-year nets
  with "periods in ledger" honesty caption; token row tints + `.sal-badge-raise` replacing
  table-warning/table-info; rows link to the cockpit focused on that period); edit form gained the
  danger zone (deactivate moved from the dead list page + inactive-profile Reactivate) and honest
  header links (Open cockpit / Projection ledger / Tax settings -> settings section).
- **Verified live:** stepper drive (primary swap + OOB deductions + 3rd-paycheck alert + width
  re-application after swap) 4/4; yearly-net chips hand-reconciled to the cent against the ledger
  rows (2026 $54,195.94 incl. the 12/31 third paycheck; 2027 $70,946.20); both themes desktop + dark
  mobile screenshots clean; targeted suites green during build.
