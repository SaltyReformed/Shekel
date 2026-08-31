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

**This module is what makes that state UNSTORABLE BY ANY SINGLE TRANSACTION,
rather than merely refused.**  Two limits are worth naming rather than leaving
a reader to assume more:

* Under READ COMMITTED two CONCURRENT transactions -- one recording a movement,
  one restating the books past it -- each see a snapshot without the other's
  uncommitted row, so both predicates pass.  **Neither trigger takes a lock,
  and what closes the window instead is that both DOORS take the owner's**
  (:func:`app.services.user_write_lock.lock_user_writes`), which was true of
  the movement side before there was a restatement door and is true of
  :func:`app.services.opening_service.stage_account_opening` by construction.
  The loser blocks until the winner's transaction ENDS, and a deferred
  constraint trigger runs at COMMIT -- after that block, on a fresh READ
  COMMITTED snapshot -- so it sees the winner's committed row and refuses.
  **Measured 2026-08-31 on a production clone rather than argued**: the settle
  path emits ``pg_advisory_xact_lock`` at statement 11 of the 13 an ordinary
  settle runs, inside ``account_posting_service.self_heal_anchor_corrections``,
  and ``reconcile_service.record_settled_days`` reaches the same lock through
  ``_post_stamped_purchases`` -> ``posting_service.sync_transaction_postings``.
  **The residue, stated rather than rounded off:** that self-heal returns
  BEFORE its lock when the source emitted no posting delta, so a movement whose
  legs all net to zero races a restatement unserialised.  ``SELECT ... FOR
  UPDATE`` on the governing opening remains the fix that would not depend on a
  second door's locking, and it is a fix for that residue rather than for the
  whole window.
* ``session_replication_role = replica`` disables constraint triggers outright,
  which is what ``pg_restore --disable-triggers`` sets.  The prod-to-dev clone
  is a documented workflow here, so a restore can land rows this module would
  have refused; the CONSTRAINT is not re-validated afterwards, only future
  writes are.

The invariant spans three tables and two directions, so PostgreSQL cannot state
it as a row-level CHECK:

* a MOVEMENT may not move back past its account's opening
  (``budget.transactions`` and ``budget.transaction_entries``, both of which
  carry ``account_id`` and ``settled_on``);
* an account's GOVERNING opening may not sit on or after a movement that
  already exists, nor AFTER a day the owner has asserted a balance for
  (``budget.account_openings``) -- stated over the rows that survive an event
  rather than over the row written, so restating forward, raw-updating the
  governing row and DELETING it (which promotes an older restatement) are one
  rule rather than three arms.

**The assertion half was added 2026-08-31 and it closes a MONEY defect the
movement half could not see.**  An account with no settled movement is
unbounded by the first rule, and that is every investment, retirement and
property account on production.  Reproduced on the developer's own Roth IRA:
restating its books forward past six assertions was accepted, moved
``unrealized_change`` from ``-$4,523.33`` to ``-$27,332.35`` -- ``$22,809.02``
of investment return that never happened -- and silently discarded the opening
the owner had just stated, because the earliest assertion RESETS the fold above
it.  Equality is legal and is the ORDINARY case: ``account_service`` writes an
account's origination opening and origination assertion for the same day, and
three of the nine production accounts still sit exactly there.

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

**THE COMMON CASE OF THIS IS GONE, and how it was closed is the point.**  The
movement trigger carries ``WHEN (NEW.settled_on IS NOT NULL)``, which is the
guard that used to be its function's first line.  An early ``RETURN`` in the
body cannot stop an event being QUEUED -- the queue happens at statement time
and the body runs at COMMIT -- so every Projected row this app writes used to
reserve ``budget.transactions`` against DDL for the rest of its transaction.
It cost two CI failures before it was found: ``recurrence:R17``'s
``test_dc06_detects_two_rows_answering_one_occurrence`` on the merge, and two
audit-trigger benchmarks in ``tests/test_performance`` -- a directory
``pytest.ini`` excludes from the ordinary run, so no local suite can see it.
**What remains is a row that really does carry a settle day**, which is the
only kind the rule was ever going to check.

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

