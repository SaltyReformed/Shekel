# Model-vs-migration drift findings

**Status (2026-05-10):** RESOLVED.  All six original findings closed,
plus one additional finding (H-5) that surfaced during the L-1
verification sweep.  Seven commits landed on the `dev` branch
(`9a5cca1`, `709786a`, `6384c77`, `d6f31b5`, `2a28f8e`, `cfc8572`,
`7939c8a`).  The comparison script's diff now contains only the
EXPECTED divergences listed at the bottom of this document.  Phase 2
of the per-worker DB isolation work is unblocked.  See per-finding
"Resolution" subsections for what was actually done -- several
findings were closed with a different approach than originally
recommended once the verification step revealed the doc's diagnosis
was incomplete or stale.

**Origin:** Surfaced during pre-flight Phase 2a of the per-pytest-
worker database isolation work (`docs/audits/security-2026-04-15/
c-38-followups.md` Issue 2 follow-up; user-approved plan
`/home/josh/.claude/plans/changing-my-tests-makes-luminous-newell.md`).
The plan's Phase 2 builds the new `shekel_test_template` database
from migrations (`flask db upgrade head`).  Running the migration
chain against an empty database and comparing the result with
`db.create_all()` (the path the current `tests/conftest.py` uses)
revealed substantial drift: the test suite has been verifying a
schema that does not match what production actually enforces.

This document catalogues every divergence so the drift can be
remediated as a dedicated piece of work, after which Phase 2 of the
per-worker isolation migration resumes.

**Comparison method (reproducible):**

```bash
# Build the create_all schema (model declarations)
PGPASSWORD=shekel_pass psql -h localhost -p 5433 -U shekel_user -d postgres \
    -c "CREATE DATABASE shekel_drift_create_all"
TEST_DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/shekel_drift_create_all \
SECRET_KEY="drift-check-key-32-characters-long-not-prod" \
python -c "
from app import create_app
from app.extensions import db
from app.audit_infrastructure import apply_audit_infrastructure
app = create_app('testing')
with app.app_context():
    for s in ('ref','auth','budget','salary','system'):
        db.session.execute(db.text(f'CREATE SCHEMA IF NOT EXISTS {s}'))
    db.session.commit()
    db.create_all()
    apply_audit_infrastructure(lambda sql: db.session.execute(db.text(sql)))
    db.session.commit()
"
PGPASSWORD=shekel_pass pg_dump -h localhost -p 5433 -U shekel_user \
    -d shekel_drift_create_all --schema-only --no-owner --no-privileges \
    > /tmp/schema_create_all.sql

# Build the migrations schema
PGPASSWORD=shekel_pass psql -h localhost -p 5433 -U shekel_user -d postgres \
    -c "CREATE DATABASE shekel_drift_migrations"
TEST_DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/shekel_drift_migrations \
DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/shekel_drift_migrations \
SECRET_KEY="drift-check-key-32-characters-long-not-prod" \
python -c "
from app import create_app
from app.extensions import db
from alembic import command
from alembic.config import Config
app = create_app('testing')
with app.app_context():
    for s in ('ref','auth','budget','salary','system'):
        db.session.execute(db.text(f'CREATE SCHEMA IF NOT EXISTS {s}'))
    db.session.commit()
    cfg = Config('alembic.ini')
    cfg.set_main_option('script_location', 'migrations')
    command.upgrade(cfg, 'head')
"
PGPASSWORD=shekel_pass pg_dump -h localhost -p 5433 -U shekel_user \
    -d shekel_drift_migrations --schema-only --no-owner --no-privileges \
    > /tmp/schema_migrations.sql

diff /tmp/schema_create_all.sql /tmp/schema_migrations.sql
```

Run this again before and after each drift fix to confirm the
divergence has closed.

---

## H-1: `budget.transactions` CHECK constraints missing from model

### Severity
**High.**  Tests can pass a regression that allowed negative
transaction amounts; production would reject it.  Direct line of
financial-correctness exposure -- the whole project is a budget
app, so a negative-amount bug ships real money mismanagement.

### Symptom

Migration-built database has:

