# Phase 0 Audit -- Double-Entry Migration

**Date:** 2026-05-08
**Branch:** claude/migrate-double-entry-accounting-CdbAQ
**Audit type:** Read-only. No code changes.
**Auditor read directly (not delegated):** every file referenced below.

---

## 1. Purpose and method

Phase 0 of the double-entry accounting migration. The design phase produced
architectural decisions about postings, balanced triggers, header/leg split,
loan-payment shape, paycheck shape, and so on. Before drafting migration
files we need to ground those decisions in the real codebase. This document
records what the codebase actually does, where the design assumptions hold,
and where they break.

Method: read every file directly with the Read tool. No subagent
summaries. Notes are organised by layer (data, services, routes, schemas,
tests, infrastructure). The findings section at the top calls out
everything the migration plan needs to revisit before Phase 1.

---

## 2. Critical findings -- design gaps that must be resolved before Phase 1

These are the items where the design phase made an assumption the codebase
does not support. Each one would silently corrupt the migration if carried
forward as drafted.

### 2.1 `transaction_entries` is an envelope/ledger feature, not double-entry legs

**The design said:** drop `budget.transaction_entries` -- it represents legs
of a double-entry transaction and is replaced by `budget.postings`.

**Reality:** `TransactionEntry` is a per-purchase ledger against an envelope
budget. The parent transaction is the envelope ("Groceries: $400/period");
each entry is one purchase against it ("Kroger $52, 2026-05-04",
"Costco $87, 2026-05-05"). Entries carry their own `is_credit`, `is_cleared`,
and `credit_payback_id` columns and participate independently in the balance
calculator's `_entry_aware_amount` formula.

**Files that show this:**

- `app/models/transaction_entry.py` (full file).
- `app/models/transaction_template.py:67` -- `is_envelope` flag controls whether
  a template's transactions act as envelopes.
- `app/services/entry_service.py` (all 590 lines) -- CRUD, cleared toggle,
  entry-sum settlement, anchor true-up clear-all, companion routing.
- `app/services/entry_credit_workflow.py` (full file) -- aggregated CC payback
  driven by `sum(credit entries)`.
- `app/services/balance_calculator.py:292-386` -- the `_entry_aware_amount`
  three-bucket formula:
  `max(estimated - cleared_debit - sum_credit, uncleared_debit)`.
- `app/routes/entries.py` (full file) -- HTMX entry-list partial,
  `version_id` stale-form check, parameter-confusion guard, idempotent CC
  payback unique-index handling.
- `app/services/year_end_summary_service.py:482-531` -- entry data feeds the
  year-end aggregation.
- `app/audit_infrastructure.py:74` -- `transaction_entries` is in the audited
  table list.

**Consequence:** the migration plan must keep `budget.transaction_entries`.
Postings represent the balance equation; entries represent purchase logging
on envelope-tracked transactions. They live alongside each other. Removing
entries deletes a real, used feature.

### 2.2 The status workflow has 6 statuses, not 3

**The design said:** simplify to `projected | settled | cancelled`.

**Reality:** the workflow has `projected | done | received | credit |
cancelled | settled`, with three boolean flags on the `ref.statuses` row
that drive engine behaviour:

- `is_settled` -- the transaction has happened; balance calculator uses
  `actual_amount` for these.
- `is_immutable` -- the recurrence engine must not overwrite this row.
- `excludes_from_balance` -- contributes zero to the projected balance
  (Cancelled, Credit).

Allowed transitions (from `app/services/state_machine.py:_build_transitions`):

```
projected -> projected | done | received | credit | cancelled
done      -> done | projected | settled
received  -> received | projected | settled
credit    -> credit | projected         (unmark goes through credit_workflow)
cancelled -> cancelled | projected
settled   -> settled                    (terminal)
```

**Consequence:** "simplify to 3 statuses" means redesigning the state
machine, the credit workflow, the carry-forward branches, the year-end
service, and ~12 places that branch on `is_settled` / `is_immutable` /
`excludes_from_balance`. Out of scope for the postings migration. The
design must keep the existing statuses or commit to a separate, scoped
state-machine simplification before Phase 1.

### 2.3 Loan payment principal/interest split happens at READ time, not at write

**The design said:** loan payments are 3 legs at write time:
`-cash`, `+liability principal`, `+interest expense`.

**Reality:** today's loan payment is one transfer with two cash-side
shadow transactions. The principal/interest split is computed
on-demand in the balance calculator's amortization variant
(`balance_calculator.py:248-289`) and in the loan dashboard via
`amortization_engine.generate_schedule()`. Nothing is stored. As the
principal decreases, the same payment splits differently next month.

**Files that show this:**

- `app/services/balance_calculator.py:176-289` --
  `calculate_balances_with_amortization`. Detects payments via shadow
  income transactions on the loan account, computes interest from
  `running_principal * monthly_rate`, treats the rest as principal.
- `app/services/loan_payment_service.py:156-230` -- queries shadow
  income transactions, returns `PaymentRecord` instances for the engine.
