"""Shared definitions for a transcribed pay stub's structural rule.

**A stub is never deleted and never moved to another profile** (plan step
``salary:S11-a``, ruling **R-SAL44**, which makes R-SAL42's fork 8a' --
"Nothing is ever deleted" -- true for every routine writer rather than for the absence
of a delete button).  The developer's words, as picked: *"Add a trigger on
salary.pay_stubs that refuses any DELETE of a stub and any change of its
profile, for every writer. 'Nothing is ever deleted' becomes true by structure.
Payday, base pay, the switch, notes and the stub's lines stay editable."*

**Three arms, one function:**

* ``BEFORE DELETE`` on ``salary.pay_stubs``, per row, refused outright.  Unlike
  :mod:`app.append_only_infrastructure`'s deferred delete arm there is no
  disposal to let through: nothing cascades INTO a stub, because its profile
  key is ``ON DELETE RESTRICT`` (``fk_pay_stubs_salary_profile_id``), so every
  delete of a stub row is somebody picking one off.  An owner who no longer
  wants a stub pricing switches it off (``use_for_pricing``).
* ``BEFORE UPDATE OF salary_profile_id`` on ``salary.pay_stubs``, per row,
  refused when the value actually changes.  A stub belongs to the job it was
  entered for.  The two composite keys already refuse moving a stub that holds
  a line amount; a stub holding only taxes and one-offs had no such anchor,
  and an adversarial review of this leaf moved one to another OWNER's profile
  with a plain ``UPDATE``.  An ``UPDATE`` that sets the column to its own value
  is not a move, and the ``WHEN`` clause lets it through.
* ``BEFORE TRUNCATE`` on all four stub tables, per statement (ruling
  **R-SAL46**, "Keep the line-table guard", which extends R-SAL44 past its
  words).  A ``TRUNCATE`` is a delete by another spelling that fires no row
  trigger and writes no audit row, so on ``pay_stubs`` it would erase every
  stub without a trace, and on a child table every stub's lines at once.
  Removing one mistyped line is a row ``DELETE``, which stays allowed and is
  audited.

**What stays editable, deliberately:** the stub's payday, base pay, switch and
notes, and every one of its lines.  The entry door (``S11-b``) edits a stub line
by line, and the audit log keeps the old figures (fork 4).

**Lifting it** -- a future migration that must remove stubs -- is
:func:`remove_pay_stub_infrastructure` before the write and
:func:`apply_pay_stub_infrastructure` after: two lines, visible in the diff,
and refused loudly if forgotten.  That is the cost
:mod:`app.append_only_infrastructure` states for its own family, accepted for
the same reason.

**Two limits, named rather than left to be assumed** -- the two that module
names for its own family.  ``session_replication_role = replica`` (which
``pg_restore --disable-triggers`` sets) fires no ordinary trigger, so a
superuser session in that mode deletes a stub unrefused and unaudited; and a
superuser or the table owner can drop the triggers.  The guarantee is against
every ROUTINE writer: the runtime role ``shekel_app`` holds no ``ALTER TABLE``
and no ``TRUNCATE``.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.append_only_infrastructure` states: the Alembic revision that
installs it (``5641f7729b68``, the same revision that creates the tables),
``scripts/init_database.py`` (a fresh database, no migration chain) and
``scripts/build_test_template.py`` (re-applied idempotently so the latest
in-code definition wins).  ``scripts/build_test_db_image.py`` counts
:data:`PAY_STUB_TRIGGERS` on the baked image so a template missing an arm is
rebuilt rather than trusted.

**Caller contract: the four stub tables must already exist.**  ``CREATE
TRIGGER`` needs its table.
"""

from __future__ import annotations

from typing import Callable

#: The one trigger function.  Its arms differ only in what they refuse, so one
#: body states all three messages in one place.
_TRIGGER_FUNCTION = "salary.refuse_pay_stub_loss"

_STUBS = "salary.pay_stubs"
_DELETE_TRIGGER = "refuse_pay_stub_delete"
_MOVE_TRIGGER = "refuse_pay_stub_move"
_TRUNCATE_TRIGGER = "refuse_pay_stub_truncate"

