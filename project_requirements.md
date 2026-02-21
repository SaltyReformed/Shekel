# Budget App â€” Requirements & Architecture Document

**Version:** 1.6
**Date:** February 17, 2026
**Stack:** Flask (Python) Â· React Â· PostgreSQL

---

## 1. Project Overview

A personal budget application that replaces a biweekly-paycheck-based spreadsheet. The app organizes finances around **pay periods** rather than calendar months, mapping every expense to a specific paycheck and projecting balances forward over a ~2-year horizon.

### Core Philosophy

- **Paycheck-centric:** Every dollar of income and every expense belongs to a specific pay period.
- **Projection-forward:** The app always shows the long-term ripple effect of any change.
- **Actuals + budget hybrid:** Users budget estimates, record actuals, mark line items "done," and the remainder flows naturally.

---

## 2. Feature Priorities (MVP â†’ Future)

| Phase                                     | Features                                                                                                                                                                                                                                                         |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 1 â€” Foundation**                | Authentication (JWT), structured application logging, PostgreSQL audit triggers, project scaffolding, pay period generation, basic transaction CRUD, budget grid (read-only â†’ editable)                                                                        |
| **Phase 2 â€” Core Workflow**             | Recurrence rules with auto-generation and manual overrides, balance roll-forward (calculated on read), mark-as-done, "true-up" anchor balance, paycheck calculator (annual salary â†’ raises â†’ gross â†’ deductions â†’ taxes â†’ net), salary projection view |
| **Phase 3 â€” Scenarios**                 | What-if scenarios (named, saved, side-by-side comparison), undo/redo for bulk operations                                                                                                                                                                         |
| **Phase 4 â€” Accounts & Savings**        | Savings account modeling with transfers in/out, savings goals (target amount, target date, auto-calculated contributions)                                                                                                                                        |
| **Phase 5 â€” Visualization**             | Charts & visualizations (balance over time, category breakdowns, spending trends, scenario comparison, net pay trajectory)                                                                                                                                       |
| **Phase 6 â€” Notifications**             | In-app alerts and email notifications for upcoming large expenses, low projected balances, and savings goal milestones                                                                                                                                           |
| **Phase 7 â€” Smart Features**            | Smart estimates for variable expenses, expense inflation with per-template and global rates, deduction inflation at open enrollment                                                                                                                              |
| **Phase 8 â€” Ops, Security & Hardening** | MFA/TOTP authentication, automated PostgreSQL backups, Docker volume snapshots, audit log retention, export to CSV/Excel, mobile-responsive layout                                                                                                               |

---

## 3. Detailed Requirements

### 3.1 Pay Periods

- The user provides a **start date** (a known payday) and a **cadence** (biweekly, defaulting to every 14 days).
- The app **auto-generates pay periods** forward for a configurable horizon (default: 2 years, ~52 periods).
- Each pay period is defined by a **start date** (payday) and an **end date** (day before next payday).
- The user can regenerate or extend the horizon at any time.

### 3.2 Income

- Each income entry belongs to a specific pay period.
- Income types include:
  - **Recurring salary** â€” auto-generated per recurrence rule (e.g., every pay period).
  - **Recurring other** â€” e.g., phone stipend, with its own recurrence rule.
  - **One-time income** â€” e.g., tax return, other windfalls.
  - **Transfer from savings** â€” modeled as income to checking (see Â§3.7).
- Each income entry has: **name, category, estimated amount, actual amount (nullable), status** (projected / received).

### 3.3 Expenses

- Each expense entry belongs to a specific pay period.
- Expense categories are hierarchical with a top-level group and a specific name:
  - Examples: `Auto: Car Payment`, `Home: Electricity`, `Family: Spending Money: Kayla`
  - The app should support at least two levels of categorization (group + name). Three levels optional.
- Each expense entry has:
  - **Name** and **category**
  - **Estimated amount** (the budgeted figure)
  - **Actual amount** (nullable â€” filled in when the real cost is known)
  - **Status:** `projected` â†’ `done`
  - **Remainder behavior:** When an item is marked `done`, the balance calculator uses the actual amount instead of the estimate. Any difference (e.g., budgeted $500, spent $487) naturally stays in the checking balance â€” no explicit reallocation needed. The grid displays both amounts so the user can see the savings at a glance.

### 3.4 Recurrence Rules

Each income or expense template can have a recurrence rule:

| Pattern              | Example                                        | Rule Definition                           |
| -------------------- | ---------------------------------------------- | ----------------------------------------- |
| Every pay period     | Fuel, Kayla's spending money                   | `every_period`                            |
| Every Nth pay period | Car payment (every 2nd), insurance (every 2nd) | `every_n_periods`, `n=2`, `offset=0 or 1` |
| Monthly on a date    | Disney+ on the 15th                            | `monthly`, `day=15`                       |
| Annual on a date     | Property tax in October                        | `annual`, `month=10`, `day=1`             |
| One-time             | Car maintenance, Christmas                     | `once`, assigned to a specific period     |

**Auto-generation behavior:**

- When periods are generated (or a rule is created/updated), the app populates expense entries into future periods based on the rule.
- **Manual overrides** are always allowed: the user can change the amount, move an entry to a different period, delete an auto-generated entry, or add ad hoc entries.
- Overridden entries are flagged so they aren't clobbered if rules are re-applied.

### 3.5 Balance Roll-Forward & True-Up

This is the financial backbone of the app.

**Design approach: Calculate on read (no stored balances).**

Balances are never stored in the database. Every time the UI requests balance data, the API calculates the full chain from the anchor forward in real time. This approach was chosen because:

- It is the simplest to keep correct â€” there is no risk of stored balances drifting out of sync with transactions.
- The balance calculator is a **pure function**: given an anchor and a list of transactions, it returns a list of balances. No side effects, no state to manage, easy to test.
- At the scale of a single-user budget (~52 periods Ã— ~30 line items = ~1,500 rows), PostgreSQL can query and sum this in milliseconds.
- If performance ever becomes a concern, a caching layer can be added later without changing the core logic.

**Calculation rules:**

- **Checking account balance** is the primary tracked balance.
- The user sets an **anchor balance**: the real, actual checking account balance as of a specific pay period.
- From the anchor forward, each period's **projected end balance** is calculated:
  ```
  end_balance[n] = end_balance[n-1] + total_income[n] - total_expenses[n]
  ```
  For the anchor period:
  ```
  end_balance[anchor] = anchor_balance + income[anchor] - expenses[anchor]
  ```
  (Adjusted based on what's already reflected in the anchor balance â€” i.e., income/expenses with status `received`/`done` are excluded from the calculation if they're already baked into the anchor.)
- **Amount selection per transaction:**
  - Status `done` or `received` â†’ use `actual_amount`
  - Status `projected` â†’ use `estimated_amount`
  - If `actual_amount` is NULL on a `done` item, fall back to `estimated_amount`
- **True-up workflow:** The user periodically updates the anchor balance and moves the anchor to the current period. Past periods become "locked" historical records.
- All future periods recalculate automatically from the new anchor.

### 3.6 What-If Scenarios

- A **scenario** is a named, saved alternate version of the budget.
- The **baseline** scenario is the user's actual working budget.
- The user can create a new scenario by **cloning the baseline** and then making changes:
  - Add/remove/modify income or expenses
  - Change recurrence rules
  - Adjust salary amounts
  - Add large one-time expenses (e.g., "new car down payment")
- Scenarios are **independent** â€” changes to one don't affect others.
- The app supports **side-by-side comparison** of any two scenarios, showing:
  - Differences in projected end balance over time
  - Net difference per period
  - Divergence point (where they start to differ)
- Example use cases:
  - "What if I get a 3% raise starting in June?"
  - "What if I add a $400/month car payment starting in October?"
  - "What if I pay off the Capital One card 3 months early?"

### 3.7 Savings Account Modeling

- The app tracks **separate account balances**: at minimum, checking and savings (could support multiple named accounts later).
- **Transfers** between accounts are modeled as paired transactions:
  - A transfer from checking to savings = an expense in checking + income in savings.
  - A transfer from savings to checking = the reverse.
- The **savings balance** rolls forward the same way checking does.
- The Emergency Fund running total from the spreadsheet becomes the savings account balance.
- **Savings goals** (Phase 5):
  - Define a goal: name, target amount, target date.
  - The app calculates the required contribution per period.
  - Track progress toward the goal over time.
  - Metrics carried over from the spreadsheet: paychecks-in-savings, months-in-savings, years-in-savings.

### 3.8 Expense Assignment Logic

Since expense assignment is a **mix of due dates and manual decisions:**

- When creating a recurring expense with a rule, the user specifies **which pay period(s)** it falls into. The rule engine handles the rest.
- For monthly bills, the app calculates which pay period contains the bill's due date and assigns it there by default.
- The user can always **drag or reassign** an expense to a different period (override).
- The grid view makes the assignment visible and editable.

### 3.9 Charts & Visualizations (Phase 6)

- **Balance over time:** Line chart showing projected checking (and savings) balance across all periods.
- **Spending by category:** Bar or pie chart showing totals per top-level category (Auto, Home, Food, etc.) for a selected time range.
- **Budget vs. actuals:** Bar chart comparing estimated vs. actual per category or per period.
- **Scenario comparison:** Overlay line chart comparing balance trajectories of two scenarios.

### 3.10 Smart Estimates (Phase 7)

- For variable expenses (groceries, fuel), the app can **suggest an estimate** for future periods based on a rolling average of the last N actuals.
- The suggestion is displayed but never auto-applied â€” the user accepts or adjusts.

### 3.11 Paycheck Calculator & Salary Projection (Phase 2)

The user's income in the budget is a **net biweekly paycheck** â€” the amount that actually lands in checking. To project this accurately over a 2-year horizon, the app models the full pipeline from annual salary to net pay, including raises, taxes, and deductions â€” some of which inflate over time.

**Calculation pipeline:**

```
Annual Salary
  â†’ Apply raises (merit, COLA, custom) at effective dates
  = Projected Annual Salary
  â†’ Ã· 26 pay periods
  = Gross Biweekly Pay
  â†’ âˆ’ Pre-tax deductions (retirement, health insurance, HSA, FSA, dental, vision)
  = Taxable Biweekly Income
  â†’ âˆ’ Federal income tax (withholding estimate based on bracket + filing status)
  â†’ âˆ’ State income tax (flat rate or bracket-based, configurable)
  â†’ âˆ’ FICA: Social Security (6.2% up to wage base cap) + Medicare (1.45%, +0.9% above threshold)
  = Net Biweekly Pay (after tax)
  â†’ âˆ’ Post-tax deductions (Roth 401k, life insurance, other)
  = Net Biweekly Paycheck (deposited to checking)
```

**Salary profile (one per income source, per scenario):**

A salary profile stores compensation details for a single income source. Multiple profiles per scenario are supported (e.g., "Primary Job", "Side Job", "Spouse"), each with its own salary, raises, deductions, and tax treatment:

- **Name:** identifies this income source (e.g., "Primary", "Freelance", "Spouse")
- **Annual salary:** the base number that raises apply to (e.g., $66,768)
- **Filing status:** single, married filing jointly, married filing separately, head of household
- **Federal allowances / W-4 elections:** used for withholding estimate
- **State:** determines state tax rate or bracket
- **Pay periods per year:** defaults to 26 (biweekly); can differ per profile (e.g., a monthly side job would be 12)

**Raises:**

- **Merit raise:** recurring annual, configurable month (default: January), percentage (default: 2.5%)
- **COLA raise:** recurring annual, configurable month (default: July), percentage (default: 2.5%)
- **Custom raise:** one-time, specific effective date, percentage or flat amount (e.g., promotion)
- Raises apply to the **annual salary** and compound in chronological order.
- Example: $66,768 salary â†’ 2.5% merit in January â†’ $68,437 â†’ 2.5% COLA in July â†’ $70,148
- Raises are **scenario-aware** â€” each scenario can have different raise assumptions.

