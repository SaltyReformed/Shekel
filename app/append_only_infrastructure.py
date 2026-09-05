"""Shared definitions for the append-only account tables' database-tier refusal.

**Three account tables record FACTS that are never edited**, each a statement
about a moment that a correction ANSWERS rather than rewrites:

* ``budget.account_anchor_history`` -- what a bank showed on a day (ruling
  **R-DH**).  The day is what every clearing link was recorded against (ruling
  **R-FL**), so editing one silently re-points cleared purchases at a statement
  that did not show them.  Finding **N-287**.
* ``budget.account_openings`` -- what an account held before its records begin
  (ruling **R-GX**); the latest restatement governs, so an edit destroys the
  record that the figure ever changed.
* ``budget.loan_anchor_events`` -- a loan's owed balance at a moment (decision
  D-A); an origination row must stay reconstructible from the same immutable
  ``LoanParams`` source.

**This module is what makes "append-only" TRUE rather than customary** (plan
step X-f3c-2c, ruling **R-HY**).  The three tables carried SQLAlchemy
``before_update`` / ``before_delete`` listeners and nothing else, and that tier
sees only writes the ORM mediates.  It is blind to a bulk ``query.update()`` --
a spelling this app already uses in production, at
``reconcile_service.record_settled_days``, which stamps a statement's day onto
ticked purchases and holds no ORM instance for a listener to fire on -- and
blind to a raw statement, a psql session and a migration.

**Why a trigger and not a REVOKE.**  ``budget.journal_entries`` and
``budget.account_postings`` are held append-only for the runtime by
``posting_infrastructure.apply_ledger_append_only_privileges``, a
``REVOKE UPDATE, DELETE ... FROM shekel_app``.  That posture refuses every
spelling the APP can produce and is **invisible to the test suite**, which
connects as the owner role: a door written to edit an assertion would pass
locally and fail in production.  A trigger refuses the app, the suite, psql and
a migration alike, so the suite proves the rule that ships rather than a weaker
local copy of it.  The named Python exception the :mod:`app.models.append_only`
listeners raise stays on top of it, because a ``psycopg2.errors.RaiseException``
naming a trigger is a worse thing for a developer to read than a Shekel
exception naming the table and the remedy.

**What that costs, stated rather than argued away.**  This project puts
one-time backfills in the Alembic revision that changes the schema, and
migration ``e5b2c8a17d34`` backfilled ``account_anchor_history.recorded_on``
exactly that way.  Under these triggers such a revision must
``remove_append_only_infrastructure(op.execute)`` first and re-apply after --
two lines, visible in the diff, and refused loudly if forgotten.  That is the
RIGHT behaviour: rewriting a stored assertion is precisely the act ruling
**R-HJ** already says a repair may not perform ("a repair is performed through
the app's own DOORS and never by a migration writing money rows").

**THREE ARMS, THREE TIMINGS, because they answer three different questions**
(plan step X-f3c-2d, ruling **balance:R-IC**; the arc is named because
``bank_import:R-IC`` was minted the same day and a bare id now resolves to
two rules).  X-f3c-2c shipped one ``BEFORE UPDATE OR
DELETE`` row trigger and justified it with a single sentence about the UPDATE
arm; a refutation pass then broke the DELETE arm twice.  What the arms actually
ask:

* **UPDATE is a question about the STATEMENT.**  An edit is refused whatever
  else the transaction does, so a plain ``BEFORE UPDATE`` row trigger is exact.
* **DELETE is a question about the transaction's END STATE**, which is why it
  is a ``DEFERRABLE INITIALLY DEFERRED`` constraint trigger.  All three tables
  carry :class:`app.models.mixins.AccountScopedMixin`'s ``ON DELETE CASCADE``,
  so disposing of an account is meant to take its history with it, and the
  refusal has to let that through while stopping a row being picked off.  The
  test is whether the owning account is gone -- but "gone" is only meaningful
  at COMMIT.  Asked mid-statement it was fooled by two ordinary statements in
  one transaction, measured: ``DELETE FROM budget.accounts WHERE id=20`` then
  ``INSERT`` of the same id left the account standing with its assertions
  destroyed, because at the instant the cascade ran the account genuinely did
  not exist.  Deferred, the same predicate refuses it.
* **TRUNCATE is invisible to row triggers**, so it gets a ``BEFORE TRUNCATE``
  statement trigger of its own.  It is the one spelling that destroyed history
  BOTH unrefused and unrecorded: ``system.audit_log`` is written by a row
  trigger too, so a measured ``TRUNCATE budget.account_openings`` with every
  account still standing took the table to zero and left the audit log
  byte-identical.  Every other path that removes a row from these tables writes
  ``to_jsonb(OLD)`` to ``system.audit_log`` first (all three tables are in
  ``audit_infrastructure.AUDITED_TABLES``), so closing TRUNCATE is what makes
  "history is never destroyed without a record" true rather than usual.  That
  conservation is also why these tables need no archive of their own: the audit
  row already holds every column of every deleted row.

**What deferring the DELETE arm costs, stated rather than argued away.**  A
transaction that has deleted from one of these tables holds pending trigger
events, and PostgreSQL then refuses ``ALTER TABLE`` on it for the rest of that
transaction -- the same cost :mod:`app.opening_infrastructure` records, and
measured here.  It binds one caller: :func:`tests._test_helpers
.append_only_guard_lifted` must disable the triggers BEFORE the delete it
means to permit, never after.

**Two limits worth naming rather than leaving a reader to assume more.**

* ``session_replication_role = replica`` disables triggers outright, which is
  what ``pg_restore --disable-triggers`` sets.  The prod-to-dev clone is a
  documented workflow here, so a restore can write rows this module would have
  refused; only future writes are guarded.  The same limit
  :mod:`app.opening_infrastructure` records.
* A superuser or the table owner can drop the triggers.  That is not a hole --
  it is how the migration escape above works -- but it means the guarantee is
  against every ROUTINE writer rather than against a determined one.  The
  runtime role cannot: ``shekel_app`` holds no ``ALTER TABLE``, and
  ``scripts/init_db_role.sql`` grants it no ``TRUNCATE`` either.

Three callers must produce identical infrastructure, exactly as
:mod:`app.audit_infrastructure`, :mod:`app.posting_infrastructure` and
:mod:`app.opening_infrastructure` do:

1. The Alembic migration that installs it (``f4a7c2d9e51b``, amended by
   ``b8e3d5a06c94``).
2. ``scripts/init_database.py``, whose fresh-database path builds the schema
   with ``db.create_all()`` + an Alembic ``stamp`` and so never runs the
   migration chain.
3. ``scripts/build_test_template.py``, which runs the chain and then RE-applies
   idempotently so the latest in-code definition wins over migration-frozen
   state.

**Caller contract: all three tables and ``budget.accounts`` must already
exist.**  ``check_function_bodies`` validates the function's table references at
``CREATE FUNCTION`` time, so applying this before they are materialised fails
loudly -- the right signal, and the same contract the two sibling modules
document.
"""

