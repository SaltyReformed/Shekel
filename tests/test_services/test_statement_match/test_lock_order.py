"""Every row lock first, in one order -- plan step ``bank_import:X-gi-5``.

Finding **N-471**.  A reviewed pass runs four arms -- matches, creations,
incomes, skips -- and each door locks the bank line it writes as it reads it.
Each of those reads is ordered, but a door per ITEM took a pass's locks in the
order the SUBMISSION listed them, so two concurrent presses naming the same
lines in different arms took them in opposite orders and PostgreSQL aborted
one mid-batch: the loser's whole press rolled back and answered with the
generic *Something went wrong* sentence over an ERROR-level traceback, on a
door that moves money.  **Reproduced 2026-09-12** with a forced interleave --
press A recording a LATE line then skipping an EARLY one, press B the
reverse, each paused after its first lock: A died ``DeadlockDetected`` and B
landed both.

The remedy is :func:`~app.services.statement_match._resolve.lock_lines`: one
locked read of every line the batch names, in the order every door's own read
already uses -- ascending ``id``, the order the ORM flushes a re-import's
UPDATEs of the same rows in -- before any arm runs.  **What these cases grade
is the MECHANISM, off the SQL the pass emits**, because the property is about
statement ORDER and a presence check cannot see order
(:func:`tests._test_helpers.capture_sql_statements`'s own argument):

1. **The first lock names every line and is ordered.**  A pass carrying all
   four arms, submitted in the REVERSE of lock order, emits one locking read
   before anything else, naming exactly :attr:`ReviewedBatch.line_ids` with
   the ``ORDER BY`` that orders the locks; every later locking read names a
   subset.  Firing control: without ``lock_lines`` the first lock is the match
   arm's, naming two of the five lines.
2. **It really blocks, and blocks BEFORE the first item.**  A second
   connection holding the FIRST line in lock order -- the one the pass names
   only in its LAST arm -- stops the pass at that first read with nothing
   written; on the old code the creation arm had already landed a purchase.
   Non-vacuity is the sibling case: the same pass with nothing held completes.
3. **The lock is the transaction's, not the item's.**  A refused item's
   savepoint rolls back the door's own re-take and not the pre-read, so the
   line stays held to commit -- graded from a second connection.
4. **A line the pass may not touch locks nothing and costs one item.**  A
   foreign owner's line in the batch is refused by its door with the rest
   landing, and a second connection takes that line at once.
5. **The mode is ``FOR NO KEY UPDATE`` at every site** (ruling
   **bank_import:R-BI5**): the flag was inverted at both doors from plan step
   ``bank_import:X-gj-4a`` until this one, and nothing read the emitted text.
6. **PostgreSQL orders the locks by the ``ORDER BY``**, which is the one
   claim the statement text cannot carry: the plan is ``LockRows`` above the
   node that yields the order.
"""

from __future__ import annotations

import re
from dataclasses import fields
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.enums import StatusEnum
from app.models.statement_import import BankStatementLine
from app.services import statement_match
from app.services.statement_match import (
    Consent,
    IncomeCreation,
    NewEnvelope,
    PurchaseCreation,
    ReviewedBatch,
    SkipRequest,
)

# Pylint: protected-access -- the query the pass locks through is a private
# collaboration inside the package with no importer outside it; a test for
# the mechanism reaches into it, the allowance every sibling here takes.
from app.services.statement_match import _resolve  # pylint: disable=protected-access

from tests._test_helpers import capture_sql_statements

from ._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
    filed_by,
)

# The same bound ``test_user_write_lock`` grades its blocking with: long
# enough that a free lock is never mistaken for a held one, short enough that
# the held case does not stall the suite.
_BLOCK_TIMEOUT_MS = 750

_TABLE = "budget.bank_statement_lines"
_LOCK = "FOR NO KEY UPDATE OF bank_statement_lines"
_ORDER = f"ORDER BY {_TABLE}.id"
_NOWAIT = text(f"SELECT id FROM {_TABLE} WHERE id = :id FOR NO KEY UPDATE NOWAIT")


def _locks_a_line(statement):
    """Return whether *statement* takes a row lock on a bank line."""
    return _TABLE in statement and " FOR " in statement and (
        "UPDATE OF" in statement or "SHARE OF" in statement
    )


