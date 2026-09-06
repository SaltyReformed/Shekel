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

from app.append_only_infrastructure import (
    APPEND_ONLY_TABLES,
    APPEND_ONLY_TRIGGERS,
)
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
                # The refusal lands at COMMIT, not at the statement: since
                # X-f3c-2d the delete arm is a DEFERRED constraint trigger,
                # because "is the owning account gone?" is a question about
                # the transaction's end state.  A case that asserted only the
                # statement would now pass while measuring nothing.
                db.session.commit()
            db.session.rollback()

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() >= 1

    def test_every_named_table_carries_every_arm(self, app, db, seed_user):
        """The census: three tables x three arms, none assumed.

        Asserted against ``pg_trigger`` rather than against the module's own
        constants, so a table added to
        :data:`app.append_only_infrastructure.APPEND_ONLY_TABLES` and never
        applied fails here rather than reading as covered -- and so does an
        arm added to
        :data:`app.append_only_infrastructure.APPEND_ONLY_TRIGGERS`.  The
        second half is what X-f3c-2c's version could not have caught: it
        counted one name, so a TRUNCATE arm that was never installed would
        have read as a full census.
        """
        with app.app_context():
            attached = {
                (row[0], row[1]) for row in db.session.execute(sa.text(
                    "SELECT n.nspname || '.' || c.relname, t.tgname "
                    "FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE t.tgname = ANY(:names)"
                ), {"names": list(APPEND_ONLY_TRIGGERS)}).all()
            }
            assert attached == {
                (table, name)
                for table in APPEND_ONLY_TABLES
                for name in APPEND_ONLY_TRIGGERS
            }

    def test_the_delete_arm_is_deferred_and_the_others_are_not(
        self, app, db, seed_user,
    ):
        """The timings differ ON PURPOSE, so the difference is asserted.

        A later hand that "simplified" the three arms back into one
        ``BEFORE UPDATE OR DELETE`` trigger would reopen both shapes X-f3c-2d
        closed, and every refusal case would still pass -- the combined
        trigger refuses all of them, just at the wrong moment.  What it could
        not do is be DEFERRABLE, which is the property that distinguishes
        disposal from delete-and-recreate.  ``tgdeferrable`` is therefore the
        thing graded, not the refusals.
        """
        with app.app_context():
            timings = dict(db.session.execute(sa.text(
                "SELECT t.tgname, t.tgdeferrable "
                "FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname || '.' || c.relname = "
                "'budget.account_anchor_history' "
                "AND t.tgname = ANY(:names)"
            ), {"names": list(APPEND_ONLY_TRIGGERS)}).all())

            assert timings == {
                "ck_append_only": False,
                "ck_append_only_delete": True,
                "ck_append_only_truncate": False,
            }

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