#: The openings-side PREDICATE, stated once and asked per affected account.
#: The trigger above it is dispatch: which account this event touched.  Keeping
#: the two apart is what lets one event ask about TWO accounts -- a raw
#: ``UPDATE`` moving a row between them -- without spelling the check twice.
_OPENING_PREDICATE_FUNCTION = "budget.assert_account_books_hold_its_movements"

_MOVEMENT_TRIGGER = "ck_movement_after_books_open"
_OPENING_TRIGGER = "ck_books_open_before_movements"

#: The two tables carrying a dated cash movement.  Both are
#: :class:`app.models.mixins.SettleDatedMixin` tables -- one row per settled
#: transaction and one per posted purchase -- and both carry ``account_id``,
#: which is what lets ONE trigger function serve both.
_MOVEMENT_TABLES = ("budget.transactions", "budget.transaction_entries")
_OPENINGS_TABLE = "budget.account_openings"


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

#: The RECORDING order that decides which opening record governs (ruling
#: **R-HE**): the table is append-only and the latest restatement wins.  Public
#: because the revision's three window functions, this module's own
#: ``budget.account_books_opened_on`` and
#: ``cash_ledger.governing_account_opening`` must all break it the same way; a
#: fourth spelling is how two tiers come to disagree about which restatement is
#: in force.
#:
#: **It is ``id`` ALONE, and it was ``created_at DESC, id DESC`` until plan step
#: X-f3c-2b-2a's adversarial review refuted the reason for the first term.**
#: That reason -- stated in this module and twice in ``cash_ledger`` -- was that
#: ``created_at`` is set on INSERT and so is monotone in recording order.  It is
#: not: :class:`app.models.mixins.CreatedAtMixin` defaults it to
#: ``db.func.now()``, which in PostgreSQL is ``transaction_timestamp()`` -- the
#: instant the transaction BEGAN.  ``anchor_service._governing_loan_anchor``
#: already says so in as many words about the loan twin, and this door never
#: carried the implication across.  **The failure it produced is a SILENT NO-OP
#: on the level every balance rests on**: two restatements from two tabs, the
#: one whose transaction opened EARLIER commits SECOND under the owner's write
#: lock, and its row sorts below the row it was meant to supersede -- so the
#: owner is told "Books restated" and nothing moved.
#:
#: ``id`` is a sequence value allocated when the INSERT executes, and the write
#: door holds ``lock_user_writes`` across its compare-and-append, so within an
#: account the id order IS the order the owner made the statements.  The
#: migration writes every row in one transaction and at most one per account,
#: so it is unaffected either way.
GOVERNING_ORDER_SQL = "id DESC"


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
     ORDER BY {GOVERNING_ORDER_SQL}
     LIMIT 1;
$$ LANGUAGE sql STABLE
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


