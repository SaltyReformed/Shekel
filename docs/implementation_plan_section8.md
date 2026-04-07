# Implementation Plan: Section 8 -- Visualization and Reporting Overhaul

**Version:** 1.0
**Date:** April 7, 2026
**Prerequisite:** Sections 5, 5A complete. Section 6 tasks 6.1-6.4 are NOT prerequisites.
Tasks 6.5, 6.6, and 6.7 (computation engines) are built in this section alongside their
display layers.
**Scope:** Bug fix 8.0a, summary dashboard (8.1), financial calendar with annual overview
(8.2/6.6), year-end summary (8.3), budget variance analysis (6.5), spending trend detection
(6.7), CSV export for all analytics views, `/charts` page replacement.

---

## Documentation vs. Code Discrepancies and Open Question Resolution

The following discrepancies and open question resolutions were found by reading the actual
codebase on April 7, 2026. The implementation plan is based on the code, not the documentation.

### D-1: Roadmap prerequisite is overstated -- Section 6 is NOT required

**Roadmap says (line 1467):** "Prerequisite: Sections 5, 5A, and 6 are complete."

**Scope document says:** "Tasks 6.5, 6.6, and 6.7 (computation engines) are pulled into this
section and built alongside their display layers."

**Code says:** Tasks 6.1-6.4 (seasonal forecasting, smart estimates, expense inflation,
deduction inflation) do not exist in the codebase and are not consumed by any Section 8 task.

**Impact:** The scope document is authoritative. Section 6 tasks 6.1-6.4 are NOT prerequisites.
Tasks 6.5, 6.6, and 6.7 are built here. This plan follows the scope document.

### D-2: Roadmap task 8.0b (inaccurate balance values) is not in the scope document

**Roadmap says (line 1485):** Task 8.0b describes inaccurate balance values on the charts page.

**Scope document says:** Task 8.0b is not listed in the task inventory (Section 2) or anywhere
in the scope document.

**Code says:** `chart_data_service.get_balance_over_time()` (line ~260) dispatches correctly
through `_calculate_account_balances()` which handles all account types via the
`has_amortization`/`has_interest` flags on `ref.account_types`. No obvious data pipeline bug
was observed during the code audit.

**Impact:** 8.0b is excluded from this plan per the scope document. If balance value
inaccuracies are observed during implementation, they will be addressed as part of the relevant
commit.

### D-3: Roadmap dashboard description is less detailed than scope document

**Roadmap 8.1 (line 1501):** Lists 7 dashboard items (current balance, days to payday,
upcoming bills, alerts, savings goals, debt summary, quick actions).

**Scope document 4.1.1:** Specifies 7 sections in priority order with detailed interactive
behaviors (mark-as-paid, inline true-up, cash runway, spending comparison).

**Impact:** The scope document is authoritative and more detailed. This plan follows the scope
document's 7-section specification.

### D-4: Notification system (Section 7) does not exist

**Scope document (4.1.1, Section 2):** "If Section 7 (Notifications) is implemented later,
this section can surface notification system alerts."

**Code says:** There is no `system.notification_settings` table, no `system.notifications`
table, and no notification service. The roadmap (line 1334) describes these as stubs "already
stubbed in the schema" but they do not exist in the current codebase.

**Impact:** Dashboard Section 2 (Alerts / Needs Attention) computes alerts directly from
existing data as the scope document specifies. No dependency on Section 7.

### D-5: `chart_data_service.py` line counts have changed since roadmap was written

**Roadmap says:** "chart_data_service.py (720 lines)."

**Code says:** `chart_data_service.py` is 720 lines. This matches. However, several other
files referenced by the roadmap have different line counts (see Codebase Inventory below).

**Impact:** This plan uses current line counts from April 7, 2026.

---

### Open Question Resolutions

### OQ-1: What is the current default route (`/`)?

**Answer:** The default route `/` is served by `grid_bp.route("/")` in
`app/routes/grid.py:133`. Function `grid.index()` renders the full budget grid
(`grid/grid.html`). When the user visits the app root, they see the budget grid with the
current pay period as the leftmost column.

**Impact on plan:** The dashboard (8.1) will replace this as the default route. The grid route
must be re-routed to a non-root URL (e.g., `/grid`). The grid blueprint's `/` mapping moves,
and the new dashboard blueprint claims `/`. The nav bar "Budget" link updates to point to the
grid's new URL.

### OQ-2: Does a debt summary / DTI service exist from task 5.12?

**Answer:** YES. The debt summary and DTI calculation exist in
`app/services/savings_dashboard_service.py`, function `_compute_debt_summary()` (called from
`compute_dashboard_data()`). It returns a dict with keys: `total_debt`,
`total_monthly_payments`, `weighted_avg_rate`, `projected_debt_free_date`, `dti_ratio`,
`dti_label` ("healthy"/"moderate"/"high"), and `gross_monthly_income`. The DTI thresholds are:
< 36% = healthy, 36-43% = moderate, > 43% = high. Escrow is included in monthly totals for
PITI.

**Impact on plan:** Dashboard Section 6 can use this existing debt summary. The dashboard
service calls `savings_dashboard_service.compute_dashboard_data()` or extracts the
`_compute_debt_summary()` logic into a reusable function.

### OQ-3: What is the existing pattern for the grid's "mark as paid" HTMX interaction?

**Answer:** The mark-as-paid interaction is in `app/routes/transactions.py`:

1. **Endpoint:** `POST /transactions/<txn_id>/mark-done` (line 194).
2. **Parameters:** Optional `actual_amount` from form data.
3. **Behavior:** Sets status to `Done` (expense) or `Received` (income). For shadow
   transactions (transfer_id not null), routes through `transfer_service.update_transfer()` to
   keep both shadows and the parent transfer in sync.
4. **Response:** Returns rendered `grid/_transaction_cell.html` with status 200 and
   `HX-Trigger: gridRefresh` header.
5. **Template pattern:** The cell template (`grid/_transaction_cell.html`) uses
   `hx-get="/transactions/<id>/quick-edit"` on click, which renders an inline edit form
   (`grid/_transaction_quick_edit.html`). The form uses `hx-patch="/transactions/<id>"` for
   amount edits. A separate "mark done" button triggers the `mark-done` endpoint.

**Impact on plan:** The dashboard's mark-as-paid action must follow this same endpoint
(`/transactions/<id>/mark-done`) with optional `actual_amount`. The dashboard response can use
a different partial template (a dashboard bill row rather than a grid cell). The `HX-Trigger:
gridRefresh` header causes other listening elements to refresh.

### OQ-4: What is the existing pattern for the grid's anchor balance / true-up interaction?

**Answer:** The true-up interaction is in `app/routes/accounts.py`:

1. **Display mode:** `GET /accounts/<id>/anchor-display` (line 687) returns
   `grid/_anchor_edit.html` with `editing=False`. The display div has
   `hx-get="/accounts/<id>/anchor-form"` on click.
2. **Edit mode:** `GET /accounts/<id>/anchor-form` (line 672) returns
   `grid/_anchor_edit.html` with `editing=True`. Shows input field with escape key handler.
3. **Save:** `PATCH /accounts/<id>/true-up` (line 615) validates via `_anchor_schema`,
   updates `account.current_anchor_balance`, sets `current_anchor_period_id` to current period,
   records in `AccountAnchorHistory`, returns the display partial with
   `HX-Trigger: balanceChanged`.
4. **Cancel:** `hx-get="/accounts/<id>/anchor-display"` on cancel button.

**Impact on plan:** The dashboard's true-up action reuses the same `PATCH /accounts/<id>/true-up`
endpoint. The dashboard renders its own balance display partial that includes the same HTMX
attributes.

### OQ-5: Where is the large transaction threshold best stored?

**Answer:** The `auth.user_settings` table (model: `UserSettings` in `app/models/user.py`)
already stores user-configurable thresholds. Current columns include:
`low_balance_threshold` (Integer, default=500), `default_inflation_rate`,
`grid_default_periods`, `safe_withdrawal_rate`, etc.

**Recommendation:** Add a new column `large_transaction_threshold` (Integer, default=500,
CHECK >= 0) to `auth.user_settings`. This follows the established pattern -- the settings
model is the canonical location for user-configurable numeric thresholds. The settings page
(`app/routes/settings.py`, template `settings/dashboard.html`) already renders and saves
these fields.

### OQ-6: Where is the spending trend alert threshold per category best stored?

**Answer:** There is no per-category settings structure in the codebase. The `budget.categories`
table has only `group_name`, `item_name`, `sort_order`, `is_active`, and `created_at`.

**Recommendation:** Use a single global default threshold stored in `auth.user_settings` as
`trend_alert_threshold` (Numeric(5,4), default=0.1000, CHECK 0 <= value <= 1). Per-category
overrides add complexity with minimal benefit in v1 -- the scope document says "default: 10%
change" and per-category is a nice-to-have. Store the global default first. If per-category
overrides are needed later, add a `trend_threshold_override` column to `budget.categories`.

**OPTION:** If per-category overrides are desired in v1, add a nullable
`trend_threshold_override` (Numeric(5,4), CHECK NULL or 0 < value <= 1) column to
`budget.categories`. When null, the global default applies. This is a one-column migration
with no downstream impact.

### OQ-7: What Chart.js configurations exist across the app that need the 8.0a x-axis fix?

**Answer:** 11 Chart.js chart files exist across the app. The x-axis label format is determined
by the Python service layer (which formats date strings) and by the JS `x.ticks` configuration.

**Charts on the `/charts` page (being removed, but fix applies before removal):**
1. `chart_balance.js` -- x-axis labels from `_format_period_label()`: `"%b %d"` (e.g.,
   "Jan 02"). **No year.** Affected.
2. `chart_net_worth.js` -- same labels source as balance. **No year.** Affected.
3. `chart_net_pay.js` -- labels from paycheck projection. **No year.** Affected.
4. `chart_amortization.js` -- labels from `row.payment_date.strftime("%b %Y")` (e.g.,
   "Jan 2026"). **Has year.** Not affected.
5. `chart_spending.js` -- x-axis is category names, not dates. Not affected.
6. `chart_budget.js` -- x-axis is category names, not dates. Not affected.

**Charts on other pages (persist after `/charts` removal):**
7. `growth_chart.js` (investment dashboard) -- labels from growth projection periods.
   **Needs audit.**
8. `payoff_chart.js` (loan dashboard) -- labels from amortization dates.
   **Needs audit.**
9. `retirement_gap_chart.js` (retirement dashboard) -- labels are income categories, not
   dates. Not affected.
10. `debt_strategy.js` (debt strategy dashboard) -- labels from payoff timeline months.
    **Needs audit.**
11. `chart_slider.js` -- not a chart itself, a date range slider. Not affected.

**Root cause:** `chart_data_service.py:178` function `_format_period_label()` uses
`period.start_date.strftime("%b %d")` which omits the year. This affects charts 1-3 above.
Charts 7, 8, and 10 need their label generation audited in their respective service/route code.

### OQ-8: What is the structure of generated transactions -- do they have a due_date field separate from pay_period assignment?

**Answer:** NO. The `Transaction` model (`app/models/transaction.py`) has NO `due_date` column.
Transactions are assigned to pay periods via `pay_period_id` (FK to `budget.pay_periods`). The
pay period's `start_date` serves as the effective date for that transaction.

The `RecurrenceRule` model (`app/models/recurrence_rule.py`) has a `day_of_month` column (used
by Monthly, Quarterly, Semi-Annual, and Annual patterns) that indicates the calendar day the
bill is due. However, this date is used only by the recurrence engine to determine *which pay
period* the transaction lands in -- it is not stored on the generated transaction.

**Impact on plan:**

- For the financial calendar (8.2), transactions without a `day_of_month` on their recurrence
  rule appear on their pay period's `start_date` (the paycheck date). Transactions whose
  templates have a `day_of_month` can be placed on that calendar day within the month, but
  this requires joining through `Transaction.template -> TransactionTemplate.recurrence_rule
  -> RecurrenceRule.day_of_month`. This join is feasible but adds complexity.
- For budget variance monthly attribution (6.5), transactions are attributed to the month of
  their pay period's `start_date` since there is no explicit due date.
- The scope document's date assignment rules (4.2.1) describe placing transactions "with an
  explicit due date" on that date. Since no due_date column exists, the implementation must
  derive the display date from the template's recurrence rule `day_of_month` when available,
  falling back to the pay period `start_date`.

### OQ-9: What is the current nav bar structure (template file, menu items)?

**Answer:** The nav bar is defined inline in `app/templates/base.html` (lines 42-135), not in
a separate partial. It contains 9 primary nav links:

1. **Budget** -> `grid.index()` -> `/`
2. **Recurring** -> `templates.list_templates()` -> `/templates`
3. **Accounts** -> `savings.dashboard()` -> `/savings`
4. **Salary** -> `salary.list_profiles()` -> `/salary`
5. **Transfers** -> `transfers.list_transfer_templates()` -> `/transfers`
6. **Obligations** -> `obligations.summary()` -> `/obligations`
7. **Retirement** -> `retirement.dashboard()` -> `/retirement`
8. **Charts** -> `charts.dashboard()` -> `/charts`
9. **Settings** -> `settings.show()` -> `/settings`

Plus theme toggle, user display name, and logout button.

**Impact on plan:**
- "Charts" (item 8) is renamed to "Analytics" and pointed at `/analytics`.
- "Budget" (item 1) is pointed at the grid's new URL (e.g., `/grid`).
- A "Dashboard" link may or may not be needed -- since `/` becomes the dashboard, clicking the
  logo or brand link navigates there. Adding an explicit "Dashboard" nav item provides clarity.
  **OPTION:** Add "Dashboard" as item 1, shift "Budget" to item 2. This adds one nav item
  (10 total) but makes the landing page discoverable. Alternatively, the brand/logo link
  serves as the dashboard link with no new nav item.

### OQ-10: What is the full list of files and templates under the current `/charts` route that will be removed?

**Answer:**

**Route file (modified, not deleted -- endpoints replaced):**
- `app/routes/charts.py` (202 lines) -- 7 endpoints to remove.

**Templates to delete (8 files):**
- `app/templates/charts/dashboard.html` (138 lines)
- `app/templates/charts/_balance_over_time.html` (35 lines)
- `app/templates/charts/_spending_category.html` (23 lines)
- `app/templates/charts/_budget_vs_actuals.html` (24 lines)
- `app/templates/charts/_amortization.html` (29 lines)
- `app/templates/charts/_net_worth.html` (12 lines)
- `app/templates/charts/_net_pay.html` (28 lines)
- `app/templates/charts/_error.html` (13 lines)

