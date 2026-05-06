# Budget App -- Project Roadmap v5

**Version:** 5.0
**Date:** May 6, 2026
**Parent Documents:** project_requirements_v2.md, project_requirements_v3_addendum.md
**Supersedes:** project_roadmap_v4-6.md (preserved for historical reference)

---

## Overview

### What changed in v5

This version is a structural reorganisation of the roadmap; almost no content was deleted.

1. **Numbering collapsed.** v4-6 carried two parallel numbering systems -- a "Priority N"
   track in the summary table and a "Section M / Phase Y" track in the document body. The two
   had drifted (Priority 4 mapped to Section 8, Priority 5 mapped to Section 6, Section 9
   claimed Priority 8 while the priority table also assigned Priority 8 to Multi-user). v5
   drops the Priority column and uses a single sequential top-level numbering scheme
   ordered by execution priority. Subsection labels inside carried-forward sections preserve
   their historical numbers (6.x, 7.x, 8A.x) for continuity with prior commits, design docs,
   and tests; a "Numbering note" at the top of each such section calls this out.
2. **Completed work moved to Appendix A.** All sections marked complete in v4-6 plus the
   newly completed Visualization and Reporting Overhaul and Spending Tracker and Companion
   View are summarised in Appendix A under their original section labels. Full historical
   detail remains in `project_roadmap_v4-6.md`.
3. **Two new sections added.**
   - **Section 1 (Security Remediation):** the in-progress April 2026 audit response
     (56 commits across 10 phases; 16 merged). Entry summarises status and links to the
     canonical plan at `docs/audits/security-2026-04-15/remediation-plan.md`; the plan is not
     duplicated in the roadmap.
   - **Section 2 (Financial Calculation Consistency):** a new parent section addressing
     drift between the multiple paths that compute monetary amounts. Three sequenced stages:
     unify existing paths (committed), double-entry ledger refactor (decision pending), and
     envelope budgeting layer (decision pending, requires the ledger).

### Production status

The app moved to production on March 23, 2026. It runs as a Docker container on an Arch
Linux desktop, with internal access via Nginx and a DNS override, and external access via a
Cloudflare Tunnel. The primary focus is now stabilization, daily-use polish, security
hardening, and incremental feature development.

### Completed phases

See **Appendix A** for the complete list of completed work, including all v3 phases, the
post-production roadmap completions through April-May 2026, and unplanned work. Each entry
preserves its original v4-6 section label for cross-reference.

### Deferred indefinitely

| Source Document | Phase     | Reason                                   |
| --------------- | --------- | ---------------------------------------- |
| v3 Phase 7      | Scenarios | Effort not worth the reward at this time |

See **Appendix B** for the full deferred-items reference.

---

## Roadmap -- Execution Order

| Section | Title                                | Status              | Summary                                                                                                  |
| ------- | ------------------------------------ | ------------------- | -------------------------------------------------------------------------------------------------------- |
| 1       | Security Remediation                 | In progress         | Close 164 verified findings across 56 commits in 10 phases; Phases 1 and 2 complete (16 of 56 merged).   |
| 2       | Financial Calculation Consistency    | Stage A pending     | Unify multiple paths that compute monetary amounts; optionally migrate to double-entry and envelopes.   |
| 3       | Smart Features                       | Planned             | Seasonal forecasting, smart estimates, inflation, third paycheck guidance, anomaly detection, etc.       |
| 4       | Notifications                        | Planned             | 15 notification types in 6 groups; bell + dropdown + `/notifications` page; email deferred.              |
| 5       | Data Export                          | Planned             | CSV, PDF reports, full data backup with restore.                                                         |
| 6       | Multi-User / Kid Accounts            | Far future          | Schema ready; companion role from Appendix A.10 is a precursor.                                          |

---

## 1. Security Remediation

**Status:** In progress (16 of 56 commits merged, ~29% complete).
**Audit date:** April 15, 2026.
**Canonical plan:** `docs/audits/security-2026-04-15/remediation-plan.md` (7042 lines).
**Supporting files in `docs/audits/security-2026-04-15/`:** `findings.md`, `c-09-followups.md`,
`reports/`, `sbom/`, `scans/`.

### 1.1 Context

The April 2026 security audit identified 164 verified findings (1 Critical, 29 High, 52
Medium, 79 Low, 3 Info). The remediation plan prescribes 56 commits across 10 sequential
phases and is the canonical execution document; this roadmap entry is a status pointer only.
Detail (per-commit scope, files, migrations, tests, code snippets) lives in the plan file and
is not duplicated here.

### 1.2 Phase Summary

| Phase | Commits      | Scope                                                                                                                                                  | Status   |
| ----- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| 1     | C-01..C-12   | Crypto and history: SECRET_KEY history excise, session invalidation, MFA hardening (backup codes, TOTP replay, secret storage), rate-limiting, cookies | COMPLETE |
| 2     | C-13..C-16   | Audit log: DB-tier audit triggers rebuild, systematic `log_event()` rollout, off-host shipping, PII redaction                                          | COMPLETE |
| 3     | C-17..C-23   | Financial invariants: anchor balance optimistic locking, stale-form prevention, TOCTOU duplicate prevention, transfer state-machine guards            | NEXT     |
| 4     | C-24..C-28   | Input validation and schema sync: Marshmallow Range sweep, boolean NOT NULL, boundary inclusivity, auth schemas, multi-tenant account_type guard       | Pending  |
| 5     | C-29..C-31   | Access-control consistency: cross-user FK re-parenting fix, analytics ownership checks, 404-everywhere unification                                     | Pending  |
| 6     | C-32..C-39   | Config and hardening: production configs, network topology, Docker hardening bundle, Postgres TLS, Docker secrets, field-level PII encryption          | Pending  |
| 7     | C-40..C-43   | Schema cleanup: migration backfill conventions, duplicate CHECK cleanup, salary + HYSA index/FK repair                                                 | Pending  |
| 8     | C-44..C-52   | Low/Info cleanup: verify_password hardening, grid robustness, retirement Decimal, narrow except blocks, Argon2id migration, config drift check         | Pending  |
| 9     | C-53..C-55   | Bigger features: server-side sessions + WebAuthn, GDPR export and delete, process-memory key documentation                                             | Pending  |
| 10    | C-56         | Host runbook: out-of-repo host hardening (chmod, sysctl, auditd, sshd, GRUB, core dumps, AIDE, NTP, PAM)                                               | Pending  |

### 1.3 Pending architectural decisions

Seven decisions are documented in the plan and gate specific commits. They require
developer input before the affected commits can land:

- **C-06:** Flask-Limiter backend (Redis vs. single-worker Gunicorn).
- **C-15:** Off-host log destination (S3 Object Lock vs. Loki vs. rsyslog).
- **C-02:** HSTS preload submission (now vs. 90-day defer).
- **C-39:** Field-level encryption scope (email + name + balance vs. expand).
- **C-11:** HIBP API source (hosted vs. self-hosted vs. zxcvbn-only).
- **C-48:** Argon2id migration (opportunistic on-login vs. batch).
- **C-53:** Server-side sessions (single commit vs. split into C-53a/C-53b).

### 1.4 Critical path

Phase 3 (Financial Invariants) is the next gate: it must complete before Section 2 (Financial
Calculation Consistency) can begin work that touches transaction or balance code, to avoid
merge conflicts on the same routes and services.

---

## 2. Financial Calculation Consistency

**Status:** Stage A pending start (after Section 1 Phase 3 lands). Stages B and C are
decision-pending and not yet committed work.

### 2.1 Context

The shadow-transaction model is single-entry with derived balances. Multiple derived-balance
paths can drift:

- `app/services/balance_calculator.py` -- canonical period-by-period from anchor.
- `app/routes/grid.py` -- inline subtotal recomputation that mirrors balance_calculator
  logic but is a separate code path.
- `app/services/dashboard_service.py`, `app/services/calendar_service.py`, and
  `app/services/year_end_summary_service.py` -- balance retrieval from the anchor and the
  calculator output.

`Transaction.effective_amount` (`app/models/transaction.py:141`) is the single
amount-source authority (priority: deleted -> excluded statuses -> actual_amount ->
estimated_amount), but it is not used uniformly. Documented divergences:

- **Net biweekly mismatch** (Appendix A.1, task 3.3): the salary profile page once showed
  one net amount while the grid showed a different one. Fixed in March 2026, but the
  underlying multi-path architecture remains.
- **Cell-vs-subtotal drift after envelope carry-forward** (`docs/carry-forward-aftermath-design.md`):
  documented and accepted as a tradeoff, but symptomatic of the larger issue.
- **Direct `estimated_amount` references** in code that should use `effective_amount` for
  status-aware semantics. The list cited in `docs/implementation_plan_section5a.md` is the
  starting inventory (multiple call sites in balance_calculator and downstream consumers).

The transfer rework (Appendix A.2) gave us strong invariants for the transfer subsystem
(see CLAUDE.md "Transfer Invariants" -- 5 invariants enforced in
`app/services/transfer_service.py`), but the broader balance computation is not protected by
the same level of structural enforcement.

