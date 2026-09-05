"""Plan step **balance:X-am**: the settled band has two members, not three.

Migration ``f2a9c4d7e310`` deletes the ``Settled`` status -- the terminal
ARCHIVE -- from ``ref.statuses``, re-pointing any row that carries it onto the
settled status its own TYPE takes.  Finding **N-177**, ruling **balance:R-HA**.

**Why this file exists rather than a line in the ref-cache suite.**  Three of
the migration's properties can only be graded against a database:

  * the re-point is TYPE-AWARE -- an archived expense becomes Paid and an
    archived income becomes Received -- and a blanket ``SET status_id = Paid``
    would pass a test that only counted rows;
  * the DELETE cannot orphan a row, and what makes that true is
    ``transactions_status_id_fkey``'s ``ON DELETE RESTRICT`` rather than any
    census the migration performs.  A claim about a constraint has to be shown
    with the constraint doing the refusing;
  * the downgrade restores a status the running code has no enum member for,
    so nothing above the SQL tier can express the assertion.

**Which cases run the migration's own SQL, and which do not.**  Every case in
the three migration classes loads the revision by path and executes its
statement objects, so those and the revision cannot drift -- the discipline
``test_c40_account_id_backfill`` established.
:class:`TestTheArchiveIsGoneFromTheLiveSchema` runs NONE of it: the test
template applies ``alembic upgrade head`` and then seeds from
``app/ref_seeds.py``, and no migration ever INSERTs this row, so the upgrade's
statements match nothing there.  Those two cases grade the SEED and the enum,
which is the state a fresh database lands in -- a different claim, and worth
separating rather than filing under one sentence.

**``upgrade()`` itself is never invoked here**, only the three statements it
runs.  Their ORDER is what the foreign key enforces at deploy time and is
asserted in prose in the revision, not by this file; what
:class:`TestTheForeignKeyIsWhatMakesTheDeleteSafe` grades is that the order
MATTERS -- the DELETE alone is refused, and the DELETE after the re-point is
not.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern;
# test bodies bind ``app`` / ``db`` / ``seed_user`` by name.
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import create_savings_account, load_migration_module
from app.models.amount_ownership import AmountOwnership

_MIGRATION = load_migration_module(
    "f2a9c4d7e310_the_settled_band_has_two_members.py"
)

#: The display name the deleted status carried.  Spelled here because the enum
#: member it named no longer exists -- which is the whole point of the step and
#: the reason these cases cannot be written in terms of ``StatusEnum``.
_ARCHIVE = "Settled"


def _restore_the_archive(db):
    """Run the migration's own downgrade SQL and return the restored row.

    The only way to get an archived row for the upgrade cases to re-point: the
    status does not exist, so no fixture, factory or enum member can name it.
    """
    db.session.execute(_MIGRATION._RESTORE_ARCHIVE, {"archive": _ARCHIVE})
    db.session.flush()
    return db.session.query(Status).filter_by(name=_ARCHIVE).one()


def _a_row(db, seed_user, period, status_id, *, income=False, name="row"):
    """Insert one transaction of the given TYPE in the given status."""
    txn = Transaction(
        account_id=seed_user["account"].id,
        user_id=period.user_id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status_id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
        ),
        amount_ownership=AmountOwnership.own(Decimal("42.00")),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestTheArchiveIsGoneFromTheLiveSchema:
    """The state the step is FOR, asserted where it is stored."""

    def test_no_row_named_settled_survives(self, app, db):
        """``ref.statuses`` holds no archive row after the migration."""
        with app.app_context():
            assert db.session.query(Status).filter_by(
                name=_ARCHIVE,
            ).one_or_none() is None

    def test_no_status_row_is_unnameable_by_the_enum(self, app, db):
        """Every seeded status has an enum member, and every member a row.

        The BOTH-ways assertion, because each direction fails differently and
        only one of them fails loudly.  A member with no row makes
        ``ref_cache.init`` raise at startup, so it cannot ship unnoticed.  A
        ROW with no member is silent: nothing resolves it, no screen offers it,
        and it sits in the table being dead vocabulary -- which is exactly the
        state ``Settled`` was in for the whole of its life, carrying zero rows
        while every reader consumed the settled band as a SET.
        """
        with app.app_context():
            rows = {row.name for row in db.session.query(Status).all()}
            members = {member.value for member in StatusEnum}
            assert rows == members, (
                f"rows with no enum member: {sorted(rows - members)}; "
                f"members with no row: {sorted(members - rows)}"
            )


class TestTheUpgradeRePointsBeforeItDeletes:
    """The backfill, shown moving rows rather than assumed to be a no-op.

    It IS a no-op on every database that exists today -- measured 2026-08-27:
    zero rows in production and in every snapshot, and no row in the 1,591
    ``system.audit_log`` entries for these two tables since 2026-05-07 (229 of
    them DELETEs) ever names the status, which is what rules out one that
    existed between snapshots.  That is not a reason to leave it ungraded: the
    full-edit Status dropdown offered the archive right up until this revision
    ran, so an owner could archive a row between the last measurement and the
    deploy, and this is the code that would have to handle it.
    """

    def test_an_archived_EXPENSE_becomes_paid(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Type-aware, arm one."""
        with app.app_context():
            archive = _restore_the_archive(db)
            txn = _a_row(
                db, seed_user, seed_periods_today[3], archive.id,
                name="archived expense",
            )
            db.session.execute(
                _MIGRATION._REPOINT_TRANSACTIONS, {"archive": _ARCHIVE},
            )
            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )

    def test_an_archived_INCOME_becomes_received(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Type-aware, arm two -- and the arm a blanket UPDATE would fail.

        Written as its own case rather than a second assertion beside the
        expense one so a re-point that sets everything to Paid fails HERE with
        a name that says what it got wrong.
        """
        with app.app_context():
            archive = _restore_the_archive(db)
            txn = _a_row(
                db, seed_user, seed_periods_today[3], archive.id,
                income=True, name="archived income",
            )
            db.session.execute(
                _MIGRATION._REPOINT_TRANSACTIONS, {"archive": _ARCHIVE},
            )
            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).status_id == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

    def test_a_row_that_was_NOT_archived_is_untouched(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The other half of the narrowing: the UPDATE has a WHERE clause.

        Without this the two cases above would pass on a statement that
        re-pointed the whole table, which is the direction that loses data.
        """
        with app.app_context():
            _restore_the_archive(db)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            txn = _a_row(
                db, seed_user, seed_periods_today[3], cancelled_id,
                name="cancelled expense",
            )
            db.session.execute(
                _MIGRATION._REPOINT_TRANSACTIONS, {"archive": _ARCHIVE},
            )
            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).status_id == cancelled_id

    def test_an_archived_TRANSFER_becomes_paid(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A transfer has ONE settled target whatever direction it runs.

        The income/expense split is meaningless for a pair whose whole point is
        that one leg is each, which is why ``state_machine``'s transfer map
        excludes Received outright -- so the transfer re-point needs no type
        test, and this pins that it does not grow one.
        """
        with app.app_context():
            archive = _restore_the_archive(db)
            # ``ck_transfers_different_accounts`` -- a transfer needs two.
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods_today[3].id,
                scenario_id=seed_user["scenario"].id,
                status_id=archive.id,
                amount_ownership=AmountOwnership.own(Decimal("50.00")),
                name="archived transfer",
            )
            db.session.add(xfer)
            db.session.flush()

            db.session.execute(
                _MIGRATION._REPOINT_TRANSFERS, {"archive": _ARCHIVE},
            )
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )


class TestTheForeignKeyIsWhatMakesTheDeleteSafe:
    """The migration writes no census, and this is why it does not need one.

    ``transactions_status_id_fkey`` and ``transfers_status_id_fkey`` are both
    ``ON DELETE RESTRICT``, so a row the re-point missed makes the DELETE raise
    and rolls the whole revision back with the stamp untouched -- which is the
    state ``deploy/shekel-deploy.sh`` can still revert the image pin from.  A
    ``SELECT count(*)`` guard above the DELETE would restate what the
    constraint already holds and could only be tested by removing it.
    """

    def test_the_delete_alone_is_refused_while_a_row_references_it(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Run step 3 WITHOUT the re-point: the schema refuses it."""
        with app.app_context():
            archive = _restore_the_archive(db)
            _a_row(
                db, seed_user, seed_periods_today[3], archive.id,
                name="archived expense",
            )

            with pytest.raises(IntegrityError, match="transactions_status_id_fkey"):
                db.session.execute(
                    _MIGRATION._DELETE_ARCHIVE, {"archive": _ARCHIVE},
                )
                db.session.flush()

    def test_a_TRANSFER_alone_also_refuses_the_delete(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The SECOND foreign key, graded separately from the first.

        This class's argument names ``transactions_status_id_fkey`` AND
        ``transfers_status_id_fkey``, and a case that only ever puts a
        transaction in front of the DELETE grades one of them.  The transfer
        re-point is a different statement with a different target, so a bug
        that dropped it would be invisible to the transaction case.
        """
        with app.app_context():
            archive = _restore_the_archive(db)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods_today[3].id,
                scenario_id=seed_user["scenario"].id,
                status_id=archive.id,
                amount_ownership=AmountOwnership.own(Decimal("50.00")),
                name="archived transfer",
            )
            db.session.add(xfer)
            db.session.flush()

            # Only the TRANSACTION arm has run, so the transfer is what is left
            # holding the reference.
            db.session.execute(
                _MIGRATION._REPOINT_TRANSACTIONS, {"archive": _ARCHIVE},
            )
            with pytest.raises(IntegrityError, match="transfers_status_id_fkey"):
                db.session.execute(
                    _MIGRATION._DELETE_ARCHIVE, {"archive": _ARCHIVE},
                )
                db.session.flush()

    def test_the_delete_succeeds_once_the_re_point_has_run(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The same fixture, with the re-point restored: the DELETE lands.

        The positive half, and it is what makes the case above a control rather
        than an assertion that the DELETE never works.
        """
        with app.app_context():
            archive = _restore_the_archive(db)
            _a_row(
                db, seed_user, seed_periods_today[3], archive.id,
                name="archived expense",
            )
            db.session.execute(
                _MIGRATION._REPOINT_TRANSACTIONS, {"archive": _ARCHIVE},
            )
            db.session.execute(
                _MIGRATION._DELETE_ARCHIVE, {"archive": _ARCHIVE},
            )
            db.session.flush()

            assert db.session.query(Status).filter_by(
                name=_ARCHIVE,
            ).one_or_none() is None


class TestTheDowngradeRestoresTheStatus:
    """What the downgrade owes, and what it deliberately does not."""

    def test_it_restores_the_row_with_its_flags(self, app, db):
        """The old code resolves this status by NAME, and needs its booleans.

        ``is_settled`` is what puts it back in the band every balance reader
        consumes; ``is_immutable`` is what keeps the finalised-edit lock on it;
        ``excludes_from_balance`` false is what keeps its rows in the balance.
        A downgrade that restored the name alone would boot the old code
        against a status that silently contributes nothing.
        """
        with app.app_context():
            restored = _restore_the_archive(db)
            assert restored.is_settled is True
            assert restored.is_immutable is True
            assert restored.excludes_from_balance is False

    def test_it_is_idempotent(self, app, db):
        """A repeated downgrade is inert, not a unique violation.

        ``ON CONFLICT (name) DO NOTHING`` against ``statuses_name_key``.  An
        operator stepping a revision twice is an ordinary thing to do, and the
        second pass must not be the one that fails.
        """
        with app.app_context():
            first = _restore_the_archive(db)
            second = _restore_the_archive(db)
            assert first.id == second.id
            assert db.session.query(Status).filter_by(
                name=_ARCHIVE,
            ).count() == 1

    def test_the_id_is_not_a_fact_anything_depends_on(self, app, db):
        """The restored row takes a fresh sequence value, and that is fine.

        Nothing stores this id: ``ref_cache`` resolves statuses by NAME at
        startup, and the only columns pointing at ``ref.statuses.id`` are the
        two ``status_id`` foreign keys -- which by the time a downgrade runs
        hold no reference to it, because the upgrade re-pointed them all.

        Pinned because the alternative shape is tempting and wrong: an
        ``INSERT`` with an explicit ``id = 6`` would look more faithful and
        would be a lie on any database whose statuses were seeded in a
        different order.  Production, dev and the migration tree's own
        inline seed already disagree about this row's id.
        """
        with app.app_context():
            restored = _restore_the_archive(db)
            live_ids = {
                ref_cache.status_id(member) for member in StatusEnum
            }
            assert restored.id not in live_ids, (
                "the restored archive collided with a live status id"
            )
