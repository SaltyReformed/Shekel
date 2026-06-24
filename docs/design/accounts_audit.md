# Accounts Audit

Static diagnosis of the two account screens (`/savings` and `/accounts`) ahead of the Fable 5
rebuild. This is the per-screen audit artifact for the unification of the account surfaces: one row
per surface covering what it should show, what the code actually produces, the divergence if any,
and a keep / fix / remove / merge verdict that feeds the rebuild decisions. It also records a
structural data finding (net worth is misleading until physical assets are tracked) and the
developer's locked decisions for the rebuild.

Last evaluated: 2026-06-24.

## Method and scope

- Read in full or in cited part: the `/savings` route and service package
  (`app/routes/savings.py`, `app/services/savings_dashboard_service/`), the `/accounts` route
  package and templates (`app/routes/accounts/`, `app/templates/accounts/`), the account and
  reference models (`app/models/account.py`, `app/models/ref.py`, `app/enums.py`,
  `app/ref_seeds.py`), the shared net-worth engine (`app/services/year_end_summary_service/`), and
  the reusable template / JS / CSS kit.
- This is a STATIC audit: findings follow directly from the code at the cited lines. Live
  confirmation (the local Playwright loop) and the developer's per-decision rulings happen in the
  rebuild loops, not here.
- No code was changed in producing this audit.

## The two screens today

Both screens sit behind one navbar item labeled "Accounts" that points at `savings.dashboard` and
lights up for both URLs (`base.html:113-115`). The label and the split are misleading: `/savings`
shows all account types, not just savings, and `/accounts` is a separate management table.

- `/savings` (`savings.dashboard` -> `savings_dashboard_service.compute_dashboard_data` ->
  `savings/dashboard.html`, `app/routes/savings.py:109-115`): titled "Accounts Dashboard", groups
  accounts into Asset / Liability / Retirement / Investment / Other
  (`savings_dashboard_service/_display.py:14-44`), and also renders Savings Goals, an Emergency
  Fund card, and a Debt Summary.
- `/accounts` (`accounts.list_accounts` -> `accounts/list.html`,
  `app/routes/accounts/crud.py:84-114`): a management table (Name / Type / Balance / Actions) with
  inline HTMX balance editing (`accounts/_anchor_cell.html`, `app/routes/accounts/anchor.py`) and
  archive / unarchive / hard-delete (`crud.py:416-646`).

## Summary table

| # | Surface | Location | Verdict |
| - | ------- | -------- | ------- |
| 1 | Page header | `savings/dashboard.html:15-28` | fix: rename; fold "Manage Accounts" away (the page becomes the manage surface) |
| 2 | Account card | `savings/dashboard.html:100-230` | fix: action order, remove archive from the face, present secondary data for glance |
| 3 | Category grid | `savings/dashboard.html:38-232` | fix: the "holes" mechanism |
| 4 | Debt Summary | `savings/dashboard.html:45-96` | keep + reorganize |
| 5 | Emergency Fund | `savings/dashboard.html:288-324` | keep + reorganize |
| 6 | Savings Goals | `savings/dashboard.html:327-467` | keep + reorganize |
| 7 | Archived accounts | `savings/dashboard.html:235-284` | keep (fold into the unified page) |
| 8 | `/accounts` table | `accounts/list.html`, `accounts/crud.py`, `accounts/anchor.py` | merge: inline edit + per-card menu onto the unified page; retire the table via redirect |
| 9 | Net worth | not computed in `savings_dashboard_service`; analytics engine skews negative | new: headline rollup + the home-equity fix |

## Surface 1: Page header

- **Should show:** what page this is and the few high-value actions.
- **Actually does:** an h4 "Accounts Dashboard" plus three buttons, "Manage Accounts" (to
  `accounts.list_accounts`), "New Account", and "New Goal" (`savings/dashboard.html:15-28`).
- **Divergence:** the title says "Dashboard" but the screen is also the only place that links to a
  separate management table; "Manage Accounts" implies the management lives elsewhere.
- **Verdict: fix.** Rename to a single "Accounts" surface; the page itself becomes the manage
  surface, so "Manage Accounts" folds away.

## Surface 2: Account card

- **Should show:** one account's standing and its actions, with the most important figure easiest
  to read.
- **Actually does:** an icon plus name plus optional badges ("Setup Required", "Paid Off"), the
  account type and APY / rate, then a Current Balance hero
  (`fs-4 fw-bold font-mono text-accent`), then type-specific rows (loan monthly payment and payoff
  date; a list of projected balances) (`savings/dashboard.html:100-230`). The card's actions are
  three icon-only `btn-sm` buttons in the order Transfer, Details (type-specific), Archive
  (`savings/dashboard.html:146-179`).
