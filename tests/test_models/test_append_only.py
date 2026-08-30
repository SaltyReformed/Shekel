"""
Shekel Budget App -- the three account-history tables refuse to be rewritten

Plan step **balance:X-f3c-2c**, ruling **balance:R-HY**, closing finding
**balance:N-287**.

``budget.account_anchor_history``, ``budget.account_openings`` and
``budget.loan_anchor_events`` each record a FACT about a moment.  A correction
answers one rather than rewriting it, and until this step that was true by
convention on the first table and by a SQLAlchemy listener on the other two --
a tier that sees only writes the ORM mediates.

**What this suite grades is the tier the listener could not reach.**  Each
refusal is asserted through the spelling that used to get past: a bulk
``query.update()`` -- the shape ``reconcile_service.record_settled_days``
already uses in production -- a bulk ``query.delete()``, and a raw statement.
Every case reads its result back from PostgreSQL rather than from the session,
because a refusal that only the ORM believes in is the thing being replaced.

**The DELETE arm's two halves are BOTH asserted, and that is the point of the
carve-out.**  Refusing every delete would make an account undeletable, which is
a regression rather than a stronger rule: ``AccountScopedMixin``'s
``ON DELETE CASCADE`` is the documented way an account's whole history is
disposed of.  So the trigger asks whether the owning account still stands.  A
case that only proved the refusal would pass just as well against a blanket arm
that breaks disposal, which is why the cascade case sits beside it.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.append_only_infrastructure import APPEND_ONLY_TABLES
from app.extensions import db
from app.models.account import (
    Account,
    AccountAnchorHistory,
    AccountAnchorHistoryImmutableError,
)
from app.models.account_opening import (
    AccountOpening,
    AccountOpeningImmutableError,
)
from app.models.append_only import AppendOnlyViolation
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from tests._test_helpers import (
    append_only_guard_lifted,
    create_account_of_type,
    create_loan_account,
    insert_trueup_event,
)


def _governing_assertion(account_id):
    """Return the account's newest assertion row, ORM-attached."""
    return (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.id.desc())
        .first()
    )


class TestTheObjectLayerNamesTheRefusal:
    """The listener half: a named exception, at the call site, with a remedy.

    The database refuses the same writes; what this tier adds is that a
    developer reads ``AccountAnchorHistoryImmutableError: ... Record a
    correction by inserting a new row`` instead of a ``psycopg2`` RaiseException
    naming a trigger, and that the suite can assert on a Shekel type.
    """

    def test_an_orm_update_raises_a_named_exception(self, app, db, seed_user):
        """Editing a loaded assertion raises before any SQL is emitted."""
        with app.app_context():
            row = _governing_assertion(seed_user["account"].id)

            row.anchor_balance = Decimal("4321.00")
            with pytest.raises(
                AccountAnchorHistoryImmutableError,
                match="Record a correction by inserting a new row",
            ):
                db.session.flush()
            db.session.rollback()

    def test_an_orm_delete_raises_a_named_exception(self, app, db, seed_user):
        """So does removing one, and the message names history's own disposal."""
        with app.app_context():
            row = _governing_assertion(seed_user["account"].id)

            db.session.delete(row)
            with pytest.raises(
                AccountAnchorHistoryImmutableError,
                match="History goes only with its account",
            ):
                db.session.flush()
            db.session.rollback()

    def test_each_table_raises_its_OWN_type_under_one_base(
        self, app, db, seed_user,
    ):
        """A caller may catch one table or all three.

        The shared installer would be a regression if it collapsed the three
        types into one: a handler meaning "an opening was edited" would then
        also swallow an assertion's refusal.  Both halves are asserted --
        the specific type, and that it is an :class:`AppendOnlyViolation`.
        """
        with app.app_context():
            opening = (
                db.session.query(AccountOpening)
                .filter_by(account_id=seed_user["account"].id)
                .first()
            )

            opening.opening_equity = Decimal("1.00")
            with pytest.raises(AccountOpeningImmutableError) as refusal:
                db.session.flush()
            db.session.rollback()

            assert isinstance(refusal.value, AppendOnlyViolation)
            assert not isinstance(
                refusal.value, AccountAnchorHistoryImmutableError,
            )