**JavaScript files to delete (7 files):**
- `app/static/js/chart_balance.js` (113 lines)
- `app/static/js/chart_spending.js` (74 lines)
- `app/static/js/chart_budget.js` (86 lines)
- `app/static/js/chart_amortization.js` (97 lines)
- `app/static/js/chart_net_worth.js` (82 lines)
- `app/static/js/chart_net_pay.js` (91 lines)
- `app/static/js/chart_slider.js` (95 lines)

**Service file (modified, not deleted -- some functions reused):**
- `app/services/chart_data_service.py` (720 lines) -- Functions
  `get_amortization_breakdown()`, `get_net_worth_over_time()`, and `get_net_pay_trajectory()`
  are called by other pages (loan dashboard, retirement dashboard). These functions must be
  preserved or moved. Functions `get_balance_over_time()`, `get_spending_by_category()`, and
  `get_budget_vs_actuals()` are only called by the charts route and can be removed.

**JavaScript files to KEEP (used by other pages):**
- `app/static/js/chart_theme.js` (228 lines) -- theme system, used by all charts.
- `app/static/js/growth_chart.js` -- investment dashboard.
- `app/static/js/payoff_chart.js` -- loan dashboard.
- `app/static/js/retirement_gap_chart.js` -- retirement dashboard.
- `app/static/js/debt_strategy.js` -- debt strategy dashboard.

**Test files (modified):**
- `tests/test_routes/test_charts.py` -- existing chart route tests to update/replace.
- `tests/test_services/test_chart_data_service.py` -- existing service tests to update.

### Additional Discrepancy: Stale anchor detection mechanism

**Scope document (4.1.1, Section 2):** "Stale anchors: accounts that have not been true-up'd
in N days."

**Code says:** The balance calculator already computes a `stale_anchor_warning` boolean
(`app/services/balance_calculator.py:92-109`). This flag is True when the anchor period
contains done/received transactions that were settled after the anchor was set -- meaning the
anchor balance no longer reflects reality. The grid displays this warning
(`grid/grid.html:64`). However, there is no "N days since last true-up" threshold. The
`AccountAnchorHistory` table records each true-up with a `created_at` timestamp, enabling a
staleness check.

**Impact on plan:** The dashboard alerts section can compute anchor staleness by comparing
`MAX(anchor_history.created_at)` against `NOW() - N days`. The threshold N should be stored in
`auth.user_settings` as `anchor_staleness_days` (Integer, default=14, CHECK > 0). This
provides a configurable staleness window.

### Additional Discrepancy: Cash runway service does not exist

**Scope document (4.1.1, Section 3):** "Cash runway: computed using a 30-day rolling average
of daily spending."

**Code says:** No cash runway or daily spending rate service exists. The closest is
`savings_dashboard_service._compute_avg_monthly_expenses()` which computes average monthly
spending from the last 6 periods or template baseline. The savings goal service
(`calculate_savings_metrics()`) computes `months_covered = savings / avg_monthly_expenses`.

**Impact on plan:** The dashboard service computes cash runway as:
`runway_days = current_balance / (avg_daily_spending)` where
`avg_daily_spending = total_paid_expenses_last_30_days / 30`. This is a new computation in the
dashboard service, not a reuse of existing services. The 30-day window uses paid transactions
with actual amounts from the last ~2 pay periods.

---

## Codebase Inventory

Every file that Section 8 tasks will create, modify, or depend on. Built from reading the
actual files on April 7, 2026.

### Models

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/models/user.py` | 123 | User and UserSettings models | 8.0-infra (new settings columns) |
| `app/models/account.py` | 88 | Account model (anchor, default account) | 8.1 depends |
| `app/models/transaction.py` | 158 | Transaction model (effective_amount, status) | 8.1, 8.2, 6.5, 6.7 depends |
| `app/models/transaction_template.py` | 64 | TransactionTemplate (recurrence_rule linkage) | 8.2 depends |
| `app/models/recurrence_rule.py` | 66 | RecurrenceRule (day_of_month, pattern_id) | 8.2 depends |
| `app/models/pay_period.py` | 49 | PayPeriod (start_date, end_date, period_index) | All tasks depend |
| `app/models/category.py` | 41 | Category (group_name, item_name) | 6.5, 6.7 depends |
| `app/models/savings_goal.py` | 100 | SavingsGoal model | 8.1 depends |
| `app/models/loan_params.py` | 80 | LoanParams (for debt summary) | 8.1, 8.3 depends |
| `app/models/transfer.py` | 104 | Transfer model (shadow transactions) | 8.3 depends |
| `app/models/salary_profile.py` | ~80 | SalaryProfile (for paycheck data) | 8.1, 8.3 depends |
| `app/models/__init__.py` | 59 | Model registry | 8.0-infra (if new models) |

### Services (existing, to modify)

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/services/chart_data_service.py` | 720 | Chart data assembly | 8.0a, 8.cleanup (prune unused) |
| `app/services/savings_dashboard_service.py` | 930 | Savings/accounts dashboard data | 8.1 depends (debt summary reuse) |

### Services (existing, read-only dependencies)

| File | Lines | Purpose | Used by |
|------|-------|---------|---------|
| `app/services/balance_calculator.py` | 354 | Balance projection from anchor | 8.1, 8.2, 8.3 |
| `app/services/paycheck_calculator.py` | 462 | Paycheck net pay computation | 8.1, 8.3 |
| `app/services/recurrence_engine.py` | 552 | Transaction generation from templates | 8.2 |
| `app/services/savings_goal_service.py` | 488 | Goal progress calculations | 8.1 |
| `app/services/amortization_engine.py` | 865 | Loan amortization with payment replay | 8.3 |
| `app/services/pay_period_service.py` | 167 | Pay period queries | 8.1, 8.2, 6.5 |
| `app/services/loan_payment_service.py` | 113 | Shadow income on debt accounts | 8.3 |
| `app/services/escrow_calculator.py` | 115 | Mortgage escrow | 8.1 (debt summary) |
| `app/services/tax_config_service.py` | 69 | Tax config loading | 8.3 |
| `app/services/retirement_dashboard_service.py` | 436 | Retirement data | 8.3 (net worth) |
| `app/services/growth_engine.py` | 326 | Investment projections | 8.3 (net worth) |
| `app/services/debt_strategy_service.py` | 703 | Debt payoff strategies | 8.1 (debt summary) |

### Services (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/services/dashboard_service.py` | Dashboard data aggregation | Commit 6 |
| `app/services/calendar_service.py` | Month/year calendar data (6.6 engine) | Commit 3 |
| `app/services/budget_variance_service.py` | Variance analysis engine (6.5) | Commit 4 |
| `app/services/spending_trend_service.py` | Trend detection engine (6.7) | Commit 5 |
| `app/services/year_end_summary_service.py` | Year-end summary aggregation | Commit 10 |
| `app/services/csv_export_service.py` | CSV generation for all analytics views | Commit 14 |

### Routes (existing, to modify)

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/routes/grid.py` | 399 | Budget grid (currently serves `/`) | Commit 8 (re-route) |
| `app/routes/charts.py` | 202 | Charts dashboard (replaced by analytics) | Commit 2, Commit 15 |
| `app/routes/settings.py` | 204 | User settings | Commit 1 (new fields) |
| `app/__init__.py` | 507 | App factory, blueprint registration | Commit 2, 6, 8 |

### Routes (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/routes/dashboard.py` | Summary dashboard route | Commit 6 |
| `app/routes/analytics.py` | Analytics page with tabs (replaces charts) | Commit 2 |

### Schemas

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/schemas/validation.py` | 1281 | Marshmallow validation schemas | Commit 1 (settings), Commit 6 (true-up) |

### Enums and Cache

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/enums.py` | 149 | All enum definitions | -- (no new enums needed) |
| `app/ref_cache.py` | 385 | Enum-to-DB-ID mapping cache | -- |

### Templates (existing, to modify)

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/templates/base.html` | 256 | App shell, nav bar, scripts | Commit 2 (nav), Commit 8 (nav) |
| `app/templates/settings/dashboard.html` | ~200 | Settings page | Commit 1 (new fields) |

### Templates (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/templates/dashboard/dashboard.html` | Main dashboard page | Commit 7 |
| `app/templates/dashboard/_upcoming_bills.html` | Bills section partial | Commit 7 |
| `app/templates/dashboard/_alerts.html` | Alerts section partial | Commit 7 |
| `app/templates/dashboard/_balance_runway.html` | Balance/runway section partial | Commit 7 |
| `app/templates/dashboard/_payday.html` | Next payday section partial | Commit 7 |
| `app/templates/dashboard/_savings_goals.html` | Savings goals section partial | Commit 7 |
| `app/templates/dashboard/_debt_summary.html` | Debt summary section partial | Commit 7 |
| `app/templates/dashboard/_spending_comparison.html` | Period spending comparison partial | Commit 7 |
| `app/templates/dashboard/_bill_row.html` | Single bill row for mark-paid HTMX | Commit 7 |
| `app/templates/dashboard/_mark_paid_form.html` | Mark-paid inline form | Commit 7 |
| `app/templates/dashboard/_true_up_form.html` | True-up inline form | Commit 7 |
| `app/templates/analytics/analytics.html` | Analytics page with tab structure | Commit 2 |
| `app/templates/analytics/_calendar_month.html` | Month detail calendar view | Commit 9 |
| `app/templates/analytics/_calendar_year.html` | Year overview calendar | Commit 9 |
| `app/templates/analytics/_year_end.html` | Year-end summary partial | Commit 11 |
| `app/templates/analytics/_variance.html` | Budget variance partial | Commit 12 |
| `app/templates/analytics/_variance_detail.html` | Variance drill-down partial | Commit 12 |
| `app/templates/analytics/_trends.html` | Spending trends partial | Commit 13 |
| `app/templates/analytics/_trends_detail.html` | Trend drill-down partial | Commit 13 |

### Templates (to delete)

| File | Lines | Deleted by |
|------|-------|------------|
| `app/templates/charts/dashboard.html` | 138 | Commit 15 |
| `app/templates/charts/_balance_over_time.html` | 35 | Commit 15 |
| `app/templates/charts/_spending_category.html` | 23 | Commit 15 |
| `app/templates/charts/_budget_vs_actuals.html` | 24 | Commit 15 |
| `app/templates/charts/_amortization.html` | 29 | Commit 15 |
| `app/templates/charts/_net_worth.html` | 12 | Commit 15 |
| `app/templates/charts/_net_pay.html` | 28 | Commit 15 |
| `app/templates/charts/_error.html` | 13 | Commit 15 |

### Static Assets (existing, to modify)

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/static/js/chart_theme.js` | 228 | CSS custom property theming for Chart.js | 8.0a (x-axis callback) |
| `app/static/css/app.css` | 975 | App-wide custom styles | Commit 7, 9, 11-13 |

### Static Assets (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/static/js/chart_variance.js` | Variance bar chart | Commit 12 |
| `app/static/js/chart_year_end.js` | Year-end net worth line chart | Commit 11 |
| `app/static/js/dashboard.js` | Dashboard interactions (mark-paid, true-up) | Commit 7 |
| `app/static/js/calendar.js` | Calendar tooltip/popover interactions | Commit 9 |

### Static Assets (to delete)

| File | Lines | Deleted by |
|------|-------|------------|
| `app/static/js/chart_balance.js` | 113 | Commit 15 |
| `app/static/js/chart_spending.js` | 74 | Commit 15 |
| `app/static/js/chart_budget.js` | 86 | Commit 15 |
| `app/static/js/chart_amortization.js` | 97 | Commit 15 |
| `app/static/js/chart_net_worth.js` | 82 | Commit 15 |
| `app/static/js/chart_net_pay.js` | 91 | Commit 15 |
| `app/static/js/chart_slider.js` | 95 | Commit 15 |

### Tests (existing, to modify)

| File | Purpose | Affected by |
|------|---------|-------------|
| `tests/test_routes/test_charts.py` | Chart route tests | Commit 15 (replace) |
| `tests/test_services/test_chart_data_service.py` | Chart data service tests | Commit 1 (x-axis), Commit 15 |
| `tests/conftest.py` | Test fixtures | Commit 1 (new settings columns) |

### Tests (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `tests/test_services/test_calendar_service.py` | Calendar engine tests | Commit 3 |
| `tests/test_services/test_budget_variance_service.py` | Variance engine tests | Commit 4 |
| `tests/test_services/test_spending_trend_service.py` | Trend engine tests | Commit 5 |
| `tests/test_services/test_dashboard_service.py` | Dashboard service tests | Commit 6 |
| `tests/test_services/test_year_end_summary_service.py` | Year-end service tests | Commit 10 |
| `tests/test_services/test_csv_export_service.py` | CSV export tests | Commit 14 |
| `tests/test_routes/test_dashboard.py` | Dashboard route tests | Commit 7 |
| `tests/test_routes/test_analytics.py` | Analytics route tests | Commit 2, 9, 11-13 |

### Migrations (new)

| Migration | Purpose | Created by |
|-----------|---------|------------|
| Add settings columns | `large_transaction_threshold`, `trend_alert_threshold`, `anchor_staleness_days` to `auth.user_settings` | Commit 1 |

---

## Task Dependency Analysis

### Dependency Graph

```text
Commit 1 (Settings migration + 8.0a fix)
  |
  +---> Commit 2 (Analytics route shell)
  |       |
  |       +---> Commit 9 (Calendar display) <--- Commit 3 (Calendar engine)
  |       |
  |       +---> Commit 11 (Year-end display) <--- Commit 10 (Year-end engine)
  |       |
  |       +---> Commit 12 (Variance display) <--- Commit 4 (Variance engine)
  |       |
  |       +---> Commit 13 (Trends display) <--- Commit 5 (Trends engine)
  |       |
  |       +---> Commit 14 (CSV export) <--- Commits 9, 11, 12, 13
  |
  +---> Commit 6 (Dashboard service) <--- Commits 3, 4, 5
  |       |
  |       +---> Commit 7 (Dashboard template + interactions)
  |               |
  |               +---> Commit 8 (Default route swap + nav update)
  |
  +---> Commit 15 (Remove old charts, final cleanup) <--- All above
```

### Commit Order Rationale

The ordering follows five principles:

1. **Infrastructure first:** Commit 1 (migration + bug fix) provides the settings columns and
   x-axis fix that other commits depend on.
2. **Shell before content:** Commit 2 creates the analytics route shell with empty tab
   placeholders, establishing the URL structure and nav changes before any tab content is built.
3. **Engine before display:** Commits 3-5 build the computation engines (pure functions with
   full test coverage) before any display work. This matches the Section 5 pattern and ensures
   the data layer is correct before templates consume it.
