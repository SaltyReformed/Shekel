# Dashboard Card Audit

Static diagnosis of the summary dashboard (`app/templates/dashboard/`) ahead of the Fable 5
rebuild proof. This is the Step 3 artifact of the overhaul plan: one row per card covering what
it should show, what the code actually produces, the divergence if any, and a usefulness verdict
that feeds the per-card keep / fix / remove gate.

Last evaluated: 2026-06-10. Re-verified against current code 2026-06-12 (see the
"Re-verification (2026-06-12)" section at the end; read it together with the card sections --
two audit claims were overturned and new defects were found).

## Method and scope

- Read in full: `app/routes/dashboard.py`, `app/services/dashboard_service.py`, and all nine
  partials under `app/templates/dashboard/`.
- This is a **static** audit: findings marked "confirmed" follow directly from the code. Findings
  marked "needs live confirmation" or "needs domain confirmation" require driving the running app
  (the local Playwright loop) or a product decision, and must not be treated as settled bugs yet.
- No code was changed. Fixes happen in Step 4a, only after the developer rules on each verdict.

## Source of truth

The route is read-only. `dashboard.page` calls
`dashboard_service.compute_dashboard_data(user_id)` and renders `dashboard/dashboard.html`. Only
two HTMX refresh endpoints exist: `dashboard.bills_section` (re-renders `_upcoming_bills.html`)
and `dashboard.balance_section` (re-renders `_balance_runway.html`). There is **no**
`spending_section` endpoint. This single fact drives the most severe finding below.

## Summary table

| # | Card | Partial | Verdict | Severity |
| - | ---- | ------- | ------- | -------- |
| 1 | Upcoming Bills | `_upcoming_bills.html`, `_bill_row.html` | fix | medium |
| 2 | Alerts | `_alerts.html` | fix | medium |
| 3 | Balance + Cash Runway | `_balance_runway.html` | investigate, then fix | high |
| 4 | Next Payday | `_payday.html` | keep | low |
| 5 | Savings Goals | `_savings_goals.html` | investigate | medium |
| 6 | Debt Summary | `_debt_summary.html` | keep | low |
| 7 | Spending Comparison | `_spending_comparison.html` | fix | high |

## Card 1: Upcoming Bills

- **Should show:** the bills the user still owes soon, so they can act before a due date.
- **Actually does:** `_get_upcoming_bills` returns projected (unpaid) expense transactions for the
  current period **and** the next period combined, sorted by due date then name. The partial
  renders them as one flat list. Entry-tracked rows show a spent / budget figure anchored on
  `estimated_amount`. Live refresh works correctly: the card wrapper does
  `hx-get="dashboard.bills_section"` on `dashboardRefresh from:body`, which re-renders this exact
  partial.
- **Divergence (confirmed):** the partial's date sub-label renders only
  `current_period.start_date -- current_period.end_date`, but the list underneath also contains
  next-period bills. A bill due in the next pay period appears under a heading that names only the
  current period's date range. The label under-describes the data.
- **Verdict: fix.** Either group the list by pay period with a heading per period, or relabel so
  the date range covers both periods shown. This is a labeling / information-architecture fix, not
  a money-math fix.

## Card 2: Alerts

- **Should show:** actionable warnings (stale checking balance, projected negative balance, low
  balance) with a way to act on each.
- **Actually does:** `_compute_alerts` builds up to three alert types, sorted danger first. The
  partial renders each with a severity icon, the message, and a "View details" link when
  `alert.link` is set.
- **Divergence (confirmed):** every alert dict sets `"link": "/"`. All three alert types point the
  "View details" link at the site root. Clicking "View details" on a low-balance or
  negative-projection alert just navigates home; it does not take the user to the anchor-update
  form, the offending pay period, or anything diagnostic.
- **Verdict: fix.** Give each alert type a meaningful link: stale or low balance to the anchor
  update flow (the balance card already links to `accounts.anchor_form`), negative projection to
  the grid at the offending period. The link should be computed in the service (it owns the period
  and account context), not hardcoded in the template.

## Card 3: Balance and Cash Runway

- **Should show:** the user's real checking balance and how long it lasts at the current spend
  rate.
- **Actually does:** `_get_balance_info` sets `current_balance` to
  `balance_results.get(current_period.id, account.current_anchor_balance or 0)`, that is, the
  balance the balance resolver computed **for the current pay period**, falling back to the raw
  anchor only if the period is missing from the map. The partial displays that number, the account
  name, and an "as of `last_true_up_date`" line (the timestamp of the most recent anchor history
  row). Cash runway is `current_balance / (settled expenses in the last 30 days by due date / 30)`,
  returning `None` for zero spend and `0` for a non-positive balance.
- **Divergence (needs live confirmation):** the displayed figure is the **projected** current-period
  balance from the resolver, while the "as of `<anchor date>`" caption reads like it labels the
  **actual anchored** checking balance. If the current-period projection differs from the raw
  anchor (it generally will, once any transaction in the period has flowed through), the number and
  its "as of" date describe two different things. This is the most likely source of the developer's
  "cards show the wrong information." Confirm against the running app: capture the displayed figure,
  the resolver's current-period value, and the raw `current_anchor_balance`, and check whether the
  caption matches the figure.
- **Verdict: investigate, then fix.** Resolve which number the card is meant to show (live anchor
  vs current-period projection) and make the figure and its caption agree. High severity because it
  is the headline number on the screen and it concerns real money.

## Card 4: Next Payday

- **Should show:** when the next paycheck lands and its projected net amount.
- **Actually does:** `_get_payday_info` finds the first period with `start_date > today`, returns
  days until, the date, and net pay from `paycheck_calculator.calculate_paycheck`. Degrades cleanly
  to a "set up salary" prompt when no active salary profile exists, and to "no upcoming pay periods"
  when none are future.
- **Note (low priority):** `compute_dashboard_data` derives the bills "next period" via
  `pay_period_service.get_next_period(current_period)`, while this card independently recomputes
  "next period" as the first period with `start_date > today`. If the two definitions ever diverge
  at a period boundary, the payday card and the bills card could disagree about which period is
  next. Worth a glance during the rebuild; not a confirmed defect.
- **Verdict: keep.** Computation is sound and the card is genuinely useful.

## Card 5: Savings Goals

