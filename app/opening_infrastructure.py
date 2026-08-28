"""Shared definitions for the account-books boundary's database-tier constraint.

**An account's opening equity is the balance at the CLOSE of its
``opened_on``** (plan step X-f3c-2b, ruling **balance:R-HG**) -- exactly the
rule ``account_anchor_history.observed_on`` states for a balance assertion
(ruling R-DH (a)).  A cash movement dated ON OR BEFORE that day is therefore
already inside the figure, and recording it counts the money twice: the fold
seeds at the opening and ``cash_ledger.dated_deltas`` emits every source at its
own day, so the running total carries it a second time until the next assertion
resets it -- and on a MODELLED account (ruling **R-FO**) the correction that
resets it books to ``unrealized_change``, turning a transfer into market
performance that never unwinds (finding **N-378**).

**This module is what makes that state UNSTORABLE rather than merely refused.**
The invariant spans three tables and two directions, so PostgreSQL cannot state
it as a row-level CHECK:

* a MOVEMENT may not move back past its account's opening
  (``budget.transactions`` and ``budget.transaction_entries``, both of which
  carry ``account_id`` and ``settled_on``);
* an OPENING may not move forward past a movement that already exists
  (``budget.account_openings``).

Both are cross-table facts, so they live in **deferred constraint triggers**
validating at COMMIT.  Deferral is not incidental: restating an account's
opening forward is legitimate when the movements in the way are being re-dated
or removed in the SAME transaction, which is exactly what the account-10 repair
(**N-379**) does.  An immediate trigger would refuse that by statement order.

**The service layer states the same rule in WORDS, and that pairing is the
established shape here** -- ``ck_transactions_settle_day_needs_a_record`` beside
:func:`app.services.status_seam.reject_settle_day_without_a_record`.
:func:`app.services.cash_ledger.reject_movement_before_books_open` is the
sentence a date box gets; this is why a bulk ``UPDATE``, a raw statement, a
psql session or a writer nobody enumerated cannot produce the state anyway.
The bulk writer is not hypothetical: ``reconcile_service.record_settled_days``
stamps a statement's day onto ticked purchases with ``query.update()`` and has
no ORM instance for the service rule to fire on.

Three callers must produce identical infrastructure, exactly as
:mod:`app.audit_infrastructure` and :mod:`app.posting_infrastructure` do:

1. The Alembic migration that legalises the existing rows and installs the
   constraint (``d3b6f1c8a274``) -- ``apply_opening_infrastructure(op.execute)``
   AFTER its restatement, because the constraint refuses the state the
   restatement exists to leave behind.
2. ``scripts/init_database.py``, whose fresh-database path builds the schema
   with ``db.create_all()`` + an Alembic ``stamp`` and so never runs the
   migration chain.
3. ``scripts/build_test_template.py``, which runs the chain and then RE-applies
   idempotently so the latest in-code definition wins over migration-frozen
   state.

**A deferred constraint trigger makes DDL on its table illegal until its events
drain, and that is worth knowing before writing the next migration.**
PostgreSQL refuses ``CREATE INDEX`` (and other DDL) on a table carrying pending
trigger events -- "cannot CREATE INDEX ... because it has pending trigger
events" -- so a transaction that WRITES a movement row and then alters that
table fails.  The remedy is ``SET CONSTRAINTS ALL IMMEDIATE`` before the DDL,
which runs the checks in place rather than deferring them; it is what
``tests/test_scripts/test_integrity_check.py`` and
``tests/test_models/test_clearing_link_schema.py`` do before re-creating an
index and dropping a NOT NULL.

**The case a reader will NOT think of is a MIGRATION**, and it is the norm here
rather than the exception: this project puts one-time backfills in the Alembic
revision that changes the schema, so an revision that alters
``budget.transactions`` or ``budget.transaction_entries`` AND writes movement
rows in the same transaction is the ordinary shape -- and it fails with
``ObjectInUse`` naming the table, not with the message
:func:`app.services.cash_ledger.reject_movement_before_books_open` gives.
Order the DDL first, or drain with ``SET CONSTRAINTS ALL IMMEDIATE``.  The same property has
been true of ``ck_account_postings_balanced`` since it shipped; this module
states it because a second deferred trigger makes it twice as likely to be met.

**Caller contract: all three tables must already exist.**  ``check_function_bodies``
validates the table references at ``CREATE FUNCTION`` time, so applying this
before the tables are materialised fails loudly -- the right signal, and the
same contract :mod:`app.posting_infrastructure` documents.
"""

from __future__ import annotations

from typing import Callable


#: The governing-row lookup, stated ONCE in SQL so both triggers below read
#: "which opening record governs" the same way -- and the same way
#: ``cash_ledger.account_opening_fact`` reads it in Python.  ``created_at``
#: DESC then ``id`` DESC is the recording order: the table is append-only and
#: the latest restatement governs (ruling **R-HE**).
_OPENED_ON_FUNCTION = "budget.account_books_opened_on"

