"""Shared definitions for the append-only account tables' database-tier refusal.

**Four account tables record FACTS that are never edited**, each a statement
about a moment that a correction ANSWERS rather than rewrites:

* ``budget.account_anchor_history`` -- the LEVEL RELATION: what a bank, or the
  owner, observed an account to hold at the close of a day (rulings **R-DH**,
  **R-IS**; plan step ``balance:X-bj-1`` moved the bank's placements in).  The
  day is what every clearing link was recorded against (ruling **R-FL**), so
  editing one silently re-points cleared purchases at a statement that did not
  show them.  Finding **N-287**.
* ``budget.anchor_releases`` -- the withdrawal of one bank level, and its
  cause (``balance:X-bj-1``).  Editing one would let a level be silently
  reinstated, which is the ``$150.00`` hole the release exists to close.
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
  is a ``DEFERRABLE INITIALLY DEFERRED`` constraint trigger.  All four tables
  carry :class:`app.models.mixins.AccountScopedMixin`'s ``ON DELETE CASCADE``,
  so disposing of an account is meant to take its history with it, and the
  refusal has to let that through while stopping a row being picked off.  The
  test is whether the owning account is gone -- but "gone" is only meaningful
  at COMMIT.  Asked mid-statement it was fooled by two ordinary statements in
  one transaction, measured: ``DELETE FROM budget.accounts WHERE id=20`` then
  ``INSERT`` of the same id left the account standing with its assertions
  destroyed, because at the instant the cascade ran the account genuinely did
  not exist.  Deferred, the same predicate refuses it.

  **Two of the four tables have a SECOND owner, and the same end-state test is
  asked of it** (plan step ``balance:X-bj-1``, developer ruling 2026-09-16).
  A bank level (an ``account_anchor_history`` row with a
  ``statement_import_id``) is its file's conclusion and cascades with its
  import; a release cascades with the level it withdrew.  So on those two
  tables the arm first asks whether that owner is gone at COMMIT -- the import,
  the level -- and permits the disposal when it is, before falling through to
  the account test.  A delete-and-recreate of the IMPORT id is refused by the
  same reasoning that refuses one of the account id.  These are the only rows
  in the family with an owner besides the account, and the arm names each by
  table rather than reading a column the sibling tables lack.

* **UPDATE admits exactly ONE transition, on ONE table.**  A release names the
  import whose lines undercut the level through a key declared
  ``ON DELETE SET NULL (released_by_import_id)``, and a referential action is
  an UPDATE this trigger sees.  The arm lets it through when the old cause was
  present, every other column is byte-equal (``to_jsonb(NEW)`` against the
  old row with that one field nulled) AND the import it named no longer
  exists -- which is true inside the referential action, since it runs after
  the DELETE has taken effect, and false for a hand-written UPDATE erasing a
  cause that still stands (found by the adversarial code review of the
  step).  It is not an allowlist of columns: it is the one statement the
  schema itself makes.
* **TRUNCATE is invisible to row triggers**, so it gets a ``BEFORE TRUNCATE``
  statement trigger of its own.  It is the one spelling that destroyed history
  BOTH unrefused and unrecorded: ``system.audit_log`` is written by a row
  trigger too, so a measured ``TRUNCATE budget.account_openings`` with every
  account still standing took the table to zero and left the audit log
  byte-identical.  Every other path that removes a row from these tables writes
  ``to_jsonb(OLD)`` to ``system.audit_log`` first (all four tables are in
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
   ``b8e3d5a06c94``, and by ``balance:X-bj-1``'s revision, which added the
   fourth table and the two owner arms).
2. ``scripts/init_database.py``, whose fresh-database path builds the schema
   with ``db.create_all()`` + an Alembic ``stamp`` and so never runs the
   migration chain.
3. ``scripts/build_test_template.py``, which runs the chain and then RE-applies
   idempotently so the latest in-code definition wins over migration-frozen
   state.

**Caller contract: all four tables, ``budget.accounts`` and
``budget.statement_imports`` must already exist.**  ``CREATE TRIGGER`` needs
its table, so applying this before they are materialised fails loudly -- the
right signal, and the same contract the two sibling modules document.  (An
earlier version of this paragraph credited ``check_function_bodies`` with
resolving the function's table references at ``CREATE FUNCTION`` time; for a
PL/pgSQL body it validates syntax only, and a table name inside a statement
resolves at first execution.)  One consequence for a chain replay from the
start: between ``f4a7c2d9e51b`` and ``d2e9f4a17c63`` the CURRENT body is
installed over a level table that does not yet carry ``statement_import_id``;
the field is read only on a DELETE from that table, which no revision in that
window performs, so an empty replay never evaluates it.
"""

