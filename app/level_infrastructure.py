"""Shared definitions for the level relation's cross-table bound.

**A bank level's day is one its FILE could have pinned** (ruling **R-GF**,
plan step ``bank_import:X-f6e-1``; moved here by ``balance:X-bj-1``).  The
solved day ranges over {the day before the file's first line} + {every day the
file covers}, so ``period_start - 1`` is its floor and ``period_end`` its
ceiling; and a bank cannot state a balance for a day after the one it wrote on
the header, so the claimed day is its other ceiling.  Measured on the
developer's exports: 08-22 solves at 08-21 under a header dated 08-22, and
08-16 at 08-13 under one dated 08-16.  It was
``ck_statement_imports_effective_day_within_file`` while the solved day lived
on the import row; the day is a level row's now, and the bound spans two
tables, so PostgreSQL cannot state it as a row-level CHECK.

**The rule is stated ONCE, as a SQL function, and attached TWICE**, because
the FACT lives on ``budget.account_anchor_history`` and the BOUNDS on
``budget.statement_imports``, so either can move without the other.  A
trigger on the level alone would have let
``UPDATE budget.statement_imports SET period_end = ...`` under a placement
commit into the state the CHECK used to refuse -- the exact hole
:mod:`app.opening_infrastructure` records being found on its matched-line arm
by adversarial review, and the one the design review of this step named.  No
app writer updates a file's span; this is what makes that true of a bulk
``UPDATE``, a psql session and a writer nobody enumerated.

**Immediate ``BEFORE`` row triggers, not deferred constraint triggers**, and
the difference from :mod:`app.opening_infrastructure` is deliberate: that
module defers because the account-10 repair legitimately re-dates movements and
restates the books in ONE transaction, so statement order would refuse it.
Nothing reorders here -- the import door writes the file's span, flushes, and
only then writes the level solved inside it -- so an immediate arm is exact,
fires without a commit, and names the offending row at the statement.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.append_only_infrastructure` states: the Alembic revision that
installs it (``balance:X-bj-1``'s), ``scripts/init_database.py`` (fresh
database, no migration chain) and ``scripts/build_test_template.py``
(re-applied idempotently so the latest in-code definition wins).
``scripts/build_test_db_image.py`` counts :data:`LEVEL_TRIGGERS` on the baked
image so a template missing an arm is rebuilt rather than trusted.

**Caller contract: both tables must already exist.**  The two ``%ROWTYPE``
declarations resolve their tables when the trigger functions are created, and
``CREATE TRIGGER`` needs its table either way.
"""

from __future__ import annotations

from typing import Callable

#: The rule, as a pure SQL function over four days.  ONE spelling of the
#: predicate; both trigger functions call it.  A NULL ``stated_on`` makes it
#: answer NULL, which both arms read as "nothing to refuse" -- and that day
#: cannot be NULL for an import that owns a level: the claim's two columns
#: are welded by ``ck_statement_imports_stated_balance_paired`` and a level's
#: key ``fk_anchor_history_statement_import_claim`` needs the figure present.
_RULE_FUNCTION = "budget.level_lies_within_file"

#: The two thin trigger functions, one per attached table, each reading its
#: own ``NEW`` and asking the rule.
_LEVEL_TRIGGER_FUNCTION = "budget.refuse_level_outside_its_file"
_IMPORT_TRIGGER_FUNCTION = "budget.refuse_file_span_leaving_its_level"

#: ``(trigger name, schema-qualified table)`` for each attachment.  Exported so
#: the image check can count them and a lift can name them.
LEVEL_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("ck_level_within_file", "budget.account_anchor_history"),
    ("ck_file_span_holds_level", "budget.statement_imports"),
)

_CREATE_RULE_SQL = f"""
CREATE OR REPLACE FUNCTION {_RULE_FUNCTION}(
    level_day date, first_line date, last_line date, stated_on date
) RETURNS boolean AS $$
    SELECT level_day >= first_line - 1
       AND level_day <= last_line
       AND level_day <= stated_on
$$ LANGUAGE sql IMMUTABLE
"""