- **Should show:** progress toward each active savings goal.
- **Actually does:** `_get_savings_goals` computes, per active goal,
  `percent_complete(goal.account.current_anchor_balance, goal.target_amount)` and renders a
  progress bar plus a "X% -- account_name" caption.
- **Divergence (needs domain confirmation):** progress is measured as the goal account's **entire**
  current anchor balance against the target. If a single account can back more than one goal, or
  holds money not earmarked for the goal, the percentage overstates progress (it counts every
  dollar in the account as saved toward this goal). Confirm the intended model: is a goal
  one-to-one with a dedicated account (in which case this is correct), or can an account hold
  multiple goals or non-goal funds (in which case the figure is misleading)?
- **Verdict: investigate.** If goals are one-to-one with accounts this stays as keep; otherwise it
  becomes fix.

## Card 6: Debt Summary

- **Should show:** total debt, monthly debt payments, debt-to-income ratio, and a projected
  debt-free date.
- **Actually does:** `_get_debt_summary` delegates to
  `savings_dashboard_service.compute_dashboard_data(user_id)["debt_summary"]`, reusing existing
  debt logic rather than duplicating it, and returns `None` when the user has no loan accounts (the
  card is then hidden by `{% if debt_summary %}` in `dashboard.html`). The partial renders totals,
  a DTI badge, and the debt-free month.
- **Note (low priority):** the partial branches on `debt_summary.dti_label == 'healthy'` /
  `'moderate'`. `dti_label` is a computed domain string from the debt service, not a reference-table
  `.name`, so this is not a `shekel-refname-compare` gate violation, but the string-literal branch
  is the kind of coupling worth centralizing if the rebuild touches this card.
- **Verdict: keep.** Reuses vetted logic and presents useful information.

## Card 7: Spending Comparison

- **Should show:** this period's settled spending versus last period's, with the delta.
- **Actually does:** `_get_spending_comparison` sums settled expenses for the current and prior
  periods and returns totals, delta, percent change, and direction. The partial renders the two
  totals and an up / down / same indicator.
- **Divergence (confirmed, high severity):** the card wrapper in `dashboard.html` carries
  `hx-get="{{ url_for('dashboard.bills_section') }}"` with `hx-swap="none"` on `dashboardRefresh`.
  There is no spending refresh endpoint, so on every `dashboardRefresh` this card fires a network
  request to the **bills** endpoint and discards the response (`hx-swap="none"`). The result is two
  defects in one: (a) the spending comparison never updates when a transaction is marked paid; it
  stays stale until a full page reload; and (b) every refresh triggers a wasted, misdirected fetch
  of the bills partial. This is a copy-paste leftover from the bills card.
- **Verdict: fix.** Decide the card's refresh behavior during the UX pass: either remove the dead
  `hx-get`/`hx-swap` entirely (the card recomputes on full page load only), or add a real
  `dashboard.spending_section` endpoint that re-renders `_spending_comparison.html` and wire the
  card to it with a proper `hx-target`/`hx-swap`. Do not leave it pointing at the bills endpoint.

## Cross-cutting observations

- **Refresh wiring is inconsistent.** Bills and Balance have correct, dedicated refresh endpoints;
  Spending Comparison points at the wrong one; Alerts, Payday, Savings, and Debt have no live
  refresh at all and only update on full page load. The rebuild should make each card's refresh
  story deliberate rather than incidental.
- **Links are placeholders.** The alert "View details" links all resolve to `/`. Any rebuilt card
  that offers a call to action should route to the screen that actually resolves the issue.
- **Money formatting is duplicated in templates.** Every card hand-formats with
  `"{:,.2f}".format(...)`. Acceptable today, but a shared currency macro would reduce drift as the
  surface is rebuilt. Out of scope for the data fixes; consider it during the visual rebuild.

## Re-verification (2026-06-12)

Every claim above was re-checked against current code (dev HEAD 091f4de) by a nine-agent
verification workflow plus a completeness critic, ahead of Gate A. Templates, route, and
dashboard.js are byte-identical since 2026-05-25 (commit 5860fa6); only
`dashboard_service.py` changed since the audit (0e27664 debt producer, d11f3e1 per-year tax
configs). Findings below use current line numbers.

### Corrections to the audit

1. **`dashboardRefresh` is a phantom event -- the audit's "live refresh works correctly" for
   Card 1 was wrong even at audit time.** The event has three listeners (`dashboard.html:45`,
   `:72`, `:121`) and ZERO emitters anywhere in `app/`. Its only emitter ever was the retired
   `dashboard.mark_paid` route, deleted in 5860fa6 on 2026-05-25, before this audit was
   written. Consequence: the Bills card never live-refreshes; the only live refresh on the
   page is the Balance card via its second trigger `balanceChanged from:body`. The Spending
   Comparison wiring is therefore triple-broken (dead event + wrong endpoint + `hx-swap=
   "none"`).
2. **Dashboard mark-paid is already resolved as REMOVE, not pending a decision.** The button
   was removed in e079a4e and the route + schema caller + 13 tests in 5860fa6 (both
   2026-05-25, "Q-1 of the mobile-first v3 plan resolved as REMOVE"). A bill row today offers
   zero actions (`_bill_row.html` has no a/button/form/hx-post). Residue remains: permanently
   false `bill.is_paid` branches in `_bill_row.html` (:5, :10, :12-13, :24, :39), the no-op
   `dashboard.js`, and four stale mark-paid comments/docstrings.
3. **Card 6 claim is stale (behavior equivalent).** `_get_debt_summary` now calls the narrow
   `savings_dashboard_service.compute_debt_summary(user_id)` (`dashboard_service.py:600`,
   commit 0e27664), not `compute_dashboard_data()["debt_summary"]`. Equivalence was
   dict-equality tested; both paths share `_debt_summary_with_dti`. Verdict unchanged.

### Seeds confirmed at current line numbers

- Spending Comparison misdirect: `dashboard.html:119-122`.
- Alert links: all four append sites hardcode `"link": "/"` (`dashboard_service.py:331`,
  `:342`, `:359`, `:375`), and "/" IS the dashboard -- the link is circular. Root cause is
  structural: the service is Flask-free, so the type-to-URL mapping belongs in the route or
  template.
