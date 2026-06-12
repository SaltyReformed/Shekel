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
