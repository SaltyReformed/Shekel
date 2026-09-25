"""
Shekel Budget App -- The reconcile panel and carry-forward walk TRANSFERS (leaf X-bi-6-4c-2)

Plan step ``balance:X-bi-6-4c-2`` moved three readers off a transfer's SHADOW
rows: the reconcile panel offers the transfer's LEG on the statement's account
(``transfer_legs.offerable_transfer_legs``), carry-forward walks the source
period's transfers, and the recurrence engine's records predicate asks
``transfer_legs.transfers_holding_records``.  These tests pin what that leaf
changed and what it declared:

* a leg is keyed by ``(transfer id, account id)`` (ruling **R-BAL87**) and
  ticked under its own form fields (ruling **R-BAL145**), so a transfer whose
  id equals a bill's id is offered and settled beside it rather than
  overwriting it;
* the loader's scope, and its two drift directions (the PARENT decides);
* the transfer block's heading is the leg's label from the endpoints' current
  names (declared), and a transfer whose shadow pair is broken is named in a
  warning and not offered while the rest is listed (ruling **R-BAL148**);
* the records predicate kept its body, dead shadows included;
* a carry-forward transfer plan is labelled by its FROM side, in transfer-id
  order (finding **BAL-546**, declared).
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_service,
    carry_forward_service,
    cash_ledger,
    reconcile_service,
    transfer_legs,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    cover_bare_settled_row,
    create_transfer,
    generate_row_of,
    make_expense_template,
    open_books_before_the_first_assertion,
    settle_day_columns,
)

#: The civil day the panel's statement is presented for; every period-0 row
#: here lands on or before it.
_OBSERVED_ON = date(2026, 1, 10)


def _reconciled(seed_user, account_id=None):
    """Return the Statement for *account_id* (the seed account by default).

    The account's REAL governing assertion presented for :data:`_OBSERVED_ON`:
    the id must exist (the clearing link's key refuses one that does not); the
    day is the test's.
    """
    account = seed_user["account"].id if account_id is None else account_id
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account,
        replace(
            cash_ledger.governing_anchor(account), observed_on=_OBSERVED_ON,
        ),
    )


def _savings(seed_user, name="Savings"):
    """Create a second cash account whose books open before any day used here."""
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name=name,
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    db.session.flush()
    open_books_before_the_first_assertion(db.session, account)
    return account


def _shadow_on(transfer, account):
    """Return *transfer*'s shadow row on *account* (a test may read shadows)."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == transfer.id,
            Transaction.account_id == account.id,
        )
        .one()
    )


def _settle(seed_user, *, transaction_ids=(), transfer_ids=(),
            corrections=None, transfer_corrections=None):
    """Run the write union with each arm's ticks in its own field."""
    return reconcile_service.record_reconciliation(
        reconcile_service.ReconcileSubmission(
            statement=_reconciled(seed_user),
            entry_ids=set(),
            transaction_ids=set(transaction_ids),
            corrections=corrections or {},
            transfer_ids=set(transfer_ids),
            transfer_corrections=transfer_corrections or {},
        ),
    )


def _bill(seed_user, period, name="Electricity"):
    """Create one projected bill in *period*, due on its payday."""
    template = make_expense_template(
        db.session, seed_user, amount="180.00",
        name=name, category_key="Groceries",
    )
    return generate_row_of(template, period)


def _transfer_numbered_like(seed_user, period, row_id, to_account):
    """Create a transfer out of the seed account whose ID equals *row_id*.

    The transfers sequence is set so its next value is *row_id* -- a state
    production reaches on its own whenever the two tables' sequences cross,
    and the one the old single ``transaction_ids`` field could not tell apart.
    """
    db.session.execute(
        text("SELECT setval('budget.transfers_id_seq', :v, false)"),
        {"v": row_id},
    )
    transfer = create_transfer(
        seed_user, db.session, seed_user["account"], to_account, period,
        amount=Decimal("75.00"),
    )
    db.session.commit()
    assert transfer.id == row_id, "the collision was not built"
    return transfer