- Balance figure vs caption: the headline is the projected END-of-current-period balance
  (`dashboard_service.py:403-408`, via the `balance_resolver.balances_for` end-balance map)
  while the caption is the anchor true-up timestamp (`:410`, `:425`;
  `_balance_runway.html:13-16`). An as-of-today producer exists
  (`balance_resolver.balance_as_of_date`, used by the calendar) but the dashboard does not
  call it.
- Staleness hardcode: `dashboard_service.py:411` `staleness_days = 14` (its "caller can
  override" comment is false) vs the settings-driven `:322`. Refinement: the flag it feeds,
  `anchor_is_stale`, has ZERO consumers (no template, JS, or test reads it), so the
  split-brain is latent dead output, not a rendered contradiction.

### New findings (not in the original audit)

High severity:

- **Card 5: income-relative goals render "$0.00" target and a permanent 0%.** For that goal
  mode `target_amount` is NULL by design; `dashboard_service.py:556` does
  `goal.target_amount or _ZERO` without calling `resolve_goal_target`, while /savings
  resolves it correctly (`savings_dashboard_service/_goals.py:105-111`). The agreement
  comment at `_goals.py:121-125` is false for this whole goal mode.
- **Card 7 / Card 3: settled transfer-out shadows count as spending.**
  `_sum_settled_expenses` (`dashboard_service.py:667-680`) has no `transfer_id` exclusion, so
  a settled checking-to-savings transfer inflates "This Period" spending, can flip the
  delta/direction, and pollutes cash runway (`:452-466`, same inclusion). Whether transfers
  count as spending or runway outflow is a product decision; today it is implicit and
  undisclosed.

Medium severity:

- **Card 4: payday net pay ignores the salary calibration override.**
  `dashboard_service.py:531-534` calls `calculate_paycheck` without `calibration=`, while the
  recurrence engine (`recurrence_engine.py:767-772`), the salary breakdown view, and the
  regenerate helper all pass it. A calibrated user's dashboard disagrees with the grid's
  stored paycheck for the same period.
- **Card 3: anchor-editor cancel strands the card on a grid partial.** Cancel/Escape in the
  swapped-in anchor form GETs `accounts.anchor_display`, which renders the GRID display cell:
  raw whole-dollar anchor, grid styling, no account name/caption/runway, until full reload.
- **Card 5: balance-basis disagreement with /savings.** Dashboard uses the raw stored
  `current_anchor_balance` (`dashboard_service.py:555`); /savings routes the goal account
  through `balance_resolver.balances_for` (entries-aware, current-period).
- **Card 5: goal-to-account 1:1 is convention only.** The only unique constraint is
  `(user_id, account_id, name)`; nothing prevents N goals on one account, and there is no
  per-goal earmarking, so two goals on one account each count the full balance (double
  counting) on every progress surface. The codebase knows how to enforce 1:1
  (`loan_params`/`investment_params` use `unique=True`); this table deliberately does not.
- **Out of dashboard scope -- pay-period overlap guard hole.** `generate_pay_periods`'s
  forward-only guard compares START dates only (`pay_period_service.py:90-92`), so a batch
  starting within the final existing period is accepted, creating overlapping periods and a
  nondeterministic `get_current_period` (unordered `.first()`). This is the only reachable
  condition under which the bills and payday cards disagree about "next period" (Card 4's
  boundary worry is otherwise unreachable on generator-produced schedules).
- **Both partial endpoints run the full `compute_dashboard_data` to render one partial**
  (`routes/dashboard.py:45`, `:64`) -- including the deliberately-deferred heavy debt import
  chain -- and `balance_section` is on the live `balanceChanged` path.

Low severity (recorded for the fix list): dead `is_paid` branches and mark-paid residue
(see correction 2); `|abs` on money in `_bill_row.html:35` and on the delta in
`_spending_comparison.html`; an already-negative CURRENT balance produces only the
low-balance warning, never the danger alert (`dashboard_service.py:348-349` skips the current
period); a NULL `low_balance_threshold` 500s the dashboard via `Decimal("None")`
(unreachable through the app UI; root cause the nullable column `user.py:255`); the "as of"
caption uses the raw TIMESTAMPTZ day without UTC normalization (unlike
`balance_resolver.py:241`) and omits the year; runway divides a 31-day inclusive window by
30 and silently excludes NULL-due-date expenses; `_sum_settled_expenses` returns int 0 for
an empty period despite its `-> Decimal` annotation; `has_default_account` is a misnomer
(the resolver falls through to ANY active account, so the checking-specific copy at
`dashboard.html:16-17` and `dashboard_service.py:329` can mislabel); the debt partial's
"No debt accounts" else-branch is unreachable (double gate), so a debt-free user gets no
card at all; the dti_label badge-class mapping is duplicated with
`savings/dashboard.html:79-85`; negative balances render as "$-1,234.56" in the savings
caption; the balance display's `role="button" tabindex="0"` has no Enter handler.

Developer-reported, root-caused and FIXED 2026-06-12: bill rows overflowed the card edge on
mobile (long names pushed the amount cell out of the card). Root cause: `_bill_row.html`'s
name column carried `min-width-0`, a class defined NOWHERE (Bootstrap 5 ships no such
utility), so the flex child kept `min-width: auto` and could never shrink to let
`.text-truncate` work. Fixed by swapping in the repo's existing `flex-1-min-0` utility
(`utilities.css`); verified by screenshot in both themes and both viewports (mobile names
now truncate with an ellipsis, desktop unchanged).

### Verified refresh-event map (for the rebuild's wiring decisions)

The app's full server event vocabulary is exactly three events: `balanceChanged` (13 fire
sites: transaction/transfer/entry CRUD, mark-done, anchor true-up), `gridRefresh` (10 fire
sites; listeners full-page reload), and `mobileCardSettled`. The natural rewire is
`#bills-section` listening to `balanceChanged` (every firer changes what bills displays);
Spending Comparison needs a real endpoint or removal; the phantom `dashboardRefresh`
listeners and the dead `dashboard.js` should be deleted. Cards with no refresh wiring at
all: Alerts, Payday, Savings, Debt.

Paths checked and found clean: the no-scenario empty-dashboard branch, both non-HX request
guards, companion access (404 per the not-yours rule), the MFA nag (app-wide via
`base.html`, not dashboard-scoped; 10 tests), and every out-of-card link target.