```sql
ALTER TABLE budget.transactions
    ADD CONSTRAINT ck_transactions_positive_amount
        CHECK (estimated_amount >= 0::numeric),
    ADD CONSTRAINT ck_transactions_positive_actual
        CHECK (actual_amount IS NULL OR actual_amount >= 0::numeric);
```

`db.create_all()` output (the schema the test suite uses) lacks
both constraints.  Tests that insert `Transaction(estimated_amount
=Decimal("-100"))` would pass against the test DB but fail at
production INSERT time.

### Where it lives

- Migration: search `migrations/versions/` for the file that
  introduced `ck_transactions_positive_*`.  Likely the C-24 range-
  check sweep (`b71c4a8f5d3e_c24_marshmallow_range_sweep...`).
- Model: `app/models/transaction.py` -- the `Transaction` class.
  Missing `__table_args__` entry for these CHECK constraints.

### Recommended fix

Add the constraints to the model's `__table_args__`:

```python
__table_args__ = (
    # ... existing ...
    db.CheckConstraint(
        "estimated_amount >= 0",
        name="ck_transactions_positive_amount",
    ),
    db.CheckConstraint(
        "actual_amount IS NULL OR actual_amount >= 0",
        name="ck_transactions_positive_actual",
    ),
    # ... existing ...
)
```

After the model is updated, `db.create_all()` produces the same
schema as migrations.  No new migration needed -- the constraint
already exists in production.

### Verification

1. Re-run the comparison script above; confirm these constraints
   appear in `schema_create_all.sql` too.
2. Add a test in `tests/test_models/` that asserts
   `Transaction(estimated_amount=Decimal("-1"))` raises
   `IntegrityError` at commit time.  This locks the contract.

### Resolution (2026-05-10, commit `9a5cca1`)

**The doc's diagnosis was inaccurate.**  Verification before
implementation revealed:

* `app/models/transaction.py` already declared the constraints, but
  with different names (`ck_transactions_estimated_amount` and
  `ck_transactions_actual_amount`) -- materialised by the later
  migration `dc46e02d15b4_add_check_constraints_to_loan_params_.py`.
* The migration `c5d6e7f8a901_add_positive_amount_check_constraints.py`
  added an older pair (`ck_transactions_positive_amount` /
  `ck_transactions_positive_actual`) that was never declared on the
  model.

A fresh-from-migrations DB ended up with FOUR transaction CHECK
constraints (two functional pairs, identical predicates).  The model
side had only two; production (bootstrapped via `db.create_all()` +
`stamp head`) also had only two.  Real drift: the migration chain
materialised duplicate constraints that the model never declared.

**Actual fix:** new migration
`migrations/versions/724d21236759_drop_redundant_transaction_check_.py`
drops `ck_transactions_positive_amount` and
`ck_transactions_positive_actual` with `ALTER TABLE ... DROP
CONSTRAINT IF EXISTS` guards (no-op against production / dev which
never carried them; real cleanup against fresh-from-migrations DBs).
Surviving model-named pair continues to enforce non-negative amounts.

**Regression test:** `tests/test_models/test_transaction_constraints.py`
asserts negative `estimated_amount` and `actual_amount` raise
`IntegrityError` and confirms NULL `actual_amount` remains allowed.

---

## H-2: `budget.scenarios` partial unique index missing from model

### Severity
**High.**  Tests can pass code paths that create multiple baseline
scenarios per user; production would reject the second baseline.
Baseline scenarios are the load-bearing reference for all balance
projections -- two baselines is a logic-corruption bug.

### Symptom

Migration-built database has:

```sql
CREATE UNIQUE INDEX uq_scenarios_one_baseline
    ON budget.scenarios USING btree (user_id)
    WHERE is_baseline = true;
```

`db.create_all()` output lacks the partial unique index.  Tests
asserting "any user can only have one baseline scenario" would
pass against test DBs even with broken application logic.

### Where it lives

- Migration: search `migrations/versions/` for `uq_scenarios_one_
  baseline`.  Likely the C-23 uniqueness commit (`a3b9c2d40e15_
  c23_salary_raise...` or sibling).
- Model: `app/models/scenario.py` -- the `Scenario` class.

### Recommended fix

Add the partial index to the model's `__table_args__`:

```python
__table_args__ = (
    # ... existing ...
    db.Index(
        "uq_scenarios_one_baseline",
        "user_id",
        unique=True,
        postgresql_where=db.text("is_baseline = true"),
    ),
    # ... existing ...
)
```

