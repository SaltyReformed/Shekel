"""Shared definitions for the sighting relation's one structural rule.

**A bank line lives while any sighting of it does, and goes with its last**
(plan step ``bank_import:X-f6b-1``, ruling **R-BI10**).  A line is the bank's
fact; what each import said about it is a ``budget.statement_line_sightings``
row keyed to that import, and deleting the import takes its sightings by
cascade.  What that leaves, without this module, is a line no source stands
behind: recorded on a day, for an amount, with nothing to say which statement
showed it -- and a walk that would go on summing it.  The rule makes a line
un-STRANDABLE; a line is still insertable before its first sighting (the
record door stages the line, flushes for its id, then stages the sighting),
and that instant is inside one unit of work.

**The rule is a trigger rather than a door's own ``DELETE``, so no door can
strand a line.**  ``statement_import.delete_import`` is the one writer in
``app/`` that removes an import today, and it could delete the lines it alone
holds before deleting the import; then a bulk ``DELETE FROM
budget.statement_imports``, a psql session, or a writer nobody enumerated
would leave the strandings the door was remembering to prevent.  An ``AFTER
DELETE`` row trigger on the sighting fires for every sighting the cascade
removes, whatever removed it, and deletes the line exactly when the sighting
it just lost was the last.  ``budget.statement_match_members`` still refuses
the delete of a line an accepted match names
(``fk_statement_match_members_line_account``, NO ACTION) and
``budget.statement_line_skips`` still goes with its line by cascade, so what a
line's removal means is unchanged by who removes it.

**``AFTER`` and not ``BEFORE``, because the question is about the rows that
remain.**  A ``BEFORE DELETE`` trigger sees the sighting still standing and
cannot ask whether it is the last; an ``AFTER`` row trigger runs once the
statement's cascade has removed every sighting it was going to, so ``NOT
EXISTS`` answers for the rows that survive.  Deleting the line then cascades
to nothing that references the sightings -- they are gone -- and to the
line's own dependants, which is the disposal the import's cascade performed
by itself while it owned the line.

**One ordering fact worth knowing when an ACCOUNT is deleted.**  The line
carries a direct ``accounts`` key now, so during an account delete the
line cascade and the match-member cascade are queued in the same round of
referential-integrity triggers, and ``fk_statement_match_members_line_
account`` (NO ACTION) passes only because the members' cascade fires
before the lines' check -- PostgreSQL fires same-level RI triggers in name
order, and ``bank_statement_lines_account_id_fkey`` was created after
``statement_matches``' account key.  Under the old schema the line check sat
one cascade level deeper and was safe in any order.  ``test_statement_match_
schema::test_deleting_the_ACCOUNT_still_cascades_everything`` pins it; a
migration that recreates ``statement_matches``' account key would flip it.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.append_only_infrastructure` states: the Alembic revision that
installs it (``bank_import:X-f6b-1``'s), ``scripts/init_database.py`` (fresh
database, no migration chain) and ``scripts/build_test_template.py``
(re-applied idempotently so the latest in-code definition wins).
``scripts/build_test_db_image.py`` counts :data:`SIGHTING_TRIGGERS` on the
baked image so a template missing the arm is rebuilt rather than trusted.

**Caller contract: both tables must already exist.**  ``CREATE TRIGGER`` needs
its table, and the function body names the line table.
"""

from __future__ import annotations

from typing import Callable

#: The one trigger function: after a sighting goes, delete its line if no
#: other sighting of that line remains.
_TRIGGER_FUNCTION = "budget.remove_line_left_unsighted"

#: ``(trigger name, schema-qualified table)`` for the one attachment.
#: Exported so the image check can count it and a lift can name it.
SIGHTING_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("rm_line_left_unsighted", "budget.statement_line_sightings"),
)

_CREATE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM budget.bank_statement_lines
    WHERE id = OLD.line_id
      AND NOT EXISTS (
          SELECT 1 FROM budget.statement_line_sightings
          WHERE line_id = OLD.line_id
      );
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


#: The one attachment, as ``CREATE`` and as a guarded ``DROP``.  PostgreSQL
#: has no ``CREATE TRIGGER IF NOT EXISTS``, so every apply pairs the drop with
#: a fresh create to stay idempotent -- the pattern the sibling infrastructure
#: modules use.
_TRIGGER_NAME, _TRIGGER_TABLE = SIGHTING_TRIGGERS[0]
_CREATE_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER_NAME} AFTER DELETE ON {_TRIGGER_TABLE} "
    f"FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION}()"
)
_DROP_TRIGGER_SQL = f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON {_TRIGGER_TABLE}"


def apply_sighting_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently install the last-sighting rule on the sighting table.

    Creates or replaces the trigger function, then drops and re-creates the
    attachment.  Every statement is idempotent, so a second run is
    indistinguishable from the first.

    **Nothing to legalise first**: the revision that installs this also
    writes one sighting per existing line, so no line is unsighted when the
    arm is first attached.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    executor(_CREATE_FUNCTION_SQL)
    executor(_DROP_TRIGGER_SQL)
    executor(_CREATE_TRIGGER_SQL)


def remove_sighting_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_sighting_infrastructure`.

    Drops the attachment, then the function, so nothing is dropped while
    something still references it.  Every statement uses ``IF EXISTS``, so
    this is idempotent and a clean no-op on a database that never carried the
    infrastructure.

    Args:
        executor: Same contract as :func:`apply_sighting_infrastructure`.
    """
    executor(_DROP_TRIGGER_SQL)
    executor(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