Live confirmation (2026-06-12, authenticated Playwright shot of the dev dashboard): the
income-relative goal bug renders exactly as predicted ("Emergency Fund -- $4,875.26 / $0.00
-- 0%"), and the negative-projection alert shows the circular "View details" link. The shot
also exposed a finding no static pass caught: **Row 1 renders Alerts BEFORE Bills on
desktop**, contradicting the layout comment at `dashboard.html:31-36`. The comment assumes
Alerts keeps an implicit `order-lg-2` by source position, but elements without an order
class default to order 0, which beats the Bills column's `order-lg-1` at the lg breakpoint.
Net effect: Bills is first on mobile (`order-first`) but second on desktop -- the opposite
of the comment's stated intent. Low severity (the rebuild redoes the layout), recorded so
the Loop A directions know the current desktop order is accidental.

## Rebuild decisions (Gate A, locked 2026-06-12)

The developer ruled per card from the re-verified audit above. These are locked product
decisions; do not revisit without a new developer ruling.

| # | Card | Decision |
| - | ---- | -------- |
| 1 | Upcoming Bills | Keep + fix: disclose/group the two periods shown; rewire refresh to `balanceChanged`; strip the mark-paid residue |
| 2 | Alerts | Keep + fix: per-type destinations mapped in the route/template layer (the service stays Flask-free): stale or never-set anchor goes to the anchor flow, negative projection to the grid at the offending period, low balance to the grid |
| 3 | Balance + Runway | Keep + fix: the headline becomes the as-of-today balance via `balance_resolver.balance_as_of_date`; the anchor true-up date becomes a secondary line; runway starts from today's balance; fix the cancel-path stranding; staleness is owned by the Alerts card via settings, so the dead `anchor_is_stale` flag and its hardcoded 14 are deleted |
| 4 | Next Payday | Keep + fix: pass the salary calibration override so the figure matches the grid's stored paycheck |
| 5 | Savings Goals | Keep + fix: reuse the /savings producers (resolved target + resolver balance) so both screens agree; the 1:1-vs-allocation design question is DEFERRED (it spans /savings) |
| 6 | Debt Summary | Keep; cleanups only (unreachable else-branch, badge-mapping duplication noted) |
| 7 | Spending Comparison | REMOVE: card, partial, service producer, and wiring; period-over-period spending trend is analytics territory |

Cross-cutting decisions, same day:

- **Transfer semantics:** settled transfer shadows are EXCLUDED from any spending /
  consumption figure and INCLUDED in cash-runway outflow (runway measures checking
  depletion; a sweep to savings genuinely drains checking).
- **Pay-period overlap guard:** fixing the start-date-only guard hole in
  `generate_pay_periods` is approved for the same data-fix phase, although it lives outside
  the dashboard (explicit scope grant).
- **Refresh wiring:** the phantom `dashboardRefresh` listeners and the dead `dashboard.js`
  are deleted; the bills section listens to `balanceChanged from:body`.

## Status after the data pass (2026-06-12)

The Gate A data-correctness pass SHIPPED to `dev` (commits `308b49f` pay-period guard,
`0ef7ba6` dashboard; full suite 6063 passed, pylint 10.00/10; every fix live-verified against
the dev app, including the anchor-editor cancel/Escape/409 paths and the mobile bill-row
overflow). Read the defect descriptions in the sections above as HISTORICAL: everything in
the re-verification section is fixed except the items explicitly deferred, which carry
forward into the UX/IA pass or later work:

- Caption wording on the Balance card: "as of today" is accurate under the app's reservation
  semantics (it equals the grid's current-period figure), but the UX pass may sharpen the
  phrasing.
- Row 1 order-class regression (Alerts before Bills on desktop) -- the rebuild redoes the
  layout anyway.
- The balance display's `role="button"` with no Enter handler (Loop B interaction work).
- `has_default_account` misnomer + checking-specific copy on a possibly non-checking account.
- Nullable `low_balance_threshold` column (needs a migration; unreachable via the app UI).
- `dti_label` badge-class mapping duplicated between `_debt_summary.html` and
  `savings/dashboard.html` (candidate for a shared macro during the visual rebuild).
- Negative balances render "$-1,234.56" in the savings caption (shared currency-macro
  candidate).
- Savings goal 1:1-vs-allocation design question (deferred; spans /savings).

## UX/IA pass (step 4) -- Gate B, locked 2026-06-12

Step 4 of the dashboard playbook: content architecture for the six surviving cards -- what each
surface shows, grouping, order, links, and the refresh contract. Loop A (step 5) owns the visual
form; nothing here picks a layout style. Grounded in: the post-data-pass code (dev `4ee2f9e`)
read in full; live authenticated shots of the dev dashboard (desktop and mobile, both themes);
the grid's locked vocabulary (`grid_audit.md` rebuild decisions); a cross-screen sweep of which
screen answers which money question; and a four-lens adversarial review of this proposal
(design-language fidelity, completeness, technical feasibility, scope discipline).

### Gate B rulings (developer, 2026-06-12)

B1 approved. B2 approved. B3 approved. B4a: REMAINING basis (entry-tracked rows contribute
remaining-after-entries, floored at zero). B4b: INCLUDE transfer-out shadows in the still-due
totals. B5 approved. B6 approved. B7 approved in full after a plain-language walkthrough (all
four sub-items: account-name-driven copy with a neutral no-account empty state; balance caption
consolidation with all three lines' information kept; the shared currency macro with -$1,234.56
negatives; Enter/Space activation on the click-to-edit balance). These are locked product
decisions; do not revisit without a new developer ruling.

Developer framing recorded with the ruling (Loop A must honor this): the dashboard in its
current state has never been useful -- largely the now-fixed broken data, partly because the
information was not helpful. The Upcoming Bills card was too big and did not provide enough
information to make budgetary decisions. Transactional edits happen on /grid. The developer
considered removing the dashboard entirely and making the grid the homepage (the original
design). The dashboard's purpose, in their words: "a quick easy to read health check of my
finances." Implications for Loop A: the pulse strip and the still-due totals are the
health-check bet; the bills list should get materially denser than today (compact,
information-rich rows, not a tall sparse list); and the grid-as-homepage question stays open
as the fallback if the rebuilt dashboard does not earn daily use.

### The screen's job

The product exists so one person can answer: do I have enough money, what is due before my next
paycheck, and where is my projection heading (design language, Purpose). Those are product
questions, and the grid is where they are RESOLVED. The dashboard is the read-only summary
surface that answers them at a glance and routes to the screen that resolves each. Two of the
three get no summary answer today: "due before my next paycheck" has a list but no number
anywhere on the screen, and "where is it heading" is answered only by exception (the
negative-projection alert), so "nothing is wrong" is indistinguishable from "nobody computed
it."

### What the live shots show (dev data, 2026-06-12)

- The NOW answer (the as-of-today balance) renders in row 2 on desktop and below sixteen bill
  rows (~2,000 px down) on mobile. The page's largest visual mass sits above its most important
  figure.
- Desktop row 1 order is accidental: Alerts has no order class (defaults to 0) while Bills
  carries `order-lg-1`, so Alerts renders first and the comment at `dashboard.html:31-36`
  describes the opposite. The left column is mostly dead gap below the short Alerts card.
- Mobile bills-first IS deliberate (the mobile v3 plan, Commit 22 sub-problem 1); B1 below
  proposes superseding it, disclosed as such.
- The six cards have two tempos but are laid out as peers: Balance, Bills, and Alerts move with
  every transaction; Payday moves daily (days-until) and on salary or pay-period edits (all
  full navigations); Savings and Debt move per paycheck or slower and duplicate /savings, which
  renders the same producers with more detail.
- Savings spends a half-width card on one progress bar; Debt renders alone with empty page
  background to its right; neither links anywhere in its populated state.
- Alerts never refresh (no wiring) while the Balance card refreshes live on `balanceChanged`,
  and the low-balance alert message embeds a dollar figure, so the card and the alert beside it
  can quote two different balances until a full reload.

### The organizing idea: the pulse timeline

The high-tempo surfaces read as a timeline: NOW (balance as of today, runway) -> BETWEEN NOW
AND PAYDAY (bills still due) -> PAYDAY (in N days, net) -> HORIZON (projected period-end
balances ahead, with an alert line for anything wrong). This is the E2 "horizon strip plus
alert line" carried from the grid's Loop A (the leading candidate recorded in `grid_audit.md`
rebuild decisions item 7 and the overhaul plan; the E2 mockup itself was deliberately
disposable, so the concept is re-derived here from the dashboard's own needs). The slow
surfaces (Savings, Debt) form a standing-position tier below the bills.

### One question per surface

| Surface | Question | Figure that answers it |
| ------- | -------- | ---------------------- |
| Balance + runway | Do I have enough money right now? | $ as of today, ~N days runway |
| Upcoming bills | What must still be paid this period (and next)? | still-due totals (NEW, B4) |
| Next payday | When is relief, and how big? | days until, net $ |
| Alerts | Is anything wrong ahead? | the alert line, danger first |
| Horizon (NEW, B3) | Where is the projection heading? | next N period-end balances |
| Savings goals | Am I on track on my goals? | % progress per goal |
| Debt | Where does my debt stand? | total debt, monthly payments, DTI, debt-free date |

### Proposed rulings (Gate B)

**B1 -- page structure and order, both viewports: pulse strip, then Bills, then position
tier.** One source order, no order classes (the desktop accident disappears structurally). On
mobile this supersedes the deliberate bills-first ruling from the mobile v3 plan (Commit 22):
the strip is one short band, so bills remain immediately below it instead of pushing the NOW
answer ~2,000 px down. The "Open Grid" header button stays as the global route to the working
surface (B5's period links do not replace it). RECOMMEND: yes.

**B2 -- consolidate Balance, Payday, and Alerts into one pulse-strip surface** with a single
HTMX region refreshing on `balanceChanged from:body`. Content carried over in full:

- Balance slot: the as-of-today figure (click-to-edit anchor affordance retained, including
  the `revert=dashboard` cancel path); account name; "as of today" caption; the Gate A-locked
  "last updated <date>" secondary line; the runway line including its "N/A" zero-spend state.
- Payday slot: days-until, date, projected net; the "Set up salary" and "No upcoming pay
  periods" fallbacks. No populated-state CTA (payday resolves nothing; adding a link would be
  speculative).
- Alert line: keeps the app alert anatomy (severity icon + message + per-type link), danger
  first, compact; all-clear collapses to one quiet line. Live refresh closes the stale-alert
  window noted above.
- Degraded-state composition: each slot renders its own fallback line independently (the
  no-scenario branch renders all fallbacks at once today and keeps doing so inside the strip);
  "No balance data available" stays the balance slot's no-scenario state.

Refresh cost, stated deliberately: the data pass narrowed the `balanceChanged` producers
(fix H) so partials did not recompute figures they do not render. The strip RENDERS alerts,
payday, and the horizon, so its producer legitimately recomputes them: one full anchor-forward
`balances_for` walk plus one `calculate_paycheck` per event -- the same work one page load
already does. Request fan-out per event is unchanged (two: strip + bills). The strip producer
and endpoint are NEW code (Opus scope). Alternative: keep three separate cards, reordered.
RECOMMEND: consolidate.

**B3 -- horizon element (content ruling only): the strip shows the next N projected period-end
balances**, so "where is it heading" is answered affirmatively, not only by exception. Negative
period-ends use `--shekel-danger` paired with a non-color signal; tabular numerals. N = 6
(about one quarter at biweekly periods) recommended; fewer render when fewer exist; Loop A may
tune N between 4 and 8 for density. Numeric run vs sparkline is a Loop A direction-gate choice
(Chart.js is already vendored app-wide, so neither option adds a dependency). The walk shares
B2's producer cost. Alternative: no horizon, alerts only (rejected by this proposal: it leaves
the heading question exception-only). RECOMMEND: yes, content as stated.

**B4 -- the Bills card gets hero figures: per-period "still due" totals** (card headline = the
current period's total; each group header carries its period's total). Computed in the producer
from the already-loaded rows; no new queries. Two financial-semantics sub-rulings, explicit per
the transfer-decision precedent:

- B4a, basis for entry-tracked rows: (i) REMAINING basis -- a tracked row contributes
  `entry_remaining` floored at zero (its recorded entries have already left the as-of-today
  balance in the same strip, so balance minus still-due composes without double counting);
  caption "still due". (ii) ESTIMATED basis -- rows contribute the full `estimated_amount`,
  matching the row's displayed dual amount; the caption must then read "budgeted", not "still
  due" (principle 2). RECOMMEND: (i) remaining basis.
- B4b, transfer shadows: whether the listed transfer-out rows count toward the totals. The
  Gate A transfer ruling classifies spending figures (exclude) and runway outflow (include); a
  still-due total is an obligation / checking-depletion figure, and the list it sums already
  discloses transfers with an icon. RECOMMEND: include, disclosed as today.
- Zero states: a current period with no remaining bills shows the all-clear line with $0.00
  while next-period bills render; the card's "No upcoming bills" empty state stays for the
  both-periods-clear case.
- Row contents unchanged: status icons (past-due, due-soon, transfer), category sub-line, due
  date (desktop-only as today), entry dual amount + tooltip, the "budget" base label. The new
  totals carry a basis label in the UI (the E-21 disclosure pattern).

**B5 -- bills navigation: group headers link to the grid at that period** (`grid.index?offset=0`
and `?offset=1`, the link vocabulary the negative-projection alert already uses). Per-row links
are NOT proposed: every row in a group would resolve to the same URL as its header, and no
recorded need exists (revisit only if mobile wants whole-row tap targets). The dashboard stays
read-only: no transaction-status mutation; the anchor true-up affordance is the sanctioned
exception. RECOMMEND: header links yes, row links no.

**B6 -- position tier: Savings and Debt share a compact bottom row** (both KEEP per Gate A).
Kept per-goal figures: name, $current / $target, progress bar with its ARIA attributes,
percent, account-name caption. Kept debt figures: Total Debt, Monthly Payments, DTI ratio +
badge, Debt-Free Date. Card titles link to `savings.dashboard` in the populated state (today
neither card links anywhere when populated; the grounding is the screen's routing job, not
principle 4, which only constrains CTAs that exist). No live refresh, deliberately: no
`balanceChanged` emitter reachable without leaving the dashboard moves these figures (the only
on-dashboard mutation, the anchor true-up, touches neither), and off-dashboard changes return
via full navigation. The tier degrades when debt is absent (savings widens; no empty debt
card). Shared dti-badge and currency macros land in Loop B. RECOMMEND: yes.

**B7 -- copy and consistency fold-ins** (decided here; built in Loop B; service-side copy is
Opus scope):

- The no-account empty state gets neutral wording (no account exists to name); alert messages
  and the balance card become account-name-driven, replacing hardcoded "checking" (the
  `has_default_account` misnomer family).
- Balance caption stack, named fates: account name KEPT (may merge onto one line with "as of
  today"); "as of today" KEPT (wording may sharpen); "last updated <date>" KEPT (Gate A-locked
  staleness affordance).
- A shared currency macro renders negatives as -$1,234.56 (today: $-1,234.56) and replaces the
  per-template `"{:,.2f}".format` duplication at every dashboard money site.
- Enter/Space activate the click-to-edit balance (the `role="button"` keyboard gap).
- The mobile over-budget bill-row truncation ("Kayla'..." at 390 px) moves to the Loop A/B fix
  list (visual-form work, not content).

RECOMMEND: yes.

### Refresh contract (the deliberate wiring story)

| Region | Trigger | Producer |
| ------ | ------- | -------- |
| Pulse strip (balance, payday, alerts, horizon) | `balanceChanged from:body` | new narrow strip producer (Opus scope) |
| Bills | `balanceChanged from:body` | `compute_bills_section` (exists) |
| Position tier (savings, debt) | page load only | /savings producers (exist) |

### Loop B engineering notes (recorded now so the build does not rediscover them)

- The strip endpoint must resolve alert links via `_resolve_alert_links` (today
  `dashboard.page` is the only resolving route).
- The anchor-edit flow couples to the strip at three points: `_anchor_revert_url` maps
  "dashboard" to `dashboard.balance_section`, whose markup must match the strip's balance
  slot; the PATCH success response renders the grid display cell and relies on an enclosing
  `balanceChanged` listener whose swap target must contain the editor; and the success
  response's OOB snippet targets `id="anchor-as-of"`, which the strip must keep (or the
  mapping must change in the same pass).
