# Dashboard Card Audit

Static diagnosis of the summary dashboard (`app/templates/dashboard/`) ahead of the Fable 5
rebuild proof. This is the Step 3 artifact of the overhaul plan: one row per card covering what
it should show, what the code actually produces, the divergence if any, and a usefulness verdict
that feeds the per-card keep / fix / remove gate.

Last evaluated: 2026-06-10.

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
