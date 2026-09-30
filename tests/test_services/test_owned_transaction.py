"""Plan step ``balance:X-bn``: the one service-level load of an owned transaction.

:func:`app.services.owned_transaction.load_owned_transaction` is what five
doors ask before they act on a row by id (Mark Credit and its undo, the
purchase door, the purchase list and the payback sync); each of those doors
also grades its own foreign-owner and missing-id refusal in its own module.
Graded here is the rule the loader itself states: one answer for "not found"
and "not yours", and a deleted row handed back for its caller to refuse.
"""

import pytest

from app.exceptions import NotFoundError
from app.extensions import db as _db
from app.services.owned_transaction import load_owned_transaction
from tests._test_helpers import generate_row_of, make_expense_template


def _hotel(seed_user):
    """A recurring $120.00 Hotel occurrence, committed; returns its id."""
    template = make_expense_template(
        _db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent",
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    return row.id


class TestLoadOwnedTransaction:
    """The owner's row comes back; another user's and a missing one get one identical refusal."""

    def test_the_owners_row_is_returned(self, app, db, seed_user):
        """The seed user's $120.00 Hotel loads for the seed user."""
        with app.app_context():
            row_id = _hotel(seed_user)
            txn = load_owned_transaction(row_id, seed_user["user"].id)
            assert txn.id == row_id

    def test_another_users_row_reads_as_not_found(
        self, app, db, seed_user, second_user,
    ):
        """The seed user's Hotel, asked for by the second user: the same words as a missing id."""
        with app.app_context():
            row_id = _hotel(seed_user)
            with pytest.raises(NotFoundError) as foreign:
                load_owned_transaction(row_id, second_user["user"].id)
            missing_id = row_id + 100_000
            with pytest.raises(NotFoundError) as missing:
                load_owned_transaction(missing_id, second_user["user"].id)
            assert str(foreign.value) == f"Transaction {row_id} not found."
            assert str(missing.value) == f"Transaction {missing_id} not found."

    def test_a_deleted_row_is_returned_for_its_caller_to_refuse(
        self, app, db, seed_user,
    ):
        """A hidden Hotel still loads for its owner: which door refuses it, and how, is the door's."""
        with app.app_context():
            row_id = _hotel(seed_user)
            txn = load_owned_transaction(row_id, seed_user["user"].id)
            txn.is_deleted = True
            _db.session.commit()
            _db.session.expire_all()
            assert load_owned_transaction(
                row_id, seed_user["user"].id,
            ).is_deleted is True