- `balance_as_of_date` builds its own all-period override map internally; the strip producer
  runs that walk and `balances_for` roughly twice per refresh unless the signature grows a
  shared-overrides path. Acceptable at current scale; note for the producer's design.

### Noted, not ruled (developer awareness)

- Payday divergence: an `is_override` edit on the next paycheck transaction changes the grid's
  figure but not the dashboard's payday net, which recomputes from the salary profile.
  Pre-existing, unchanged by this pass. Options if it should close: read the stored paycheck
  transaction for the next period, or accept profile-derived as the dashboard's definition.
- Bill due dates stay desktop-only (`d-none d-md-inline`), as today.

### Explicitly out of scope

- New decision-support figures from the cross-screen sweep (savings burn rate, contribution
  headroom, cross-account rollups, and similar): new features, not this rebuild.
- The savings goal 1:1-vs-allocation question (deferred at Gate A; spans /savings).
- The nullable `low_balance_threshold` column: re-deferred to migration work (not UX/IA).
- The MFA nag: app-wide chrome from `base.html`, untouched (its partial merely lives in the
  dashboard template directory).

## Data-value pass (Gate B amendments, locked 2026-06-12)

Triggered by the developer's Loop A round-1 ruling, before any direction was picked: the three
round-1 directions read "about the same" (all dense ledgers -- an anchoring artifact), the
payday figures were called out as near-useless (salaried, constant paycheck; every period
boundary IS a payday), and the full bills list was questioned. The developer asked for a
data-value analysis before more mockups. These amendments supersede the matching parts of
B2/B3/B4 above and Gate A card 3's runway wording; everything else in Gate B stands. The two
values the developer tracks most: STILL DUE and PROJECTED END BALANCE.

