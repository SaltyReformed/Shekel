"""The MOVEMENT arm: a settled row may not predate its account's opening.

Installed by revision ``d3b6f1c8a274``, which censuses and repairs the
movements already in the way before turning it on.  The package docstring
(:mod:`app.opening_infrastructure`) carries the argument; this file is the DDL
for one arm, and the openings-side predicate that grades it from the other
direction lives in :mod:`._openings` beside its twin.
"""

from __future__ import annotations

from ._base import _BOOKS_HOLD_FUNCTION, _OPENED_ON_FUNCTION

_MOVEMENT_FUNCTION = "budget.assert_movement_after_books_open"

_MOVEMENT_TRIGGER = "ck_movement_after_books_open"

#: The two tables carrying a dated cash movement.  Both are
#: :class:`app.models.mixins.SettleDatedMixin` tables -- one row per settled
#: transaction and one per posted purchase -- and both carry ``account_id``,
#: which is what lets ONE trigger function serve both.
_MOVEMENT_TABLES = ("budget.transactions", "budget.transaction_entries")

#: Every dated cash movement, over BOTH movement tables, as ONE SQL expression.
#:
#: **PUBLIC because the Alembic revision interpolates it too.**  The rule "a
#: settled transaction and a posted purchase are one kind of fact to the fold"
#: (ruling **R-FM**) was hand-spelled four times across this module and
#: ``d3b6f1c8a274``, held together by a comment saying the copies must agree --
#: and nothing could grade that: ``duplicate-code`` does not see SQL inside a
#: string literal, and the migration is outside ``app/`` besides.  A migration
#: that moved an opening to a day the constraint installed four lines later
#: refuses is exactly the failure this module's own docstring names as the
#: arc's root cause, so the statement is named here and interpolated there.
#:
#: **Soft-deleted rows are INCLUDED** -- see
#: :data:`_CREATE_OPENING_PREDICATE_SQL` for why the constraint's row set is
#: deliberately wider than the fold's.
SETTLED_MOVEMENTS_SQL = """
        SELECT account_id, settled_on
          FROM budget.transactions
         WHERE settled_on IS NOT NULL
        UNION ALL
        SELECT account_id, settled_on
          FROM budget.transaction_entries
         WHERE settled_on IS NOT NULL
"""

_CREATE_MOVEMENT_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_MOVEMENT_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_opened_on DATE;
BEGIN
    -- **A row with no settle day never reaches here at all**: the trigger's
    -- WHEN clause states that, and states it ONCE.  It used to be an early
    -- RETURN in this body, which is the same rule asked one phase too late --
    -- see :func:`_create_movement_trigger_sql` for what that cost.
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
    IF v_opened_on IS NOT NULL
       AND NOT {_BOOKS_HOLD_FUNCTION}(v_opened_on, NEW.settled_on) THEN
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

def _create_movement_trigger_sql(table: str) -> str:
    """Return the ``CREATE CONSTRAINT TRIGGER`` attaching the movement rule.

    ``AFTER INSERT OR UPDATE OF settled_on, account_id`` rather than a bare
    ``AFTER INSERT OR UPDATE``: those two columns are the whole of what the
    predicate reads, and ``budget.transactions`` is updated on every status
    change, amount correction and template regeneration, so a column list is
    the difference between one indexed lookup per SETTLE and one per WRITE.

    **``WHEN (NEW.settled_on IS NOT NULL)`` is the guard that used to be this
    trigger function's first line, and moving it here is a bug fix rather than
    a tidy-up.**  A deferred constraint trigger queues its event at STATEMENT
    time and runs the function at COMMIT, so an early ``RETURN`` inside the
    body cannot stop the event being queued -- and PostgreSQL refuses
    ``ALTER TABLE`` on any table carrying pending trigger events.  Every
    Projected row this app writes therefore made DDL on ``budget.transactions``
    illegal for the rest of the transaction, which broke two audit-trigger
    benchmarks in CI (``tests/test_performance``, a directory ``pytest.ini``
    excludes from the ordinary run) and, on the merge before that,
    ``recurrence:R17``'s index re-key.  A WHEN clause is evaluated where the
    queueing decision is made, so a row that cannot violate the rule now
    queues nothing.  Measured both ways: with the clause an unsettled INSERT
    leaves ``ALTER TABLE`` legal, and a SETTLED one still queues and still
    blocks it -- enforcement is unchanged, only the rows that were never going
    to be checked stop reserving the table.

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
        "FOR EACH ROW WHEN (NEW.settled_on IS NOT NULL) "
        f"EXECUTE FUNCTION {_MOVEMENT_FUNCTION}()"
    )