- `app/services/amortization_engine.py:339-619` -- the engine takes a
  list of payments and re-replays the schedule month by month, applying
  rate changes and ARM anchoring.

**Consequence (decision needed):** the migration must choose between

- **Option A:** pre-split at write time. Postings are
  `-$1500 cash / +$X liability / +$Y interest expense`. Requires
  regenerating postings on every loan-rate change, ARM adjustment,
  principal-anchor edit, and template amount change. Means the recurrence
  engine has to call into the amortization engine when generating loan
  payments.
- **Option B:** keep 2-leg postings (`-cash / +liability`) and continue
  to compute the split at read time. Postings still balance. Interest
  expense is invisible to category-based reporting until a separate
  reconciliation pass writes it.
- **Option C:** 2 legs at write, periodic rebalancing job that emits a
  correction pair (`-liability +interest expense`) for each historical
  payment. Most accounting-system-like. Most complex.

The design picked Option A implicitly. Option A is the right answer if we
accept the regeneration cost. The cost needs to be acknowledged
explicitly before Phase 1.

### 2.4 Paycheck breakdown is computed but NOT stored

**The design said:** paycheck = N postings (cash, gross income, federal
tax, state tax, SS, medicare, deductions) at write time.

**Reality:** the recurrence engine writes a single Transaction with
`estimated_amount = breakdown.net_pay`. The breakdown is computed by
`paycheck_calculator.calculate_paycheck()` (a pure function) at the
moment of generation and discarded after the amount is captured.

**Files that show this:**

- `app/services/paycheck_calculator.py:61-205` --
  `calculate_paycheck` returns a `PaycheckBreakdown` dataclass with
  pre/post-tax deductions, federal/state/SS/medicare, gross, net.
- `app/services/recurrence_engine.py:721-776` -- recurrence engine
  calls the calculator only to extract `breakdown.net_pay`.
- Deductions can have `target_account_id`. When a 401k deduction has
  `target_account_id` pointing to the 401k account, today nothing
  reflects that flow on the 401k side -- the user has to manually add
  it as an investment contribution.

**Consequence (decision needed):** if we want paycheck postings (gross
income, taxes, deductions to target accounts), we have to

- regenerate postings every time the salary profile changes (new
  raise, new deduction, calibration override applied);
- decide whether historical paychecks freeze their breakdown or
  re-compute on profile edit;
- decide whether existing transactions migrate to N postings via
  backfill (replaying the paycheck calculator against each historical
  pay period using the salary profile state at that time).

The historical replay is hard. The salary profile may have changed
several times since older paychecks were generated. The honest answer
is to accept that backfilled paychecks will use the current breakdown,
not the one in effect at the original write time.

### 2.5 `scenario_id` is NOT NULL on every transaction

**The design said:** drop the `scenario_id` column from postings; defer
scenarios; add it back when scenarios ship.

**Reality:** `scenario_id` is `NOT NULL` on `transactions`, `transfers`,
`salary_profiles`, and several other tables. The Scenario model is
fully wired up with `is_baseline` flag. In practice every user has one
scenario (the baseline) and it functions as a tenancy column.

**Files that show this:**

- `app/models/transaction.py:141-145` -- `scenario_id` NOT NULL FK.
- `app/models/scenario.py` (full file) -- `is_baseline`, `cloned_from_id`.
- Every route that creates a transaction looks up the baseline scenario
  and writes its id (e.g. `app/routes/grid.py:176-181`).

**Consequence:** "drop scenario_id from postings" means the postings table
diverges from the header in tenancy semantics, and the structural-validation
trigger's denorm-consistency check loses one of its anchors. Two options:

- **Keep `scenario_id` on postings as a denormalised column matching the
  header.** Aligns with current convention, costs trivially. The user's
  Rule 13 argument applies less strongly because the column is already
  in active use system-wide.
- **Drop `scenario_id` from postings** as designed, accept the
  denorm-consistency check covers `user_id` / `is_deleted` only, plan to
  add it when scenarios ship.

The design picked Drop. I recommend revisiting -- this is more like
"keep scenario_id as a denormed tenancy column" than "speculative future
feature."

### 2.6 Categories are flat (group, item), not hierarchical

**The design said:** sidecar Category table with `parent_id`.

**Reality:** `app/models/category.py` -- two columns, `group_name` and
`item_name`. Display label is `f"{group}: {item}"`. Unique constraint on
`(user_id, group_name, item_name)`. No hierarchy.

**Consequence:** trivial. Update the design to match (no `parent_id`).

### 2.7 The recurrence engine produces ONE Transaction per period, not N postings

**The design said:** the recurrence engine emits the canonical row plus its
postings.

**Reality:** `recurrence_engine.generate_for_template` (lines 51-172) emits
one Transaction. Period skip rules respect existing entries (override,
deleted, immutable). The path that would emit postings is not present.

**Consequence:** the recurrence engine is one of the highest-traffic write
paths and one of the most security-audited (cross-user defense, IDOR
checks, conflict resolution). Adding posting emission means either

- modifying `generate_for_template` to call `app/services/postings.py`
  builders inline, OR
