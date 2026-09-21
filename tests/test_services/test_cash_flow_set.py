"""
Shekel Budget App -- the cash-flow set: checking and its cards (plan step credit_card:CC-4-1)

The predicate developer ruling ``credit_card:R-CC16`` names -- the owner's
primary grid account plus their active revolving accounts, read as ONE SET by
every plan-item reader with the balance line still ONE account's -- and the
rule ruling ``credit_card:R-CC23`` adds to it: a transfer between two
members shows once, from the balance line's side.

Three subjects, each its own class: the resolver
(:func:`~app.services.account_resolver.resolve_cash_flow_set`), the ONE row
clause (:func:`~app.services.cash_flow_set.own_rows_clause`: every member's
own rows and no shadow, since leaf ``balance:X-bi-6-1b`` the only row clause
-- R-CC23's far leg is the LEG spelling's, ``leg_accounts_shown``, graded in
``test_transfer_legs.py``; the far-leg query the balance seam reads is graded
beside it here), and the orthogonal ``revolving`` filter the set is built on.
The seam's composed subtotal and its "On other accounts" term are graded in
``test_balance_at.py``, beside the identity oracle they extend; the grid's
render in ``test_routes/test_grid.py``.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import UserSettings
from app.services import account_service
from app.services.account_resolver import (
    resolve_cash_flow_set,
    resolve_grid_account,
)
from app.services.cash_flow_set import (
    CashFlowSet,
    FarLegs,
    far_legs_of,
    own_rows_clause,
)
from tests._test_helpers import (
    capture_sql_statements,
    create_account_of_type,
    create_loan_account,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    one_off_row_of,
)


def _card(seed_user, name="Rewards Card", **kwargs):
    """Create an active Credit Card account for *seed_user*."""
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"), **kwargs,
    )


def _settings(seed_user):
    """Return the owner's settings row."""
    return db.session.query(UserSettings).filter_by(
        user_id=seed_user["user"].id,
    ).one()


def _expense_row(seed_user, period, account, name, amount):
    """Place a projected one-off EXPENSE row on *account* in *period*."""
    return one_off_row_of(
        period, name=name, amount=Decimal(amount),
        user_id=seed_user["user"].id, account_id=account.id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )


def _loaded_ids(cash_flow, scenario_id):
    """Return the ids the own-rows clause loads for *cash_flow*."""
    return {
        row.id for row in
        db.session.query(Transaction)
        .filter(
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            own_rows_clause(cash_flow),
        )
        .all()
    }


def _shadow_on(transfer, account):
    """Return *transfer*'s live shadow row on *account*."""
    return (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer.id, account_id=account.id)
        .filter(Transaction.is_deleted.is_(False))
        .one()
    )