Verified financial fact the new model rests on (`balance_resolver.py:787-`, read in full):
`balance_as_of_date` uses reservation semantics -- mid-period it returns the anchor rolled
forward plus the WHOLE current period's projected net (only entries dated after `as_of` are
excluded from the entry-aware reduction). The dashboard headline is therefore, to within
entry-timing noise, the CURRENT period's projected end balance, and headline + still-due is
approximately the cash physically in checking. The "as of today" caption was the defect, not
the figure.

The dashboard tells one story in one unit (projected end balance) along one axis (time):

- **Tier 1, health check:** hero = this period's projected end balance (same producer; caption
  reframed, e.g. "projected through Jun 17"; click-to-edit anchor + "last updated" line kept;
  one-line "next paycheck <date>" caption). Beside it, the still-due-this-period total (B4a
  remaining basis and B4b transfer inclusion unchanged). The projected end-balance CHART
  (supersedes B3's numeric mini-strip): 13 periods / ~6 months (the developer's normal grid
  timeframe -- "short range enough to be fairly accurate but far enough out to catch big
  expenses"), zero line emphasized, `low_balance_threshold` drawable as a faint line, negatives
  in danger color paired with a marker. Trough stat as the chart caption ("lowest point: $X on
  <date>"), scanned over the SAME full horizon the negative-projection alert walks: the trough
  is the minimum, the alert is the first negative; both labeled, the dates may differ. The
  chart's FIRST point coincides with the hero by construction (same producer family); round 1's
  mockup data violated this, the build must not. Alert line unchanged.
- **Tier 2, act soon:** "Due soon" REPLACES the full two-period list: the current period's
  unpaid rows only (its overdue rows included; unpaid rows in PRIOR periods stay out of scope
  exactly as they are for the current card -- expanding to them would be a new product
  decision). Row anatomy unchanged (B4). The next period's TOTAL survives as a one-line stat
  with its grid link ("Next period: $X still due"); its rows live on the grid.
- **Tier 3, position:** unchanged (B6).

Removed (explicit removals per principle 3):

- **Cash runway** -- superseded by the chart + still-due in the developer's period-based model
  (runway was a calendar heuristic over the last 30 days of settled spend). Amends Gate A
  card 3's "runway starts from today's balance."
- **The payday card** -- the days-until hero and projected net pay are both dropped (salaried;
  constant paycheck; "nice to have but I wouldn't miss it"). Survives only as the one-line
  "next paycheck <date>" caption. This also moots the earlier "Noted, not ruled" payday
  `is_override` divergence: the net figure no longer renders on the dashboard.
- **The full two-period bills list** (see Tier 2; both period totals survive).

Rulings (developer, 2026-06-12): chart horizon ~6 months / 13 periods; due soon = overdue +
rest of current period; runway dropped; payday = one-line caption only; design language
Differentiation section re-scoped same day (the quiet-dense-ledger aesthetic belongs to the
GRID; other screens use the presentation that serves the glance, charts first; the principles
bind everywhere).

Build notes: the chart is Chart.js (already vendored; theme via the `chart_theme.js` factory;
data passed via `data-*`, floats only at the serialization boundary); the Gate B refresh
contract carries over (one pulse region on `balanceChanged`, the chart re-rendered from the
swapped data attributes).

Also considered and left off (the developer saw the option space): net worth rollup and
emergency-fund coverage months (/savings owns both), the /obligations committed-outflow rollup,
spending trends (analytics, per Gate A), an over-budget-envelope callout (revisit if the
due-soon list hides it).

## Loop A direction gate (2026-06-12)

Round 2 ran chart-forward directions D "Chart Hero" / E "Cockpit" / F "Focus Column" on the
amended content model. Developer rulings:

- **Direction: D "Chart Hero" chosen** -- one leading panel (stat band: end-of-period hero,
  still due, lowest point ahead; the 6-month chart filling the panel), Due Soon and Position
  below. The panel is the single `balanceChanged` refresh region.
- **The alert banner is DROPPED.** The conditions it carried move into the panel itself:
  negative projection and low balance are shown by the chart (danger dip + dashed threshold
  line) and the trough stat; the trough stat's date links to the grid at that period
  (replacing the negative-projection "View details" link); anchor staleness moves into the
  "last updated" caption, which turns warning-colored with an icon when older than
  `settings.anchor_staleness_days` (the figure stays the click-to-edit anchor affordance, so
  the stale state sits on the control that fixes it). With no remaining consumer,
  `_compute_alerts` and the route-layer link mapping retire in Loop B (dead code once the
  banner is gone). The never-set-anchor and no-scenario degraded states keep their slot
  fallbacks from B2.
- **Position tier must be revamped, dropped, or replaced**: the savings/debt cards are
  "replicas of cards on /savings" that do not let a quick glance answer "am I good / on
  track." Verified before round 3 (rule 3): the /savings producers already compute the
  verdict -- `calculate_trajectory` returns `pace` (ahead / on_track / behind),
  `projected_completion_date`, and `required_monthly` per goal (`savings_goal_service.py:284-`,
  surfaced via `_goals.py:_build_goal_datum`), and debt carries `dti_label` + the debt-free
  date. Round 3 mocks verdict-first treatments (verdict rows / verdict chips / dropped) for
  the developer to pick; pace colors: ahead and on_track use `--shekel-done`, behind uses
  `--shekel-credit` (proportionate urgency: danger stays reserved for negative balance).
- **Round 3 ruling (developer, 2026-06-12): position becomes CHARTS, not text** -- "the
  dashboard should have less text and more visualizations; I can always click through to get
  the details." Round 4 mocks the position tier as two mini trajectory charts in the main
  chart's visual language: the savings goal as a projection line rising to a dashed target
  line (target-date marker shows pace as a visible gap; verdict pill + one caption line), and
  debt as the amortization payoff curve falling to zero at the debt-free date. Both are
  grounded in existing engines (`calculate_trajectory` arithmetic; the loan amortization walk
  that already produces the debt-free date) -- new narrow producers are Loop B Opus scope.
- **Round 4 ruling (developer, 2026-06-12): L (trajectories band -- main chart, then savings +
  debt mini charts, then Due Soon full width) is the favorite so far.** Developer then asked
  for a deliberate divergence round: three mockups "more radical than anything previously" to
  surface fresh ideas before locking. Round 5 paradigms: M full-bleed chart canvas with
  floating glass panels; N everything-on-one-time-axis (bills as events on the road, savings/
  debt as progress tracks); O bento tile grid. Same locked content model in all three.
- **Round 5 ruling (developer, 2026-06-12): fuse them.** M Terminal "best looking" BUT overlays
  must not cover chart information; N's one-time-axis concept is liked BUT overdue +
  due-before-next-paycheck events cluster at the left of a 6-month axis; O's color-as-meaning
  is liked. Round 6 fusion ("Terminal Road"): full-bleed canvas with a reserved SKY zone (the
  chart plots only in the lower band, so panels can never cover data); the current period gets
  a second time scale -- a full-width zoomed "street" band under the main road, joined by a
  visible magnification bracket, where overdue/due-soon events sit day-by-day; semantic tints
  (danger fill below the zero line and on the trough chip, amber for behind-pace/due-soon,
  red for overdue). Variants differ only in where bill DETAILS live: P street events only,
  Q street + compact two-column list, R street + due-soon glass panel in the sky.

## Rebuild decisions (Loop A COMPLETE, locked 2026-06-12)

**Direction: Q "Terminal Road"** (round 6), chosen by the developer after six mockup rounds
(A-C ledger forms; D-F chart-forward, D chosen; G-J verdict tiers; K-L trajectory charts, L
favored; M-O radical divergence; P-R fusion, Q chosen). The mockups were disposable per the
visual loop; this section is the durable anatomy Loop B builds.

### Page anatomy (top to bottom)

1. **Canvas** (full-bleed band under the standard navbar; the mockup's brand line is replaced
   by the real navbar; the page h1/breadcrumb folds into the sky):
   - **Sky** (reserved top zone; the chart NEVER plots into it): the hero "end of this
     period" figure (click-to-edit anchor affordance with Enter/Space activation and the
     `revert=dashboard` cancel path; the three recorded anchor-edit coupling points apply);
     captions "projected through <period end> -- <account name>" and "last updated <date> --
     next paycheck <date>" (the last-updated fragment turns `--shekel-credit` with an icon
     when older than `settings.anchor_staleness_days` or never set); two chips: "Still due
     this period" (neutral) and "Lowest point ahead" (danger-tinted when negative; its date
     links to `grid.index?offset=N`); the "Open Grid" button.
   - **Chart band**: the 13-period projected end-balance line (Chart.js through the
     `chart_theme.js` factory; data via `data-*`; floats only at the serialization boundary);
     solid zero line; dashed `low_balance_threshold` line in `--shekel-credit`; the
     below-zero pocket filled `--shekel-danger`; negative points get danger dots + a value
     label; period ticks with date labels along the bottom axis.
