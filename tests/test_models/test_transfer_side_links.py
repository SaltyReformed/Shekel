"""Plan step ``balance:X-bi-6-4d-2``: a transfer side's payment hangs off the transfer.

Ruling **R-BAL88** (developer 2026-09-20, "Two side-keyed links"): a transfer
side's payment record names its transfer by one of two link columns, each in a
COMPOSITE key with the record's ``account_id`` onto that side's endpoint, under a
CHECK that exactly one parent is set.  *"The side IS which column is set, and the
account IS the endpoint -- structurally, no trigger.  The bug's row is
UNSTORABLE."*  Ruling **R-BAL107** ("Key transfer accounts to owner") keys each
endpoint to the transfer's owner, so a record is the owner's by the chain.
Ruling **R-BAL168** makes the side keys ``ON UPDATE CASCADE``: an endpoint move
carries the record.  And the deleted-row rule (rulings **R-CC89**, **R-CC92**)
gains its transfer arm, :data:`app.deleted_row_infrastructure.TRANSFER_ARM`.

Each case writes the state with raw SQL or the bare ORM, the way a writer
nobody enumerated would, against the database the suite runs on, and each
refusal sits beside the control that lands.  The records planted are KEPT
un-dated ones (a revert's, ruling R-BAL61) under a Projected transfer, the one
side-record state no settle verb is needed to reach.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from tests._test_helpers import (
    constraint_name_from,
    create_account_of_type,
    create_envelope_txn,
    create_transfer,
    refused_by_database_rule,
)

#: The arrival arm's transfer refusal, as the database words it.
_ARRIVAL_REFUSED = "transfer .* was deleted: a payment cannot be recorded under it"

#: The transfer hiding arm's refusal, as the database words it.
_HIDING_REFUSED = "transfer .* was deleted while it still holds a recorded payment"


def _accounts(seed_user):
    """Return ``(checking, savings, van loan)`` -- the worked example's accounts."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Side Savings",
    )
    van = create_account_of_type(
        seed_user, _db.session, "Savings", "Side Van Fund",
    )
    _db.session.commit()
    return seed_user["account"], savings, van


def _transfer(seed_user, from_account, to_account):
    """A Projected $500.00 transfer between the two accounts, committed."""
    xfer = create_transfer(
        seed_user, _db.session, from_account, to_account,
        seed_user["bootstrap_period"], amount=Decimal("500.00"),
    )
    _db.session.commit()
    return xfer


def _record(xfer, *, income=False, account_id=None, covers=True, row_id=None):
    """Stage one side's kept, un-dated payment record of *xfer*; returns it unflushed.

    Args:
        xfer: The transfer.
        income: ``True`` for the to-side link, ``False`` for the from-side.
        account_id: The record's account; the side's endpoint when ``None``.
        covers: The ``covers_settlement`` mark.
        row_id: A plan row to ALSO name as parent, for the one-parent cases.
    """
    if account_id is None:
        account_id = xfer.to_account_id if income else xfer.from_account_id
    entry = TransactionEntry(
        transaction_id=row_id,
        expense_transfer_id=None if income else xfer.id,
        income_transfer_id=xfer.id if income else None,
        account_id=account_id,
        owner_id=xfer.user_id,
        user_id=xfer.user_id,
        amount=Decimal("500.00"),
        description="Side record",
        covers_settlement=covers,
        figure_source_id=ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.RESOLVED,
        ),
    )
    _db.session.add(entry)
    return entry


def _refused_by(constraint):
    """Expect the next flush to raise an IntegrityError on *constraint*."""
    with pytest.raises(sa_exc.IntegrityError) as excinfo:
        _db.session.flush()
    _db.session.rollback()
    assert constraint_name_from(excinfo.value) == constraint