- **Divergence (confirmed):** Details, the most-used action, is sandwiched between Transfer and
  Archive, so it is easy to mis-click an adjacent button; Archive should not sit on the card face
  at all. The Current Balance reads well, but the rest of the data is rendered as small muted rows
  that do not read at a glance, and the card anatomy varies by account type, which hurts scanning.
- **Verdict: fix.** One obvious primary action (the card resolves to the type-specific Details
  page), management actions (Transfer / Edit / Archive / Delete) move into a quiet overflow (kebab)
  menu, and the secondary data gets a glanceable treatment (a per-account sparkline plus a single
  consistent secondary line). The Details destinations stay context-driven: checking ->
  `accounts.checking_detail`, interest-bearing -> `accounts.interest_detail`, amortizing ->
  `loan.dashboard`, retirement / investment -> `investment.dashboard`
  (`savings/dashboard.html:146-171`).

## Surface 3: Category grid (the "holes")

- **Should show:** all accounts, grouped, with no wasted space, and no artificial limit on how many
  cards sit in a row on a wide screen.
- **Actually does:** for each category it renders a section heading and a separate `.row.g-3` of
  cards at `col-md-6 col-lg-4` (two per row at md, three per row at lg)
  (`savings/dashboard.html:38-41`, `98-232`). The Debt Summary is a full-width standalone `.card`
  injected into the Liability section BEFORE that section's card grid, outside the grid entirely
  (`savings/dashboard.html:45-96`).
- **Divergence (confirmed):** two mechanisms leave "holes". First, each category is its own row, so
  any category whose card count is not a multiple of three leaves trailing empty columns (Asset
  with two cards shows one empty slot; only a three-card category such as Retirement looks full).
  Second, the full-width Debt Summary card breaks the grid rhythm in the Liability section. The
  `col-lg-4` cap also fixes the maximum at three cards across regardless of screen width.
- **Verdict: fix.** This is the core layout bug. Replace the per-category rows with a single
  responsive auto-fit grid, `repeat(auto-fit, minmax(min(280px, 100%), 1fr))`, so cards fill each
  row and more cards flow in as the viewport widens, with grouping expressed by inline headers or
  chips within the single flow. The Debt Summary stops being a full-width grid-breaker.

## Surface 4: Debt Summary

- **Should show:** total debt, monthly payments, weighted rate, DTI, and a projected debt-free
  date.
- **Actually does:** a full-width card rendered only in the Liability section when `debt_summary`
  is present, with a DTI badge whose class mapping duplicates the shared `dti_badge` macro
  (`savings/dashboard.html:45-96`; macro at `_money_macros.html`).
- **Verdict: keep + reorganize.** Keep the figures; tie the card to the liabilities group in the
  new layout and render the DTI badge through the shared `dti_badge` macro.

## Surface 5: Emergency Fund

- **Should show:** how long current savings cover expenses.
- **Actually does:** a full-width card with Months / Paychecks / Years covered, shown when
  `total_savings > 0` (`savings/dashboard.html:288-324`). The operand is liquid balances only
  (`savings_dashboard_service/_metrics.py` `_sum_liquid_balances`, summing accounts whose
  `account_type.is_liquid` is true).
- **Verdict: keep + reorganize** into a savings section alongside the goals.

## Surface 6: Savings Goals

- **Should show:** per-goal progress and trajectory.
- **Actually does:** a `.row.g-3 col-md-6` grid of goal cards with a progress bar, target date,
  recommended contribution, projected completion date, and a pace badge
  (`savings/dashboard.html:327-467`). The pace verdict comes from
  `savings_goal_service.calculate_trajectory` (pace ahead / on_track / behind).
- **Verdict: keep + reorganize.** Keep the figures; render pace through the shared `pace_pill`
  macro and present the goals using the dashboard `_tracks.html` mini-trajectory vocabulary for
  cross-screen consistency.

## Surface 7: Archived accounts

- **Should show:** a way to restore or permanently remove archived accounts.
- **Actually does:** a collapsible accordion listing archived accounts with their last known
  balance (`savings/dashboard.html:235-284`).
- **Verdict: keep**, folded into the unified page.

## Surface 8: the `/accounts` management table