_CREATE_OPENING_PREDICATE_SQL = f"""
CREATE OR REPLACE FUNCTION {_OPENING_PREDICATE_FUNCTION}(p_account_id INTEGER)
RETURNS VOID AS $$
DECLARE
    v_opened_on DATE;
    v_earliest DATE;
    v_first_assertion DATE;
BEGIN
    -- **The predicate is over the account's RESULTING STATE, not over the row
    -- that was written**, and that is what makes one function serve every
    -- event.  An INSERT, an UPDATE and a DELETE on
    -- ``budget.account_openings`` all change the same thing -- which row
    -- governs -- so each is checked by asking the same question afterwards:
    -- do this account's books, as they now stand, hold every movement it
    -- records?  The alternative (grade the written row, and skip it when some
    -- other row governs) needs the governing lookup spelled a SECOND time to
    -- find out, and it is blind to a DELETE, where the row that breaks the
    -- invariant is the one that SURVIVED.
    v_opened_on := {_OPENED_ON_FUNCTION}(p_account_id);

    -- No opening record at all.  Two ways to get here and neither is a
    -- violation: a CASCADE from ``budget.accounts`` has just disposed of the
    -- whole account, and (unreachably, but stated rather than assumed) an
    -- account that never got one.  The READ side already refuses the second
    -- loudly -- ``cash_ledger.account_opening_fact`` raises rather than
    -- fabricating a level -- so raising here would make this function enforce
    -- a SECOND invariant no other constraint states, and would surface it as
    -- a COMMIT abort on an unrelated write path.
    IF v_opened_on IS NULL THEN
        RETURN;
    END IF;

    -- The earliest day the account records money moving, over BOTH movement
    -- tables -- the same union the movement trigger's two attachments cover
    -- from the other side.
    --
    -- **SOFT-DELETED rows are counted, and this row set is deliberately WIDER
    -- than the fold's.**  ``balance_contributing_clause`` excludes
    -- ``is_deleted`` rows and the Credit / Cancelled statuses; this counts
    -- them, so a soft-deleted settled row still bounds how far back its
    -- account's books may be restated.  Narrowing to match the fold would
    -- open a hole on RESTORE: un-deleting is an ``UPDATE`` of ``is_deleted``
    -- alone, and the movement trigger fires ``UPDATE OF settled_on,
    -- account_id``, so a restored pre-books row would pass both tiers
    -- untouched.  The cost is over-refusal -- the books cannot move past a
    -- day whose only row the owner cannot see -- and that is the safe
    -- direction: it refuses a legal act loudly rather than admitting an
    -- illegal one silently.  Stated because two statements of one rule that
    -- differ silently is the failure this arc names as its own root cause.
    SELECT MIN(settled_on) INTO v_earliest FROM (
        {SETTLED_MOVEMENTS_SQL}
    ) AS movements WHERE account_id = p_account_id;

    IF v_earliest IS NOT NULL AND v_earliest <= v_opened_on THEN
        RAISE EXCEPTION
            'account % cannot open its books on %: a movement is already '
            'dated %, and an opening equity is the closing balance for its '
            'own day, so that money would be counted twice',
            p_account_id, v_opened_on, v_earliest;
    END IF;

    -- The ASSERTION bound.  An account with no settled movement passes
    -- everything above, so without this an opening could be restated past
    -- every balance the owner has ever recorded -- at which point the
    -- earliest assertion resets the fold above it, the stated opening is read
    -- by nothing, and the whole gap is reported as a gain on a MODELLED
    -- account (ruling R-FO).  ``>`` and not ``>=``: an opening EQUAL to the
    -- earliest assertion is what ``account_service.create_account`` writes for
    -- every account, so refusing equality would make the factory's own output
    -- unstorable.
    SELECT MIN(observed_on) INTO v_first_assertion
      FROM budget.account_anchor_history
     WHERE account_id = p_account_id;

    IF v_first_assertion IS NOT NULL AND v_opened_on > v_first_assertion THEN
        RAISE EXCEPTION
            'account % cannot open its books on %: a balance is already '
            'recorded for %, and the fold restarts at a recorded balance -- '
            'so an opening after it would be ignored and the difference '
            'reported as a gain',
            p_account_id, v_opened_on, v_first_assertion;
    END IF;
END;
$$ LANGUAGE plpgsql
"""