### 2.2 Stage A -- Unify existing paths (committed)

**Goal:** eliminate divergence between the multiple paths that derive monetary totals,
without changing the underlying single-entry model.

**Scope:**

- Extract a single shared subtotal/balance helper used by both `balance_calculator.py` and
  `grid.py`. The helper consumes the existing `Transaction.effective_amount` property as
  the single amount-source authority.
- Audit and replace direct `estimated_amount` references with `effective_amount` wherever
  status semantics matter. Use the inventory in `docs/implementation_plan_section5a.md` as
  the starting list; expand by `grep -rn "estimated_amount" app/`.
- Add a path-equivalence regression test suite. For each scenario fixture in
  `tests/conftest.py`, assert that the values produced by `balance_calculator`, the grid
  route's subtotal computation, the dashboard balance retrieval, and the calendar service's
  month-end balance are all equal for the same inputs. Tests must use exact Decimal
  equality, not tolerances.
- Document the canonical balance-computation path in `docs/coding-standards.md` so future
  features cannot reintroduce a parallel path without explicit review. Add a CLAUDE.md note
  if the rule belongs there.

**Deliverable:** every monetary aggregation in the app derives from one helper that calls
`effective_amount`. New regression tests fail loudly if any future change reintroduces a
parallel path.

**Dependencies:** Section 1 Phase 3 (Financial Invariants, C-17..C-23) should land first.
Phase 3 introduces optimistic locking and state-machine guards on transactions and
transfers; Stage A's regression suite needs that machinery in place to assert behaviour
under contention.

### 2.3 Stage B -- Double-entry ledger refactor (decision pending)

**Status:** decision pending. Documented for visibility; do not start without explicit
developer approval and a separate design document.

**Concept:** add a true double-entry journal alongside the existing transactions table. Every
financial operation produces matched debit and credit journal entries. The balance calculator
ultimately becomes a journal aggregator; transactions and transfers become drivers that emit
journal entries.

**Sketched scope (subject to design doc):**

- Add `budget.journal_entries` table: `account_id`, `amount NUMERIC(12,2)`, `dr_cr` flag,
  `transaction_id` FK (nullable, for transaction-driven entries), `transfer_id` FK
  (nullable, for transfer-driven entries), `pay_period_id`, `status_id`, standard audit
  columns. Add to `app/audit_infrastructure.py:AUDITED_TABLES` per CLAUDE.md SQL standards.
- Populate from existing transaction and transfer writes. The transfer service's existing
  shadow-transaction generation extends naturally: each shadow now also emits a debit and a
  credit journal entry. The CLAUDE.md transfer invariants extend to journal entries.
- Run shadow alongside the current calculator. Use Stage A's regression suite to prove
  parity between the journal aggregator and the existing balance_calculator output.
- Make `journal_entries` authoritative only after parity holds across the full test suite
  for a sustained period.
- Backfill from existing `budget.transactions` rows in the migration. The migration is
  destructive in the sense that switching authority is not trivially reversible; standard
  destructive-migration approval applies.

**Why decision-pending:** this is a substantial architectural change. The current model
works. The case for double-entry is mostly defensive (eliminates a class of consistency
bugs) and forward-looking (enables Stage C). The case against is implementation cost,
migration risk, and the fact that Stage A may close enough of the gap to make B
unnecessary.

**Decision input needed:** does the cost/benefit favour the refactor after Stage A is
complete and the actual residual divergence is measurable? Decide after Stage A lands.

### 2.4 Stage C -- Envelope budgeting layer (decision pending, requires Stage B)

**Status:** decision pending; depends on Stage B.

**Concept:** layer category-level envelope budgeting on top of the double-entry ledger. Each
budget category gets a per-period allocation (the envelope); spending consumes the
envelope; remaining balance is visible per-envelope.

**Sketched scope (subject to design doc):**

- Add `budget.category_budget_allocations`: `category_id`, `pay_period_id`,
  `allocated_amount`, `user_id`, audit columns.
- Build on the existing `TransactionTemplate.is_envelope` flag and the `TransactionEntry`
  model from Appendix A.10 (Spending Tracker). Those features track per-purchase remaining
  balance; Stage C extends that model to category-level envelopes that aggregate across
  templates.
- Enforcement service consults journal entries from Stage B for actual spend per envelope.
- UI: per-envelope progress bars, category-level remaining balance, optional hard-cap mode
  vs. soft-warning mode.

**Why decision-pending:** envelope budgeting changes the user's mental model of the app
substantially. The Spending Tracker (Appendix A.10) already provides per-template entry
tracking, which addresses the most common envelope use case (groceries, fuel, etc.). Stage C
generalises that to category-level allocation, which is a different feature with a different
audience. Confirm the user need is real before committing.

**Dependency:** requires Stage B's journal entries as the authoritative spend source; building
Stage C on top of the multi-path single-entry model would re-introduce the consistency
problem Stage A and B aim to eliminate.

---

## 3. Smart Features

> **Numbering note:** This section was Section 6 / Phase 9 in v4-6. Subsection labels (6.1
> through 6.12) are preserved as historical tags for continuity with prior commits, design
> docs, and tests. Cross-references inside this section use the historical labels;
> cross-references to other top-level sections use the new v5 numbering.

**Goal:** Make the app smarter about projecting future expenses based on historical patterns
and detecting anomalies. The core additions are seasonal expense forecasting, rolling average
estimates for non-seasonal variable expenses, inflation adjustments, and expense anomaly
detection at the point of data entry. This section also includes third paycheck actionable
suggestions, estimate confidence indicators, bill due date optimization analysis, and
year-over-year seasonal comparisons. Budget variance analysis (6.5), annual expense calendar
(6.6), and spending trend detection (6.7) computation engines and display layers were built
as part of Visualization and Reporting Overhaul (Appendix A.9), not in this section.

### 6.1 Seasonal Expense Forecasting

This is the primary feature of Phase 9.

#### 6.1.1 Concept

For a handful of expenses with predictable seasonal variation (electricity, gas, water, and
possibly 1-2 others), the user enters historical monthly amounts. The app uses this history to
forecast future amounts, weighting recent years more heavily and detecting year-over-year
trends.

#### 6.1.2 Data Model

New table for historical expense data:

```
budget.seasonal_history
- id: SERIAL PRIMARY KEY
- template_id: INT NOT NULL FK -> budget.transaction_templates(id)
- user_id: INT NOT NULL FK -> auth.users(id)
- year: INT NOT NULL
- month: INT NOT NULL (1-12)
- amount: NUMERIC(10,2) NOT NULL
- period_start_date: DATE             -- start of the billing/consumption period
- period_end_date: DATE               -- end of the billing/consumption period
- due_date: DATE                      -- when the bill was/is due
- created_at: TIMESTAMPTZ DEFAULT NOW()
- updated_at: TIMESTAMPTZ DEFAULT NOW()
- UNIQUE(template_id, year, month)
```

**Indexing by consumption period:** The `year` and `month` columns represent the consumption
period, not the due date. The consumption month is derived from the midpoint of
`period_start_date` and `period_end_date`. For example, a bill due April 14 covering
February 18 through March 18 has a midpoint of approximately March 4, so it is indexed as
year=2026, month=3. This ensures the seasonal curve reflects actual usage patterns (e.g.,
summer electricity consumption maps to summer months, not the billing month that follows).

Both `period_start_date`/`period_end_date` and `due_date` are stored for future-proofing but
are optional for historical data entry. If only the amount and due date are entered, the app
falls back to indexing by due date month.

New columns on `budget.transaction_templates`:

```
- is_seasonal: BOOLEAN DEFAULT FALSE
- seasonal_method: VARCHAR(20) DEFAULT 'weighted_trend'
    CHECK (seasonal_method IN ('weighted_trend', 'weighted_average'))
```

#### 6.1.3 Historical Data Entry

- A "Seasonal History" tab or section on the transaction template edit page, visible only when
  `is_seasonal` is checked.
- The form displays a grid: rows are years, columns are months (Jan-Dec). The user enters
  dollar amounts per cell. Each cell can optionally expand to enter billing period dates
  (period start, period end, due date). If billing period dates are provided, the app
  calculates the consumption month automatically and places the amount in the correct column.
- Manual entry only. No CSV import. This is intentional given the small number of templates
  that will use this feature (approximately 3-5).
- The form should support entering partial years (e.g., only the months you have data for).

#### 6.1.4 Forecasting Engine

New service: `services/seasonal_forecast.py`

- Pure function: given a list of (year, month, amount) historical data points (indexed by
  consumption period) and a target (year, month), return a projected amount.
- **Primary method -- weighted trend:**
  1. For the target month, collect all historical amounts across years.
  2. Apply exponential decay weighting (most recent year gets the highest weight, older years
     decay). Suggested default decay factor: 0.7 (configurable in user_settings if needed
     later).
  3. Calculate a weighted average as the base.
  4. Apply a linear trend adjustment: fit a simple linear regression to the year-over-year
     values for that month and extrapolate the slope forward.
  5. The final forecast = weighted_average + (trend_slope \* years_forward).