- **Should show:** a place to manage accounts (rename, retype, set balance, archive, delete).
- **Actually does:** a desktop table (Name / Type / Balance / Actions) plus a mobile card variant,
  an inline HTMX balance editor (`accounts/_anchor_cell.html`, click to edit, PATCH to
  `inline_anchor_update`, optimistic `version_id` lock, `app/routes/accounts/anchor.py:50-198`), and
  edit / archive / unarchive / hard-delete routes (`accounts/crud.py:285-646`). Hard-delete is
  fresh-login gated (`@fresh_login_required()`, `crud.py:505`) and confirmed through the shared
  `data-confirm` modal (`app/static/js/app.js:447-471`).
- **Divergence:** management is a second screen reached from the dashboard, and the mobile balance
  cell is deliberately read-only there (`accounts/list.html` note) to avoid double-rendering the
  editable cell.
- **Verdict: merge.** Move the inline balance editor and the management actions (in a per-card
  kebab) onto the unified page, and retire the table. Retire by REDIRECT, not deletion: many
  handlers redirect to `accounts.list_accounts` on success
  (`crud.py:280,413,452,469,499,554,568,591,646`), so deleting the endpoint would raise
  `BuildError`; keep the endpoint and redirect it to `savings.dashboard`, which also keeps
  `tests/test_routes/test_auth_required.py:89` green.

## Surface 9: Net worth (new, and the structural finding)

- **Should show:** net worth (assets minus liabilities) as a trustworthy headline, with a forward
  trend, as the organizing figure of an accounts dashboard.
- **Actually does:** nothing. `compute_dashboard_data` returns no net-worth figure
  (`savings_dashboard_service/_orchestrator.py:386-396`). The only net-worth computation in the app
  is the `/analytics` Year-End chart, and it skews disproportionately negative.
- **Verdict: new.** Add a headline rollup, but first fix the structural cause below; net worth as a
  hero is misleading until physical assets are tracked.

### Structural finding: net worth is misleading until physical assets are tracked

The `/analytics` Year-End net-worth chart subtracts the full mortgage principal with no offsetting
asset, so a homeowner reads a disproportionately negative net worth.

- `_sum_net_worth_at_period` does `total -= abs(bal)` for any Liability-category account and
  `total += bal` for assets (`app/services/year_end_summary_service/_net_worth.py:216-238`).
- No physical-asset account type exists: `AcctTypeEnum` / `AcctCategoryEnum` (`app/enums.py:42-82`,
  seeds `app/ref_seeds.py:51-71`) cover only financial types. The skew is structural, a missing
  feature, with no existing mitigation (confirmed across `_net_worth.py`, `_balances.py`,
  `loan_params.py`).
- The same engine backs the analytics chart (`app/routes/analytics.py:184-215` ->
  `analytics/_year_end.html:232-237`), so the fix below corrects both that chart and the new
  dashboard headline.

The fix tracks the home as a first-class Asset account. An `Account` already holds a manually-set
value in `current_anchor_balance` (`app/models/account.py:84`), and a transaction-free account
carries that value forward through `balance_resolver.balances_for` (empty transaction list, flat)
(`app/services/year_end_summary_service/_balances.py:198-206`). The home then flows into the
existing net-worth producers and nets against the mortgage automatically; equity (home value minus
mortgage balance) is emergent, not a special calculation.

## Reusable kit (use, do not reinvent)

- Macros `app/templates/_money_macros.html`: `money(value, cents)`, `pace_pill(pace)`,
  `dti_badge(dti_label)`.
- Charting `app/static/js/chart_theme.js`: `ShekelChart.create(canvasId, buildConfig)`, theme
  re-render on `shekel:theme-changed`; data via `data-*` JSON, `float()` only at the route-layer
  serialization boundary (`app/routes/dashboard.py` `_serialize_chart`).
- Progress bars: `data-progress-pct` plus `app/static/js/progress_bar.js`.
- Inline balance editor: `app/templates/accounts/_anchor_cell.html` plus
  `app/routes/accounts/anchor.py`.
- Dashboard "tracks" vocabulary: `app/templates/dashboard/_tracks.html` for goal and debt mini
  visuals.
- `InterestParams` (`app/models/interest_params.py:12-103`) is the one-to-one params pattern to
  mirror for appreciation.
- CSS split (load order is load-bearing; `utilities.css` stays last): a new `accounts.css` slots
  between `dashboard.css` and `utilities.css` (`app/templates/base.html:70-72`); Bootstrap
  utilities first, no `!important` in new rules.

## UI/UX best practices applied

