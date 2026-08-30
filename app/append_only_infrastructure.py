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
locally and fail in production.  A trigger refuses everyone identically -- the
app, the suite, psql, a migration -- so the suite proves the rule that ships
rather than a weaker local copy of it.  The named Python exception the
:mod:`app.models.append_only` listeners raise stays on top of it, because a
``psycopg2.errors.RaiseException`` naming a trigger is a worse thing for a
developer to read than a Shekel exception naming the table and the remedy.

**What that costs, stated rather than argued away.**  This project puts
one-time backfills in the Alembic revision that changes the schema, and
migration ``e5b2c8a17d34`` backfilled ``account_anchor_history.recorded_on``
exactly that way.  Under this trigger such a revision must
``remove_append_only_infrastructure(op.execute)`` first and re-apply after --
two lines, visible in the diff, and refused loudly if forgotten.  That is the
RIGHT behaviour: rewriting a stored assertion is precisely the act ruling
**R-HJ** already says a repair may not perform ("a repair is performed through
the app's own DOORS and never by a migration writing money rows").

**The DELETE arm asks whether the OWNING ACCOUNT still exists, and that is the
whole of what separates disposal from vandalism.**  All three tables carry
:class:`app.models.mixins.AccountScopedMixin`'s ``ON DELETE CASCADE``, so
deleting an account is meant to take its history with it.  PostgreSQL runs that
cascade as an ``AFTER DELETE`` referential action on ``budget.accounts``, which
issues the child ``DELETE`` only once the parent row is gone from the
transaction's own snapshot -- so this trigger sees no account and returns.  A
DELETE aimed at one history row while its account still stands sees the account
and raises.  No ordering assumption beyond that one, and it is PostgreSQL's own
documented referential-action order rather than a coincidence of this schema.

**A plain row trigger, NOT a deferred constraint trigger**, which is the
opposite choice from :mod:`app.opening_infrastructure` and for a stated reason.
That module's rule is about the account's RESULTING STATE -- movements versus
books, across three tables -- so it has to run at COMMIT, and it pays for that
with pending trigger events that make ``ALTER TABLE`` illegal for the rest of
the transaction (its docstring records the two CI failures that cost).  This
rule is about the STATEMENT: an UPDATE is refused whatever else the transaction
does, so there is nothing to defer and nothing to reserve the table against.

**Two limits worth naming rather than leaving a reader to assume more.**

* ``session_replication_role = replica`` disables triggers outright, which is
  what ``pg_restore --disable-triggers`` sets.  The prod-to-dev clone is a
  documented workflow here, so a restore can write rows this module would have
  refused; only future writes are guarded.  The same limit
  :mod:`app.opening_infrastructure` records.
* A superuser or the table owner can drop the trigger.  That is not a hole --
  it is how the migration escape above works -- but it means the guarantee is
  against every ROUTINE writer rather than against a determined one.  The
  runtime role cannot: ``shekel_app`` holds no ``ALTER TABLE``.

Three callers must produce identical infrastructure, exactly as
:mod:`app.audit_infrastructure`, :mod:`app.posting_infrastructure` and
:mod:`app.opening_infrastructure` do:

1. The Alembic migration that installs it (``f4a7c2d9e51b``).
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


#: The one trigger function, serving all three tables.  It can, because the
#: only column it reads besides ``TG_OP`` is ``account_id``, which all three
#: carry from :class:`app.models.mixins.AccountScopedMixin` -- the same
#: property that lets ``opening_infrastructure``'s movement trigger serve two
#: tables from one body.
_APPEND_ONLY_FUNCTION = "budget.refuse_append_only_change"

_APPEND_ONLY_TRIGGER = "ck_append_only"

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
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            '%.% is append-only; UPDATE rejected for id=%. Record a '
            'correction by inserting a new row.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    -- DELETE.  The owning account still standing means this is a row being
    -- picked off rather than an account being disposed of: the ON DELETE
    -- CASCADE from budget.accounts is executed by PostgreSQL only after the
    -- parent row has left this transaction's snapshot, so a genuine disposal
    -- finds no account here and falls through.
    IF EXISTS (SELECT 1 FROM budget.accounts WHERE id = OLD.account_id) THEN
        RAISE EXCEPTION
            '%.% is append-only; DELETE rejected for id=%. History goes only '
            'with its account.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, OLD.id;
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""


def _drop_trigger_sql(table: str) -> str:
    """Return the guarded ``DROP TRIGGER`` for *table*.

    PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so every apply pairs a
    guarded drop with a fresh create to stay idempotent -- the same pattern
    :mod:`app.posting_infrastructure` and :mod:`app.opening_infrastructure`
    use.

    Args:
        table: The schema-qualified table the trigger is attached to.

    Returns:
        The ``DROP TRIGGER IF EXISTS`` statement.
    """
    return f"DROP TRIGGER IF EXISTS {_APPEND_ONLY_TRIGGER} ON {table}"


def _create_trigger_sql(table: str) -> str:
    """Return the ``CREATE TRIGGER`` attaching the refusal to *table*.

    ``BEFORE UPDATE OR DELETE`` with no column list: an append-only table has
    no column whose edit is legal, so naming any would be an allowlist a future
    column silently joins.  ``FOR EACH ROW`` because the message names the row.

    Args:
        table: The schema-qualified table to attach to.

    Returns:
        The ``CREATE TRIGGER`` statement.
    """
    return (
        f"CREATE TRIGGER {_APPEND_ONLY_TRIGGER} "
        f"BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}()"
    )


def apply_append_only_infrastructure(
    executor: Callable[[str], object],
) -> None:
    """Idempotently install the append-only refusal on all three tables.

    Executes ``CREATE OR REPLACE FUNCTION budget.refuse_append_only_change``,
    then a guarded drop plus a fresh ``CREATE TRIGGER`` for each table in
    :data:`APPEND_ONLY_TABLES`.  Every statement is idempotent, so a second run
    is indistinguishable from the first.

    **The caller must have LEGALISED nothing, and that is the difference from
    :func:`app.opening_infrastructure.apply_opening_infrastructure`.**  That
    module's constraint refuses a STATE, so existing rows had to be repaired
    before it could be installed; this one refuses a STATEMENT, so no row that
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
        executor(_drop_trigger_sql(table))
        executor(_create_trigger_sql(table))


def remove_append_only_infrastructure(
    executor: Callable[[str], object],
) -> None:
    """Inverse of :func:`apply_append_only_infrastructure`.

    Drops the three triggers and then the function, so nothing is dropped while
    something still references it.  Every statement uses ``IF EXISTS``, so this
    is idempotent and a clean no-op on a database that never carried the
    infrastructure.

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
        executor(_drop_trigger_sql(table))
    executor(f"DROP FUNCTION IF EXISTS {_APPEND_ONLY_FUNCTION}()")
