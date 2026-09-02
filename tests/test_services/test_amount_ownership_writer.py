"""The ONE writer of a row's amount-ownership pair.

Plan step **X-au-g-2c-2**.  ``app.services.amount_ownership`` is the write half
of ruling **R-FI**: a row either STATES ITS OWN figure or DECLARES the relation
that prices it, and the two columns that say which move together or not at all.
:mod:`app.services.cash_ledger._amount_source` is the read half, graded in
``tests/test_services/test_amount_source.py``; the schema's own refusals are
graded in ``tests/test_models/test_amount_ownership.py``.

**What is asserted here is that the SEAM cannot express the half-write**, not
that the CHECK refuses it -- the constraint's tests already own that.  The
difference matters: a constraint catches a mistake at the flush, and this seam
is what stops the mistake being writable at all.
"""

from decimal import Decimal

import pytest

from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.amount_ownership import (
    _FIGURE_COLUMNS,
    declare_derived,
    owns_its_amount,
    state_own_amount,
)
from app import ref_cache
from tests._test_helpers import add_txn, create_transfer


class TestTheRegistryIsComplete:
    """Every table carrying ``amount_source_id`` has a figure column here."""

    def test_it_covers_exactly_the_models_that_carry_the_column(self, app):
        """The completeness predicate, discovered rather than restated.

        The mapping is keyed on the model, so a THIRD table brought under the
        amount model raises ``KeyError`` at the lookup instead of being written
        through some default -- but only if somebody notices.  This is what
        notices: the expected set is derived from the MAPPERS, so a table that
        gains ``amount_source_id`` without a figure column beside it fails
        here rather than at the first row somebody tries to declare.

        The same shape as ``_RULE_ANSWERS`` / ``_RELATION_RULES`` one tier up,
        and for the same reason.
        """
        with app.app_context():
            carrying = {
                mapper.class_ for mapper in db.Model.registry.mappers
                if "amount_source_id" in mapper.columns
            }

            assert carrying == set(_FIGURE_COLUMNS)

    def test_each_registered_column_exists_on_its_model(self, app):
        """A registered column NAME must be a real column on that table.

        Without this the registry could name a column that does not exist --
        a near-miss spelling, or one renamed by a migration -- and every write
        would create an ORM attribute nobody reads.  ``setattr`` on a mapped
        class does not fail, and neither does the ``commit`` after it: a
        SQLAlchemy declarative class has no ``__slots__``, so the value simply
        goes nowhere.  That is the same silent shape an adversarial review of
        this step found twice in the fixtures, where a test wrote
        ``EscrowLine.annual_amount`` -- a column that lives on the VERSION --
        and an assertion passed because nothing had changed.
        """
        with app.app_context():
            for model, column in _FIGURE_COLUMNS.items():
                assert column in model.__mapper__.columns, (
                    f"{model.__name__} has no column {column!r}"
                )

    def test_an_unregistered_model_raises_rather_than_guessing(self, app):
        """A model with no figure column fails loudly at the seam.

        The negative control on the registry: a lookup that fell back to a
        default would write a declaration onto a table whose CHECK does not
        pair it with anything.
        """
        with app.app_context():
            class _NotAmountOwned:  # pylint: disable=too-few-public-methods
                amount_source_id = None

            with pytest.raises(KeyError):
                declare_derived(
                    _NotAmountOwned(), AmountSourceEnum.PARENT_TRANSFER,
                )


class TestTheTwoActs:
    """Each act writes BOTH columns, so neither state can be half-written."""

    def test_declaring_a_transaction_empties_its_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """``declare_derived`` sets the source and clears ``estimated_amount``.

        Flushed, because a pair written only in memory would satisfy this
        assertion while the CHECK refused it -- and the CHECK is what the seam
        exists to keep satisfiable.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Gas", "60.00",
            )
            db.session.flush()

            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()

            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert owns_its_amount(txn) is False

    def test_stating_an_own_amount_clears_the_declaration(
        self, app, db, seed_user, seed_periods,
    ):
        """``state_own_amount`` writes the figure and clears the source.

        The other direction, and the one the transfer door takes when a human
        authors a figure (ruling **R-IO**).
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Gas", "60.00",
            )
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()

            state_own_amount(txn, Decimal("42.50"))
            db.session.flush()

            assert txn.estimated_amount == Decimal("42.50")
            assert txn.amount_source_id is None
            assert owns_its_amount(txn) is True

    def test_a_transfers_figure_column_is_its_own(
        self, app, db, seed_full_user_data,
    ):
        """The SECOND table is written through the same two functions.

        ``budget.transfers`` stores an owned figure in ``amount`` where a
        transaction uses ``estimated_amount``, and the registry is what keeps
        one seam serving both.  Without this case the seam could hard-code the
        transaction column and every transfer write would silently create an
        attribute.
        """
        with app.app_context():
            td = seed_full_user_data
            xfer = create_transfer(
                td, db.session, td["account"], td["savings_account"],
                td["periods"][0], amount=Decimal("250.00"),
            )
            # ``ck_transfers_adhoc_owns_amount`` refuses a declaration on a
            # transfer no definition prices, so this has to be a GENERATED one
            # to be a legal row at all.
            xfer.transfer_template_id = td["transfer_template"].id
            db.session.flush()

            declare_derived(xfer, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            assert xfer.amount is None
            assert owns_its_amount(xfer) is False

            state_own_amount(xfer, Decimal("300.00"))
            db.session.flush()
            assert xfer.amount == Decimal("300.00")
            assert xfer.amount_source_id is None

    def test_both_acts_are_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-declaring a derived row and re-stating an owned one are no-ops.

        Load-bearing rather than incidental: ``_update._apply_amount``
        re-declares BOTH legs on every definition-driven amount write, so the
        ordinary case -- a leg that is already derived -- must cost nothing and
        change nothing.  A version bump is not asserted either way; what is
        asserted is that the pair does not move.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Gas", "60.00",
            )
            db.session.flush()

            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

            state_own_amount(txn, Decimal("42.50"))
            db.session.flush()
            state_own_amount(txn, Decimal("42.50"))
            db.session.flush()
            assert txn.estimated_amount == Decimal("42.50")
            assert txn.amount_source_id is None

    def test_a_declaration_can_be_switched_between_relations(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-declaring under a DIFFERENT relation replaces the first.

        The state a mode change would need if a relation ever moved.  It is
        asserted because ``declare_derived`` writes the source unconditionally
        rather than only when it is absent, and a reader would be entitled to
        assume the opposite from the idempotence above.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Gas", "60.00",
            )
            db.session.flush()

            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            declare_derived(txn, AmountSourceEnum.PARENT_TRANSFER)
            db.session.flush()

            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.PARENT_TRANSFER,
            )