from __future__ import annotations

from typing import Callable


#: The one trigger function, serving all three tables and all three arms.  It
#: can, because the only column it reads besides ``TG_OP`` is ``account_id``,
#: which all three carry from :class:`app.models.mixins.AccountScopedMixin` --
#: the same property that lets ``opening_infrastructure``'s movement trigger
#: serve two tables from one body.
_APPEND_ONLY_FUNCTION = "budget.refuse_append_only_change"

#: One name per ARM, because the three differ in timing and a single trigger
#: cannot hold them: ``BEFORE UPDATE`` row, deferred ``AFTER DELETE``
#: constraint, ``BEFORE TRUNCATE`` statement.  Exported because every caller
#: that disables the guard must disable ALL of it -- a lift that named only the
#: update arm would leave a delete refused and read as a passing test.
APPEND_ONLY_TRIGGERS: tuple[str, ...] = (
    "ck_append_only",
    "ck_append_only_delete",
    "ck_append_only_truncate",
)

_UPDATE_TRIGGER, _DELETE_TRIGGER, _TRUNCATE_TRIGGER = APPEND_ONLY_TRIGGERS

#: The tables held append-only at this tier, schema-qualified.  ``ref`` and the
#: posting ledger are deliberately absent: the ledger has its own posture
#: (``posting_infrastructure.apply_ledger_append_only_privileges``) and no
#: account-scoped column for this function to read.
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "budget.account_anchor_history",
    "budget.account_openings",
    "budget.loan_anchor_events",
)