- **Fallback method -- weighted average only:** Same as above but without the trend adjustment.
  Used when there are fewer than 3 data points for a given month (not enough to detect a
  reliable trend).
- **Minimum data requirement:** At least 1 year of data for the target month. If no data
  exists for a given month, fall back to the template's base amount.
- **Course correction:** As actuals are recorded (transaction marked as "Paid" with an actual
  amount), the actual is automatically added to the seasonal history table for that month/year.
  Future forecasts for the same month in subsequent years will incorporate this new data point.

#### 6.1.5 Integration with Recurrence Engine

- When the recurrence engine generates a transaction for a seasonal template, it calls the
  forecasting engine to get the projected amount for that transaction's target month.
- The recurrence engine already handles monthly placement by due date. The seasonal forecast
  only supplies the amount; placement logic is unchanged.
- The generated transaction should display an indicator (icon or label) showing that the amount
  is a seasonal forecast, not a flat recurring amount.

#### 6.1.6 Applicable Templates

Expected to apply to approximately 3-5 expense templates: electricity, gas, water, and
possibly 1-2 others (e.g., lawn care, heating oil). The feature is opt-in per template via the
`is_seasonal` flag.

### 6.2 Smart Estimates (Rolling Average)

For non-seasonal variable expenses (groceries, fuel, dining out), suggest a future estimate
based on a rolling average of recent actuals.

#### 6.2.1 Concept

- When the user views a future transaction for a variable expense template, the app calculates
  the average of the last N actuals (suggested default: N = 6, configurable).
- The suggestion is displayed alongside the current estimate but is never auto-applied. The
  user must explicitly accept the suggestion to update the template or individual transaction.
- This feature becomes useful only after enough actuals have been recorded. Display the
  suggestion only when at least 3 actuals exist for the template.

#### 6.2.2 Implementation

- New service: `services/smart_estimate.py`
- Pure function: given a template_id and a count N, query the last N transactions for that
  template where status is "paid" and an actual amount exists. Return the average.
- UI: A small "suggested: $X.XX" label next to the amount field on future transactions for
  eligible templates. Clicking it populates the amount field.

### 6.3 Expense Inflation

- Per-template inflation settings: opt-in per expense template with a global default rate
  (stored in `auth.user_settings.default_inflation_rate`, already exists) and per-template
  override.
- When enabled, the recurrence engine multiplies the base amount by
  `(1 + annual_rate) ^ (years_from_start)` for each future period.
- Inflated amounts display with an indicator so the user knows the number includes an inflation
  adjustment.
- The user can override any individual period's amount, which locks it.
- **Interaction with seasonal forecasting:** If a template is both seasonal and has inflation
  enabled, the seasonal forecast already incorporates trend (which captures inflation
  implicitly). In this case, the explicit inflation adjustment should be disabled or the user
  warned about double-counting. Recommended: seasonal templates should not also use explicit
  inflation. The trend component of the seasonal forecast serves this purpose.

### 6.4 Deduction Inflation

- Applied at open enrollment time (user-specified month, likely November or December for a
  January effective date).
- Each paycheck deduction can have an optional annual inflation rate.
- At the open enrollment month, deduction amounts for the next year are recalculated:
  `new_amount = current_amount * (1 + deduction_inflation_rate)`.
- The user is prompted to review and confirm adjusted amounts rather than having them
  auto-applied.

### 6.5 Budget Variance Analysis -- COMPLETE

The computation engine and display layer were built as part of Visualization and Reporting
Overhaul (Appendix A.9). See v4-6 Section 6.5 for the original spec.

### 6.6 Annual Expense Calendar -- COMPLETE

Built as part of Visualization and Reporting Overhaul (Appendix A.9). See v4-6 Section 6.6
for the original spec.

### 6.7 Spending Trend Detection -- COMPLETE

Built as part of Visualization and Reporting Overhaul (Appendix A.9). See v4-6 Section 6.7
for the original spec.

### 6.8 Third Paycheck Suggestions

- **Problem:** Biweekly pay results in two months per year containing a third paycheck. The
  calendar service detects these months (`is_third_paycheck_month` flag on the year overview,
  built in Appendix A.9), but the detection is passive -- the user sees a badge but receives
  no actionable guidance on how to use the extra funds.
- **Feature:** An actionable third paycheck card on the dashboard and enhanced calendar badge.
  When the next third paycheck is within 60 days, the dashboard displays a card showing the
  net amount of the extra check alongside two contextual facts: emergency fund status (current
  balance vs. goal target, if an emergency fund savings goal exists and is not yet met) and
  highest-rate debt (account name, rate, remaining balance, if active debt accounts exist).
  These facts give the user context for their decision without recommending one option over
  the other.
- **Action button:** A "Create Transfer" button opens the existing one-time transfer creation
  form (`/transfers/new`) with the amount pre-filled to the net paycheck amount via query
  parameter. The user selects the destination account and confirms. The transfer form route
  needs a small modification to accept an optional `amount` query parameter as a form default.
- **Calendar integration:** The existing "3rd check" badge on the year overview calendar gains
  a tooltip or popover showing the same net amount and contextual facts. No action button on
  the calendar -- the dashboard is the action surface.
- **Implementation:** No new service file. The dashboard service orchestrates the lookup using
  existing data: net biweekly pay from the salary service, emergency fund goal from the
  savings dashboard service, highest-rate debt from the debt summary service
  (`_compute_debt_summary`), and third paycheck dates from the calendar service's
  `_detect_third_paycheck_months()`. A helper function within the dashboard service assembles
  the card data.
- **Future enhancement -- priority engine (not in Phase 9):** A future mini-phase could add a
  priority engine that recommends the optimal destination for surplus funds. This would
  require new `auth.user_settings` columns (surplus priority mode, savings floor months),
  committed debt strategy selection, and orchestration logic. Noted here for future scoping
  but explicitly out of scope for Phase 9.
- **Dependency:** Built on the calendar service and dashboard service from Appendix A.9.
  Benefits from the debt summary (Appendix A.7 task 5.12) and savings goal trajectory
  (Appendix A.7 task 5.15) features already implemented.

### 6.9 Estimate Confidence Indicator

- **Problem:** Every future transaction in the grid displays an estimated amount, but the
  reliability of that estimate varies dramatically depending on its source. A seasonal
  forecast backed by 4 years of history is far more reliable than a flat amount the user
  entered once during initial setup. The user has no visual signal for which estimates to
  trust and which to review.
- **Feature:** A three-tier confidence indicator displayed on future transactions in the grid
  and transaction detail views.
- **Confidence tiers:**
  1. **High confidence (green):** Seasonal forecast (6.1) with 3+ years of data for the
     target month, OR rolling average (6.2) with 6+ actuals.
  2. **Medium confidence (yellow):** Seasonal forecast with 1-2 years of data, OR rolling
     average with 3-5 actuals, OR inflation adjustment (6.3) applied.
  3. **Low confidence (gray):** Template's static base amount with no supporting actuals, no
     seasonal history, and no inflation adjustment.
  If multiple sources apply, the highest-confidence source wins.
- **Display:** A small color-coded icon (dot or badge) on each future transaction cell in the
  grid. Tooltip on hover shows the source: "Seasonal forecast (4 years of data)" or "Rolling
  average of last 6 actuals" or "Static estimate -- no actuals recorded." Same indicator with
  a one-line explanation in the transaction quick edit / detail view. No indicator on past
  transactions with actual amounts.
- **Implementation:** New service: `services/estimate_confidence.py`. Pure function that takes
  a template_id and target (year, month) and returns a confidence tier and source description.
  Checks: is the template seasonal? Count seasonal_history records. Does the template have
  actuals? Count paid transactions. Does the template have inflation enabled? Apply tier
  rules. Supports a batch mode (list of template_id/year/month tuples, two bulk queries,
  evaluate in memory) to avoid N+1 queries during grid rendering.
- **No new data model.** Computed from existing data: `seasonal_history` rows (6.1), paid
  transaction counts, and template flags.
- **Dependency:** Requires 6.1 (seasonal history table, `is_seasonal` flag) and 6.2 (rolling
  average service for actual count lookup). Benefits from 6.3 (inflation flag adds a
  medium-confidence signal).

### 6.10 Bill Due Date Optimization

- **Problem:** When expenses cluster unevenly across pay periods, one period may have a
  dangerously low projected end balance while the adjacent period has comfortable surplus.
  The user has no visibility into which expenses could be shifted to balance cash flow.
- **Feature:** An advisory analysis that detects pay period imbalance, identifies moveable
  expenses, simulates the balance impact of shifting them, and recommends an action path.
  The app advises but does not automate -- the user decides how to act (change the due date,
  use the Credit/Credit Payback feature, or transfer from savings).

#### 6.10.1 Data Model

New column on `budget.transaction_templates`:

```
- is_due_date_flexible: BOOLEAN DEFAULT FALSE
```