4. **Dashboard after engines:** Commit 6 aggregates data from existing services and the new
   engines. Commits 7-8 build the dashboard UI and swap the default route.
5. **Cleanup last:** Commit 15 removes old charts only after all replacements are in place.

**Phase 1 -- Infrastructure:** Commits 1-2
**Phase 2 -- Computation Engines:** Commits 3-5
**Phase 3 -- Dashboard:** Commits 6-8
**Phase 4 -- Analytics Display:** Commits 9-13
**Phase 5 -- Export and Cleanup:** Commits 14-15

---

## Commit 1: Settings Migration and X-Axis Date Format Fix (8.0a)

### A. Commit message

```text
fix(charts): add year to x-axis labels crossing year boundary; add Section 8 settings columns
```

### B. Problem statement

Two issues addressed in one commit because the migration and x-axis fix are both small,
independent infrastructure prerequisites:

1. Charts spanning multiple years show x-axis labels like "Jan 02" with no year context. Users
   cannot determine whether a data point is in 2026 or 2027. The fix must detect cross-year
   data and include the year in labels.
2. Section 8 requires three new user settings columns (`large_transaction_threshold`,
   `trend_alert_threshold`, `anchor_staleness_days`) that must exist before the dashboard and
   analytics services can reference them. Adding them now avoids migration dependencies in later
   commits.

### C. Files modified

- `app/services/chart_data_service.py` -- Fix `_format_period_label()` to include year when
  data spans multiple years.
- `app/static/js/chart_theme.js` -- Add a shared x-axis tick callback that detects year
  boundaries and appends the year.
- `app/static/js/growth_chart.js` -- Add year-aware x-axis label callback.
- `app/static/js/payoff_chart.js` -- Add year-aware x-axis label callback.
- `app/static/js/debt_strategy.js` -- Add year-aware x-axis label callback.
- `app/models/user.py` -- Add three new columns to `UserSettings`.
- `app/schemas/validation.py` -- Add validation for new settings fields.
- `app/routes/settings.py` -- Render and save new settings fields.
- `app/templates/settings/dashboard.html` -- Add form fields for new settings.
- `migrations/versions/<auto>.py` -- Alembic migration for new columns.
- `tests/test_services/test_chart_data_service.py` -- Add x-axis label tests.
- `tests/test_routes/test_settings.py` -- Add tests for new settings fields.

### D. Implementation approach

**X-axis fix -- Python side:**

Modify `_format_period_label()` in `chart_data_service.py` (line 170-178) to accept a
`spans_multiple_years` boolean parameter:

```python
def _format_period_label(period: PayPeriod, spans_multiple_years: bool = False) -> str:
    """Format a pay period as a short date label.

    Includes the year when the chart data spans multiple calendar years,
    providing context for cross-year data ranges.
    """
    if spans_multiple_years:
        return period.start_date.strftime("%b %d '%y")  # "Jan 02 '26"
    return period.start_date.strftime("%b %d")  # "Jan 02"
```

In `get_balance_over_time()`, `get_net_worth_over_time()`, and `get_net_pay_trajectory()`,
compute `spans_multiple_years` by comparing the first and last period years:

```python
spans_years = periods[0].start_date.year != periods[-1].start_date.year if periods else False
labels = [_format_period_label(p, spans_years) for p in periods]
```

**X-axis fix -- JavaScript side:**

Add a utility function to `chart_theme.js` that can be used by any chart to append the year
at year boundary ticks:

```javascript
ShekelChart.formatDateLabel = function(labels) {
  // Detect if labels span multiple years by checking for year patterns.
  // Returns a tick callback that appends the year at January labels.
  // Used by charts on non-/charts pages that survive the removal.
};
```

Apply this callback in `growth_chart.js`, `payoff_chart.js`, and `debt_strategy.js` by
adding a `ticks.callback` to the x-axis scale configuration that uses the year-aware
formatter. These charts generate their own labels (not from `_format_period_label`) so the
JS-side fix handles them.

**Settings migration:**

Add three columns to `auth.user_settings`:

```python
large_transaction_threshold = Column(
    Integer, nullable=False, server_default="500",
    info={"check": "ck_user_settings_large_txn_threshold >= 0"},
)
trend_alert_threshold = Column(
    Numeric(5, 4), nullable=False, server_default="0.1000",
    info={"check": "0 <= ck_user_settings_trend_threshold <= 1"},
)
anchor_staleness_days = Column(
    Integer, nullable=False, server_default="14",
    info={"check": "ck_user_settings_staleness_days > 0"},
)
```

Migration includes named CHECK constraints and `server_default` so existing rows get the
defaults without a data migration.

**Settings UI:**

Add three form fields to the settings page in a new "Dashboard & Analytics" section:
- "Large Transaction Threshold ($)" -- integer input, min=0.
- "Spending Trend Alert Threshold (%)" -- integer display (stored as decimal), min=1, max=100.
- "Anchor Staleness Warning (days)" -- integer input, min=1.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-1 | test_format_label_single_year | Period in 2026 | `_format_period_label(p, False)` | "Jan 02" (no year) | New |
| C1-2 | test_format_label_multi_year | Period in 2026 | `_format_period_label(p, True)` | "Jan 02 '26" | New |
| C1-3 | test_balance_labels_single_year | 10 periods in 2026 | `get_balance_over_time()` | Labels have no year suffix | New |
| C1-4 | test_balance_labels_cross_year | 52 periods (2026-2027) | `get_balance_over_time()` | Labels include year suffix | New |
| C1-5 | test_net_worth_labels_cross_year | Periods spanning 2 years | `get_net_worth_over_time()` | Labels include year suffix | New |
| C1-6 | test_save_large_txn_threshold | auth_client | POST /settings with threshold=1000 | Settings saved, value persists | New |
| C1-7 | test_save_trend_threshold | auth_client | POST /settings with threshold=15 | Stored as Decimal("0.1500") | New |
| C1-8 | test_save_staleness_days | auth_client | POST /settings with days=7 | Settings saved, value persists | New |
| C1-9 | test_settings_validation_rejects_negative_threshold | auth_client | POST with threshold=-1 | 422 validation error | New |
| C1-10 | test_settings_validation_rejects_zero_staleness | auth_client | POST with days=0 | 422 validation error | New |

### F. Manual verification steps

1. Navigate to `/charts`. Select an account with 52 periods (2-year view). Verify x-axis
   labels show year (e.g., "Jan 02 '26").
2. Select an account with only current-year periods. Verify labels omit year.
3. Navigate to a loan dashboard with a multi-year amortization chart. Verify x-axis labels
   include years.
4. Navigate to `/settings`. Verify the three new fields appear with defaults (500, 10%, 14).
5. Change values, save, reload. Verify values persist.
6. Verify dark mode renders the new settings fields correctly.

### G. Downstream effects

- The `_format_period_label()` signature change is backward-compatible (default parameter).
- New settings columns have `server_default` so all existing users get defaults automatically.
- No other routes or services are affected.

### H. Rollback notes

Revert the migration with `flask db downgrade`. The three columns are dropped. Revert the
Python and JS changes. No data loss -- the columns use server defaults and contain no
user-entered data yet.

---

## Commit 2: Analytics Route Shell with Tab Structure

### A. Commit message

```text
feat(analytics): create /analytics route with lazy-loaded tab structure
```

### B. Problem statement

The `/analytics` page needs to exist as a structural container before any tab content is built.
This commit creates the route, registers the blueprint, sets up the four-tab HTMX lazy-loading
pattern, and updates the nav bar. Each tab returns a placeholder until its content commit lands.

### C. Files modified

- `app/routes/analytics.py` -- New file: analytics blueprint with 5 endpoints (page + 4 tabs).
- `app/templates/analytics/analytics.html` -- New file: tabbed page extending `base.html`.
- `app/__init__.py` -- Register `analytics_bp` blueprint.
- `app/templates/base.html` -- Rename "Charts" nav item to "Analytics", update URL.
- `tests/test_routes/test_analytics.py` -- New file: route and auth tests.

### D. Implementation approach

**Blueprint (`app/routes/analytics.py`):**

```python
from flask import Blueprint, render_template, redirect, request, url_for
from flask_login import login_required

analytics_bp = Blueprint("analytics", __name__)

@analytics_bp.route("/analytics")
@login_required
def page():
    """Main analytics page with four lazy-loaded tabs."""
    return render_template("analytics/analytics.html")

@analytics_bp.route("/analytics/calendar")
@login_required
def calendar_tab():
    """HTMX partial: calendar tab content."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Calendar -- coming soon.</p>"

@analytics_bp.route("/analytics/year-end")
@login_required
def year_end_tab():
    """HTMX partial: year-end summary tab content."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Year-end summary -- coming soon.</p>"

@analytics_bp.route("/analytics/variance")
@login_required
def variance_tab():
    """HTMX partial: budget variance tab content."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Budget variance -- coming soon.</p>"

@analytics_bp.route("/analytics/trends")
@login_required
def trends_tab():
    """HTMX partial: spending trends tab content."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))
    return "<p class='text-muted'>Spending trends -- coming soon.</p>"
```

**Template (`analytics/analytics.html`):**

Extends `base.html`. Uses Bootstrap 5 nav-pills (not tabs -- pills are more touch-friendly
on mobile per the mobile plan). Each pill triggers an HTMX GET to its endpoint:

```html
{% extends "base.html" %}
{% block content %}
<div class="container-fluid py-3">
  <h1 class="h3 mb-3">Analytics</h1>
  <ul class="nav nav-pills mb-3" role="tablist">
    <li class="nav-item">
      <button class="nav-link active" data-bs-toggle="pill"
              hx-get="{{ url_for('analytics.calendar_tab') }}"
              hx-target="#tab-content" hx-trigger="click, load"
              hx-swap="innerHTML">Calendar</button>
    </li>
    <!-- ... year-end, variance, trends pills ... -->
  </ul>
  <div id="tab-content">
    <div class="d-flex justify-content-center py-5">
      <div class="spinner-border text-secondary" role="status">
        <span class="visually-hidden">Loading...</span>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

Calendar is the default tab (loaded on page load via `hx-trigger="click, load"`).

**Nav bar update:** In `base.html`, change the "Charts" nav item:
- Link text: "Analytics"
- URL: `url_for('analytics.page')`
- Active detection: `request.path.startswith('/analytics')`

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C2-1 | test_analytics_requires_auth | client | GET /analytics | 302 redirect to /login | New |
| C2-2 | test_analytics_page_renders | auth_client | GET /analytics | 200, contains "Analytics" | New |
| C2-3 | test_calendar_tab_htmx | auth_client | GET /analytics/calendar (HX-Request) | 200, placeholder content | New |
| C2-4 | test_calendar_tab_no_htmx | auth_client | GET /analytics/calendar (no HX-Request) | 302 redirect to /analytics | New |
| C2-5 | test_year_end_tab_htmx | auth_client | GET /analytics/year-end (HX-Request) | 200, placeholder content | New |
| C2-6 | test_variance_tab_htmx | auth_client | GET /analytics/variance (HX-Request) | 200, placeholder content | New |
| C2-7 | test_trends_tab_htmx | auth_client | GET /analytics/trends (HX-Request) | 200, placeholder content | New |
| C2-8 | test_nav_shows_analytics | auth_client, seed_user | GET / | Nav contains "Analytics" link | New |

### F. Manual verification steps

1. Navigate to `/analytics`. Verify the page loads with four tab pills.
2. Click each tab. Verify placeholder text loads via HTMX swap.
3. Verify the nav bar shows "Analytics" instead of "Charts".
4. Verify the "Analytics" nav item is highlighted when on `/analytics`.
5. Test at 375px width -- verify pills are readable and do not overflow.
6. Toggle dark mode. Verify tab pills and page render correctly.

### G. Downstream effects

- The `/charts` route still exists and functions. It is not removed until Commit 15.
- The nav bar no longer links to `/charts`. Users who have bookmarked `/charts` can still
  access it directly.

### H. Rollback notes

Remove `analytics_bp` from `__init__.py`, delete new files, revert nav bar change. No
migration, no data impact.

---

## Commit 3: Calendar Service Engine (6.6)

### A. Commit message

```text
feat(calendar): add calendar service for month/year expense aggregation and 3rd paycheck detection
```

### B. Problem statement

The financial calendar (8.2) and year overview (6.6) need a service that groups transactions
by calendar month, computes per-month income/expense/net totals, detects 3rd-paycheck months,
identifies large/infrequent transactions, and computes projected month-end balances. The
recurrence engine and balance calculator already compute the underlying data; this service
reshapes it for calendar display.

### C. Files modified

- `app/services/calendar_service.py` -- New file: calendar data aggregation engine.
- `tests/test_services/test_calendar_service.py` -- New file: full test coverage.

### D. Implementation approach

**New service: `app/services/calendar_service.py`**

Pure function service -- no Flask imports, no database writes.

**Data structures:**

```python
@dataclass(frozen=True)
class DayEntry:
    """A single transaction on a calendar day."""
    transaction_id: int
    name: str
    amount: Decimal  # effective_amount
    is_income: bool
    is_paid: bool  # status is done/received/settled
    is_large: bool  # exceeds threshold
    is_infrequent: bool  # recurrence less frequent than monthly
    category_group: str | None
    category_item: str | None

@dataclass(frozen=True)
class MonthSummary:
    """Aggregated data for one calendar month."""
    year: int
    month: int
    total_income: Decimal
    total_expenses: Decimal
    net: Decimal  # income - expenses
    projected_end_balance: Decimal
    is_third_paycheck_month: bool
    large_transactions: list[DayEntry]
    day_entries: dict[int, list[DayEntry]]  # day_of_month -> entries

@dataclass(frozen=True)
class YearOverview:
    """12-month year overview data."""
    year: int
    months: list[MonthSummary]  # 12 entries, Jan-Dec
```

**Functions:**

```python
def get_month_detail(
    user_id: int,
    year: int,
    month: int,
    account_id: int | None = None,
    large_threshold: int = 500,
) -> MonthSummary:
    """Compute calendar data for a single month.

    Queries transactions for pay periods that overlap the given month.
    Groups by calendar day using the template's recurrence rule day_of_month
    when available, falling back to the pay period start_date.
    """

def get_year_overview(
    user_id: int,
    year: int,
    account_id: int | None = None,
    large_threshold: int = 500,
) -> YearOverview:
    """Compute 12-month overview for a calendar year.

    Calls get_month_detail for each month and assembles into YearOverview.
    """