class TestTheDatabaseRefusesEverySpelling:
    """The trigger half: the tier the object listener cannot see."""

    def test_a_bulk_update_is_refused(self, app, db, seed_user):
        """``query.update()`` fires no listener, and is refused anyway.

        This is the spelling finding **N-287**'s harm sentence names: the app
        already writes this way in production
        (``reconcile_service.record_settled_days``), so a future door editing an
        assertion would most plausibly do it here, invisibly to the object
        layer.
        """
        with app.app_context():
            account = seed_user["account"]
            before = _governing_assertion(account.id).observed_on

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.query(AccountAnchorHistory).filter_by(
                    account_id=account.id,
                ).update({"observed_on": date(2020, 1, 1)})
            db.session.rollback()

            assert _governing_assertion(account.id).observed_on == before

    def test_a_raw_update_is_refused(self, app, db, seed_user):
        """A hand-written statement, which no application tier sees at all."""
        with app.app_context():
            account = seed_user["account"]
            row_id = _governing_assertion(account.id).id

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.execute(sa.text(
                    "UPDATE budget.account_anchor_history "
                    "SET anchor_balance = 1 WHERE id = :i"
                ), {"i": row_id})
            db.session.rollback()

            stored = db.session.execute(sa.text(
                "SELECT anchor_balance FROM budget.account_anchor_history "
                "WHERE id = :i"
            ), {"i": row_id}).scalar()
            assert stored == Decimal("1000.00")

    def test_a_bulk_delete_is_refused_while_the_account_stands(
        self, app, db, seed_user,
    ):
        """The DELETE arm's refusing half, on the spelling the listener misses."""
        with app.app_context():
            account = seed_user["account"]

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.query(AccountAnchorHistory).filter_by(
                    account_id=account.id,
                ).delete(synchronize_session=False)
            db.session.rollback()

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() >= 1

    def test_every_named_table_carries_the_trigger(self, app, db, seed_user):
        """The census: all three, not just the one the finding named.

        Asserted against ``pg_trigger`` rather than against the module's own
        constant, so a table added to
        :data:`app.append_only_infrastructure.APPEND_ONLY_TABLES` and never
        applied fails here rather than reading as covered.
        """
        with app.app_context():
            attached = {
                row[0] for row in db.session.execute(sa.text(
                    "SELECT n.nspname || '.' || c.relname "
                    "FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE t.tgname = 'ck_append_only'"
                )).all()
            }
            assert attached == set(APPEND_ONLY_TABLES)

    def test_the_two_sibling_tables_are_refused_too(self, app, db, seed_user):
        """One rule, three tables, asserted on the other two.

        ``account_openings`` and ``loan_anchor_events`` carried an ORM listener
        before this step; what is new for them is the spelling below.
        """
        with app.app_context():
            account = seed_user["account"]
            # **Each half asserts its row EXISTS first**, because a row trigger
            # fires per row: an UPDATE matching nothing raises nothing and
            # would read as a passing refusal.  The loan half was written
            # against a params-less Mortgage and did exactly that.
            assert db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).count() >= 1

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.execute(sa.text(
                    "UPDATE budget.account_openings SET opening_equity = 1 "
                    "WHERE account_id = :a"
                ), {"a": account.id})
            db.session.rollback()

            loan = create_loan_account(
                seed_user, db.session, name="Guarded Loan",
                principal=Decimal("20000.00"), term=60,
            )
            db.session.flush()
            params = (
                db.session.query(LoanParams)
                .filter_by(account_id=loan.id).one()
            )
            insert_trueup_event(params, Decimal("19500.00"))
            db.session.commit()
            assert db.session.query(LoanAnchorEvent).filter_by(
                account_id=loan.id,
            ).count() >= 1, (
                "the true-up must have written an anchor event, or the "
                "refusal below has nothing to refuse"
            )

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.execute(sa.text(
                    "UPDATE budget.loan_anchor_events SET anchor_balance = 1 "
                    "WHERE account_id = :a"
                ), {"a": loan.id})
            db.session.rollback()


