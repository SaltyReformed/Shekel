# Model-vs-migration drift findings

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

| Finding | Severity | Recommended commit | Estimated effort |
|---|---|---|---|
| H-1 (`budget.transactions` CHECK constraints in migrations not models) | High | `fix(models): add ck_transactions_positive_amount/_actual to Transaction.__table_args__` (model-only, no new migration) | 30 min |
| H-2 (`budget.scenarios` partial unique index in migrations not model) | High | `fix(models): add uq_scenarios_one_baseline partial unique index to Scenario` (model-only, no new migration) | 30 min |
| H-3 (`ref.recurrence_rules` CHECK constraints in model not migrations) | High | `feat(migrations): add ck_recurrence_rules_dom/_moy CHECK constraints` (new migration; production data audit before upgrade) | 1 hr |
| H-4 (`system.audit_log.executed_at` NOT NULL) | Medium | `feat(migrations): set system.audit_log.executed_at NOT NULL` (new migration) | 30 min |
| M-1 (`hysa_params` rename incomplete) | Medium | `feat(migrations): finish hysa_params -> interest_params rename for sequence + trigger + index + constraints` (new migration) | 1 hr |
| L-1 (DEFAULT clause drift across ~10 columns) | Low | `fix(models): align server_default declarations with production migrations` (model-only sweep) | 2 hr |

**Total estimated effort:** ~5-6 hours of focused work.  Each
finding lands on its own commit so progress is incremental and
revertable.  After all six findings close, re-run the comparison
script and confirm zero meaningful drift remains, then resume
Phase 2 of the per-worker DB isolation work (see
`docs/audits/security-2026-04-15/c-38-followups.md` follow-up
plan and `/home/josh/.claude/plans/changing-my-tests-makes-
luminous-newell.md`).
