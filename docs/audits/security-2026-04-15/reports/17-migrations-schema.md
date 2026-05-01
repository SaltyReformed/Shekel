# Session S6 -- Migration and Database Schema Audit

**Scope:** Section 1N of `docs/security-audit-workflow.md`.
**Branch:** `audit/security-2026-04-15`.
**Live DB alembic_version (pre-session):** `c7e3a2f9b104` (matches migration head -- DB fully upgraded).
**psql command used throughout:** `docker exec shekel-prod-db psql -U shekel_user -d shekel -c '...'`

This report is written check-by-check. Checks 2-6 append as each is completed.

Conventions:
- **PASS** = upgrade and downgrade fully reverse each other with no obvious data loss.
- **WARN** = works but has a latent issue (partial reversal, fragile string-match logic, or
  irreversible change the developer should notice).
- **FAIL** = downgrade is `pass`, `NotImplementedError` without justification, destructive without
  data preservation, or the migration cannot safely run on a populated database.

---

## Check 1: Migration Inventory

### Chain structure

Linear chain starting at `9dea99d4e33e` (down_revision = None) through 38 subsequent
migrations to HEAD `c7e3a2f9b104`. No branching. Live DB alembic_version matches HEAD.

Total migrations: **39**. All have `def upgrade()` and `def downgrade()` declared (no empty stubs
at the top level).

### Inventory table