2. **Bracket + street**: a visible magnification trapezoid from the current-period sliver of
   the main axis down to a full-width **street band** (faint accent wash): a day-by-day axis
   spanning the current period; dated unpaid rows as events (overdue in `--shekel-danger`
   with name + amount + "overdue"/"over budget" caption; upcoming in `--shekel-credit`);
   TODAY as a dashed accent marker; the period-end station on the right carrying the SAME
   figure as the hero (the reservation-semantics identity drawn); undated rows on an
   "anytime this period" shelf. MOBILE RULE: the street does not scale below ~760 px; it
   collapses and the due-soon list (item 3) is the mobile representation.
3. **Due-soon list** (compact; two columns desktop, one mobile): the current period's unpaid
   rows, row anatomy unchanged from Gate B (status icons, category sub-line, due date, entry
   dual amount + tooltip, base label); header carries the still-due total; the next-period
   total line with its grid link closes the section.
4. **Tracks** (savings goals + debt, the position tier): metro-track rows -- name + pace
   pill (`behind` = `--shekel-credit`; `on_track`/`ahead`/`healthy` = `--shekel-done`), a
   rail with progress fill and a you-are-here marker, a target-date tick in
   `--shekel-credit` where a target date exists, destination on the right (target amount or
   $0, plus arrival: projected completion date / debt-free date). Savings basis:
   `progress_pct` + `calculate_trajectory` outputs (pace, projected_completion_date,
   required_monthly -- the latter available as secondary detail). Debt basis (B-1 verified
   `LoanParams.original_principal` exists, non-null, CHECK > 0; loan-set fork ruled by the
   developer 2026-06-12): the marker uses ALL LOANS EVER ORIGINATED -- paid-off loans stay in
   both numerator and denominator (a paid-off loan contributes 0 to the remaining-balance
   sum), so the fraction is MONOTONIC, reaches 1.0 at full payoff and stays there, and never
   jumps backward on a single loan's payoff. None (rail renders without a marker) only when
   the user has no loans at all. Reachable meaning of "all": non-archived loan accounts with
   a LoanParams row (archived accounts are filtered upstream by the projection pipeline).
   The displayed balance label stays active-loans-only (position on the journey vs what
   remains are different questions). Track titles link to `savings.dashboard`.