#: The four tables a ``TRUNCATE`` is refused on: the stub and its three kinds
#: of line.
_TRUNCATE_TABLES: tuple[str, ...] = (
    _STUBS,
    "salary.pay_stub_line_amounts",
    "salary.pay_stub_withholdings",
    "salary.pay_stub_one_offs",
)

#: ``(trigger name, schema-qualified table)`` for every attachment -- one entry
#: per trigger the database carries, so the image check can count them and a
#: lift can name them.  The truncate arm shares its name across the four tables
#: it guards (a trigger name is unique per table, not per database).
PAY_STUB_TRIGGERS: tuple[tuple[str, str], ...] = (
    (_DELETE_TRIGGER, _STUBS),
    (_MOVE_TRIGGER, _STUBS),
    *((_TRUNCATE_TRIGGER, table) for table in _TRUNCATE_TABLES),
)

_CREATE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- TRUNCATE first: it is the only arm with no OLD row to name.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION
            '%.% holds transcribed pay stubs; TRUNCATE rejected. A stub is '
            'never deleted: switch it off instead, or edit its lines one '
            'at a time.',
            TG_TABLE_SCHEMA, TG_TABLE_NAME;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'salary.pay_stubs is a transcribed record; DELETE rejected for '
            'id=%. Switch the stub off (use_for_pricing) instead.',
            OLD.id;
    END IF;
    -- UPDATE: the trigger's WHEN clause admits only a real change of profile.
    RAISE EXCEPTION
        'salary.pay_stubs id=% belongs to salary profile %; moving it to % '
        'is rejected. A stub stays with the profile it was entered for.',
        OLD.id, OLD.salary_profile_id, NEW.salary_profile_id;
END;
$$ LANGUAGE plpgsql
"""


def _create_trigger_sql() -> tuple[str, ...]:
    """Return every ``CREATE TRIGGER`` statement, one per attachment.

    Returns:
        The delete and move arms on ``salary.pay_stubs``, then one truncate
        arm per table in :data:`_TRUNCATE_TABLES`.
    """
    return (
        f"CREATE TRIGGER {_DELETE_TRIGGER} BEFORE DELETE ON {_STUBS} "
        f"FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION}()",
        f"CREATE TRIGGER {_MOVE_TRIGGER} "
        f"BEFORE UPDATE OF salary_profile_id ON {_STUBS} "
        "FOR EACH ROW "
        "WHEN (OLD.salary_profile_id IS DISTINCT FROM NEW.salary_profile_id) "
        f"EXECUTE FUNCTION {_TRIGGER_FUNCTION}()",
        *(
            f"CREATE TRIGGER {_TRUNCATE_TRIGGER} BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {_TRIGGER_FUNCTION}()"
            for table in _TRUNCATE_TABLES
        ),
    )


def _drop_trigger_sql() -> tuple[str, ...]:
    """Return a guarded ``DROP TRIGGER`` for every attachment.

    PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so every apply pairs
    these with a fresh create to stay idempotent -- the pattern the sibling
    infrastructure modules use.

    Returns:
        One ``DROP TRIGGER IF EXISTS`` per entry in :data:`PAY_STUB_TRIGGERS`.
    """
    return tuple(
        f"DROP TRIGGER IF EXISTS {name} ON {table}"
        for name, table in PAY_STUB_TRIGGERS
    )


def apply_pay_stub_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently install the never-deleted, never-moved rule on the stub tables.

    Creates or replaces the trigger function, then drops and re-creates every
    attachment.  Every statement is idempotent, so a second run is
    indistinguishable from the first.  Nothing needs legalising first: the
    revision that installs this creates the tables empty.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    for statement in (
        _CREATE_FUNCTION_SQL, *_drop_trigger_sql(), *_create_trigger_sql(),
    ):
        executor(statement)


def remove_pay_stub_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_pay_stub_infrastructure`.

    Drops every attachment, then the function, so nothing is dropped while
    something still references it.  Every statement uses ``IF EXISTS``, so this
    is idempotent and a clean no-op on a database that never carried the
    infrastructure.

    Args:
        executor: Same contract as :func:`apply_pay_stub_infrastructure`.
    """
    for statement in (
        *_drop_trigger_sql(), f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()",
    ):
        executor(statement)