class TestTheSideKeys:
    """The account IS the side's endpoint (ruling R-BAL88's worked example)."""

    def test_each_side_holds_its_record_on_its_endpoint(self, app, seed_user):
        """CONTROL: one record per side, each on its own endpoint, stores."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer)
        _record(xfer, income=True)
        _db.session.commit()

        stored = _db.session.execute(text(
            "SELECT expense_transfer_id, income_transfer_id, account_id "
            "FROM budget.transaction_entries "
            "WHERE coalesce(expense_transfer_id, income_transfer_id) = :t "
            "ORDER BY account_id"
        ), {"t": xfer.id}).all()
        assert sorted(stored, key=lambda row: row[2]) == sorted([
            (xfer.id, None, checking.id), (None, xfer.id, savings.id),
        ], key=lambda row: row[2])

    def test_a_from_side_record_on_another_account_is_unstorable(self, app, seed_user):
        """$500.00 Checking -> Savings; its from-side record written on Van Fund."""
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, account_id=van.id)
        _refused_by("fk_transaction_entries_expense_side")

    def test_a_from_side_record_on_the_to_account_is_unstorable(self, app, seed_user):
        """The side is the link column, never inferred from the account."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, account_id=savings.id)
        _refused_by("fk_transaction_entries_expense_side")

    def test_a_to_side_record_on_the_from_account_is_unstorable(self, app, seed_user):
        """The to-side twin of the case above."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, income=True, account_id=checking.id)
        _refused_by("fk_transaction_entries_income_side")

    def test_a_side_holds_one_record(self, app, seed_user):
        """A second from-side record is refused by the per-side unique index."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer)
        _db.session.commit()
        _record(xfer)
        _refused_by("uq_transaction_entries_one_expense_side_record")


class TestOneParent:
    """A movement is filed under exactly one parent, and a side link is a record."""

    def test_a_record_naming_a_row_and_a_side_is_unstorable(self, app, seed_user):
        """Both parents at once."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        row = create_envelope_txn(
            seed_user, _db.session, seed_user["bootstrap_period"],
            "Side Groceries", Decimal("80.00"),
        )
        _db.session.commit()
        _record(xfer, row_id=row.id)
        _refused_by("ck_transaction_entries_one_parent")

    def test_a_record_naming_both_sides_is_unstorable(self, app, seed_user):
        """Two side links: which side it is would be ambiguous."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        entry = _record(xfer)
        entry.income_transfer_id = xfer.id
        # The CHECK, not the to-side key the from-account also violates: a
        # CHECK is evaluated as the row is written, a foreign key after it.
        _refused_by("ck_transaction_entries_one_parent")

    def test_a_record_naming_no_parent_is_unstorable(self, app, seed_user):
        """``transaction_id`` NULL is a fact only because a side link is set."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        entry = _record(xfer)
        entry.expense_transfer_id = None
        _refused_by("ck_transaction_entries_one_parent")

    def test_a_side_link_that_is_not_a_record_is_unstorable(self, app, seed_user):
        """A transfer side holds its payment and nothing else."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, covers=False)
        _refused_by("ck_transaction_entries_side_link_is_a_record")


class TestTheTransferOwnerKeys:
    """A transfer's accounts are its owner's (ruling R-BAL107)."""

    @pytest.mark.parametrize("side", ["from_account_id", "to_account_id"])
    def test_a_transfer_naming_another_users_account_is_unstorable(
        self, app, seed_user, second_user, side,
    ):
        """The R-BAL107 example: a transfer re-pointed at another user's account."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        with pytest.raises(sa_exc.IntegrityError) as excinfo:
            _db.session.execute(
                text(f"UPDATE budget.transfers SET {side} = :a WHERE id = :t"),
                {"a": second_user["account"].id, "t": xfer.id},
            )
        _db.session.rollback()
        assert constraint_name_from(excinfo.value) == (
            "fk_transfers_owner_from_account" if side == "from_account_id"
            else "fk_transfers_owner_to_account"
        )

    def test_a_transfer_between_its_owners_accounts_moves(self, app, seed_user):
        """CONTROL: re-pointing onto another of the owner's own accounts lands."""
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _db.session.execute(
            text("UPDATE budget.transfers SET to_account_id = :a WHERE id = :t"),
            {"a": van.id, "t": xfer.id},
        )
        _db.session.commit()
        assert _db.session.get(Transfer, xfer.id).to_account_id == van.id