class TestALegIsKeyedApartFromARow:
    """A transfer id equal to a bill's id is two offers, not one.

    Before this leaf the transfer arm offered the SHADOW row, keyed by the
    shadow's id in one map with the bills, and ticks for both rode one
    ``transaction_ids`` field; keying a leg by its transfer id in that map
    would have silently overwritten the bill's offer.  The leg's key is a pair
    and its tick has its own field (ruling **R-BAL145**).
    """

    def test_a_transfer_whose_id_equals_a_bills_id_is_offered_beside_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Both blocks are published, each under its own key and kind."""
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            db.session.commit()
            transfer = _transfer_numbered_like(
                seed_user, seed_periods[0], bill.id, _savings(seed_user),
            )

            groups = reconcile_service.outstanding_set(
                _reconciled(seed_user),
            ).groups
            by_key = {group.key: group for group in groups}

            assert by_key[bill.id].kind is reconcile_service.OfferKind.BILL
            leg_key = (transfer.id, seed_user["account"].id)
            assert by_key[leg_key].kind is (
                reconcile_service.OfferKind.TRANSFER
            )
            # What each tick POSTS: the same number under two fields.
            assert by_key[bill.id].settle.tick_form.ids_field == (
                "transaction_ids"
            )
            assert by_key[bill.id].settle.amount_field == (
                f"settled_amount-{bill.id}"
            )
            assert by_key[leg_key].settle.tick_form.ids_field == "transfer_ids"
            assert by_key[leg_key].settle.tick_id == transfer.id
            assert by_key[leg_key].settle.amount_field == (
                f"transfer_amount-{transfer.id}"
            )

    def test_each_tick_settles_only_its_own_arms_item(
        self, app, db, seed_user, seed_periods,
    ):
        """The bill's tick leaves the transfer Projected, and the reverse."""
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            db.session.commit()
            transfer = _transfer_numbered_like(
                seed_user, seed_periods[0], bill.id, _savings(seed_user),
            )
            projected = ref_cache.status_id(StatusEnum.PROJECTED)

            assert _settle(seed_user, transaction_ids=[bill.id]) == 1
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transaction, bill.id).status_id != projected
            assert db.session.get(Transfer, transfer.id).status_id == projected

            assert _settle(seed_user, transfer_ids=[transfer.id]) == 1
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).status_id != projected


class TestTheOfferableLegLoader:
    """``transfer_legs.offerable_transfer_legs``: its scope, and who decides."""

    def test_either_side_is_offered_on_its_own_account(
        self, app, db, seed_user, seed_periods,
    ):
        """The from-side on the source account, the to-side on the destination."""
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            period_ids = {seed_periods[0].id}
            owner = seed_user["user"].id

            (out_leg,) = transfer_legs.offerable_transfer_legs(
                seed_user["account"].id, owner, period_ids, options=(),
            )
            (in_leg,) = transfer_legs.offerable_transfer_legs(
                savings.id, owner, period_ids, options=(),
            )

            assert (out_leg.transfer.id, out_leg.is_income) == (
                transfer.id, False,
            )
            assert (in_leg.transfer.id, in_leg.is_income) == (transfer.id, True)

    def test_the_owner_the_window_and_the_narrowing_each_exclude(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Another owner, a period outside the window, an unnamed transfer."""
        with app.app_context():
            savings = _savings(seed_user)
            inside = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            later = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            db.session.commit()
            account = seed_user["account"].id

            def offered(owner, period_ids, transfer_ids=None):
                return {
                    leg.transfer.id
                    for leg in transfer_legs.offerable_transfer_legs(
                        account, owner, period_ids, options=(),
                        transfer_ids=transfer_ids,
                    )
                }

            owner = seed_user["user"].id
            both = {seed_periods[0].id, seed_periods[1].id}
            assert offered(owner, both) == {inside.id, later.id}
            assert offered(owner, {seed_periods[0].id}) == {inside.id}
            assert offered(owner, set()) == set()
            assert offered(owner, both, transfer_ids={later.id}) == {later.id}
            assert offered(seed_second_user["user"].id, both) == set()

    def test_a_parent_that_is_not_projected_is_not_offered_whatever_its_shadow_says(
        self, app, db, seed_user, seed_periods,
    ):
        """Drift, parent side: a Paid parent over a Projected shadow.

        No door writes it (Transfer Invariants 3 and 4).  The shadow scope this
        replaced offered the shadow, and a tick then no-opped the settle and
        linked a Projected row; the parent decides now.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            db.session.execute(
                text("UPDATE budget.transfers SET status_id = :s WHERE id = :t"),
                {"s": ref_cache.status_id(StatusEnum.DONE), "t": transfer.id},
            )
            db.session.commit()
            db.session.expire_all()
            assert _shadow_on(transfer, seed_user["account"]).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            ), "the drift did not land: the shadow must stay Projected"

            assert transfer_legs.offerable_transfer_legs(
                seed_user["account"].id, seed_user["user"].id,
                {seed_periods[0].id}, options=(),
            ) == []

    def test_a_projected_parents_side_is_offered_whatever_its_shadow_says(
        self, app, db, seed_user, seed_periods,
    ):
        """Drift, shadow side: a Cancelled shadow under a Projected parent."""
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            shadow = _shadow_on(transfer, seed_user["account"])
            db.session.execute(
                text(
                    "UPDATE budget.transactions SET status_id = :s "
                    "WHERE id = :t",
                ),
                {
                    "s": ref_cache.status_id(StatusEnum.CANCELLED),
                    "t": shadow.id,
                },
            )
            db.session.commit()

            legs = transfer_legs.offerable_transfer_legs(
                seed_user["account"].id, seed_user["user"].id,
                {seed_periods[0].id}, options=(),
            )
            assert [leg.transfer.id for leg in legs] == [transfer.id]

    def test_a_side_whose_own_movement_is_dated_is_not_offered_but_the_other_is(
        self, app, db, seed_user, seed_periods,
    ):
        """R-BAL79 per side: the checking side's money moved; savings' has not."""
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()
            shadow = _shadow_on(transfer, seed_user["account"])
            for column, value in settle_day_columns(
                seed_periods[0].start_date,
            ).items():
                setattr(shadow, column, value)
            shadow.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.flush()
            cover_bare_settled_row(db.session, shadow, Decimal("75.00"))
            db.session.commit()

            owner = seed_user["user"].id
            window = {seed_periods[0].id}
            assert transfer_legs.offerable_transfer_legs(
                seed_user["account"].id, owner, window, options=(),
            ) == []
            (leg,) = transfer_legs.offerable_transfer_legs(
                savings.id, owner, window, options=(),
            )
            assert leg.transfer.id == transfer.id