**Deductions:**

Each deduction is a separate record with:

- **Name:** e.g., "401(k)", "Health Insurance", "Dental", "Vision", "HSA"
- **Deduction type:** `pre_tax` or `post_tax` (determines where in the pipeline it's subtracted)
- **Calculation method:** `flat` (fixed dollar amount per period) or `percentage` (of gross)
- **Amount:** the flat dollar amount or percentage
- **Deductions per year:** controls how often this deduction is taken. Common values:
  - `26` = every paycheck (default; e.g., 401k, tax withholding)
  - `24` = first 2 paychecks per calendar month only (e.g., health insurance, dental, vision). **The 3rd paycheck in a month skips this deduction.**
  - `12` = once per month (first paycheck of each month only)
- **Annual cap:** optional (e.g., 401(k) annual contribution limit)
- **Inflation-enabled:** for deductions like insurance premiums that increase annually
- **Inflation rate:** annual rate (e.g., 5% for health insurance); NULL = use global default
- **Inflation effective month:** when the increase kicks in (e.g., month of open enrollment renewal)

**3rd paycheck handling:**

In biweekly pay (26 checks/year), 10 months have exactly 2 paychecks and 2 months have 3. Many employers only deduct benefits from the first 2 paychecks of each calendar month â€” meaning the 3rd paycheck in those months has higher net pay because benefit deductions are skipped.

The paycheck calculator determines whether a given pay period is the 3rd paycheck in its calendar month by counting how many pay period start dates fall in that month. If this is the 3rd, deductions with `deductions_per_year = 24` are skipped. This correctly models the real-world behavior where benefit paychecks alternate with "bonus" paychecks.

This also interacts with FICA: the Social Security wage base cap means SS deductions may stop partway through the year for higher salaries, further increasing net pay in later periods.

**Tax estimation:**

The app provides a **reasonable estimate** of tax withholding, not exact payroll calculation. This is disclosed to the user.

- **Federal income tax:** estimated using current-year brackets, standard deduction, and filing status. The app stores a `tax_brackets` table with bracket ranges and rates that the user (or a future update) can adjust.
- **State income tax:** configurable as a flat rate or simple brackets. A `state_tax_configs` table stores rates per state.
- **FICA:**
  - Social Security: 6.2% of gross, up to the annual wage base (e.g., $168,600 for 2025). The app tracks cumulative wages across pay periods within a calendar year and stops withholding once the cap is hit â€” this means net pay increases slightly in later periods of high-salary years.
  - Medicare: 1.45% of all gross wages, plus an additional 0.9% on wages above $200,000 (single) or $250,000 (married filing jointly).
- Tax brackets and FICA caps can be updated annually by the user. The app ships with current-year defaults.

**How it integrates with the budget:**

- Each salary profile is linked to its own salary income template in the budget.
- When the recurrence engine generates salary transactions for future pay periods, it calls the **paycheck calculator service** for each active profile to compute the net amount for each period.
- The calculator applies the correct salary (post-raises), deductions (post-inflation), and taxes (using year-appropriate brackets) for each period's date.
- Multiple income sources appear as separate rows in the budget grid, each with their own net pay amount.
- The user can still **override** any individual paycheck amount in the grid.
- A **paycheck breakdown view** shows the full calculation for any selected period and profile: gross, each deduction line, each tax line, and net.

**Interaction with scenarios:**

All of these can differ between scenarios: annual salary, raise percentages, deduction amounts, inflation rates, and which income sources are active. This enables questions like:

- "What if I increase my 401(k) contribution from 6% to 10%?"
- "What if health insurance premiums go up 8% instead of 5% at renewal?"
- "What if I only get a 1% merit raise?"
- "What if I pick up a side job paying $20,000/year?"
- "What if my spouse starts working?"

### 3.12 Expense Inflation Adjustment (Phase 7)

For long-range accuracy, recurring expenses can optionally be adjusted for inflation. Without this, a 2-year projection assumes today's prices hold steady, which understates future costs.

**Per-template inflation settings:**

- **Enable inflation adjustment:** off by default. The user opts in per expense template.
- **Annual inflation rate:** user-configurable per template, with a global default (e.g., 3%).
- **Inflation start date:** the date from which inflation adjustments begin (defaults to the current period â€” no retroactive adjustment).

**How it works:**

- When enabled, the recurrence engine multiplies the base amount by `(1 + annual_rate) ^ (years_from_start)` for each future period.
- The calculation uses fractional years based on pay period dates, so the increase is gradual rather than a single annual jump.
- Example: Groceries at $500/period with 3% annual inflation â†’ $500.00 today, ~$507.50 in 6 months, ~$515.00 in 1 year, ~$530.45 in 2 years.
- Inflated amounts are displayed with an indicator (e.g., a small "â†‘" icon or subtle highlight) so the user knows the number includes an inflation adjustment.
- The user can **override** any individual period's amount, which locks it and prevents further inflation adjustment for that entry.

**Global vs. per-template rates:**

- A **global default rate** is set in user settings (e.g., 3% based on CPI).
- Individual templates can override this with a custom rate (e.g., 5% for health insurance, 2% for internet).
- Some categories naturally inflate faster than others, so per-template control is important.

**Interaction with scenarios:** Inflation rates can differ between scenarios, enabling questions like: "What if inflation runs at 5% instead of 3% â€” how does my balance look in 2 years?"

### 3.13 Authentication (Phase 1)

Built from the start to support hosting.

- **Registration:** Email + password. Passwords hashed with bcrypt.
- **Login:** Returns a JWT access token (short-lived, ~15 min) and a refresh token (longer-lived, ~7 days, stored in an HTTP-only cookie).
- **Protected routes:** All API endpoints except `/api/auth/register` and `/api/auth/login` require a valid JWT in the `Authorization: Bearer <token>` header.
- **Session management:** The React frontend stores the access token in memory (not localStorage) and uses the refresh token cookie to silently renew it.
- **Password reset:** Email-based reset flow (requires email service â€” can stub with console logging in development).
- **Single-user focus for now:** No roles or permissions. Every authenticated user sees only their own data. Multi-user is supported by the data model (all tables have `user_id`), but admin features are out of scope.

**MFA / TOTP (table ready in Phase 1, feature built in Phase 8):**

The database schema includes an `auth.mfa_configs` table from day one so the schema is ready when MFA is implemented. The table stores the TOTP shared secret (encrypted), backup codes, and an enabled flag. The actual MFA feature is deferred to Phase 8 because TOTP is an additive step in the login flow â€” it doesn't require reworking the existing JWT/refresh token system.

When built, the login flow becomes:

1. `POST /api/auth/login {email, password}` â†’ verify password
2. If MFA enabled: return `{mfa_required: true, mfa_session_token: "..."}` (short-lived, ~5 min, single-use)
3. `POST /api/auth/login/mfa {mfa_session_token, totp_code}` â†’ verify TOTP code â†’ issue JWT + refresh token

MFA setup flow: user scans a QR code (generated from the TOTP secret), enters a confirmation code, and receives backup codes for recovery. Backup codes are single-use and hashed.

### 3.14 Audit Logging (Phase 1)

A complete, tamper-evident record of every data change in the system. This goes beyond the `operation_log` (which only captures bulk operations for undo/redo) â€” the audit log captures _every_ INSERT, UPDATE, and DELETE across all schemas.

**Implementation: PostgreSQL trigger-based.**

- A `system.audit_log` table stores one row per change.
- A generic PL/pgSQL trigger function is attached to every table that should be audited (all tables in `budget`, `salary`, and `auth` schemas; not `ref` since lookup data rarely changes; not `system` to avoid self-referential logging).
- The trigger fires AFTER INSERT, UPDATE, and DELETE, capturing:
  - `schema_name` and `table_name` â€” which table changed
  - `row_id` â€” primary key of the affected row
  - `action` â€” 'INSERT', 'UPDATE', 'DELETE'
  - `old_data` â€” JSONB of the row before change (NULL for INSERT)
  - `new_data` â€” JSONB of the row after change (NULL for DELETE)
  - `changed_fields` â€” for UPDATE only, an array of column names that actually changed
  - `user_id` â€” the authenticated user who made the change (set via `SET LOCAL app.current_user_id` at the start of each API request)
  - `ip_address` â€” client IP (set via session variable)
  - `request_id` â€” UUID correlating to the application log entry for the same request
  - `created_at` â€” timestamp

**Retention policy:** Audit log rows are append-only (no UPDATE or DELETE allowed on the audit table). Retention is configurable: default 1 year. A scheduled cleanup job archives or deletes rows older than the retention period.

**Querying:** The audit log is indexed on `(table_name, row_id)` for "show me the history of this specific record" queries, and on `(user_id, created_at)` for "show me everything this user changed" queries.

**Performance consideration:** Trigger-based auditing adds a small overhead to every write. At the scale of a single-user budget app, this is negligible. If performance ever matters, the audit trigger can be made asynchronous by writing to an unlogged table and flushing to the main audit table in batches.

### 3.15 Application Logging (Phase 1)

Structured, centralized application logs for debugging, monitoring, and security.

**Log levels:** DEBUG, INFO, WARNING, ERROR, CRITICAL (Python standard `logging` module).

**What gets logged:**

| Level   | Events                                                                                                        |
| ------- | ------------------------------------------------------------------------------------------------------------- |
| INFO    | Every API request (method, path, user_id, status code, duration_ms, request_id)                               |
| INFO    | Authentication events (login success, login failure, token refresh, password reset request, MFA verification) |
| INFO    | Business events (pay period generated, scenario cloned, balance true-up, bulk recurrence regenerated)         |
| WARNING | Failed authentication attempts, rate limit hits, invalid input rejected by validation                         |
| ERROR   | Unhandled exceptions, database connection failures, email delivery failures                                   |
| DEBUG   | SQL queries (development only), service method entry/exit, calculation inputs/outputs                         |

**Structured format (JSON):**

Every log entry is a JSON object for machine parseability:

```json
{
  "timestamp": "2026-02-18T14:30:00Z",
  "level": "INFO",
  "request_id": "a1b2c3d4-...",
  "user_id": 42,
  "method": "PUT",
  "path": "/api/transactions/123",
  "status": 200,
  "duration_ms": 45,
  "message": "Transaction updated",
  "extra": { "transaction_id": 123, "field_changed": "actual_amount" }
}
```

**Implementation:**

- **Request middleware:** Generates a UUID `request_id` at the start of each request. Attaches it to Flask's `g` object. All log entries within that request include the same `request_id`, enabling correlation between application logs and audit log entries.
- **Flask `after_request` handler:** Logs method, path, user_id, status code, and duration for every request.
- **Service-level logging:** Each service method logs significant business events at INFO and errors at ERROR.
- **Exception handler:** Global Flask error handler catches unhandled exceptions, logs the full traceback at ERROR, and returns a generic 500 response (no stack trace to the client).

**Log output:**

- **Development:** Pretty-printed to console (stdout).
- **Production:** JSON to stdout (for Docker log collection). Docker Compose captures stdout and can forward to a log aggregation service (ELK, Loki/Grafana, CloudWatch, etc.) if needed.
- **Log rotation:** In production, Docker handles log rotation via its logging driver config. No application-level file rotation needed.

**Security logging:**

- Failed login attempts include the email (but never the password) and client IP.
- Multiple failed logins from the same IP or for the same email are logged at WARNING.
- Password changes, MFA setup/disable, and token revocations are logged at INFO.
- Sensitive data (passwords, tokens, TOTP secrets) is **never** logged at any level.

### 3.16 Undo/Redo for Bulk Operations (Phase 3)

Bulk operations â€” such as regenerating transactions from a recurrence rule, cloning a scenario, or reassigning expenses across periods â€” should be reversible.

- **Operation log:** Before a bulk operation executes, the app snapshots the affected rows into an `operation_log` table.
- **Log entry structure:**
  - `operation_id` (UUID) â€” groups all changes from one action
  - `operation_type` â€” e.g., `recurrence_regenerate`, `scenario_clone`, `bulk_reassign`
  - `table_name` â€” which table was affected
  - `row_id` â€” the primary key of the affected row
  - `previous_state` â€” JSONB snapshot of the row before the change
  - `new_state` â€” JSONB snapshot after the change
  - `created_at` â€” timestamp
  - `undone` â€” boolean flag
- **Undo:** Restores all rows in an operation to their `previous_state`. Sets `undone = TRUE`.
- **Redo:** Restores all rows to their `new_state`. Sets `undone = FALSE`.
- **Scope:** Only the most recent N operations are undoable (configurable, default 20). Older entries are archived or deleted by a cleanup job.
- **UI:** An "Undo" toast notification appears after any bulk operation with a clickable undo button (disappears after ~10 seconds but the operation remains undoable from a history view).

### 3.17 Notifications (Phase 6)

Alerts for financial events the user should be aware of.

**In-App Notifications:**

- Displayed as a notification badge/bell icon in the app header.
- Notification types:
  - **Large upcoming expense:** Triggered when a projected expense in the next N periods (configurable, default 2) exceeds a user-defined threshold (e.g., $500).
  - **Low projected balance:** Triggered when any future period's projected end balance drops below a user-defined threshold (e.g., $100).
  - **Savings goal milestone:** Triggered when a savings goal reaches 25%, 50%, 75%, and 100% of its target.
  - **Unreconciled periods:** Reminder if a past pay period still has items in "projected" status.
- Notifications are generated by a background check that runs whenever balances are recalculated.
- Users can dismiss individual notifications or mark all as read.

**Email Notifications:**

- Opt-in per notification type in user settings.
- Sent via a transactional email service (e.g., SendGrid, AWS SES, or SMTP relay).
- Frequency: daily digest or immediate (user-configurable).
- Includes a direct link back into the app to the relevant pay period or account.

**Notification settings table:**

- `user_id`, `notification_type`, `enabled_in_app` (boolean), `enabled_email` (boolean), `threshold_amount` (nullable), `lookahead_periods` (nullable).

### 3.18 Data Backup & Recovery (Phase 8)

**Automated PostgreSQL Backups:**

- Scheduled via a cron job or a dedicated backup container in Docker Compose.
- Uses `pg_dump` to create compressed SQL backups.
- Frequency: daily at a configurable time (default: 2:00 AM).
- Retention policy: keep daily backups for 7 days, weekly backups for 4 weeks, monthly backups for 6 months.
- Backups stored locally in a mounted volume and optionally synced to an offsite location (e.g., S3 bucket or remote server via rsync).

**Docker Volume Snapshots:**

- The PostgreSQL data directory lives on a named Docker volume.
- A pre-backup script stops writes (or uses `pg_start_backup`/`pg_stop_backup`) and snapshots the volume.
- Volume snapshots provide a point-in-time recovery option independent of `pg_dump`.

**Recovery procedure (documented in README):**

- Restore from `pg_dump`: `pg_restore` into a fresh or existing database.
- Restore from volume snapshot: stop containers, replace volume, restart.
- Test restores should be run periodically (add a reminder to the app's own notification system).

### 3.19 Data Import

- The app will **not** import data from the existing spreadsheet.
- The user starts fresh from the current period forward.
- A future stretch goal could support CSV import for historical data if desired later.

---

## 4. Data Model (PostgreSQL)

### Design Principles

- **Fully normalized (3NF minimum):** No redundant data. Every non-key column depends on the whole key and nothing but the key.
- **Referential integrity:** All foreign keys enforced with appropriate `ON DELETE` actions. No orphaned records.
- **Scalable:** Indexed for the query patterns the app uses. Ready for multi-user without structural changes. Enums use reference/lookup tables instead of hardcoded CHECK constraints so new types can be added via INSERT rather than schema migration.
- **Auditable:** `created_at` and `updated_at` timestamps on mutable tables. Anchor history tracked.
- **Scenario isolation:** Scenario-specific data is cleanly separated so scenarios never interfere with each other.
- **Logical schemas:** Tables are organized into PostgreSQL schemas for clarity and future security isolation.

### PostgreSQL Schemas

| Schema   | Purpose                                                                  | Tables                                                                                                                                                                    |
| -------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ref`    | Reference/lookup tables (type enums). Rarely written, frequently joined. | account_types, transaction_types, statuses, recurrence_patterns, filing_statuses, deduction_timings, calc_methods, tax_types, raise_types                                 |
| `auth`   | Identity and authentication. Isolated for security.                      | users, refresh_tokens, password_reset_tokens, mfa_configs, user_settings                                                                                                  |
| `budget` | Core budgeting domain. The bulk of the app.                              | accounts, account_anchor_history, pay_periods, categories, recurrence_rules, scenarios, transaction_templates, inflation_settings, transactions, transfers, savings_goals |
| `salary` | Paycheck calculation domain.                                             | salary_profiles, salary_raises, paycheck_deductions, tax_bracket_sets, tax_brackets, state_tax_configs, fica_configs                                                      |
| `system` | Operational tables.                                                      | operation_log, audit_log, notifications, notification_settings                                                                                                            |

This organization means:

- Adding a new account type, recurrence pattern, or deduction timing = `INSERT INTO ref.<table>` (no migration needed).
- Security policies can be applied per-schema if multi-tenant isolation is needed later.
- Developers can reason about the database by domain.

### Entity Relationship Summary

```
ref (lookup/enum tables)
 â”œâ”€â”€ account_types, transaction_types, statuses,
 â”‚   recurrence_patterns, filing_statuses, deduction_timings,
 â”‚   calc_methods, tax_types, raise_types

auth
 â”œâ”€â”€ User
 â”‚    â”œâ”€â”€ UserSettings (1:1)
 â”‚    â”œâ”€â”€ RefreshToken (1:N)
 â”‚    â”œâ”€â”€ PasswordResetToken (1:N)
 â”‚    â””â”€â”€ MfaConfig (1:1 â€” table ready Phase 1, feature Phase 8)

budget
 â”œâ”€â”€ Account (1:N per user) â†’ ref.account_types
 â”‚    â””â”€â”€ AccountAnchorHistory (1:N â€” true-up audit trail)
 â”œâ”€â”€ PayPeriod (1:N â€” auto-generated biweekly dates)
 â”œâ”€â”€ Category (1:N â€” self-referencing adjacency list via parent_id)
 â”œâ”€â”€ RecurrenceRule (1:N) â†’ ref.recurrence_patterns
 â”œâ”€â”€ Scenario (1:N â€” named budget versions)
 â”œâ”€â”€ TransactionTemplate (1:N) â†’ ref.transaction_types
 â”‚    â”œâ”€â”€ InflationSetting (1:1 â€” opt-in, normalized out)
 â”‚    â””â”€â”€ Transaction (1:N per period per scenario) â†’ ref.statuses
 â”œâ”€â”€ Transfer (1:N â€” scenario-scoped) â†’ ref.statuses
 â””â”€â”€ SavingsGoal (1:N â€” per account)

salary
 â”œâ”€â”€ SalaryProfile (N per scenario â€” multiple income sources)
 â”‚    â”‚   â†’ ref.filing_statuses
 â”‚    â”œâ”€â”€ SalaryRaise (1:N) â†’ ref.raise_types
 â”‚    â””â”€â”€ PaycheckDeduction (1:N) â†’ ref.deduction_timings, ref.calc_methods
 â”œâ”€â”€ TaxBracketSet (1:N by year + filing status)
 â”‚    â””â”€â”€ TaxBracket (1:N â€” individual rows)
 â”œâ”€â”€ StateTaxConfig (1:N) â†’ ref.tax_types
 â””â”€â”€ FicaConfig (1:N â€” versioned by year)

system
 â”œâ”€â”€ OperationLog (1:N â€” undo/redo snapshots)
 â”œâ”€â”€ AuditLog (append-only â€” trigger-based, every data change)
 â”œâ”€â”€ Notification (1:N â€” in-app alerts)
 â””â”€â”€ NotificationSetting (1:N â€” per-type preferences)
```

### Tables

```sql
-- ============================================================
-- SCHEMAS
-- ============================================================
CREATE SCHEMA IF NOT EXISTS ref;      -- Reference / lookup tables
CREATE SCHEMA IF NOT EXISTS auth;     -- Identity & authentication
CREATE SCHEMA IF NOT EXISTS budget;   -- Core budgeting domain
CREATE SCHEMA IF NOT EXISTS salary;   -- Paycheck calculation domain
CREATE SCHEMA IF NOT EXISTS system;   -- Operational tables

-- ============================================================
-- REF SCHEMA â€” Reference / Lookup Tables
-- ============================================================
-- These replace hardcoded CHECK constraint enums. Adding a new
-- type is INSERT, not ALTER TABLE. Seeded on app initialization.

CREATE TABLE ref.account_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) UNIQUE NOT NULL    -- 'checking', 'savings', ...
);
INSERT INTO ref.account_types (name) VALUES ('checking'), ('savings');