_CREATE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_APPEND_ONLY_FUNCTION}()
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


def _drop_trigger_sql(table: str) -> tuple[str, ...]:
    """Return the guarded ``DROP TRIGGER`` for each arm on *table*.

    PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so every apply pairs a
    guarded drop with a fresh create to stay idempotent -- the same pattern
    :mod:`app.posting_infrastructure` and :mod:`app.opening_infrastructure`
    use.  All three names are dropped whatever is installed, which is what
    lets this run against a database still carrying X-f3c-2c's single
    combined trigger.

    Args:
        table: The schema-qualified table the triggers are attached to.

    Returns:
        One ``DROP TRIGGER IF EXISTS`` statement per name in
        :data:`APPEND_ONLY_TRIGGERS`.
    """
    return tuple(
        f"DROP TRIGGER IF EXISTS {name} ON {table}"
        for name in APPEND_ONLY_TRIGGERS
    )


def _create_trigger_sql(table: str) -> tuple[str, ...]:
    """Return the three ``CREATE TRIGGER`` statements attaching *table*'s arms.

    No column list on the update arm: an append-only table has no column whose
    edit is legal, so naming any would be an allowlist a future column
    silently joins.  ``FOR EACH ROW`` on the two arms whose message names a
    row; ``FOR EACH STATEMENT`` on TRUNCATE, which has no row to name.

    Args:
        table: The schema-qualified table to attach to.

    Returns:
        The update, delete and truncate ``CREATE TRIGGER`` statements.
    """
    return (
        f"CREATE TRIGGER {_UPDATE_TRIGGER} "
        f"BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}()",
        f"CREATE CONSTRAINT TRIGGER {_DELETE_TRIGGER} "
        f"AFTER DELETE ON {table} "
        f"DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}()",
        f"CREATE TRIGGER {_TRUNCATE_TRIGGER} "
        f"BEFORE TRUNCATE ON {table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}()",
    )


def apply_append_only_infrastructure(
    executor: Callable[[str], object],
) -> None:
    """Idempotently install the append-only refusal on all three tables.

    Executes ``CREATE OR REPLACE FUNCTION budget.refuse_append_only_change``,
    then a guarded drop plus a fresh ``CREATE TRIGGER`` for each arm of each
    table in :data:`APPEND_ONLY_TABLES`.  Every statement is idempotent, so a
    second run is indistinguishable from the first.

    **The caller must have LEGALISED nothing, and that is the difference from
    :func:`app.opening_infrastructure.apply_opening_infrastructure`.**  That
    module's constraint refuses a STATE, so existing rows had to be repaired
    before it could be installed; this one refuses STATEMENTS, so no row that
    already exists can be in violation and there is nothing to restate first.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    executor(_CREATE_FUNCTION_SQL)
    for table in APPEND_ONLY_TABLES:
        for statement in _drop_trigger_sql(table):
            executor(statement)
        for statement in _create_trigger_sql(table):
            executor(statement)


def remove_append_only_infrastructure(
    executor: Callable[[str], object],
) -> None:
    """Inverse of :func:`apply_append_only_infrastructure`.

    Drops every arm on every table and then the function, so nothing is
    dropped while something still references it.  Every statement uses
    ``IF EXISTS``, so this is idempotent and a clean no-op on a database that
    never carried the infrastructure.

    **It is also the documented escape for a migration that must rewrite these
    tables** -- adding a column and backfilling it is the case, and this
    project puts such a backfill in the revision that adds the column.  Call
    this, do the work, call :func:`apply_append_only_infrastructure` again.
    Two lines, both visible in the diff, which is the whole point: the escape
    is deliberate and reviewable rather than ambient.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Same contract as :func:`apply_append_only_infrastructure`.
    """
    for table in APPEND_ONLY_TABLES:
        for statement in _drop_trigger_sql(table):
            executor(statement)
    executor(f"DROP FUNCTION IF EXISTS {_APPEND_ONLY_FUNCTION}()")
