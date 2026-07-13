"""
Shekel Budget App -- Action-card status-dropdown pre-hint tests.

Grid audit D2 (ruled 2026-07-11, both halves): besides the designed
error fragments, the action cards' status dropdowns disable options the
state machine would reject from the row's CURRENT status, so an illegal
transition cannot be picked instead of failing after Save.  These tests
pin the disabled-option rendering per card; the legality table itself is
covered by ``tests/test_services/test_state_machine.py``.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.services import account_service, transfer_service


def _create_expense(seed_user, seed_periods_today, *, is_envelope=False):
    """Insert a projected ad-hoc expense for a full-edit GET."""
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = Transaction(
        pay_period_id=seed_periods_today[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Prehint Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("42.00"),
        is_envelope=is_envelope,
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _option_tag(html, status_id):
    """Return the ``<option>`` tag chunk for *status_id* from *html*.

    Splitting on ``<option`` keeps each option's attributes in one
    chunk regardless of attribute order or line wrapping, so the
    assertions below cannot false-positive on a neighbouring option.
    """
    for chunk in html.split("<option")[1:]:
        tag = chunk.split(">", 1)[0]
        if f'value="{status_id}"' in tag:
            return tag
    raise AssertionError(f"no option for status id {status_id} in fragment")


class TestTransactionCardPreHint:
    """The transaction card's dropdown disables illegal transitions."""

    def test_projected_disables_settled_enables_done(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """From Projected: Settled is disabled, Paid stays selectable.

        projected -> settled is illegal (a row must pass through Done /
        Received); projected -> done is the everyday transition.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            settled_tag = _option_tag(
                html, ref_cache.status_id(StatusEnum.SETTLED),
            )
            assert "disabled" in settled_tag
            assert "Not reachable from Projected" in settled_tag

            done_tag = _option_tag(html, ref_cache.status_id(StatusEnum.DONE))
            assert "disabled" not in done_tag

    def test_envelope_row_disables_credit_option(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A purchase-tracking row's Credit option is disabled.

        Credit is per-entry on tracked rows (the route guard's message);
        the dropdown pre-hints the same rule the Credit quick-action
        button already follows.
        """
        with app.app_context():
            txn = _create_expense(
                seed_user, seed_periods_today, is_envelope=True,
            )

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            credit_tag = _option_tag(
                html, ref_cache.status_id(StatusEnum.CREDIT),
            )
            assert "disabled" in credit_tag
            assert "per purchase entry" in credit_tag


class TestTransferCardPreHint:
    """The transfer card's dropdown excludes transaction-only statuses."""

    def test_transfer_disables_credit_and_received(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit and Received are disabled on a transfer's dropdown.

        The transfer transition map excludes both statuses entirely
        (credit/payback is expense-only; Received is an income display
        convention), so they are never selectable regardless of the
        current status.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Prehint Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("75.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                    name="Prehint Transfer",
                ),
            )
            db.session.commit()

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            for member in (StatusEnum.CREDIT, StatusEnum.RECEIVED):
                tag = _option_tag(html, ref_cache.status_id(member))
                assert "disabled" in tag

            done_tag = _option_tag(html, ref_cache.status_id(StatusEnum.DONE))
            assert "disabled" not in done_tag
