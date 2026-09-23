"""
Shekel Budget App -- A recurring definition's edit never strands a projected row below its books

Plan step ``pay_calendar:C18-a``, rulings **R-PC90** (refuse an account move
that strands a still-projected row) and **R-PC91** (widened to ANY save:
the edit door checks the state the save would leave, whatever field
changed).  A recurring definition's occurrences start above its accounts'
books (ruling **R-PC85**), so a save that leaves a live, still-Projected row
of the definition on or before them leaves that row named by no occurrence,
and the regeneration in the same save retires it -- the forecast rises by
its amount without a word.  Both template edit doors refuse instead, roll
the edit back and say which row.

Every POST here carries what the edit form renders (a route test that posts
a hand-picked payload can pass against an arm a browser never reaches):
the envelope box is posted only when ticked, as a browser posts a checkbox.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import AccountType, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service, recurrence_engine, transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.utils.balance_predicates import is_projected_clause
from tests._test_helpers import (
    all_periods,
    cadence_payload,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

_ONE_DAY = timedelta(days=1)


@pytest.mark.usefixtures("seed_periods_today")
class TestATransactionDefinitionsEditIsRefused:
    """The transaction-template edit door, rulings R-PC90 and R-PC91."""

    def test_a_move_onto_books_opening_after_a_projected_row_is_refused(
        self, app, auth_client, seed_user,
    ):
        """R-PC90: the first row is due on the first payday; the new books open a day later.

        The save carries a rename too, so the rollback is graded: nothing of
        the edit survives the refusal.
        """
        with app.app_context():
            template = _transaction_template_with_rows(seed_user, "Rent")
            first_due = _first_due(template)
            later = _account_opened_on(seed_user, "New checking", first_due + _ONE_DAY)
            rows_before = len(_live_rows(template))

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, name="Rent, moved", account_id=str(later.id),
                ),
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {first_due.isoformat()}".encode() in (
                resp.data
            )
            saved = _reload(TransactionTemplate, template.id)
            assert saved.account_id == seed_user["account"].id
            assert saved.name == "Rent"
            assert len(_live_rows(saved)) == rows_before

    def test_a_move_onto_books_opening_BEFORE_every_row_is_saved(
        self, app, auth_client, seed_user,
    ):
        """The other side: books opening the day before the first row strand nothing.

        The template's own books open on the first payday (an opening cannot
        precede it), so its rows start one paycheck later and a day before
        that is a real opening.
        """
        with app.app_context():
            home = _account_opened_on(
                seed_user, "Home checking", _first_payday(seed_user),
            )
            template = _transaction_template_with_rows(
                seed_user, "Rent", account_id=home.id,
            )
            earlier = _account_opened_on(
                seed_user, "New checking", _first_due(template) - _ONE_DAY,
            )

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(
                    template, account_id=str(earlier.id),
                ),
            )

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/templates")
            assert _reload(TransactionTemplate, template.id).account_id == earlier.id

    def test_unticking_the_envelope_box_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-PC91: no account moves; the box alone strands the straddled row.

        The books open three days into the second paycheck.  As an envelope,
        that paycheck's row is compared on the paycheck's LAST day, after the
        books, so it was generated (R-PC89); unticked, it is a bill compared
        on its due day -- the payday, inside the books.  The refusal names
        the row as the save would leave it: a bill and its due day.
        """
        with app.app_context():
            straddled = seed_periods_today[1].start_date
            account = _account_opened_on(
                seed_user, "Mid-paycheck books", straddled + 3 * _ONE_DAY,
            )
            template = _transaction_template_with_rows(
                seed_user, "Groceries", account_id=account.id, is_envelope=True,
            )
            assert _first_due(template) == straddled

            payload = _transaction_update_payload(template)
            del payload["is_envelope"]
            resp = auth_client.post(
                f"/templates/{template.id}", data=payload, follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {straddled.isoformat()}".encode() in (
                resp.data
            )
            saved = _reload(TransactionTemplate, template.id)
            assert saved.is_envelope is True
            assert _first_due(saved) == straddled

    def test_keeping_the_envelope_box_ticked_is_saved(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same definition, the box posted ticked: nothing is stranded."""
        with app.app_context():
            straddled = seed_periods_today[1].start_date
            account = _account_opened_on(
                seed_user, "Mid-paycheck books", straddled + 3 * _ONE_DAY,
            )
            template = _transaction_template_with_rows(
                seed_user, "Groceries", account_id=account.id, is_envelope=True,
            )

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(template, name="Food"),
            )

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/templates")
            assert _reload(TransactionTemplate, template.id).name == "Food"

    def test_a_RENAME_alone_is_refused_over_a_row_already_below_the_books(
        self, app, auth_client, seed_user,
    ):
        """R-PC91's 'whatever field changed': no account, no envelope box moves.

        The definition is put on later books directly -- the state a row
        that predates the bound is in -- so the only thing the save changes
        is the name.  A refusal gated on WHICH field moved would save it, and
        the regeneration would retire the stranded row.
        """
        with app.app_context():
            template = _transaction_template_with_rows(seed_user, "Rent")
            first_due = _first_due(template)
            later = _account_opened_on(seed_user, "Later books", first_due + _ONE_DAY)
            template.account_id = later.id
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}",
                data=_transaction_update_payload(template, name="Rent, renamed"),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            saved = _reload(TransactionTemplate, template.id)
            assert saved.name == "Rent"
            assert _first_due(saved) == first_due


@pytest.mark.usefixtures("seed_periods_today")
class TestATransferDefinitionsEditIsRefused:
    """The transfer-template edit door: the later of its two books binds."""

    def test_a_destination_move_onto_later_books_is_refused(
        self, app, auth_client, seed_user,
    ):
        """R-PC90 at the transfer door; its rows compare their due day."""
        with app.app_context():
            savings = _savings_account(seed_user)
            template = _transfer_template_with_rows(seed_user, savings)
            first_due = _first_transfer_due(template)
            later = _account_opened_on(seed_user, "Late savings", first_due + _ONE_DAY)

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, name="Sweep, moved", to_account_id=str(later.id),
                ),
                follow_redirects=True,
            )

            assert b"This change cannot be saved" in resp.data
            assert f"still projected and due {first_due.isoformat()}".encode() in (
                resp.data
            )
            saved = _reload(TransferTemplate, template.id)
            assert saved.to_account_id == savings.id
            assert saved.name == "Sweep to savings"

    def test_a_destination_move_onto_EARLIER_books_is_saved(
        self, app, auth_client, seed_user,
    ):
        """The other side, so the transfer door's refusal is not unconditional."""
        with app.app_context():
            savings = _savings_account(seed_user)
            template = _transfer_template_with_rows(seed_user, savings)
            earlier = _account_opened_on(
                seed_user, "Early savings",
                _first_transfer_due(template) - _ONE_DAY,
            )

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data=_transfer_update_payload(
                    template, to_account_id=str(earlier.id),
                ),
            )

            assert resp.status_code == 302
            assert _reload(TransferTemplate, template.id).to_account_id == earlier.id