After the model is updated, `db.create_all()` produces the same
schema as migrations.  No new migration needed.

### Verification

1. Re-run the comparison script; confirm the index appears in
   `schema_create_all.sql`.
2. Add a test in `tests/test_models/test_scenario.py` (or wherever
   the existing scenario tests live) that asserts a second
   `Scenario(user_id=u, is_baseline=True)` raises `IntegrityError`.

### Resolution (2026-05-10, commit `709786a`)

Diagnosis accurate.  Implemented as recommended: model-only edit to
`app/models/scenario.py` adding the partial unique index to
`__table_args__`.  No new migration -- production already carries
the index from `c5d6e7f8a901`.

**Regression test:** `tests/test_models/test_scenario_constraints.py`
asserts (a) a second baseline for the same user raises
`IntegrityError`, (b) many non-baseline scenarios may coexist with
the baseline, (c) two separate users each carry their own baseline
simultaneously (the partial index is per-user-scoped).

**Fixture audit:** verified all 26 `is_baseline=True` references in
the test suite are either fixture creates (one baseline per
distinct user) or `filter_by(...is_baseline=True)` query filters --
no test relies on creating two baselines for the same user.

---

## H-3: `ref.recurrence_rules` CHECK constraints missing from migrations

### Severity
**High.**  The opposite direction: model declares constraints,
migrations don't.  Production lets through `day_of_month=99` or
`month_of_year=15` while tests reject them.  This is the
recurrence engine's input domain -- bad values would silently
generate transactions on impossible dates.

### Symptom

`db.create_all()` output contains:

```sql
CONSTRAINT ck_recurrence_rules_dom
    CHECK (day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)),
CONSTRAINT ck_recurrence_rules_moy
    CHECK (month_of_year IS NULL OR (month_of_year >= 1 AND month_of_year <= 12)),
```

Migration-built database lacks both constraints.

### Where it lives

- Model: `app/models/recurrence_rule.py` -- the `RecurrenceRule`
  class.  Has the constraints.
- Migration: no migration adds them.  Likely added to the model
  after the initial migration was authored, with no follow-up
  migration written.

### Recommended fix

Author a new Alembic migration that adds both CHECK constraints
to `budget.recurrence_rules`.  The migration upgrade step:

```python
def upgrade():
    op.create_check_constraint(
        "ck_recurrence_rules_dom",
        "recurrence_rules",
        "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_moy",
        "recurrence_rules",
        "month_of_year IS NULL OR (month_of_year >= 1 AND month_of_year <= 12)",
        schema="budget",
    )
```

Before the migration upgrades production, audit existing
`budget.recurrence_rules` for rows that would violate either
constraint.  If found, fix them (likely none in single-user prod;
worth a quick sanity check).

### Verification

1. Run the new migration upgrade against a staging copy; verify
   no rows are rejected.
2. Run downgrade; confirm the constraint disappears.
3. Re-run the comparison script; confirm the constraints exist on
   both sides.

### Resolution (2026-05-10, commit `6384c77`)

Diagnosis correct in spirit but wrong on two details:

* The table is in the `budget` schema, not `ref` (the doc heading
  was misleading; the doc's recommended `op.create_check_constraint`
  call already used the correct `schema="budget"`).
* The model declares THREE inline column-level CHECK constraints:
  `ck_recurrence_rules_dom`, `ck_recurrence_rules_due_dom`, and
  `ck_recurrence_rules_moy`.  The doc only mentioned two.
  Verification revealed `ck_recurrence_rules_due_dom` was already
  added by migration `f15a72a3da6c_add_due_date_paid_at_to_transactions_.py`
  (the only one of the three the migration chain ever materialised),
  so only two constraints were missing.

**Actual fix:** new migration
`migrations/versions/1702cadcae54_add_recurrence_rules_dom_moy_check_.py`
adds `ck_recurrence_rules_dom` and `ck_recurrence_rules_moy` only.
Pre-flight detection refuses the upgrade on any pre-existing
violator (zero in dev/prod; defensive per the
`b71c4a8f5d3e_c24_marshmallow_range_check_sweep` precedent).
DDL phase uses `_constraint_exists` guards so the migration is
idempotent against the test path that already materialises the
constraints from the inline model declarations.