class TestTheShapesThatBrokeTheFIRSTVersion:
    """Plan step **balance:X-f3c-2d**: the two defects a refutation found.

    X-f3c-2c's guard was verified by hand on ONE shape -- a single account
    disposed of by a single ``DELETE`` -- and the suite that shipped with it
    graded exactly that shape.  A pass briefed to BREAK the carve-out found
    two ways through, both reproduced on a clone with controls that fired
    first, and both are asserted here.

    Each case reads its result back from PostgreSQL rather than from the
    session, and each asserts the rows it means to protect EXIST before trying
    to destroy them: a refusal with nothing to refuse is the failure mode this
    file has already paid for once.
    """

    @pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
    def test_truncate_is_refused_on_every_named_table(
        self, app, db, seed_user, table,
    ):
        """REFUTATION 1: ``TRUNCATE`` never reaches a row trigger.

        Measured on X-f3c-2c's guard: with every account still standing,
        ``TRUNCATE budget.account_openings`` took the table to zero and was
        refused by nothing.  ``system.audit_log`` is written by a row trigger
        too, so the log was byte-identical across the statement -- which made
        this the ONE spelling that destroyed history both unrefused and
        unrecorded.

        ``CASCADE`` is the spelling asserted because it is the destructive
        one: a plain ``TRUNCATE budget.account_anchor_history`` is stopped
        earlier by ``fk_transactions_reconciled_by``, so a case using it would
        pass on somebody else's refusal.
        """
        with app.app_context():
            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.execute(sa.text(f"TRUNCATE {table} CASCADE"))
            db.session.rollback()

    def test_the_truncate_arm_is_what_refuses_it(self, app, db, seed_user):
        """The control for the case above: lift the arms and TRUNCATE lands.

        Without this, ``test_truncate_is_refused_on_every_named_table`` would
        pass identically if some unrelated constraint were doing the refusing,
        which is exactly the trap the ``CASCADE`` note describes.
        """
        with app.app_context():
            assert db.session.query(AccountOpening).count() >= 1

            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text("TRUNCATE budget.account_openings CASCADE"),
                )
                assert db.session.query(AccountOpening).count() == 0
            db.session.rollback()

    def test_deleting_an_account_and_recreating_its_id_is_refused(
        self, app, db, seed_user,
    ):
        """REFUTATION 2: two ordinary statements defeated the predicate.

        ``DELETE FROM budget.accounts WHERE id=N`` then ``INSERT`` of the same
        id committed clean on X-f3c-2c's guard, leaving the account standing
        with its assertions destroyed -- because the predicate asked whether
        the owning account existed at the INSTANT the cascade ran, and at that
        instant it genuinely did not.  Deferred to COMMIT, the same predicate
        refuses it.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Recreated",
                anchor_balance=Decimal("321.00"),
            )
            db.session.commit()
            account_id = account.id
            user_id = account.user_id
            type_id = account.account_type_id
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() >= 1, "nothing to destroy, so nothing to refuse"

            with pytest.raises(sa.exc.InternalError, match="append-only"):
                db.session.execute(
                    sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                    {"i": account_id},
                )
                db.session.execute(sa.text(
                    "INSERT INTO budget.accounts "
                    "(id, user_id, account_type_id, name) "
                    "VALUES (:i, :u, :t, 'Recreated')"
                ), {"i": account_id, "u": user_id, "t": type_id})
                db.session.commit()
            db.session.rollback()

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() >= 1, (
                "the whole transaction must roll back, history included"
            )

    def test_a_disposal_conserves_every_column_in_the_audit_log(
        self, app, db, seed_user,
    ):
        """Why these tables need no archive of their own.

        The design of X-f3c-2d rests on this: once TRUNCATE is refused, every
        remaining path that removes a row from these tables writes
        ``to_jsonb(OLD)`` to ``system.audit_log`` first.  If that stopped being
        true -- a table dropped from ``AUDITED_TABLES``, an audit trigger not
        re-applied -- the guard would still refuse everything it refuses today
        while the claim "history is never destroyed without a record" quietly
        became false.  So the conservation is graded, not assumed.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Disposed With A Record",
                anchor_balance=Decimal("456.78"),
            )
            db.session.commit()
            account_id = account.id
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() >= 1

            db.session.execute(
                sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                {"i": account_id},
            )
            db.session.commit()

            conserved = db.session.execute(sa.text(
                "SELECT old_data FROM system.audit_log "
                "WHERE table_name = 'account_anchor_history' "
                "AND operation = 'DELETE' "
                "AND (old_data ->> 'account_id')::int = :i"
            ), {"i": account_id}).all()

            assert conserved, (
                "the disposal destroyed assertions and the audit log kept "
                "no record of them"
            )
            assert any(
                row[0]["anchor_balance"] == "456.78"
                or Decimal(str(row[0]["anchor_balance"])) == Decimal("456.78")
                for row in conserved
            ), (
                "the audit row exists but does not carry the balance it "
                f"destroyed: {[row[0] for row in conserved]}"
            )

    def test_a_multi_row_account_delete_disposes_of_all_of_them(
        self, app, db, seed_user,
    ):
        """One statement deleting several accounts is still a disposal.

        The referential action issues one child ``DELETE`` per deleted parent,
        so a live sibling cannot confuse ``OLD.account_id`` -- but that was an
        argument until this case, and the deferred arm made it worth
        re-asserting: at COMMIT, several accounts are gone at once and one
        remains.
        """
        with app.app_context():
            doomed = [
                create_account_of_type(
                    seed_user, db.session, "Savings", f"Doomed {n}",
                    anchor_balance=Decimal("10.00"),
                )
                for n in range(2)
            ]
            survivor = create_account_of_type(
                seed_user, db.session, "Savings", "Survivor",
                anchor_balance=Decimal("99.00"),
            )
            db.session.commit()
            doomed_ids = [a.id for a in doomed]
            survivor_id = survivor.id
            assert db.session.query(AccountAnchorHistory).filter(
                AccountAnchorHistory.account_id.in_(doomed_ids),
            ).count() >= 2

            db.session.execute(
                sa.text("DELETE FROM budget.accounts WHERE id = ANY(:ids)"),
                {"ids": doomed_ids},
            )
            db.session.commit()

            assert db.session.query(AccountAnchorHistory).filter(
                AccountAnchorHistory.account_id.in_(doomed_ids),
            ).count() == 0
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=survivor_id,
            ).count() >= 1, "the survivor's history went with somebody else's"


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