def _get_display_day(
    transaction: Transaction,
    period: PayPeriod,
) -> int:
    """Determine the calendar day to display a transaction on.

    If the transaction's template has a recurrence rule with day_of_month set
    (Monthly, Quarterly, Semi-Annual, Annual patterns), use that day.
    Otherwise, use the pay period start_date day.
    """

def _is_infrequent(transaction: Transaction) -> bool:
    """Check if a transaction's recurrence is less frequent than monthly.

    Returns True for Quarterly, Semi-Annual, Annual, and Once patterns.
    Returns False for Every Period, Every N Periods, Monthly, Monthly First,
    and transactions with no template.
    """

def _detect_third_paycheck_months(
    periods: list[PayPeriod],
    year: int,
) -> set[int]:
    """Identify months in the given year that contain 3 or more pay period start dates.

    Biweekly pay produces exactly 2 months per year with 3 paychecks.
    """
```

**Transaction date assignment logic:**

For each transaction in the month's pay periods:
1. If `txn.template` exists and `txn.template.recurrence_rule` exists and
   `txn.template.recurrence_rule.day_of_month` is not None:
   - Display on day = `min(day_of_month, last_day_of_month)` (handle Feb 30 -> Feb 28).
   - The month is determined by which calendar month the pay period overlaps.
2. Otherwise: display on `txn.pay_period.start_date.day` (the paycheck date).

**Month-end balance:**

Query the balance calculator for the last pay period ending in or after the month. The
projected end balance of that period approximates the month-end balance. For the year
overview, this provides 12 balance data points.

**Large transaction detection:**

A transaction is "large" if `txn.effective_amount >= large_threshold`. The threshold is
passed in as a parameter (loaded from `user_settings.large_transaction_threshold` by the
caller).

**3rd paycheck detection:**

Count how many pay period `start_date` values fall within each month. Standard biweekly pay
produces 2 months per year with 3 paychecks. These months have extra income.

**Database queries:**

The service queries `Transaction` joined with `PayPeriod`, filtered by:
- `Transaction.account_id == account_id` (or user's default account if None)
- `Transaction.scenario_id == baseline_scenario_id`
- `Transaction.is_deleted.is_(False)`
- `PayPeriod.start_date` within the date range
- Eager load: `Transaction.template -> TransactionTemplate.recurrence_rule`,
  `Transaction.category`, `Transaction.status`

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C3-1 | test_month_detail_empty | User with no transactions | `get_month_detail(uid, 2026, 4)` | MonthSummary with zero totals, empty day_entries | New |
| C3-2 | test_month_detail_income_and_expense | 2 periods in April: $2000 income, $500 rent | `get_month_detail(uid, 2026, 4)` | total_income=4000 (2 checks), total_expenses=500, net=3500 | New |
| C3-3 | test_day_assignment_with_due_date | Template with day_of_month=15 | `get_month_detail` | Transaction appears on day 15 | New |
| C3-4 | test_day_assignment_without_due_date | Template with Every Period pattern | `get_month_detail` | Transaction appears on paycheck day | New |
| C3-5 | test_large_transaction_flagging | Txn of $600, threshold=500 | `get_month_detail(threshold=500)` | DayEntry.is_large=True | New |
| C3-6 | test_infrequent_transaction_flagging | Template with Annual pattern | `_is_infrequent(txn)` | Returns True | New |
| C3-7 | test_monthly_not_infrequent | Template with Monthly pattern | `_is_infrequent(txn)` | Returns False | New |
| C3-8 | test_third_paycheck_detection | 52 periods in 2026 | `_detect_third_paycheck_months(periods, 2026)` | Exactly 2 months flagged | New |
| C3-9 | test_year_overview_12_months | Full year of data | `get_year_overview(uid, 2026)` | 12 MonthSummary entries, totals consistent | New |
| C3-10 | test_year_overview_marks_third_paycheck | 52 periods | `get_year_overview` | is_third_paycheck_month=True for exactly 2 months | New |
| C3-11 | test_month_end_balance | Anchor=1000, income=2000, expenses=1500 per period | `get_month_detail` | projected_end_balance reflects anchor + net | New |
| C3-12 | test_feb_day_of_month_clamping | Template day_of_month=30, February | `_get_display_day` | Returns 28 (or 29 in leap year) | New |

### F. Manual verification steps

No UI in this commit -- verified via tests only.

### G. Downstream effects

None. This is a new standalone service.

### H. Rollback notes

Delete the new files. No migration, no data impact.

---

## Commit 4: Budget Variance Service Engine (6.5)

### A. Commit message

```text
feat(variance): add budget variance analysis service with pay-period, monthly, and annual views
```

### B. Problem statement

The budget variance analysis (6.5) compares estimated vs. actual transaction amounts grouped
by category. Users need to see which categories they consistently overspend or underspend on.
The engine must support three time windows (pay period, month, year) and drill-down from
category group to individual transactions.

### C. Files modified

- `app/services/budget_variance_service.py` -- New file: variance computation engine.
- `tests/test_services/test_budget_variance_service.py` -- New file: full test coverage.

### D. Implementation approach

**New service: `app/services/budget_variance_service.py`**

Pure function service -- no Flask imports, no database writes.

**Data structures:**

```python
@dataclass(frozen=True)
class TransactionVariance:
    """Variance data for a single transaction."""
    transaction_id: int
    name: str
    estimated: Decimal
    actual: Decimal  # actual_amount if paid, estimated_amount if projected
    variance: Decimal  # actual - estimated (positive = over budget)
    variance_pct: Decimal | None  # variance / estimated * 100 (None if estimated is 0)
    is_paid: bool

@dataclass(frozen=True)
class CategoryItemVariance:
    """Variance data for a category item (e.g., "Car Payment")."""
    category_id: int
    group_name: str
    item_name: str
    estimated_total: Decimal
    actual_total: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    transactions: list[TransactionVariance]

@dataclass(frozen=True)
class CategoryGroupVariance:
    """Variance data for a category group (e.g., "Auto")."""
    group_name: str
    estimated_total: Decimal
    actual_total: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    items: list[CategoryItemVariance]

@dataclass(frozen=True)
class VarianceReport:
    """Complete variance report for a time window."""
    window_type: str  # "pay_period" | "month" | "year"
    window_label: str  # e.g., "Jan 02 - Jan 15, 2026" or "January 2026" or "2026"
    groups: list[CategoryGroupVariance]
    total_estimated: Decimal
    total_actual: Decimal
    total_variance: Decimal
```

**Functions:**

```python
def compute_variance(
    user_id: int,
    window_type: str,
    period_id: int | None = None,
    month: int | None = None,
    year: int | None = None,
) -> VarianceReport:
    """Compute budget variance for the given time window.

    Args:
        user_id: The user's ID.
        window_type: One of "pay_period", "month", "year".
        period_id: Required when window_type is "pay_period".
        month: Required (with year) when window_type is "month".
        year: Required when window_type is "month" or "year".

    For each transaction in the window:
    - estimated = transaction.estimated_amount
    - actual = transaction.actual_amount if status is paid/received/settled,
               else transaction.estimated_amount (projected items use estimate as "actual")
    - variance = actual - estimated

    Transactions are grouped by category (group_name -> item_name -> transactions).
    Groups and items are sorted by absolute variance descending (biggest variances first).
    """

def _get_transactions_for_window(
    user_id: int,
    window_type: str,
    period_id: int | None,
    month: int | None,
    year: int | None,
) -> list[Transaction]:
    """Query transactions for the specified time window.

    Filters: baseline scenario, not deleted, excludes_from_balance=False.
    For pay_period: transactions where pay_period_id matches.
    For month: transactions where pay_period.start_date falls in the month.
    For year: transactions where pay_period.start_date falls in the year.
    """
```

**Monthly attribution:** Transactions are attributed to the month of their pay period's
`start_date`, consistent with OQ-8 resolution and scope document section 4.4.2.

**Variance calculation details:**
- For paid/received/settled transactions: `actual = txn.actual_amount or txn.estimated_amount`
  (uses actual when available, falls back to estimated if actual is null).
- For projected transactions: `actual = txn.estimated_amount` (no actual yet, so variance
  is always 0 for individual projected items).
- Percentage: `variance_pct = (variance / estimated) * 100` when estimated != 0, else None.
- Sign convention: positive variance = over budget (spent more than estimated).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C4-1 | test_variance_empty_period | User with no transactions | `compute_variance(uid, "pay_period", period_id=1)` | VarianceReport with zero totals, empty groups | New |
| C4-2 | test_variance_pay_period_exact | $500 estimated, $500 actual (paid) | `compute_variance("pay_period")` | variance=0, variance_pct=0 | New |
| C4-3 | test_variance_pay_period_over | $500 est, $550 actual | `compute_variance("pay_period")` | variance=50, variance_pct=10.00 | New |
| C4-4 | test_variance_pay_period_under | $500 est, $450 actual | `compute_variance("pay_period")` | variance=-50, variance_pct=-10.00 | New |
| C4-5 | test_variance_projected_zero_variance | $500 est, projected status | `compute_variance("pay_period")` | variance=0 (projected uses estimate) | New |
| C4-6 | test_variance_monthly_window | Txns across 2 periods in same month | `compute_variance("month", month=1, year=2026)` | Both periods' txns included | New |
| C4-7 | test_variance_annual_window | Txns across full year | `compute_variance("year", year=2026)` | All periods' txns included | New |
| C4-8 | test_variance_category_grouping | Multiple categories, items | `compute_variance` | Groups contain correct items, totals sum correctly | New |
| C4-9 | test_variance_sorted_by_magnitude | Multiple items with different variances | `compute_variance` | Sorted by abs(variance) descending | New |
| C4-10 | test_variance_zero_estimated | $0 estimated, $50 actual | `compute_variance` | variance_pct=None (division by zero guard) | New |
| C4-11 | test_variance_excludes_deleted | Soft-deleted transaction | `compute_variance` | Deleted txn not in results | New |
| C4-12 | test_variance_excludes_cancelled | Cancelled status transaction | `compute_variance` | Cancelled txn not in results | New |

### F. Manual verification steps

No UI in this commit -- verified via tests only.

### G. Downstream effects

None. This is a new standalone service.

### H. Rollback notes

Delete the new files. No migration, no data impact.

---

## Commit 5: Spending Trend Service Engine (6.7)

### A. Commit message

```text
feat(trends): add spending trend detection service with linear regression and threshold flagging
```

### B. Problem statement

The spending trend detection (6.7) computes per-category spending trends over rolling windows,
identifies lifestyle inflation or spending decreases, and flags categories exceeding a
configurable threshold. This builds the analytical engine; the display layer is built later.

### C. Files modified

- `app/services/spending_trend_service.py` -- New file: trend detection engine.
- `tests/test_services/test_spending_trend_service.py` -- New file: full test coverage.

### D. Implementation approach

**New service: `app/services/spending_trend_service.py`**

Pure function service -- no Flask imports, no database writes.

**Data structures:**

```python
@dataclass(frozen=True)
class ItemTrend:
    """Trend data for a single category item."""
    category_id: int
    group_name: str
    item_name: str
    period_average: Decimal  # average spending per period in the window
    trend_direction: str  # "up" | "down" | "flat"
    pct_change: Decimal  # percentage change over the window
    absolute_change: Decimal  # dollar change per period
    is_flagged: bool  # exceeds threshold
    data_points: int  # number of periods with data

@dataclass(frozen=True)
class GroupTrend:
    """Aggregated trend for a category group."""
    group_name: str
    pct_change: Decimal  # weighted average of item pct changes
    trend_direction: str
    items: list[ItemTrend]

@dataclass(frozen=True)
class TrendReport:
    """Complete trend detection report."""
    window_months: int  # 3 or 6
    top_increasing: list[ItemTrend]  # top 5 trending up, sorted by pct_change desc
    top_decreasing: list[ItemTrend]  # top 5 trending down, sorted by pct_change asc
    group_trends: list[GroupTrend]
    data_sufficiency: str  # "sufficient" | "preliminary" | "insufficient"
```

**Functions:**

```python
def compute_trends(
    user_id: int,
    threshold: Decimal = Decimal("0.1000"),
) -> TrendReport:
    """Compute spending trends across all categories.

    Window logic:
    - If >= 6 months of data exist, use 6-month rolling window.
    - If >= 3 months but < 6 months, use 3-month window.
    - If < 3 months, return TrendReport with data_sufficiency="insufficient".

    For each category item with paid transactions in the window:
    1. Group paid expenses by pay period.
    2. Compute per-period totals.
    3. Fit a simple linear regression (OLS) to the per-period totals.
    4. The slope indicates trend direction and magnitude.
    5. Compute pct_change = (last_predicted - first_predicted) / first_predicted.
    6. Flag if abs(pct_change) >= threshold.

    Group-level trends use weighted average of item pct_changes, weighted by
    each item's total spending (items with more spending influence the group
    trend more).
    """

def _compute_linear_regression(
    values: list[Decimal],
) -> tuple[Decimal, Decimal]:
    """Simple OLS linear regression over equally-spaced data points.

    Returns (slope, intercept) using the least-squares formula:
    slope = (n * sum(x*y) - sum(x) * sum(y)) / (n * sum(x^2) - sum(x)^2)

    Uses Decimal arithmetic throughout for precision.
    """