class TestTheBlockReadsTheLeg:
    """What the panel prints for a transfer, and what it refuses."""

    def test_the_heading_is_the_legs_label_from_the_CURRENT_account_name(
        self, app, db, seed_user, seed_periods,
    ):
        """DECLARED: a renamed endpoint re-labels the block; the shadow's copy does not.

        The block was headed by the shadow's stored ``name``, written when the
        transfer was created; it is the leg's label now, composed from the
        endpoints' current names -- the change leaf X-bi-6-1 made on the grid.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            savings.name = "Emergency Fund"
            db.session.commit()

            (block,) = [
                group for group in reconcile_service.outstanding_set(
                    _reconciled(seed_user),
                ).groups
                if group.key == (transfer.id, seed_user["account"].id)
            ]

            assert block.name == "Transfer to Emergency Fund"
            assert _shadow_on(transfer, seed_user["account"]).name == (
                "Transfer to Savings"
            ), "the control: the shadow's stored copy did not follow"

    def test_a_damaged_transfer_is_named_not_offered_and_the_rest_is_listed(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling R-BAL148: a broken shadow pair is WARNED about, never offered.

        Transfer Invariant 1 broken around the service (no door writes it;
        integrity check DC-12 reports it).  The leg price asks the verified
        pair and refuses; the panel catches that one refusal, lists the bill
        beside it as normal, and names the transfer by label, figure and day.
        As first built the refusal propagated -- a server error on the account
        page that builds this panel inline.  A tick of it still refuses at the
        settle, which the route renders as the panel's designed refusal.
        """
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.commit()
            _shadow_on(transfer, savings).is_deleted = True
            db.session.commit()

            offered = reconcile_service.outstanding_set(_reconciled(seed_user))

            assert {group.key for group in offered.groups} == {bill.id}
            (damaged,) = offered.damaged
            assert damaged.label == "Transfer to Savings"
            assert damaged.amount == Decimal("500.00")
            assert damaged.attributed_on == seed_periods[0].start_date
            assert offered.payment_count == 1, "the damaged transfer is counted"
            with pytest.raises(ValidationError):
                _settle(seed_user, transfer_ids=[transfer.id])

    def test_the_panel_prints_the_warning_and_no_tick_for_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Every door that builds the panel answers 200; the permanent ones warn.

        The true-up (whose response builds the prompt after its write), the
        cash detail page (which builds the panel inline), and the panel's own
        fragment -- each a server error while the pair's refusal propagated.
        With the damaged transfer ALL that is outstanding there is nothing to
        tick, so the true-up opens no modal (``prompt_fragment`` gates on
        ticks); the detail page and the fragment carry the warning.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.commit()
            _shadow_on(transfer, savings).is_deleted = True
            db.session.commit()
            account_id = seed_user["account"].id
            transfer_id = transfer.id

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": _OBSERVED_ON.isoformat(),
            },
        )
        assert response.status_code == 200, response.data
        assert b"reconcileModal" not in response.data
        warning = (
            "Transfer to Savings, $500.00, Jan 2 is damaged and can't be "
            "reconciled here."
        )
        page = auth_client.get(f"/accounts/{account_id}/details")
        assert page.status_code == 200, page.data
        assert warning in page.data.decode()
        response = auth_client.get(f"/accounts/{account_id}/reconcile")
        assert response.status_code == 200, response.data
        body = response.data.decode()
        assert warning in body, body
        assert f'name="transfer_ids" value="{transfer_id}"' not in body
        assert "has been matched to your bank" not in body


    def test_the_true_up_modal_carries_the_warning_beside_a_tickable_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With a bill to tick, the post-true-up modal opens and warns too."""
        with app.app_context():
            _bill(seed_user, seed_periods[0])
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.commit()
            _shadow_on(transfer, savings).is_deleted = True
            db.session.commit()
            account_id = seed_user["account"].id

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": _OBSERVED_ON.isoformat(),
            },
        )
        assert response.status_code == 200, response.data
        body = response.data.decode()
        assert "reconcileModal" in body
        assert (
            "Transfer to Savings, $500.00, Jan 2 is damaged and can't be "
            "reconciled here." in body
        )


class TestTheRecordsPredicateKeptItsBody:
    """``transfer_legs.transfers_holding_records`` asks every shadow, dead too."""

    def test_a_movement_under_a_DEAD_shadow_still_holds_the_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """The body moved unchanged: no live-shadow term (review LOW 15)."""
        with app.app_context():
            savings = _savings(seed_user)
            held = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            bare = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            shadow = _shadow_on(held, seed_user["account"])
            for column, value in settle_day_columns(
                seed_periods[0].start_date,
            ).items():
                setattr(shadow, column, value)
            shadow.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.flush()
            cover_bare_settled_row(db.session, shadow, Decimal("75.00"))
            shadow.is_deleted = True
            db.session.commit()

            assert transfer_legs.transfers_holding_records(
                [held.id, bare.id],
            ) == {held.id}
            assert transfer_legs.transfers_holding_records([]) == set()


class TestCarryForwardWalksTransfers:
    """The preview's transfer plans: one per transfer, labelled, ordered."""

    def test_a_transfer_plan_is_labelled_by_its_FROM_side_in_transfer_id_order(
        self, app, db, seed_user, seed_periods,
    ):
        """DECLARED (BAL-546): "Transfer to <account>", ordered by transfer id.

        The label was whichever shadow the context's unordered query returned
        first, and so was the order; the figure is the transfer's resolved
        amount, as it was.
        """
        with app.app_context():
            savings = _savings(seed_user)
            brokerage = _savings(seed_user, name="Brokerage")
            first = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("40.00"),
            )
            second = create_transfer(
                seed_user, db.session, brokerage, seed_user["account"],
                seed_periods[0], amount=Decimal("60.00"),
            )
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            plans = [
                plan for plan in preview.plans
                if plan.kind == carry_forward_service.PLAN_KIND_TRANSFER
            ]

            assert [plan.item.transfer.id for plan in plans] == [
                first.id, second.id,
            ]
            assert [plan.item.name for plan in plans] == [
                "Transfer to Savings",
                f"Transfer to {seed_user['account'].name}",
            ]
            assert [plan.budget for plan in plans] == [
                Decimal("40.00"), Decimal("60.00"),
            ]