- Net worth is the headline KPI for an accounts dashboard; high-level rollups at top, detail one
  click away; line charts for trend, donut for allocation, tables for nitty-gritty.
- `repeat(auto-fit, minmax(min(280px, 100%), 1fr))` collapses empty tracks and distributes leftover
  space, which removes trailing gaps and lets more cards flow in on wider screens.
- Give each card one obvious primary action (often the card itself); keep secondary actions quiet
  and few; put infrequent item-specific actions (Edit, Archive, Delete) behind an overflow / kebab
  menu, never frequent ones.
- Consistent cards scan better than heterogeneous ones; disclose type-specific detail progressively.
- Sparklines give glanceable per-account trend with click-through to detail.

Sources: quadratichq.com personal-finance-dashboard, eleken.co trusted-fintech-ui-examples,
css-tricks.com auto-sizing-columns-css-grid, ishadeed.com css-grid-minmax, nngroup.com
contextual-menus-guidelines, patternfly.org overflow-menu, baymard.com cards-dashboard-layout,
domo.com sparkline-chart.

## Rebuild decisions (locked 2026-06-24)

Decided by the developer from this audit, before any code. Locked product decisions; do not revisit
without a new developer ruling.

1. **Unified page with inline management.** One Accounts page displays AND manages accounts. Edit /
   archive / delete move into a quiet per-card overflow (kebab) menu; the balance is click-to-edit
   inline (reuse the existing HTMX anchor editor). The separate `/accounts` table is retired via
   redirect. Hard-delete stays confirm plus fresh-login gated, inside the menu.
2. **Keep and reorganize** Savings Goals, Emergency Fund, and Debt Summary into the new layout
   (debt tied to liabilities; emergency-fund plus goals as a savings section).
3. **Net worth is the headline figure**, with a forward trend chart, made trustworthy by the
   home-equity fix (decision 5). Assets / liabilities / retirement roll up to it.
4. **Visual direction: "Net Worth Cockpit"** (chart-forward, consistent with the rebuilt
   dashboard). The per-account DETAIL pages (`checking`, `interest`, `loan`, `investment`) are a
   separate, later overhaul; their visual direction is decided when that work begins.
5. **Home and physical-asset model: a new "Property" (Real Estate) account type** in the Asset
   category, illiquid, whose balance is the market value the user sets and trues-up, with an
   optional annual appreciation rate. Equity nets against the mortgage automatically. This is a
   prerequisite mini-sprint and also fixes the analytics chart.

## Home-equity / physical-asset mini-sprint (prerequisite; Opus data-model work)

Adds the Property asset type with optional appreciation, wires its projection into the shared
producers, gives it a create / edit UI, and verifies the new headline and the analytics chart.
Shippable independently of the visual rebuild, and it fixes the analytics chart on its own.

- **New account type:** a "Property" (Real Estate) `AccountType` in the Asset category, flags
  `has_amortization=False`, `has_interest=False`, `is_pretax=False`, `is_liquid=False` (illiquid,
  so it is excluded from emergency-fund and savings math automatically), `has_parameters=True`.
  Add to `app/enums.py` and `app/ref_seeds.py`; the type is generic so vehicles and valuables can
  be seeded later.
- **Appreciation params:** a new one-to-one `AssetAppreciationParams(account_id,
  annual_appreciation_rate)` mirroring `InterestParams`; project the value forward with the
  existing per-period rate-compounding math (no contributions, no transactions). Build-time
  sub-decision (rule 8): a dedicated params table plus a new APPRECIATING projection branch (clean
  semantics, recommended) versus repurposing the interest engine (less code, muddier labels).
- **Projection wiring (SSOT-critical):** add an APPRECIATING branch so the home value projects
  identically on every surface. Touch `classify_account` / `AccountProjectionKind`
  (`app/services/account_projection.py`), `_dispatch_account_balance_map`
  (`app/services/year_end_summary_service/_balances.py:209-274`), and `_project_one_account`
  (`app/services/savings_dashboard_service/_projections.py:341-410`).
- **Net-worth flow:** emergent; the Property account is summed `+bal`, the mortgage `-abs(bal)`.
- **UI:** the account create / edit form (`app/templates/accounts/form.html`) gains the
  appreciation rate field for the Property type; value set and trued-up via the normal anchor flow.
- **Analytics fix:** the Year-End chart self-corrects once the asset projects; verify it is no
  longer disproportionately negative.