```

**Window determination:**

Count distinct months with paid expense transactions for the user. If >= 6, use 6-month
window. If >= 3, use 3-month window. The window end is the current month; the window start
is `current_month - window_months`.

**Linear regression implementation:**

The x-values are period indices (0, 1, 2, ...) representing each pay period in the window.
The y-values are total paid expense amounts per period for that category item. The regression
uses Decimal arithmetic (not numpy) to maintain precision with monetary values. This is a
simple OLS fit -- no external dependencies needed.

**Threshold flagging:**

An item is flagged if `abs(pct_change) >= threshold`. Both increases and decreases are
flagged. The threshold is passed in from `user_settings.trend_alert_threshold`.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C5-1 | test_trends_insufficient_data | User with 1 month of data | `compute_trends(uid)` | data_sufficiency="insufficient", empty lists | New |
| C5-2 | test_trends_preliminary_3_months | 3 months of data | `compute_trends(uid)` | data_sufficiency="preliminary", window_months=3 | New |
| C5-3 | test_trends_sufficient_6_months | 6+ months of data | `compute_trends(uid)` | data_sufficiency="sufficient", window_months=6 | New |
| C5-4 | test_trend_increasing_category | Groceries: $400, $420, $440, $460, $480, $500 | `compute_trends` | Groceries flagged as "up", pct_change ~25% | New |
| C5-5 | test_trend_decreasing_category | Dining: $300, $270, $240, $210, $180, $150 | `compute_trends` | Dining flagged as "down", pct_change ~-50% | New |
| C5-6 | test_trend_flat_not_flagged | Rent: $1200 every period | `compute_trends` | Rent direction="flat", is_flagged=False | New |
| C5-7 | test_threshold_boundary | Category at exactly 10% change | `compute_trends(threshold=0.10)` | is_flagged=True (>= threshold) | New |
| C5-8 | test_top_5_sorting | 7 increasing categories | `compute_trends` | top_increasing has exactly 5, sorted desc | New |
| C5-9 | test_group_weighted_average | Group with 2 items: big spender up, small down | `compute_trends` | Group pct weighted toward bigger item | New |
| C5-10 | test_linear_regression_known_values | values=[10, 20, 30, 40, 50] | `_compute_linear_regression` | slope=10.0, intercept=10.0 | New |
| C5-11 | test_linear_regression_constant | values=[100, 100, 100] | `_compute_linear_regression` | slope=0.0 | New |
| C5-12 | test_excludes_projected_transactions | Mix of paid and projected | `compute_trends` | Only paid transactions contribute to trends | New |

### F. Manual verification steps

No UI in this commit -- verified via tests only.

### G. Downstream effects

None. This is a new standalone service.

### H. Rollback notes

Delete the new files. No migration, no data impact.

---

## Commit 6: Dashboard Service Aggregation Layer

### A. Commit message

```text
feat(dashboard): add dashboard service aggregating balance, bills, alerts, paycheck, goals, and debt data
```

### B. Problem statement

The summary dashboard (8.1) needs a service that aggregates data from multiple existing
services into a single template-ready data structure. Following the established pattern
(savings_dashboard_service, retirement_dashboard_service), the dashboard service takes a
`user_id` and returns plain data.

### C. Files modified

- `app/services/dashboard_service.py` -- New file: dashboard data aggregation.
- `tests/test_services/test_dashboard_service.py` -- New file: full test coverage.

### D. Implementation approach

**New service: `app/services/dashboard_service.py`**

```python
def compute_dashboard_data(user_id: int) -> dict:
    """Assemble all dashboard sections.

    Returns a dict with keys for each dashboard section:
    - upcoming_bills: list of unpaid expense transactions for current + next period
    - alerts: list of alert dicts (type, message, severity, entity_id)
    - balance_info: dict with current_balance, cash_runway_days, account_id, account_name
    - payday_info: dict with days_until, next_amount, next_date
    - savings_goals: list of goal progress dicts
    - debt_summary: dict with total_debt, dti_ratio, dti_label, accounts
    - spending_comparison: dict with current_total, prior_total, delta, delta_pct
    """
```

**Section 1: Upcoming Bills**

```python
def _get_upcoming_bills(user_id: int, account_id: int, scenario_id: int) -> list[dict]:
    """Get unpaid transactions for remainder of current period + full next period.

    Queries Transaction where:
    - account_id matches default account
    - scenario_id is baseline
    - is_deleted is False
    - status.excludes_from_balance is False
    - status.is_settled is False (not yet paid)
    - transaction_type is Expense
    - pay_period_id in (current_period, next_period)
    Sorted by pay_period.start_date ASC, then name ASC.

    Returns list of dicts: {id, name, amount (effective_amount), period_start_date,
    category_group, category_item, is_transfer (transfer_id is not None)}.
    """
```

**Section 2: Alerts**

```python
def _compute_alerts(user_id: int, account_id: int, settings: UserSettings) -> list[dict]:
    """Compute operational alerts.

    Alert types:
    1. Stale anchor: MAX(anchor_history.created_at) for the default account
       is older than settings.anchor_staleness_days. Message: "Your checking
       balance hasn't been updated in N days."
    2. Negative projected balance: Any future period where the projected end
       balance < 0. Message: "Projected balance goes negative on {date}."
    3. Overdue reconciliation: Current period has no anchor history entry.
       Message: "Current pay period has not been reconciled."

    Returns list of dicts: {type, message, severity ('warning'|'danger'),
    link (url to relevant page)}.
    """
```

**Section 3: Balance and Cash Runway**

```python
def _get_balance_info(
    user_id: int, account: Account, periods: list[PayPeriod],
    transactions: list[Transaction],
) -> dict:
    """Get current balance and compute cash runway.

    current_balance: The balance calculator result for the current period,
    or the anchor balance if no current period exists.

    cash_runway_days: Query paid expense transactions (not income, not
    transfers, not cancelled/credit) from the last 30 calendar days.
    Sum their effective_amounts. Divide by 30 for daily average.
    runway = current_balance / daily_avg. Clamp to 0 if negative.

    Returns: {current_balance, cash_runway_days, account_id, account_name,
    anchor_is_stale (bool), last_true_up_date}.
    """
```

**Section 4: Payday Info**

```python
def _get_payday_info(user_id: int) -> dict:
    """Compute days until next paycheck and projected net amount.

    next_period: First period with start_date > today.
    days_until: (next_period.start_date - today).days
    next_amount: Call paycheck_calculator.calculate_paycheck() for the next
    period using the active salary profile.

    Returns: {days_until, next_amount, next_date}. If no salary profile
    or no future period, returns {days_until: None, next_amount: None}.
    """
```

**Section 5: Savings Goals**

```python
def _get_savings_goals(user_id: int) -> list[dict]:
    """Get active savings goal progress.

    Queries active SavingsGoal records. For each goal:
    - Resolve target_amount (handles income-relative goals).
    - Get current account balance.
    - Compute pct_complete = current / target * 100.

    Returns list of dicts: {name, current_balance, target_amount,
    pct_complete, account_name}.
    """
```

**Section 6: Debt Summary**

Calls `savings_dashboard_service._compute_debt_summary()` logic. Since this is a private
function, extract the debt summary computation into a standalone function
`compute_debt_summary(account_data, escrow_map)` that both the savings dashboard and the
main dashboard can call. Alternatively, make the dashboard service call
`savings_dashboard_service.compute_dashboard_data()` and extract the `debt_summary` key.

**OPTION:** Extract `_compute_debt_summary` into a shared utility to avoid importing the full
savings dashboard computation just for the debt summary. This is cleaner but requires moving
a private function to a public interface.

**Recommended approach:** The dashboard service calls the relevant portions of the savings
dashboard service directly. Since `compute_dashboard_data()` returns the full dict including
`debt_summary`, and the function is already optimized, calling it once and extracting the
needed keys is simpler than extracting sub-functions. The overhead of computing unused
sections is negligible.

**Section 7: Spending Comparison**

```python
def _get_spending_comparison(
    user_id: int, account_id: int, scenario_id: int,
    current_period: PayPeriod, prior_period: PayPeriod | None,
) -> dict:
    """Compare spending between current and prior pay periods.

    For each period, sum effective_amount of paid expense transactions
    (status.is_settled=True, transaction_type=Expense).

    Returns: {current_total, prior_total, delta (current - prior),
    delta_pct, direction ('higher'|'lower'|'same')}.
    If no prior period, returns prior_total=None.
    """
```

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C6-1 | test_dashboard_empty_user | User with no data | `compute_dashboard_data(uid)` | All sections return empty/zero/None gracefully | New |
| C6-2 | test_upcoming_bills | 3 expense txns (2 projected, 1 paid) | `_get_upcoming_bills` | Returns 2 unpaid, sorted by date | New |
| C6-3 | test_upcoming_bills_two_periods | Txns in current + next period | `_get_upcoming_bills` | Both periods' unpaid txns included | New |
| C6-4 | test_alert_stale_anchor | Anchor updated 20 days ago, threshold=14 | `_compute_alerts` | Stale anchor alert present | New |
| C6-5 | test_alert_no_stale | Anchor updated 5 days ago | `_compute_alerts` | No stale anchor alert | New |
| C6-6 | test_alert_negative_balance | Period with projected balance < 0 | `_compute_alerts` | Negative balance alert present | New |
| C6-7 | test_cash_runway_calculation | Balance=3000, last 30 days spending=$1500 | `_get_balance_info` | runway=60 days (3000 / (1500/30)) | New |
| C6-8 | test_cash_runway_zero_spending | Balance=3000, no spending last 30 days | `_get_balance_info` | runway=None or very large (division guard) | New |
| C6-9 | test_payday_info | Next period starts in 5 days | `_get_payday_info` | days_until=5, next_amount from paycheck calc | New |
| C6-10 | test_payday_info_no_salary | No salary profile | `_get_payday_info` | days_until=5, next_amount=None | New |
| C6-11 | test_savings_goals_progress | Goal: target=10000, account balance=2500 | `_get_savings_goals` | pct_complete=25.00 | New |
| C6-12 | test_debt_summary_present | Mortgage account with LoanParams | `compute_dashboard_data` | debt_summary has total_debt, dti_ratio | New |
| C6-13 | test_spending_comparison | Current period $800 spent, prior $600 | `_get_spending_comparison` | delta=200, direction='higher' | New |
| C6-14 | test_spending_comparison_no_prior | First period ever | `_get_spending_comparison` | prior_total=None | New |
| C6-15 | test_full_dashboard_integration | seed_full_user_data | `compute_dashboard_data` | All sections populated, no errors | New |

### F. Manual verification steps

No UI in this commit -- verified via tests only.

### G. Downstream effects

None. This is a new standalone service.

### H. Rollback notes

Delete the new files. No migration, no data impact.

---

## Commit 7: Dashboard Template with All 7 Sections and Interactive Elements

### A. Commit message

```text
feat(dashboard): add summary dashboard page with mark-paid, true-up, and HTMX refresh
```

### B. Problem statement

The dashboard needs a complete template with all 7 sections, interactive mark-as-paid and
true-up actions, and HTMX refresh wiring. This is the largest template commit in the plan.

### C. Files modified

- `app/routes/dashboard.py` -- New file: dashboard blueprint with mark-paid and refresh
  endpoints.
- `app/templates/dashboard/dashboard.html` -- New file: main dashboard page.
- `app/templates/dashboard/_upcoming_bills.html` -- Bills section with mark-paid buttons.
- `app/templates/dashboard/_alerts.html` -- Alerts section.
- `app/templates/dashboard/_balance_runway.html` -- Balance display with inline true-up.
- `app/templates/dashboard/_payday.html` -- Next payday section.
- `app/templates/dashboard/_savings_goals.html` -- Savings goals with progress bars.
- `app/templates/dashboard/_debt_summary.html` -- Debt summary section.
- `app/templates/dashboard/_spending_comparison.html` -- Period comparison.
- `app/templates/dashboard/_bill_row.html` -- Single bill row partial for HTMX swap.
- `app/templates/dashboard/_mark_paid_form.html` -- Mark-paid inline form.
- `app/templates/dashboard/_true_up_form.html` -- True-up inline form.
- `app/static/js/dashboard.js` -- Dashboard-specific interactions.
- `app/static/css/app.css` -- Dashboard-specific styles.
- `app/__init__.py` -- Register `dashboard_bp` blueprint.
- `tests/test_routes/test_dashboard.py` -- New file: dashboard route tests.

### D. Implementation approach

**Dashboard blueprint (`app/routes/dashboard.py`):**

```python
dashboard_bp = Blueprint("dashboard", __name__)

@dashboard_bp.route("/dashboard")
@login_required
def page():
    """Summary dashboard -- the app's landing page."""
    data = dashboard_service.compute_dashboard_data(current_user.id)
    return render_template("dashboard/dashboard.html", **data)

@dashboard_bp.route("/dashboard/mark-paid/<int:txn_id>", methods=["POST"])
@login_required
def mark_paid(txn_id):
    """Mark a bill as paid from the dashboard.

    Delegates to the same transaction mark-done logic as the grid.
    Returns the updated bill row partial for HTMX swap.
    """
    # Same ownership check and status update as transactions.mark_done
    # but returns dashboard/_bill_row.html instead of grid cell

@dashboard_bp.route("/dashboard/bills", methods=["GET"])
@login_required
def bills_section():
    """HTMX partial: refresh the upcoming bills section."""
    # Called after mark-paid to refresh the full bills list

@dashboard_bp.route("/dashboard/balance", methods=["GET"])
@login_required
def balance_section():
    """HTMX partial: refresh the balance/runway section."""
    # Called after true-up to refresh balance display
```

**Mark-as-paid interaction flow:**

1. Each bill row has a "Mark Paid" button with `hx-post="/dashboard/mark-paid/<id>"`.
2. The button includes an optional actual amount input (inline, collapsed by default --
   click to expand, consistent with the grid's quick-edit pattern).
3. On success: the endpoint marks the transaction as done/received (using the same logic as
   `transactions.mark_done`), then returns the updated `_bill_row.html` partial with the
   transaction in "paid" visual state (struck through, muted).
4. The response includes `HX-Trigger: dashboardRefresh` to refresh the balance and spending
   sections (since marking a bill paid affects the projected balance).
5. On the next full page load, paid bills no longer appear (they fail the "not settled"
   filter).

**True-up interaction flow:**

1. The balance display in Section 3 has a clickable balance amount (same pattern as the grid).
2. Click triggers `hx-get="/accounts/<id>/anchor-form"` -> reuses the existing accounts
   endpoint to render the edit form.
3. On save: `hx-patch="/accounts/<id>/true-up"` -> reuses the existing true-up endpoint.
4. The response triggers `HX-Trigger: balanceChanged` which the dashboard listens for to
   refresh the balance section.

**Template structure (`dashboard/dashboard.html`):**

```html
{% extends "base.html" %}
{% block content %}
<div class="container-fluid py-3">
  <div class="row g-3">
    <!-- Section 1: Upcoming Bills (col-lg-8) -->
    <div class="col-lg-8">
      <div class="card" id="bills-section">
        <div class="card-header d-flex justify-content-between">
          <h5 class="mb-0">Upcoming Bills</h5>
          <a href="{{ url_for('grid.index') }}" class="btn btn-sm btn-outline-secondary">
            Open Grid</a>
        </div>
        <div class="card-body p-0">
          {% include "dashboard/_upcoming_bills.html" %}
        </div>
      </div>
    </div>

    <!-- Section 2: Alerts (col-lg-4) -->
    <div class="col-lg-4">
      {% include "dashboard/_alerts.html" %}
    </div>

    <!-- Section 3: Balance & Runway (col-md-6 col-lg-3) -->
    <!-- Section 4: Payday (col-md-6 col-lg-3) -->
    <!-- Section 5: Savings Goals (col-lg-6) -->
    <!-- Section 6: Debt Summary (col-md-6 col-lg-3) -->
    <!-- Section 7: Spending Comparison (col-md-6 col-lg-3) -->
  </div>
</div>
{% endblock %}
```

**HTMX refresh wiring:**

```html
<div id="bills-section"
     hx-get="{{ url_for('dashboard.bills_section') }}"
     hx-trigger="dashboardRefresh from:body"
     hx-swap="innerHTML">