def _writes(statement):
    """Return whether *statement* changes a row."""
    return statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))


def _line_ids_bound(parameters):
    """Return the bank-line ids a captured statement binds.

    The ORM binds ``BankStatementLine.id`` as ``id_1`` for one row and
    ``id_1_1, id_1_2, ...`` for an ``IN``; the account and owner terms bind
    under their own column names, so the prefix is what tells them apart.
    """
    return frozenset(
        value for key, value in parameters.items() if key.startswith("id_")
    )


def _four_arms(seed_user):
    """Stage one item per arm, five lines, submitted in REVERSE lock order.

    Lock order is ascending ``id``.  The SKIP -- the last arm -- names the
    FIRST line and the MATCH -- the first arm -- names the two LAST, so a
    pass that locked per item in submission order would take them last-first,
    which is the shape finding **N-471** deadlocks on.  **The posted days run
    the other way**: the first line by id is the LATEST by day, so a read
    ordered by day would lock it last -- the order this read had until the
    2026-09-12 design review found it crossing a re-import's id-ordered
    UPDATEs.

    Returns:
        ``(batch, lines)`` -- the :class:`ReviewedBatch` and the five staged
        lines in lock order.
    """
    statement = an_import(seed_user)
    day = seed_user["bootstrap_period"].start_date
    to_skip = a_bank_line(
        seed_user, statement, amount="-9.99", posted_on=day + timedelta(days=4),
        description="SKIP ME",
    )
    deposit = a_bank_line(
        seed_user, statement, amount="0.15", posted_on=day + timedelta(days=3),
        description="DIVIDEND EARNED (Dividend Earned)",
        merchant="Dividend Earned", sequence_in_group=1,
    )
    to_record = a_bank_line(
        seed_user, statement, amount="-30.00",
        posted_on=day + timedelta(days=2), description="LOWES ONE",
        sequence_in_group=2,
    )
    matched = [
        a_bank_line(
            seed_user, statement, amount="-180.00",
            posted_on=day + timedelta(days=1), description="DUKE ENERGY",
            sequence_in_group=3,
        ),
        a_bank_line(
            seed_user, statement, amount="-20.00",
            posted_on=day, description="WATER",
            sequence_in_group=4,
        ),
    ]
    rows = [
        a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=1),
        ),
        a_transaction(
            seed_user, name="Water", amount="20.00",
            status=StatusEnum.DONE, settled_on=day,
        ),
    ]
    category = seed_user["categories"]["Groceries"]
    batch = ReviewedBatch(
        consent=Consent.TICKED,
        matches=(a_submission(a_scope(seed_user), lines=matched, transactions=rows),),
        creations=(PurchaseCreation(
            line_id=to_record.id, transaction_id=None,
            new_envelope=NewEnvelope(name="Home Improvement", category_id=category.id),
        ),),
        incomes=(IncomeCreation(line_id=deposit.id),),
        skips=(SkipRequest(line_id=to_skip.id),),
    )
    return batch, [to_skip, deposit, to_record, *matched]


def _every_arm_is_carried(batch):
    """Refuse a fixture that leaves an act class empty.

    The coverage claim below is only as wide as the batch it measures, so a
    fifth act class added to :class:`ReviewedBatch` fails HERE, where the
    author sees it, rather than passing vacuously with its lines unlocked.
    """
    empty = [
        field.name for field in fields(ReviewedBatch)
        if field.name != "consent" and not getattr(batch, field.name)
    ]
    assert not empty, f"the fixture carries no item for {empty}"


def _pass(seed_user, batch):
    """Apply *batch* over a scope derived at the point of use."""
    return statement_match.apply_reviewed(batch, a_scope(seed_user))