- adding a post-flush hook (DB trigger or session event) that emits
  postings from the just-flushed Transaction.

Inline is cleaner. Both will need careful review against the existing
shadow-mutation guards in `resolve_conflicts` (lines 404-427).

### 2.8 The paycheck path through the recurrence engine is special

When a `transaction_template` is linked to a `salary_profile` (via
`salary_profile.template_id`), the recurrence engine calls
`paycheck_calculator.calculate_paycheck` to derive the amount instead of
using `template.default_amount` (`recurrence_engine.py:721-776`). For
posting emission, this means a salary-linked template emits N postings
(gross income, taxes, deductions); a non-salary template emits 2 postings
(cash + expense or cash + income).

**Consequence:** the postings builder for paychecks needs to take the
`PaycheckBreakdown` directly, not just the net amount. Builder signature
likely:
`build_paycheck_postings(tx, cash_account, breakdown, account_resolver)`,
where `account_resolver` maps deduction names to target accounts.

### 2.9 The transfer service is the chokepoint -- carefully replicate its semantics

`transfer_service.py` enforces five invariants (CLAUDE.md "Transfer
Invariants" 1-5). The postings approach removes the parent-transfer
record but inherits the same invariants in posting form:

- Every transaction has at least 2 postings, sum = 0 (existing balanced
  check).
- A "transfer-shaped" transaction has exactly 2 cash-kind postings on
  different accounts with opposite signs. (This is structurally
  equivalent to the shadow-pair invariant.)
- Postings can never be soft-deleted independently of the parent
  transaction (denorm-consistency check).

Service semantics to preserve:

- `restore_transfer` corrects shadow drift on undelete (amount, status,
  period). Postings restore should similarly verify and correct.
- `delete_transfer(soft=True)` explicitly soft-deletes shadows; CASCADE
  fires only on hard delete. Postings soft-delete via the
  `is_deleted` denorm column on every leg.
- `update_transfer` sets `paid_at` to `now()` automatically when
  transitioning to a settled status if the caller did not pass one
  explicitly. The postings-equivalent `update_transaction` already does
  this for non-shadow rows; the postings service should match.

---

## 3. Current architecture (what's actually there)

### 3.1 Schemas and tables

**PostgreSQL schemas:**

- `ref` -- 13 lookup tables: `account_type_categories`, `account_types`,
  `transaction_types`, `statuses`, `recurrence_patterns`, `filing_statuses`,
  `deduction_timings`, `calc_methods`, `tax_types`, `raise_types`,
  `goal_modes`, `income_units`, `user_roles`.
- `auth` -- `users`, `mfa_configs`, `user_settings`.
- `budget` -- 16 tables: `accounts`, `account_anchor_history`, `categories`,
  `escrow_components`, `interest_params`, `investment_params`, `loan_params`,
  `pay_periods`, `rate_history`, `recurrence_rules`, `savings_goals`,
  `scenarios`, `transaction_entries`, `transaction_templates`,
  `transactions`, `transfer_templates`, `transfers`.
- `salary` -- 10 tables: `calibration_deduction_overrides`,
  `calibration_overrides`, `fica_configs`, `paycheck_deductions`,
  `pension_profiles`, `salary_profiles`, `salary_raises`,
  `state_tax_configs`, `tax_bracket_sets`, `tax_brackets`.
- `system` -- `audit_log`.

**Audit triggers:** 28 audited tables. `EXPECTED_TRIGGER_COUNT` derived
from `len(AUDITED_TABLES)`. Entrypoint health check refuses to start
Gunicorn if the trigger count is short.

### 3.2 Models in scope (column-level, abridged)

**`budget.transactions` (Transaction):**

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| account_id | int NOT NULL | FK budget.accounts ON DELETE RESTRICT |
| template_id | int NULL | FK budget.transaction_templates ON DELETE SET NULL |
| pay_period_id | int NOT NULL | FK budget.pay_periods ON DELETE CASCADE |
| scenario_id | int NOT NULL | FK budget.scenarios ON DELETE CASCADE |
| status_id | int NOT NULL | FK ref.statuses ON DELETE RESTRICT |
| name | varchar(200) NOT NULL | |
| category_id | int NULL | FK budget.categories ON DELETE SET NULL |
| transaction_type_id | int NOT NULL | FK ref.transaction_types ON DELETE RESTRICT |
| estimated_amount | numeric(12,2) NOT NULL | CHECK >= 0 |
| actual_amount | numeric(12,2) NULL | CHECK NULL OR >= 0 |
| is_override | bool NOT NULL DEFAULT false | |
| is_deleted | bool NOT NULL DEFAULT false | |
| transfer_id | int NULL | FK budget.transfers ON DELETE CASCADE (shadow link) |
| credit_payback_for_id | int NULL | self-FK ON DELETE SET NULL |
| notes | text NULL | |
| due_date | date NULL | |
| paid_at | timestamptz NULL | |
| version_id | int NOT NULL DEFAULT 1 | optimistic locking |
| created_at, updated_at | timestamptz | TimestampMixin |

Indexes (from `__table_args__`):

- `idx_transactions_period_scenario` on `(pay_period_id, scenario_id)`.
- `idx_transactions_template` on `template_id`.
- `idx_transactions_credit_payback` on `credit_payback_for_id`.
- `uq_transactions_credit_payback_unique` partial unique on
  `credit_payback_for_id` WHERE `credit_payback_for_id IS NOT NULL AND
  is_deleted = FALSE`.
- `idx_transactions_account` on `account_id`.
- `idx_transactions_transfer` partial on `transfer_id` WHERE
  `transfer_id IS NOT NULL`.
- `uq_transactions_transfer_type_active` partial unique on
  `(transfer_id, transaction_type_id)` WHERE `transfer_id IS NOT NULL
  AND is_deleted = FALSE`. Backstop for Transfer Invariant 1.
- `idx_transactions_due_date` partial on `due_date`.
- `idx_transactions_template_period_scenario` partial unique on
  `(template_id, pay_period_id, scenario_id)` WHERE `template_id IS NOT
  NULL AND is_deleted = FALSE AND is_override = FALSE`.

CHECK constraints: `estimated_amount >= 0`, `actual_amount IS NULL OR
actual_amount >= 0`, `version_id > 0`.

`__mapper_args__ = {"version_id_col": version_id}`.

Relationships: account, template, pay_period, scenario, status,
category, transaction_type, transfer (with `shadow_transactions`
backref), credit_payback_for (self), entries (one-to-many,
`cascade="all, delete-orphan"`, `order_by=entry_date`).

Properties: `effective_amount`, `is_income`, `is_expense`,
`days_until_due`, `days_paid_before_due`.

**`budget.transfers` (Transfer):**

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| user_id | int NOT NULL | FK auth.users ON DELETE CASCADE |
| from_account_id | int NOT NULL | FK budget.accounts ON DELETE RESTRICT |
| to_account_id | int NOT NULL | FK budget.accounts ON DELETE RESTRICT |
| pay_period_id | int NOT NULL | FK budget.pay_periods ON DELETE RESTRICT |
| scenario_id | int NOT NULL | FK budget.scenarios ON DELETE CASCADE |
| status_id | int NOT NULL | FK ref.statuses ON DELETE RESTRICT |
| transfer_template_id | int NULL | FK budget.transfer_templates ON DELETE SET NULL |
| name | varchar(200) NULL | |
| amount | numeric(12,2) NOT NULL | CHECK > 0 |
| is_override | bool NOT NULL DEFAULT false | |
| is_deleted | bool NOT NULL DEFAULT false | |
| category_id | int NULL | FK budget.categories ON DELETE SET NULL |
| notes | text NULL | |
| version_id | int NOT NULL DEFAULT 1 | |
| created_at, updated_at | timestamptz | |

CHECK: `from_account_id != to_account_id`, `amount > 0`,
`version_id > 0`.

Indexes: `idx_transfers_period_scenario`,
`idx_transfers_template_period_scenario` (partial unique),
`uq_transfers_adhoc_dedupe` (partial unique double-submit guard).

Properties: `effective_amount`.

**`budget.transaction_entries` (TransactionEntry):**

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | |
| transaction_id | int NOT NULL | FK budget.transactions ON DELETE CASCADE |
| user_id | int NOT NULL | FK auth.users ON DELETE CASCADE |
| amount | numeric(12,2) NOT NULL | CHECK > 0 |
| description | varchar(200) NOT NULL | |
| entry_date | date NOT NULL DEFAULT CURRENT_DATE | |
| is_credit | bool NOT NULL DEFAULT false | |
| is_cleared | bool NOT NULL DEFAULT false | |
| credit_payback_id | int NULL | FK budget.transactions ON DELETE SET NULL |
| version_id | int NOT NULL DEFAULT 1 | |
| created_at, updated_at | timestamptz | |

Indexes: `idx_transaction_entries_txn_id`,
`idx_transaction_entries_txn_credit`.

**`budget.accounts` (Account):**

- `current_anchor_balance numeric(12,2) NULL` and
  `current_anchor_period_id int NULL` -- the anchor lives on the account
  row (matches Option A1 from design).
- `account_type_id` FK to `ref.account_types`.
- `is_active`, `sort_order`, `version_id`.

`AccountAnchorHistory` is the audit trail of true-ups. Carries a
functional unique index on `(account_id, pay_period_id, anchor_balance,
((created_at AT TIME ZONE 'UTC')::date))` for same-day duplicate
prevention.

**`ref.account_types` (AccountType):** rich behavioural flags --
`has_parameters`, `has_amortization`, `has_interest`, `is_pretax`,
`is_liquid`. Drives engine selection (loan vs interest vs simple).

**`ref.statuses` (Status):** boolean flags `is_settled`, `is_immutable`,
`excludes_from_balance` (see Finding 2.2).

**Salary domain:** `SalaryProfile` (with W-4 fields and per-scenario
linkage), `SalaryRaise` (one-time and recurring), `PaycheckDeduction`
(pre/post-tax, percentage/flat, inflation, `target_account_id`),
`PensionProfile`, `TaxBracketSet`, `TaxBracket`, `StateTaxConfig`,
`FicaConfig`, `CalibrationOverride` (effective rates from real pay stub),
`CalibrationDeductionOverride`.

**Loan-side parameters:** `LoanParams` (1:1 with Account), `RateHistory`,
`EscrowComponent`, `InterestParams`, `InvestmentParams`.

**Other:** `RecurrenceRule` (8 patterns), `Scenario`, `PayPeriod`,
`Category`, `SavingsGoal`.

### 3.3 Services (write paths and their characteristics)

| Service | Lines | Role |
|---------|-------|------|
| `balance_calculator.py` | 452 | Pure function. Three variants: base, with_interest, with_amortization. Reads only `Transaction`. Uses `_entry_aware_amount` for envelope expenses. |
| `recurrence_engine.py` | 777 | Generates `Transaction` rows from `TransactionTemplate`. Salary-linked templates use `paycheck_calculator`. Conflict resolution path with shadow-mutation guard. |
| `transfer_service.py` | 849 | Chokepoint for transfer mutations. `create/update/delete/restore_transfer`. Enforces all five transfer invariants. Verifies state transitions via `state_machine.verify_transition`. |
| `transfer_recurrence.py` | 322 | Generates `Transfer` rows. Delegates create to `transfer_service.create_transfer`. |
| `transaction_service.py` | 169 | `settle_from_entries` -- shared by manual mark-done and carry-forward envelope branch. |
| `entry_service.py` | 590 | `create_entry`, `update_entry`, `delete_entry`, `toggle_cleared`, `clear_entries_for_anchor_true_up`. Companion-aware via `resolve_owner_id`. Triggers `sync_entry_payback`. |
| `entry_credit_workflow.py` | 237 | Aggregated CC payback driven by sum(credit entries). 2x2 state matrix. Row lock via `lock_source_transaction_for_payback`. |
| `credit_workflow.py` | 371 | Per-transaction credit (legacy). `mark_as_credit`, `unmark_credit`. Same row-lock helper. |
| `state_machine.py` | 172 | `verify_transition`. Identity transitions always legal. Settled is terminal. |
| `paycheck_calculator.py` | 463 | Pure function. Returns `PaycheckBreakdown` with N deduction lines. Calibration override path. |
| `tax_calculator.py` | 350+ | Pure function. Pub 15-T federal withholding, state, FICA. |
| `amortization_engine.py` | 992 | Pure function. `generate_schedule` with payments + rate-changes + ARM anchor support. |
| `loan_payment_service.py` | 354 | Queries shadow income transactions on a loan account, returns `PaymentRecord` list. Escrow subtraction + biweekly redistribution. |
| `escrow_calculator.py` | 116 | Pure function. Monthly escrow + projection. |
| `interest_projection.py` | 74 | Pure function. APY + compounding -> period interest. |
| `carry_forward_service.py` | 1017 | Three-branch (envelope/discrete/transfer). `_build_carry_forward_context` shared between mutating and preview paths. Bulk UPDATE with race-protection WHERE clause. |
| `pay_period_service.py` | 177 | `generate_pay_periods`, `get_current_period`, `get_next_period`. |
| `account_resolver.py` | 61 | Grid account fallback chain. |
| `dashboard_service.py` | 600+ | Read-only dashboard aggregates. |
| `year_end_summary_service.py` | 1700+ | Year-end aggregation. Queries `Transfer` directly (line 657) AND aggregates `TransactionEntry` (line 482-531). |
| `chart_data_service.py` | -- | (not present in current tree -- the inventory document referenced this; service may have been refactored or merged). |
| `dashboard_service` and friends | -- | Also touch `transfer_id` for "is_transfer" display flag. |

Common service contract: no Flask imports, takes plain data, returns ORM
objects, flushes but does NOT commit. Caller owns the transaction
boundary. Optimistic locking via `version_id_col` on every mutable model.

### 3.4 Routes (write paths)

**Files in scope:**

| File | Size | Notes |
|------|------|-------|
| `routes/transactions.py` | 42 KB | All transaction CRUD. Transfer detection guard on every mutation handler. Stale-form check via `version_id`. State-machine verification. Idempotent CC-payback handling. |
| `routes/transfers.py` | 54 KB | Transfer CRUD and template management. Routes through `transfer_service`. |
| `routes/entries.py` | 15 KB | Entry CRUD. Companion-aware. Stale-form + idempotent CC-payback handling. |
| `routes/grid.py` | 17 KB | Read-only grid view. Account-scoped. Eager-loads entries + template. Computes per-period subtotals via Decimal. |
| `routes/accounts.py` | 49 KB | Anchor true-up, archival, account params (loan, interest, investment), escrow, rate history. Queries `Transfer` for active-template guard on deactivation. |
| `routes/templates.py` | 29 KB | Transaction template CRUD. Calls recurrence engine. |
| `routes/salary.py` | 55 KB | Salary profile CRUD, raises, deductions, calibration, tax configs. |
| `routes/loan.py` | 47 KB | Loan dashboard, payoff calculator, ARM rate history, escrow components. Calls `loan_payment_service.load_loan_context`. |
| `routes/debt_strategy.py` | 19 KB | Avalanche/snowball debt payoff strategies. |
| `routes/investment.py`, `routes/retirement.py`, `routes/savings.py`, `routes/analytics.py`, `routes/dashboard.py`, `routes/companion.py`, `routes/obligations.py` | various | Mostly read paths; some touch `transfer_id` for display flags. |

Auth helpers in `app/utils/auth_helpers.py` enforce ownership via
`require_owner` decorator and `_get_owned_transaction` patterns.

### 3.5 Schema layer

`app/schemas/validation.py` is 99 KB, ~50 schema classes. Schemas relevant
to migration scope:

- Transaction: `TransactionUpdateSchema`, `TransactionCreateSchema`,
  `InlineTransactionCreateSchema`, `MarkDoneSchema`.
- Template: `TemplateCreateSchema`, `TemplateUpdateSchema`.
- Transfer: `TransferTemplateCreateSchema`,
  `TransferTemplateUpdateSchema`, `TransferCreateSchema`,
  `TransferUpdateSchema`.
- Entry: `EntryCreateSchema`, `EntryUpdateSchema`.
- Anchor: `AnchorUpdateSchema`.
- Account: `AccountCreateSchema`, `AccountUpdateSchema`,
  `AccountTypeCreateSchema`, `AccountTypeUpdateSchema`.
- Loan: `LoanParamsCreateSchema`, `LoanParamsUpdateSchema`,
  `RateChangeSchema`, `EscrowComponentSchema`, `PayoffCalculatorSchema`,
  `RefinanceSchema`, `LoanPaymentTransferSchema`.
- Investment: `InvestmentContributionTransferSchema`,
  `InvestmentParamsCreateSchema`, `InvestmentParamsUpdateSchema`.
- Interest: `InterestParamsCreateSchema`,
  `InterestParamsUpdateSchema`.
- Calibration: `CalibrationSchema`, `CalibrationConfirmSchema`.
- Salary: `SalaryProfileCreateSchema`, `SalaryProfileUpdateSchema`,
  `RaiseCreateSchema`, `RaiseUpdateSchema`,
  `DeductionCreateSchema`, `DeductionUpdateSchema`,
  `TaxBracketSetSchema`, `FicaConfigSchema`, `StateTaxConfigSchema`.
- Pay period: `PayPeriodGenerateSchema`.
- Category: `CategoryCreateSchema`, `CategoryEditSchema`.
- Savings goal: `SavingsGoalCreateSchema`, `SavingsGoalUpdateSchema`.
- Pension: `PensionProfileCreateSchema`,
  `PensionProfileUpdateSchema`.
- User settings: `UserSettingsSchema`, `RetirementSettingsSchema`.
- Other: `BaseSchema`, `DebtStrategyCalculateSchema`.

### 3.6 Tests

**Total:** 4357 tests across 134 test files. Approximate per-category:

| Directory | Files | Lines |
|-----------|-------|-------|
| `test_routes/` | 47 | 50,017 |
| `test_services/` | 43 | 45,407 |
| `test_integration/` | 9 | 4,935 |
| `test_adversarial/` | 8 | 5,095 |
| `test_models/` | 6 | 2,478 |
| `test_schemas/` | 4 | 2,446 |
| `test_scripts/` | 7 | 2,195 |
| `test_utils/` | 6 | 2,815 |
| `test_concurrent/` | 1 | 489 |
| `test_performance/` | 1 | 457 |
| Top-level | 4 | 2,275 |
| **Total** | **134** | **118,609** |

A historical baseline file `docs/baseline_test_results.txt` records 1780
passing tests in 6:32. That snapshot predates the C-* security audits and
is now stale. The current 4357 tests / ~12-13 minute runtime per CLAUDE.md
is correct.

**Tests that depend on shadow-row mechanics specifically (subset of
preservation work):**

- `tests/test_services/test_transfer_service.py` -- create / update /
  delete / restore + invariant tests.
- `tests/test_services/test_transfer_recurrence.py` -- generation,
  conflict resolution.
- `tests/test_services/test_balance_calculator.py` -- base + transfer
  cases.
- `tests/test_services/test_balance_calculator_debt.py` -- amortization
  tests with shadow income transactions.
- `tests/test_services/test_balance_calculator_hysa.py` -- interest +
  shadow tests.
- `tests/test_services/test_balance_calculator_entries.py` -- entry-aware
  formula tests.
- `tests/test_services/test_carry_forward_service.py` -- envelope /
  discrete / transfer branches.
- `tests/test_services/test_credit_workflow.py` and
  `test_entry_service.py` -- payback creation tests.
- `tests/test_routes/test_transfers.py` -- transfer route IDOR,
  validation, state transitions.
- `tests/test_routes/test_transaction_guards.py` -- transfer detection
  guards on transaction routes.
- `tests/test_routes/test_optimistic_locking_c18.py` -- version_id
  contracts.
- `tests/test_models/test_transfer_shadow_schema.py` -- shadow
  uniqueness invariant tests.
- `tests/test_integration/test_workflows.py` -- end-to-end transfer
  flows.
- `tests/test_audit_fixes.py::TestBalanceWithTransfers` -- balance
  effect tests.

### 3.7 Migration history (Alembic)

55 migration files. Migrations directly relevant to the postings work:

- `efffcf647644_add_account_id_column_to_transactions.py` -- adds
  `account_id` NOT NULL.
- `772043eee094_add_transfer_id_to_transactions_and_.py` -- adds
  `transfer_id` nullable FK CASCADE; adds `category_id` to transfers
  and transfer_templates.
- `c21a1f0b8e74_add_partial_unique_index_for_transfer_.py` -- partial
  unique on `(transfer_id, transaction_type_id)` for shadow uniqueness.
- `b961beb0edf6_add_entry_tracking_and_companion_support.py` --
  `transaction_entries` table, `is_envelope` and `companion_visible`
  columns on templates, `user_roles` ref table, `linked_owner_id`
  on auth.users.
- `c7e3a2f9b104_add_is_cleared_to_transaction_entries.py` -- envelope
  reconciliation flag.
- `e138e6f55bf0_add_boolean_columns_to_ref_statuses_and_.py` --
  `is_settled`, `is_immutable`, `excludes_from_balance`.
- `07198f0d6716_add_cancelled_status.py`.
- `415c517cf4a4_add_account_type_categories_booleans_.py` -- the
  `has_parameters`, `has_amortization`, `has_interest`, `is_pretax`,
  `is_liquid` flags.
- `f1a2b3c4d5e6_add_hysa_and_account_categories.py`.
- `c67773dc7375_unify_loan_params_into_single_table_.py`.
- `a1b2c3d4e5f6_add_debt_account_tables.py`.
- `dc46e02d15b4_add_check_constraints_to_loan_params_.py`.
- `861a48e11960_add_version_id_to_accounts_for_.py` -- and analogous
  per-table version_id migrations from C-17 / C-18.

The audit infrastructure rebuild migration
(`a5be2a99ea14_rebuild_audit_infrastructure.py`) materialises the trigger
function and per-table triggers. New tables (`postings`,
`recurring_template_legs`, etc.) require:

1. Adding the entry to `app/audit_infrastructure.py:AUDITED_TABLES`
   alphabetically.
2. Re-running `flask db upgrade` to pick up the trigger via the rebuild
   migration's idempotent CREATE TRIGGER.
3. The entrypoint health check then passes because `EXPECTED_TRIGGER_COUNT`
   is `len(AUDITED_TABLES)`.

### 3.8 Audit infrastructure

`app/audit_infrastructure.py` is the single source of truth for
`AUDITED_TABLES` and `EXPECTED_TRIGGER_COUNT`. Three callers
(rebuild migration, `init_database.py`, `tests/conftest.py`) must produce
identical infrastructure. Centralising the SQL in this module is what
keeps them in lockstep.

The runtime `shekel_app` role has USAGE/SELECT/INSERT on `system.audit_log`
but cannot drop or replace audit triggers (least-privilege role policy
from C-13).

---

## 4. Impact map: where shadow detection / TransactionEntry / Transfer are used today

### 4.1 `Transaction.transfer_id IS NOT NULL` checks (shadow-aware code)

- `app/services/balance_calculator.py:268` -- amortization variant
  detects loan payments.
- `app/services/loan_payment_service.py:205` -- payment history query
  filters on shadow income.
- `app/services/entry_service.py:158` -- create_entry refuses on
  shadow rows.
- `app/services/dashboard_service.py:195` -- "is_transfer" flag for
  display.
- `app/services/carry_forward_service.py:273, 451-468` -- partition
  source rows; route shadow updates through transfer_service.
- `app/services/recurrence_engine.py:413-427` -- shadow-mutation
  refusal in `resolve_conflicts`.
- `app/services/investment_projection.py:10, 116, 227` -- contributions
  derived from shadow income transactions.
- `app/routes/transactions.py:237, 315, 467, 581, 615, 658, 985` --
  transfer detection guards on every mutation handler.

### 4.2 Direct `Transfer` queries (would be removed in postings world)

- `app/services/year_end_summary_service.py:657-664` -- aggregates
  transfers separately. Will need rework to query
  `Transaction` filtered to transfer-shaped postings.
- `app/services/transfer_recurrence.py:171, 310` -- regenerate logic.
- `app/utils/archive_helpers.py:57-60` -- archive guard.
- `app/routes/accounts.py:549-552` -- account deactivation guard.
- `app/routes/transfers.py:505-689` -- the transfer routes themselves.

### 4.3 `TransactionEntry` callsites (preserved -- envelope feature)

- `app/services/entry_service.py` -- core CRUD.
- `app/services/entry_credit_workflow.py` -- aggregated CC payback.
- `app/services/credit_workflow.py:64` -- comment about FK validation
  conflict.
- `app/services/year_end_summary_service.py:482-531` -- entry-level
  aggregation in year-end summary.
- `app/models/transaction.py:211-216` -- `entries` relationship with
  `cascade="all, delete-orphan"`.
- `app/models/__init__.py:38` -- registry import.
- `app/audit_infrastructure.py:74` -- audited table.
- `app/routes/entries.py` -- HTTP layer.

---

## 5. Open design questions to resolve before Phase 1

These are decisions the design phase did not resolve, surfaced by the
codebase reading:

1. **Loan-payment posting shape (Option A / B / C from Finding 2.3).**
   Recommend Option A (3 legs at write time) with explicit acknowledgement
   of the regeneration cost on rate / principal / template changes.
2. **Paycheck posting persistence (Finding 2.4).** Lock in: do paycheck
   postings freeze or re-compute on salary profile edit? Recommend
   freeze, regenerate only when the parent transaction is regenerated by
   the recurrence engine.
3. **`scenario_id` on postings (Finding 2.5).** Recommend keep as
   denormed column; the column is in active use as a tenancy key, not a
   speculative future feature.
4. **Status simplification (Finding 2.2).** Recommend keep all 6
   statuses. Simplification is an independent project, not part of
   double-entry.
5. **Backfill policy for paycheck breakdowns of historical
   transactions.** Recommend backfilled paychecks use *current* salary
   profile state (latest breakdown logic). Document this -- historical
   backfill of complete time-of-write breakdowns is impractical.
6. **Companion-user posting visibility.** Companions today see their
   linked owner's data through `companion_visible` template flag.
   Postings inherit this via `pay_period -> user_id` chain. No new
   policy needed, but tests must cover companion access to envelope
   transactions in the postings era.
7. **Year-end summary's direct Transfer query.** Replace with a posting
   query, or accept that year-end runs against the legacy table during
   the dual-write window.

---

## 6. Adjustments to the migration plan from Phase 0 findings

Updated estimates and scope changes vs. the draft Phase 0+ plan:

- **Phase 1 schema additions:** `budget.postings` only. Do NOT add a
  separate `budget.categories` sidecar (current categories are already
  flat). Do NOT remove `transaction_entries`.
- **Phase 1 trigger:** structural validation trigger checks
  (a) sum=0, (b) >= 2 postings, (c) denorm consistency on `user_id` /
  `is_deleted` (and `scenario_id` if we keep it on postings).
- **Phase 2 service module:** `app/services/postings.py` exposes
  builders. Builder for paychecks takes a `PaycheckBreakdown`; builder
  for loan payments takes the principal-interest split derived from a
  call into `amortization_engine`.
- **Phase 3 dual-write:** integrate posting emission into
  `transfer_service.create_transfer`, `recurrence_engine.generate_for_template`,
  `transfer_recurrence.generate_for_template`, the manual transaction
  creation routes (`create_inline`, `create_transaction`),
  `credit_workflow.mark_as_credit`, and
  `entry_credit_workflow._create_payback`.
- **Phase 3 backfill:** simple expense / income / transfer backfills are
  mechanical. Loan and paycheck backfills require careful policy:
  - Loan: query each existing loan-payment shadow income transaction,
    derive the principal-interest split using *current* loan params,
    emit 3 postings.
  - Paycheck: re-run `paycheck_calculator.calculate_paycheck` with
    *current* salary profile state for each historical paycheck
    transaction; emit N postings from the breakdown. Document that
    historical breakdowns may not match what the user saw at the time.
- **Phase 4 read switch:** balance calculator's three variants need
  rewriting. The amortization variant in particular needs to read
  liability-kind postings instead of detecting shadow income on the
  loan account. The entry-aware formula (`_entry_aware_amount`) stays
  as-is -- entries continue to live alongside postings.
- **Phase 4 cutover gate (revised preconditions):**
  1. Posting parity: every non-deleted transaction has >=2 postings,
     SUM(amount) = 0.
  2. Transfer collapse: every legacy `transfers` row has a corresponding
     parent transaction with exactly 2 cash-kind postings on different
     accounts with opposite signs.
  3. Cash-balance parity: anchor + cleared cash-kind postings since
     anchor_as_of equals legacy balance for every account. Exact decimal.
  4. Projection parity: posting-derived projection equals legacy
     projection for every account, every materialised period (1..52).
     Exact decimal.
  5. Audit trigger health: trigger count matches `EXPECTED_TRIGGER_COUNT`.
- **Phase 5 removal:** drop `budget.transfers`, drop
  `Transaction.transfer_id`, `credit_payback_for_id`, the per-row
  shadow uniqueness index, the transfer-related code paths. KEEP
  `transaction_entries` (Finding 2.1).
- **Test rewrite scope:** ~10 service test modules, ~5 route test
  modules need substantive rewrites. Behaviour-level tests (4357 total)
  remain. The full-suite run takes ~12-13 minutes; budget two suite
  runs per phase deploy.

---

## 7. Recommended next step

Resolve the design questions in section 5 with the user before drafting
Phase 1 migration files. Especially questions 1, 2, and 3 (loan, paycheck,
scenario_id) -- they change the schema and the service layer.