# ── helpers ──────────────────────────────────────────────────────────────


def _account_opened_on(seed_user, name, opened_on):
    """Create and commit a checking account whose books open on *opened_on*."""
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name=name,
            anchor_balance=Decimal("0.00"),
            observed_on=opened_on,
        ),
    )
    db.session.commit()
    return account


def _first_payday(seed_user):
    """The owner's first payday -- the earliest day an opening may state."""
    return min(p.start_date for p in all_periods(seed_user["user"].id))


def _savings_account(seed_user):
    """Create and commit a Savings account whose books open on the first payday.

    A transfer into it therefore starts one paycheck later: its first-payday
    row would land ON the opening, inside it.
    """
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0.00"),
            observed_on=_first_payday(seed_user),
        ),
    )
    db.session.commit()
    return account


def _schedule(user_id):
    """A generation schedule over every one of the owner's periods."""
    return GenerationSchedule.for_period_ids(
        BalanceContext.build(user_id),
        {p.id for p in all_periods(user_id)},
    )


def _transaction_template_with_rows(seed_user, name, *, account_id=None, is_envelope=False):
    """An every-paycheck expense template, its rows generated, committed."""
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=account_id or seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name=name,
        default_amount=Decimal("100.00"),
        is_envelope=is_envelope,
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    make_cadence_rule(template, EVERY_PERIOD)
    recurrence_engine.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _transfer_template_with_rows(seed_user, savings):
    """An every-paycheck transfer into *savings*, its rows generated, committed."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="Sweep to savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    make_cadence_rule(template, EVERY_PERIOD)
    transfer_recurrence.generate_for_template(
        template, _schedule(template.user_id), seed_user["scenario"].id,
    )
    db.session.commit()
    return template


def _live_rows(template):
    """The template's projected, non-deleted transactions."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template.id,
            is_projected_clause(Transaction),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )


def _first_due(template):
    """The earliest due day among the template's live projected rows."""
    return min(row.due_date for row in _live_rows(template))


def _first_transfer_due(template):
    """The earliest due day among the transfer template's live projected transfers."""
    return min(
        row.due_date for row in db.session.query(Transfer).filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_deleted.is_(False),
        )
    )


def _reload(model, template_id):
    """Re-read *model* ``template_id`` from the database."""
    db.session.expire_all()
    return db.session.get(model, template_id)


def _transaction_update_payload(template, **overrides):
    """The transaction edit form's fields, the cadence restated, as a browser posts them.

    The envelope box is present only when ticked (``"on"``); a test that
    unticks it deletes the key.
    """
    payload = {
        "name": template.name,
        "default_amount": str(template.default_amount),
        "account_id": str(template.account_id),
        "category_id": str(template.category_id),
        "transaction_type_id": str(template.transaction_type_id),
        "version_id": str(template.version_id),
        # The cadence restated WITHOUT a start: the stored one rides through,
        # which is what a browser posts for an untouched rule.
        **cadence_payload(unit=RecurrenceUnitEnum.PERIOD, states_a_start=False),
    }
    if template.is_envelope:
        payload["is_envelope"] = "on"
    payload.update(overrides)
    return payload


def _transfer_update_payload(template, **overrides):
    """The transfer edit form's fields, the cadence restated, as a browser posts them."""
    payload = {
        "name": template.name,
        "default_amount": str(template.default_amount),
        "from_account_id": str(template.from_account_id),
        "to_account_id": str(template.to_account_id),
        "category_id": str(template.category_id),
        "version_id": str(template.version_id),
        **cadence_payload(unit=RecurrenceUnitEnum.PERIOD, states_a_start=False),
    }
    payload.update(overrides)
    return payload