_MOVEMENT_FUNCTION = "budget.assert_movement_after_books_open"
_OPENING_FUNCTION = "budget.assert_books_open_before_books_movements"

_MOVEMENT_TRIGGER = "ck_movement_after_books_open"
_OPENING_TRIGGER = "ck_books_open_before_movements"

#: The two tables carrying a dated cash movement.  Both are
#: :class:`app.models.mixins.SettleDatedMixin` tables -- one row per settled
#: transaction and one per posted purchase -- and both carry ``account_id``,
#: which is what lets ONE trigger function serve both.
_MOVEMENT_TABLES = ("budget.transactions", "budget.transaction_entries")
_OPENINGS_TABLE = "budget.account_openings"


_CREATE_OPENED_ON_SQL = f"""
CREATE OR REPLACE FUNCTION {_OPENED_ON_FUNCTION}(p_account_id INTEGER)
RETURNS DATE AS $$
    -- The GOVERNING opening record's day, or NULL when the account carries
    -- none.  ``budget.account_openings`` is append-only and the latest
    -- RECORDING instant governs (ruling R-HE); ``id`` breaks a same-instant
    -- tie, exactly as the Python loader does, so the two cannot disagree
    -- about which restatement is in force.
    SELECT opened_on
      FROM budget.account_openings
     WHERE account_id = p_account_id
     ORDER BY created_at DESC, id DESC
     LIMIT 1;
$$ LANGUAGE sql STABLE
"""


_CREATE_MOVEMENT_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_MOVEMENT_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_opened_on DATE;
BEGIN
    -- A row with no settle day states nothing about when money moved: a
    -- Projected row, a cancelled one, and the revert that CLEARS the pair all
    -- land here.  Bounding them would break the unlock path ruling R-EG keeps
    -- open, which is the same carve-out ``settle_day.record_settle_day``
    -- makes for its own clear arm.
    IF NEW.settled_on IS NULL THEN
        RETURN NULL;
    END IF;

    v_opened_on := {_OPENED_ON_FUNCTION}(NEW.account_id);

    -- An account carrying NO opening record is a broken invariant, and it is
    -- deliberately NOT raised here.  Every account gets one at creation and
    -- migration a7c41f9d2b60 backfilled the rest, so the state is unreachable
    -- through any door; where it did occur, the READ side already refuses it
    -- loudly -- ``cash_ledger.account_opening_fact`` raises rather than
    -- fabricating a level -- so the account renders no balance at all and
    -- nothing silently goes wrong.  Raising here would make this trigger
    -- enforce a SECOND invariant ("every account has an opening") that no
    -- other constraint states, and would surface it as a COMMIT abort on an
    -- unrelated write path.
    IF v_opened_on IS NOT NULL AND NEW.settled_on <= v_opened_on THEN
        RAISE EXCEPTION
            '%.% % is dated % on account %, on or before the day that '
            'account''s books open (%); the opening equity is the closing '
            'balance for its own day, so this money is already inside it',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, NEW.id, NEW.settled_on,
            NEW.account_id, v_opened_on;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


_CREATE_OPENING_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_OPENING_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_earliest DATE;
BEGIN
    -- Only the GOVERNING record constrains anything.  An earlier restatement
    -- is history: the table is append-only precisely so what the opening USED
    -- to be survives, and a superseded row saying something the live data now
    -- contradicts is a record, not a violation.  Evaluated at COMMIT, so a
    -- transaction inserting two restatements checks only the one that wins.
    IF NEW.id IS DISTINCT FROM (
        SELECT id
          FROM budget.account_openings
         WHERE account_id = NEW.account_id
         ORDER BY created_at DESC, id DESC
         LIMIT 1
    ) THEN
        RETURN NULL;
    END IF;

    -- The earliest day the account records money moving, over BOTH movement
    -- tables -- the same union the movement trigger's two attachments cover
    -- from the other side.
    SELECT MIN(settled_on) INTO v_earliest FROM (
        SELECT settled_on
          FROM budget.transactions
         WHERE account_id = NEW.account_id AND settled_on IS NOT NULL
        UNION ALL
        SELECT settled_on
          FROM budget.transaction_entries
         WHERE account_id = NEW.account_id AND settled_on IS NOT NULL
    ) AS movements;

    IF v_earliest IS NOT NULL AND v_earliest <= NEW.opened_on THEN
        RAISE EXCEPTION
            'account % cannot open its books on %: a movement is already '
            'dated %, and an opening equity is the closing balance for its '
            'own day, so that money would be counted twice',
            NEW.account_id, NEW.opened_on, v_earliest;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def _drop_trigger_sql(trigger: str, table: str) -> str:
    """Return the guarded ``DROP TRIGGER`` for *trigger* on *table*.

    PostgreSQL has no ``CREATE CONSTRAINT TRIGGER IF NOT EXISTS``, so every
    apply pairs a guarded drop with a fresh create to stay idempotent.  The
    same pattern :mod:`app.posting_infrastructure` uses, spelled as a helper
    here because this module attaches three triggers rather than one.

    Args:
        trigger: The trigger's name.
        table: The schema-qualified table it is attached to.

    Returns:
        The ``DROP TRIGGER IF EXISTS`` statement.
    """
    return f"DROP TRIGGER IF EXISTS {trigger} ON {table}"