class TestDisposingOfAnAccountStillWorks:
    """The carve-out, which is what keeps the rule from being a regression."""

    def test_a_cascade_from_the_account_takes_its_history(
        self, app, db, seed_user,
    ):
        """Deleting the account row disposes of all three tables' rows.

        The half a blanket DELETE arm would break.  PostgreSQL runs the
        ``ON DELETE CASCADE`` only once the parent row has left this
        transaction's snapshot, so the trigger finds no account and falls
        through -- and this asserts the ROWS are gone rather than that no
        exception was raised, because a cascade that silently did nothing would
        pass the weaker claim.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Disposable",
                anchor_balance=Decimal("100.00"),
            )
            db.session.commit()
            account_id = account.id
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() >= 1
            assert db.session.query(AccountOpening).filter_by(
                account_id=account_id,
            ).count() >= 1

            db.session.execute(sa.text(
                "DELETE FROM budget.accounts WHERE id = :i"
            ), {"i": account_id})
            db.session.commit()

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == 0
            assert db.session.query(AccountOpening).filter_by(
                account_id=account_id,
            ).count() == 0

    def test_the_orm_delete_of_an_account_does_not_touch_its_history(
        self, app, db, seed_user,
    ):
        """``session.delete(account)`` leaves the disposal to the database.

        The relationship half of the same property: ``Account.anchor_history``
        carries ``passive_deletes``, so the unit of work emits no DELETE the
        object listener would refuse.  Without it this raises
        :class:`~app.models.account.AccountAnchorHistoryImmutableError`, which
        is how the need was measured.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Disposable Two",
                anchor_balance=Decimal("100.00"),
            )
            db.session.commit()
            account_id = account.id
            # Load the collection first: an unloaded one proves less, because
            # ``passive_deletes`` is most easily got wrong for children the
            # session is already holding.
            assert account.anchor_history

            db.session.delete(account)
            db.session.commit()

            assert db.session.get(Account, account_id) is None
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == 0


class TestTheGuardCanBeLiftedAndComesBack:
    """The test instrument itself, because a lift that stuck would be silent."""

    def test_the_lift_restores_the_trigger_after_a_refusal(
        self, app, db, seed_user,
    ):
        """A block whose inner control REFUSES still leaves the guard on.

        The failure this exists to catch is invisible: a lift that did not
        restore would leave every later case in the same test unguarded, and
        they would pass.  So the guard is re-asserted afterwards by trying the
        write that must fail.
        """
        with app.app_context():
            account = seed_user["account"]
            row_id = _governing_assertion(account.id).id

            with append_only_guard_lifted(
                db.session, "budget.account_anchor_history",
            ):
                db.session.execute(sa.text(
                    "UPDATE budget.account_anchor_history "
                    "SET anchor_balance = 7 WHERE id = :i"
                ), {"i": row_id})
            db.session.rollback()

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.execute(sa.text(
                    "UPDATE budget.account_anchor_history "
                    "SET anchor_balance = 8 WHERE id = :i"
                ), {"i": row_id})
            db.session.rollback()

    def test_it_refuses_a_table_that_carries_no_such_trigger(
        self, app, db, seed_user,
    ):
        """A typo would lift nothing and the case would grade the wrong guard."""
        with app.app_context():
            with pytest.raises(AssertionError, match="no append-only trigger"):
                with append_only_guard_lifted(db.session, "budget.accounts"):
                    pass