class TestTheFirstLockNamesEveryLineInOneOrder:
    """Property 1: one ordered read, before every arm, covering every line."""

    def test_one_ordered_read_precedes_every_arm(self, app, db, seed_user):
        """The first lock is the pass's own, names all five, and is ordered.

        FIRING CONTROL: delete the ``lock_lines`` call from ``apply_reviewed``
        and the first locking statement is the match arm's ``load_lines``,
        naming two lines, not five.
        """
        with app.app_context():
            batch, lines = _four_arms(seed_user)
            _every_arm_is_carried(batch)
            db.session.commit()

            outcome, statements = capture_sql_statements(
                lambda: _pass(seed_user, batch),
            )

            assert outcome.applied_count == 4, outcome.refused
            locking = [
                (index, statement, parameters)
                for index, (statement, parameters) in enumerate(statements)
                if _locks_a_line(statement)
            ]
            assert locking, "no bank-line lock was taken at all"
            first_at, first, parameters = locking[0]
            assert _LOCK in first and _ORDER in first, first
            assert _line_ids_bound(parameters) == batch.line_ids
            # The independent reference: the two sides above share one
            # producer, and this is the one that does not.
            assert batch.line_ids == frozenset(line.id for line in lines)
            assert [line.id for line in lines] == sorted(line.id for line in lines)
            assert [line.posted_on for line in lines] != sorted(
                line.posted_on for line in lines
            ), "the fixture no longer separates id order from day order"
            # Nothing before it wrote a row or took any lock: this is the
            # pass's FIRST lock, which is the whole of what orders it.
            preceding = [statement for statement, _ in statements[:first_at]]
            assert not any(_writes(statement) for statement in preceding)
            assert not any(
                " FOR " in statement and ("UPDATE" in statement or "SHARE" in statement)
                or "pg_advisory" in statement
                for statement in preceding
            ), preceding
            # Every door still takes its own lock -- one per item -- and
            # none of them reaches outside the set the pass locked first.
            assert len(locking) == 1 + batch.item_count, [s for _, s, _ in locking]
            for _, statement, later in locking[1:]:
                assert _line_ids_bound(later) <= batch.line_ids, statement

    def test_the_mode_is_FOR_NO_KEY_UPDATE_at_every_site(
        self, app, db, seed_user,
    ):
        """Ruling **bank_import:R-BI5**, graded off the emitted text.

        Five locking reads -- the pass's own, three doors through
        ``load_lines`` and the skip door through ``_line_on`` -- and every
        one says ``FOR NO KEY UPDATE``.  FIRING CONTROL: ``key_share=False``
        in ``locked_for_write`` renders ``FOR UPDATE OF`` and this fails on
        all five.
        """
        with app.app_context():
            batch, _ = _four_arms(seed_user)
            db.session.commit()

            _, statements = capture_sql_statements(lambda: _pass(seed_user, batch))

            locking = [s for s, _ in statements if _locks_a_line(s)]
            assert len(locking) == 1 + batch.item_count
            assert all(_LOCK in statement for statement in locking), locking
            assert not any(
                "FOR UPDATE OF bank_statement_lines" in statement
                for statement in locking
            )

    def test_an_empty_pass_locks_nothing(self, app, db, seed_user):
        """An untouched form posts an empty batch, and that emits no lock."""
        with app.app_context():
            empty = ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(), incomes=(),
                skips=(),
            )
            assert empty.line_ids == frozenset()

            outcome, statements = capture_sql_statements(
                lambda: _pass(seed_user, empty),
            )

            assert outcome.applied_count == 0
            assert not any(_locks_a_line(s) for s, _ in statements)