from __future__ import annotations

from typing import Callable


#: The one trigger function, serving all four tables and all three arms.  The
#: column every arm reads is ``account_id``, which all four carry from
#: :class:`app.models.mixins.AccountScopedMixin`; the two tables with a second
#: owner (``statement_import_id`` on a level, ``anchor_id`` and
#: ``released_by_import_id`` on a release) are read only inside a branch
#: guarded by ``TG_TABLE_NAME``, because PL/pgSQL resolves a record's fields at
#: execution and a sibling table has no such field to resolve.  One body, so
#: the family's disposal rules are stated in one place rather than four.
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
    "budget.anchor_releases",
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
                   to_jsonb(OLD), '{{released_by_import_id}}', 'null'::jsonb
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
    *,
    tables: tuple[str, ...] = APPEND_ONLY_TABLES,
) -> None:
    """Idempotently install the append-only refusal on *tables*.

    Executes ``CREATE OR REPLACE FUNCTION budget.refuse_append_only_change``,
    then a guarded drop plus a fresh ``CREATE TRIGGER`` for each arm of each
    table in *tables*.  Every statement is idempotent, so a second run is
    indistinguishable from the first.

    **A MIGRATION names its tables literally; the two script callers take the
    default.**  The default is the module constant, which is what a database
    built at HEAD wants -- and what a migration replayed from the START of
    the chain must NOT read, because the constant names tables a later
    revision creates (``budget.anchor_releases`` since ``balance:X-bj-1``),
    and ``CREATE TRIGGER`` on a table that does not exist yet fails the whole
    replay.  ``f4a7c2d9e51b`` and ``b8e3d5a06c94`` therefore pass the three
    tables they were written for; the shape ``opening_infrastructure``'s
    ``arms`` parameter takes for the same reason.

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
        tables: The schema-qualified tables to attach every arm to.  A
            migration names its own literally; the two scripts take the
            default, :data:`APPEND_ONLY_TABLES`.
    """
    executor(_CREATE_FUNCTION_SQL)
    for table in tables:
        for statement in _drop_trigger_sql(table):
            executor(statement)
        for statement in _create_trigger_sql(table):
            executor(statement)


def remove_append_only_infrastructure(
    executor: Callable[[str], object],
    *,
    tables: tuple[str, ...] = APPEND_ONLY_TABLES,
) -> None:
    """Inverse of :func:`apply_append_only_infrastructure`.

    Drops every arm on every table in *tables* and then the function, so
    nothing is dropped while something still references it.  Every statement
    uses ``IF EXISTS``, so this is idempotent and a clean no-op on a database
    that never carried the infrastructure -- which is also what lets a
    migration's downgrade name a table a later revision has already dropped.

    **It is also the documented escape for a migration that must rewrite these
    tables** -- adding a column and backfilling it is the case, and this
    project puts such a backfill in the revision that adds the column.  Call
    this, do the work, call :func:`apply_append_only_infrastructure` again.
    Two lines, both visible in the diff, which is the whole point: the escape
    is deliberate and reviewable rather than ambient.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Same contract as :func:`apply_append_only_infrastructure`.
        tables: The schema-qualified tables to detach every arm from; the
            same contract as the apply's.
    """
    for table in tables:
        for statement in _drop_trigger_sql(table):
            executor(statement)
    executor(f"DROP FUNCTION IF EXISTS {_APPEND_ONLY_FUNCTION}()")
