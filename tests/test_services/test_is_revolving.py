"""
Shekel Budget App -- ``account_projection.is_revolving`` (plan step credit_card:CC-1)

The ONE predicate every credit-card feature gates on (design
``docs/design/credit_card_from_scratch.md`` 3.1, ruling ``credit_card:R-CC14``):
a schema flag on the account's type, read through one function, and NOT a
projection kind -- ``classify_account`` never sees it, so a card stays PLAIN and
the balance seam dispatches nothing on it.
"""

from decimal import Decimal

from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    is_revolving,
)
from tests._test_helpers import create_account_of_type, create_loan_account


class TestIsRevolving:
    """The predicate answers off the type's flag and nothing else."""

    def test_a_credit_card_is_revolving(self, app, db, seed_user, seed_periods_today):
        """An account of the seeded ``Credit Card`` type answers True."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            assert is_revolving(card) is True

    def test_checking_and_a_loan_are_not(self, app, db, seed_user, seed_periods_today):
        """The seeded grid account (Checking) and a configured Mortgage answer False.

        Two non-cards on purpose: a PLAIN asset and an AMORTIZING liability.
        The predicate is not "is a liability" and not "is not amortizing" --
        both readings would admit an account that is not a card.
        """
        with app.app_context():
            checking = seed_user["account"]
            mortgage = create_loan_account(
                seed_user, db.session, name="House",
                principal=Decimal("200000.00"), rate=Decimal("0.06000"),
                term=360,
            )
            assert is_revolving(checking) is False
            assert is_revolving(mortgage) is False

    def test_an_account_with_no_type_is_not_revolving(self, app):
        """A degenerate, partially loaded account answers False, never raises.

        The same contract ``classify_account`` keeps (it answers PLAIN): a
        half-loaded row must not be mistaken for a card, and must not take down
        the reader that asked.
        """
        with app.app_context():
            bare = Account(name="unloaded")
            assert bare.account_type is None
            assert is_revolving(bare) is False


class TestTheFlagIsNotAKind:
    """``classify_account`` is untouched by the flag (design 3.1: a card is PLAIN)."""

    def test_a_credit_card_classifies_plain(self, app, db, seed_user, seed_periods_today):
        """A revolving account is PLAIN to the classifier.

        The 2026-07-19 plan's sixth kind (``REVOLVING``) existed to send the
        card to a fold of its own; ruling R-CC14 dissolved that fold, so the
        kind enum stays five-valued and the card's balance is the cash fold
        every non-loan account already rides.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            assert is_revolving(card) is True
            assert classify_account(card) is AccountProjectionKind.PLAIN
            assert len(AccountProjectionKind) == 5