This flag indicates whether the template's due date can realistically be moved. Fixed
obligations (mortgage, loan payments, rent) are `FALSE`. Discretionary or provider-adjustable
expenses (groceries, subscriptions, some utilities) can be marked `TRUE` by the user on the
template edit form.

#### 6.10.2 Analysis Engine

New service: `services/due_date_optimizer.py`

- **Input:** A date range (typically the next 2-4 pay periods) and the user's active
  transaction templates.
- **Step 1 -- Detect imbalance:** For each pay period in the range, compute total committed
  outflows and projected end balance. Identify pairs of adjacent periods where the balance
  difference exceeds a threshold (any period's projected end balance drops below the user's
  warning threshold, or balance difference between adjacent periods exceeds 40% of net pay).
- **Step 2 -- Identify moveable expenses:** From the lower-balance period, collect generated
  transactions whose template has `is_due_date_flexible=TRUE`. Sort by amount descending.
- **Step 3 -- Simulate moves:** For each moveable expense, calculate the effect of shifting it
  to the adjacent higher-balance period. Report: "Moving [expense_name] ($[amount]) from
  period [date] to period [date] would raise your low-period end balance from $[old] to
  $[new]."
- **Step 4 -- Recommend action path:** For each suggested move:
  1. If the expense due date falls after the next paycheck date: "This bill can be
     rescheduled -- change the due date to [suggested_date] or later."
  2. If the expense due date falls before the next paycheck (must be paid before money
     arrives): "This bill is due before your next paycheck. Options: pay with a credit card
     (use the Credit/Credit Payback feature), or transfer from savings to cover the
     $[shortfall] gap."
  3. Compute the exact shortfall amount for bridge funding scenarios.
- **Output:** A list of suggestion objects: template name, amount, source period, target
  period, old end balance, new end balance, action path (reschedule / credit card / savings
  transfer), and shortfall amount.
- **Pure function:** Receives pre-loaded period and transaction data. Does not query the
  database.

#### 6.10.3 Display

- **Dashboard integration:** When the optimizer detects an actionable imbalance in the next 2
  pay periods, show a brief alert in the dashboard's "Alerts / Needs Attention" section:
  "Cash flow imbalance detected in [period_date] -- [count] suggestion(s) available." Links
  to the full analysis.
- **Full analysis view:** A section on the analytics page or a dedicated page. Table showing
  each suggestion with template name, amount, source/target periods, balance improvement, and
  recommended action. All suggestions are informational -- no action buttons that automate
  the move.

#### 6.10.4 Dependency

- **Requires:** Dashboard service (Appendix A.9) for alert integration; existing balance
  calculator for projected end balances.
- **No dependency on 6.1-6.4.** Works with whatever amounts are in the generated
  transactions.
- **Benefits from:** Section 4 (notifications) for delivering imbalance alerts.

### 6.11 Year-over-Year Seasonal Comparison

- **Problem:** Seasonal forecasts are opaque. The user sees a forecasted amount for a future
  seasonal bill but has no easy way to verify whether it feels right without manually looking
  up last year's actual.
- **Feature:** When viewing a future transaction for a seasonal template, show the prior
  year's actual alongside this year's forecast. Example: "Jul 2025 actual: $187.32 --
  Forecast: $195.00 (+4.1%)." If no prior year actual exists, show: "No prior year data for
  [month]."
- **Display locations:**
  - **Grid transaction cell:** Tooltip or expanded detail view shows the comparison.
  - **Transaction quick edit / detail:** Small info line below the amount field.
  - **Seasonal history entry form (6.1.3):** Highlight cells that already have prior year data
    and show the year-over-year change as the user enters new values. Catches data entry
    errors (e.g., $1,870 instead of $187).
- **Implementation:** Add a function to `services/seasonal_forecast.py` (created in 6.1.4):
  `get_prior_year_comparison(template_id, target_year, target_month)`. Returns a dict:
  `{prior_year, prior_amount, forecast_amount, change_pct}` or `None` if no prior year data.
  Supports batch lookup (list of template_id/year/month tuples, one bulk query) for grid
  rendering.
- **No new data model.** Reads from the `seasonal_history` table created by 6.1.
- **Dependency:** Requires 6.1 (seasonal history table and forecasting engine). Should be
  implemented after 6.1 is complete and the user has entered at least one year of seasonal
  history.

### 6.12 Expense Anomaly Detection

- **Problem:** When a recurring bill comes in significantly different from its expected amount,
  the user may not notice until they've already confirmed the transaction. Billing errors,
  forgotten add-ons, rate changes, and data entry typos go undetected at the point of entry.
- **Feature:** When the user records an actual amount for a recurring transaction (via
  mark-as-paid), the app compares the actual to the expected amount and flags significant
  deviations inline before the transaction is confirmed.

#### 6.12.1 Expected Amount Resolution

The comparison uses the most specific estimate source available, checked in priority order:

1. **Seasonal forecast (6.1):** If the template has `is_seasonal=True` and a forecast is
   available for the transaction's target month.
2. **Rolling average (6.2):** If the template has 3+ paid actuals.
3. **Template base amount:** The template's static `amount` field.

#### 6.12.2 Anomaly Threshold

An actual amount is flagged as anomalous when it deviates from the expected amount by more
than a configurable percentage threshold.

- **New column:** `auth.user_settings.anomaly_threshold_pct` (NUMERIC(5,2), DEFAULT 20.00).
- Global threshold (all templates). Per-template override deferred to a future enhancement.
- Both over and under deviations are flagged.

#### 6.12.3 Detection Timing and Display

- **Primary trigger -- mark-as-paid flow:** When the user enters an actual amount during
  mark-as-paid (grid or dashboard), the anomaly check runs before the transaction is
  confirmed. If anomalous, an inline warning is displayed: "This amount ($[actual]) is [X]%
  [higher/lower] than expected ($[expected] from [source_label]). Confirm or correct."
  Source label identifies the estimate source: "seasonal forecast", "rolling average", or
  "template amount."
- **Two-step confirmation:** When an anomaly is detected, the response renders a confirmation
  form with the warning and two buttons: "Confirm $[actual]" and "Edit Amount." The "Confirm"
  button submits with a `force=true` parameter that bypasses the anomaly check. This prevents
  accidental confirmation of typos while not blocking legitimate unusual amounts.
- **Dashboard mark-as-paid:** Same anomaly check and two-step confirmation applies to the
  dashboard's mark-as-paid flow (built in Appendix A.9).

#### 6.12.4 Implementation

- **New service:** `services/anomaly_detection.py`
- **Primary function:** `check_anomaly(template_id, actual_amount, target_year, target_month,
  threshold_pct)` -- returns an `AnomalyResult` with fields: `is_anomalous` (bool),
  `expected_amount` (Decimal), `source` (str), `deviation_pct` (float), `direction` (str:
  'over' or 'under').
- **Resolution logic:** Checks sources in priority order (6.12.1). Calls
  `seasonal_forecast.get_forecast()` if seasonal, then `smart_estimate.get_rolling_average()`
  if actuals exist, then falls back to template base amount.
- **Integration:** The transaction route's mark-done handler calls `check_anomaly()` when
  `actual_amount` is provided. If anomalous and `force` is not set, the response includes the
  warning partial. If `force=true`, the transaction is committed normally.
- **Shared utility:** Tasks 6.9 and 6.12 both resolve "best estimate source for this
  template" in the same priority order. A shared `_resolve_best_estimate(template_id, year,
  month)` utility function avoids duplicating this logic.
- **No retroactive review screen.** Budget variance analysis (6.5, completed) already
  provides retroactive visibility into estimate-vs-actual gaps.

#### 6.12.5 Interaction with Other Features

- **6.1 course correction:** When a seasonal transaction is marked paid, 6.1.4 writes the
  actual to seasonal_history. Anomaly detection runs before this write. If the user confirms
  an anomalous amount, it still gets recorded -- the actual is real data regardless.
- **6.2 smart estimates:** An anomalous-but-confirmed actual shifts the rolling average. Over
  time, if the new amount becomes the norm, future occurrences stop being flagged.
- **6.9 estimate confidence:** The anomaly service can reuse the confidence service's source
  resolution logic (or both use the shared utility).

#### 6.12.6 Dependency

- **Requires:** 6.1 (seasonal forecast as comparison source) and 6.2 (rolling average as
  comparison source). Can function with only the template base amount if 6.1 and 6.2 are not
  yet implemented, but value is significantly reduced.
- **Benefits from:** 6.9 (shared estimate source resolution logic).
- **Requires:** Dashboard mark-as-paid flow (Appendix A.9) for dashboard integration. Grid
  integration depends only on the existing mark-as-paid endpoint.

---

## 4. Notifications

> **Numbering note:** This section was Section 7 / Phase 10 in v4-6. Subsection labels (7.1
> through 7.4 and their nested numbering) are preserved as historical tags.

**Goal:** Alert the user to important financial events without requiring them to manually scan
the grid or dashboard. Provide 15 notification types across 6 logical groups, delivered in-app
via a bell icon dropdown and a full `/notifications` page. Email delivery is added as a
deferred sub-phase once the self-hosted mail server is operational. All notification types
default to in-app enabled and email disabled. Users manage preferences through a grouped
settings UI with expandable sections at `/settings/notifications`.

### 7.1 Notification Infrastructure

#### 7.1.1 Data Model

The `system.notifications` and `system.notification_settings` tables are already stubbed in the
schema. The implementation should include:

```
system.notification_settings
- id: SERIAL PRIMARY KEY
- user_id: INT NOT NULL FK -> auth.users(id)
- notification_type: VARCHAR(50) NOT NULL
- is_enabled: BOOLEAN DEFAULT TRUE
- delivery_in_app: BOOLEAN DEFAULT TRUE
- delivery_email: BOOLEAN DEFAULT FALSE
- threshold_warning: NUMERIC(12,2)         -- low balance warning threshold (per account)
- threshold_critical: NUMERIC(12,2)        -- low balance critical threshold (per account)
- lookahead_periods: INT DEFAULT 6         -- how many future periods to check for low balance
                                           -- (default 6 = ~3 months biweekly)