### Refresh contract

One **pulse region** = canvas + bracket + street + due-soon list (all derive from the same
transaction state): `hx-trigger="balanceChanged from:body"`, served by one new narrow
producer + endpoint; the chart re-renders from the swapped `data-*` via the chart_theme
factory. **Tracks**: page-load only (deliberate; rationale recorded at B6). Fan-out per
event: ONE request (down from two).

### Retirements (all previously ruled; executed in Loop B)

The alerts banner with `_compute_alerts` + the route link mapping; cash runway; the payday
card and its producer (only the next-paycheck DATE survives, inside the hero caption); the
two-period bills list (next-period TOTAL survives); the old `compute_balance_section` /
`compute_bills_section` pairing collapses into the pulse producer.

### Loop B phases (gated; full suite per phase; Opus for services/routes/tests, Fable for
templates/CSS/JS)

- **B-1 (Opus): producers + tests, additive only.** New pulse producer (hero figures,
  staleness flag, next-paycheck date, 13-point chart series + threshold, full-horizon trough
  with grid offset, still-due totals on the locked bases, due-soon rows split dated/undated
  with day offsets) and tracks producer (goal reshape incl. trajectory fields; debt summary +
  honest principal-paid fraction or None). Old producers stay so the live page keeps working.
  Test the hero == first-chart-point identity in the no-post-dated-entries case.
- **B-2 (Fable): templates + CSS + JS** against the new producers (canvas, street SVG/JS,
  tracks, list; currency + dti macros; Enter/Space handler).
- **B-3 (Opus): route swap, pulse endpoint, retirements** (incl. deleting the retired
  producers' tests -- sanctioned removals, not test-gaming), anchor-edit coupling updates.
- **B-4: live verification** (authenticated Playwright, both themes/viewports, mutation
  paths incl. anchor edit cancel/Escape/409) and developer acceptance with real data.
