# Fixes and Improvements

Items are categorized by priority. Critical items are data correctness or broken workflow bugs
that should be fixed immediately. Deferred items are UX improvements assigned to a specific
future phase.

---

## Critical -- Fix Now

These bugs produce incorrect data or block normal workflows. Fix before starting new feature
work.

### Taxes: Calculating on Gross Pay Not Taxable Income

- **Severity:** Critical
- **Impact:** Every projected paycheck amount in the grid is wrong. Pre-tax deductions (401(k),
  HSA, etc.) must reduce the taxable base before federal and state income tax are applied.
- **Area:** Paycheck calculator service, tax calculator service

### Recurrence Rule: Every 6 Months Calculates Incorrectly

- **Severity:** Critical
- **Impact:** Semi-annual expenses land in the wrong pay periods. Appears to calculate 6 pay
  periods (~3 months) instead of 6 calendar months.
- **Area:** Recurrence engine
- **Follow-up:** After fixing, audit all other recurrence patterns (every_n_periods, monthly,
  monthly_first, annual) for correctness. See Roadmap v4 section 5.2.

### Net Biweekly Mismatch: Salary Profile vs Grid

- **Severity:** Critical
- **Impact:** The net biweekly amount on the salary profile page does not match what the grid
  shows for biweekly salary income. One or both numbers may be wrong. Could be a display issue
  or a calculation divergence between two code paths.
- **Area:** Salary profile view, grid income rendering, paycheck calculator

### Cannot Edit Raises and Deductions

- **Severity:** High
- **Impact:** No UI to modify an existing raise or deduction. User must delete and re-create to
  fix a mistake.
- **Area:** Salary routes, salary templates (raise and deduction forms)

### Raises: Page Refresh Required Before Adding a Second

- **Severity:** High
- **Impact:** After adding a raise on /salary, the page must be manually refreshed before a
  second raise can be added. HTMX response likely does not re-render the form correctly.
- **Area:** Salary raise form, HTMX partial response

### Escrow: Cannot Add Amount When Inflation Rate Is Present

- **Severity:** High
- **Impact:** Adding an escrow component with an inflation rate fails. Blocks realistic escrow
  modeling.
- **Area:** Mortgage escrow form, escrow validation/schema

### Escrow: Hard Refresh Required After Adding Component

- **Severity:** Medium
- **Impact:** After adding an escrow component, the total monthly payment does not update until
  a manual page refresh. The HTMX response does not trigger recalculation of the payment
  summary.
- **Area:** Mortgage dashboard, escrow HTMX partial, payment summary rendering

### Paycheck Calibration (Distinct Task After Tax Bug Fix)

- **Severity:** High
- **Dependency:** Fix the tax-on-gross-pay bug first.
- **Feature:** A one-time calibration workflow: enter actual line-item values from a real pay
  stub (federal tax withheld, state tax withheld, Social Security, Medicare, each deduction
  amount). The app back-calculates effective rates and stores them as overrides for future
  projections.
- **Impact:** Grounds projected paychecks in real withholding data. Directly addresses the net
  biweekly mismatch between the salary profile and the grid.
- See Roadmap v4 section 3.10 for full specification.

### Pension: Date Validation Missing

- **Severity:** Medium
- **Impact:** Earliest Retirement Date and Planned Retirement Date accept values before the
  Hire Date or before today. Allows nonsensical pension profiles.
- **Area:** Pension form validation (client-side and server-side)

### Retirement: Stale Settings Message

- **Severity:** Low
- **Impact:** A message stating retirement settings have moved to Settings > Retirement still
  displays and should be removed.
- **Area:** Retirement template

---

## Deferred -- UX/Grid Overhaul Phase

These are UX improvements to be addressed in the dedicated UX/Grid Overhaul sprint. See
Roadmap v4 section 4.

### Grid: Category/Transaction Name Confusion

- Showing the Group and Item Name for the category without the transaction name gets confusing
  when there are multiple transactions per category item. Hovering works but is extra work.
- **Plan:** Prototype two layouts side by side and compare:
  - Option A: Full row headers per transaction (compact styling to manage vertical space).
  - Option B: Enhanced current layout with visible transaction name labels.
- The spreadsheet being replaced used full row headers, so that pattern is familiar.

### Grid: Footer Takes Up Too Much Space

- The footer consumes a lot of screen space. Could be condensed or made sticky at the top or
  bottom of the page.

### Grid: Redundant Pay Period Date Format

- Pay period column headers show both MM/DD and MM/DD/YY. Only one format is needed.
  Simplifying frees horizontal space for transactions.

### Grid: "Done" to "Paid" Terminology Change

- Change the status label from "Done" to "Paid" for expenses. "Received" already exists for
  income. Recommended approach: add a `display_label` or `context` column to `ref.statuses` so
  the ID remains constant and only the display text changes.
- **Ripple effects:** Templates, tests, status badges, filter dropdowns, documentation.

### Salary: Deduction Frequency Display

- The amount and "per year" columns on deductions are squished together. Frequency should be
  displayed more clearly (e.g., "24x/year", "26x/year").

### Taxes: Page Layout Reorganization

- Move adjustable settings to the top of Tax Config. Move static bracket tables to a
  collapsible section at the bottom. Hide previous tax years (e.g., 2025) by default.

### Accounts: Parameter Setup Not Discoverable

- Setting rates and parameters for accounts requires clicking an icon in the corner of the
  account card. This is not intuitive. Parameters should be prompted during account creation or
  indicated with a "Setup required" badge.

### Mortgage: Parameter Page Redirect

- Mortgage accounts redirect to a separate page for parameters. Consider inline fields that
  appear based on account type selection during creation. Evaluate alongside the account
  parameter setup UX improvement above.

### Charts: Balance Over Time Line Contrast

- The "standard payments" line on the mortgage Balance Over Time chart has low contrast and is
  difficult to see. Increase line weight, change color, or add a dash pattern.

### Charts: Balance Over Time Time Frame Control

- The chart defaults to one year, which shows flat lines for mortgages and retirement accounts.
  Add time frame controls (1 year, 5 years, 10 years, full term) with intelligent defaults
  based on account type.

---

## Deferred -- Recurring Transaction Improvements Phase

See Roadmap v4 section 5.

### Mortgage: No Auto-Payment Recurring Transaction

- After adding a mortgage account with a monthly payment, there is no option to create a
  recurring transfer from checking/savings to the mortgage account. The app should offer to set
  this up automatically after mortgage parameters are saved.

### Paycheck: Actual Value Entry

- Superseded by the Paycheck Calibration feature (see Critical section above). The user can
  already enter an actual net pay amount in the grid. The calibration feature addresses the
  deeper need of deriving accurate tax and deduction rates from real pay stub data.

---

## Deferred -- Phase 9: Smart Features

See Roadmap v4 section 6.

### Seasonal/Fluctuating Bills

- Electricity and similar bills vary seasonally. Currently the only method is to set a flat
  rate and adjust the actual. Phase 9 adds seasonal expense forecasting with historical data
  entry and trend-adjusted projections.

---

## Deferred -- Evaluate Later

### Account Default Rates

- Each account type should have a sensible default rate. Lower priority; revisit when
  addressing account parameter UX in the overhaul phase.
