"""The test clock reaches the DATABASE clock (finding N-65).

``tests/test_services`` freezes ``date.today()`` to 2026-03-20, inside the
``seed_periods`` window.  PostgreSQL's clock was untouched, and it answers in
FOUR places: 61 columns take their INSERT value from a ``NOW()`` server
default, one (``transaction_entries.entry_date``) from a raw-text
``CURRENT_DATE`` default, 23 of them re-stamp on UPDATE, and ``status_seam``
assigns ``db.func.now()`` to ``Transaction.paid_at`` outright.  So a fixture that
settled a row "now" stamped it at the real wall clock -- months outside the
periods the test seeded -- and the balance fold, which dates every event,
replayed it outside the window entirely.  Nothing noticed while the shipping
producers read the LATEST anchor row and ignored its date; the fold made the
instant load-bearing.

The per-fixture mitigations (``override_anchor``'s period-start default,
``conftest._pin_opening_to``, an explicit ``paid_at``) stay and are unaffected
-- this pins the STRUCTURAL half that stops a fourth instance:
``_test_helpers._freeze_db_clock``.  Read its docstring for the design and its
one stated boundary.

Every assertion below is against a stored value read back from PostgreSQL,
never against the helper's own bookkeeping.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import event

from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import (
    _db_clock_insert_attrs,
    _rewrite_db_clock_calls,
    create_settled_cash_transaction,
    override_anchor,
)

#: The instant ``tests/test_services/conftest.py`` freezes the suite to.
FROZEN_DATE = date(2026, 3, 20)


class TestTheDatabaseClockIsTheTestClock:
    """A row the DATABASE timestamps lands on the day the test froze."""

    def test_the_omitted_default_columns_are_derived_not_listed(self):
        """Premise: the derivation finds every clock default, in both spellings.

        Asserted first and separately because every behavioural assertion in
        this class is vacuous if the derivation returns nothing -- a stamping
        rule that covers no columns would leave every timestamp to the database
        and still pass a test that only checked "no crash".
        """
        anchor_attrs = dict(_db_clock_insert_attrs(AccountAnchorHistory))
        assert "created_at" in anchor_attrs, (
            "AccountAnchorHistory.created_at is the NOW() server default "
            f"finding N-65 names; derivation found {anchor_attrs!r}"
        )
        assert anchor_attrs["created_at"] is False  # an instant, not a date
        txn_attrs = dict(_db_clock_insert_attrs(Transaction))
        assert "created_at" in txn_attrs
        # ``paid_at`` is NOT a column default -- it is an assignment inside the
        # status seam -- so it must NOT appear here.  That it is frozen anyway
        # is what the settle test below proves, through the other mechanism.
        assert "paid_at" not in txn_attrs
        # The RAW-TEXT spelling: ``entry_date`` defaults to
        # ``db.text("CURRENT_DATE")``, a TextClause, so an
        # ``isinstance(..., now)`` test alone is blind to it -- which is how it
        # kept landing on the real wall date while every timestamp beside it
        # was frozen (found by plan step X-h's adversarial review).  And it is
        # a DATE column, so it must be stamped with a date.
        entry_attrs = dict(_db_clock_insert_attrs(TransactionEntry))
        assert entry_attrs.get("entry_date") is True, (
            "transaction_entries.entry_date is a CURRENT_DATE text default on a "
            f"DATE column; derivation found {entry_attrs!r}"
        )

    def test_a_server_defaulted_instant_lands_on_the_frozen_day(
        self, app, db, seed_user, seed_periods,
    ):
        """An anchor row written with no explicit instant is dated 2026-03-20.

        ``AccountAnchorHistory.created_at`` is a ``NOW()`` server default, so
        before this fix the row carried the real wall clock: months past the
        end of ``seed_periods`` (2026-01-02 to 2026-05-21), which is a state
        production cannot reach -- a true-up files against
        ``get_current_period``.
        """
        with app.app_context():
            account = seed_user["account"]
            row = AccountAnchorHistory(
                account_id=account.id,
                pay_period_id=seed_periods[5].id,
                anchor_balance=Decimal("1234.56"),
                notes="N-65: no explicit instant",
            )
            db.session.add(row)
            db.session.commit()

            db.session.expire(row)
            assert row.created_at.date() == FROZEN_DATE, (
                f"anchor row dated {row.created_at!r}, not the frozen "
                f"{FROZEN_DATE!r} -- the database clock escaped the freeze"
            )
            # And it is inside the seeded window, which is the property the
            # fold actually depends on.
            assert (
                seed_periods[0].start_date
                <= row.created_at.date()
                <= seed_periods[-1].end_date
            )

    def test_a_settled_transactions_paid_at_lands_on_the_frozen_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The status seam's ``db.func.now()`` assignment is frozen too.

        ``paid_at`` is not a column default: ``status_seam`` assigns
        ``db.func.now()`` to it so PostgreSQL evaluates the instant
        server-side.  That renders ``now()`` into the INSERT, which is the
        second of the three mechanisms and the one N-65 names first.
        """
        with app.app_context():
            txn = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[5], Decimal("25.00"),
                name="N-65: settled at the frozen now",
            )
            db.session.commit()

            db.session.expire(txn)
            assert txn.paid_at is not None
            assert txn.paid_at.date() == FROZEN_DATE, (
                f"paid_at is {txn.paid_at!r}, not the frozen {FROZEN_DATE!r}"
            )

    def test_an_onupdate_column_is_frozen_on_a_row_update(
        self, app, db, seed_user, seed_periods,
    ):
        """``updated_at`` re-stamps from the frozen clock, not the wall clock.

        The ``onupdate=NOW()`` mechanism: 23 columns re-stamp on every UPDATE,
        and the call is rendered into the statement rather than supplied by a
        default, which is why the statement rewriter and not the flush listener
        is what covers it.
        """
        with app.app_context():
            txn = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[5], Decimal("40.00"),
                name="N-65: onupdate",
            )
            db.session.commit()

            txn.name = "N-65: onupdate, renamed"
            db.session.commit()

            db.session.expire(txn)
            assert txn.updated_at.date() == FROZEN_DATE, (
                f"updated_at is {txn.updated_at!r}, not the frozen {FROZEN_DATE!r}"
            )

    def test_a_bulk_update_is_frozen_too(
        self, app, db, seed_user, seed_periods,
    ):
        """A statement-level UPDATE bypasses the ORM and is frozen anyway.

        ``carry_forward_service`` moves rows with ``query.update(...)``, which
        SQLAlchemy renders as one statement carrying ``updated_at=now()`` and
        which never enters the session's unit of work -- so no ``before_flush``
        listener can reach it.  This is the path the FIRST draft of the fix
        missed: it stamped mapped objects, and the full suite came back with 41
        failures, every one of them a bulk UPDATE.
        """
        with app.app_context():
            txn = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[5], Decimal("55.00"),
                name="N-65: bulk",
            )
            db.session.commit()
            txn_id = txn.id

            db.session.query(Transaction).filter(
                Transaction.id == txn_id,
            ).update(
                {"name": "N-65: bulk, moved"}, synchronize_session=False,
            )
            db.session.commit()

            moved = db.session.get(Transaction, txn_id)
            db.session.expire(moved)
            assert moved.name == "N-65: bulk, moved"
            assert moved.updated_at.date() == FROZEN_DATE, (
                f"a bulk UPDATE stamped {moved.updated_at!r}, not the frozen "
                f"{FROZEN_DATE!r} -- the statement rewriter did not reach it"
            )

    def test_the_rewriter_is_bound_before_any_test_flushes(self, app):
        """The rewriter's INSTALLATION must not depend on test order.

        It used to be bound lazily, from inside the flush listener, so a frozen
        test whose only writes were bulk ``query.update(...)`` never installed
        it and silently got the real wall clock.  Measured by plan step X-h's
        adversarial review, same test and same assertion:

            fresh process                  -> updated_at 2026-07-28  (WRONG)
            after another test had flushed -> updated_at 2026-03-20  (right)

        It is bound by the session-scoped ``setup_database`` fixture instead,
        so this holds at the first statement of the first test.
        """
        with app.app_context():
            assert event.contains(
                _db.engine, "before_cursor_execute", _rewrite_db_clock_calls,
            ), "the frozen-clock rewriter is not bound to the engine"

    def test_a_raw_text_date_default_is_frozen_too(
        self, app, db, seed_user, seed_periods,
    ):
        """``entry_date`` defaults to ``CURRENT_DATE`` and must be frozen.

        The fourth mechanism, and the one an ``isinstance(..., now)``
        derivation is structurally blind to: the default is
        ``db.text("CURRENT_DATE")``, a ``TextClause``.  It is also a ``DATE``
        column, so it must be answered with a DATE -- handing it an instant is
        a different value, not a formatting detail.  Found by plan step X-h's
        adversarial review, from PostgreSQL's own error DETAIL on a row whose
        timestamps were frozen and whose ``entry_date`` was not.
        """
        with app.app_context():
            txn = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[5], Decimal("30.00"),
                name="N-65: raw text default",
            )
            entry = TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=Decimal("5.00"),
                description="N-65: no explicit entry_date",
            )
            db.session.add(entry)
            db.session.commit()

            db.session.expire(entry)
            assert entry.entry_date == FROZEN_DATE, (
                f"entry_date is {entry.entry_date!r}, not the frozen "
                f"{FROZEN_DATE!r} -- a raw-text clock default escaped"
            )

    def test_ddl_is_not_rewritten(self):
        """Declaring a ``DEFAULT now()`` column is not asking the time.

        The exemption is load-bearing and never fires in the suite by
        construction (the schema is built from a template before any test), so
        it is pinned here directly: rewriting a ``CREATE TABLE`` would bake a
        frozen instant into the SCHEMA, which is a worse defect than the one
        being fixed.  ``DO $$ ... $$`` is exempt for the same reason -- this
        app ships audit and posting infrastructure as anonymous blocks.
        """
        ddl = "CREATE TABLE t (created_at timestamptz NOT NULL DEFAULT now())"
        assert _rewrite_db_clock_calls(
            None, None, ddl, {}, None, False,
        )[0] == ddl
        block = "DO $$ BEGIN PERFORM now(); END $$"
        assert _rewrite_db_clock_calls(
            None, None, block, {}, None, False,
        )[0] == block
        # And a real UPDATE is still rewritten, so the exemption is not
        # swallowing everything.
        dml = "UPDATE budget.transactions SET updated_at=now() WHERE id = 1"
        rewritten = _rewrite_db_clock_calls(None, None, dml, {}, None, False)[0]
        assert "now()" not in rewritten and "TIMESTAMPTZ '" in rewritten

    def test_rows_written_in_sequence_keep_their_order(
        self, app, db, seed_user, seed_periods,
    ):
        """Two anchors written in order are strictly increasing, not tied.

        The reason the frozen clock advances a microsecond per row rather than
        handing every row one flat instant: the app resolves an account's
        current anchor by ``ORDER BY created_at DESC``, and PostgreSQL breaks a
        tie arbitrarily.  A flat freeze would turn a deterministic fixture into
        a coin flip -- trading N-65 for a flake.
        """
        with app.app_context():
            account = seed_user["account"]
            first = override_anchor(
                db.session, account, seed_periods[4], Decimal("100.00"),
                notes="N-65 ordering: first",
                at=None,
            )
            second = AccountAnchorHistory(
                account_id=account.id,
                pay_period_id=seed_periods[5].id,
                anchor_balance=Decimal("200.00"),
                notes="N-65 ordering: second",
            )
            db.session.add(second)
            db.session.commit()
            third = AccountAnchorHistory(
                account_id=account.id,
                pay_period_id=seed_periods[5].id,
                anchor_balance=Decimal("300.00"),
                notes="N-65 ordering: third",
            )
            db.session.add(third)
            db.session.commit()

            db.session.expire(second)
            db.session.expire(third)
            assert second.created_at < third.created_at, (
                f"two rows written in sequence tied at {second.created_at!r} "
                "-- the anchor resolver's ORDER BY is now a coin flip"
            )
            assert third.created_at - second.created_at < timedelta(seconds=1)
            # The explicitly-pinned row is untouched: the freeze supplies an
            # instant, it never overwrites one a fixture chose.
            assert first.created_at.date() == seed_periods[4].start_date