class TestTheReadReallyBlocksBeforeTheFirstItem:
    """Property 2: PostgreSQL's behaviour, against a second connection."""

    def test_a_held_first_line_stops_the_pass_before_it_writes(
        self, app, db, seed_user,
    ):
        """The pass waits at its first read, having written nothing.

        The holder takes the FIRST line in lock order, which the pass names
        only in its LAST arm.  On the old code the match, creation and income
        arms all landed before the skip arm hit that line; here the pass's
        first statement that locks is the one that times out, and no
        ``INSERT`` or ``UPDATE`` precedes it.
        """
        with app.app_context():
            batch, lines = _four_arms(seed_user)
            db.session.commit()
            earliest = lines[0]

            holder = db.engine.connect()
            try:
                holder.execute(
                    text(
                        f"SELECT id FROM {_TABLE} WHERE id = :id "
                        "FOR NO KEY UPDATE"
                    ),
                    {"id": earliest.id},
                )
                scope = a_scope(seed_user)
                db.session.execute(
                    text(f"SET LOCAL lock_timeout = '{_BLOCK_TIMEOUT_MS}ms'"),
                )

                def _blocked():
                    """Return the timeout the pass dies of, or its outcome."""
                    try:
                        return statement_match.apply_reviewed(batch, scope)
                    except OperationalError as exc:
                        return exc

                result, statements = capture_sql_statements(_blocked)

                assert isinstance(result, OperationalError), (
                    f"the pass did not wait for the held line: {result}"
                )
                assert "lock timeout" in str(result).lower(), result
                emitted = [statement for statement, _ in statements]
                assert not any(_writes(statement) for statement in emitted), [
                    statement for statement in emitted if _writes(statement)
                ]
                locking = [
                    index for index, statement in enumerate(emitted)
                    if _locks_a_line(statement)
                ]
                assert locking == [len(emitted) - 1], (
                    "the pass took a lock before the one it waited on"
                )
                assert _LOCK in emitted[-1] and _ORDER in emitted[-1]
            finally:
                holder.rollback()
                holder.close()
                db.session.rollback()

    def test_the_same_pass_completes_when_nothing_holds_the_line(
        self, app, db, seed_user,
    ):
        """The non-vacuity control: same pass, same timeout, no holder."""
        with app.app_context():
            batch, _ = _four_arms(seed_user)
            db.session.commit()
            scope = a_scope(seed_user)
            db.session.execute(
                text(f"SET LOCAL lock_timeout = '{_BLOCK_TIMEOUT_MS}ms'"),
            )

            outcome = statement_match.apply_reviewed(batch, scope)

            assert outcome.applied_count == 4, outcome.refused
            assert outcome.refused_count == 0
            db.session.rollback()


class TestTheLockOutlivesARefusedItemsSavepoint:
    """Property 3: the pre-read's lock is the TRANSACTION's, not the item's.

    A door re-takes the lock INSIDE its item's savepoint, and PostgreSQL
    releases what a rolled-back subtransaction acquired -- so a pass that
    held its lines only through the doors handed a REFUSED item's line back
    mid-pass, and a crafted body naming that line again in a later arm
    re-took it in submission order: finding **N-471**'s window, one item
    wide.  The pre-read is taken outside every savepoint, and the parent's
    lock survives the child's abort.
    """

    def test_a_refused_items_line_is_still_held_by_the_pass(
        self, app, db, seed_user,
    ):
        """A second connection cannot take the refused item's line.

        The pass carries ONE item, a skip of a line a committed match already
        answers, so the door refuses it (ruling **R-HP**) and its savepoint
        rolls back.  ``NOWAIT`` on that line from another connection is still
        refused while the pass's transaction is open, and succeeds once it
        has ended.  FIRING CONTROL: delete the ``lock_lines`` call and the
        only lock on the line was the door's, gone with the savepoint, so the
        probe takes it and this fails.
        """
        with app.app_context():
            envelope = an_envelope(seed_user)
            line = an_unexplained_outflow(
                seed_user, merchant="Walmart", amount="-12.34",
            )
            db.session.commit()
            filed_by(seed_user, line, envelope, by_rule=False)
            db.session.commit()
            batch = ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(), incomes=(),
                skips=(SkipRequest(line_id=line.id),),
            )

            outcome = _pass(seed_user, batch)
            assert outcome.refused_count == 1, outcome.applied
            assert "already explained by a match" in outcome.refused[0].reason

            probe = db.engine.connect()
            try:
                with pytest.raises(OperationalError) as excinfo:
                    probe.execute(_NOWAIT, {"id": line.id})
                assert "could not obtain lock" in str(excinfo.value)
                probe.rollback()
                # The non-vacuity control: with the pass's transaction gone,
                # the same probe takes the line at once.
                db.session.rollback()
                probe.execute(_NOWAIT, {"id": line.id})
            finally:
                probe.rollback()
                probe.close()
                db.session.rollback()