- days_before: INT                         -- for upcoming expense / ARM rate reminders
- large_expense_amount: NUMERIC(12,2)      -- flat dollar threshold for large expense alerts
- large_expense_pct: NUMERIC(5,2)          -- percentage-of-net-paycheck threshold for large
                                           --   expense alerts (either or both may be set)
- missed_payment_escalation_days: INT DEFAULT 3
                                           -- days after due date before warning -> critical
- period_aging_warning_days: INT DEFAULT 3 -- days past period end before warning fires
- period_aging_critical_days: INT DEFAULT 7
                                           -- days past period end before critical fires
- template_change_threshold_pct: NUMERIC(5,2) DEFAULT 15.00
                                           -- rolling avg vs. template amount deviation
                                           --   threshold for template change detection
- created_at: TIMESTAMPTZ DEFAULT NOW()
- updated_at: TIMESTAMPTZ DEFAULT NOW()
- UNIQUE(user_id, notification_type)

system.notifications
- id: SERIAL PRIMARY KEY
- user_id: INT NOT NULL FK -> auth.users(id)
- notification_type: VARCHAR(50) NOT NULL
- severity: VARCHAR(10) NOT NULL CHECK (severity IN ('info', 'warning', 'critical'))
- title: VARCHAR(200) NOT NULL
- message: TEXT NOT NULL
- is_read: BOOLEAN DEFAULT FALSE
- is_pinned: BOOLEAN DEFAULT FALSE         -- for weekly digest pinned in dropdown
- related_entity_type: VARCHAR(50)         -- 'account', 'transaction', 'pay_period', etc.
- related_entity_id: INT
- related_entity_url: VARCHAR(500)         -- pre-computed link to relevant page
- snoozed_until: TIMESTAMPTZ              -- null = not snoozed; notification hidden until
                                           --   this timestamp
- resolved_at: TIMESTAMPTZ                -- null = unresolved; set when underlying condition
                                           --   clears (auto-resolve) or user dismisses
- created_at: TIMESTAMPTZ DEFAULT NOW()
- read_at: TIMESTAMPTZ

system.notification_email_preferences
- id: SERIAL PRIMARY KEY
- user_id: INT NOT NULL FK -> auth.users(id) UNIQUE
- preferred_delivery_time: TIME DEFAULT '07:00'
                                           -- user's preferred time for email delivery
- delivery_window_start: TIME DEFAULT '07:00'
                                           -- emails generated outside this window are held
- delivery_window_end: TIME DEFAULT '21:00'
                                           -- until the window opens