```

The `dashboardRefresh` event is triggered by mark-paid responses. The `balanceChanged` event
(from true-up) triggers refresh of the balance section.

**Dark mode:** All new CSS uses the existing custom properties (`--shekel-surface`,
`--shekel-text-primary`, etc.). Progress bars use Bootstrap's built-in dark mode support.
Alert severity colors use the established `--shekel-danger`, `--shekel-done` variables.

**Mobile:** The responsive grid uses `col-lg-*` and `col-md-*` so sections stack vertically
on narrow screens. Bills display as a list (no table) for mobile compatibility. Mark-paid
buttons are touch-target sized (min 44x44px).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C7-1 | test_dashboard_requires_auth | client | GET /dashboard | 302 redirect to /login | New |
| C7-2 | test_dashboard_renders_empty | auth_client, seed_user | GET /dashboard | 200, contains "Upcoming Bills" | New |
| C7-3 | test_dashboard_shows_bills | seed_full_user_data, projected expense | GET /dashboard | Bill name and amount in response | New |
| C7-4 | test_mark_paid_from_dashboard | seed_full_user_data, projected expense | POST /dashboard/mark-paid/<id> | 200, transaction status changed to done | New |
| C7-5 | test_mark_paid_with_actual_amount | projected expense | POST with actual_amount=450 | Transaction actual_amount set | New |
| C7-6 | test_mark_paid_returns_paid_row | projected expense | POST /dashboard/mark-paid/<id> | Response contains struck-through styling | New |
| C7-7 | test_mark_paid_triggers_refresh | projected expense | POST /dashboard/mark-paid/<id> | HX-Trigger header contains dashboardRefresh | New |
| C7-8 | test_bills_section_htmx | auth_client | GET /dashboard/bills (HX-Request) | 200, bills HTML partial | New |
| C7-9 | test_balance_section_htmx | auth_client | GET /dashboard/balance (HX-Request) | 200, balance HTML partial | New |
| C7-10 | test_dashboard_shows_alerts | seed_user, stale anchor | GET /dashboard | Alert message present | New |
| C7-11 | test_dashboard_no_alerts | seed_user, fresh anchor | GET /dashboard | No alert section or empty | New |
| C7-12 | test_dashboard_savings_goals | seed_full_user_data | GET /dashboard | Goal name and progress bar | New |
| C7-13 | test_dashboard_debt_summary | seed_full_user_data + mortgage | GET /dashboard | DTI ratio displayed | New |
| C7-14 | test_dashboard_spending_comparison | 2 periods with paid expenses | GET /dashboard | Current vs prior amounts shown | New |
| C7-15 | test_dashboard_payday_info | seed_full_user_data | GET /dashboard | "days until payday" and amount | New |
| C7-16 | test_mark_paid_wrong_user | second_auth_client, first user's txn | POST /dashboard/mark-paid/<id> | 404 (ownership check) | New |
| C7-17 | test_mark_paid_transfer_shadow | Transfer shadow transaction | POST /dashboard/mark-paid/<id> | Both shadows updated via transfer_service | New |

### F. Manual verification steps

1. Navigate to `/dashboard`. Verify all 7 sections render with data.
2. Click "Mark Paid" on a bill. Verify the row transitions to paid state (struck through).
3. Verify the balance section refreshes after marking paid.
4. Click the balance amount. Verify the inline edit form appears. Enter a new balance and
   submit. Verify the balance updates and the "as of" date changes.
5. Verify the "Open Grid" link navigates to the budget grid.
6. Verify savings goal progress bars show correct percentages.
7. Test at 375px width. Verify sections stack vertically, bills are readable, mark-paid
   buttons are tappable.
8. Toggle dark mode. Verify all sections render correctly.

### G. Downstream effects

- The dashboard blueprint is registered but `/` still points to the grid. The route swap
  happens in Commit 8.

### H. Rollback notes

Remove `dashboard_bp` from `__init__.py`, delete all new dashboard files. No migration.

---

## Commit 8: Default Route Swap and Nav Bar Update

### A. Commit message

```text
feat(dashboard): make dashboard the default route, move grid to /grid
```

### B. Problem statement

The dashboard must become the app's landing page (`/`). The grid must move to `/grid`. The
nav bar must reflect these changes.

### C. Files modified

- `app/routes/grid.py` -- Change the grid's route from `/` to `/grid`.
- `app/routes/dashboard.py` -- Add `/` as an additional route for the dashboard page.
- `app/templates/base.html` -- Update nav bar: add "Dashboard" as first item pointing to `/`,
  update "Budget" to point to `/grid`.
- `tests/test_routes/test_grid.py` -- Update URL references from `/` to `/grid`.
- `tests/test_routes/test_dashboard.py` -- Add test for `/` route.

### D. Implementation approach

**Grid route change:**

In `app/routes/grid.py`, change `@grid_bp.route("/")` (line 133) to `@grid_bp.route("/grid")`.
Also update any `url_for("grid.index")` references in templates that construct the URL.

Grep the entire codebase for `url_for('grid.index')` and `url_for("grid.index")` to find all
references. Update each one -- the generated URL will automatically change since the route
mapping changed, but any hardcoded `/` paths in HTMX attributes or redirects need updating.

**Dashboard route addition:**

Add `@dashboard_bp.route("/")` as an additional decorator on the dashboard page function. The
dashboard is now accessible at both `/` and `/dashboard`.

**Nav bar update:**

In `base.html`, update the nav items:
1. **Dashboard** (new) -> `url_for('dashboard.page')` -> `/dashboard`
2. **Budget** -> `url_for('grid.index')` -> `/grid`
3. (remaining items unchanged)

Active detection for "Dashboard": `request.path in ('/', '/dashboard')`.
Active detection for "Budget": `request.path.startswith('/grid')`.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C8-1 | test_root_serves_dashboard | auth_client | GET / | 200, contains "Upcoming Bills" | New |
| C8-2 | test_grid_at_new_url | auth_client, seed_user | GET /grid | 200, contains grid content | New |
| C8-3 | test_old_grid_url_gone | auth_client | GET / | Does NOT contain grid table | New |
| C8-4 | test_nav_has_dashboard | auth_client | GET /grid | Nav contains "Dashboard" link | New |
| C8-5 | test_nav_budget_points_to_grid | auth_client | GET / | "Budget" link href="/grid" | New |
| C8-6 | test_grid_redirect_after_baseline | client, no scenario | POST /grid/create-baseline | Redirects to /grid | Mod |

### F. Manual verification steps

1. Open browser, navigate to app root. Verify the dashboard loads (not the grid).
2. Click "Budget" in nav bar. Verify the grid loads at `/grid`.
3. Click "Dashboard" in nav bar. Verify the dashboard loads.
4. Verify the nav item highlighting: "Dashboard" is active on `/`, "Budget" is active on
   `/grid`.
5. Bookmark `/` and `/grid`. Reload both. Verify correct pages load.

### G. Downstream effects

- All internal links that previously pointed to `/` for the grid now point to `/grid`.
- The onboarding flow in `__init__.py` that checks various setup steps may redirect to `/`
  which now serves the dashboard. Verify the onboarding banner still works correctly.
- External bookmarks to `/` now get the dashboard instead of the grid. This is intentional.

### H. Rollback notes

Revert route changes, revert nav bar. Grid returns to `/`. No migration.

---

## Commit 9: Calendar Display Layer (8.2 + 6.6 Display)

### A. Commit message

```text
feat(calendar): add month detail and year overview calendar views to analytics page
```

### B. Problem statement

The calendar tab on the analytics page needs two views: a month detail calendar (7-column
grid with transaction markers) and a year overview (4x3 grid with monthly totals). Navigation
between views and months must work via HTMX.

### C. Files modified

- `app/routes/analytics.py` -- Replace calendar tab placeholder with real endpoint.
- `app/templates/analytics/_calendar_month.html` -- New: month detail view.
- `app/templates/analytics/_calendar_year.html` -- New: year overview.
- `app/static/js/calendar.js` -- New: tooltip/popover interactions for day cells.
- `app/static/css/app.css` -- Calendar grid styles.
- `tests/test_routes/test_analytics.py` -- Add calendar tab tests.

### D. Implementation approach

**Analytics route updates:**

```python
@analytics_bp.route("/analytics/calendar")
@login_required
def calendar_tab():
    """HTMX partial: calendar tab content."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    view = request.args.get("view", "month")  # "month" or "year"
    year = request.args.get("year", type=int, default=date.today().year)
    month = request.args.get("month", type=int, default=date.today().month)
    account_id = request.args.get("account_id", type=int)

    settings = UserSettings.query.filter_by(user_id=current_user.id).one()
    threshold = settings.large_transaction_threshold

    if view == "year":
        data = calendar_service.get_year_overview(
            current_user.id, year, account_id, threshold)
        return render_template("analytics/_calendar_year.html",
                               data=data, year=year)
    else:
        data = calendar_service.get_month_detail(
            current_user.id, year, month, account_id, threshold)
        return render_template("analytics/_calendar_month.html",
                               data=data, year=year, month=month)
```

**Month detail template (`_calendar_month.html`):**

A 7-column CSS grid (Sun-Sat) with one row per week:

```html
<div class="d-flex justify-content-between align-items-center mb-3">
  <button class="btn btn-sm btn-outline-secondary"
          hx-get="{{ url_for('analytics.calendar_tab', view='month',
                    year=prev_year, month=prev_month) }}"
          hx-target="#tab-content" hx-swap="innerHTML">
    <i class="bi bi-chevron-left"></i> Prev
  </button>
  <h5 class="mb-0">{{ month_name }} {{ year }}</h5>
  <button class="btn btn-sm btn-outline-secondary"
          hx-get="{{ url_for('analytics.calendar_tab', view='month',
                    year=next_year, month=next_month) }}"
          hx-target="#tab-content" hx-swap="innerHTML">
    Next <i class="bi bi-chevron-right"></i>
  </button>
</div>
<button class="btn btn-sm btn-outline-secondary mb-3"
        hx-get="{{ url_for('analytics.calendar_tab', view='year', year=year) }}"
        hx-target="#tab-content" hx-swap="innerHTML">
  <i class="bi bi-zoom-out"></i> Year Overview
</button>

<div class="calendar-grid">
  <!-- Day header row: Sun Mon Tue Wed Thu Fri Sat -->
  <!-- Day cells with transaction markers -->
  {% for day in month_days %}
  <div class="calendar-day {% if day.is_paycheck_day %}calendar-paycheck{% endif %}"
       {% if day.entries %}
       data-bs-toggle="popover"
       data-bs-trigger="hover focus"
       data-bs-html="true"
       data-bs-content="{{ day.popover_html }}"
       {% endif %}>
    <span class="calendar-day-number">{{ day.number }}</span>
    {% for entry in day.entries[:3] %}
    <span class="calendar-marker
      {% if entry.is_income %}calendar-income{% else %}calendar-expense{% endif %}
      {% if entry.is_large %}calendar-large{% endif %}"
      title="{{ entry.name }}: ${{ entry.amount }}">
    </span>
    {% endfor %}
    {% if day.entries|length > 3 %}
    <span class="calendar-more">+{{ day.entries|length - 3 }}</span>
    {% endif %}
  </div>
  {% endfor %}
</div>
```

Each day cell shows small colored dots for transactions (green=income, red/pink=expense).
Large or infrequent transactions get a distinct marker style (ring or larger dot). Paycheck
days get a highlighted background. Hovering/clicking shows a Bootstrap popover with
transaction names and amounts.

**Year overview template (`_calendar_year.html`):**

A 4x3 grid of month cards:

```html
<div class="row row-cols-2 row-cols-md-3 row-cols-lg-4 g-3">
  {% for month in data.months %}
  <div class="col">
    <div class="card calendar-month-card
      {% if month.net < 0 %}border-danger{% elif month.net > month.total_income * 0.8 %}border-warning{% else %}border-success{% endif %}"
         hx-get="{{ url_for('analytics.calendar_tab', view='month',
                   year=data.year, month=month.month) }}"
         hx-target="#tab-content" hx-swap="innerHTML"
         role="button">
      <div class="card-body p-2">
        <h6>{{ month_name(month.month) }}
          {% if month.is_third_paycheck_month %}
          <span class="badge bg-info">3rd check</span>
          {% endif %}
        </h6>
        <small class="text-success">+${{ month.total_income }}</small><br>
        <small class="text-danger">-${{ month.total_expenses }}</small><br>
        <strong>Net: ${{ month.net }}</strong>
      </div>
    </div>
  </div>
  {% endfor %}
</div>
```

Month cards are color-coded: green border for net-positive, yellow for tight, red for
net-negative. Clicking a month navigates to its detail view via HTMX.

**Calendar CSS:**

```css
.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 1px;
  background-color: var(--shekel-border-subtle);
}
.calendar-day {
  background-color: var(--shekel-surface);
  min-height: 5rem;
  padding: 0.25rem;
}
.calendar-paycheck {
  background-color: color-mix(in srgb, var(--shekel-surface), #2ECC71 8%);
}
.calendar-marker {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.calendar-income { background-color: var(--shekel-done); }
.calendar-expense { background-color: var(--shekel-danger); }
.calendar-large {
  width: 10px;
  height: 10px;
  box-shadow: 0 0 0 2px var(--shekel-text-muted);
}
```

**Mobile:** On `<768px`, the calendar grid shrinks. Day numbers remain visible but marker dots
are smaller. The year overview uses `row-cols-2` for a 2-column layout on small screens.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C9-1 | test_calendar_month_renders | seed_full_user_data | GET /analytics/calendar?view=month (HX) | 200, month name in response | New |
| C9-2 | test_calendar_year_renders | seed_full_user_data | GET /analytics/calendar?view=year (HX) | 200, 12 month cards | New |
| C9-3 | test_calendar_navigation | seed_full_user_data | GET ?view=month&year=2026&month=3 | March 2026 displayed | New |
| C9-4 | test_calendar_paycheck_highlighting | seed_periods | GET ?view=month | Paycheck days have highlight class | New |
| C9-5 | test_calendar_large_txn_marker | seed_full_user_data, txn > threshold | GET ?view=month | Transaction marked as large | New |
| C9-6 | test_calendar_third_paycheck_badge | 52 periods | GET ?view=year | 2 months have "3rd check" badge | New |
| C9-7 | test_calendar_empty_month | No transactions | GET ?view=month | Month renders with empty day cells | New |
| C9-8 | test_calendar_no_htmx_redirect | auth_client | GET /analytics/calendar (no HX-Request) | 302 redirect to /analytics | Existing |

### F. Manual verification steps

1. Navigate to `/analytics`. Calendar tab loads by default. Verify current month displays.
2. Click forward/back buttons. Verify month changes via HTMX swap.
3. Hover a day with transactions. Verify popover shows names and amounts.
4. Click "Year Overview". Verify 4x3 grid with monthly totals.
5. Click a month card. Verify navigation to that month's detail view.
6. Verify 3rd paycheck months have the badge.
7. Verify large transaction markers are visually distinct.
8. Test at 375px width. Verify calendar is readable and year overview uses 2 columns.
9. Toggle dark mode. Verify all calendar elements render correctly.

### G. Downstream effects

None. This replaces the calendar tab placeholder from Commit 2.

### H. Rollback notes

Revert analytics route to placeholder, delete new templates and JS. No migration.

---

## Commit 10: Year-End Summary Service Engine (8.3)

### A. Commit message

```text
feat(year-end): add year-end summary service aggregating income, taxes, spending, transfers, net worth, and debt progress
```

### B. Problem statement

The year-end financial summary (8.3) needs a service that aggregates a full calendar year of
financial data: income and tax breakdowns (W-2 line items), spending by category, transfer
summaries, net worth trend, debt progress, and savings progress.

### C. Files modified

- `app/services/year_end_summary_service.py` -- New file.
- `tests/test_services/test_year_end_summary_service.py` -- New file.

### D. Implementation approach

**New service: `app/services/year_end_summary_service.py`**

```python
def compute_year_end_summary(user_id: int, year: int) -> dict:
    """Aggregate annual financial data for the specified calendar year.

    Returns dict with keys:
    - income_tax: dict with gross_wages, federal_tax, state_tax, ss_tax,
      medicare_tax, pretax_deductions (list), posttax_deductions (list),
      net_pay_total, mortgage_interest_total
    - spending_by_category: list of {group_name, group_total, items: [{item_name,
      item_total}]}
    - transfers_summary: list of {destination_account, total_amount}
    - net_worth: {monthly_values: list of 12 {month, balance}, jan1, dec31, delta}
    - debt_progress: list of {account_name, jan1_balance, dec31_balance,
      principal_paid}
    - savings_progress: list of {account_name, jan1_balance, dec31_balance,
      total_contributions}
    """
```

**Income/Tax section:**

Query all pay periods in the calendar year. For each, call
`paycheck_calculator.calculate_paycheck()` for each active salary profile. Sum across all
periods:
- `gross_wages` = sum of `breakdown.gross_biweekly`
- `federal_tax` = sum of `breakdown.federal_tax`
- `state_tax` = sum of `breakdown.state_tax`
- `ss_tax` = sum of `breakdown.social_security`
- `medicare_tax` = sum of `breakdown.medicare`
- Pre-tax deductions: grouped by deduction name, summed across periods
- Post-tax deductions: grouped by deduction name, summed across periods
- `net_pay_total` = sum of `breakdown.net_pay`

**Mortgage interest:**

For each account with `has_amortization=True`, call `amortization_engine.generate_schedule()`
with payment history. Sum the `interest` column for rows whose `payment_date` falls in the
calendar year.

**Spending by category:**

Query paid/settled expense transactions for the year. Group by `category.group_name` then
`category.item_name`. Sum `effective_amount` for each item and group.

**Transfers summary:**

Query transfers in the year's pay periods. Group by `to_account.name`. Sum `amount`.

**Net worth:**

For each of the 12 months, compute the sum of all account balances at month-end. This
requires running the balance calculator for each account type at the last pay period of
each month.

**Debt/savings progress:**

For debt accounts (`has_amortization=True`): compute balance at Jan 1 and Dec 31 using the
amortization engine with payment history. Principal paid = jan1 - dec31.

For savings/investment accounts: compute balance at Jan 1 and Dec 31 using the balance
calculator or growth engine. Total contributions = sum of shadow income transactions
(transfers in) during the year.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C10-1 | test_year_end_empty | User, no salary/txns | `compute_year_end_summary(uid, 2026)` | All sections zero/empty | New |
| C10-2 | test_income_aggregation | Salary profile, 26 periods in year | compute | gross = annual_salary, net = 26 * net_biweekly | New |
| C10-3 | test_tax_breakdown | Salary profile with known brackets | compute | Federal tax matches sum of per-period calculations. Hand-computed: $75,000 salary -> taxable ~$60,050 -> ~$6,786 federal (verify with bracket math) | New |
| C10-4 | test_mortgage_interest_total | Mortgage with 12 payments in year | compute | mortgage_interest_total = sum of interest portions. Hand-computed: $240,000 at 6.5%, first year ~$15,500 interest | New |
| C10-5 | test_spending_by_category | 3 paid expenses in 2 categories | compute | Categories grouped, totals correct | New |
| C10-6 | test_spending_hierarchy | Multiple items in same group | compute | Group total = sum of item totals | New |
| C10-7 | test_transfers_summary | Transfers to savings and mortgage | compute | Grouped by destination, amounts summed | New |
| C10-8 | test_net_worth_12_points | Account with varying balance | compute | 12 monthly values, jan1 and dec31 match endpoints | New |
| C10-9 | test_debt_progress | Mortgage, payments made | compute | jan1_balance > dec31_balance, principal_paid = difference | New |
| C10-10 | test_savings_progress | Savings account, contributions | compute | dec31 > jan1, contributions = sum of transfers in | New |

### F. Manual verification steps

No UI in this commit -- verified via tests only.

### G. Downstream effects

None. This is a new standalone service.

### H. Rollback notes

Delete the new files. No migration, no data impact.

---

## Commit 11: Year-End Summary Display Layer (8.3 Display)

### A. Commit message

```text
feat(year-end): add year-end summary view with income/tax breakdown, spending, and net worth chart
```

### B. Problem statement

The year-end summary tab on the analytics page needs a template that renders the service data
as a structured annual report with income/tax breakdown, spending by category, transfer
summary, net worth chart, and debt/savings progress sections.

### C. Files modified

- `app/routes/analytics.py` -- Replace year-end tab placeholder with real endpoint.
- `app/templates/analytics/_year_end.html` -- New: year-end summary template.
- `app/static/js/chart_year_end.js` -- New: net worth line chart.
- `app/static/css/app.css` -- Year-end specific styles.
- `tests/test_routes/test_analytics.py` -- Add year-end tab tests.

### D. Implementation approach

**Analytics route update:**

```python
@analytics_bp.route("/analytics/year-end")
@login_required
def year_end_tab():
    """HTMX partial: year-end financial summary."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    year = request.args.get("year", type=int, default=date.today().year)
    data = year_end_summary_service.compute_year_end_summary(current_user.id, year)
    return render_template("analytics/_year_end.html", data=data, year=year)
