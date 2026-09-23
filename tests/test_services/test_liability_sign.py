"""Tests for :mod:`app.services.liability_sign` -- the one flip and the door crossing.

What is pinned, every expectation hand-computed:

* **the flip** (ruling R-CC29, moved here at plan step credit_card:CC-5-5b):
  what an account owes is minus the balance it holds -- a debt is a positive
  owed figure, a credit stays negative, and zero carries no sign.  The three
  tests were ``test_card_statement.TestTheSignIsFixedOnce`` (5a's review L4);
  their assertions are byte-unchanged;
* **the door crossing** (rulings R-CC52 / R-CC57): a LIABILITY's door speaks
  the amount owed and stores the held sign -- typing ``5,000.00`` on a car loan
  stores ``-5,000.00`` (R-CC52's worked case), and a card holding a ``+50.00``
  credit pre-fills ``-50.00``;
* **the asset arm passes through unchanged**, for EVERY non-liability
  category and for a type with no category, so a refactor that flipped an
  asset's door fails here rather than on a screen (the coordinator's note on
  the 5b announce);
* the crossing is its own inverse in both directions.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.services.liability_sign import entered_figure, held_balance, owed

#: Every category that is NOT a liability -- each must pass through the door.
_NON_LIABILITY = [
    member for member in AcctCategoryEnum
    if member is not AcctCategoryEnum.LIABILITY
]


def _type_in(category: AcctCategoryEnum):
    """Build a stand-in account TYPE sitting in *category*."""
    return SimpleNamespace(category_id=ref_cache.acct_category_id(category))


class TestTheSignIsFixedOnce:
    """Ruling R-CC29: the fold's negative-when-owed becomes an owed figure."""

    def test_a_debt_is_a_positive_owed_figure(self):
        """The seam's -1,234.56 is 1,234.56 owed."""
        assert owed(Decimal("-1234.56")) == Decimal("1234.56")

    def test_a_credit_stays_negative(self):
        """A fold of +50.00 (the issuer owes the owner) is -50.00 owed."""
        assert owed(Decimal("50.00")) == Decimal("-50.00")

    def test_zero_carries_no_sign(self):
        """A zero fold is an UNSIGNED 0.00 owed -- ``-0.00`` would print as
        money owed on a screen -- and still two places."""
        result = owed(Decimal("0.00"))
        assert result == Decimal("0.00")
        assert result.is_signed() is False
        assert result.as_tuple().exponent == -2


class TestALiabilityDoorSpeaksOwed:
    """R-CC52: a liability's door asks the amount OWED and stores held."""

    def test_a_card_owing_1000_prefills_1000(self, app, db, seed_user):
        """Held -1,000.00 (the card owes 1,000.00) shows as 1,000.00."""
        with app.app_context():
            liability = _type_in(AcctCategoryEnum.LIABILITY)
            assert entered_figure(liability, Decimal("-1000.00")) == Decimal(
                "1000.00",
            )

    def test_a_card_holding_a_credit_prefills_negative(
        self, app, db, seed_user,
    ):
        """Held +50.00 (the issuer owes the owner) shows as -50.00 owed."""
        with app.app_context():
            liability = _type_in(AcctCategoryEnum.LIABILITY)
            assert entered_figure(liability, Decimal("50.00")) == Decimal(
                "-50.00",
            )

    def test_typing_5000_on_a_car_loan_stores_minus_5000(
        self, app, db, seed_user,
    ):
        """R-CC52's own worked case: 5,000.00 typed stores -5,000.00 held."""
        with app.app_context():
            liability = _type_in(AcctCategoryEnum.LIABILITY)
            assert held_balance(liability, Decimal("5000.00")) == Decimal(
                "-5000.00",
            )

    def test_a_credit_typed_as_negative_owed_stores_a_positive_hold(
        self, app, db, seed_user,
    ):
        """-50.00 typed as owed is a +50.00 credit held."""
        with app.app_context():
            liability = _type_in(AcctCategoryEnum.LIABILITY)
            assert held_balance(liability, Decimal("-50.00")) == Decimal(
                "50.00",
            )

    def test_zero_typed_on_a_liability_stores_an_unsigned_zero(
        self, app, db, seed_user,
    ):
        """0.00 typed stores an unsigned 0.00, never ``-0.00``."""
        with app.app_context():
            liability = _type_in(AcctCategoryEnum.LIABILITY)
            stored = held_balance(liability, Decimal("0.00"))
            assert stored == Decimal("0.00")
            assert stored.is_signed() is False


class TestAnAssetDoorPassesThrough:
    """Every non-liability door stores and shows the balance unchanged."""

    @pytest.mark.parametrize("category", _NON_LIABILITY)
    def test_every_non_liability_category_is_unchanged_both_ways(
        self, app, db, seed_user, category,
    ):
        """-1,000.00 and +2,500.00 cross unchanged, in both directions."""
        with app.app_context():
            acct_type = _type_in(category)
            for figure in (Decimal("-1000.00"), Decimal("2500.00")):
                assert entered_figure(acct_type, figure) == figure
                assert held_balance(acct_type, figure) == figure

    def test_a_type_with_no_category_is_unchanged(self, app, db, seed_user):
        """No type at all is not a liability, so the figure is untouched."""
        with app.app_context():
            assert entered_figure(None, Decimal("-1000.00")) == Decimal(
                "-1000.00",
            )
            assert held_balance(None, Decimal("-1000.00")) == Decimal(
                "-1000.00",
            )


class TestTheCrossingIsItsOwnInverse:
    """What a door stores reads back as what was typed, for every category."""

    @pytest.mark.parametrize("category", list(AcctCategoryEnum))
    def test_typed_then_shown_is_what_was_typed(
        self, app, db, seed_user, category,
    ):
        """entered_figure(held_balance(x)) == x and the converse."""
        with app.app_context():
            acct_type = _type_in(category)
            for figure in (Decimal("1234.56"), Decimal("-50.00")):
                assert entered_figure(
                    acct_type, held_balance(acct_type, figure),
                ) == figure
                assert held_balance(
                    acct_type, entered_figure(acct_type, figure),
                ) == figure