class TestTheSideKeysCarryAndKeep:
    """ON UPDATE CASCADE carries a record (R-BAL168); NO ACTION keeps it (R-CC54)."""

    def test_an_endpoint_move_carries_the_sides_record_in_the_database(
        self, app, seed_user,
    ):
        """The database half of R-BAL168: the record follows its endpoint.

        One raw statement moves the from-account; no application code runs, so
        the record's new account is the key's cascade alone.  The to-side
        record stays where it is.
        """
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        expense = _record(xfer)
        income = _record(xfer, income=True)
        _db.session.commit()
        expense_id, income_id = expense.id, income.id

        _db.session.execute(
            text("UPDATE budget.transfers SET from_account_id = :a WHERE id = :t"),
            {"a": van.id, "t": xfer.id},
        )
        _db.session.commit()

        accounts = dict(_db.session.execute(text(
            "SELECT id, account_id FROM budget.transaction_entries "
            "WHERE id IN (:e, :i)"
        ), {"e": expense_id, "i": income_id}).all())
        assert accounts == {expense_id: van.id, income_id: savings.id}

    def test_deleting_a_transfer_holding_a_record_is_refused(self, app, seed_user):
        """A transfer holding a payment is history; only the removal act takes it off."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer)
        _db.session.commit()
        with pytest.raises(sa_exc.IntegrityError) as excinfo:
            _db.session.execute(
                text("DELETE FROM budget.transactions WHERE transfer_id = :t"),
                {"t": xfer.id},
            )
            _db.session.execute(
                text("DELETE FROM budget.transfers WHERE id = :t"),
                {"t": xfer.id},
            )
        _db.session.rollback()
        assert constraint_name_from(excinfo.value) == (
            "fk_transaction_entries_expense_side"
        )


def _hide_transfer(xfer_id):
    """Stage ``UPDATE ... SET is_deleted`` on transfer *xfer_id*; the caller commits."""
    _db.session.execute(
        text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
        {"t": xfer_id},
    )


class TestTheDeletedRowTransferArm:
    """No payment under a deleted transfer, either way round (R-CC89, R-CC92)."""

    def test_a_record_arriving_under_a_deleted_transfer_is_refused(
        self, app, seed_user,
    ):
        """The arrival arm reads the TRANSFER's ``is_deleted``."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _hide_transfer(xfer.id)
        _db.session.commit()
        _record(xfer)
        with refused_by_database_rule(_ARRIVAL_REFUSED):
            _db.session.flush()
        _db.session.rollback()

    def test_a_record_re_pointed_onto_a_deleted_transfer_is_refused(
        self, app, seed_user,
    ):
        """An UPDATE of the link is an arrival too; the live original is the control."""
        del app
        checking, savings, van = _accounts(seed_user)
        live = _transfer(seed_user, checking, savings)
        _record(live)
        # Its own to-account, so the ad-hoc double-submit index does not see
        # the two transfers as one; the from-side record moves between two
        # transfers sharing its from-account.
        hidden = _transfer(seed_user, checking, van)
        _db.session.commit()
        _hide_transfer(hidden.id)
        _db.session.commit()
        with refused_by_database_rule(_ARRIVAL_REFUSED):
            _db.session.execute(
                text("UPDATE budget.transaction_entries "
                     "SET expense_transfer_id = :h WHERE expense_transfer_id = :l"),
                {"h": hidden.id, "l": live.id},
            )
        _db.session.rollback()

    def test_hiding_a_transfer_that_holds_a_record_is_refused_at_commit(
        self, app, seed_user,
    ):
        """The hiding arm is deferred: the flush passes and COMMIT refuses."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer)
        _db.session.commit()
        _hide_transfer(xfer.id)
        with refused_by_database_rule(_HIDING_REFUSED):
            _db.session.commit()
        _db.session.rollback()

    def test_taking_the_record_off_and_hiding_in_one_save_commits(
        self, app, seed_user,
    ):
        """CONTROL: the delete door's own shape -- money off, transfer hidden, one save."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer)
        _db.session.commit()
        _hide_transfer(xfer.id)
        _db.session.execute(
            text("DELETE FROM budget.transaction_entries "
                 "WHERE expense_transfer_id = :t"),
            {"t": xfer.id},
        )
        _db.session.commit()
        assert _db.session.get(Transfer, xfer.id).is_deleted

    def test_an_empty_transfer_hides(self, app, seed_user):
        """CONTROL: a transfer holding nothing hides as it always did."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _hide_transfer(xfer.id)
        _db.session.commit()
        assert _db.session.get(Transfer, xfer.id).is_deleted