CREATE TABLE ref.transaction_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL    -- 'income', 'expense'
);
INSERT INTO ref.transaction_types (name) VALUES ('income'), ('expense');

CREATE TABLE ref.statuses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(15) UNIQUE NOT NULL    -- 'projected', 'done', 'received'
);
INSERT INTO ref.statuses (name) VALUES ('projected'), ('done'), ('received');

CREATE TABLE ref.recurrence_patterns (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL    -- 'every_period', 'every_n_periods',
                                        -- 'monthly', 'annual', 'once'
);
INSERT INTO ref.recurrence_patterns (name)
VALUES ('every_period'), ('every_n_periods'), ('monthly'), ('annual'), ('once');

CREATE TABLE ref.filing_statuses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(25) UNIQUE NOT NULL    -- 'single', 'married_jointly', etc.
);
INSERT INTO ref.filing_statuses (name)
VALUES ('single'), ('married_jointly'), ('married_separately'), ('head_of_household');

CREATE TABLE ref.deduction_timings (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL    -- 'pre_tax', 'post_tax'
);
INSERT INTO ref.deduction_timings (name) VALUES ('pre_tax'), ('post_tax');

CREATE TABLE ref.calc_methods (
    id SERIAL PRIMARY KEY,
    name VARCHAR(12) UNIQUE NOT NULL    -- 'flat', 'percentage'
);
INSERT INTO ref.calc_methods (name) VALUES ('flat'), ('percentage');

CREATE TABLE ref.tax_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL    -- 'flat', 'none', 'bracket'
);
INSERT INTO ref.tax_types (name) VALUES ('flat'), ('none'), ('bracket');

CREATE TABLE ref.raise_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL    -- 'merit', 'cola', 'custom'
);
INSERT INTO ref.raise_types (name) VALUES ('merit'), ('cola'), ('custom');

-- ============================================================
-- AUTH SCHEMA â€” Identity & Authentication
-- ============================================================