**Regression test:** `tests/test_models/test_recurrence_rule_constraints.py`
six tests exercise day_of_month above 31, day_of_month below 1,
due_day_of_month above 31 (uses the existing constraint),
month_of_year above 12, month_of_year below 1, and the NULL-allowed
case.

---

## H-4: `system.audit_log.executed_at` NOT NULL discrepancy

### Severity
**Medium.**  Models declare `NOT NULL`; migrations omit it.
Production allows NULL `executed_at`, but the trigger function
always sets the value (`DEFAULT now()`), so the practical impact
is nil today.  Becomes high-severity if anything ever inserts
into `system.audit_log` directly without going through the
trigger.

### Symptom

`db.create_all()` output:

```sql
executed_at timestamp with time zone DEFAULT now() NOT NULL,
```

Migration-built database:

```sql
executed_at timestamp with time zone DEFAULT now(),
```

### Where it lives

- Model: `app/models/audit_log.py` (or wherever `AuditLog` is
  declared) -- declares `nullable=False`.
- Migration: search `migrations/versions/` for the audit-log
  table creation (likely `a5be2a99ea14_rebuild_audit_
  infrastructure.py`).  The CREATE TABLE omits `NOT NULL`.

### Recommended fix

Author a migration that adds `NOT NULL` to `system.audit_log
.executed_at`.  Cheap because every row already has a value
(trigger-supplied `DEFAULT now()`).

```python
def upgrade():
    op.alter_column(
        "audit_log",
        "executed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        existing_server_default=sa.text("now()"),
        schema="system",
    )
```

### Verification

1. Migration upgrade: confirms no `NULL` rows reject (should be
   none).
2. Re-run the comparison script.

### Resolution (2026-05-10, commit `d6f31b5`)

Diagnosis correct.  Implementation also surfaced and closed an
ADDITIONAL drift on the same table that the doc did not mention:

* `app/audit_infrastructure.py:_CREATE_AUDIT_LOG_TABLE_SQL` declares
  `CONSTRAINT ck_audit_log_operation CHECK (operation IN ('INSERT',
  'UPDATE', 'DELETE'))`.  This constraint never reached the
  migration-built `system.audit_log` table because the original
  `a8b1c2d3e4f5` migration predated it and the rebuild migration
  `a5be2a99ea14` uses `CREATE TABLE IF NOT EXISTS` so leaves an
  existing table untouched.