- **Migration and tests:** new table plus seeded type, test upgrade and downgrade, rebuild the test
  template; appreciation projection and net-worth math hand-confirmed (rule 5); full suite plus
  pylint 10.00/10.

## Chosen direction: "Net Worth Cockpit"

A chart-forward accounts dashboard consistent with the rebuilt "Terminal Road" dashboard. Anatomy
top to bottom:

1. **Hero band:** Net Worth as the hero figure plus a forward net-worth trend line (13 periods) plus
   rollup stat chips (Total Assets, Total Liabilities, Retirement). One `balanceChanged` refresh
   region.
2. **Accounts surface:** one responsive auto-fit grid; category grouping by inline headers or chips
   within the single flow. Each card carries a consistent anatomy (name, type icon, balance hero,
   one secondary line, a sparkline), the whole card resolves to the type-specific Details page, a
   quiet kebab carries Transfer / Edit / Archive / Delete, and the balance is click-to-edit inline.
3. **Savings section:** Emergency Fund coverage plus per-goal trajectory mini-charts.
4. **Debt section:** Debt Summary tied to the liabilities group.

### Feasibility notes (verified, cited)

- **Net-worth headline (as-of-today): cheap.** A small reduction over `grouped_accounts`
  (`_orchestrator.py:388`): sum `current_balance` per category, subtract liabilities. Liability
  balances are stored positive, so subtract `abs` (canonical `_net_worth.py:235`). Sum the resolver
  producers, never the raw `current_anchor_balance` cache (`_projections.py:96-104`).
- **Forward net-worth trend: moderate, the engine exists.** `_dispatch_account_balance_map`
  (`_balances.py:209`) returns a dense per-period map per account for every kind (checking via
  `balances_for`, loans via amortization, investments via growth, Property via the new appreciation
  branch), and `_sum_net_worth_at_period` sums with the right sign. The trend wires those to the
  dashboard forward window (13 periods, `dashboard_pulse_service.py:59,107-122`) and serializes at
  the established Chart.js boundary (`dashboard.py:43-71`). Open: those helpers are private to
  `year_end_summary_service`, so reuse means importing privates or promoting them to a shared
  module.
- **Allocation and per-account sparkline:** trivial / free from the same dense maps; not from the
  sparse 3-point `projected` dict (`_projections.py:145-156`).
- **Inline management:** reusable, with one wiring fix; `inline_anchor_update` returns no
  `HX-Trigger` (`anchor.py:168-170`), so add `HX-Trigger: balanceChanged` (or reuse the grid editor)
  so a balance edit refreshes the headline and trend.

## Process and next steps

Follows `docs/design/overhaul_plan.md`, "Process per screen":

0. **Mini-sprint** (ships first or in parallel): Property type plus `AssetAppreciationParams` plus
   migration plus the appreciation projection plus the form field plus the analytics chart verified,
   with tests and the full suite.
1. **Gate A confirm** (this audit's rebuild decisions).
2. **Loop A** scratch mockups for the Net Worth Cockpit in /tmp (never committed), screenshot rounds
   via `tests/manual/shoot.py`, iterate, lock the visual here.
3. **Loop B** (gated, full suite per phase; Opus for services / routes / tests, Fable for
   templates / CSS / JS): net-worth headline plus forward-series producer; the unified template plus
   `accounts.css` plus kebab and inline edit plus charts; `balanceChanged` wiring, the `/accounts`
   redirect, retire `list.html`, repoint links; then live verification.

## Verification (for the build)

- **Mini-sprint:** a Property account appreciates per its rate and carries forward on the dashboard
  AND the analytics chart; net worth equals assets plus home minus liabilities hand-confirmed at a
  period; the analytics Year-End chart is no longer disproportionately negative; migration tested up
  and down; test template rebuilt.
- **SSOT:** the net-worth headline period-0 equals the summed grid end-balances for that period;
  net-worth math hand-confirmed before any new assertion; liability sign verified.
- **Inline edit:** editing a card balance fires `balanceChanged`; the headline and trend refresh;
  the 409 conflict cell renders; cancel and Escape revert.
- **Management:** kebab archive / unarchive / delete work; hard-delete still needs fresh login plus
  confirm; `/accounts` redirects; no `BuildError` on any account create / update / delete redirect.
- **Layout:** no trailing holes at one to six or more accounts and at narrow and wide viewports;
  both themes; the mobile inline-edit cell is usable at touch size.
- **Suite:** targeted route and service tests, then the full suite green, pylint 10.00/10 with the
  custom checkers, output shown to the developer.