CREATE TABLE auth.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Supports multiple active sessions and clean revocation.
CREATE TABLE auth.refresh_tokens (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_refresh_tokens_user ON auth.refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_hash ON auth.refresh_tokens(token_hash);

-- Separate table keeps user row clean.
CREATE TABLE auth.password_reset_tokens (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- MFA table created in Phase 1 (schema ready); feature built in Phase 8.
-- Stores TOTP shared secret and single-use backup codes.
CREATE TABLE auth.mfa_configs (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    totp_secret_encrypted BYTEA,            -- AES-256 encrypted TOTP secret
    is_enabled BOOLEAN DEFAULT FALSE,
    backup_codes JSONB,                     -- array of hashed single-use codes
    confirmed_at TIMESTAMPTZ,               -- NULL until user verifies setup
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE auth.user_settings (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    default_inflation_rate NUMERIC(5,4) DEFAULT 0.0300,
    grid_default_range VARCHAR(20) DEFAULT '3_months',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- BUDGET SCHEMA â€” Core Budgeting Domain
-- ============================================================

CREATE TABLE budget.accounts (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    account_type_id INT NOT NULL REFERENCES ref.account_types(id),
    name VARCHAR(100) NOT NULL,
    current_anchor_balance NUMERIC(12,2),
    current_anchor_period_id INT,           -- FK added after pay_periods
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE TABLE budget.account_anchor_history (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL
        REFERENCES budget.accounts(id) ON DELETE CASCADE,
    pay_period_id INT NOT NULL,             -- FK added after pay_periods
    anchor_balance NUMERIC(12,2) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_anchor_history_account
    ON budget.account_anchor_history(account_id, created_at DESC);

CREATE TABLE budget.pay_periods (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    period_index INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, start_date)
);
CREATE INDEX idx_pay_periods_user_index
    ON budget.pay_periods(user_id, period_index);

-- Deferred FKs (circular reference resolution)
ALTER TABLE budget.accounts
    ADD CONSTRAINT fk_accounts_anchor_period
    FOREIGN KEY (current_anchor_period_id)
    REFERENCES budget.pay_periods(id);

ALTER TABLE budget.account_anchor_history
    ADD CONSTRAINT fk_anchor_history_period
    FOREIGN KEY (pay_period_id)
    REFERENCES budget.pay_periods(id);

-- Self-referencing adjacency list. Supports unlimited depth.
CREATE TABLE budget.categories (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    parent_id INT REFERENCES budget.categories(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    depth INT NOT NULL DEFAULT 0 CHECK (depth >= 0),
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, parent_id, name)
);
CREATE INDEX idx_categories_user_parent
    ON budget.categories(user_id, parent_id);

CREATE TABLE budget.recurrence_rules (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    pattern_id INT NOT NULL REFERENCES ref.recurrence_patterns(id),
    interval_n INT DEFAULT 1,
    offset_periods INT DEFAULT 0,
    day_of_month INT
        CHECK (day_of_month IS NULL
            OR (day_of_month >= 1 AND day_of_month <= 31)),
    month_of_year INT
        CHECK (month_of_year IS NULL
            OR (month_of_year >= 1 AND month_of_year <= 12)),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE budget.scenarios (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    is_baseline BOOLEAN DEFAULT FALSE,
    cloned_from_id INT REFERENCES budget.scenarios(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE TABLE budget.transaction_templates (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    account_id INT NOT NULL REFERENCES budget.accounts(id),
    category_id INT NOT NULL REFERENCES budget.categories(id),
    recurrence_rule_id INT REFERENCES budget.recurrence_rules(id),
    transaction_type_id INT NOT NULL REFERENCES ref.transaction_types(id),
    name VARCHAR(200) NOT NULL,
    default_amount NUMERIC(12,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_templates_user_type
    ON budget.transaction_templates(user_id, transaction_type_id);

-- Only exists for templates with inflation tracking enabled.
CREATE TABLE budget.inflation_settings (
    id SERIAL PRIMARY KEY,
    template_id INT NOT NULL UNIQUE
        REFERENCES budget.transaction_templates(id) ON DELETE CASCADE,
    annual_rate NUMERIC(5,4),               -- NULL = use global default
    start_date DATE NOT NULL,
    effective_month INT
        CHECK (effective_month IS NULL
            OR (effective_month >= 1 AND effective_month <= 12)),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE budget.transactions (
    id SERIAL PRIMARY KEY,
    template_id INT
        REFERENCES budget.transaction_templates(id) ON DELETE SET NULL,
    pay_period_id INT NOT NULL
        REFERENCES budget.pay_periods(id) ON DELETE CASCADE,
    scenario_id INT NOT NULL
        REFERENCES budget.scenarios(id) ON DELETE CASCADE,
    status_id INT NOT NULL REFERENCES ref.statuses(id),
    estimated_amount NUMERIC(12,2) NOT NULL,
    actual_amount NUMERIC(12,2),
    is_override BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(template_id, pay_period_id, scenario_id)
);
CREATE INDEX idx_transactions_period_scenario
    ON budget.transactions(pay_period_id, scenario_id);
CREATE INDEX idx_transactions_template
    ON budget.transactions(template_id);

CREATE TABLE budget.transfers (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    from_account_id INT NOT NULL REFERENCES budget.accounts(id),
    to_account_id INT NOT NULL REFERENCES budget.accounts(id),
    pay_period_id INT NOT NULL REFERENCES budget.pay_periods(id),
    scenario_id INT NOT NULL
        REFERENCES budget.scenarios(id) ON DELETE CASCADE,
    status_id INT NOT NULL REFERENCES ref.statuses(id),
    amount NUMERIC(12,2) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (from_account_id != to_account_id)
);
CREATE INDEX idx_transfers_period_scenario
    ON budget.transfers(pay_period_id, scenario_id);

CREATE TABLE budget.savings_goals (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL
        REFERENCES budget.accounts(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    target_amount NUMERIC(12,2) NOT NULL,
    target_date DATE,
    contribution_per_period NUMERIC(12,2),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SALARY SCHEMA â€” Paycheck Calculation Domain
-- ============================================================

-- One salary profile per income source per scenario. Supports
-- multiple income sources (primary job, side job, spouse, etc.)
-- Each profile has its own raises, deductions, and tax treatment.
CREATE TABLE salary.salary_profiles (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scenario_id INT NOT NULL
        REFERENCES budget.scenarios(id) ON DELETE CASCADE,
    template_id INT
        REFERENCES budget.transaction_templates(id),
    filing_status_id INT NOT NULL REFERENCES ref.filing_statuses(id),
    name VARCHAR(100) NOT NULL DEFAULT 'Primary',  -- 'Primary', 'Side Job', 'Spouse', etc.
    annual_salary NUMERIC(12,2) NOT NULL,
    state_code VARCHAR(2),
    pay_periods_per_year INT DEFAULT 26,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, scenario_id, name)      -- multiple profiles per scenario allowed
);

CREATE TABLE salary.salary_raises (
    id SERIAL PRIMARY KEY,
    salary_profile_id INT NOT NULL
        REFERENCES salary.salary_profiles(id) ON DELETE CASCADE,
    raise_type_id INT NOT NULL REFERENCES ref.raise_types(id),
    effective_month INT NOT NULL
        CHECK (effective_month >= 1 AND effective_month <= 12),
    effective_year INT,                     -- NULL = every year (recurring)
    percentage NUMERIC(5,4),
    flat_amount NUMERIC(12,2),
    is_recurring BOOLEAN DEFAULT TRUE,
    notes VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (
        (percentage IS NOT NULL AND flat_amount IS NULL) OR
        (percentage IS NULL AND flat_amount IS NOT NULL)
    )
);
CREATE INDEX idx_salary_raises_profile
    ON salary.salary_raises(salary_profile_id);

CREATE TABLE salary.paycheck_deductions (
    id SERIAL PRIMARY KEY,
    salary_profile_id INT NOT NULL
        REFERENCES salary.salary_profiles(id) ON DELETE CASCADE,
    deduction_timing_id INT NOT NULL
        REFERENCES ref.deduction_timings(id),
    calc_method_id INT NOT NULL
        REFERENCES ref.calc_methods(id),
    name VARCHAR(100) NOT NULL,
    amount NUMERIC(12,4) NOT NULL,
    deductions_per_year INT NOT NULL DEFAULT 26
        CHECK (deductions_per_year IN (12, 24, 26)),
        -- 26 = every paycheck (default: 401k, taxes)
        -- 24 = skip 3rd paycheck of month (benefits)
        -- 12 = once per month (first paycheck only)
    annual_cap NUMERIC(12,2),
    inflation_enabled BOOLEAN DEFAULT FALSE,
    inflation_rate NUMERIC(5,4),
    inflation_effective_month INT
        CHECK (inflation_effective_month IS NULL
            OR (inflation_effective_month >= 1
                AND inflation_effective_month <= 12)),
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_deductions_profile
    ON salary.paycheck_deductions(salary_profile_id);

-- Federal income tax brackets, versioned by year + filing status.
CREATE TABLE salary.tax_bracket_sets (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    filing_status_id INT NOT NULL REFERENCES ref.filing_statuses(id),
    tax_year INT NOT NULL,
    standard_deduction NUMERIC(12,2) NOT NULL,
    description VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, tax_year, filing_status_id)
);

CREATE TABLE salary.tax_brackets (
    id SERIAL PRIMARY KEY,
    bracket_set_id INT NOT NULL
        REFERENCES salary.tax_bracket_sets(id) ON DELETE CASCADE,
    min_income NUMERIC(12,2) NOT NULL CHECK (min_income >= 0),
    max_income NUMERIC(12,2),
    rate NUMERIC(5,4) NOT NULL,
    sort_order INT NOT NULL,
    CHECK (max_income IS NULL OR max_income > min_income)
);
CREATE INDEX idx_tax_brackets_set
    ON salary.tax_brackets(bracket_set_id, sort_order);

CREATE TABLE salary.state_tax_configs (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tax_type_id INT NOT NULL REFERENCES ref.tax_types(id),
    state_code VARCHAR(2) NOT NULL,
    flat_rate NUMERIC(5,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, state_code)
);

CREATE TABLE salary.fica_configs (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tax_year INT NOT NULL,
    ss_rate NUMERIC(5,4) DEFAULT 0.0620,
    ss_wage_base NUMERIC(12,2) DEFAULT 168600.00,
    medicare_rate NUMERIC(5,4) DEFAULT 0.0145,
    medicare_surtax_rate NUMERIC(5,4) DEFAULT 0.0090,
    medicare_surtax_threshold NUMERIC(12,2) DEFAULT 200000.00,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, tax_year)
);

-- ============================================================
-- SYSTEM SCHEMA â€” Operational Tables
-- ============================================================

CREATE TABLE system.operation_log (
    id SERIAL PRIMARY KEY,
    operation_id UUID NOT NULL,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    operation_type VARCHAR(50) NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    row_id INT NOT NULL,
    previous_state JSONB,
    new_state JSONB,
    undone BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_operation_log_op
    ON system.operation_log(operation_id);
CREATE INDEX idx_operation_log_user
    ON system.operation_log(user_id, created_at DESC);

CREATE TABLE system.notifications (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    notification_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    message TEXT,
    related_period_id INT REFERENCES budget.pay_periods(id),
    related_account_id INT REFERENCES budget.accounts(id),
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_notifications_user_unread
    ON system.notifications(user_id, is_read, created_at DESC);

CREATE TABLE system.notification_settings (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    notification_type VARCHAR(50) NOT NULL,
    enabled_in_app BOOLEAN DEFAULT TRUE,
    enabled_email BOOLEAN DEFAULT FALSE,
    threshold_amount NUMERIC(12,2),
    lookahead_periods INT,
    email_frequency_id INT,                 -- could be ref table; keeping simple for now
    UNIQUE(user_id, notification_type)
);

-- ============================================================
-- SYSTEM SCHEMA â€” Audit Log (trigger-based)
-- ============================================================

-- Append-only record of every data change. No UPDATE or DELETE
-- allowed on this table (enforced by a REVOKE or trigger).
CREATE TABLE system.audit_log (
    id BIGSERIAL PRIMARY KEY,               -- BIGSERIAL for high-volume append
    schema_name VARCHAR(50) NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    row_id INT NOT NULL,
    action VARCHAR(6) NOT NULL              -- 'INSERT', 'UPDATE', 'DELETE'
        CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
    old_data JSONB,                         -- NULL for INSERT
    new_data JSONB,                         -- NULL for DELETE
    changed_fields TEXT[],                  -- UPDATE only: columns that changed
    user_id INT,                            -- set via session variable
    ip_address INET,                        -- set via session variable
    request_id UUID,                        -- correlates with application log
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_log_table_row
    ON system.audit_log(table_name, row_id);
CREATE INDEX idx_audit_log_user
    ON system.audit_log(user_id, created_at DESC);
CREATE INDEX idx_audit_log_request
    ON system.audit_log(request_id);
CREATE INDEX idx_audit_log_created
    ON system.audit_log(created_at);

-- Generic audit trigger function. Attach to any table.
-- Reads app.current_user_id, app.current_ip, and
-- app.current_request_id from session variables set by
-- Flask middleware at the start of each request.
CREATE OR REPLACE FUNCTION system.audit_trigger_fn()
RETURNS TRIGGER AS $$
DECLARE
    _user_id INT;
    _ip INET;
    _request_id UUID;
    _changed TEXT[];
    _col TEXT;
BEGIN
    -- Read session variables (set by Flask middleware via SET LOCAL)
    BEGIN
        _user_id := current_setting('app.current_user_id', TRUE)::INT;
    EXCEPTION WHEN OTHERS THEN
        _user_id := NULL;
    END;
    BEGIN
        _ip := current_setting('app.current_ip', TRUE)::INET;
    EXCEPTION WHEN OTHERS THEN
        _ip := NULL;
    END;
    BEGIN
        _request_id := current_setting('app.current_request_id', TRUE)::UUID;
    EXCEPTION WHEN OTHERS THEN
        _request_id := NULL;
    END;

    IF TG_OP = 'INSERT' THEN
        INSERT INTO system.audit_log
            (schema_name, table_name, row_id, action, new_data,
             user_id, ip_address, request_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, NEW.id, 'INSERT',
             to_jsonb(NEW), _user_id, _ip, _request_id);
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        -- Compute which columns changed
        _changed := ARRAY[]::TEXT[];
        FOR _col IN
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = TG_TABLE_SCHEMA
              AND table_name = TG_TABLE_NAME
        LOOP
            IF to_jsonb(OLD) -> _col IS DISTINCT FROM to_jsonb(NEW) -> _col THEN
                _changed := _changed || _col;
            END IF;
        END LOOP;

        INSERT INTO system.audit_log
            (schema_name, table_name, row_id, action, old_data,
             new_data, changed_fields, user_id, ip_address, request_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id, 'UPDATE',
             to_jsonb(OLD), to_jsonb(NEW), _changed,
             _user_id, _ip, _request_id);
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO system.audit_log
            (schema_name, table_name, row_id, action, old_data,
             user_id, ip_address, request_id)
        VALUES
            (TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id, 'DELETE',
             to_jsonb(OLD), _user_id, _ip, _request_id);
        RETURN OLD;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Attach audit triggers to all audited tables.
-- Audited: auth.users, auth.user_settings, and all tables
-- in budget.* and salary.* schemas.
-- Not audited: ref.* (rarely changes), system.* (avoid self-reference).

-- Auth tables
CREATE TRIGGER audit_users
    AFTER INSERT OR UPDATE OR DELETE ON auth.users
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_user_settings
    AFTER INSERT OR UPDATE OR DELETE ON auth.user_settings
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();

-- Budget tables
CREATE TRIGGER audit_accounts
    AFTER INSERT OR UPDATE OR DELETE ON budget.accounts
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_pay_periods
    AFTER INSERT OR UPDATE OR DELETE ON budget.pay_periods
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_categories
    AFTER INSERT OR UPDATE OR DELETE ON budget.categories
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_scenarios
    AFTER INSERT OR UPDATE OR DELETE ON budget.scenarios
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_transaction_templates
    AFTER INSERT OR UPDATE OR DELETE ON budget.transaction_templates
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_transactions
    AFTER INSERT OR UPDATE OR DELETE ON budget.transactions
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_transfers
    AFTER INSERT OR UPDATE OR DELETE ON budget.transfers
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_savings_goals
    AFTER INSERT OR UPDATE OR DELETE ON budget.savings_goals
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();

-- Salary tables
CREATE TRIGGER audit_salary_profiles
    AFTER INSERT OR UPDATE OR DELETE ON salary.salary_profiles
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_salary_raises
    AFTER INSERT OR UPDATE OR DELETE ON salary.salary_raises
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_paycheck_deductions
    AFTER INSERT OR UPDATE OR DELETE ON salary.paycheck_deductions
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
CREATE TRIGGER audit_tax_bracket_sets
    AFTER INSERT OR UPDATE OR DELETE ON salary.tax_bracket_sets
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_fn();
```

### Scalability Considerations

**What the current design handles well:**

- **Multi-user:** Every table links back to `auth.users` via foreign keys. No structural changes needed to host multiple users.
- **Feature extension via data, not migrations:** Adding a new account type, recurrence pattern, raise type, or filing status = `INSERT INTO ref.<table>`. No schema migration required.
- **Category flexibility:** Self-referencing adjacency list supports any depth of categorization without schema changes.
- **Scenario isolation:** All scenario-specific data (transactions, salary profiles, raises, deductions, transfers) is cleanly FK'd to a scenario. Scenarios cannot accidentally cross-contaminate.
- **Tax system versioning:** Tax brackets and FICA configs are versioned by year. Updating for a new tax year is just inserting new rows.
- **Multiple income sources:** `salary.salary_profiles` is keyed on `(user_id, scenario_id, name)`, allowing multiple profiles per scenario (e.g., "Primary", "Side Job", "Spouse"). Each profile has its own raises, deductions, and tax treatment. The paycheck calculator processes each profile independently.
- **Audit trail:** Every INSERT, UPDATE, and DELETE on audited tables is captured by PostgreSQL triggers into `system.audit_log` with full before/after state, changed fields, user, IP, and request correlation ID.
- **MFA readiness:** `auth.mfa_configs` table is in place. Feature implementation only requires adding route handlers and frontend UI.

**Documented future upgrade paths (not built now, but the design supports them):**

- **Multi-tenant security isolation:** The schema-per-domain structure means PostgreSQL Row Level Security (RLS) policies can be layered on without restructuring. Alternatively, schema-per-tenant can be added by duplicating the `budget` and `salary` schemas per user.
- **State bracket taxes:** The `ref.tax_types` table already includes `'bracket'`. When needed, add a `salary.state_tax_brackets` table mirroring `salary.tax_brackets`, and update the tax calculator.
- **International tax:** The tax system is US-specific by design. Supporting other countries would require a tax plugin architecture (strategy pattern) where each country implements a `calculate_tax()` interface. The schema structure (reference tables, versioned configs) would remain the same.
- **Audit log partitioning:** If `system.audit_log` grows very large, PostgreSQL table partitioning by `created_at` (e.g., monthly partitions) can be added transparently without changing application code.

---

## 5. Architecture

### Design Patterns

**Application Factory Pattern.** The Flask app is created by a `create_app()` factory function in `app/__init__.py`. This function accepts a configuration object, initializes extensions (SQLAlchemy, Alembic, JWT), registers all blueprints, and returns the configured app instance. Benefits: testability (create fresh app instances per test), multiple configurations (dev, test, prod), and clean separation of concerns.

```python
# app/__init__.py (simplified)
def create_app(config_name="development"):
    """Application factory â€” creates and configures the Flask app."""
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    # Register blueprints
    from app.routes import auth_bp, pay_periods_bp, transactions_bp, ...
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(pay_periods_bp, url_prefix="/api/pay-periods")
    app.register_blueprint(transactions_bp, url_prefix="/api/transactions")
    # ... remaining blueprints

    return app
```

**Blueprints.** Each API domain is a Flask Blueprint with its own file in `routes/`. Blueprints own only HTTP concerns: request parsing, response serialization, status codes, and auth decorators. They delegate all business logic to the service layer. No SQLAlchemy queries or business rules in route handlers.

**Service Layer.** All business logic lives in `services/`. Services are plain Python classes or modules â€” they are not Flask-aware (no `request`, no `jsonify`). They accept and return Python objects and raise domain-specific exceptions. This makes them independently testable and reusable across routes.

```
Request flow:
  Route handler (Blueprint)
    â†’ validates input, extracts params
    â†’ calls Service method
      â†’ Service contains business logic
      â†’ Service calls SQLAlchemy models / other services
      â†’ Service returns result or raises exception
    â†’ Route serializes response as JSON
```

**Repository pattern (optional, future).** If the service layer grows complex, data access can be extracted into repository classes. For the MVP, services can call SQLAlchemy directly since the ORM already provides a reasonable abstraction.

### High-Level Diagram

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                    React Frontend                     â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â” â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚  Budget  â”‚ â”‚ Scenario â”‚ â”‚ Paycheckâ”‚ â”‚ Charts &â”‚  â”‚
â”‚  â”‚   Grid   â”‚ â”‚ Compare  â”‚ â”‚Breakdownâ”‚ â”‚  Viz    â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”˜ â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”˜  â”‚
â”‚       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜       â”‚
â”‚                         â”‚ HTTP/JSON                   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                          â”‚
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Flask Application (factory pattern + blueprints)     â”‚
â”‚                         â”‚                             â”‚
â”‚  â”Œâ”€â”€â”€ Routes (Blueprints) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ auth â”‚ pay_periods â”‚ transactions â”‚ scenarios â”‚  â”‚  â”‚
â”‚  â”‚ accounts â”‚ salary â”‚ notifications â”‚ reports   â”‚  â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â”‚                         â”‚                             â”‚
â”‚  â”Œâ”€â”€â”€ Service Layer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ AuthService      â”‚ PayPeriodService             â”‚  â”‚
â”‚  â”‚ RecurrenceEngine â”‚ BalanceCalculator            â”‚  â”‚
â”‚  â”‚ PaycheckCalc     â”‚ ScenarioService              â”‚  â”‚
â”‚  â”‚ OperationLog     â”‚ NotificationService          â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â”‚                         â”‚                             â”‚
â”‚  â”Œâ”€â”€â”€ Data Layer (SQLAlchemy ORM) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ Models map 1:1 to normalized PostgreSQL tables   â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                          â”‚
                 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”
                 â”‚   PostgreSQL    â”‚
                 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Backend Structure (Flask)

```
budget-app/
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ app/
â”‚   â”‚   â”œâ”€â”€ __init__.py                   # Application factory (create_app)
â”‚   â”‚   â”œâ”€â”€ extensions.py                 # SQLAlchemy, Migrate, JWT instances
â”‚   â”‚   â”œâ”€â”€ config.py                     # Dev / Test / Prod config classes
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ models/                       # SQLAlchemy models (mirror DB schemas)
â”‚   â”‚   â”‚   â”œâ”€â”€ __init__.py               # Imports all models for Alembic
â”‚   â”‚   â”‚   â”œâ”€â”€ ref.py                    # All ref.* lookup tables
â”‚   â”‚   â”‚   â”œâ”€â”€ user.py                   # auth.users, refresh_tokens, reset_tokens
â”‚   â”‚   â”‚   â”œâ”€â”€ mfa_config.py             # auth.mfa_configs
â”‚   â”‚   â”‚   â”œâ”€â”€ user_settings.py          # auth.user_settings
â”‚   â”‚   â”‚   â”œâ”€â”€ account.py                # budget.accounts, anchor_history
â”‚   â”‚   â”‚   â”œâ”€â”€ pay_period.py             # budget.pay_periods
â”‚   â”‚   â”‚   â”œâ”€â”€ category.py               # budget.categories (adjacency list)
â”‚   â”‚   â”‚   â”œâ”€â”€ recurrence_rule.py        # budget.recurrence_rules
â”‚   â”‚   â”‚   â”œâ”€â”€ transaction_template.py   # budget.transaction_templates, inflation_settings
â”‚   â”‚   â”‚   â”œâ”€â”€ transaction.py            # budget.transactions
â”‚   â”‚   â”‚   â”œâ”€â”€ scenario.py               # budget.scenarios
â”‚   â”‚   â”‚   â”œâ”€â”€ salary_profile.py         # salary.salary_profiles (multi-income),
â”‚   â”‚   â”‚   â”‚                             #   salary_raises, paycheck_deductions
â”‚   â”‚   â”‚   â”œâ”€â”€ tax_config.py             # salary.tax_bracket_sets, brackets,
â”‚   â”‚   â”‚   â”‚                             #   state_tax_configs, fica_configs
â”‚   â”‚   â”‚   â”œâ”€â”€ transfer.py               # budget.transfers
â”‚   â”‚   â”‚   â”œâ”€â”€ savings_goal.py           # budget.savings_goals
â”‚   â”‚   â”‚   â”œâ”€â”€ operation_log.py          # system.operation_log
â”‚   â”‚   â”‚   â”œâ”€â”€ audit_log.py              # system.audit_log (read-only model)
â”‚   â”‚   â”‚   â””â”€â”€ notification.py           # system.notifications, settings
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ routes/                       # Blueprints (HTTP layer only)
â”‚   â”‚   â”‚   â”œâ”€â”€ __init__.py               # Registers all blueprints
â”‚   â”‚   â”‚   â”œâ”€â”€ auth.py                   # /api/auth/* (incl. MFA endpoints)
â”‚   â”‚   â”‚   â”œâ”€â”€ pay_periods.py            # /api/pay-periods/*
â”‚   â”‚   â”‚   â”œâ”€â”€ transactions.py           # /api/transactions/*
â”‚   â”‚   â”‚   â”œâ”€â”€ templates.py              # /api/templates/*
â”‚   â”‚   â”‚   â”œâ”€â”€ scenarios.py              # /api/scenarios/*
â”‚   â”‚   â”‚   â”œâ”€â”€ accounts.py               # /api/accounts/*
â”‚   â”‚   â”‚   â”œâ”€â”€ salary.py                 # /api/salary/* (multi-profile aware)
â”‚   â”‚   â”‚   â”œâ”€â”€ notifications.py          # /api/notifications/*
â”‚   â”‚   â”‚   â”œâ”€â”€ operations.py             # /api/operations/*
â”‚   â”‚   â”‚   â”œâ”€â”€ settings.py               # /api/settings
â”‚   â”‚   â”‚   â””â”€â”€ reports.py                # /api/reports/*
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ services/                     # Business logic (no Flask imports)
â”‚   â”‚   â”‚   â”œâ”€â”€ auth_service.py           # JWT, bcrypt, token management
â”‚   â”‚   â”‚   â”œâ”€â”€ mfa_service.py            # TOTP setup, verify, backup codes
â”‚   â”‚   â”‚   â”œâ”€â”€ pay_period_service.py     # Generate, extend, list periods
â”‚   â”‚   â”‚   â”œâ”€â”€ recurrence_engine.py      # Auto-generate from rules
â”‚   â”‚   â”‚   â”œâ”€â”€ balance_calculator.py     # Pure function: anchor â†’ balances
â”‚   â”‚   â”‚   â”œâ”€â”€ paycheck_calculator.py    # Annual salary â†’ net biweekly pay
â”‚   â”‚   â”‚   â”œâ”€â”€ tax_calculator.py         # Federal, state, FICA computation
â”‚   â”‚   â”‚   â”œâ”€â”€ scenario_service.py       # Clone, diff, compare
â”‚   â”‚   â”‚   â”œâ”€â”€ operation_log_service.py  # Snapshot, undo, redo
â”‚   â”‚   â”‚   â”œâ”€â”€ notification_service.py   # Generate & route alerts
â”‚   â”‚   â”‚   â”œâ”€â”€ email_service.py          # Transactional email
â”‚   â”‚   â”‚   â””â”€â”€ smart_estimate_service.py # Rolling averages for variables
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ middleware/
â”‚   â”‚   â”‚   â”œâ”€â”€ auth_middleware.py         # @jwt_required decorator
â”‚   â”‚   â”‚   â”œâ”€â”€ request_context.py        # Generates request_id UUID, sets
â”‚   â”‚   â”‚   â”‚                             #   session vars for audit trigger
â”‚   â”‚   â”‚   â”‚                             #   (app.current_user_id, app.current_ip,
â”‚   â”‚   â”‚   â”‚                             #   app.current_request_id)
â”‚   â”‚   â”‚   â””â”€â”€ request_logger.py         # Logs method, path, user_id, status,
â”‚   â”‚   â”‚                                 #   duration_ms on every response
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ logging_config.py             # Structured JSON logging setup
â”‚   â”‚   â”‚                                 #   (dev: pretty-print, prod: JSON)
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ schemas/                      # Marshmallow or Pydantic for
â”‚   â”‚   â”‚   â”œâ”€â”€ auth.py                   #   request validation and
â”‚   â”‚   â”‚   â”œâ”€â”€ transaction.py            #   response serialization
â”‚   â”‚   â”‚   â”œâ”€â”€ salary.py
â”‚   â”‚   â”‚   â””â”€â”€ ...
â”‚   â”‚   â”‚
â”‚   â”‚   â”œâ”€â”€ exceptions.py                 # Domain-specific exceptions
â”‚   â”‚   â”‚                                 #   (NotFound, ValidationError, etc.)
â”‚   â”‚   â””â”€â”€ utils/
â”‚   â”‚       â””â”€â”€ validators.py
â”‚   â”‚
â”‚   â”œâ”€â”€ migrations/                       # Alembic DB migrations
â”‚   â”œâ”€â”€ scripts/
â”‚   â”‚   â”œâ”€â”€ backup_db.sh
â”‚   â”‚   â”œâ”€â”€ restore_db.sh
â”‚   â”‚   â”œâ”€â”€ seed_tax_brackets.py          # Seed current-year tax data
â”‚   â”‚   â””â”€â”€ seed_ref_tables.py            # Seed all ref.* lookup data
â”‚   â”œâ”€â”€ tests/
â”‚   â”‚   â”œâ”€â”€ conftest.py                   # Fixtures: test app, test db, test client
â”‚   â”‚   â”œâ”€â”€ test_services/
â”‚   â”‚   â”‚   â”œâ”€â”€ test_paycheck_calculator.py
â”‚   â”‚   â”‚   â”œâ”€â”€ test_balance_calculator.py
â”‚   â”‚   â”‚   â”œâ”€â”€ test_recurrence_engine.py
â”‚   â”‚   â”‚   â””â”€â”€ ...
â”‚   â”‚   â””â”€â”€ test_routes/
â”‚   â”‚       â””â”€â”€ ...
â”‚   â”œâ”€â”€ requirements.txt
â”‚   â””â”€â”€ run.py
â”‚
â”œâ”€â”€ frontend/
â”‚   â”œâ”€â”€ src/
â”‚   â”‚   â”œâ”€â”€ components/
â”‚   â”‚   â”‚   â”œâ”€â”€ Auth/                     # Login, Register, Password Reset
â”‚   â”‚   â”‚   â”œâ”€â”€ BudgetGrid/              # Main paycheck â†” expense grid
â”‚   â”‚   â”‚   â”œâ”€â”€ PaycheckBreakdown/       # Gross â†’ deductions â†’ taxes â†’ net
â”‚   â”‚   â”‚   â”œâ”€â”€ SalaryProjection/        # Salary + raises over time
â”‚   â”‚   â”‚   â”œâ”€â”€ ScenarioCompare/         # Side-by-side scenario view
â”‚   â”‚   â”‚   â”œâ”€â”€ AccountSummary/          # Checking/savings balances
â”‚   â”‚   â”‚   â”œâ”€â”€ Notifications/           # Bell icon, notification list
â”‚   â”‚   â”‚   â”œâ”€â”€ Charts/                  # Visualization components
â”‚   â”‚   â”‚   â”œâ”€â”€ UndoToast/              # Undo toast notification
â”‚   â”‚   â”‚   â””â”€â”€ common/                  # Shared UI components
â”‚   â”‚   â”œâ”€â”€ hooks/
â”‚   â”‚   â”œâ”€â”€ services/                    # API client + auth token management
â”‚   â”‚   â”œâ”€â”€ store/                       # State management (Zustand)
â”‚   â”‚   â””â”€â”€ App.jsx
â”‚   â”œâ”€â”€ package.json
â”‚   â””â”€â”€ vite.config.js
â”‚
â”œâ”€â”€ docker-compose.yml                    # PostgreSQL + Flask + React + backup
â”œâ”€â”€ backup/
â”‚   â”œâ”€â”€ Dockerfile
â”‚   â”œâ”€â”€ crontab
â”‚   â””â”€â”€ backup.sh
â””â”€â”€ README.md
```

### Key Backend Services

**Auth Service** (`auth_service.py`)

- Register: validate email, hash password with bcrypt, create user + default accounts + default baseline scenario + default user_settings + seed tax brackets + create empty mfa_config row
- Login: verify credentials â†’ check if MFA enabled â†’ if yes, return `mfa_required` + short-lived session token; if no, issue JWT + refresh token
- MFA verification: validate TOTP code or backup code â†’ issue JWT + refresh token
- Refresh: validate refresh token hash, issue new access token
- Password reset: generate token, send email, verify token, update password
- All auth events logged at INFO (success) or WARNING (failure)

**MFA Service** (`mfa_service.py`) â€” Phase 8

- `setup(user_id)` â†’ generate TOTP secret, return provisioning URI (for QR code) + backup codes
- `confirm(user_id, totp_code)` â†’ verify code against secret, enable MFA, hash and store backup codes
- `verify(user_id, code)` â†’ check TOTP or backup code, return boolean
- `disable(user_id)` â†’ require password confirmation, disable MFA
- TOTP secret encrypted at rest (AES-256); backup codes hashed with bcrypt

**Request Context Middleware** (`request_context.py`)

- Runs `before_request` on every API call
- Generates a UUID `request_id` and stores it on Flask's `g` object
- Sets PostgreSQL session variables via `SET LOCAL` for the audit trigger:
  - `app.current_user_id` â€” from the JWT (NULL for unauthenticated requests)
  - `app.current_ip` â€” from `request.remote_addr`
  - `app.current_request_id` â€” the generated UUID
- These session variables are automatically picked up by `system.audit_trigger_fn()` on every database write within that request

**Request Logger Middleware** (`request_logger.py`)

- Runs `after_request` on every API call
- Logs: method, path, user_id, status_code, duration_ms, request_id
- Format: structured JSON in production, pretty-printed in development
- Security events (failed logins, password changes, MFA events) logged at appropriate levels

**Recurrence Engine** (`recurrence_engine.py`)

- Input: a transaction template + its recurrence rule + a list of pay periods
- Output: generated transaction entries for each applicable period
- Respects existing overrides (won't clobber `is_override = True`)
- For **salary templates**: delegates to PaycheckCalculator to compute net amount per period
- For **inflation-enabled templates**: applies compound inflation formula
- Before regenerating, snapshots affected rows to operation_log (enables undo)

**Paycheck Calculator** (`paycheck_calculator.py`)

- Pure function: given a salary profile, a target date, and a list of raises/deductions, returns the net biweekly paycheck amount for that date.
- Pipeline: annual salary â†’ apply raises up to target date â†’ gross biweekly (Ã· pay_periods_per_year) â†’ subtract pre-tax deductions (with inflation applied) â†’ compute taxes via TaxCalculator â†’ subtract post-tax deductions â†’ net
- **3rd paycheck detection:** Counts how many pay periods start in the target period's calendar month. If this is the 3rd, deductions with `deductions_per_year = 24` are skipped, resulting in higher net pay for those periods.
- Handles deduction annual caps (e.g., 401k stops contributing mid-year once cap is hit)
- Called by the recurrence engine once per pay period when generating salary transactions

**Tax Calculator** (`tax_calculator.py`)

- Pure function: given taxable income, filing status, state, and year â†’ returns federal tax, state tax, SS, and Medicare amounts
- Federal: applies bracket set for the matching year + filing status, accounts for standard deduction
- State: flat rate or bracket-based lookup
- FICA: tracks cumulative wages across periods within a calendar year for Social Security wage base cap
- Returns itemized breakdown (useful for the paycheck breakdown view)

**Balance Calculator** (`balance_calculator.py`)

- A **pure function** â€” no database writes, no side effects
- Input: account, anchor balance, anchor period, all transactions from anchor forward
- Output: list of `(period_id, projected_end_balance)` tuples
- Uses actual amounts where status is `done`/`received`, estimated amounts where `projected`
- Called on every grid load and balance-related API request (no stored balances)

**Scenario Service** (`scenario_service.py`)

- Clone baseline â†’ new scenario (deep copies: transactions, salary profile + raises + deductions, transfers)
- Diff two scenarios â†’ list of changed periods with balance deltas
- Clone operation logged to operation_log (enables undo)

**Operation Log Service** (`operation_log_service.py`)

- `snapshot(operation_type, rows)` â€” saves previous state before bulk changes
- `undo(operation_id)` â€” restores all rows to previous state
- `redo(operation_id)` â€” restores all rows to new state
- `list_recent(user_id, limit=20)` â€” returns undoable operations
- Cleanup job: prune entries older than configurable threshold

**Notification Service** (`notification_service.py`)

- Runs checks against user's notification settings after balance recalculation
- Generates in-app notification records
- Queues email notifications for the email service (immediate or daily digest)
- Deduplicates: won't create a duplicate notification for the same event

### API Endpoints

| Method   | Endpoint                                                    | Description                                  | Phase |
| -------- | ----------------------------------------------------------- | -------------------------------------------- | ----- |
| `POST`   | `/api/auth/register`                                        | Create account                               | 1     |
| `POST`   | `/api/auth/login`                                           | Login (may return mfa_required)              | 1     |
| `POST`   | `/api/auth/login/mfa`                                       | Verify TOTP code, issue tokens               | 8     |
| `POST`   | `/api/auth/refresh`                                         | Refresh access token                         | 1     |
| `POST`   | `/api/auth/reset-password`                                  | Request password reset email                 | 1     |
| `PUT`    | `/api/auth/reset-password/:token`                           | Set new password                             | 1     |
| `POST`   | `/api/auth/mfa/setup`                                       | Generate TOTP secret + QR URI + backup codes | 8     |
| `POST`   | `/api/auth/mfa/confirm`                                     | Verify setup code, enable MFA                | 8     |
| `DELETE` | `/api/auth/mfa`                                             | Disable MFA (requires password)              | 8     |
| `GET`    | `/api/settings`                                             | Get user settings                            | 1     |
| `PUT`    | `/api/settings`                                             | Update user settings                         | 1     |
| `GET`    | `/api/pay-periods`                                          | List all pay periods                         | 1     |
| `POST`   | `/api/pay-periods/generate`                                 | Generate periods from start date             | 1     |
| `GET`    | `/api/transactions?period_id=&scenario_id=`                 | Get transactions for a period                | 1     |
| `POST`   | `/api/transactions`                                         | Create a transaction                         | 1     |
| `PUT`    | `/api/transactions/:id`                                     | Update (amount, status, override)            | 1     |
| `POST`   | `/api/transactions/:id/mark-done`                           | Set done, record actual                      | 2     |
| `GET`    | `/api/templates`                                            | List transaction templates                   | 2     |
| `POST`   | `/api/templates`                                            | Create template + recurrence rule            | 2     |
| `PUT`    | `/api/templates/:id`                                        | Update template/rule, regenerate             | 2     |
| `GET`    | `/api/accounts`                                             | List accounts with balances                  | 2     |
| `PUT`    | `/api/accounts/:id/true-up`                                 | Set new anchor balance                       | 2     |
| `GET`    | `/api/salary/profiles?scenario_id=`                         | List all salary profiles for a scenario      | 2     |
| `POST`   | `/api/salary/profiles`                                      | Create a salary profile (new income source)  | 2     |
| `PUT`    | `/api/salary/profiles/:id`                                  | Update profile (salary, filing, state)       | 2     |
| `DELETE` | `/api/salary/profiles/:id`                                  | Remove an income source                      | 2     |
| `GET`    | `/api/salary/profiles/:id/raises`                           | List raises for a profile                    | 2     |
| `POST`   | `/api/salary/profiles/:id/raises`                           | Add a raise                                  | 2     |
| `PUT`    | `/api/salary/raises/:id`                                    | Update a raise                               | 2     |
| `DELETE` | `/api/salary/raises/:id`                                    | Delete a raise                               | 2     |
| `GET`    | `/api/salary/profiles/:id/deductions`                       | List deductions for a profile                | 2     |
| `POST`   | `/api/salary/profiles/:id/deductions`                       | Add a deduction                              | 2     |
| `PUT`    | `/api/salary/deductions/:id`                                | Update a deduction                           | 2     |
| `DELETE` | `/api/salary/deductions/:id`                                | Delete a deduction                           | 2     |
| `GET`    | `/api/salary/projection?scenario_id=&profile_id=`           | Projected net pay over time                  | 2     |
| `GET`    | `/api/salary/breakdown?period_id=&scenario_id=&profile_id=` | Full paycheck breakdown                      | 2     |
| `GET`    | `/api/salary/tax-config?year=`                              | Get tax brackets + FICA config               | 2     |
| `PUT`    | `/api/salary/tax-config`                                    | Update tax brackets or FICA for a year       | 2     |
| `GET`    | `/api/scenarios`                                            | List scenarios                               | 3     |
| `POST`   | `/api/scenarios`                                            | Create (clone from baseline)                 | 3     |
| `GET`    | `/api/scenarios/compare?a=&b=`                              | Side-by-side diff                            | 3     |
| `GET`    | `/api/operations/recent`                                    | List undoable operations                     | 3     |
| `POST`   | `/api/operations/:id/undo`                                  | Undo a bulk operation                        | 3     |
| `POST`   | `/api/operations/:id/redo`                                  | Redo a bulk operation                        | 3     |
| `POST`   | `/api/transfers`                                            | Create account transfer                      | 4     |
| `GET`    | `/api/reports/balance-projection`                           | Balance over time data                       | 5     |
| `GET`    | `/api/reports/category-totals`                              | Spending by category                         | 5     |
| `GET`    | `/api/notifications`                                        | List notifications (unread first)            | 6     |
| `PUT`    | `/api/notifications/:id/read`                               | Mark notification as read                    | 6     |
| `PUT`    | `/api/notifications/read-all`                               | Mark all as read                             | 6     |
| `GET`    | `/api/notifications/settings`                               | Get notification preferences                 | 6     |
| `PUT`    | `/api/notifications/settings`                               | Update notification preferences              | 6     |
| `GET`    | `/api/audit-log?table=&row_id=&from=&to=`                   | Query audit log (admin/debug)                | 1     |

---

## 6. Frontend â€” Key Views

### 6.1 Budget Grid (Primary View)

The main screen mirrors the spreadsheet layout:

- **Columns** = pay periods, with the current/anchor period highlighted.
- **Rows** = income and expense line items, grouped by category.
- **Cells** show the amount (estimated or actual) with visual indicators:
  - Gray = projected
  - Green = done/received
  - When actual differs from estimate, show both (e.g., "~~$500~~ $487 âœ“") so the user can see the savings at a glance
- **Bottom summary rows:**
  - Total Income
  - Total Expenses
  - Net (Income âˆ’ Expenses)
  - Projected End Balance (checking)
  - Savings Balance

**Dynamic date range:**

The grid defaults to showing the **current period + 2 months ahead** (~4â€“5 columns), which covers the user's typical day-to-day view. The user can adjust the visible range with:

- **Quick-select buttons:** "1 Month" Â· "3 Months" Â· "6 Months" Â· "1 Year" Â· "2 Years"
- **Custom date range picker:** choose a start and end date to show only those periods
- **Scroll navigation:** left/right arrows or horizontal scroll to move through periods without changing the range size

The grid **dynamically sizes columns** based on how many periods are visible:

- 1â€“6 periods: wide columns with full detail (estimated, actual, status)
- 7â€“13 periods: medium columns (amounts + status icon)
- 14+ periods: compact columns (amount only, hover for detail)

The full 2-year horizon is always calculated in the background â€” the date range only controls what's _displayed_. The balance projection at the bottom always shows the full forward trajectory regardless of the visible range, so the user can always see the long-term impact of current decisions.

### 6.2 Transaction Detail Modal

Click any cell to open a detail view:

- Edit estimated and actual amounts
- Change status (projected â†’ done)
- Reassign to a different pay period
- Add notes
- View the recurrence rule (link to edit the template)

### 6.3 Paycheck Breakdown View

Accessible from the salary income row in the grid (click the paycheck amount for any period):

- **Header:** Pay period date, scenario name
- **Gross section:** Annual salary (with raise history), gross biweekly amount
- **Pre-tax deductions:** Each line item (401k, health insurance, HSA, etc.) with amount
- **Taxable income:** Gross âˆ’ pre-tax deductions
- **Tax section:** Federal income tax, state income tax, Social Security, Medicare â€” each on its own line
- **Post-tax deductions:** Each line item (Roth 401k, life insurance, etc.)
- **Net pay:** Final amount deposited to checking (bold, highlighted)
- **Inflation indicators:** Small "â†‘" icons next to deductions that have been inflation-adjusted from their base amount
- **Edit links:** Each section links to the relevant settings (salary profile, deductions, tax config)

### 6.4 Salary Projection View

Shows projected compensation trajectory over the full 2-year horizon:

- **Table:** Each row is a raise event (merit Jan, COLA Jul, custom). Columns: effective date, raise type, percentage, annual salary before, annual salary after, gross biweekly, net biweekly
- **Chart:** Line graph of net biweekly paycheck over time, with step-ups at raise dates
- **Deduction impact:** Toggle to show how deduction inflation erodes net pay growth over time

### 6.5 Scenario Comparison View

- Two budget grids side by side (or overlaid)
- Differences highlighted (red = worse, green = better)
- Summary: total difference in end balance at 6 months, 1 year, 2 years

### 6.6 Accounts Dashboard

- Checking balance (current actual + projected)
- Savings balance (current actual + projected)
- Recent transfers
- Savings goal progress bars

---

## 7. Development Roadmap

### Phase 1 â€” Foundation (Weeks 1â€“5)

- [ ] Project setup: Flask app factory, React app (Vite), PostgreSQL via Docker Compose
- [ ] `extensions.py`, `config.py` (dev/test/prod), `exceptions.py`
- [ ] `logging_config.py`: structured JSON logging (pretty-print dev, JSON prod)
- [ ] Request context middleware: generate `request_id`, set PG session variables for audit trigger
- [ ] Request logger middleware: log method, path, user_id, status, duration_ms on every response
- [ ] Global exception handler: log unhandled errors at ERROR, return generic 500
- [ ] Database models and Alembic migrations for all Phase 1 tables
- [ ] Create PostgreSQL schemas (ref, auth, budget, salary, system)
- [ ] Seed ref.\* lookup tables with initial values
- [ ] `system.audit_log` table + `audit_trigger_fn()` PL/pgSQL function
- [ ] Attach audit triggers to all audited tables (auth._, budget._, salary.\*)
- [ ] Audit log query endpoint (`GET /api/audit-log`)
- [ ] `auth.mfa_configs` table created (empty â€” feature built in Phase 8)
- [ ] **Authentication:** Registration, login, JWT access/refresh tokens, protected routes
- [ ] Security logging: auth events at INFO, failed attempts at WARNING
- [ ] React auth flow: login/register pages, token storage in memory, silent refresh
- [ ] Pay period generation service + blueprint
- [ ] Basic transaction CRUD (create, read, update)
- [ ] Categories CRUD with self-referencing hierarchy
- [ ] User settings CRUD
- [ ] Simple budget grid (read-only) fetching from API
- [ ] Dynamic grid date range: quick-select buttons, custom range picker, responsive column sizing
- [ ] Connect grid to API, make cells editable

### Phase 2 â€” Core Budget Workflow (Weeks 6â€“11)

- [ ] Transaction templates and recurrence rules (data model + CRUD)
- [ ] Recurrence engine: auto-generate transactions from rules into future periods
- [ ] Manual override support: edit auto-generated entries, flag as `is_override`
- [ ] Balance roll-forward calculator (pure function, calculated on read, no stored balances)
- [ ] Anchor balance / true-up functionality with anchor history tracking
- [ ] Mark-as-done workflow (status toggle, actual amount entry)
- [ ] Editable budget grid: click cells to update amounts, change status
- [ ] Status indicators in grid (gray = projected, green = done/received)
- [ ] Salary profiles CRUD â€” multi-income ready (name, annual salary, filing, state)
- [ ] Salary raises CRUD (merit, COLA, custom â€” per profile)
- [ ] Paycheck deductions CRUD (pre-tax, post-tax, with inflation settings and deductions_per_year)
- [ ] Tax bracket + FICA config seeding and CRUD
- [ ] Paycheck calculator service: annual salary â†’ net biweekly
- [ ] 3rd paycheck detection: skip 24-per-year deductions when period is 3rd in month
- [ ] Tax calculator service: federal, state, FICA
- [ ] Wire paycheck calculator into recurrence engine for salary transactions
- [ ] Paycheck breakdown view (gross â†’ deductions â†’ taxes â†’ net)
- [ ] Salary projection view (raises + net pay over time, per profile)

### Phase 3 â€” Scenarios & Undo (Weeks 12â€“15)

- [ ] Operation log service: snapshot, undo, redo
- [ ] Wire undo into recurrence engine and bulk operations
- [ ] Undo toast notification in React (auto-dismiss with clickable undo button)
- [ ] Scenario CRUD: create, clone from baseline (deep copy: transactions, all salary profiles + raises + deductions), rename, delete
- [ ] Scenario-scoped transactions and salary profiles
- [ ] Side-by-side comparison view
- [ ] Balance diff calculation and visual highlighting

### Phase 4 â€” Accounts & Savings (Weeks 16â€“19)

- [ ] Multiple account support (checking, savings, custom named accounts)
- [ ] Transfer creation and tracking (scenario-scoped)
- [ ] Savings balance roll-forward
- [ ] Savings goals: target amount, target date, auto-calculated contributions
- [ ] Paychecks-in-savings, months-in-savings, years-in-savings metrics
- [ ] Accounts dashboard view

### Phase 5 â€” Visualization (Weeks 20â€“22)

- [ ] Balance-over-time line chart (Recharts)
- [ ] Category spending breakdown (bar chart)
- [ ] Budget vs. actuals comparison chart
- [ ] Scenario comparison overlay chart
- [ ] Net pay trajectory chart (from salary projection view)

### Phase 6 â€” Notifications (Weeks 23â€“25)

- [ ] Notification settings UI (per-type toggle, thresholds, lookahead)
- [ ] In-app notification generation after balance recalculations
- [ ] Notification bell icon + dropdown list in app header
- [ ] Email service integration (SendGrid or SMTP)
- [ ] Daily digest and/or immediate email alerts

### Phase 7 â€” Smart Features (Weeks 26â€“28)

- [ ] Smart estimates: rolling average of actuals for variable expenses
- [ ] Suggestion UI: displayed inline with accept/adjust controls
- [ ] Expense inflation: global default rate in user settings
- [ ] Expense inflation: per-template enable/disable and custom rate override
- [ ] Deduction inflation: apply to paycheck deductions at open enrollment month
- [ ] Recurrence engine: apply inflation formulas when generating future amounts
- [ ] Grid indicators for inflation-adjusted amounts

### Phase 8 â€” Ops, Security & Hardening (Weeks 29â€“33)

- [ ] **MFA / TOTP:** MFA service (setup, confirm, verify, disable)
- [ ] MFA login flow: return `mfa_required` flag, verify TOTP code endpoint
- [ ] MFA setup UI: QR code display, confirmation code entry, backup codes display
- [ ] MFA settings UI: enable/disable toggle (with password confirmation)
- [ ] Backup container: cron + `pg_dump` with retention policy
- [ ] Docker volume snapshot scripting
- [ ] Restore scripts and documentation
- [ ] Audit log retention: scheduled job to archive/delete rows older than configurable period
- [ ] Export to CSV/Excel
- [ ] Mobile-responsive layout
- [ ] Password reset email flow (if stubbed earlier)
- [ ] Production deployment guide (Docker, reverse proxy, HTTPS)

---

## 8. Key Technical Decisions

| Decision               | Choice                                                                                              | Rationale                                                                                                                                         |
| ---------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| App structure          | Application factory pattern                                                                         | Testable, multi-config, clean extension init                                                                                                      |
| Route organization     | Flask Blueprints                                                                                    | Each domain is a self-contained module with its own URL prefix                                                                                    |
| Business logic         | Service layer (plain Python)                                                                        | No Flask coupling; independently testable; reusable across routes                                                                                 |
| Request/response       | Marshmallow or Pydantic schemas                                                                     | Validates input, serializes output, keeps routes thin                                                                                             |
| Backend framework      | Flask                                                                                               | Lightweight, learn-as-you-go, good for APIs                                                                                                       |
| Balance calculation    | Calculate on read (Option A)                                                                        | Pure function; no stored balances; fast at single-user scale; cacheable later                                                                     |
| Paycheck calculation   | Annual salary â†’ net biweekly (pure function pipeline)                                             | Mirrors real payroll flow; scenario-aware; handles raises, deductions, taxes, and FICA caps                                                       |
| Multi-income           | salary_profiles keyed (user_id, scenario_id, name)                                                  | Multiple profiles per scenario from day one; no migration needed to add income sources                                                            |
| Audit logging          | PostgreSQL trigger-based â†’ system.audit_log                                                       | Every data change captured automatically; no application code needed per table; append-only; indexed for record history and user activity queries |
| Application logging    | Python `logging` â†’ structured JSON                                                                | Request correlation via UUID; dev=pretty-print, prod=JSON stdout for Docker; security events at WARNING                                           |
| MFA                    | TOTP with pyotp; table ready Phase 1, feature Phase 8                                               | Additive to existing JWT auth; no rework of token system; backup codes for recovery                                                               |
| Database normalization | 3NF minimum; self-referencing categories; separated auth tokens, inflation settings, anchor history | No redundant data; referential integrity enforced; scalable for multi-user                                                                        |
| Database schemas       | 5 logical schemas: ref, auth, budget, salary, system                                                | Self-documenting; domain isolation; ready for RLS; mirrors service boundaries                                                                     |
| Enum strategy          | Reference/lookup tables in `ref` schema                                                             | New types added via INSERT not migration; FK-enforced; queryable; joinable                                                                        |
| ORM                    | SQLAlchemy                                                                                          | Standard for Flask, strong PostgreSQL support                                                                                                     |
| Migrations             | Alembic                                                                                             | Pairs with SQLAlchemy, version-controlled schema changes                                                                                          |
| Authentication         | JWT (PyJWT) + bcrypt                                                                                | Stateless, industry standard, hostable                                                                                                            |
| API format             | REST + JSON                                                                                         | Simple, well-understood, good for learning                                                                                                        |
| Frontend framework     | React (Vite)                                                                                        | Most in-demand, clean separation from API                                                                                                         |
| Frontend state         | Zustand                                                                                             | Lighter than Redux, simpler API, sufficient for this scale                                                                                        |
| Charting               | Recharts                                                                                            | React-native, good docs, free                                                                                                                     |
| Dev environment        | Docker Compose                                                                                      | One command to run PostgreSQL + app + backup                                                                                                      |
| Email service          | SendGrid or SMTP (configurable)                                                                     | Free tier for low volume, can stub in dev                                                                                                         |
| Linting (Python)       | Pylint                                                                                              | Per user preference                                                                                                                               |
| Linting (SQL)          | SQLFluff                                                                                            | Per user preference                                                                                                                               |
| Naming convention      | snake_case                                                                                          | Per user preference                                                                                                                               |

---

## 9. Resolved Design Decisions

| Question                | Decision                                                                                                                                                                                                                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Authentication          | Build login from the start to support future hosting                                                                                                                                                                                                                           |
| Architecture patterns   | Application factory, Blueprints, service layer â€” explicitly enforced                                                                                                                                                                                                         |
| Database design         | Fully normalized (3NF+); self-referencing categories; auth tokens, inflation, and anchor history normalized into separate tables; CHECK constraints at DB level                                                                                                                |
| Data backup             | Automated `pg_dump` backups (daily, with retention) + Docker volume snapshots                                                                                                                                                                                                  |
| Undo/redo               | Bulk operations are reversible via operation log with snapshot/restore                                                                                                                                                                                                         |
| Notifications           | Both in-app (bell icon) and email (opt-in) for large expenses, low balances, savings milestones, unreconciled periods                                                                                                                                                          |
| Data import             | Start fresh â€” no spreadsheet import (possible future stretch goal)                                                                                                                                                                                                           |
| Expense assignment      | Mix of due-date-based and manual assignment                                                                                                                                                                                                                                    |
| Recurrence              | Auto-generate from rules with manual override on individual entries                                                                                                                                                                                                            |
| What-if scenarios       | Named scenarios, saved, side-by-side comparison                                                                                                                                                                                                                                |
| Savings modeling        | Separate account balances with transfer tracking                                                                                                                                                                                                                               |
| Variable expenses       | Flat estimates now, smart suggestions from actuals later                                                                                                                                                                                                                       |
| Balance calculation     | Calculate on read (Option A) â€” pure function; no stored balances; cacheable later                                                                                                                                                                                            |
| Remainder handling      | Simplified â€” no explicit reallocation; difference stays in checking balance                                                                                                                                                                                                  |
| Grid view range         | Dynamic â€” defaults to ~2 months; quick-select for 1mo/3mo/6mo/1yr/2yr; responsive columns                                                                                                                                                                                    |
| Salary modeling         | Full paycheck calculator: annual salary â†’ raises â†’ gross â†’ pre-tax deductions â†’ taxes (federal + state + FICA) â†’ post-tax deductions â†’ net biweekly                                                                                                                |
| Salary raises           | Merit (default January) + COLA (default July) + custom one-time; compound on annual salary; scenario-aware                                                                                                                                                                     |
| Deduction inflation     | Opt-in per deduction; inflates at open enrollment month; scenario-aware                                                                                                                                                                                                        |
| Expense inflation       | Opt-in per template; compound formula; global default rate + per-template overrides; scenario-aware                                                                                                                                                                            |
| Tax estimation          | Reasonable withholding estimate (not exact payroll); user-updatable brackets; FICA wage base cap tracked across periods                                                                                                                                                        |
| 3rd paycheck handling   | Deductions track `deductions_per_year` (26/24/12); paycheck calculator detects 3rd paycheck in a calendar month and skips 24-per-year deductions (insurance, dental, vision), resulting in higher net pay for those periods                                                    |
| Database schemas        | Five logical schemas: `ref` (lookup enums), `auth` (identity), `budget` (core domain), `salary` (paycheck domain), `system` (operations) â€” self-documenting and ready for RLS                                                                                                |
| Enum handling           | Reference/lookup tables in `ref` schema replace all hardcoded CHECK constraint enums. Adding new types = INSERT, not migration                                                                                                                                                 |
| Multiple income sources | Built into schema from day one â€” `salary.salary_profiles` keyed on `(user_id, scenario_id, name)` allows multiple profiles per scenario; each has independent raises, deductions, and tax treatment                                                                          |
| Audit logging           | PostgreSQL trigger-based â€” generic `system.audit_trigger_fn()` attached to all tables in auth, budget, and salary schemas; captures every INSERT/UPDATE/DELETE with before/after JSONB, changed fields, user_id, IP, and request_id; append-only with configurable retention |
| Application logging     | Structured JSON via Python logging â€” request middleware generates UUID request_id for correlation; every request logged with method/path/user/status/duration; auth events at INFO/WARNING; unhandled exceptions at ERROR; dev=pretty-print, prod=JSON stdout                |
| MFA / TOTP              | Table (`auth.mfa_configs`) created in Phase 1; feature built in Phase 8; additive to existing JWT flow â€” no rework needed; TOTP secret encrypted at rest; backup codes hashed; login becomes two-step only when MFA is enabled                                               |