```

**Template structure:**

Year selector dropdown at the top. Then 6 sections with Bootstrap accordion for expandable
category drill-downs:

1. **Income & Taxes:** Table with W-2-style line items. Each row: label (with optional W-2
   box reference), annual total. Includes gross wages, federal/state/SS/Medicare tax, each
   pre-tax deduction, each post-tax deduction, net pay, and mortgage interest (labeled
   "Mortgage Interest Paid (Schedule A)").

2. **Spending by Category:** Accordion groups. Each group header shows group name and total.
   Expanding shows individual items with their totals.

3. **Transfers Summary:** Simple table: destination account name, total transferred.

4. **Net Worth Trend:** Chart.js line chart with 12 monthly points. Uses `chart_year_end.js`
   with the `ShekelChart.create()` pattern. Displays Jan 1 vs Dec 31 delta prominently.

5. **Debt Progress:** Table: account name, Jan 1 balance, Dec 31 balance, principal paid.

6. **Savings Progress:** Table: account name, Jan 1 balance, Dec 31 balance, contributions.

**Net worth chart JS (`chart_year_end.js`):**

Follows the established pattern -- reads `data-labels` and `data-data` attributes from
canvas element. Single line chart with area fill. Year-aware x-axis labels. Tooltip shows
dollar values.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C11-1 | test_year_end_tab_renders | seed_full_user_data | GET /analytics/year-end (HX) | 200, contains year-end content | New |
| C11-2 | test_year_end_year_selector | seed_full_user_data | GET ?year=2026 | 2026 data displayed | New |
| C11-3 | test_year_end_income_section | Salary profile | GET year-end | Gross wages and net pay shown | New |
| C11-4 | test_year_end_spending_section | Paid expenses | GET year-end | Category groups visible | New |
| C11-5 | test_year_end_net_worth_chart | Account data | GET year-end | Canvas element with data attributes | New |
| C11-6 | test_year_end_empty_year | No data for year | GET ?year=2025 | Empty state message | New |

### F. Manual verification steps

1. Navigate to `/analytics`, click "Year-End Summary" tab.
2. Verify income/tax section shows W-2-style line items.
3. Expand a spending category group. Verify individual items shown.
4. Verify net worth chart renders with 12 monthly points.
5. Verify Jan 1 vs Dec 31 delta is displayed.
6. Change year selector. Verify data updates via HTMX.
7. Toggle dark mode. Verify chart and all sections render correctly.

### G. Downstream effects

None. Replaces the year-end tab placeholder.

### H. Rollback notes

Revert analytics route to placeholder, delete new templates and JS. No migration.

---

## Commit 12: Budget Variance Display Layer (6.5 Display)

### A. Commit message

```text
feat(variance): add budget variance view with bar chart, drill-down table, and time window toggle
```

### B. Problem statement

The variance tab on the analytics page needs a bar chart (estimated vs. actual per category),
a tabular view with drill-down, and a time window toggle (pay period / month / year).

### C. Files modified

- `app/routes/analytics.py` -- Replace variance tab placeholder with real endpoint.
- `app/templates/analytics/_variance.html` -- New: variance main template.
- `app/templates/analytics/_variance_detail.html` -- New: category drill-down partial.
- `app/static/js/chart_variance.js` -- New: grouped bar chart.
- `app/static/css/app.css` -- Variance-specific styles.
- `tests/test_routes/test_analytics.py` -- Add variance tab tests.

### D. Implementation approach

**Analytics route update:**

```python
@analytics_bp.route("/analytics/variance")
@login_required
def variance_tab():
    """HTMX partial: budget variance analysis."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    window_type = request.args.get("window", "pay_period")
    period_id = request.args.get("period_id", type=int)
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)

    # Default to current period / current month / current year
    if window_type == "pay_period" and period_id is None:
        current = pay_period_service.get_current_period(current_user.id)
        period_id = current.id if current else None
    if window_type == "month" and month is None:
        month, year = date.today().month, date.today().year
    if window_type == "year" and year is None:
        year = date.today().year

    report = budget_variance_service.compute_variance(
        current_user.id, window_type, period_id, month, year)

    # Build chart data for the bar chart
    chart_data = {
        "labels": [g.group_name for g in report.groups],
        "estimated": [float(g.estimated_total) for g in report.groups],
        "actual": [float(g.actual_total) for g in report.groups],
    }

    return render_template("analytics/_variance.html",
                           report=report, chart_data=chart_data,
                           window_type=window_type)

@analytics_bp.route("/analytics/variance/detail/<int:category_id>")
@login_required
def variance_detail(category_id):
    """HTMX partial: drill-down into a category's transactions."""
    # ... returns _variance_detail.html with transaction-level data
```

**Template:**

Three toggle buttons at top for pay period / month / year. Each triggers an HTMX GET with
the appropriate window type. Below: a Chart.js grouped bar chart (estimated vs actual per
category group) and a table listing each category group and item with expandable rows.

Category rows are clickable (HTMX GET to `variance_detail`) to expand and show individual
transactions.

Conditional color coding: red text for over-budget (positive variance), green for under-budget
(negative variance).

**Chart (`chart_variance.js`):**

Grouped bar chart with two datasets (estimated in blue, actual in green/red based on
over/under). Uses `ShekelChart.create()` pattern. Dollar formatting on y-axis.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C12-1 | test_variance_tab_renders | seed_full_user_data | GET /analytics/variance (HX) | 200, contains variance content | New |
| C12-2 | test_variance_pay_period_window | seed_full_user_data | GET ?window=pay_period | Current period data shown | New |
| C12-3 | test_variance_monthly_window | seed_full_user_data | GET ?window=month&month=1&year=2026 | Monthly data shown | New |
| C12-4 | test_variance_annual_window | seed_full_user_data | GET ?window=year&year=2026 | Annual data shown | New |
| C12-5 | test_variance_chart_data | seed_full_user_data | GET variance | Canvas with data-labels, data-estimated, data-actual | New |
| C12-6 | test_variance_detail_drilldown | seed_full_user_data | GET /analytics/variance/detail/<cat_id> (HX) | Transaction-level rows shown | New |
| C12-7 | test_variance_color_coding | Over-budget category | GET variance | Red styling on over-budget row | New |

### F. Manual verification steps

1. Navigate to `/analytics`, click "Variance" tab.
2. Verify grouped bar chart renders with estimated vs actual bars.
3. Toggle between pay period, month, and year views.
4. Click a category row. Verify transaction drill-down expands.
5. Verify color coding: red for over-budget, green for under-budget.
6. Toggle dark mode. Verify chart and table render correctly.

### G. Downstream effects

None. Replaces the variance tab placeholder.

### H. Rollback notes

Revert analytics route to placeholder, delete new templates and JS. No migration.

---

## Commit 13: Spending Trends Display Layer (6.7 Display)

### A. Commit message

```text
feat(trends): add spending trend view with top-5 lists and category drill-down
```

### B. Problem statement

The trends tab on the analytics page needs two ranked lists (top 5 trending up, top 5 trending
down), category group drill-down, and a data sufficiency banner.

### C. Files modified

- `app/routes/analytics.py` -- Replace trends tab placeholder with real endpoint.
- `app/templates/analytics/_trends.html` -- New: trends main template.
- `app/templates/analytics/_trends_detail.html` -- New: group-level drill-down.
- `app/static/css/app.css` -- Trends-specific styles.
- `tests/test_routes/test_analytics.py` -- Add trends tab tests.

### D. Implementation approach

**Analytics route update:**

```python
@analytics_bp.route("/analytics/trends")
@login_required
def trends_tab():
    """HTMX partial: spending trend detection."""
    if not request.headers.get("HX-Request"):
        return redirect(url_for("analytics.page"))

    settings = UserSettings.query.filter_by(user_id=current_user.id).one()
    threshold = settings.trend_alert_threshold
    report = spending_trend_service.compute_trends(current_user.id, threshold)
    return render_template("analytics/_trends.html", report=report)

