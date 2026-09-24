"""a mis-dated loan statement is withdrawn; the stated balance takes its setup day

Revision ID: cddb15ffba5f
Revises: 9b64df71cc34
Create Date: 2026-09-23 21:30:00.000000

Plan step **recurrence:R23**, rulings **R-R98** (the act) and **R-R99** (the
downgrade); closes ledger row **FU-1**.

**The defect.**  Migration ``d3d25212504b`` copied a loan's stored
``current_principal`` into a ``user_trueup`` dated the day THAT MIGRATION RAN,
wherever the stored value disagreed with a replay.  A balance stated at SETUP
is then asserted for the wrong day: every payment between the setup day and
the run day sits inside a statement that did not see it, and the walk charges
each of them against the balance before setup.  **What the predicate below
cannot test is WHEN the balance was stated** -- the params form could also
edit the column after setup, and ``system.audit_log`` begins after that
window -- so the re-dating rests on the one production row's evidence: its
servicer's statements show the balance holding from the payment before the
setup day to the first payment after it, and the loan records no payment in
between (checked 2026-09-23).  Plan
step ``recurrence:R20`` (``22b23085394d``) records the balance an owner states
at setup as a ``tracking_start`` on the setup day, but its backfill skipped any
loan carrying a ``user_trueup`` of any date -- the copied loan included.

**The rows, by predicate (no literal id or figure).**  A ``user_trueup``

* whose ``created_at`` equals the same account's legacy ``origination`` row's
  ``created_at`` -- ``d3d25212504b`` wrote both in one transaction, and
  ``created_at`` is that transaction's start instant;
* which is the loan's EARLIEST non-origination statement, by the loan's
  chronology ``(anchor_date, created_at, id)``.  A loan whose owner recorded
  an earlier statement of their own -- a ``tracking_start`` before the copy --
  has its stated balance on record already, and this revision has no ground to
  call its later copy mis-dated; production's other live loan is that shape
  (measured on a production clone, 2026-09-23);
* dated AFTER the setup day, on a loan originated BEFORE it.  A copy on the
  setup day has nothing to move, and a loan originated on its setup day has
  its origination as its assertion.

The setup day is ``loan_params.created_at`` read as the owner's civil day,
``(created_at AT TIME ZONE 'America/New_York')::date`` -- R20's derivation,
spelled as a literal for R20's reason (a migration is a historical record and
keeps meaning the same thing if the display zone ever changes).  **Measured on
that clone: exactly one row.**

**The act, per row: two INSERTs and nothing else.**  A ``tracking_start`` at
the setup day carrying the copy's balance, and a withdrawal of the copy in the
new relation.  Nothing is edited or deleted: the statement table stays
append-only (ruling **R-HY**), and a statement stands until a withdrawal names
it (:class:`app.models.loan_anchor_withdrawal.LoanAnchorWithdrawal`).  The
count is printed for the operator to compare with the rehearsal's.

**The posted ledger is not touched here.**
``loan_posting_service.backfill_all_loan_postings`` re-derives every loan's
posted ledger on every deploy, inside the deploy's one transaction
(``scripts/init_database.py``); the split is a running-balance walk and cannot
run in a migration.  A local ``flask db upgrade`` does not run it: call it
after.

**The relation.**  ``budget.loan_anchor_withdrawals``: one row per withdrawn
statement, unique on the statement, composite-keyed onto the new superkey
``uq_loan_anchor_events_account_id`` so it cannot name another account's
statement, CASCADE with its statement; audited (the trigger attached here, as
``d2e9f4a17c63`` does for its relation); append-only through the shared
``budget.refuse_append_only_change``, whose live body gains the relation's
owner arm (a withdrawal is deleted only once its statement is gone).

**Downgrade (ruling R-R99, "exact undo").**  Deletes ONLY the
``tracking_start`` this upgrade appended -- the one written in the same
transaction as a withdrawal (``created_at`` equal to the withdrawal's), on
that account, dated its setup day, carrying the withdrawn copy's balance --
with the append-only refusal lifted for the statement (the ``d2e9f4a17c63``
precedent); ``system.audit_log`` keeps the deleted row.  It then drops the
relation and the superkey and re-installs the refusal exactly as
``5641f7729b68`` left it (``9b64df71cc34``, the revision this one now
follows, does not touch it), so the database is the one this revision found.  A
re-upgrade selects the same copy again UNLESS the owner recorded a statement
earlier than the copy in between -- then the copy is no longer the loan's
earliest statement, the predicate passes it by, and it stands again.
Production never runs a downgrade: a genuine one restores the pre-deploy dump
(``deploy/shekel-deploy.sh``).

Review: solo developer, 2026-09-23 (rulings R-R98, R-R99; the downgrade
deletes the row the upgrade appended and drops the relation it created).
"""