class TestALineThePassMayNotTouchLocksNothing:
    """Property 4: the account filter, graded on the lock and the receipt."""

    def test_a_foreign_line_costs_one_item_and_holds_no_lock(
        self, app, db, seed_user, seed_second_user,
    ):
        """Another owner's line: its item refused, the rest landing, no lock.

        The pre-read filters on the pass's account, so the foreign id locks
        nothing -- a second connection takes that line at once while the
        pass's transaction is still open -- and the door's own refusal names
        the item, with the good item beside it landing.
        """
        with app.app_context():
            theirs = a_bank_line(
                seed_second_user, an_import(seed_second_user), amount="-5.00",
                description="NOT OURS",
            )
            mine = a_bank_line(
                seed_user, an_import(seed_user), amount="-9.99",
                description="OURS",
            )
            db.session.commit()
            batch = ReviewedBatch(
                consent=Consent.TICKED, matches=(), creations=(), incomes=(),
                skips=(SkipRequest(line_id=theirs.id), SkipRequest(line_id=mine.id)),
            )

            outcome, statements = capture_sql_statements(
                lambda: _pass(seed_user, batch),
            )

            assert outcome.applied_count == 1 and outcome.refused_count == 1
            assert outcome.refused[0].line_ids == (theirs.id,)
            assert outcome.applied[0].line_ids == (mine.id,)
            first = next(
                (statement, parameters) for statement, parameters in statements
                if _locks_a_line(statement)
            )
            assert _line_ids_bound(first[1]) == {theirs.id, mine.id}, (
                "the pre-read names every id the batch carries; the account "
                "FILTER is what keeps the foreign one from locking"
            )
            probe = db.engine.connect()
            try:
                probe.execute(_NOWAIT, {"id": theirs.id})
                with pytest.raises(OperationalError):
                    probe.execute(_NOWAIT, {"id": mine.id})
            finally:
                probe.rollback()
                probe.close()
                db.session.rollback()


class TestPostgreSQLOrdersTheLocksByTheOrderBy:
    """Property 6: the one claim the statement text cannot carry."""

    def test_the_plan_locks_above_the_ordering_node(self, app, db, seed_user):
        """``LockRows`` sits above the node that yields ascending ``id``.

        PostgreSQL applies ``ORDER BY`` first and takes each row's lock as
        the ordered rows come out, which is why two transactions running this
        read over overlapping sets wait on the first row they share rather
        than crossing.  Asserted off ``EXPLAIN`` because the property lives in
        the planner, not in our SQL: a statement with the right ``ORDER BY``
        whose plan locked in scan order would pass every text assertion above
        and order nothing.  The order is yielded either by a ``Sort`` on
        ``id`` or by a forward scan of the primary key; a plan showing
        neither is the failure this exists to catch.
        """
        with app.app_context():
            _, lines = _four_arms(seed_user)
            db.session.commit()
            # Pylint: protected-access -- the query builder is the package's
            # own; the plan under test is its plan.
            query = _resolve.locked_for_write(
                _resolve._lines_on(  # pylint: disable=protected-access
                    seed_user["account"].id,
                    frozenset(line.id for line in lines),
                ).with_entities(BankStatementLine.id)
            )
            compiled = str(query.statement.compile(
                dialect=db.engine.dialect, compile_kwargs={"literal_binds": True},
            ))
            assert _LOCK in compiled and _ORDER in compiled, compiled

            plan = [
                row[0] for row in
                db.session.execute(text("EXPLAIN " + compiled)).fetchall()
            ]

            assert plan[0].startswith("LockRows"), plan
            ordered_by_a_sort = any("Sort Key: id" in node for node in plan)
            # A forward scan of an index LED by ``id`` yields the order with
            # no sort node; which index the planner picks is its business,
            # so the leading column is read from the catalog, not guessed.
            forward_scans = [
                match.group(1) for node in plan
                for match in [re.search(r"Index (?:Only )?Scan using (\S+)", node)]
                if match is not None and "Backward" not in node
            ]
            led_by_id = [
                name for name in forward_scans
                if db.session.execute(
                    text(
                        "SELECT a.attname FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indexrelid "
                        "JOIN pg_attribute a "
                        "ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0] "
                        "WHERE c.relname = :name"
                    ),
                    {"name": name},
                ).scalar() == "id"
            ]
            assert ordered_by_a_sort or led_by_id, plan
            db.session.rollback()