class TestTheFormCarriesATransfersTickInItsOwnFields:
    """The route end to end: the panel renders, and parses, both fields."""

    def test_a_bill_and_a_transfer_of_one_number_both_settle_through_the_form(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """One POST, one number in two fields: two settles, no partial notice."""
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            db.session.commit()
            transfer = _transfer_numbered_like(
                seed_user, seed_periods[0], bill.id, _savings(seed_user),
            )
            account_id = seed_user["account"].id
            number = bill.id

        # The route reconciles against the account's governing assertion, so
        # one is declared through the true-up door for the day both period-0
        # items are overdue by.
        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": _OBSERVED_ON.isoformat(),
            },
        )
        assert response.status_code == 200, response.data

        body = auth_client.get(f"/accounts/{account_id}/reconcile").data.decode()
        assert f'name="transaction_ids" value="{number}"' in body
        assert f'name="settled_amount-{number}"' in body
        assert f'name="transfer_ids" value="{number}"' in body
        assert f'name="transfer_amount-{number}"' in body
        # The two checkboxes' DOM ids differ too (the template prefixes the
        # panel id with ``reconcile-``), so each label still names its box.
        control = f"reconcile-reconcile-panel-{account_id}"
        assert f'id="{control}-t{number}"' in body
        assert f'id="{control}-l{number}"' in body

        response = auth_client.post(
            f"/accounts/{account_id}/reconcile",
            data={
                "transaction_ids": [str(number)],
                f"settled_amount-{number}": "180.00",
                "transfer_ids": [str(number)],
                f"transfer_amount-{number}": "75.00",
            },
        )
        assert response.status_code == 200, response.data
        assert b"already been settled" not in response.data

        with app.app_context():
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(Transaction, number).status_id != projected
            assert db.session.get(Transfer, transfer.id).status_id != (
                projected
            )