class TestResolveCashFlowSet:
    """The set is the primary plus the owner's active cards; the balance line is one member."""

    def test_an_owner_with_no_card_has_a_set_of_one(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The pre-card shape: the primary alone, and it is the balance line.

        Every owner before the credit-card arc's release, and the whole of
        production on 2026-09-18 -- which is why the grid of such an owner is
        byte-identical to the one before this step.
        """
        with app.app_context():
            cash_flow = resolve_cash_flow_set(seed_user["user"].id)
            checking = seed_user["account"]
            assert cash_flow.balance.id == checking.id
            assert cash_flow.member_ids == (checking.id,)
            assert cash_flow.others == ()

    def test_active_cards_join_the_set_after_the_primary_in_picker_order(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Two cards, ordered by ``(sort_order, id)``; an inactive one and a loan stay out."""
        with app.app_context():
            checking = seed_user["account"]
            second = _card(seed_user, "Second Card")
            first = _card(seed_user, "First Card")
            first.sort_order = -1
            closed = _card(seed_user, "Closed Card")
            closed.is_active = False
            create_loan_account(
                seed_user, db.session, name="Van Loan",
                principal=Decimal("15000.00"), rate=Decimal("0.05000"),
                term=60,
            )
            db.session.commit()

            cash_flow = resolve_cash_flow_set(seed_user["user"].id)

            assert cash_flow.balance.id == checking.id
            assert cash_flow.member_ids == (checking.id, first.id, second.id)
            assert [a.id for a in cash_flow.others] == [first.id, second.id]

    def test_another_owners_card_is_not_a_member(
        self, app, db, seed_user, seed_periods_today, bare_user, bare_periods,
    ):  # pylint: disable=unused-argument
        """The set is owner-scoped: a card the other user holds never joins it."""
        with app.app_context():
            other = dict(seed_user)
            other["user"] = bare_user["user"]
            mine = _card(seed_user, "My Card")
            theirs = create_account_of_type(
                other, db.session, "Credit Card", "Their Card",
                anchor_balance=Decimal("-10.00"),
            )
            db.session.commit()

            cash_flow = resolve_cash_flow_set(seed_user["user"].id)

            assert mine.id in cash_flow.member_ids
            assert theirs.id not in cash_flow.member_ids

    def test_the_primary_is_the_grid_resolvers_no_override_answer(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """``default_grid_account_id`` names the primary, as it names the grid account.

        ONE definition of the primary for every reader: the set's first
        member is exactly ``resolve_grid_account(user_id, settings)``.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            card = _card(seed_user)
            settings = _settings(seed_user)
            settings.default_grid_account_id = savings.id
            db.session.commit()

            cash_flow = resolve_cash_flow_set(seed_user["user"].id, settings)

            assert cash_flow.balance.id == savings.id
            assert cash_flow.balance.id == resolve_grid_account(
                seed_user["user"].id, settings,
            ).id
            assert cash_flow.member_ids == (savings.id, card.id)

    def test_an_override_naming_a_member_moves_the_balance_line_only(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """``?account_id=<card>``: the card's balance, the same paycheck's rows."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            db.session.commit()

            cash_flow = resolve_cash_flow_set(
                seed_user["user"].id, None, card.id,
            )

            assert cash_flow.balance.id == card.id
            assert cash_flow.member_ids == (checking.id, card.id)
            assert [a.id for a in cash_flow.others] == [checking.id]

    def test_an_override_outside_the_set_is_that_accounts_single_account_view(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """``?account_id=<savings>`` keeps the savings grid exactly as it was.

        The set collapses to that one account: no card row on the savings
        grid, no "On other accounts" term, no chip.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            _card(seed_user)
            db.session.commit()

            cash_flow = resolve_cash_flow_set(
                seed_user["user"].id, None, savings.id,
            )

            assert cash_flow.balance.id == savings.id
            assert cash_flow.member_ids == (savings.id,)

    def test_a_refused_override_falls_through_to_the_primary(
        self, app, db, seed_user, seed_periods_today, bare_user, bare_periods,
    ):  # pylint: disable=unused-argument
        """Another owner's account, an archived one, a loan: the primary's set, as the grid always did."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            other = dict(seed_user)
            other["user"] = bare_user["user"]
            theirs = create_account_of_type(
                other, db.session, "Checking", "Their Checking",
                anchor_balance=Decimal("10.00"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Van Loan",
                principal=Decimal("15000.00"), rate=Decimal("0.05000"),
                term=60,
            )
            db.session.commit()

            for refused in (theirs.id, loan.id, 999_999):
                cash_flow = resolve_cash_flow_set(
                    seed_user["user"].id, None, refused,
                )
                assert cash_flow.balance.id == checking.id, refused
                assert cash_flow.member_ids == (checking.id, card.id), refused

    def test_an_owner_whose_only_account_is_a_card_gets_a_set_of_that_card(
        self, app, db, bare_user, bare_periods,
    ):  # pylint: disable=unused-argument
        """The primary can itself be revolving (the resolver's step 4); it is not listed twice."""
        with app.app_context():
            card = create_account_of_type(
                bare_user, db.session, "Credit Card", "Only Card",
                anchor_balance=Decimal("-1.00"),
            )
            db.session.commit()

            cash_flow = resolve_cash_flow_set(bare_user["user"].id)

            assert cash_flow.balance.id == card.id
            assert cash_flow.member_ids == (card.id,)

    def test_no_eligible_account_answers_none(self, app, db, bare_user):
        """The zero-accounts owner: ``None``, the state the grid renders empty for."""
        with app.app_context():
            assert resolve_cash_flow_set(bare_user["user"].id) is None

    def test_the_value_refuses_a_balance_line_outside_its_members(self, app):
        """The type's one invariant is checked at construction, not trusted."""
        with app.app_context():
            balance = Account(id=1, name="a")
            member = Account(id=2, name="b")
            with pytest.raises(ValueError, match="must be a member"):
                CashFlowSet(balance=balance, members=(member,))


class TestOwnRowsClause:
    """The ONE clause every plan-item reader appends for its ROWS (R-CC16).

    Every member's own rows and no shadow.  It was ``paycheck_rows_clause``
    -- the same member filter, keeping the NEAR shadow of a transfer as a row
    and dropping the far one (R-CC23) -- until leaf ``balance:X-bi-6-1b``
    left that clause with no reader: every reader draws a transfer as a LEG
    of its parent now, and R-CC23 is the leg spelling's
    (``leg_accounts_shown``, graded case for case in ``test_transfer_legs.py``:
    both endpoints members from either balance line, one endpoint in the set,
    both in and the line on neither).  What is left to grade here is the row
    half, and that no shadow is a row on either side.
    """

    def test_every_members_rows_load_and_an_outsiders_do_not(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """A card bill is a paycheck row; a savings row is not."""
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            period = seed_periods_today[3]
            grocery = _expense_row(seed_user, period, checking, "Grocery", "500.00")
            phone = _expense_row(seed_user, period, card, "Phone", "45.00")
            goal = _expense_row(seed_user, period, savings, "Goal", "20.00")
            db.session.commit()

            loaded = _loaded_ids(
                resolve_cash_flow_set(seed_user["user"].id),
                seed_user["scenario"].id,
            )

            assert {grocery.id, phone.id} <= loaded
            assert goal.id not in loaded

    def test_a_one_member_set_is_the_old_single_account_filter(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """No card: exactly ``account_id == checking`` over the own rows."""
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods_today[3]
            grocery = _expense_row(seed_user, period, checking, "Grocery", "500.00")
            db.session.commit()
            cash_flow = resolve_cash_flow_set(seed_user["user"].id)
            assert cash_flow.member_ids == (checking.id,)

            loaded = _loaded_ids(cash_flow, seed_user["scenario"].id)
            expected = {
                row.id for row in
                db.session.query(Transaction).filter(
                    Transaction.scenario_id == seed_user["scenario"].id,
                    Transaction.is_deleted.is_(False),
                    Transaction.account_id == checking.id,
                    Transaction.transfer_id.is_(None),
                ).all()
            }

            assert grocery.id in loaded
            assert loaded == expected

    def test_no_shadow_is_a_row_on_either_side(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """A transfer's two shadows are not rows on either member; the card bill beside them is.

        Both projected and settled.  The clause it replaced kept the checking
        shadow here (the near leg) and dropped the card's; the leg the set
        draws for the same transfer is ``leg_accounts_shown``'s, graded in
        ``test_transfer_legs.py``.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            payment = create_transfer(
                seed_user, db.session, checking, card, seed_periods_today[3],
                amount=Decimal("165.00"),
            )
            paid = create_settled_transfer(
                seed_user, db.session, checking, card, seed_periods_today[1],
                amount=Decimal("80.00"),
            )
            phone = _expense_row(
                seed_user, seed_periods_today[3], card, "Phone", "45.00",
            )
            db.session.commit()

            loaded = _loaded_ids(
                resolve_cash_flow_set(seed_user["user"].id),
                seed_user["scenario"].id,
            )

            assert phone.id in loaded
            for transfer in (payment, paid):
                assert _shadow_on(transfer, checking).id not in loaded
                assert _shadow_on(transfer, card).id not in loaded


class TestFarLegsOf:
    """The seam's reading of the same rule: both identities, no projected shadow read, none for a set of one."""

    def test_answers_the_settled_shadow_ids_and_every_intra_set_transfer_id(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The planned half is every intra-set transfer (off ``budget.transfers``);
        the settled half is the SETTLED far-leg shadows only.

        Transfer Invariant 5 admits a balance reader to read a settled leg's
        shadow record and forbids it a projected one: the projected payment's
        card shadow is therefore NOT in ``transaction_ids`` (its plan leg is
        excluded by ``transfer_ids``), and the paid payment's card shadow IS.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            payment = create_transfer(
                seed_user, db.session, checking, card, seed_periods_today[3],
                amount=Decimal("165.00"),
            )
            paid = create_settled_transfer(
                seed_user, db.session, checking, card, seed_periods_today[1],
                amount=Decimal("80.00"),
            )
            # A transfer that is NOT intra-set never appears, whichever side.
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            create_transfer(
                seed_user, db.session, savings, card, seed_periods_today[3],
                amount=Decimal("300.00"),
            )
            db.session.commit()

            far = far_legs_of(resolve_cash_flow_set(seed_user["user"].id))

            assert far.transaction_ids == {_shadow_on(paid, card).id}
            assert _shadow_on(payment, card).id not in far.transaction_ids
            assert far.transfer_ids == {payment.id, paid.id}

    def test_reads_no_projected_shadow_row(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Invariant 5 by statement census: the settled-status filter is on every shadow read.

        Every ``budget.transactions`` SELECT the producer issues carries the
        settled-status ``IN`` predicate; the transfer read touches
        ``budget.transfers`` alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = _card(seed_user)
            create_transfer(
                seed_user, db.session, checking, card, seed_periods_today[3],
                amount=Decimal("165.00"),
            )
            db.session.commit()
            cash_flow = resolve_cash_flow_set(seed_user["user"].id)

            _far, statements = capture_sql_statements(
                lambda: far_legs_of(cash_flow),
            )

            transaction_reads = [
                text for text, _params in statements
                if "budget.transactions" in text
            ]
            assert transaction_reads, "the settled half issued no read"
            for text in transaction_reads:
                assert "status_id IN" in text, text

    def test_a_set_of_one_issues_no_query(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            cash_flow = resolve_cash_flow_set(seed_user["user"].id)
            assert cash_flow.others == ()
            far, statements = capture_sql_statements(
                lambda: far_legs_of(cash_flow),
            )
            assert far == FarLegs.none()
            assert not statements


class TestRevolvingFilter:
    """``active_accounts_query``'s orthogonal ``revolving`` value (design 3.8)."""

    def test_true_false_and_none_partition_the_active_accounts(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            card = _card(seed_user)
            closed = _card(seed_user, "Closed Card")
            closed.is_active = False
            db.session.commit()

            def ids(**kwargs):
                return {
                    a.id for a in account_service.active_accounts_query(
                        user_id, amortizing=False, **kwargs,
                    ).all()
                }

            assert ids(revolving=True) == {card.id}
            assert ids(revolving=False) == {checking.id}
            assert ids() == ids(revolving=None) == {checking.id, card.id}