def _create_movement_trigger_sql(table: str) -> str:
    """Return the ``CREATE CONSTRAINT TRIGGER`` attaching the movement rule.

    ``AFTER INSERT OR UPDATE OF settled_on, account_id`` rather than a bare
    ``AFTER INSERT OR UPDATE``: those two columns are the whole of what the
    predicate reads, and ``budget.transactions`` is updated on every status
    change, amount correction and template regeneration, so a column list is
    the difference between one indexed lookup per SETTLE and one per WRITE.

    ``DEFERRABLE INITIALLY DEFERRED`` moves the check to COMMIT, which is what
    lets one transaction re-date a movement and restate its account's opening
    in either order (the account-10 repair, **N-379**).

    There is no DELETE arm, and the reason is the one
    :mod:`app.posting_infrastructure` gives for its own: deleting rows can only
    ever move ``MIN(settled_on)`` LATER, so a delete cannot break this
    invariant, and a CASCADE disposal must not have to satisfy it mid-flight.

    Args:
        table: The schema-qualified movement table to attach to.

    Returns:
        The ``CREATE CONSTRAINT TRIGGER`` statement.
    """
    return (
        f"CREATE CONSTRAINT TRIGGER {_MOVEMENT_TRIGGER} "
        f"AFTER INSERT OR UPDATE OF settled_on, account_id ON {table} "
        "DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {_MOVEMENT_FUNCTION}()"
    )


#: The openings side fires on INSERT alone: the table is append-only, so a
#: restatement IS an insert, and the ORM guard
#: (``AccountOpening._block_update`` / ``_block_delete``) plus the absence of
#: any UPDATE door mean there is no legitimate update path to police.  A raw
#: ``UPDATE`` would slip past -- which is why the arm is stated rather than
#: assumed away: closing it would mean policing every superseded row on every
#: write, and the row that decides money is the governing one, which can only
#: be reached by inserting.
_CREATE_OPENING_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_OPENING_TRIGGER} "
    f"AFTER INSERT OR UPDATE ON {_OPENINGS_TABLE} "
    "DEFERRABLE INITIALLY DEFERRED "
    f"FOR EACH ROW EXECUTE FUNCTION {_OPENING_FUNCTION}()"
)


def apply_opening_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently materialise the account-books boundary constraint.

    Executes, in order:

    1. ``CREATE OR REPLACE FUNCTION budget.account_books_opened_on`` -- the
       governing-record lookup both triggers read.
    2. ``CREATE OR REPLACE FUNCTION budget.assert_movement_after_books_open``
       and its constraint trigger on ``budget.transactions`` AND
       ``budget.transaction_entries``.
    3. ``CREATE OR REPLACE FUNCTION
       budget.assert_books_open_before_books_movements`` and its constraint
       trigger on ``budget.account_openings``.

    Every statement is idempotent (``CREATE OR REPLACE FUNCTION`` swaps the
    body; ``DROP TRIGGER IF EXISTS`` + ``CREATE`` re-pins each trigger), so a
    second run is indistinguishable from the first.

    **The caller must have LEGALISED the data first.**  Applying this to a
    database still holding a movement dated on or before its account's opening
    does not fail here -- a constraint trigger validates writes, not existing
    rows -- but the next write touching such a row will abort at COMMIT.  The
    migration therefore restates the openings BEFORE calling this.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    executor(_CREATE_OPENED_ON_SQL)
    executor(_CREATE_MOVEMENT_FUNC_SQL)
    for table in _MOVEMENT_TABLES:
        executor(_drop_trigger_sql(_MOVEMENT_TRIGGER, table))
        executor(_create_movement_trigger_sql(table))
    executor(_CREATE_OPENING_FUNC_SQL)
    executor(_drop_trigger_sql(_OPENING_TRIGGER, _OPENINGS_TABLE))
    executor(_CREATE_OPENING_TRIGGER_SQL)


def remove_opening_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_opening_infrastructure` for a migration downgrade.

    Drops the three triggers first and then the three functions, so a function
    is never dropped while a trigger still references it.  Every statement uses
    ``IF EXISTS``, so this is idempotent and a clean no-op on a database that
    never carried the infrastructure.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Same contract as :func:`apply_opening_infrastructure`.
    """
    for table in _MOVEMENT_TABLES:
        executor(_drop_trigger_sql(_MOVEMENT_TRIGGER, table))
    executor(_drop_trigger_sql(_OPENING_TRIGGER, _OPENINGS_TABLE))
    executor(f"DROP FUNCTION IF EXISTS {_MOVEMENT_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_OPENING_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_OPENED_ON_FUNCTION}(INTEGER)")