@analytics_bp.route("/analytics/trends/group/<group_name>")
@login_required
def trends_group_detail(group_name):
    """HTMX partial: group-level trend drill-down."""
    # Returns _trends_detail.html showing all items in the group
```

**Template:**

1. **Data sufficiency banner:** If `report.data_sufficiency == "preliminary"`, show a
   dismissable alert: "Trends are preliminary. At least 3 months of transaction history is
   needed for reliable trend detection." If "insufficient", show: "Not enough data for trend
   analysis. Continue using the app and check back after 3 months."

2. **Two columns on desktop (one on mobile):**
   - Left: "Trending Up" -- top 5 items with red up-arrow, category name (group: item),
     percentage change, dollar change.
   - Right: "Trending Down" -- top 5 items with green down-arrow, category name, percentage
     change, dollar change.

3. **Drill-down:** Clicking a category item triggers an HTMX GET to `trends_group_detail`
   which shows the group-level aggregate trend and all items in that group.

4. **Direction indicators:** Up arrows (`bi-arrow-up-right`) in red/danger color for increases.
   Down arrows (`bi-arrow-down-right`) in green/success color for decreases.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C13-1 | test_trends_tab_renders | seed_full_user_data (6+ months) | GET /analytics/trends (HX) | 200, trend lists present | New |
| C13-2 | test_trends_insufficient_data | seed_user (no data) | GET trends | "Not enough data" banner | New |
| C13-3 | test_trends_preliminary_banner | 3-month user | GET trends | "Trends are preliminary" banner | New |
| C13-4 | test_trends_up_list | Categories with increases | GET trends | Red arrows, percentage shown | New |
| C13-5 | test_trends_down_list | Categories with decreases | GET trends | Green arrows, percentage shown | New |
| C13-6 | test_trends_group_drilldown | seed_full_user_data | GET /analytics/trends/group/Auto (HX) | Group items listed with trends | New |
| C13-7 | test_trends_no_htmx_redirect | auth_client | GET /analytics/trends (no HX) | 302 redirect | Existing |

### F. Manual verification steps

1. Navigate to `/analytics`, click "Trends" tab.
2. Verify top-5 increasing and decreasing lists render.
3. Click a category item. Verify group-level drill-down expands.
4. Verify arrow indicators and color coding (red=up, green=down).
5. With a new user (< 3 months of data), verify the data sufficiency banner appears.
6. Toggle dark mode. Verify all elements render correctly.
7. Test at 375px width. Verify two columns stack to one.

### G. Downstream effects

None. Replaces the trends tab placeholder.

### H. Rollback notes

Revert analytics route to placeholder, delete new templates. No migration.

---

## Commit 14: CSV Export for All Analytics Views

### A. Commit message

```text
feat(export): add CSV export endpoints for calendar, year-end, variance, and trends tabs
```

### B. Problem statement

Every tab on the analytics page needs an "Export CSV" button that downloads the current view's
data as a CSV file. The export respects the user's current filters and time window.

### C. Files modified

- `app/services/csv_export_service.py` -- New file: CSV generation.
- `app/routes/analytics.py` -- Add `?format=csv` handling to each tab endpoint.
- `app/templates/analytics/_calendar_month.html` -- Add export button.
- `app/templates/analytics/_calendar_year.html` -- Add export button.
- `app/templates/analytics/_year_end.html` -- Add export button.
- `app/templates/analytics/_variance.html` -- Add export button.
- `app/templates/analytics/_trends.html` -- Add export button.
- `tests/test_services/test_csv_export_service.py` -- New file.
- `tests/test_routes/test_analytics.py` -- Add CSV export tests.

### D. Implementation approach

**CSV service (`app/services/csv_export_service.py`):**

```python
import csv
import io

def export_calendar_csv(data: MonthSummary | YearOverview) -> str:
    """Export calendar data as CSV.

    Columns: Date, Name, Category, Amount, Status, Account.
    For year overview: one row per transaction across all months.
    """

def export_year_end_csv(data: dict) -> str:
    """Export year-end summary as multi-section CSV.

    Sections separated by blank lines with section headers:
    [Income and Taxes], [Spending by Category], [Transfers],
    [Net Worth Monthly], [Debt Progress], [Savings Progress].
    """

def export_variance_csv(report: VarianceReport) -> str:
    """Export variance data as CSV.

    Columns: Category Group, Category Item, Estimated, Actual,
    Variance ($), Variance (%). Includes transaction-level rows
    beneath each category.
    """

def export_trends_csv(report: TrendReport) -> str:
    """Export trends data as CSV.

    Columns: Category Group, Category Item, Period Average,
    Direction, Change (%), Change ($), Threshold, Flagged.
    """
```

Each function returns a CSV string using Python's `csv` module with `io.StringIO`.

**Route-level CSV handling:**

Each tab endpoint checks for `format=csv` query parameter:

```python
@analytics_bp.route("/analytics/calendar")
@login_required
def calendar_tab():
    # ... existing data loading ...
    if request.args.get("format") == "csv":
        csv_content = csv_export_service.export_calendar_csv(data)
        response = make_response(csv_content)
        response.headers["Content-Disposition"] = (
            f"attachment; filename=calendar_{year}_{month:02d}.csv"
        )
        response.headers["Content-Type"] = "text/csv"
        return response
    # ... existing HTML rendering ...
```

**Export buttons in templates:**

```html
<a href="{{ url_for('analytics.calendar_tab', view='month',
           year=year, month=month, format='csv') }}"
   class="btn btn-sm btn-outline-secondary">
  <i class="bi bi-download"></i> Export CSV
</a>
```

The CSV download links are regular `<a>` tags (not HTMX) so the browser handles the file
download.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C14-1 | test_calendar_csv_export | seed_full_user_data | GET /analytics/calendar?format=csv | 200, Content-Type: text/csv | New |
| C14-2 | test_calendar_csv_content | seed_full_user_data with txns | GET calendar?format=csv | CSV contains transaction names and amounts | New |
| C14-3 | test_year_end_csv_export | seed_full_user_data | GET /analytics/year-end?format=csv&year=2026 | 200, Content-Type: text/csv | New |
| C14-4 | test_year_end_csv_sections | seed_full_user_data | GET year-end?format=csv | CSV has [Income and Taxes] section header | New |
| C14-5 | test_variance_csv_export | seed_full_user_data | GET /analytics/variance?format=csv | 200, Content-Type: text/csv | New |
| C14-6 | test_variance_csv_includes_transactions | seed_full_user_data | GET variance?format=csv | CSV has category group AND transaction rows | New |
| C14-7 | test_trends_csv_export | seed_full_user_data | GET /analytics/trends?format=csv | 200, Content-Type: text/csv | New |
| C14-8 | test_csv_requires_auth | client | GET calendar?format=csv | 302 redirect to login | New |
| C14-9 | test_csv_content_disposition | seed_full_user_data | GET calendar?format=csv | Content-Disposition: attachment header present | New |
| C14-10 | test_export_calendar_empty | No transactions | export_calendar_csv(empty) | CSV with headers only, no data rows | New |

### F. Manual verification steps

1. Navigate to each analytics tab. Click the "Export CSV" button.
2. Verify a CSV file downloads with appropriate filename.
3. Open each CSV in a spreadsheet. Verify columns, data, and formatting.
4. Verify year-end CSV has section headers separating different data blocks.
5. Verify variance CSV includes both category totals and transaction detail rows.

### G. Downstream effects

None. Adds functionality to existing analytics tab endpoints.

### H. Rollback notes

Remove `?format=csv` handling from analytics routes, delete CSV service. No migration.

---

## Commit 15: Remove Old Charts Route and Templates, Final Cleanup

### A. Commit message

```text
refactor(charts): remove old /charts route, templates, and JS; clean up chart_data_service
```

### B. Problem statement

The old `/charts` route and its 8 templates and 7 JS files are now replaced by the analytics
page and dashboard. They must be removed to eliminate dead code. The `chart_data_service.py`
must be pruned of functions only used by the old charts route, while preserving functions used
by other pages.

### C. Files modified

- `app/routes/charts.py` -- Delete file entirely (or gut and redirect `/charts` to
  `/analytics`).
- `app/__init__.py` -- Remove `charts_bp` registration. Add redirect.
- `app/templates/charts/*.html` -- Delete all 8 template files.
- `app/static/js/chart_balance.js` -- Delete.
- `app/static/js/chart_spending.js` -- Delete.
- `app/static/js/chart_budget.js` -- Delete.
- `app/static/js/chart_amortization.js` -- Delete.
- `app/static/js/chart_net_worth.js` -- Delete.
- `app/static/js/chart_net_pay.js` -- Delete.
- `app/static/js/chart_slider.js` -- Delete.
- `app/services/chart_data_service.py` -- Remove unused functions: `get_balance_over_time`,
  `get_spending_by_category`, `get_budget_vs_actuals`, `_format_period_label`,
  `_get_period_range`, `_get_expense_transactions`. Keep: `get_amortization_breakdown`
  (used by loan dashboard), `get_net_worth_over_time` (used by retirement dashboard),
  `get_net_pay_trajectory` (used by salary pages), `_to_chart_float`,
  `_calculate_account_balances`.
- `app/templates/base.html` -- Remove chart JS `<script>` includes for deleted files.
- `tests/test_routes/test_charts.py` -- Delete or replace with redirect test.
- `tests/test_services/test_chart_data_service.py` -- Remove tests for deleted functions.

### D. Implementation approach

**Redirect for old bookmarks:**

Instead of deleting `charts.py` entirely, keep a minimal redirect:

```python
@charts_bp.route("/charts")
@login_required
def dashboard():
    """Redirect old /charts URL to /analytics."""
    return redirect(url_for("analytics.page"), code=301)
```

This ensures old bookmarks and any external links continue to work.

**Chart data service cleanup:**

Grep the entire codebase for each function name to confirm it is only called from the charts
route before deleting:
- `get_balance_over_time` -- only called in `charts.py`
- `get_spending_by_category` -- only called in `charts.py`
- `get_budget_vs_actuals` -- only called in `charts.py`
- `get_amortization_breakdown` -- called in `loan.py` dashboard
- `get_net_worth_over_time` -- called in `retirement.py`
- `get_net_pay_trajectory` -- called in `salary.py`

**Script include cleanup:**

Remove `<script src>` tags for deleted JS files from `base.html`. The remaining chart JS
files (growth_chart, payoff_chart, retirement_gap_chart, debt_strategy, chart_theme) stay.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C15-1 | test_charts_redirects_to_analytics | auth_client | GET /charts | 301 redirect to /analytics | New |
| C15-2 | test_chart_fragments_gone | auth_client | GET /charts/balance-over-time (HX) | 301 or 404 | New |
| C15-3 | test_loan_chart_still_works | auth_client, loan account | GET loan dashboard | Amortization chart renders | New |
| C15-4 | test_salary_chart_still_works | auth_client, salary profile | GET salary projection | Net pay chart renders | New |
| C15-5 | test_no_dead_script_includes | auth_client | GET any page | No 404s for deleted JS files | New |
| C15-6 | test_chart_data_service_retained_functions | seed_full_user_data | Call get_amortization_breakdown | Returns valid data | Mod |

### F. Manual verification steps

1. Navigate to `/charts`. Verify 301 redirect to `/analytics`.
2. Navigate to a loan dashboard. Verify the amortization chart still renders.
3. Navigate to salary projection. Verify the net pay chart still renders.
4. Open browser DevTools network tab. Verify no 404 errors for missing JS files.
5. Run `pylint app/ --fail-on=E,F` to confirm no import errors from deleted code.

### G. Downstream effects

- Any bookmarks or external links to `/charts` are redirected.
- The `chart_data_service.py` file shrinks significantly but retains functions used elsewhere.

### H. Rollback notes

Restore deleted files from git history. Re-register `charts_bp`. This is the riskiest
commit to roll back because it deletes multiple files. Before committing, verify all
non-charts pages that use Chart.js still function.

---

## Commit Checklist

| # | Commit Message | Summary |
|---|----------------|---------|
| 1 | `fix(charts): add year to x-axis labels crossing year boundary; add Section 8 settings columns` | X-axis date fix for all charts; migration adding large_transaction_threshold, trend_alert_threshold, anchor_staleness_days to user_settings |
| 2 | `feat(analytics): create /analytics route with lazy-loaded tab structure` | Analytics page shell with 4 tab pills, HTMX lazy-loading, nav bar rename Charts->Analytics |
| 3 | `feat(calendar): add calendar service for month/year expense aggregation and 3rd paycheck detection` | Calendar engine: month grouping, day assignment, large/infrequent flagging, 3rd paycheck detection, month-end balance |
| 4 | `feat(variance): add budget variance analysis service with pay-period, monthly, and annual views` | Variance engine: estimated vs actual per category, three time windows, drill-down data |
| 5 | `feat(trends): add spending trend detection service with linear regression and threshold flagging` | Trend engine: rolling window analysis, linear regression, threshold flagging, top-5 lists |
| 6 | `feat(dashboard): add dashboard service aggregating balance, bills, alerts, paycheck, goals, and debt data` | Dashboard service: 7 sections aggregating data from existing services |
| 7 | `feat(dashboard): add summary dashboard page with mark-paid, true-up, and HTMX refresh` | Dashboard template with all sections, mark-as-paid interaction, inline true-up, HTMX refresh |
| 8 | `feat(dashboard): make dashboard the default route, move grid to /grid` | Route swap: dashboard at /, grid at /grid, nav bar updated |
| 9 | `feat(calendar): add month detail and year overview calendar views to analytics page` | Calendar display: 7-column month grid with markers, 4x3 year overview with totals, HTMX navigation |
| 10 | `feat(year-end): add year-end summary service aggregating income, taxes, spending, transfers, net worth, and debt progress` | Year-end engine: W-2-style income/tax, spending by category, net worth trend, debt/savings progress |
| 11 | `feat(year-end): add year-end summary view with income/tax breakdown, spending, and net worth chart` | Year-end display: structured annual report with Chart.js net worth line chart |
| 12 | `feat(variance): add budget variance view with bar chart, drill-down table, and time window toggle` | Variance display: grouped bar chart, category table with expandable rows, period/month/year toggle |
| 13 | `feat(trends): add spending trend view with top-5 lists and category drill-down` | Trends display: top-5 up/down lists, direction indicators, group drill-down, data sufficiency banner |
| 14 | `feat(export): add CSV export endpoints for calendar, year-end, variance, and trends tabs` | CSV export: ?format=csv on each analytics tab, Content-Disposition download |
| 15 | `refactor(charts): remove old /charts route, templates, and JS; clean up chart_data_service` | Cleanup: delete 8 templates, 7 JS files, prune unused service functions, add /charts->analytics redirect |