- created_at: TIMESTAMPTZ DEFAULT NOW()
- updated_at: TIMESTAMPTZ DEFAULT NOW()
```

#### 7.1.2 Notification Types

Organized into 6 groups matching the settings UI layout.

**Group 1: Balance & Cash Flow**

1. **Low projected balance (warning and critical):** Triggered when the projected end balance
   for any future pay period within the configured lookahead window drops below the configured
   threshold. Warning and critical thresholds are configurable per account. Severity is
   determined by which threshold is breached. The lookahead window defaults to 6 periods (~3
   months biweekly) and is configurable via `lookahead_periods`. The app calculates out to 52
   periods by default, but generating low-balance notifications for periods 6+ months out
   creates noise the user cannot act on.
2. **Balance recovery:** Triggered when a prior low-balance warning's underlying condition
   clears (the projected balance for that period recovers above the threshold). The original
   warning notification is auto-resolved (`resolved_at` set) and a new info-severity
   notification is created: "Your checking balance for [period_date] has recovered from
   -$[old_balance] to $[new_balance]. The low balance warning has been resolved." Provides
   confirmation that the user's corrective action worked.
3. **Pre-payday cash flow summary:** Triggered 2 days before each payday. Generates a single
   notification summarizing the upcoming period: bill count, total bill amount, and projected
   end balance. Example: "Next period (Apr 11--24): 6 bills totaling $1,847. Projected end
   balance: $423." Fires even when nothing is wrong -- proactive awareness, not just alerts.
   Implementation: one query against generated transactions for the next period, formatted
   into a single notification. Runs during the daily scheduled check.

**Group 2: Bills & Payments**

4. **Upcoming large expense:** Triggered N days before a large expense is due (`days_before`
   configurable, default: 7). "Large" is configurable as a flat dollar threshold
   (`large_expense_amount`), a percentage of net paycheck (`large_expense_pct`), or both. When
   both are set, an expense triggers the notification if it exceeds either threshold. Gives the
   user time to ensure funds are available.
5. **Missed payment detection:** Triggered when a recurring transaction passes its due date and
   remains in Projected status (not marked Paid). Catches genuinely missed payments and
   transactions the user forgot to reconcile. Detection runs during the daily scheduled check
   (7.1.3). Severity: warning initially, escalates to critical after a configurable number of
   days (`missed_payment_escalation_days`, default: 3). Auto-resolved when the transaction is
   marked Paid.
6. **Unreconciled period aging:** Triggered when a pay period's end date has passed and the
   period has not been reconciled (no anchor balance set). Warning fires after
   `period_aging_warning_days` (default: 3) past the period end date. Escalates to critical
   after `period_aging_critical_days` (default: 7). Message includes the downstream impact:
   "Your [period_date] period ended [N] days ago and hasn't been reconciled. Projected
   balances for all future periods may be inaccurate." Auto-resolved when the period is
   reconciled (anchor balance set). The dashboard already computes this as an ephemeral alert
   (Appendix A.9) -- this notification persists it and adds escalation.

**Group 3: Savings & Goals**

7. **Savings milestone reached:** Triggered when a savings goal reaches a milestone percentage
   (25%, 50%, 75%, 100%). Informational and motivational. The 100% milestone receives distinct
   celebratory treatment in the UI (different icon or color, congratulatory message) to
   differentiate it from routine progress notifications.
8. **Savings goal pace alert:** Triggered when a savings goal with a target date is falling
   behind the required savings rate. Depends on the savings goal trajectory feature
   (Appendix A.7 task 5.15). Includes the corrective action: "At your current rate, you'll
   miss your Emergency Fund goal by $2,400. Increase monthly contributions from $500 to $650
   to reach your target by December 2027." Links to the savings account dashboard.
9. **Savings contribution reminder:** Triggered at the start of each month if no transfer to a
   goal-linked savings account has been recorded for that month. Message: "No contribution to
   your [goal_name] this month. You need $[required_monthly]/month to stay on pace for your
   [target_date] target." Different from the pace alert (#8), which fires after you've fallen
   behind -- this fires before you fall behind, when there's still time to act. Depends on
   savings goal trajectory (Appendix A.7 task 5.15). Runs during the daily scheduled check.

**Group 4: Debt**

10. **Debt payoff milestone:** Triggered when a debt account's balance crosses a round-number
    threshold (e.g., drops below $10,000) or when the projected payoff date moves ahead of
    schedule. Example: "Your auto loan balance dropped below $10,000!" or "At your current
    payment rate, your student loan pays off in October 2028 -- 2 months ahead of schedule."
    Motivational. Uses data already computed by the debt payoff projection services
    (Appendix A.7 tasks 5.1, 5.5). Runs during the daily scheduled check.
11. **ARM rate adjustment reminder:** Triggered N days before an ARM loan's next scheduled rate
    adjustment date (`days_before` configurable, default: 30). The adjustment date is
    calculated from the loan's origination date, `arm_first_adjustment_months`, and
    `arm_adjustment_interval_months`. Reminds the user to watch for their lender's rate change
    notice and update the rate history in the app. Depends on ARM rate support
    (Appendix A.7 task 5.7). Runs during the daily scheduled check.

**Group 5: Templates & Trends**

12. **Recurring template change detection:** Triggered when the rolling average of a recurring
    transaction's actual amounts has diverged from the template's base amount by more than a
    configurable threshold (`template_change_threshold_pct`, default: 15%). Example: "Your
    T-Mobile bill has averaged $95.20 over the last 3 months, but the template amount is
    $85.00 (+12%). Update the template?" Links to the template edit page. Bridges the gap
    between anomaly detection (Section 3 task 6.12, which catches one-off spikes) and the user
    forgetting to update a template after a legitimate rate change. Depends on Section 3 task
    6.2 (rolling average engine). Runs during the daily scheduled check.

**Group 6: Digests**

13. **Weekly summary:** Generated once per week (day configurable, default: Monday). In-app:
    rendered as a pinned notification at the top of the notification dropdown. Email: sent as
    an HTML digest. Content includes: upcoming bills for the week, savings goal progress
    summary, any unresolved alerts from the past week, and net worth change (if net worth is
    computed elsewhere; omit this section if not available). The weekly digest is an
    all-or-nothing toggle -- the user cannot select individual sections. Auto-resolved after
    7 days (replaced by the next week's digest).
14. **Payday reconciliation reminder:** Email only -- not shown in-app (the user is already in
    the app if they can see notifications). Triggered on payday or the day after as a reminder
    to open the app and reconcile the current period. Runs during the daily scheduled check.
    Delivered immediately (not batched).

Future notification types (not in initial build):

- Retirement goal milestones
- Contribution limit warnings (approaching annual 401(k)/IRA limit)
- Upcoming rate/term change awareness (promotional APR expiry, student loan deferment end,
  escrow analysis adjustment) -- depends on whether account parameter architecture stores
  future effective dates for rate changes

#### 7.1.3 Persist Dashboard Alerts as Notifications

The dashboard (Appendix A.9) computes three alert types ephemerally (stale anchors, negative
projected balances, overdue reconciliation). When the notification system is implemented,
these alerts should additionally write to `system.notifications` so they persist across page
navigations and accumulate history. The dashboard continues to compute and display alerts
directly (no dependency on the notification system for rendering), but the notification system
picks up the same conditions during its scheduled check and persists them.

Mapping:
- Stale anchor alert -> no dedicated notification type; covered by unreconciled period aging
  (#6) which escalates on a similar timeline. If the stale anchor alert uses a different
  staleness threshold than period aging, reconcile the two thresholds during implementation.
- Negative projected balance alert -> covered by low projected balance (#1). The dashboard
  alert fires for any negative balance; the notification fires based on configurable
  thresholds. A threshold of $0.00 produces equivalent behavior.
- Overdue reconciliation alert -> covered by unreconciled period aging (#6).

#### 7.1.4 Trigger Mechanism

- **On transaction edit:** After any transaction is created, updated, or status-changed, the
  balance roll-forward is recalculated. At this point, check projected balances against
  thresholds for low balance warnings (#1) and check for balance recovery (#2).
- **Scheduled check (daily):** A lightweight scheduled job (cron or APScheduler) runs daily.
  Checks for: pre-payday cash flow summary (#3), upcoming large expenses (#4), missed
  payments (#5), unreconciled period aging (#6), savings contribution reminders (#9), debt
  payoff milestones (#10), ARM rate adjustment reminders (#11), recurring template change
  detection (#12), weekly summary generation (#13, on the configured day), and payday
  reconciliation reminders (#14). This avoids making the transaction edit path heavier than
  necessary.
- **On savings goal update:** When a savings account balance changes (via transfer or anchor
  true-up), check goal progress for milestone notifications (#7) and pace alerts (#8).

#### 7.1.5 Deduplication

- The system should not generate duplicate notifications for the same event. Use a combination
  of notification_type + related_entity_type + related_entity_id + a time window to prevent
  duplicates.
- Example: A low balance warning for checking account ID 1 for pay period ID 47 should only
  be generated once. If the user fixes the issue and the balance recovers, the warning is
  auto-resolved and a balance recovery notification is generated. If the balance drops again
  later, a new warning is generated.
- Weekly summaries replace the prior week's pinned digest (old one is auto-resolved).
- Savings milestones use the milestone percentage as part of the deduplication key (reaching
  50% only fires once per goal, even if the balance fluctuates around the threshold).

#### 7.1.6 Snooze

- Any notification can be snoozed. Snoozing sets `snoozed_until` to a future timestamp.
- Snoozed notifications are hidden from the dropdown and the `/notifications` page until the
  snooze expires.
- Configurable snooze durations presented as options: 1 day, 3 days, 7 days, until next
  payday. "Until next payday" computes the next pay period start date from the pay period
  schedule.
- Snoozing does not prevent auto-resolve. If the underlying condition clears while a
  notification is snoozed, it is still resolved.
- Snoozing does not prevent deduplication. If the same event would generate a new notification
  while the original is snoozed, no duplicate is created.

#### 7.1.7 Auto-Resolve

- Notifications are automatically marked resolved (`resolved_at` set to current timestamp)
  when the underlying condition clears. Resolved notifications are visually distinct in the UI
  (muted styling, strikethrough, or moved to a "resolved" section).
- Auto-resolve conditions by type:
  - Low projected balance (#1): balance recovers above the threshold for that period.
  - Balance recovery (#2): not auto-resolved (informational; ages out naturally).
  - Pre-payday summary (#3): auto-resolved when the period begins (the summary is no longer
    relevant once the period is active).
  - Upcoming large expense (#4): auto-resolved when the transaction is marked Paid.
  - Missed payment (#5): auto-resolved when the transaction is marked Paid.
  - Unreconciled period aging (#6): auto-resolved when the period is reconciled.
  - Savings milestone (#7): not auto-resolved (permanent achievement record).
  - Savings goal pace (#8): auto-resolved when the savings rate recovers to on-pace.
  - Savings contribution reminder (#9): auto-resolved when a transfer to the goal account is
    recorded for the current month.
  - Debt payoff milestone (#10): not auto-resolved (permanent achievement record).
  - ARM rate reminder (#11): auto-resolved when the rate history is updated in the app for
    the current adjustment period.
  - Template change detection (#12): auto-resolved when the template amount is updated.
  - Weekly summary (#13): auto-resolved after 7 days (replaced by the next digest).
  - Payday reconciliation reminder (#14): auto-resolved when the period is reconciled.

### 7.2 In-App Delivery

#### 7.2.1 Notification Bell and Dropdown

- **Notification bell icon** in the app header (Bootstrap navbar). Displays an unread count
  badge. The badge counts only unread, non-snoozed, non-resolved notifications.
- **Dropdown panel** on click: shows recent notifications (last 10), newest first. The weekly
  digest (#13) is pinned to the top of the dropdown when present, regardless of age. Each
  notification shows severity (color-coded), title, timestamp, and a brief message.
- **Actions per notification:** mark as read (click), snooze (with duration picker), dismiss
  (mark resolved manually).
- **Bulk actions:** mark all as read.
- **Link to context:** Notifications with a `related_entity_url` link to the relevant page
  (e.g., a low balance warning links to the pay period in the grid; a savings milestone links
  to the account dashboard; a template change detection links to the template edit page).
- **"View all" link** at the bottom of the dropdown navigates to `/notifications`.

#### 7.2.2 Notifications Page (`/notifications`)

- Full-page view of all notifications.
- **Filters:** by notification type, by severity (info/warning/critical), by date range, by
  status (unread/read/resolved/snoozed).
- **Default view:** shows unresolved, non-snoozed notifications, newest first.
- **Resolved notifications** are accessible via the status filter but hidden by default to
  keep the view clean.
- **Bulk actions:** mark selected as read, snooze selected, dismiss selected.
- **No analytics or trends.** This is a filtered list, not a reporting tool. Historical
  analysis of notification patterns can be done via direct database queries if needed.

### 7.3 Settings UI (`/settings/notifications`)

- Located as a new tab or section in the existing Settings area.
- **Layout: Option B -- grouped settings with expandable sections.** Notification types are
  organized into 6 collapsible cards matching the notification type groups:
  1. Balance & Cash Flow (types #1, #2, #3)
  2. Bills & Payments (types #4, #5, #6)
  3. Savings & Goals (types #7, #8, #9)
  4. Debt (types #10, #11)
  5. Templates & Trends (type #12)
  6. Digests (types #13, #14)
- **Group-level master toggle:** Each group card has a toggle that enables/disables all
  notification types within the group at once. Expanding the card reveals per-type controls.
- **Per-type controls:** Each notification type within a group has:
  - Enabled/disabled toggle
  - Delivery channel checkboxes: In-App, Email
  - Type-specific parameters displayed inline (thresholds, days-before, lookahead window,
    percentages, etc.)
- **Email checkboxes** are greyed out with a tooltip ("Email delivery requires mail server
  configuration") until the mail server is configured. The presence of a configured mail
  server can be indicated by an application setting or environment variable.
- **Defaults for all types:** In-app enabled, email disabled.
- **Defaults for new types added in future releases:** In-app enabled, email disabled. When a
  new notification type is added in a future release, a row is inserted into
  `notification_settings` for each existing user with these defaults.
- **Email delivery preferences** section at the top or bottom of the page (outside the type
  groups): preferred delivery time, delivery window start, delivery window end. Greyed out
  until mail server is configured.

### 7.4 Email Delivery (Deferred Sub-phase)

- **Dependency:** Requires a self-hosted mail server to be operational. This is infrastructure
  work outside the app (OPNsense mail features or a dedicated mail service on the Arch Linux
  host). Research and setup should happen in parallel with the in-app notification build.
- **Implementation:** When a notification is generated and the user has `delivery_email = TRUE`
  for that type, queue an email. Use Python's `smtplib` with the self-hosted SMTP server.
- **Delivery window:** Emails are only sent during the user's configured delivery window
  (`delivery_window_start` to `delivery_window_end`). Emails generated outside this window
  are queued and delivered when the window opens. The preferred delivery time
  (`preferred_delivery_time`) is used for scheduled digests and reminders.
- **Email format:** Simple HTML email with the notification title, message, severity, and a
  link back to the app.
- **Batching:** For notification types that could fire frequently (low balance warnings during
  a heavy editing session), batch emails into a digest rather than sending one per event.
  Batch window: 1 hour. Milestone notifications, payday reconciliation reminders, and the
  weekly digest send immediately (within the delivery window).
- **Payday reconciliation reminder (#14):** Email only. This notification type is not rendered
  in-app. The in-app delivery checkbox is hidden or disabled for this type in the settings UI.
- **Weekly digest (#13):** Sent as an HTML email containing all digest sections. Delivered at
  the user's preferred delivery time on the configured day of week.

---

## 5. Data Export

> **Numbering note:** This section was Section 8A in v4-6. Subsection labels (8A.1 through
> 8A.3) are preserved as historical tags.

**Goal:** Provide data export capabilities for use in external tools, tax preparation, and
personal record-keeping. This section is a data management concern independent of the
visualization and reporting overhaul (which built CSV export for analytics views as part of
Appendix A.9).

- **Problem:** The app currently has no general transaction-level data export capability. The
  user cannot extract their financial data for use in external tools, tax preparation, or
  personal record-keeping.
- **Feature:** Export functionality accessible from a settings or reports page. Three export
  types:

### 8A.1 CSV Export

Export transactions for a configurable date range. Columns include: date, pay period,
transaction name, category, estimated amount, actual amount, status, account, and notes.
Transfers include both the transfer record and the shadow transaction detail. The user selects
a date range and optionally filters by account, category, or status. The export downloads as
a `.csv` file.

### 8A.2 PDF Export

Generate printable PDF reports for account dashboards, payoff calculator results, amortization
schedules, and the year-end financial summary (Appendix A.9). These are formatted for sharing
with a financial advisor, lender, or for personal record-keeping. Each exportable page gets a
"Download PDF" button.

### 8A.3 Full Data Backup

A complete export of all user data as a structured file (JSON or SQL dump) that can be used
for disaster recovery without requiring database-level access. Triggered from the settings
page. The backup includes all accounts, transactions, templates, transfers, salary profiles,
tax configurations, and account parameters. A corresponding import/restore function allows the
user to rebuild their data from a backup file.

- **Implementation notes:** CSV export is the highest priority (most broadly useful). PDF
  export depends on a PDF generation library (e.g., WeasyPrint or ReportLab). Full data
  backup is an ops feature that should be simple and reliable rather than feature-rich.

---

## 6. Multi-User / Kid Accounts

**Status:** Far future, not actively planned.

The database schema already includes `user_id` on all relevant tables. The companion role
introduced in Appendix A.10 (`owner`/`companion` with `linked_owner_id`) is a deliberate
precursor. The full multi-user design should evaluate compatibility with the companion model
and plan the migration path. When the time comes, the work is primarily:

- Registration UI and flow (note: `REGISTRATION_ENABLED` toggle already exists, added in
  Appendix A.5).
- Ensuring all queries filter by `user_id` (audit needed; substantially advanced by
  Section 1's access-control consistency phase).
- Role/permission model (parent vs. kid account).
- Kid account restrictions (view-only? limited editing?).
- **Account sharing model:** Some accounts may need to be visible to multiple users (e.g., a
  joint checking account shared between spouses, a savings account visible to both parent and
  child). The multi-user design should not assume strictly siloed data. A sharing model where
  specific accounts can be linked to multiple users (with configurable permissions: view-only
  vs. full access) would support household financial management. This does not need to be
  designed now but should be noted as a constraint so the eventual implementation does not
  paint itself into a single-user-per-account corner.

This section will be scoped when it becomes relevant.

---

## Appendix A -- Completed Work

This appendix records work that has shipped. Each entry preserves its original v4-6 section
label so prior commits, PRs, and design docs continue to resolve. Full historical detail
remains in `project_roadmap_v4-6.md`; this appendix is a navigation index, not a duplicate.

### A.1 Critical Bug Fixes (v4-6 Section 3) -- COMPLETE March 2026

10 production bug fixes including the tax-on-gross-pay calculation error (3.1), recurrence
correctness audit (3.2), net biweekly mismatch (3.3), HTMX form re-render bugs (3.4, 3.7),
escrow-with-inflation entry (3.6), pension date validation (3.8), stale retirement message
(3.9), and the paycheck calibration feature (3.10) which superseded the originally-planned
Actual Paycheck Value Entry. See v4-6 Section 3 for full resolution notes.

### A.2 Transfer Architecture Rework (v4-6 Section 3A) -- COMPLETE March 2026

Eliminated the dual-path `budget.transactions` / `budget.transfers` architecture. Every
transfer now has two linked shadow transactions (one expense, one income); the balance
calculator queries only `budget.transactions`. Established the 5 transfer invariants that
appear in CLAUDE.md. Supporting documents: `docs/transfer_rework_design.md`,
`docs/transfer_rework_inventory.md`, `docs/transfer_rework_implementation.md`. See v4-6
Section 3A for full detail.

### A.3 UX/Grid Overhaul (v4-6 Section 4) -- COMPLETE March 2026

17 daily-use grid and detail-page improvements: full row headers (4.1), date format cleanup
(4.3), status refactor and rename to ID-based lookups across all reference tables (4.4a/b/c),
tax config page reorganization (4.6), post-creation parameter setup redirects (4.7, 4.8),
chart contrast and sizing fixes (4.9, 4.10), salary profile button placement (4.11), grid
tooltip enhancement (4.12), emergency fund coverage calculation fix (4.13), checking balance
projection on the account detail page (4.14), auto loan parameter fixes (4.15), retirement
date validation UX (4.16), retirement return rate clarity (4.17). Supporting document:
`docs/implementation_plan_section4.md`. See v4-6 Section 4 for full detail.

### A.4 Account Parameter Architecture (v4-6 Section 4A) -- COMPLETE March 2026

Architectural rework that completed the metadata-driven account parameter dispatch system.
`HysaParams` renamed to `InterestParams`; `has_interest`, `has_amortization`, `has_parameters`,
`category_id`, and `max_term_months` flags drive all dispatch with zero hardcoded type ID
checks. Money Market and CD types enabled. Supporting document:
`docs/account_parameter_architecture.md`. See v4-6 Section 4A for full detail.

### A.5 Adversarial Audit Remediation (v4-6 Section 4B) -- COMPLETE March 2026

Comprehensive 17,844-line adversarial codebase audit identifying 1 Critical, 11 High, 17
Medium, and 15 Low findings. Critical: silent paycheck fallback in the recurrence engine
(broad `except Exception` masking financial calculation failures). High findings included
scenario_id IDOR, salary route info leakage, AccountType mutation accessibility, grid
subtotals using float arithmetic, seed script crash on migrated databases, and systematic
ref-table string-name comparisons. All findings remediated. Supporting documents:
`docs/adversarial_audit.md`, `docs/implementation_plan_audit_remediation.md`. See v4-6
Section 4B for full detail.

> **Note:** A second, deeper security audit was conducted in April 2026 (`docs/audits/security-2026-04-15/`).
> That audit's remediation work is tracked as Section 1 in this roadmap.

### A.6 Cleanup Sprint (v4-6 Section 5A) -- COMPLETE April 2026

Five tasks from production feedback: estimated-vs-actual grid calculation fix that introduced
`effective_amount` semantics (5A.1), category item sub-headers in grid (5A.2), salary listing
page button cleanup (5A.3), category management overhaul with edit, re-parent, and group
dropdown (5A.4), unified two-step delete/archive lifecycle pattern across templates,
transfers, accounts, and categories (5A.5). Source: `fixes_improvements.md`. See v4-6 Section
5A for full detail.

### A.7 Debt and Account Improvements (v4-6 Section 5) -- COMPLETE April 2026

16 tasks completing the debt account story: payment linkage to amortization engine (5.1),
income-relative savings goals with `ref.goal_modes` and `ref.income_units` (5.4), payoff
calculator multi-scenario visualization with original/committed/what-if lines and floor
marker (5.5), savings dashboard SRP refactor (5.6), ARM rate adjustment support in
amortization engine (5.7), amortization engine edge cases for overpayment and zero-balance
termination (5.8), loan payoff lifecycle with recurring transfer end date and account
archival (5.9), refinance what-if calculator (5.10), debt snowball/avalanche cross-account
strategy (5.11), debt summary metrics and DTI ratio (5.12), full amortization schedule view
(5.13), payment allocation breakdown on loan dashboard (5.14), savings goal progress
trajectory (5.15), recurring obligation summary page (5.16). Tasks 5.2 (recurrence audit) and
5.3 (actual paycheck value entry) were removed as superseded by Sections 3.2 and 3.10. See
v4-6 Section 5 for full detail.

### A.8 Mobile Responsiveness (Unplanned) -- COMPLETE April 2026

Unplanned work delivered in April 2026: CSS/JS/template-only changes for a mobile-responsive
web experience, including bottom-sheet patterns for transaction detail, single-period grid
navigation, and responsive layouts at Bootstrap `sm` and `md` breakpoints. No data model or
service changes.

### A.9 Visualization and Reporting Overhaul (v4-6 Section 8) -- COMPLETE May 2026

Replaced the existing `/charts` page with two major additions: a summary dashboard at `/`
(now the app's landing page) and an analytics page at `/analytics` (tabbed container with
calendar, year-end summary, budget variance, and spending trends). Built the computation
engines AND display layers for budget variance analysis (originally 6.5), annual expense
calendar with third-paycheck month detection (originally 6.6), and spending trend detection
(originally 6.7). Added CSV export for all analytics views (analytics-level CSV; full
transaction-level export remains in Section 5). Fixed the x-axis date format bug (8.0a). Task
8.0b (inaccurate balance values) excluded per the scope document; no bug found in code audit.
Supporting documents: `docs/section8_scope.md`, `docs/implementation_plan_section8.md`. See
v4-6 Section 8 for full detail.

### A.10 Spending Tracker and Companion View (v4-6 Section 9) -- COMPLETE May 2026

Three interconnected features: sub-transaction entry tracking via `budget.transaction_entries`
on budget-type transactions with remaining balance visibility (9.1, 9.2, 9.4); entry-level
credit card workflow with aggregated CC paybacks per parent transaction per period (9.3);
companion user role on `auth.users` (`role` and `linked_owner_id` columns) with mobile-first
single-period view, entry CRUD, and mark-as-Paid capability (9.5). Balance calculator extended
with the entry-aware effective amount formula
`checking_impact = max(estimated - sum_credit, sum_debit)` for mid-period mixed
debit/credit scenarios. Parent transactions with `track_individual_purchases` cannot use
legacy Credit status (entry-level credit replaces it). Supporting document:
`phase_scope_spending_tracker.md`. See v4-6 Section 9 for full detail.

---

## Appendix B -- Deferred Items Reference

| Item                                  | Deferred From             | Notes                                                                  |
| ------------------------------------- | ------------------------- | ---------------------------------------------------------------------- |
| Scenarios (named, clone, compare)     | v3 Phase 7                | Indefinitely deferred; effort not worth reward                         |
| Paycheck calibration                  | fixes_improvements.md     | Completed as Appendix A.1 task 3.10                                    |
| Fluctuating/seasonal bills            | fixes_improvements.md     | Addressed by Section 3 task 6.1 (seasonal forecasting, planned)        |
| Multi-user / kid accounts             | v2 Phase 6                | Section 6; far future; schema ready                                    |
| Checking account APY/interest         | fixes_improvements.md     | User confirmed checking APY is negligible; not implementing            |
| Recurrence pattern audit              | Roadmap v4, task 5.2      | Removed; section 3.2 confirmed all patterns are correct                |
| Actual paycheck value entry           | Roadmap v4, task 5.3      | Removed; superseded by paycheck calibration (Appendix A.1 task 3.10)   |
| implementation_plan_section5.md       | Roadmap v4.1, Section 5   | Defunct; Section 5 completed April 2026 without this plan (a new implementation plan was written from scratch). |
| CSV export                            | v2 Phase 6                | Listed in v2 Phase 6 (Hardening & Ops) but not implemented. Moved to Section 5 task 8A.1. |
| Account Types editing                 | fixes_improvements.md     | Completed as Appendix A.4 settings UI enhancement (edit path added with metadata flags) |
| Salary button duplication             | fixes_improvements.md     | Completed: Appendix A.3 task 4.11 (`/salary/{id}/edit` fixed March 2026); `/salary` page fix completed as Appendix A.6 task 5A.3 (April 2026) |
| Grid estimated vs. actual             | fixes_improvements.md     | Completed as Appendix A.6 task 5A.1 (April 2026)                       |
| Grid transaction sort display         | fixes_improvements.md     | Completed as Appendix A.6 task 5A.2 (April 2026)                       |
| Category editing and add flow         | fixes_improvements.md     | Completed as Appendix A.6 task 5A.4 (April 2026)                       |
| CRUD deactivate/delete inconsistency  | fixes_improvements.md     | Completed as Appendix A.6 task 5A.5 (April 2026)                       |
| Charts: x-axis date format            | fixes_improvements.md     | Completed as Appendix A.9 task 8.0a                                    |
| Charts: inaccurate values             | fixes_improvements.md     | Investigated as Appendix A.9 task 8.0b; no bug found in code audit; excluded |
| Charts: total overhaul                | fixes_improvements.md     | Completed as Appendix A.9 (full visualization and reporting overhaul)  |

---

## Change Log

| Version | Date       | Changes |
| ------- | ---------- | ------- |
| 4.0     | 2026-03-24 | Post-production roadmap: added critical bug fix sprint, UX/grid overhaul phase, recurring transaction improvements phase; rescoped Phase 9 with seasonal expense forecasting; rescoped Phase 10 with tiered notification system; added multi-user as far-future placeholder; established priority ordering based on production usage feedback. |
| 4.0.1   | 2026-03-24 | Hosting updated to Arch Linux desktop with Docker/Nginx/Cloudflare Tunnel; paycheck calibration feature added as section 3.10; seasonal history data model updated with billing period dates indexed by consumption period midpoint. |
| 4.1     | 2026-03-27 | Section 3 marked complete. Section 4 expanded with production feedback (tasks 4.11-4.17). Section 5 retitled to "Debt and Account Improvements" with task 5.1 expanded to all debt types and tasks 5.2/5.3 removed. |
| 4.2     | 2026-03-30 | Sections 3A, 4, 4A, 4B marked complete. Section 5 expanded with seven new tasks (5.6-5.12) and four more (5.13-5.16). Section 6 expanded with 6.5-6.7. Section 7 notification types expanded. New Section 8 added (Dashboard, Reporting, and Data Management) with subsections 8.1-8.4. |
| 4.3     | 2026-03-31 | New Section 5A (Cleanup Sprint, five tasks). Section 8 retitled to "Visualization and Reporting Overhaul" with chart bug fixes 8.0a/8.0b prerequisite. Task 8.4 separated into Section 8A (Data Export). Phase ordering updated. |
| 4.4     | 2026-04-07 | Sections 5A and 5 marked complete. Mobile responsiveness added as completed unplanned work. Section 8 marked in progress; priority moved 6 -> 4. Five new tasks added to Phase 9: 6.8-6.12. |
| 4.5     | 2026-04-07 | Section 7 fully rescoped: notification types expanded from 7 to 15 across 6 named groups; data model expanded with explicit columns for all configurable parameters; new infrastructure subsections (snooze, auto-resolve, persist dashboard alerts); in-app delivery split into bell/dropdown and full page; settings UI Option B grouped expandable sections; email delivery expanded with delivery window and batching. |
| 4.6     | 2026-04-09 | New Section 9 added (Spending Tracker and Companion View): sub-transaction entry tracking, entry-level credit card workflow, companion user role, balance calculator extended with entry-aware effective amount. Multi-user (Section 10) bumped from Priority 8 to Priority 9. Sections 10-12 renumbered. |
| 5.0     | 2026-05-06 | Major reorganisation. Numbering: collapsed the dual Priority/Section system into a single sequential top-level track in execution order; subsection labels (6.x, 7.x, 8A.x) preserved as historical tags within carried-forward sections for continuity with prior commits and design docs. Completions: marked Visualization and Reporting Overhaul (was Priority 4 / Section 8, now Appendix A.9) and Spending Tracker and Companion View (was Priority 8 / Section 9, now Appendix A.10) complete (May 2026). Completed work relocated to Appendix A with original section labels preserved. New Section 1 (Security Remediation) added, linking to `docs/audits/security-2026-04-15/remediation-plan.md` (in progress, 16 of 56 commits merged). New Section 2 (Financial Calculation Consistency) added with three sequenced stages (Stage A committed; Stages B and C decision-pending). Execution order for remaining work: Security, Financial Consistency, Smart Features, Notifications, Data Export, Multi-User. Phase 10 (was Section 7) became Section 4. Section 8A (Data Export) became Section 5. Section 10 (Multi-User) became Section 6. Section 11 (Deferred Items Reference) became Appendix B. Supersedes `project_roadmap_v4-6.md` (preserved as historical archive). |