_CREATE_OPENING_FUNC_SQL = f"""
CREATE OR REPLACE FUNCTION {_OPENING_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- DISPATCH ONLY: name the accounts this event could have changed the
    -- books of, and ask the predicate about each.  There are two of them
    -- only for a raw ``UPDATE`` that moves a row BETWEEN accounts, which no
    -- door does and which would otherwise leave the abandoned account's books
    -- ungraded -- the same reason the arms below are written out rather than
    -- collapsed into ``NEW``.
    IF TG_OP <> 'INSERT' THEN
        PERFORM {_OPENING_PREDICATE_FUNCTION}(OLD.account_id);
    END IF;
    IF TG_OP <> 'DELETE' AND (
        TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id
    ) THEN
        PERFORM {_OPENING_PREDICATE_FUNCTION}(NEW.account_id);
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


#: **INSERT, UPDATE and DELETE**, where the movement side needs only the first
#: two.  The table is append-only and a restatement IS an insert, so INSERT is
#: the only door; the other two arms exist because the invariant is a property
#: of the SURVIVING rows rather than of the written one.  A raw ``UPDATE``
#: moving the governing row's ``opened_on`` forward, and a raw ``DELETE`` of
#: the governing row -- which promotes an older restatement whose day the live
#: movements may contradict -- both break it without any door being opened.
#: Neither is reachable through the ORM (``AccountOpening._block_update`` /
#: ``_block_delete`` refuse both), which is exactly why the DATABASE is where
#: they are stated: this tier's whole job is the writer nobody enumerated.  No
#: column list, because a ``DELETE`` has no columns to name and the table is
#: written to only when an account is created or restated.
#:
#: **The DELETE arm cannot make an account undeletable, and the reason is an
#: FK asymmetry rather than the predicate's own care.**  Disposing of an
#: account CASCADEs its openings away (``AccountScopedMixin.account_id`` is
#: ``ON DELETE CASCADE``) while ``budget.transactions.account_id`` is ON
#: DELETE **RESTRICT** -- so an account that still records a movement cannot
#: be deleted at all, and one that can be has no movement for the surviving
#: books to fail against.  At COMMIT the predicate finds no governing opening
#: and returns before it counts anything.
_CREATE_OPENING_TRIGGER_SQL = (
    f"CREATE CONSTRAINT TRIGGER {_OPENING_TRIGGER} "
    f"AFTER INSERT OR UPDATE OR DELETE ON {_OPENINGS_TABLE} "
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
       budget.assert_account_books_hold_its_movements`` -- the openings-side
       predicate -- then
       ``CREATE OR REPLACE FUNCTION
       budget.assert_books_open_before_books_movements``, the trigger function
       that dispatches to it, and its constraint trigger on
       ``budget.account_openings``.  The predicate goes first: the dispatcher
       ``PERFORM``s it, and ``check_function_bodies`` validates that reference
       at ``CREATE FUNCTION`` time.

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
    executor(_CREATE_OPENING_PREDICATE_SQL)
    executor(_CREATE_OPENING_FUNC_SQL)
    executor(_drop_trigger_sql(_OPENING_TRIGGER, _OPENINGS_TABLE))
    executor(_CREATE_OPENING_TRIGGER_SQL)


def remove_opening_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_opening_infrastructure` for a migration downgrade.

    Drops the three triggers first and then the four functions, innermost
    caller last, so nothing is dropped while something still references it: the
    triggers name the two trigger functions, the openings dispatcher
    ``PERFORM``s the openings predicate, and both predicates read the
    governing-day lookup.  Every statement uses ``IF EXISTS``, so this is
    idempotent and a clean no-op on a database that never carried the
    infrastructure.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Same contract as :func:`apply_opening_infrastructure`.
    """
    for table in _MOVEMENT_TABLES:
        executor(_drop_trigger_sql(_MOVEMENT_TRIGGER, table))
    executor(_drop_trigger_sql(_OPENING_TRIGGER, _OPENINGS_TABLE))
    executor(f"DROP FUNCTION IF EXISTS {_MOVEMENT_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_OPENING_FUNCTION}()")
    executor(
        f"DROP FUNCTION IF EXISTS {_OPENING_PREDICATE_FUNCTION}(INTEGER)"
    )
    executor(f"DROP FUNCTION IF EXISTS {_OPENED_ON_FUNCTION}(INTEGER)")