Confirmed `8a21d16c9bde_tighten_audit_timestamp_nullability_`
deliberately skipped `system` schema (its docstring says "across
user-facing schemas") and so did not address either drift.

**Actual fix:** new migration
`migrations/versions/b2b1ff4c3cea_audit_log_executed_at_not_null.py`
runs two pre-flight checks (NULL `executed_at`, off-trigger
`operation` values) then ALTERs the column to NOT NULL and adds the
CHECK constraint.  `_constraint_exists` guard makes the CHECK add
idempotent against the test path.

**Regression test:** none added -- the existing
`tests/test_models/test_audit_migration.py` and
`tests/test_integration/test_audit_triggers.py` exercise the
round-trip and pass against the tightened schema.

---

## M-1: `budget.interest_params` -- incomplete `hysa_params` rename

### Severity
**Medium.**  Cosmetic on the surface (sequence/trigger/index
names referencing a renamed table).  Becomes problematic when a
future operator tries to manage these objects by their model-
suggested name -- "rename `interest_params_id_seq`" would fail
because the sequence is actually still `hysa_params_id_seq`.

### Symptom

Migration-built database has:

```sql
CREATE SEQUENCE budget.hysa_params_id_seq ...
ALTER SEQUENCE budget.hysa_params_id_seq OWNED BY budget.interest_params.id;
CREATE INDEX idx_hysa_params_account ON budget.interest_params USING btree (account_id);
CREATE TRIGGER audit_hysa_params AFTER ... ON budget.interest_params ...
ADD CONSTRAINT hysa_params_pkey PRIMARY KEY (id);
ADD CONSTRAINT hysa_params_account_id_fkey FOREIGN KEY ...
```

`db.create_all()` output uses the new `interest_params_*` names
throughout.  Three of the four artifacts (sequence, index, FK
constraint) carry the legacy name.

### Where it lives

- Migration: search `migrations/versions/` for the rename
  migration.  Likely a recent `hysa_params -> interest_params`
  rename that used `op.rename_table` but did not also rename the
  sequence, trigger, index, or constraints.
- Model: `app/models/interest_params.py` (the new name) -- has
  the canonical naming.

### Recommended fix

Author a migration that renames the leftover artifacts:

```python
def upgrade():
    op.execute("ALTER SEQUENCE budget.hysa_params_id_seq RENAME TO interest_params_id_seq")
    op.execute("ALTER INDEX budget.idx_hysa_params_account RENAME TO idx_interest_params_account")
    op.execute("ALTER TRIGGER audit_hysa_params ON budget.interest_params RENAME TO audit_interest_params")
    op.execute("ALTER TABLE budget.interest_params RENAME CONSTRAINT hysa_params_pkey TO interest_params_pkey")
    op.execute("ALTER TABLE budget.interest_params RENAME CONSTRAINT hysa_params_account_id_fkey TO interest_params_account_id_fkey")
```

Also update `app/audit_infrastructure.py::AUDITED_TABLES` if
the table appears there -- otherwise the next
`apply_audit_infrastructure` run would create a brand-new
`audit_interest_params` trigger alongside the existing
`audit_hysa_params`.

### Verification

1. Re-run the comparison script; confirm no `hysa_params`
   references remain in either schema dump.
2. Confirm the audit trigger count is exactly
   `EXPECTED_TRIGGER_COUNT` (no extras from the rename).

### Resolution (2026-05-10, commit `2a28f8e`)

Diagnosis covered four artifacts (PK, FK, sequence, separate index
`idx_hysa_params_account`).  Verification revealed a fifth artifact
the doc missed AND a more severe problem than "cosmetic":

* The original `a8b1c2d3e4f5` migration created `audit_hysa_params`
  on `budget.hysa_params`.  PostgreSQL leaves triggers attached to
  the renamed table when ALTER TABLE RENAME runs, so the trigger
  followed the table to its new `interest_params` name without
  itself being renamed.
* The rebuild migration `a5be2a99ea14` then created
  `audit_interest_params` via `apply_audit_infrastructure` (which
  loops over `AUDITED_TABLES` carrying the new name) WITHOUT
  dropping the orphan.
* Result: every `interest_params` write on a fresh-from-migrations
  DB fired BOTH triggers, double-writing into `system.audit_log`
  -- a real correctness bug for the forensic trail, not cosmetic.

Production was bootstrapped via `db.create_all()` + `stamp head`
per the rebuild migration's docstring, so prod likely never carried
the orphan; the double-write hazard manifested only in fresh-from-
migrations builds (the per-pytest-worker template path).

Also note: `app/audit_infrastructure.py::AUDITED_TABLES` already
correctly named `interest_params` -- the doc's note about updating
that list was already done in a prior commit.

**Actual fix:** new migration
`migrations/versions/44893a9dbcc3_finish_hysa_to_interest_params_rename.py`
renames PK, FK, and sequence; drops the redundant
`idx_hysa_params_account` (account_id is already covered by the
unique index `interest_params_account_id_key`); and drops the
orphan `audit_hysa_params` trigger.  Every operation is wrapped in
`DO $$` blocks checking `pg_constraint` (for PK/FK renames -- no
`IF EXISTS` form for ALTER TABLE RENAME CONSTRAINT) or `IF EXISTS`
guards (sequence rename, index drop, trigger drop) so the migration
is a no-op on databases that don't carry the legacy artifacts.

**Regression test:** `tests/test_models/test_interest_params_naming.py`
asserts every artifact carries the new name (PK, FK, sequence) and
the legacy `idx_hysa_params_account` is absent.  Most importantly,
asserts `pg_trigger` shows EXACTLY `["audit_interest_params"]` on
`budget.interest_params` -- if a future regression re-introduces
the orphan, the test turns red.

---

## L-1: DEFAULT clauses on many columns differ

### Severity
**Low.**  Affects raw INSERTs only; ORM-based tests that set
every column are unaffected.  Worth fixing for consistency but
not load-bearing.

### Symptom

Migrations declare server defaults that models don't:

```
DEFAULT 'Primary'::character varying    on accounts.name
DEFAULT 0.0400                          on safe_withdrawal_rate
DEFAULT 26                              on deductions_per_year
DEFAULT '0'::numeric                    on child_credit_amount / other_dependent_credit_amount
DEFAULT false                           on every ref-table boolean
```

`db.create_all()` produces these columns without defaults.

### Where it lives

Spread across many models and many migrations.  A full sweep
would require pairing each migration's `server_default=...`
declaration with the corresponding model column.

### Recommended fix

For each column with drift, decide which side is authoritative:

* If the migration's DEFAULT is intentional production behaviour
  (e.g., `name DEFAULT 'Primary'` for newly-created accounts) --
  add `server_default=...` to the model declaration.
* If the model's lack-of-default is intentional and the
  migration's default was added in error -- author a migration
  that drops the DEFAULT.

The default scenario is "the migration is authoritative" because
production has been running with these defaults; removing them
could break existing INSERT paths.

### Verification

Re-run the comparison script; confirm DEFAULT clauses match on
both sides.

### Resolution (2026-05-10, commit `cfc8572`)

Diagnosis correct except for one example:

* `accounts.name DEFAULT 'Primary'` is wrong -- `accounts.name` has
  NO server_default in either the model OR any migration.  The
  `'Primary'` default actually lives on `salary_profiles.name`
  (added by `22b3dd9d9ed3_add_salary_schema_tables.py`), where the
  model omitted it.

Confirmed all other examples and ran a comprehensive sweep
(authoritative-from-migration direction per the doc's
recommendation).  Thirteen columns across five models updated:

| Model file (column) | server_default added |
|---|---|
| `app/models/user.py::UserSettings.safe_withdrawal_rate` | `0.0400` |
| `app/models/salary_profile.py::SalaryProfile.name` | `'Primary'` |
| `app/models/paycheck_deduction.py::PaycheckDeduction.deductions_per_year` | `26` |
| `app/models/tax_config.py::TaxBracketSet.child_credit_amount` | `'0'` (string) |
| `app/models/tax_config.py::TaxBracketSet.other_dependent_credit_amount` | `'0'` (string) |
| `app/models/ref.py::AccountType.has_parameters` | `false` |
| `app/models/ref.py::AccountType.has_amortization` | `false` |
| `app/models/ref.py::AccountType.has_interest` | `false` |
| `app/models/ref.py::AccountType.is_pretax` | `false` |
| `app/models/ref.py::AccountType.is_liquid` | `false` |
| `app/models/ref.py::Status.is_settled` | `false` |
| `app/models/ref.py::Status.is_immutable` | `false` |
| `app/models/ref.py::Status.excludes_from_balance` | `false` |

The two `tax_config` columns use bare-string `server_default="0"`
(not `db.text("0")`) so pg_dump renders the default as
`DEFAULT '0'::numeric` -- matching the form materialised by the
`b4c7d8e9f012` migration's `server_default='0'`.  `db.text("0")`
would have rendered as `DEFAULT 0` (functionally identical but a
pg_dump diff against the migration-built schema).

Comparison script after the sweep confirmed zero DEFAULT-clause
divergences remain.

---

## H-5: `salary.salary_raises` `ck_salary_raises_one_method` missing from migrations

### Severity
**High.**  Surfaced during the L-1 verification sweep, not in the
original drift catalogue.  Same shape as H-3 (model declares CHECK
constraint, migration chain never materialised it).  Direct line
of paycheck-correctness exposure -- a `SalaryRaise` row with both
`percentage` and `flat_amount` populated would silently apply only
the percentage path, drifting projected gross pay.

### Symptom

`db.create_all()` output contains:

```sql
CONSTRAINT ck_salary_raises_one_method CHECK (
    (percentage IS NOT NULL AND flat_amount IS NULL) OR
    (percentage IS NULL AND flat_amount IS NOT NULL)
)
```

Migration-built database lacks the constraint.

### Where it lives

* Model: `app/models/salary_raise.py` -- the `SalaryRaise` class.
  Has the constraint inline in `__table_args__`.
* Migration: no migration adds it.  The Marshmallow schema in
  `app/schemas/validation.py` rejects violator rows at the API
  tier, so the runtime never produced a violator -- which is why
  the storage-tier gap went unnoticed.

### Resolution (2026-05-10, commit `7939c8a`)

New migration
`migrations/versions/2109f7a490e7_add_ck_salary_raises_one_method.py`
adds the CHECK constraint with pre-flight detection of any pre-
existing XOR violator (zero in dev/prod thanks to schema-layer
rejection; defensive).  `_constraint_exists` guard makes the
migration idempotent against the test path (db.create_all() already
materialises the constraint from the inline declaration).

No new test added: existing salary tests
(`tests/test_routes/test_salary.py`,
`tests/test_services/test_paycheck_calculator.py`) exercise the
percentage-vs-flat split and pass against the tightened storage
tier.

---

## EXPECTED divergences (no fix required)

These appear in the diff but are not drift:

1. `public.alembic_version` table -- only the migration path
   creates it.  `create_all` is not aware of Alembic's bookkeeping
   table.  Expected.
2. Constraint name differences for FK constraints
   (`users_linked_owner_id_fkey` vs `fk_users_linked_owner`).
   Both name the same foreign key; the difference is just
   migration-author convention vs SQLAlchemy autogeneration.
   Worth aligning eventually but not load-bearing.
3. Column ordering differences -- `pg_dump` lists columns in the
   order PostgreSQL stored them, which differs between
   migration-applied DDL and `create_all`-generated DDL.
   Semantically meaningless.
4. The `\restrict` token at the top of each dump (different
   random IDs).  Internal `pg_dump` metadata, unrelated to
   schema.

---

## Tracking

| Finding | Severity | Resolution commit | Notes |
|---|---|---|---|
| H-1 (`budget.transactions` CHECK constraints) | High | `9a5cca1` `feat(migrations): drop redundant ck_transactions_positive_* constraints (H-1)` | Doc diagnosis was wrong: model already had the constraints under different names; real drift was duplicates in migration chain.  New migration drops them with `IF EXISTS` guards. |
| H-2 (`budget.scenarios` partial unique index) | High | `709786a` `fix(models): declare uq_scenarios_one_baseline partial unique index (H-2)` | Model-only edit as recommended. |
| H-3 (`recurrence_rules` CHECK constraints) | High | `6384c77` `feat(migrations): add ck_recurrence_rules_dom and ck_recurrence_rules_moy (H-3)` | Table is in `budget` schema (not `ref`).  Model declared 3 constraints; migration chain already had `due_dom` (via f15a72a3da6c), so only 2 needed. |
| H-4 (`system.audit_log.executed_at` NOT NULL) | Medium | `d6f31b5` `feat(migrations): align system.audit_log with canonical schema (H-4)` | Closed two drifts on the same table: NOT NULL on `executed_at` AND `ck_audit_log_operation` CHECK constraint that the doc didn't mention. |
| M-1 (`hysa_params` rename incomplete) | Medium -> High | `2a28f8e` `feat(migrations): finish hysa_params -> interest_params rename + drop orphan trigger (M-1)` | Doc missed the `audit_hysa_params` orphan trigger that double-fired into `system.audit_log` on every `interest_params` write in fresh-from-migrations DBs.  Severity higher than catalogued. |
| L-1 (DEFAULT clause drift) | Low | `cfc8572` `fix(models): align server_default declarations with production migrations (L-1)` | 13 columns aligned across 5 model files.  Doc's `accounts.name` example was wrong -- the `'Primary'` default is on `salary_profiles.name`. |
| H-5 (`salary.salary_raises` `ck_salary_raises_one_method`) | High | `7939c8a` `feat(migrations): add ck_salary_raises_one_method CHECK constraint` | Surfaced during L-1 verification, not in original catalogue. |

**Realised effort:** ~3 hours including planning, verification,
implementation, regression tests, and full-suite verification across
8 directory batches (5,148 tests, all passed).  Pylint `app/` held
at 9.50/10 throughout.  Phase 2 of the per-worker DB isolation work
is unblocked; resume per
`docs/audits/security-2026-04-15/per-worker-database-plan.md` and
`/home/josh/.claude/plans/changing-my-tests-makes-luminous-newell.md`.