_CREATE_LEVEL_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_LEVEL_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    file budget.statement_imports%ROWTYPE;
BEGIN
    -- An owner-declared level names no file and is bounded by nothing here.
    IF NEW.statement_import_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO file FROM budget.statement_imports
    WHERE id = NEW.statement_import_id;
    IF NOT {_RULE_FUNCTION}(
        NEW.observed_on, file.period_start, file.period_end,
        file.stated_balance_on
    ) THEN
        RAISE EXCEPTION
            'level % for account % is dated % but its statement % covers '
            '%..% and states its balance as of %: a placed day must lie '
            'inside the file (rule budget.level_lies_within_file)',
            NEW.id, NEW.account_id, NEW.observed_on, file.id,
            file.period_start, file.period_end, file.stated_balance_on;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_CREATE_IMPORT_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_IMPORT_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    stranded budget.account_anchor_history%ROWTYPE;
BEGIN
    SELECT * INTO stranded FROM budget.account_anchor_history
    WHERE statement_import_id = NEW.id
      AND NOT {_RULE_FUNCTION}(
          observed_on, NEW.period_start, NEW.period_end,
          NEW.stated_balance_on
      )
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'statement % cannot cover %..% as of %: its level % is placed on '
            '%, which that span would leave outside the file (rule '
            'budget.level_lies_within_file)',
            NEW.id, NEW.period_start, NEW.period_end, NEW.stated_balance_on,
            stranded.id, stranded.observed_on;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def _create_trigger_sql() -> tuple[str, ...]:
    """Return the two ``CREATE TRIGGER`` statements, one per attachment.

    The level arm fires on INSERT only, because the table is append-only and
    an UPDATE is refused one trigger over; the import arm fires on an UPDATE
    OF the three bounding columns, which is the only write that can move the
    bounds out from under a placed level.
    """
    (level_trigger, level_table), (import_trigger, import_table) = (
        LEVEL_TRIGGERS
    )
    return (
        f"CREATE TRIGGER {level_trigger} "
        f"BEFORE INSERT ON {level_table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_LEVEL_TRIGGER_FUNCTION}()",
        f"CREATE TRIGGER {import_trigger} "
        f"BEFORE UPDATE OF period_start, period_end, stated_balance_on "
        f"ON {import_table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_IMPORT_TRIGGER_FUNCTION}()",
    )


def _drop_trigger_sql() -> tuple[str, ...]:
    """Return the guarded ``DROP TRIGGER`` for each attachment.

    PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, so every apply pairs a
    guarded drop with a fresh create to stay idempotent -- the pattern the
    three sibling infrastructure modules use.
    """
    return tuple(
        f"DROP TRIGGER IF EXISTS {name} ON {table}"
        for name, table in LEVEL_TRIGGERS
    )


def apply_level_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently install the within-file bound on both tables.

    Creates or replaces the rule function and the two trigger functions, then
    drops and re-creates each attachment.  Every statement is idempotent, so a
    second run is indistinguishable from the first.

    **Nothing to legalise first**: the bound was a CHECK on the import row
    until this module existed, so no stored placement can be outside its
    file when this is first applied.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    executor(_CREATE_RULE_SQL)
    executor(_CREATE_LEVEL_TRIGGER_FUNCTION_SQL)
    executor(_CREATE_IMPORT_TRIGGER_FUNCTION_SQL)
    for statement in _drop_trigger_sql():
        executor(statement)
    for statement in _create_trigger_sql():
        executor(statement)


def remove_level_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_level_infrastructure`.

    Drops both attachments, then the three functions, so nothing is dropped
    while something still references it.  Every statement uses ``IF EXISTS``,
    so this is idempotent and a clean no-op on a database that never carried
    the infrastructure.

    Args:
        executor: Same contract as :func:`apply_level_infrastructure`.
    """
    for statement in _drop_trigger_sql():
        executor(statement)
    executor(f"DROP FUNCTION IF EXISTS {_LEVEL_TRIGGER_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_IMPORT_TRIGGER_FUNCTION}()")
    executor(
        f"DROP FUNCTION IF EXISTS {_RULE_FUNCTION}(date, date, date, date)"
    )