from alembic import op
import sqlalchemy as sa

from app.append_only_infrastructure import (
    APPEND_ONLY_TRIGGERS,
    apply_append_only_infrastructure,
    remove_append_only_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "cddb15ffba5f"
down_revision = "9b64df71cc34"
branch_labels = None
depends_on = None


#: The copies this revision withdraws (see the module docstring for each
#: clause).  Exposed so a test can build the shapes and grade the predicate
#: without re-running the chain.
_MIS_DATED_COPIES_SQL = """
SELECT c.id, c.account_id, c.anchor_date, c.anchor_balance,
       (lp.created_at AT TIME ZONE 'America/New_York')::date AS setup_day
  FROM budget.loan_anchor_events c
  JOIN ref.loan_anchor_sources cs
    ON cs.id = c.source_id AND cs.name = 'user_trueup'
  JOIN budget.loan_params lp ON lp.account_id = c.account_id
 WHERE EXISTS (
         SELECT 1 FROM budget.loan_anchor_events o
           JOIN ref.loan_anchor_sources os ON os.id = o.source_id
          WHERE o.account_id = c.account_id
            AND os.name = 'origination'
            AND o.created_at = c.created_at
       )
   AND lp.origination_date
       < (lp.created_at AT TIME ZONE 'America/New_York')::date
   AND c.anchor_date
       > (lp.created_at AT TIME ZONE 'America/New_York')::date
   AND NOT EXISTS (
         SELECT 1 FROM budget.loan_anchor_events e
           JOIN ref.loan_anchor_sources es ON es.id = e.source_id
          WHERE e.account_id = c.account_id
            AND es.name <> 'origination'
            AND (e.anchor_date, e.created_at, e.id)
                < (c.anchor_date, c.created_at, c.id)
       )
 ORDER BY c.account_id, c.id
"""

#: The statement recorded on the setup day, one per copy.  ``id`` and
#: ``created_at`` fill themselves; the audit trigger records the INSERT with no
#: request user, as R20's backfill did.
_RECORD_ON_SETUP_DAY_SQL = """
INSERT INTO budget.loan_anchor_events
    (account_id, anchor_date, anchor_balance, source_id)
VALUES (
    :account_id, :setup_day, :balance,
    (SELECT id FROM ref.loan_anchor_sources WHERE name = 'tracking_start')
)
"""

_WITHDRAW_SQL = """
INSERT INTO budget.loan_anchor_withdrawals (account_id, anchor_event_id)
VALUES (:account_id, :anchor_event_id)
"""

#: Downgrade (R-R99): the setup-day statement the upgrade appended beside each
#: withdrawal, and nothing else.  Only this revision writes a withdrawal and a
#: statement in one transaction, so the shared instant names its row.
_DELETE_RECORDED_SQL = """
DELETE FROM budget.loan_anchor_events t
 USING budget.loan_anchor_withdrawals w,
       budget.loan_anchor_events c,
       budget.loan_params lp,
       ref.loan_anchor_sources ts
 WHERE c.id = w.anchor_event_id
   AND t.account_id = w.account_id
   AND t.created_at = w.created_at
   AND t.anchor_balance = c.anchor_balance
   AND ts.id = t.source_id AND ts.name = 'tracking_start'
   AND lp.account_id = t.account_id
   AND t.anchor_date = (lp.created_at AT TIME ZONE 'America/New_York')::date
RETURNING t.id, t.account_id
"""

#: The one table this revision installs the refusal on, named literally so a
#: replay from the start of the chain installs what this revision installed
#: even after the constant grows (the ``d2e9f4a17c63`` reason).
_WITHDRAWALS = "budget.loan_anchor_withdrawals"

#: The tables the PREVIOUS function served, for the downgrade.
_PREVIOUS_APPEND_ONLY_TABLES = (
    "budget.account_anchor_history",
    "budget.anchor_releases",
    "budget.account_openings",
    "budget.loan_anchor_events",
)

#: ``5641f7729b68``'s function body, verbatim (last changed by
#: ``d2e9f4a17c63``), for the downgrade -- the chain lands on
#: ``9b64df71cc34``, which does not touch it, so this is still exactly what it
#: finds.
_PREVIOUS_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION budget.refuse_append_only_change()
RETURNS TRIGGER AS $$
BEGIN
    -- TRUNCATE first, because it is the only arm with no OLD row to name.
    -- It is refused outright rather than conditionally: a TRUNCATE cannot
    -- distinguish disposing of an account from emptying the table, and it is
    -- invisible to the audit trigger, so permitting it would destroy history
    -- leaving no record anywhere.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION
            '%.% is append-only; TRUNCATE rejected. Dispose of an account by '
            'deleting the account, which carries its history through the '
            'audit log.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        -- The ONE admitted transition: a release's cause was deleted and the
        -- key's own SET NULL is running.  Old cause present, new cause
        -- absent, every other column equal, AND the import it named gone --
        -- the referential action runs after the DELETE has taken effect, so
        -- the row is already invisible here, where a hand-written UPDATE
        -- erasing a standing cause still sees it and is refused.  Nothing
        -- else passes.
        IF TG_TABLE_NAME = 'anchor_releases' THEN
            IF OLD.released_by_import_id IS NOT NULL
               AND to_jsonb(NEW) = jsonb_set(
                   to_jsonb(OLD), '{released_by_import_id}', 'null'::jsonb
               )
               AND NOT EXISTS (
                   SELECT 1 FROM budget.statement_imports
                   WHERE id = OLD.released_by_import_id
               ) THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION
            '%.% is append-only; UPDATE rejected for id=%. Record a '
            'correction by inserting a new row.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    -- DELETE, evaluated at COMMIT because this trigger is DEFERRED.  The
    -- owning account still standing at the END of the transaction means this
    -- is a row being picked off rather than an account being disposed of --
    -- and asking at the end is what distinguishes a genuine disposal from a
    -- delete-and-recreate, which leaves the account standing by the time
    -- anybody looks.
    --
    -- Two tables have a SECOND owner and are asked about it first, each
    -- inside its own table guard so a sibling's row never has the field
    -- looked up: a bank level goes with its import, a release with its level.
    IF TG_TABLE_NAME = 'account_anchor_history' THEN
        IF OLD.statement_import_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM budget.statement_imports
            WHERE id = OLD.statement_import_id
        ) THEN
            RETURN NULL;
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'anchor_releases' THEN
        IF NOT EXISTS (
            SELECT 1 FROM budget.account_anchor_history
            WHERE id = OLD.anchor_id
        ) THEN
            RETURN NULL;
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM budget.accounts WHERE id = OLD.account_id) THEN
        RAISE EXCEPTION
            '%.% is append-only; DELETE rejected for id=%. History goes only '
            'with its account.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def _attach_audit_trigger(table: str) -> None:
    """Attach ``system.audit_trigger_func`` to a table this revision creates.

    The idempotent pair ``app.audit_infrastructure`` writes, spelled here
    because the table did not exist when the audit revision ran and the
    deploy's trigger-count check expects it.
    """
    op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON budget.{table}")
    op.execute(
        f"CREATE TRIGGER audit_{table} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{table} "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def _create_previous_append_only_triggers(table: str) -> None:
    """Re-create the three arms ``5641f7729b68`` carried on one table."""
    update_arm, delete_arm, truncate_arm = APPEND_ONLY_TRIGGERS
    op.execute(
        f"CREATE TRIGGER {update_arm} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION budget.refuse_append_only_change()"
    )
    op.execute(
        f"CREATE CONSTRAINT TRIGGER {delete_arm} AFTER DELETE ON {table} "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION budget.refuse_append_only_change()"
    )
    op.execute(
        f"CREATE TRIGGER {truncate_arm} BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION "
        "budget.refuse_append_only_change()"
    )


def upgrade():
    """Create the withdrawal relation, then re-date each mis-dated copy."""
    op.create_unique_constraint(
        "uq_loan_anchor_events_account_id",
        "loan_anchor_events", ["account_id", "id"], schema="budget",
    )
    op.create_table(
        "loan_anchor_withdrawals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("anchor_event_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["budget.accounts.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "anchor_event_id", name="uq_loan_anchor_withdrawals_event",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "anchor_event_id"],
            ["budget.loan_anchor_events.account_id",
             "budget.loan_anchor_events.id"],
            name="fk_loan_anchor_withdrawals_event_account",
            ondelete="CASCADE",
        ),
        schema="budget",
    )
    _attach_audit_trigger("loan_anchor_withdrawals")
    apply_append_only_infrastructure(op.execute, tables=(_WITHDRAWALS,))

    bind = op.get_bind()
    copies = bind.execute(sa.text(_MIS_DATED_COPIES_SQL)).fetchall()
    for copy in copies:
        bind.execute(sa.text(_RECORD_ON_SETUP_DAY_SQL), {
            "account_id": copy.account_id,
            "setup_day": copy.setup_day,
            "balance": copy.anchor_balance,
        })
        bind.execute(sa.text(_WITHDRAW_SQL), {
            "account_id": copy.account_id,
            "anchor_event_id": copy.id,
        })
    # The count is the migration's own measurement, printed so the operator
    # can compare it with the rehearsal's (1 on the 2026-09-23 clone).
    moved = [
        (r.account_id, r.id, r.anchor_date.isoformat(), r.setup_day.isoformat())
        for r in copies
    ]
    print(
        f"cddb15ffba5f: recorded {len(copies)} stated balance(s) on their "
        f"setup day and withdrew the mis-dated copies: {moved}"
    )


def downgrade():
    """Delete the setup-day statements this revision recorded; drop the rest."""
    # Lifted for the DELETE below: the refusal permits a statement's delete
    # only with its account.  Every table's arms go, then the function, so the
    # previous body can be re-installed over the four it served.
    remove_append_only_infrastructure(
        op.execute, tables=(*_PREVIOUS_APPEND_ONLY_TABLES, _WITHDRAWALS),
    )
    deleted = op.get_bind().execute(sa.text(_DELETE_RECORDED_SQL)).fetchall()
    print(
        f"cddb15ffba5f downgrade: deleted {len(deleted)} setup-day "
        f"statement(s) this revision recorded: {sorted(tuple(r) for r in deleted)}"
    )
    op.drop_table("loan_anchor_withdrawals", schema="budget")
    op.drop_constraint(
        "uq_loan_anchor_events_account_id", "loan_anchor_events",
        schema="budget", type_="unique",
    )
    op.execute(_PREVIOUS_APPEND_ONLY_FUNCTION)
    for table in _PREVIOUS_APPEND_ONLY_TABLES:
        _create_previous_append_only_triggers(table)