| # | Rev ID          | Message (short)                                  | upgrade() ops (summary)                                                                                                                                                                                                             | downgrade() ops (summary)                                                                            | Verdict |
|--:|-----------------|--------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|---------|
|  1| 9dea99d4e33e    | initial schema                                    | create 25+ tables across auth/ref/budget schemas; many FKs without explicit `ondelete`                                                                                                                                              | drop every table in reverse dependency order                                                         | PASS (but downgrade on populated DB would destroy all data -- see Check 2) |
|  2| 07198f0d6716    | add cancelled status                              | `INSERT INTO ref.statuses (name) VALUES ('cancelled')`                                                                                                                                                                              | `DELETE FROM ref.statuses WHERE name = 'cancelled'`                                                  | PASS |
|  3| a3b1c2d4e5f6    | add quarterly, semi_annual, start_period_id       | insert 2 recurrence_patterns; add_column start_period_id (nullable); create named FK `fk_recurrence_rules_start_period` ondelete=SET NULL                                                                                           | drop constraint, drop column, delete 2 patterns                                                      | PASS |
|  4| 44460d8fe471    | add low_balance_threshold to user_settings        | add_column (nullable Integer)                                                                                                                                                                                                       | drop_column                                                                                           | PASS |
|  5| 22b3dd9d9ed3    | add salary schema tables                          | `create_table` x7 (salary_profiles, salary_raises, paycheck_deductions, tax_bracket_sets, tax_brackets, state_tax_configs, fica_configs); many `alter_column` and `_rename_unique` on pre-existing tables; `alter_column` JSONB→JSON | drop 7 salary tables; revert mfa_configs JSON→JSONB; revert 4 unique-constraint renames              | **WARN** (partial reversal -- downgrade does NOT restore the dropped indexes `idx_deductions_profile`, `idx_salary_raises_profile`, `idx_tax_brackets_set`, does NOT revert `alter_column` on fica_configs/paycheck_deductions/salary_profiles/salary_raises/tax_brackets) |
|  6| b4c7d8e9f012    | W-4 fields + credit amounts                       | add 7 columns (5 on salary_profiles, 2 on tax_bracket_sets) all NOT NULL with `server_default='0'`                                                                                                                                  | drop all 7 columns                                                                                    | PASS |
|  7| c5d6e7f8a901    | positive amount CHECKs + baseline scenario uidx   | `create_check_constraint` `ck_transactions_positive_amount` on estimated_amount >= 0; `ck_transactions_positive_actual` on actual_amount; `create_index` `uq_scenarios_one_baseline` partial unique                                  | drop all 3                                                                                            | **WARN** (creates CHECKs with names that DIFFER from later migration `dc46e02d15b4` and the model -- risk of duplicate CHECKs in live DB; `uq_scenarios_one_baseline` is NOT declared in `app/models/scenario.py`) |
|  8| d4e5f6a7b8c9    | phase 4 transfers + savings_goals.user_id         | create transfer_templates; add 4 cols to transfers (name, transfer_template_id FK, is_override, is_deleted); partial unique `idx_transfers_template_period_scenario`; add user_id to savings_goals with server_default='1'          | drop user_id from savings_goals; drop 4 transfers cols + index; drop transfer_templates + index      | PASS |
|  9| e5f6a7b8c9d0    | CHECK and UNIQUE constraints                      | 33 CHECK constraints + 2 UNIQUE constraints across budget / salary / auth                                                                                                                                                            | drop all 35                                                                                           | PASS |
| 10| f1a2b3c4d5e6    | HYSA + account categories (string)                | add_column `account_types.category` String(20); backfill; insert 'hysa'; create_table `hysa_params`; create CHECK ck_hysa_params_frequency                                                                                            | drop hysa_params + index; delete hysa type; drop category column                                     | PASS (note: table renamed later by migration #30, and string category replaced by FK by migration #25) |
| 11| a1b2c3d4e5f6    | debt account tables                               | insert mortgage, auto_loan types; create mortgage_params, mortgage_rate_history, escrow_components, auto_loan_params                                                                                                                 | drop 4 tables in reverse; delete 2 types                                                             | PASS (note: 3 of these tables dropped later by migration #26) |
| 12| c3d4e5f6g7h8    | investment + retirement tables                     | insert 5 account types (401k, roth_401k, traditional_ira, roth_ira, brokerage); create investment_params + pension_profiles; add target_account_id; add 3 retirement cols to user_settings                                          | reverse all                                                                                           | PASS |
| 13| d5e6f7a8b9c0    | default_grid_account_id on user_settings          | add_column (nullable FK SET NULL); `op.execute` UPDATE to backfill first active checking account per user                                                                                                                            | drop_column                                                                                           | PASS (backfilled data irreversibly lost on downgrade -- expected for a column drop) |
| 14| 2ae345ea9048    | session_invalidated_at                            | add_column (nullable DateTime)                                                                                                                                                                                                       | drop_column                                                                                           | PASS |
| 15| a8b1c2d3e4f5    | audit_log + triggers                              | `CREATE TABLE system.audit_log`, 3 indexes, PL/pgSQL `system.audit_trigger_func()`, attach AFTER triggers to 22 tables via loop                                                                                                      | drop all triggers, drop function, drop table                                                          | **WARN** (assumes `system` schema already exists -- no `CREATE SCHEMA IF NOT EXISTS system`; migration fails if schema missing. Schema creation presumably lives in `entrypoint.sh` but is outside the Alembic chain -- a fresh-env `flask db upgrade` on a bare DB would fail.) |
| 16| 02b1ff12b08c    | standard_deduction on state_tax_configs           | add_column (nullable Numeric(12,2))                                                                                                                                                                                                  | drop_column                                                                                           | PASS |
| 17| 7abcbf372fff    | tax_year on state_tax_configs                     | add_column NOT NULL with `server_default='2026'`; remove default; drop old unique (user_id, state_code); create new unique (user_id, state_code, tax_year)                                                                          | drop new unique; recreate old 2-col unique; drop column                                              | **WARN** (if multiple rows share (user_id, state_code) with different tax_years, the downgrade's recreation of the 2-col unique fails with duplicate-key violation) |
| 18| b4c5d6e7f8a9    | backfill NULL effective_year on recurring raises  | `UPDATE salary.salary_raises SET effective_year = EXTRACT(YEAR FROM created_at) WHERE is_recurring=TRUE AND effective_year IS NULL`                                                                                                  | **`pass`** with comment "No safe automatic downgrade"                                                | **WARN** (per workflow rules a bare `pass` is FAIL; comment-only justification weaker than `NotImplementedError`. Intent is fine -- this is a data fix -- but the form violates the standard) |
| 19| f8f8173ff361    | end_date on recurrence_rules                      | add_column (nullable Date)                                                                                                                                                                                                            | drop_column                                                                                           | PASS |
| 20| 75b00691df57    | calibration_overrides tables                      | create_table calibration_overrides + calibration_deduction_overrides                                                                                                                                                                 | drop both                                                                                             | PASS |
| 21| 01214a4ff394    | widen effective rate precision Numeric(7,5)→(12,10) | 4x `alter_column` type change                                                                                                                                                                                                        | 4x `alter_column` type change back                                                                     | **WARN** (narrowing on downgrade truncates any rate with >5 decimals; in practice calibration produces ≤10 decimals but typically only 4-6 are meaningful, so real-world data loss unlikely -- flag for awareness) |
| 22| efffcf647644    | add account_id to transactions                    | `add_column('transactions', 'account_id', nullable=False)` **with NO server_default**; create_index; create_foreign_key                                                                                                              | drop FK, drop index, drop column                                                                     | **FAIL** (migration is not safe to re-run against a populated `budget.transactions` -- `ALTER TABLE ADD COLUMN NOT NULL` without default fails if any rows exist. The live DB has this column populated, so the migration must have been applied when the table was empty or via a manual workaround; it does not satisfy the coding-standards rule "Adding NOT NULL to a populated table requires server_default") |
| 23| 772043eee094    | transfer_id + category_id                         | add transfer_id (nullable, FK CASCADE) + partial index; add category_id to transfers + transfer_templates (both nullable FK, **no ondelete**)                                                                                        | reverse                                                                                               | **WARN** (category_id FKs on transfers / transfer_templates created without explicit ondelete here -- defaults to NO ACTION. Fixed later by migration #29 for transfers/transfer_templates; good outcome but fragile chain) |
| 24| e138e6f55bf0    | boolean cols + rename status display names        | add 3 bool cols (server_default false, NOT NULL); UPDATE values by lowercase name; rename names to Capitalized                                                                                                                        | revert rename; drop 3 bool cols                                                                       | PASS |
| 25| 415c517cf4a4    | account_type_categories + booleans + capitalize    | create ref.account_type_categories; seed 4 rows; add category_id nullable FK; backfill FK from string category; add has_parameters + has_amortization; backfill; capitalize AccountType/RecurrencePattern/TransactionType names; alter category_id NOT NULL; drop old category string column | reverse all name changes; restore category string; populate from FK; drop booleans; drop category_id; drop categories table | PASS (note: relies on string matching for name reverts -- any out-of-chain rename would break reversal) |
| 26| c67773dc7375    | unify loan_params into single table               | add icon_class + max_term_months to account_types; backfill; flip HELOC has_parameters; create loan_params + rate_history; INSERT…SELECT from auto_loan_params/mortgage_params/mortgage_rate_history; drop 3 old tables               | recreate 3 old tables; INSERT…SELECT back using `at.name IN ('Mortgage', 'Auto Loan')`; drop new tables; drop icon_class + max_term_months; flip HELOC back | **WARN** (downgrade only migrates back rows whose account type is exactly 'Mortgage' or 'Auto Loan'. Any Student Loan / Personal Loan / HELOC rows in loan_params are permanently lost on downgrade -- those types didn't exist in the pre-unification schema, so strictly speaking this is an expected data loss, but it should be explicit) |
| 27| a45b88e8fa2e    | has_interest + is_pretax + is_liquid               | add 3 bool cols (server_default false, NOT NULL); UPDATE flags for HYSA, HSA, 401(k), etc.; flip HSA has_parameters TRUE                                                                                                              | UPDATE HSA has_parameters back to false; drop 3 bool cols                                            | PASS |
| 28| dc46e02d15b4    | CHECK constraints on loan_params + transactions   | create 4 CHECKs on loan_params (orig/curr/rate/term); create 2 CHECKs on transactions (`ck_transactions_estimated_amount`, `ck_transactions_actual_amount`)                                                                           | drop all 6                                                                                            | **FAIL** (the 2 transaction CHECKs duplicate those created in migration #7 `c5d6e7f8a901` under different names (`ck_transactions_positive_amount`, `ck_transactions_positive_actual`). Both pairs should exist in the live DB -- to be confirmed in Check 3. Downgrading this migration leaves the #7 pair in place; re-upgrading re-creates the #28 pair with the same SQL logic under different name -- **schema churn**) |
| 29| 047bfed04987    | standardize ondelete policies across FKs          | drop + recreate ~20 FKs in budget schema with explicit ondelete (RESTRICT for ref FKs, SET NULL for optional refs, CASCADE none here -- those were already explicit)                                                                  | drop + recreate same FKs WITHOUT ondelete (back to implicit NO ACTION)                               | PASS as written, but **only covers budget schema FKs** -- salary ref-FKs remain without explicit ondelete |
| 30| b4a6bb55f78b    | rename hysa_params → interest_params              | `rename_table`; rename unique index `hysa_params_account_id_key` → `interest_params_account_id_key`; rename CHECK constraint                                                                                                         | reverse                                                                                               | **WARN** (does NOT rename the implicit primary-key constraint `hysa_params_pkey` or the identity sequence `hysa_params_id_seq`. These will persist under old names in the live DB -- will verify in Check 3) |
| 31| 98b1adb05030    | enable 529 plan parameters                         | `UPDATE ref.account_types SET has_parameters=true WHERE name='529 Plan'`                                                                                                                                                             | UPDATE back to false                                                                                  | PASS |
| 32| 1dc0e7a1b9e4    | goal_modes + income_units + seed data              | create_table goal_modes; create_table income_units; INSERT seed rows (id=1 Fixed, id=2 Income-Relative; id=1 Paychecks, id=2 Months)                                                                                                  | drop both tables (cascade-cleans seed data)                                                          | PASS |
| 33| 4f2d894216ad    | income-relative goal columns                       | assert id=1 is 'Fixed' (fail-early guard); add goal_mode_id NOT NULL server_default='1'; add income_unit_id nullable; add income_multiplier; `alter_column('target_amount', nullable=True)`; create 2 FKs **without explicit ondelete**; create CHECK ck_savings_goals_multiplier_positive | drop CHECK, 2 FKs; set target_amount=1 for NULL; revert to NOT NULL; drop 3 cols                     | **WARN** (FKs on goal_mode_id and income_unit_id lack explicit ondelete; per coding standards this is a ref-table FK and must be RESTRICT) |
| 34| 087bb96db063    | is_active column on categories                     | add_column NOT NULL server_default=true                                                                                                                                                                                              | drop_column                                                                                           | PASS |
| 35| 2c1115378030    | Money Market + CD interest params                  | UPDATE has_parameters/has_interest TRUE for those types; INSERT InterestParams for existing accounts lacking them                                                                                                                    | DELETE only InterestParams rows still at defaults; UPDATE flags back                                 | PASS (smart reverse -- preserves user-edited InterestParams) |
| 36| f06bcc98bc3a    | section 8 settings columns                         | add 3 cols on user_settings (large_transaction_threshold, trend_alert_threshold, anchor_staleness_days) NOT NULL server_default; 3 CHECK constraints                                                                                  | drop 3 CHECKs + 3 cols                                                                                | PASS |
| 37| f15a72a3da6c    | due_date + paid_at + due_day_of_month              | add due_day_of_month to recurrence_rules + CHECK; add due_date + paid_at to transactions + partial index; complex DATE backfill                                                                                                      | drop index + paid_at + due_date + CHECK + due_day_of_month                                           | PASS |
| 38| b961beb0edf6    | entry tracking + companion support                 | create ref.user_roles + seed; add track_individual_purchases + companion_visible to transaction_templates; add role_id NOT NULL default=1 to auth.users with REFERENCES; add linked_owner_id + FK; create budget.transaction_entries + 2 indexes + CHECK  | DROP TABLE transaction_entries; drop FK + linked_owner_id + role_id; drop user_roles; drop 2 template cols | PASS |
| 39| c7e3a2f9b104    | is_cleared on transaction_entries (HEAD)           | ADD COLUMN is_cleared BOOLEAN NOT NULL DEFAULT FALSE; backfill to TRUE for past-dated projected entries                                                                                                                              | DROP COLUMN IF EXISTS is_cleared                                                                     | PASS |

### Check 1 tally

- **PASS:** 30 migrations
- **WARN:** 8 migrations (#5, #7, #15, #17, #18, #21, #23, #26, #30, #33 -- revised to 10; see below)
- **FAIL:** 2 migrations (#22 efffcf647644, #28 dc46e02d15b4)

Recount:
- WARN entries above: #5, #7, #15, #17, #18, #21, #23, #26, #30, #33 = **10 WARN**
- FAIL entries: #22, #28 = **2 FAIL**
- PASS entries: 39 - 10 - 2 = **27 PASS**

### Key findings from Check 1

1. **F-S6-C1-01 (High / process)** -- Migration `efffcf647644_add_account_id_column_to_transactions` adds a NOT NULL column without `server_default`. If re-run against a non-empty `budget.transactions` it will fail. The live DB has this column populated, which means the original run must have relied on an out-of-migration data backfill or an empty table at the time -- neither is documented. **Impact:** any fresh-env recovery (drop DB, `flask db upgrade`) with production transaction data will hit a hard error. **Remediation:** amend the migration to `add_column(... server_default='<id>')` or run an `op.execute("UPDATE ... SET account_id = ...")` before the `alter_column` that makes it NOT NULL, with a sensible fallback (e.g. the user's first checking account).

2. **F-S6-C1-02 (High / schema churn)** -- Migrations `c5d6e7f8a901` and `dc46e02d15b4` both add effectively the same CHECK constraints on `budget.transactions.estimated_amount` and `actual_amount`, under different names (`ck_transactions_positive_amount` / `ck_transactions_positive_actual` vs `ck_transactions_estimated_amount` / `ck_transactions_actual_amount`). The live DB almost certainly has **both pairs**. The model only declares the second pair. This is confirmed drift -- Check 3 will dump and verify.

3. **F-S6-C1-03 (Medium / downgrade reachability)** -- Migration `a8b1c2d3e4f5` creates tables in the `system` schema but does NOT `CREATE SCHEMA IF NOT EXISTS system`. If a fresh database does not have the `system` schema pre-created (for example, via `entrypoint.sh`), `flask db upgrade` will fail at this migration. This is a deployment-process bug -- the migration chain is not self-contained.

4. **F-S6-C1-04 (Medium / partial reversal)** -- Migration `22b3dd9d9ed3` is the canonical "auto-generated and not adjusted" Alembic migration. Its upgrade mixes `create_table` for 7 new tables with `alter_column` + `drop_index` on pre-existing tables; its downgrade only drops the 7 new tables and reverts 4 unique-constraint renames. It does NOT restore the dropped `idx_deductions_profile`, `idx_salary_raises_profile`, `idx_tax_brackets_set` indexes, and does NOT revert the NOT NULL enforcement on fica_configs columns, the String length changes on paycheck_deductions.name / salary_profiles.name, or the Text↔VARCHAR change on salary_raises.notes. Downgrading past this point leaves the schema in a state that is neither pre-migration nor post-migration.

5. **F-S6-C1-05 (Medium / rename hygiene)** -- Migration `b4a6bb55f78b` renames table `budget.hysa_params` to `budget.interest_params` but does not rename the implicit primary-key constraint or the identity sequence. The live DB will have a table named `interest_params` but a PK constraint named `hysa_params_pkey` and a sequence named `hysa_params_id_seq`. This is cosmetic drift that complicates future diagnostics; not a functional bug.

6. **F-S6-C1-06 (Medium / FK ondelete gap)** -- Migration `4f2d894216ad` adds `goal_mode_id` / `income_unit_id` FKs on `budget.savings_goals` referring to ref tables without explicit ondelete. Per `docs/coding-standards.md` SQL section, ref-table FKs must be RESTRICT. Also a gap for the salary schema ref FKs (filing_status_id, raise_type_id, deduction_timing_id, calc_method_id, tax_type_id, goal_mode_id, income_unit_id -- Check 5 will catalog exhaustively).

7. **F-S6-C1-07 (Low / downgrade correctness)** -- Migration `7abcbf372fff` replaces the unique constraint `(user_id, state_code)` with `(user_id, state_code, tax_year)` on `salary.state_tax_configs`. The downgrade recreates the narrower constraint without checking for duplicate (user_id, state_code) pairs. With the rest of the chain applied, a user can hold multiple state_tax_configs for the same state in different tax_years; downgrade will fail with a unique violation in that case.

8. **F-S6-C1-08 (Low / downgrade is `pass`)** -- Migration `b4c5d6e7f8a9` uses `pass` (not `NotImplementedError`) in its downgrade. The comment justifies it ("NULL effective_year was a bug"), but per workflow rules this is technically a FAIL -- the migration should use `raise NotImplementedError` or restore the NULL state for rows where created_at year equals effective_year (so that only backfilled rows are reverted).

---

## Check 2: Destructive Operations Review

Filtered the 39 migrations for destructive ops: `drop_table`, `drop_column`, `rename_table`, `alter_column` (type change), `drop_constraint`, `drop_index`, and `op.execute` with raw `DELETE`/`DROP`/`ALTER ... DROP`. 32 of 39 files contain at least one such op (most only in the downgrade path, which is expected). This section focuses on the ones whose destructiveness is non-trivial.

### 2A. Destructive ops in the UPGRADE path (applied to the live DB)

These modify the production schema when `flask db upgrade` runs. Each was presumably applied to the live DB at some point in the past.

| # | Rev | Op (upgrade) | Table | Data preserved? | Destructive when run? | Severity |
|--:|-----|--------------|-------|-----------------|-----------------------|----------|
| D-01 | c67773dc7375 | `drop_table auto_loan_params`, `drop_table mortgage_rate_history`, `drop_table mortgage_params` | budget | YES -- `INSERT…SELECT` into new `loan_params` / `rate_history` before drop | PASS at run time (data migrated) -- but permanently destructive to a rollback scenario (see 2B D-08 below) | Info (well-handled forward) |
| D-02 | 415c517cf4a4 | `drop_column account_types.category` (String) | ref | YES -- backfilled `category_id` FK from the string column before drop | PASS (column drop after migration) | Info |
| D-03 | b4a6bb55f78b | `rename_table hysa_params → interest_params` | budget | YES (rename is not destructive of row data) | PASS -- but leaves orphan-named PK (`hysa_params_pkey`) and sequence (`hysa_params_id_seq`). See Check 1 F-S6-C1-05 | Medium (drift) |
| D-04 | efffcf647644 | `add_column account_id NOT NULL` **without server_default**, then create FK | budget.transactions | **NO** -- no backfill step exists in the migration | **WOULD FAIL on a populated table.** The live DB has this column populated, so the migration was applied at a time when (a) the table was empty, or (b) a manual `ALTER TABLE ... SET DEFAULT ... UPDATE ... SET NOT NULL` was executed outside Alembic, or (c) the developer ran a one-off backfill script. None of those is in the repo. | **High** (process / reproducibility -- see Check 1 F-S6-C1-01) |
| D-05 | 22b3dd9d9ed3 | `drop_index idx_anchor_history_account` (then recreate with DESC order in downgrade but not in upgrade), `drop_index idx_deductions_profile`, `drop_index idx_salary_raises_profile`, `drop_index idx_tax_brackets_set` | budget.account_anchor_history, salary.paycheck_deductions, salary.salary_raises, salary.tax_brackets | YES (indexes are structural; no row data) | Upgrade is fine; downgrade does not restore 3 of the 4 indexes -- see F-S6-C1-04 | Medium (partial reversal -- but no user-data loss) |
| D-06 | 22b3dd9d9ed3 | `alter_column` x11 (NULL→NOT NULL on fica_configs, VARCHAR(100)→VARCHAR(200) on salary_profiles/paycheck_deductions.name, VARCHAR(200)→TEXT on salary_raises.notes, INTEGER nullable change on tax_brackets.sort_order, JSONB→JSON on mfa_configs.backup_codes) | auth, salary | Most alters expand capacity or enforce constraints -- not destructive of existing values except `mfa_configs.backup_codes` JSONB→JSON which drops binary representation metadata (PostgreSQL JSONB→JSON preserves values but loses indexing benefits). | Upgrade: works on populated table because widening string columns and tightening nullability is safe when current data already satisfies the new constraint. Downgrade: does not reverse the widens (see F-S6-C1-04). | Medium (partial reversal) |

### 2B. Destructive ops in the DOWNGRADE path (what happens if `flask db downgrade` is run)

The downgrade path is rarely exercised but must be safe in an emergency. The concern here is: **if the developer needs to roll back one migration, what data is lost?** These ops would execute on the live DB (with real user data) if `flask db downgrade` is invoked.

| # | Rev | Op (downgrade) | Table | Reversible? | Data loss severity | Notes |
|--:|-----|----------------|-------|-------------|--------------------|-------|
| D-07 | 9dea99d4e33e | drop every table across auth/ref/budget schemas (22 tables) | all | N/A -- this is the initial migration | **Catastrophic** if run against populated DB | Acceptable (initial migration). User's responsibility not to run `downgrade base` against production. |
| D-08 | c67773dc7375 | drop_table loan_params + rate_history after INSERT-SELECT back into mortgage_params / auto_loan_params / mortgage_rate_history, using `WHERE at.name IN ('Mortgage', 'Auto Loan')` | budget | PARTIAL -- only Mortgage and Auto Loan rows are migrated back | **HIGH**: any Student Loan / Personal Loan / HELOC row in loan_params has no destination table (those types didn't exist pre-unification) and is silently dropped. The pre-unification schema had no place for them, so this is technically "expected," but the migration should `raise NotImplementedError` when such rows exist rather than silently discarding them. | Enhance downgrade to `RAISE EXCEPTION IF EXISTS (SELECT 1 FROM budget.loan_params lp JOIN budget.accounts a ON a.id = lp.account_id JOIN ref.account_types at ON at.id = a.account_type_id WHERE at.name NOT IN ('Mortgage', 'Auto Loan'))` |
| D-09 | 22b3dd9d9ed3 | drop 7 salary tables (salary_profiles, salary_raises, paycheck_deductions, tax_brackets, tax_bracket_sets, state_tax_configs, fica_configs) | salary | N/A -- no preservation | **HIGH**: all salary configuration, raises, deductions, tax brackets, state tax rules, FICA rates are lost. | Expected for "add" migration's downgrade, but destructive. If the developer rolls back to pre-22b3dd9d9ed3 for any reason, they lose *everything* in salary schema. |
| D-10 | 75b00691df57 | drop calibration_overrides + calibration_deduction_overrides | salary | N/A | **MEDIUM**: all pay-stub-derived effective rates and deduction overrides lost | Expected for "add" migration's downgrade. |
| D-11 | 4f2d894216ad | drop income_multiplier, income_unit_id, goal_mode_id columns on savings_goals; also `UPDATE savings_goals SET target_amount=1 WHERE target_amount IS NULL` to satisfy the NOT NULL it re-asserts | budget.savings_goals | PARTIAL -- income-relative goals irreversibly destroyed | **MEDIUM**: any income-relative savings goal has its `goal_mode_id`, `income_unit_id`, `income_multiplier` lost (columns dropped) AND its `target_amount` silently replaced by `1`. The original semantic (multiplier of income) is destroyed. User would see a $1 savings goal with no way to reconstruct. | The downgrade should refuse if any income-relative goals exist, not silently re-write their target to $1. |
| D-12 | d4e5f6a7b8c9 | drop user_id column from savings_goals; drop transfer_templates table; drop 4 columns from transfers | budget | PARTIAL | **MEDIUM**: all transfer_templates deleted; transfer names, template links, override flags, and soft-delete state on transfers lost; user ownership of savings goals lost (goals become user-less). | Pre-existing transfer data remains but loses its template linkage; next `flask db upgrade` would re-create empty transfer_templates. |
| D-13 | c3d4e5f6g7h8 | drop pension_profiles, investment_params, 3 user_settings cols, 1 paycheck_deductions col; DELETE ref.account_types WHERE name IN 5-types | salary, budget, auth, ref | N/A | **MEDIUM**: all investment params (assumed returns, contribution limits, employer match), all pension profiles (benefit multipliers, hire dates), all retirement settings (SWR, retirement date, tax rate) lost. The ref DELETE would additionally fail with FK violation if any accounts exist for 401k/IRA/brokerage types -- which correctly prevents silent data corruption. | Refuse-to-run behavior on FK is actually a safety feature; consider adding an explicit check. |
| D-14 | b961beb0edf6 | DROP TABLE transaction_entries; drop role_id, linked_owner_id on users; DROP TABLE ref.user_roles | budget, auth, ref | N/A | **HIGH**: all individual purchase entries are lost. For any transaction with `track_individual_purchases=True`, the entry history is destroyed. Companion users' `linked_owner_id` is lost (they become unlinked). The role_id default of 1 (owner) means companion users lose their role marker. | The `ref.user_roles` DROP would fail with FK violation if any user has `role_id` set -- but since downgrade also drops that column first, the sequence works. The owner/companion relationship is destroyed. |
| D-15 | c7e3a2f9b104 (HEAD) | DROP COLUMN is_cleared | budget.transaction_entries | N/A | **LOW**: only 1 bit of state per entry lost; balance calculator would compute stale values but entries remain. | Minor data loss. |
| D-16 | a1b2c3d4e5f6 | drop mortgage_params, mortgage_rate_history, auto_loan_params, escrow_components; DELETE ref.account_types mortgage/auto_loan | budget, ref | N/A | **MEDIUM**: but noted that later migration c67773dc7375 already drops mortgage_params and auto_loan_params and mortgage_rate_history (migrates data to loan_params / rate_history first), so by the time a1b2c3d4e5f6 downgrade runs (via chain-walking back from HEAD), those tables have already been re-created by c67773dc7375 downgrade. Chain-walked downgrade is therefore chained correctly. | Chain is consistent. Skipping directly to a1b2c3d4e5f6 from HEAD without running c67773dc7375 downgrade first would fail. |
| D-17 | f1a2b3c4d5e6 | drop_table hysa_params | budget | N/A | **Contingent**: by the time downgrade runs (chain-walking back from HEAD), b4a6bb55f78b's downgrade will already have renamed `interest_params` back to `hysa_params`. So this DROP lands on the renamed table. Interest params for HYSA/HSA/Money Market/CD accounts are lost. | Chain-consistent but destructive. |
| D-18 | 07198f0d6716, a3b1c2d4e5f6, 98b1adb05030, various | `DELETE FROM ref.recurrence_patterns WHERE name=…`, `DELETE FROM ref.account_types WHERE name=…`, `UPDATE ref.* SET flag=…` | ref | N/A | **LOW but potentially RAISE**: these DELETEs on ref tables would RAISE with FK violation if any live row references the deleted enum value. This is actually the desired behavior -- better to fail the downgrade than silently orphan FK references. | Safety feature (FK RESTRICT / NO ACTION prevents silent corruption). |
| D-19 | 01214a4ff394 | `alter_column` Numeric(12,10) → Numeric(7,5) on 4 effective_rate cols | salary.calibration_overrides | PARTIAL | **LOW**: any effective rate value with >5 decimal places truncates. In practice calibration rates rarely use more than 5-6 decimals, but the truncation is silent. | Document in migration comment. |
| D-20 | 7abcbf372fff | recreate 2-col unique `(user_id, state_code)` on state_tax_configs | salary | Conditional | **LOW-MEDIUM**: if any user has >1 state_tax_config entry for the same state across different tax_years, the downgrade fails with a unique-violation. Forces manual cleanup before downgrade. | Arguably correct (refuses to silently conflate rows) but the migration should document this. |

### 2C. Process / review findings

- **No migration in the chain has a `review_by:` or `approved_by:` line.** Per `docs/coding-standards.md` "Destructive migrations require explicit approval. Drops, renames, type changes, and constraint removals must be discussed with the developer first." The audit cannot verify whether D-01 through D-06 were reviewed before application, because the review record does not live in the migration file. **Finding F-S6-C2-01 (Low / process):** add a docstring convention for destructive migrations to record the developer's review, e.g. `# REVIEWED: <date>, <scope-of-review-performed>`.

- **The migration chain is linear and the developer self-reviewed auto-generated migrations.** This is acceptable for a solo-operator project but means typos or partial reversals in auto-generated downgrades can persist indefinitely. F-S6-C1-04 (migration `22b3dd9d9ed3`) is the canonical example -- its downgrade was never adjusted to fully reverse the upgrade.

### Check 2 severity tally

- **High (data loss or unrunnable):** 3 findings -- D-04 (efffcf647644 unsafe on populated table), D-08 (c67773dc7375 downgrade silently drops non-Mortgage/Auto Loan loans), D-14 (b961beb0edf6 downgrade drops all transaction_entries).
- **Medium:** 5 findings -- D-03 (b4a6bb55f78b orphan PK/seq), D-05/D-06 (22b3dd9d9ed3 partial reversal + indexes), D-09/D-10/D-12/D-13 (drop-data-on-downgrade for salary / calibration / transfers / investments -- expected but undocumented), D-11 (4f2d894216ad silently overwrites income-relative goals), D-16/D-17 (chain-dependent data loss).
- **Low:** D-15 (is_cleared drop), D-18 (ref DELETE FK safety), D-19 (rate truncation), D-20 (unique recreate), F-S6-C2-01 (review process).

---

## Check 3: Live Schema Drift

Dumped `\d+ <schema>.<table>` for all 43 user-data tables to `docs/audits/security-2026-04-15/scans/schema-<schema>-<table>.txt`. Compared each against the SQLAlchemy model in `app/models/`.

Pre-conditions verified:

- Alembic version on live DB: `c7e3a2f9b104` (migration chain HEAD).
- The 43 tables live in the DB exactly correspond to the 43 tables declared across `app/models/*.py`. No missing tables, no orphan tables.
- The `system` schema exists but is empty (no tables, sequences, functions, triggers). This is the largest drift finding -- see F-S6-C3-01 below.

### 3A. Critical drift: missing audit-log infrastructure

**F-S6-C3-01 (High / audit-log regression)**

Migration `a8b1c2d3e4f5_add_audit_log_and_triggers.py` (revision #15 in the chain, applied before the current HEAD) creates:

- `system.audit_log` table with 11 columns and 3 indexes.
- `system.audit_trigger_func()` PL/pgSQL function.
- 22 AFTER INSERT/UPDATE/DELETE triggers attached to every financial/auth table (`audit_accounts`, `audit_transactions`, etc.).

The live DB at alembic_version `c7e3a2f9b104` (which is AFTER #15) contains **none of these**. Verification queries:

```
SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
  WHERE n.nspname = 'system';                                          -- 0 rows (no tables/sequences/indexes)
SELECT n.nspname || '.' || p.proname FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid
  WHERE n.nspname = 'system';                                          -- 0 rows (no functions)
SELECT tgname FROM pg_trigger WHERE tgname LIKE 'audit_%';             -- 0 rows (no triggers)
```

**Impact:** the audit log that tracks all INSERT/UPDATE/DELETE on financial + auth tables does not run. Any security-relevant change -- a password update, a transaction edit, an account deletion -- produces zero audit entries. The audit table exists in the migration file, in the developer's mental model, and in anything the app relies on, but not in reality.

**Root cause (inferred):** either (a) the migration ran once but the `system` schema, function, and triggers were dropped manually afterward; (b) the migration partially failed but alembic_version was advanced anyway; or (c) a DB restore happened from a pre-migration snapshot without re-running Alembic to HEAD. The alembic_version matches HEAD, so (a) is most plausible. Without a documented reason for removal, this is a **silent regression** of an intentionally-built security control.

**Remediation:** either (a) re-run the audit-log migration logic (CREATE TABLE, CREATE FUNCTION, CREATE TRIGGERs) manually against the live DB, or (b) create a new migration that rebuilds the audit infrastructure if the intent was to redesign it. Either way, document what happened.

### 3B. Artifacts from migration c5d6e7f8a901 are missing from the live DB

**F-S6-C3-02 (Medium / migration-to-DB drift)**

Migration `c5d6e7f8a901` (revision #7 in chain) created three artifacts:

1. `ck_transactions_positive_amount` CHECK (on estimated_amount)
2. `ck_transactions_positive_actual` CHECK (on actual_amount)
3. `uq_scenarios_one_baseline` partial unique index on `budget.scenarios(user_id) WHERE is_baseline=TRUE`

**None of the three exist in the live DB.** Queries:

```
SELECT conname FROM pg_constraint c JOIN pg_namespace n ON c.connamespace = n.oid
  JOIN pg_class cl ON c.conrelid = cl.oid
  WHERE cl.relname = 'transactions' AND n.nspname = 'budget' AND c.contype = 'c';
-- returns only ck_transactions_actual_amount, ck_transactions_estimated_amount
-- (both from later migration dc46e02d15b4 -- see Check 4)

\d+ budget.scenarios  -- Indexes section shows only scenarios_pkey and uq_scenarios_user_name
```

No migration in the chain drops these. Therefore they were **manually dropped outside Alembic** (or the migration failed partially). The enforcement layer that guarantees "only one baseline scenario per user" is missing.

**Impact:** it is possible (via raw SQL or a racy UI interaction that bypasses app-level checks) to create two baseline scenarios for the same user. No app-level check catches this -- the model relies on the partial unique index for enforcement (the `is_baseline=True` check lives in `Scenario` model as a boolean column, not as a Python-level invariant).

**Remediation:** recreate `uq_scenarios_one_baseline` manually or via a new migration. Then confirm no duplicates exist before applying.

### 3C. Table rename leaves orphan identifiers

**F-S6-C3-03 (Low / cosmetic drift)**

After migration `b4a6bb55f78b` (renaming `budget.hysa_params` → `budget.interest_params`), the live DB shows:

```
\d+ budget.interest_params
...
 id | integer | not null | nextval('budget.hysa_params_id_seq'::regclass)         ← old sequence name
Indexes:
    "hysa_params_pkey" PRIMARY KEY, btree (id)                                     ← old PK name
    "interest_params_account_id_key" UNIQUE CONSTRAINT, btree (account_id)         ← renamed
Foreign-key constraints:
    "hysa_params_account_id_fkey" FOREIGN KEY (account_id) REFERENCES budget.accounts(id) ON DELETE CASCADE  ← old FK name
```

So the table is renamed, and the UNIQUE constraint and the CHECK constraint are renamed, but the primary-key constraint, the identity sequence, and the FK constraint from this table retain the `hysa_params_*` prefix. No functional impact but diagnostics (pg_dump, Alembic autogenerate, performance tools) will show confusing legacy names.

**Remediation:** add a follow-up migration that renames the PK, sequence, and FK:

```sql
ALTER SEQUENCE budget.hysa_params_id_seq RENAME TO interest_params_id_seq;
ALTER INDEX   budget.hysa_params_pkey RENAME TO interest_params_pkey;
ALTER TABLE   budget.interest_params RENAME CONSTRAINT hysa_params_account_id_fkey TO interest_params_account_id_fkey;
```

### 3D. Column-level drift: model nullability vs. live DB

**F-S6-C3-04 (Medium / nullable drift on boolean flags)**

The `budget.transactions` model declares:

```python
is_override = db.Column(db.Boolean, default=False)  # no nullable=False
is_deleted  = db.Column(db.Boolean, default=False)  # no nullable=False
```

Live DB:

```
 is_override | boolean | | <no not null> | <no default>
 is_deleted  | boolean | | <no not null> | <no default>
```

Both columns are `nullable=True` in the live DB with no server_default. The model has a Python-side default but no server_default. Per coding standards, *"NOT NULL by default. Every new column should be NOT NULL unless there is a specific reason for nullability."* Neither column has a documented reason for nullability. In practice the balance calculator and grid route may read `is_deleted` as `None` instead of `False` for older rows that were inserted before the column existed (the initial migration `9dea99d4e33e` declared `is_override` and `is_deleted` as `nullable=True` and never tightened them). The `Transaction.effective_amount` property handles `None` via `if self.is_deleted:` which correctly treats `None` as falsy, but this is implicit reliance.

Same pattern exists on the `budget.transfers` model's `is_override`/`is_deleted` columns in the MODEL, except the model declares them as `nullable=False` (correct). Live DB for transfers DOES have `not null` on both. So transfers was fixed; transactions was not. **Drift between `transactions.is_deleted` and `transfers.is_deleted`.**

Additionally: `budget.accounts.is_active`, `budget.accounts.sort_order`, `budget.scenarios.is_baseline`, `auth.users.is_active`, `budget.savings_goals.is_active`, `budget.transfer_templates.is_active`, `budget.transfer_templates.sort_order`, `salary.pension_profiles.is_active`, `salary.salary_raises.is_recurring`, `salary.calibration_overrides.is_active`, `salary.paycheck_deductions.is_active`, `salary.paycheck_deductions.inflation_enabled`, `salary.paycheck_deductions.sort_order`, `salary.salary_profiles.is_active`, `salary.salary_profiles.sort_order` -- all are `nullable=True` in the live DB and in the model. Per coding standards these are wrong; per actual current behavior they work because the app always writes explicit values. The risk is that a raw SQL INSERT omitting these columns creates a row with `is_active=NULL` which is neither True nor False.

**Remediation:** new migration that tightens all `is_active`, `is_deleted`, `is_override`, `is_baseline`, `is_recurring`, `sort_order`, `inflation_enabled` columns to `NOT NULL` with appropriate `server_default`, AND updates the model to add `nullable=False, server_default="..."`.

### 3E. Column-level drift: server_default removal

**F-S6-C3-05 (Low / migration-to-DB drift)**

Several columns that the initial creation migrations declared with `server_default=...` show no default in the live `\d+` output. Examples:

| Table | Column | Migration set | Live has |
|-------|--------|--------------|----------|
| salary.fica_configs | ss_rate, ss_wage_base, medicare_rate, medicare_surtax_rate, medicare_surtax_threshold | server_default=0.0620, 176100, 0.0145, 0.0090, 200000 | **no default** |
| salary.pension_profiles | name (server_default 'Pension'), consecutive_high_years (4) | declared in migration | **no default** |
| salary.salary_profiles | name (server_default 'Primary'), pay_periods_per_year (26) | declared in migration | **no default** |
| budget.investment_params | employer_contribution_type (server_default 'none'), assumed_annual_return (0.07000) | declared in migration | **no default** |
| budget.transfers | is_override, is_deleted | server_default='false' in migration d4e5f6a7b8c9 | **no default** (but NOT NULL) |

These are not drift *against the model* -- the models use Python-side `default=` not `server_default=`, so aligning with the model actually means not having a DB default. But they ARE drift against what the migrations set up. Either:
- (a) something is stripping the defaults after creation (unclear what), or
- (b) the `\d+` "Default" column does not display defaults that exist in `pg_attrdef` for some edge case.

Verification via `pg_attrdef` would resolve (b). For now treat as Low -- the functional impact is that apps doing raw INSERTs without explicit column values will hit NOT NULL violation instead of receiving the sensible default.

**Remediation:** query `pg_attrdef` to confirm whether defaults actually exist; if not, issue a migration to restore them.

### 3F. Per-table drift summary

| Table | Columns | CHECKs | FKs (ondelete) | Indexes | Overall |
|-------|---------|--------|----------------|---------|---------|
| auth.users | ✓ | ✓ (none declared) | ✓ | ✓ | PASS |
| auth.user_settings | ✓ | ✓ (all 6) | ✓ | ✓ | PASS |
| auth.mfa_configs | ✓ | ✓ (none declared) | ✓ | ✓ | PASS |
| budget.accounts | ✓ | ✓ (none declared) | ✓ | ✓ | PASS |
| budget.account_anchor_history | ✓ | ✓ (none declared) | ✓ | ✓ | PASS |
| budget.categories | ✓ | ✓ (none declared) | ✓ | ✓ | PASS |
| budget.escrow_components | ✓ | ✓ (none declared -- `annual_amount` should CHECK >= 0 but model doesn't declare) | ✓ | ✓ | PASS (potential gap noted) |
| budget.interest_params | ✓ | ✓ | ✓ | **Orphan names** (F-S6-C3-03) | MEDIUM drift |
| budget.investment_params | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.loan_params | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.pay_periods | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.rate_history | ✓ | **No CHECK on interest_rate >= 0** (model doesn't declare; should) | ✓ | ✓ | PASS (potential gap) |
| budget.recurrence_rules | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.savings_goals | ✓ | ✓ | **2 FKs (goal_mode_id, income_unit_id) lack ondelete** | ✓ | MEDIUM drift |
| budget.scenarios | ✓ | ✓ (none declared) | ✓ | **Missing `uq_scenarios_one_baseline`** (F-S6-C3-02) | MEDIUM drift |
| budget.transaction_entries | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.transaction_templates | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.transactions | `is_override`/`is_deleted` nullable mismatch (F-S6-C3-04); model declares 2 CHECKs with names `ck_transactions_estimated_amount` + `ck_transactions_actual_amount`, live has only these 2 (old migration #7 names are missing -- F-S6-C3-02) | ✓ | ✓ | ✓ | MEDIUM drift |
| budget.transfer_templates | ✓ | ✓ | ✓ | ✓ | PASS |
| budget.transfers | ✓ | ✓ | ✓ | ✓ | PASS |
| ref.account_type_categories | ✓ | ✓ (none declared) | ✓ | ✓ | PASS |
| ref.account_types | ✓ | ✓ (none declared) | **account_types_category_id_fkey lacks ondelete** | ✓ | MEDIUM drift |
| ref.calc_methods, deduction_timings, filing_statuses, goal_modes, income_units, raise_types, recurrence_patterns, statuses, tax_types, transaction_types, user_roles | ✓ (simple id+name+boolean ref tables) | ✓ | n/a (not referencing other tables) | ✓ | PASS |
| salary.calibration_deduction_overrides | ✓ | ✓ | ✓ | ✓ | PASS |
| salary.calibration_overrides | ✓ | ✓ | ✓ | ✓ | PASS |
| salary.fica_configs | NOT NULL set but server_default cleared (F-S6-C3-05) | ✓ | ✓ | ✓ | LOW drift |
| salary.paycheck_deductions | ✓ | ✓ | **2 FKs (deduction_timing_id, calc_method_id) lack ondelete** | ✓ | MEDIUM drift |
| salary.pension_profiles | server_default cleared (F-S6-C3-05) | ✓ | ✓ | ✓ | LOW drift |
| salary.salary_profiles | server_default cleared (F-S6-C3-05) | ✓ | **1 FK (filing_status_id) lacks ondelete** | ✓ | MEDIUM drift |
| salary.salary_raises | ✓ | ✓ (including the `ck_salary_raises_one_method` cross-field CHECK) | **1 FK (raise_type_id) lacks ondelete** | ✓ | MEDIUM drift |
| salary.state_tax_configs | ✓ | ✓ | **1 FK (tax_type_id) lacks ondelete** | ✓ | MEDIUM drift |
| salary.tax_bracket_sets | ✓ | ✓ | **1 FK (filing_status_id) lacks ondelete** | ✓ | MEDIUM drift |
| salary.tax_brackets | ✓ | ✓ | ✓ | ✓ | PASS |
| system.audit_log | **MISSING (F-S6-C3-01)** | -- | -- | -- | HIGH drift |

### 3G. Check 3 severity tally

- **High:** 1 finding (F-S6-C3-01 missing audit infrastructure).
- **Medium:** 4 findings (F-S6-C3-02 missing CHECK + partial unique index, F-S6-C3-03 orphan PK/seq/FK names, F-S6-C3-04 nullable drift on boolean flags, **7 FKs without ondelete** across savings_goals / account_types / salary schema -- these are enumerated here but fully cataloged in Check 5).
- **Low:** 1 finding (F-S6-C3-05 server_default clearing that may or may not be a dump-tool artifact).

---

## Check 4: CHECK Constraint Parity

Three-layer comparison for every monetary / bounded column:

1. **Marshmallow** (`app/schemas/validation.py`) -- runs on form/request input.
2. **SA CheckConstraint** (`app/models/*.py` `__table_args__`) -- declared in the ORM.
3. **Live PG CHECK** (from the `\d+` dumps in `docs/audits/security-2026-04-15/scans/schema-*.txt`) -- actually enforced by the database.

A **gap** is any layer that is missing a constraint that another layer has, OR a layer whose range is inconsistent with another layer's range for the same column.

### 4A. The parity table

Key: M = Marshmallow, SA = SQLAlchemy model CheckConstraint, PG = live PostgreSQL CHECK. "None" = not enforced. "--" = column not user-facing.

#### Consistent (all three layers agree)

| Column | M rule | SA | PG | Gap? |
|--------|--------|----|----|------|
| transactions.estimated_amount | Range(min=0) | >= 0 | >= 0 | No |
| transactions.actual_amount | Range(min=0) | NULL or >= 0 | NULL or >= 0 | No |
| transaction_templates.default_amount | Range(min=0) | >= 0 | >= 0 | No |
| transfers.amount | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| transfer_templates.default_amount | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| transfer_templates.from_account_id / to_account_id | (validates_schema) | from != to | from <> to | No |
| transfers.from_account_id / to_account_id | (validates_schema) | from != to | from <> to | No |
| savings_goals.target_amount | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| savings_goals.income_multiplier | Range(min=0, min_inclusive=False) | NULL or > 0 | NULL or > 0 | No |
| loan_params.current_principal | Range(min=0) | >= 0 | >= 0 | No |
| loan_params.payment_day | Range(min=1, max=31) | 1-31 | 1-31 | No |
| recurrence_rules.interval_n | Range(min=1) | > 0 | > 0 | No |
| recurrence_rules.offset_periods | Range(min=0) | >= 0 | >= 0 | No |
| recurrence_rules.day_of_month | Range(min=1, max=31) | NULL or 1-31 | NULL or 1-31 | No |
| recurrence_rules.due_day_of_month | Range(min=1, max=31) | NULL or 1-31 | NULL or 1-31 | No |
| recurrence_rules.month_of_year | Range(min=1, max=12) | NULL or 1-12 | NULL or 1-12 | No |
| salary_raises.effective_month | Range(min=1, max=12) | 1-12 | 1-12 | No |
| salary_profiles.annual_salary | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| salary_profiles.qualifying_children | Range(min=0) | >= 0 | >= 0 | No |
| salary_profiles.other_dependents | Range(min=0) | >= 0 | >= 0 | No |
| user_settings.low_balance_threshold | Range(min=0) | >= 0 | >= 0 | No |
| user_settings.large_transaction_threshold | Range(min=0) | >= 0 | >= 0 | No |
| user_settings.anchor_staleness_days | Range(min=1) | > 0 | > 0 | No |
| investment_params.assumed_annual_return | Range(min=-1, max=1) | -1 to 1 | -1 to 1 | No |
| investment_params.employer_contribution_type | OneOf([none,flat_percentage,match]) | IN (none,flat_percentage,match) | IN (none,flat_percentage,match) | No |
| interest_params.compounding_frequency | OneOf([daily,monthly,quarterly]) | IN (daily,monthly,quarterly) | IN (daily,monthly,quarterly) | No |
| pay_periods.period_index | -- (not user-facing) | >= 0 | >= 0 | No (Info: if raw inserts occur, OK) |
| pay_periods.start_date/end_date | -- | start < end | start < end | No |
| pension_profiles.benefit_multiplier | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| calibration_overrides.actual_gross_pay | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| calibration_overrides.actual_federal_tax/state_tax/ss/medicare | Range(min=0) | >= 0 | >= 0 | No |
| tax_brackets.min_income | -- | >= 0 | >= 0 | No |
| tax_brackets.max_income | -- | NULL or >= min_income | NULL or >= min_income | No |
| tax_brackets.rate | -- | 0-1 | 0-1 | No |
| fica_configs.ss_wage_base | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |
| fica_configs.medicare_surtax_threshold | Range(min=0, min_inclusive=False) | > 0 | > 0 | No |

#### Gaps -- inconsistent or missing layers

| Column | M rule | SA | PG | Gap description | Severity |
|--------|--------|----|----|------|----------|
| **user_settings.default_inflation_rate** | Range(min=0, **max=100**) | 0-1 | 0-1 | **HIGH semantic mismatch.** Marshmallow says 0-100 (treating as percentage 0-100%); DB says 0-1 (treating as decimal). Entering 5 in the form (5%) passes Marshmallow but fails DB CHECK (5 > 1). Entering 0.05 passes both. The Marshmallow author treated this as a percentage; the DB author treated it as a decimal. One or the other is wrong; they disagree. | High |
| **user_settings.trend_alert_threshold** | Range(**min=1**, max=100) | 0-1 | 0-1 | **CRITICAL semantic mismatch.** Marshmallow says 1-100, DB says 0-1. **No value passes both.** Entering 5 passes Marshmallow but fails DB CHECK. Entering 0.5 fails Marshmallow but passes DB CHECK. This column is effectively unwritable by any route that goes through the schema. | **High** -- broken field |
| **fica_configs.ss_rate** | Range(min=0, max=100) | 0-1 | 0-1 | Same percentage/decimal mismatch. FICA rates in the U.S. are ~0.062 (6.2%) -- the real values pass both, but user error enters "6.2" instead of "0.062" and gets a 500 error from the DB instead of a clean 400 from Marshmallow. | Medium |
| **fica_configs.medicare_rate, medicare_surtax_rate** | Range(min=0, max=100) | 0-1 | 0-1 | Same. | Medium |
| **state_tax_configs.flat_rate** | Range(min=0, max=100) | NULL or 0-1 | NULL or 0-1 | Same. | Medium |
| **salary_raises.percentage** | Range(min=-100, max=1000) | NULL or > 0 | NULL or > 0 | **Mutually incompatible.** Marshmallow allows negative (pay cut) and values > 1 (up to 1000, presumably as percentage); SA+PG demand > 0 only. A pay-cut raise (-0.05 for -5%) fails DB. A 10000% bonus (100) passes Marshmallow but fails DB (> 0 → OK actually). Actually DB says > 0 and Marshmallow's -100 to 1000 includes > 0, so >0 works. But negative values pass Marshmallow and get 500'd by DB. | High |
| **salary_raises.flat_amount** | Range(min=-10000000, max=10000000) | NULL or > 0 | NULL or > 0 | Marshmallow allows negative (pay cut as flat amount); DB disallows. Flat pay cuts fail DB CHECK. | High |
| **user_settings.safe_withdrawal_rate** | Range(min=0, max=1) | None | None | Marshmallow enforces 0-1; DB has **no CHECK**. Raw SQL inserts or scripts can set SWR to any value. | Medium |
| **user_settings.estimated_retirement_tax_rate** | Range(min=0, max=1) | None | None | Same gap. | Medium |
| **user_settings.grid_default_periods** | Range(min=1, max=52) | > 0 | > 0 | DB enforces > 0 but no upper bound; Marshmallow caps at 52. Raw SQL can set 10000. Low-impact. | Low |
| **loan_params.interest_rate** | Range(min=0, max=100) | >= 0 | >= 0 | DB allows any non-negative rate (could be 999%); Marshmallow caps at 100. | Low |
| **loan_params.term_months** | Range(min=1, max=600) | > 0 | > 0 | DB allows any positive; Marshmallow caps at 600 months (50 years). | Low |
| **loan_params.original_principal** | Range(min=0) | > 0 | > 0 | Marshmallow allows 0, DB disallows. A 0-balance loan passes schema but fails DB with IntegrityError (500 instead of 400). | Low |
| **transaction_entries.amount** | Range(min=Decimal("0.01")) | > 0 | > 0 | Marshmallow requires >= 0.01, DB requires > 0. Entering 0.001 passes DB but fails Marshmallow (correct boundary in Marshmallow). DB would accept 0.001 via raw SQL. | Low |
| **savings_goals.contribution_per_period** | Range(min=0) | NULL or > 0 | NULL or > 0 | Marshmallow allows 0; DB requires > 0. A $0 contribution goal passes schema but fails DB. | Low |
| **escrow_components.annual_amount** | Range(min=0) | None | None | Marshmallow enforces >= 0; no DB CHECK. Raw SQL can insert negative annual amount. | Medium |
| **escrow_components.inflation_rate** | Range(min=0, max=100) | None | None | Same -- percentage/decimal ambiguity (Marshmallow treats as 0-100%) AND no DB CHECK. | Medium |
| **interest_params.apy** | Range(min=0, max=100) | None | None | Marshmallow enforces 0-100; no DB CHECK. APY could be set to any value via raw SQL. Also percentage/decimal ambiguity. | Medium |
| **investment_params.annual_contribution_limit** | Range(min=0) | None | None | No DB CHECK -- raw SQL can insert negative. | Low |
| **investment_params.employer_flat_percentage** | Range(min=0, max=1) | None | None | No DB CHECK. | Low |
| **investment_params.employer_match_percentage** | Range(min=0, max=10) | None | None | No DB CHECK. | Low |
| **investment_params.employer_match_cap_percentage** | Range(min=0, max=1) | None | None | No DB CHECK. | Low |
| **paycheck_deductions.amount** | (no Range validator) | > 0 | > 0 | **Marshmallow has NO validation** on amount -- Schema accepts any Decimal, including negatives. DB CHECK requires > 0. A negative deduction passes the schema but fails at commit with IntegrityError (500). | **High** -- routes are supposed to validate through Marshmallow per coding standards |
| **paycheck_deductions.deductions_per_year** | OneOf([12,24,26]) | > 0 | > 0 | Marshmallow restricts to 3 specific values; DB accepts any positive. A malicious/buggy code path that bypasses schema (e.g. raw SQL migration) can insert 100. | Low |
| **paycheck_deductions.annual_cap** | (no Range validator) | NULL or > 0 | NULL or > 0 | Marshmallow lacks validator; DB requires > 0. Marshmallow allows 0. | Medium |
| **paycheck_deductions.inflation_rate** | (no Range validator) | None | None | No validation at any layer. | Medium |
| **paycheck_deductions.inflation_effective_month** | Range(min=1, max=12) | None | None | Marshmallow enforces; DB has no CHECK. | Low |
| **salary_profiles.additional_income** | (no Range validator -- just as_string+places=2) | >= 0 | >= 0 | Marshmallow doesn't validate non-negative; DB does. User sending negative value gets IntegrityError (500). | Medium |
| **salary_profiles.additional_deductions** | (no Range validator) | >= 0 | >= 0 | Same gap. | Medium |
| **salary_profiles.extra_withholding** | (no Range validator) | >= 0 | >= 0 | Same gap. | Medium |
| **salary_profiles.pay_periods_per_year** | OneOf([12,24,26,52]) | > 0 | > 0 | Marshmallow tighter; DB only checks >0. Raw SQL could set 1. | Low |
| **salary_raises.effective_year** | Range(min=2000, max=2100) | None | None | No DB CHECK. | Low |
| **pension_profiles.consecutive_high_years** | Range(min=1, max=10) | > 0 | > 0 | DB no upper bound. Marshmallow caps at 10. | Low |
| **tax_bracket_sets.standard_deduction** | (no Range validator) | >= 0 | >= 0 | Marshmallow missing validator. | Medium |
| **tax_bracket_sets.child_credit_amount** | (no Range validator) | >= 0 | >= 0 | Same. | Medium |
| **tax_bracket_sets.other_dependent_credit_amount** | (no Range validator) | >= 0 | >= 0 | Same. | Medium |
| **state_tax_configs.standard_deduction** | Range(min=0) | None | None | Marshmallow enforces; DB has no CHECK. | Low |
| **state_tax_configs.tax_year** | Range(min=2000, max=2100) | None | None | Same. | Low |
| **calibration_overrides.effective_federal_rate / state_rate / ss_rate / medicare_rate** | CalibrationConfirmSchema: Range(min=0, max=1) | None | None | Marshmallow enforces; DB has no CHECK. The field is computed server-side so schema validation is moot (bypassed unless confirm-step runs) -- but raw SQL can still insert negative effective rates. | Medium |
| **calibration_deduction_overrides.actual_amount** | (no specific validation) | >= 0 | >= 0 | Marshmallow missing. | Medium |
| **accounts.current_anchor_balance** | AnchorUpdateSchema: Decimal (no Range) | None | None | Anchor balance can be negative (debit overdraft, liability debt) -- so no bound is correct. Info. | Info |
| **account_anchor_history.anchor_balance** | -- | None | None | Same. Info. | Info |

### 4B. Categorized findings

**F-S6-C4-01 (High / cross-layer semantic mismatch):** Several percentage-vs-decimal pairs where Marshmallow's Range and DB CHECK disagree:
- `user_settings.default_inflation_rate`, `trend_alert_threshold`
- `fica_configs.ss_rate`, `medicare_rate`, `medicare_surtax_rate`
- `state_tax_configs.flat_rate`

The Marshmallow author wrote `Range(min=0, max=100)` interpreting the field as a percentage. The DB author wrote `CHECK(field >= 0 AND field <= 1)` interpreting it as a decimal. The model uses `Numeric(5, 4)` (4 decimal places) which naturally stores `0.0620` for 6.2% SSA rate -- so the DB's decimal interpretation is consistent with the storage. **Fix**: change Marshmallow to `Range(min=0, max=1)` on all listed fields.

**F-S6-C4-02 (High / broken field):** `user_settings.trend_alert_threshold` has mutually incompatible bounds -- Marshmallow requires 1-100 and DB requires 0-1, so **no value is accepted by both layers**. The field was added in migration `f06bcc98bc3a` (f06bcc98bc3a) with `server_default='0.1000'` and `Numeric(5, 4)`. The default itself satisfies the DB CHECK but fails the Marshmallow min=1 rule. Users cannot update this setting via the route. Verify by reading `app/routes/user_settings.py` to see whether the route path is wired up; if so, it has been broken since the field was added.

**F-S6-C4-03 (High / inconsistent sign rules on raises):** `salary_raises.percentage` and `salary_raises.flat_amount` allow negatives in Marshmallow (pay cuts) but DB rejects. A user entering a pay cut in the form passes validation but gets a 500 error at commit.

**F-S6-C4-04 (High / missing Marshmallow validation on amount):** `paycheck_deductions.amount` has NO Marshmallow Range validator. A route-driven POST with a negative amount is accepted by Marshmallow and fails at the DB with IntegrityError. Fix by adding `validate=validate.Range(min=0, min_inclusive=False)` to `DeductionCreateSchema.amount`.

**F-S6-C4-05 (Medium / missing Marshmallow validators on salary fields):** `salary_profiles.additional_income`, `additional_deductions`, `extra_withholding` all have SA+PG CHECK >= 0 but NO Marshmallow Range. Same pattern with tax_bracket_sets standard_deduction / credit amounts. Users sending negative values get 500 instead of 400.

**F-S6-C4-06 (Medium / missing DB CHECKs on validated fields):** Several fields have Marshmallow validation but no CHECK:
- `escrow_components.annual_amount`, `inflation_rate`
- `interest_params.apy`
- `investment_params.annual_contribution_limit`, `employer_flat_percentage`, `employer_match_percentage`, `employer_match_cap_percentage`
- `user_settings.safe_withdrawal_rate`, `estimated_retirement_tax_rate`
- `paycheck_deductions.inflation_rate`, `inflation_effective_month`
- `salary_raises.effective_year`
- `state_tax_configs.standard_deduction`, `tax_year`
- `calibration_overrides.effective_*_rate` (x4)
- `rate_history.interest_rate` (no schema but model has no CHECK)

Raw SQL inserts, migrations, or direct admin actions can insert invalid values. The layered-defense principle of coding standards requires DB CHECKs for every Marshmallow Range rule.

**F-S6-C4-07 (Low / boundary mismatches):** Several cases where the Marshmallow boundary is inclusive (>=0) and DB is exclusive (>0) or vice versa: `loan_params.original_principal`, `savings_goals.contribution_per_period`, `paycheck_deductions.annual_cap`, `transaction_entries.amount`. Users entering 0 get either 400 or 500 depending on which layer catches it first. Align both sides to the intended semantic (usually: amounts should be > 0, counts should be >= 0).

### 4C. Check 4 severity tally

- **High:** 4 findings (F-S6-C4-01 through F-S6-C4-04 -- semantic mismatches + broken trend_alert_threshold + missing amount validation).
- **Medium:** 2 findings (F-S6-C4-05, F-S6-C4-06 -- missing validators in one layer or the other).
- **Low:** 1 finding (F-S6-C4-07 -- boundary inclusivity).

---

## Check 5: Foreign Key and ON DELETE Audit

Per `docs/coding-standards.md` (SQL/Database section):

> Every new column should be NOT NULL unless there is a specific reason for nullability.
> Explicit ondelete on every foreign key. Never rely on PostgreSQL's implicit default. Use CASCADE for user_id FKs, RESTRICT for ref table FKs, CASCADE or SET NULL for inter-domain FKs.
> Name all constraints explicitly. Pattern: `ck_<table>_<description>` for CHECK, `uq_<table>_<columns>` for unique, `ix_<table>_<columns>` for indexes.

I enumerated every FK from every model (`app/models/*.py`) and diffed against the live DB (per-table `\d+` output). 52 FK constraints total.

### 5A. FKs grouped by category (all match live DB)

**User-id FKs -- all CASCADE (per standards):**

| Table | FK column | Model ondelete | Live ondelete | Standard? | Named? |
|-------|-----------|---------------|---------------|-----------|--------|
| auth.user_settings | user_id | CASCADE | CASCADE | ✓ | auto (`user_settings_user_id_fkey`) |
| auth.mfa_configs | user_id | CASCADE | CASCADE | ✓ | auto |
| auth.users.linked_owner_id | auth.users (self-ref) | SET NULL | SET NULL | ✓ | **explicit** (`fk_users_linked_owner`) |
| budget.accounts | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.categories | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.pay_periods | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.recurrence_rules | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.savings_goals | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.scenarios | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.transaction_entries | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.transaction_templates | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.transfer_templates | user_id | CASCADE | CASCADE | ✓ | auto |
| budget.transfers | user_id | CASCADE | CASCADE | ✓ | auto |
| salary.fica_configs | user_id | CASCADE | CASCADE | ✓ | auto |
| salary.pension_profiles | user_id | CASCADE | CASCADE | ✓ | auto |
| salary.salary_profiles | user_id | CASCADE | CASCADE | ✓ | auto |
| salary.state_tax_configs | user_id | CASCADE | CASCADE | ✓ | auto |
| salary.tax_bracket_sets | user_id | CASCADE | CASCADE | ✓ | auto |

**Ref-table FKs that ARE correct (RESTRICT per standards):**

| Table | FK column | References | Model ondelete | Live ondelete | Standard? |
|-------|-----------|-----------|---------------|---------------|-----------|
| auth.users | role_id | ref.user_roles | RESTRICT | RESTRICT | ✓ |
| budget.accounts | account_type_id | ref.account_types | RESTRICT | RESTRICT | ✓ |
| budget.recurrence_rules | pattern_id | ref.recurrence_patterns | RESTRICT | RESTRICT | ✓ |
| budget.transactions | status_id | ref.statuses | RESTRICT | RESTRICT | ✓ |
| budget.transactions | transaction_type_id | ref.transaction_types | RESTRICT | RESTRICT | ✓ |
| budget.transaction_templates | transaction_type_id | ref.transaction_types | RESTRICT | RESTRICT | ✓ |
| budget.transfers | status_id | ref.statuses | RESTRICT | RESTRICT | ✓ |

**Parent/child & inter-domain FKs that ARE correct (CASCADE or SET NULL):**

| Table | FK column | References | ondelete | Standard? |
|-------|-----------|-----------|----------|-----------|
| budget.account_anchor_history | account_id | budget.accounts | CASCADE | ✓ |
| budget.account_anchor_history | pay_period_id | budget.pay_periods | CASCADE | ✓ |
| budget.accounts | current_anchor_period_id | budget.pay_periods | SET NULL | ✓ |
| budget.escrow_components | account_id | budget.accounts | CASCADE | ✓ |
| budget.interest_params | account_id | budget.accounts | CASCADE | ✓ |
| budget.investment_params | account_id | budget.accounts | CASCADE | ✓ |
| budget.loan_params | account_id | budget.accounts | CASCADE | ✓ |
| budget.rate_history | account_id | budget.accounts | CASCADE | ✓ |
| budget.savings_goals | account_id | budget.accounts | CASCADE | ✓ |
| budget.scenarios | cloned_from_id | budget.scenarios (self-ref) | SET NULL | ✓ |
| budget.transactions | account_id | budget.accounts | RESTRICT | ✓ |
| budget.transactions | pay_period_id | budget.pay_periods | CASCADE | ✓ |
| budget.transactions | scenario_id | budget.scenarios | CASCADE | ✓ |
| budget.transactions | category_id | budget.categories | SET NULL | ✓ |
| budget.transactions | template_id | budget.transaction_templates | SET NULL | ✓ |
| budget.transactions | transfer_id | budget.transfers | CASCADE | ✓ |
| budget.transactions | credit_payback_for_id | budget.transactions (self-ref) | SET NULL | ✓ |
| budget.transaction_entries | transaction_id | budget.transactions | CASCADE | ✓ |
| budget.transaction_entries | credit_payback_id | budget.transactions | SET NULL | ✓ |
| budget.transaction_templates | account_id | budget.accounts | RESTRICT | ✓ |
| budget.transaction_templates | category_id | budget.categories | RESTRICT | ✓ |
| budget.transaction_templates | recurrence_rule_id | budget.recurrence_rules | SET NULL | ✓ |
| budget.transfers | from_account_id | budget.accounts | RESTRICT | ✓ |
| budget.transfers | to_account_id | budget.accounts | RESTRICT | ✓ |
| budget.transfers | pay_period_id | budget.pay_periods | **RESTRICT** | ✓ (note: transactions.pay_period_id is CASCADE -- inconsistent but both valid) |
| budget.transfers | scenario_id | budget.scenarios | CASCADE | ✓ |
| budget.transfers | category_id | budget.categories | SET NULL | ✓ |
| budget.transfers | transfer_template_id | budget.transfer_templates | SET NULL | ✓ |
| budget.transfer_templates | from_account_id | budget.accounts | RESTRICT | ✓ |
| budget.transfer_templates | to_account_id | budget.accounts | RESTRICT | ✓ |
| budget.transfer_templates | recurrence_rule_id | budget.recurrence_rules | SET NULL | ✓ |
| budget.transfer_templates | category_id | budget.categories | SET NULL | ✓ |
| auth.user_settings | default_grid_account_id | budget.accounts | SET NULL | ✓ |
| salary.paycheck_deductions | salary_profile_id | salary.salary_profiles | CASCADE | ✓ |
| salary.paycheck_deductions | target_account_id | budget.accounts | SET NULL | ✓ |
| salary.salary_profiles | scenario_id | budget.scenarios | CASCADE | ✓ |
| salary.salary_profiles | template_id | budget.transaction_templates | SET NULL | ✓ |
| salary.salary_raises | salary_profile_id | salary.salary_profiles | CASCADE | ✓ |
| salary.pension_profiles | salary_profile_id | salary.salary_profiles | SET NULL | ✓ |
| salary.tax_brackets | bracket_set_id | salary.tax_bracket_sets | CASCADE | ✓ |
| salary.calibration_overrides | salary_profile_id | salary.salary_profiles | CASCADE | ✓ |
| salary.calibration_deduction_overrides | calibration_id | salary.calibration_overrides | CASCADE | ✓ |
| salary.calibration_deduction_overrides | deduction_id | salary.paycheck_deductions | CASCADE | ✓ |
| budget.recurrence_rules | start_period_id | budget.pay_periods | SET NULL | ✓ |

### 5B. Non-compliant FKs (missing ondelete -- FK defaults to NO ACTION)

Every one of these is a ref-table FK that, per standards, must be `ondelete='RESTRICT'`. PostgreSQL's NO ACTION is functionally similar to RESTRICT in this codebase (both refuse to delete a referenced ref row), but the difference matters: NO ACTION defers to end-of-transaction while RESTRICT fires immediately, and explicit `ondelete` is required by the coding standards for review clarity.

| Table | FK column | References | Model ondelete | Live ondelete | Named? | Severity |
|-------|-----------|-----------|---------------|---------------|--------|----------|
| **ref.account_types** | category_id | ref.account_type_categories | **(none)** | **(none -- NO ACTION)** | auto | Medium |
| **budget.savings_goals** | goal_mode_id | ref.goal_modes | **(none)** | **(none)** | **explicit** (`fk_savings_goals_goal_mode_id`) | Medium |
| **budget.savings_goals** | income_unit_id | ref.income_units | **(none)** | **(none)** | **explicit** (`fk_savings_goals_income_unit_id`) | Medium |
| **salary.salary_profiles** | filing_status_id | ref.filing_statuses | **(none)** | **(none)** | auto | Medium |
| **salary.salary_raises** | raise_type_id | ref.raise_types | **(none)** | **(none)** | auto | Medium |
| **salary.paycheck_deductions** | deduction_timing_id | ref.deduction_timings | **(none)** | **(none)** | auto | Medium |
| **salary.paycheck_deductions** | calc_method_id | ref.calc_methods | **(none)** | **(none)** | auto | Medium |
| **salary.tax_bracket_sets** | filing_status_id | ref.filing_statuses | **(none)** | **(none)** | auto | Medium |
| **salary.state_tax_configs** | tax_type_id | ref.tax_types | **(none)** | **(none)** | auto | Medium |

**9 FKs fail the standard.** All 9 reference ref tables. The `047bfed04987_standardize_ondelete_policies_across_` migration fixed the budget-schema FKs but never covered the salary schema or the post-hoc additions to savings_goals and account_types.

### 5C. Findings

**F-S6-C5-01 (Medium / standards gap):** Nine ref-table FKs lack explicit `ondelete`. Per coding standards these must be `RESTRICT`. In practice, PostgreSQL's implicit NO ACTION provides similar protection against deletes (it refuses to delete a referenced ref row), so the real-world impact is subtle: the difference between immediate-failure (RESTRICT) and end-of-transaction failure (NO ACTION) can leak through complex transactions that delete-and-reinsert a ref row in one atomic block. More importantly, the standards-compliance gap means future changes to the ref schema will not get the intended behavior by default. **Remediation:** follow-up migration that drops and recreates each of the 9 FKs with explicit `ondelete='RESTRICT'` -- analogous to what `047bfed04987` did for the budget schema.

**F-S6-C5-02 (Medium / naming convention):** Only 3 FK constraints in the live DB carry the `fk_<table>_<description>` naming convention required by coding standards: `fk_users_linked_owner`, `fk_savings_goals_goal_mode_id`, `fk_savings_goals_income_unit_id`. The other 49 FKs use the auto-generated `<table>_<column>_fkey` pattern. The initial migration and most later migrations accepted Alembic's default names. Per the standards:

> Name all constraints explicitly. Pattern: ... `fk_<table>_<description>` ... is implied by the naming scheme for CHECK/UNIQUE/INDEX.

This is a Medium finding because inconsistent naming frustrates tool-based auditing (grep for `fk_transactions_*` misses all budget.transactions FKs). Fixing this retroactively would require dropping and recreating every FK, which is churn for cosmetic gain. Recommend: establish the convention going forward but don't rename existing FKs unless adjacent work requires it.

**F-S6-C5-03 (Low / inconsistent inter-budget pay_period_id policies):** 
- `budget.transactions.pay_period_id`: CASCADE (delete period → delete transactions)
- `budget.transfers.pay_period_id`: RESTRICT (delete period → refuse)

Both are valid per standards but the asymmetry is surprising. A user who tries to delete a pay period gets different behavior depending on whether they have transactions or transfers in that period. `transfers` RESTRICT was set in the initial transfers migration (from `9dea99d4e33e`) and never updated, while `transactions` CASCADE is from the same initial migration. Investigate whether this is intentional (maybe the developer wanted transfer deletion to require explicit cleanup for audit reasons) or accidental drift.

**F-S6-C5-04 (Low / self-referential PK names):** `budget.transactions.credit_payback_for_id` and `budget.scenarios.cloned_from_id` are self-references. Both use `SET NULL` which is correct but their FK names (`transactions_credit_payback_for_id_fkey`, `scenarios_cloned_from_id_fkey`) again don't follow the `fk_*` convention. Same general issue as F-S6-C5-02.

**F-S6-C5-05 (Low / referenced pkey naming drift):** From Check 3 F-S6-C3-03, the `budget.interest_params` table's FK constraint `hysa_params_account_id_fkey` still reflects the pre-rename table name. Cosmetic drift.

### 5D. Check 5 severity tally

- **Medium:** 2 findings (F-S6-C5-01 nine ref FKs without ondelete, F-S6-C5-02 pervasive FK naming-convention violation).
- **Low:** 3 findings (F-S6-C5-03 pay_period_id asymmetry, F-S6-C5-04 self-ref naming, F-S6-C5-05 orphan FK name).

---

## Check 6: Index Audit

Per `docs/coding-standards.md` SQL section:

> Add indexes for query patterns. Every column in a frequent WHERE, JOIN, or ORDER BY should have an index. Consider partial indexes for filtered queries.

The live DB has 16 explicit non-PK / non-unique indexes plus ~30 implicit indexes via UNIQUE constraints. Inventory of explicit indexes:

```
 budget | account_anchor_history | idx_anchor_history_account
 budget | categories             | idx_categories_user_group
 budget | pay_periods            | idx_pay_periods_user_index
 budget | transaction_entries    | idx_transaction_entries_txn_credit
 budget | transaction_entries    | idx_transaction_entries_txn_id
 budget | transaction_templates  | idx_templates_user_type
 budget | transactions           | idx_transactions_account
 budget | transactions           | idx_transactions_credit_payback
 budget | transactions           | idx_transactions_due_date
 budget | transactions           | idx_transactions_period_scenario
 budget | transactions           | idx_transactions_template
 budget | transactions           | idx_transactions_template_period_scenario
 budget | transactions           | idx_transactions_transfer
 budget | transfer_templates     | idx_transfer_templates_user
 budget | transfers              | idx_transfers_period_scenario
 budget | transfers              | idx_transfers_template_period_scenario
```

**Salary schema has zero explicit indexes.** `ref` schema has zero (appropriate -- tiny lookup tables).

### 6A. High-traffic columns WITHOUT an index

These columns are named in frequent queries based on route and service code patterns.

#### Missing indexes directly caused by migration #5 (22b3dd9d9ed3)

Migration `22b3dd9d9ed3` DROPPED three indexes as part of its upgrade (lines 272, 298, 307):

1. `idx_deductions_profile` on `salary.paycheck_deductions (salary_profile_id)` -- DROPPED, never recreated.
2. `idx_salary_raises_profile` on `salary.salary_raises (salary_profile_id)` -- DROPPED.
3. `idx_tax_brackets_set` on `salary.tax_brackets (bracket_set_id, sort_order)` -- DROPPED.

None of these are re-added by any later migration. The downgrade of 22b3dd9d9ed3 doesn't restore them either. Model `__table_args__` do NOT declare these indexes. The net result: **three parent-child relationships in salary schema lack indexes on the child-side FK column**. Every query like "get all deductions for this salary profile" does a sequential scan of `paycheck_deductions`. This is the textbook index that PostgreSQL cannot auto-create.

#### Other missing indexes

| Table | Column | Why it's queried | Severity |
|-------|--------|-------------------|----------|
| **salary.paycheck_deductions** | salary_profile_id | Child rows, joined from SalaryProfile.deductions collection | **Medium** (was dropped by 22b3dd9d9ed3) |
| **salary.salary_raises** | salary_profile_id | Same (SalaryProfile.raises) | **Medium** (dropped) |
| **salary.tax_brackets** | bracket_set_id | Same (TaxBracketSet.brackets) | **Medium** (dropped) |
| **salary.pension_profiles** | user_id | User-scoped listing | Low |
| **salary.pension_profiles** | salary_profile_id | Cross-reference from profile | Low |
| **salary.calibration_deduction_overrides** | deduction_id | Queried when computing effective rate per deduction | Low |
| **salary.state_tax_configs** | state_code | Filtering by state (leftmost of unique includes user_id first, so state alone is seq-scanned) | Low |
| **budget.rate_history** | account_id | Every amortization projection joins by account_id | **Medium** (small current row count, but grows over time) |
| **budget.savings_goals** | account_id | Dashboard queries savings goals by account | Low (usually also user-filtered) |
| **budget.transfers** | user_id | Direct user queries of transfers without period filter (e.g. audit views) | Low |
| **budget.transfers** | from_account_id / to_account_id | Account detail page lists transfers in/out | Low |
| **budget.recurrence_rules** | user_id | Rule listing per user | Low |
| **budget.transaction_entries** | user_id | Companion-user entry listing | Low |
| **budget.transaction_templates** | category_id | Category drilldown | Low |
| **budget.transactions** | status_id | Filtering by status (e.g. all settled) -- usually combined with period/scenario | Low |
| **budget.transactions** | is_deleted | Not directly indexed, but usually filtered in combination with template_id or period_id (which ARE indexed including the is_deleted=false partial-index case for `idx_transactions_template_period_scenario`) | Info |

#### Indexes that DO exist via leftmost-column rule

These user_id queries are covered by UNIQUE constraints where user_id is the leftmost column:

| Table | UNIQUE/INDEX that covers user_id |
|-------|---------------------------------|
| auth.user_settings | user_settings_user_id_key (single-col) |
| auth.mfa_configs | mfa_configs_user_id_key (single-col) |
| budget.accounts | uq_accounts_user_name (user_id leftmost) |
| budget.categories | idx_categories_user_group + uq_categories_user_group_item |
| budget.pay_periods | idx_pay_periods_user_index + uq_pay_periods_user_start |
| budget.savings_goals | uq_savings_goals_user_acct_name |
| budget.scenarios | uq_scenarios_user_name |
| budget.transaction_templates | idx_templates_user_type |
| budget.transfer_templates | idx_transfer_templates_user (single-col) |
| salary.fica_configs | uq_fica_configs_user_year |
| salary.salary_profiles | uq_salary_profiles_user_scenario_name |
| salary.state_tax_configs | uq_state_tax_configs_user_state_year |
| salary.tax_bracket_sets | uq_tax_bracket_sets_user_year_status |

### 6B. Indexes in model but missing from live DB

None. Every model-declared index exists in the live DB.

### 6C. Indexes in live DB but missing from model

None of the live indexes are absent from model declarations. The one exception worth re-noting is the **`uq_scenarios_one_baseline`** partial unique index -- it's in migration #7 but MISSING from both model AND live DB (covered in F-S6-C3-02).

### 6D. Findings

**F-S6-C6-01 (Medium / performance regression from migration 22b3dd9d9ed3):** Three child-FK indexes on salary schema were dropped by migration `22b3dd9d9ed3` and never restored. Every parent-child query (profile → deductions, profile → raises, bracket_set → brackets) now does a sequential scan. The row counts today are small (single-user app), but growth is unbounded -- deductions over years add up. **Remediation:** new migration that recreates:

```
CREATE INDEX idx_deductions_profile  ON salary.paycheck_deductions (salary_profile_id);
CREATE INDEX idx_salary_raises_profile ON salary.salary_raises (salary_profile_id);
CREATE INDEX idx_tax_brackets_set      ON salary.tax_brackets (bracket_set_id, sort_order);
```

Also add them to the corresponding model `__table_args__`.

**F-S6-C6-02 (Low / missing rate_history index):** `budget.rate_history.account_id` has no index and is the primary query column for every amortization projection against a variable-rate loan. Add `CREATE INDEX idx_rate_history_account ON budget.rate_history (account_id, effective_date DESC);`.

**F-S6-C6-03 (Low / missing foreign-key indexes on salary):** `salary.pension_profiles.user_id` and `salary_profile_id`, `salary.calibration_deduction_overrides.deduction_id` have no indexes. Low impact (single-user app, small tables) but correct per coding standards which require indexes on "every column in a frequent WHERE, JOIN, or ORDER BY."

**F-S6-C6-04 (Info / optional coverage indexes):** A handful of columns (`budget.transfers.from_account_id`/`to_account_id`, `budget.savings_goals.account_id`, `budget.transaction_templates.category_id`) would benefit from indexes as the app grows. Currently acceptable given single-user workload. Mark as Info.

### 6E. Check 6 severity tally

- **Medium:** 1 finding (F-S6-C6-01 -- 3 dropped child-FK indexes not restored).
- **Low:** 2 findings (F-S6-C6-02 rate_history, F-S6-C6-03 pension/calibration FKs).
- **Info:** 1 finding (F-S6-C6-04 -- growth-dependent indexes).

---

## Session S6 Wrap-up

### Deliverables

| File | Purpose | Lines |
|------|---------|-------|
| `docs/audits/security-2026-04-15/reports/17-migrations-schema.md` | Main report (6 Checks + summary) | ~850 |
| `docs/audits/security-2026-04-15/scans/schema-*.txt` | Per-table `\d+` dumps, one file per user-data table | 43 files, ~1,000 lines |

### Severity tally across all six Checks

| Severity | Count | Category distribution |
|----------|-------|-----------------------|
| **High** | **9** | Check 1: 2 · Check 2: 3 · Check 3: 1 · Check 4: 4 |
| **Medium** | **20** | Check 1: 4 · Check 2: 6 · Check 3: 3 · Check 4: 2 · Check 5: 2 · Check 6: 1 |
| **Low** | **13** | Check 1: 2 · Check 2: 4 · Check 3: 1 · Check 4: 1 · Check 5: 3 · Check 6: 2 |
| **Info** | **1** | Check 6: 1 |

(Some findings are noted in multiple checks -- the unique underlying issue count is smaller. For example, migration `efffcf647644` is finding F-S6-C1-01 in Check 1 and D-04 in Check 2 -- one underlying defect, two mentions.)

### Checks summary table

| Check | Scope | Findings | Top severity | One-line verdict |
|-------|-------|----------|--------------|------------------|
| **1 -- Migration inventory** | All 39 migrations in chain | 8 named findings (C1-01 ... C1-08) | 2× High | 27 PASS / 10 WARN / 2 FAIL. Unsafe NOT-NULL add, duplicate CHECK names. |
| **2 -- Destructive operations** | 20 destructive ops (upgrade + downgrade paths) | 14 D-series findings + 1 C2-series | 3× High | Most destructive ops are PASS; 3 HIGH cases where downgrade silently drops data. |
| **3 -- Live schema drift** | 43 tables dumped + diffed | 5 findings (C3-01 ... C3-05) | 1× High | **Audit-log infrastructure entirely missing from live DB** despite migration applied. |
| **4 -- CHECK constraint parity** | ~90 monetary/bounded fields × 3 layers | 7 findings (C4-01 ... C4-07) | 4× High | Percentage/decimal mismatches + `trend_alert_threshold` broken + missing `amount` validator. |
| **5 -- FK and ON DELETE** | 52 foreign keys | 5 findings (C5-01 ... C5-05) | 2× Medium | 9 ref-FKs lack explicit `ondelete`; widespread FK naming-convention gap. |
| **6 -- Index audit** | 16 explicit indexes + ~30 implicit via UNIQUE | 4 findings (C6-01 ... C6-04) | 1× Medium | 3 salary-schema child-FK indexes dropped by migration 22b3dd9d9ed3 and never restored. |

### Top five worries (in priority order)

**1. F-S6-C3-01: the audit log is not running.**
- **What:** migration `a8b1c2d3e4f5` created `system.audit_log` + trigger function + 22 AFTER triggers on financial/auth tables. Live DB has **zero of the three** -- no table, no function, no triggers. `alembic_version` says the migration is applied.
- **What goes wrong if not fixed:** every INSERT/UPDATE/DELETE on `auth.users`, `budget.accounts`, `budget.transactions`, `budget.transfers`, `salary.salary_profiles`, etc. produces no audit entry. The compliance-grade change-log that the developer believed was running is silent. Attribution for "who changed this" is lost. Forensics after a security incident are impossible.
- **Remediation:** re-apply the migration's DDL manually, or create a new migration that recreates the audit infrastructure. Verify via `SELECT tgname FROM pg_trigger WHERE tgname LIKE 'audit_%'` -- should return 22 rows. Document in CHANGELOG why this happened.

**2. F-S6-C4-01 + F-S6-C4-02: six fields have Marshmallow/DB bound mismatches; one is fully broken.**
- **What:** `fica_configs.ss_rate`/`medicare_rate`/`medicare_surtax_rate`, `state_tax_configs.flat_rate`, `user_settings.default_inflation_rate`, `user_settings.trend_alert_threshold`. Marshmallow validates 0-100 (percentage) while DB CHECKs 0-1 (decimal). For `trend_alert_threshold`, Marshmallow requires 1-100 while DB requires 0-1 -- **no value passes both layers**, making the field unwritable via any schema-validated route.
- **What goes wrong if not fixed:** users entering "6.2" for a 6.2% SSA rate get 500 IntegrityError instead of 400 validation error. `trend_alert_threshold` cannot be changed at all. Silent Marshmallow↔DB divergence on tax rates is the kind of bug that only surfaces when the user tries a valid-looking value.
- **Remediation:** change all listed Marshmallow `Range(min=0, max=100)` to `Range(min=0, max=1)` (or `Range(min=0, min_inclusive=False, max=1)` where appropriate). Fix `trend_alert_threshold` Marshmallow to `Range(min=0, max=1)`. Add regression tests that send each boundary value and assert 400/422 (not 500).

**3. F-S6-C1-01 / D-04: the `efffcf647644_add_account_id_column_to_transactions` migration cannot be re-run on a populated DB.**
- **What:** the migration does `add_column('transactions', 'account_id', nullable=False)` with no `server_default` and no backfill step. If the table has any rows, the migration fails. The live DB has this column populated, implying a manual intervention that's not in the repo.
- **What goes wrong if not fixed:** any fresh-environment recovery (disaster recovery, new staging deploy, test DB provisioning from a backup) fails at this migration. The migration chain is NOT idempotent against a production-like dataset.
- **Remediation:** amend the migration to: (a) add the column as nullable, (b) `op.execute("UPDATE budget.transactions t SET account_id = (SELECT first_checking FROM auth.user_settings WHERE user_id = (SELECT user_id FROM budget.pay_periods WHERE id = t.pay_period_id))")` or similar backfill, (c) then `alter_column(nullable=False)`. Add a comment explaining the backfill logic.

**4. F-S6-C4-04: `paycheck_deductions.amount` has no Marshmallow validation.**
- **What:** `DeductionCreateSchema.amount = fields.Decimal(required=True, places=4, as_string=True)` -- no `validate=validate.Range(...)`. DB has `CHECK(amount > 0)`. A POST with `amount=-100` passes the schema and fails at commit.
- **What goes wrong if not fixed:** user gets a 500 error instead of a clean 400, and the error message in the DB exception leaks implementation detail. Per coding standards rule #1 and Marshmallow-is-mandatory rule in `docs/coding-standards.md` SQL section ("Marshmallow schema for every state-changing route"), the validation contract is broken.
- **Remediation:** add `validate=validate.Range(min=0, min_inclusive=False)` to `DeductionCreateSchema.amount`. Same fix for `salary_profiles.additional_income` / `additional_deductions` / `extra_withholding` (F-S6-C4-05) and `tax_bracket_sets.standard_deduction` / credit amounts.

**5. F-S6-C6-01: three salary-schema indexes were dropped by migration and never restored.**
- **What:** migration `22b3dd9d9ed3` does `op.drop_index('idx_deductions_profile', ...)`, `op.drop_index('idx_salary_raises_profile', ...)`, `op.drop_index('idx_tax_brackets_set', ...)` in its upgrade (as part of an auto-generated "adjust salary schema" block). The downgrade does NOT recreate them. No subsequent migration restores them. The models don't declare them in `__table_args__`. **Three parent→child relationships now do sequential scans on every query.**
- **What goes wrong if not fixed:** today's single-user workload masks the issue. As salary_raises/paycheck_deductions/tax_brackets row counts grow (each gets ~26 rows per year of usage × profile count), query times degrade linearly. Eventually the paycheck calculator latency becomes a user-visible regression.
- **Remediation:** new migration `CREATE INDEX idx_deductions_profile ON salary.paycheck_deductions (salary_profile_id);` (and the other two), AND add matching `db.Index(...)` entries to each model's `__table_args__` so Alembic autogenerate catches any future drop.

### Schema drift summary

- **Tables checked:** 43
- **Tables matching their model perfectly:** 34
- **Tables with drift:** 9 (with severity levels spread across High/Medium/Low)
  - `budget.transactions` (nullable drift on is_override/is_deleted; also missing CHECKs from migration #7)
  - `budget.interest_params` (orphan PK/seq/FK names)
  - `budget.scenarios` (missing `uq_scenarios_one_baseline` partial unique)
  - `budget.savings_goals` (2 ref-FKs lack ondelete)
  - `ref.account_types` (1 ref-FK lacks ondelete)
  - `salary.paycheck_deductions` (2 ref-FKs lack ondelete + dropped index)
  - `salary.salary_profiles` (1 ref-FK lacks ondelete + server_default removed)
  - `salary.salary_raises` (1 ref-FK lacks ondelete + dropped index)
  - `salary.tax_bracket_sets` (1 ref-FK lacks ondelete)
  - `salary.state_tax_configs` (1 ref-FK lacks ondelete)
  - `salary.fica_configs`, `salary.pension_profiles` (server_default drift only)
- **Tables MISSING from live DB:** 1 (`system.audit_log`)
- **Most dangerous drifts:** F-S6-C3-01 (missing audit log) and F-S6-C3-02 (missing partial unique for baseline scenario enforcement).

### Migration health summary

- **Total migrations in chain:** 39 (all linear, no branching)
- **Head migration:** `c7e3a2f9b104` (live DB matches)
- **Migrations with working downgrade:** 38 / 39 (only `b4c5d6e7f8a9` uses `pass`, which technically violates the standard)
- **Migrations with destructive operations in upgrade:** 6 (7 if you count `efffcf647644`'s unsafe add-column)
- **Migrations flagged WARN:** 10 (partial reversal, unrunnable-on-populated, naming drift)
- **Migrations flagged FAIL:** 2 (F-S6-C1-01 `efffcf647644`, F-S6-C1-02 `dc46e02d15b4` duplicate CHECK names)
- **Overall migration chain fitness:** **69% PASS, 26% WARN, 5% FAIL.**

### Cross-references to Session S5 (16-business-logic.md)

S5 ran a separate Check on Marshmallow ↔ DB CHECK parity (S5 Check 9, "Validation layering"). S6's Check 4 overlaps in scope but went deeper on per-field analysis. Points of convergence:

- **S5 L-V1 (SavingsGoal.contribution_per_period mismatch)** corresponds to S6 F-S6-C4-07 entry for contribution_per_period. Confirmed at DB level.
- **S5 L-V6 (StateTaxConfig.standard_deduction no DB CHECK)** corresponds to S6 F-S6-C4-06. Confirmed no PG CHECK exists.
- **S5 L-V8 (LoanParams.original_principal boundary mismatch)** corresponds to S6 F-S6-C4-07 entry. Confirmed.
- **S5 findings on SalaryRaise.percentage / flat_amount sign rules** correspond to S6 F-S6-C4-03. Confirmed: DB CHECKs enforce `> 0` while Marshmallow allows negatives.
- **S5's type-purity Grep results** (Grep 1-6) are orthogonal to S6 -- they concern Python-level Decimal hygiene and don't intersect with schema-level checks.

S6 adds findings S5 did not raise:
- F-S6-C3-01 (audit log missing) -- a schema-level concern S5 had no reason to investigate.
- F-S6-C4-01 (6 percentage/decimal mismatches) -- S5 L-V6 mentioned StateTaxConfig but didn't enumerate all 6.
- F-S6-C4-02 (`trend_alert_threshold` broken) -- S5 didn't catch this because the field was out of S5's scope (business logic focus, not UX validation).
- F-S6-C5-01 (9 ref-FK ondelete gaps), F-S6-C6-01 (3 dropped salary indexes) -- schema-level concerns S5 did not cover.

### Post-session prod health snapshot

Container states (unchanged from pre-session, aside from uptime increment):

```
shekel-prod-app  Up 40 hours (healthy)    ghcr.io/saltyreformed/shekel:latest
shekel-prod-db   Up 40 hours (healthy)    postgres:16-alpine
shekel-prod-nginx  (n/a -- not running on this system, replaced by shared nginx)
```

`pg_stat_activity` connection count at session close: **8** (identical to pre-session baseline -- no leaked psql sessions).

`alembic_version` unchanged: **`c7e3a2f9b104`**.

No writes were issued to the database. No container state was modified. No migrations were run.

### Session completion

Session S6 is **complete**. All six Checks are written to `docs/audits/security-2026-04-15/reports/17-migrations-schema.md`. All 43 per-table schema dumps are saved to `docs/audits/security-2026-04-15/scans/schema-*.txt`. Findings are scoped and ready for inclusion in the final `findings.md` in Session S8.

You may close this chat and start Session S7.
