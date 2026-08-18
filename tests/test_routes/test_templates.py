"""
Shekel Budget App -- Template Route Tests

Tests for transaction template CRUD and recurrence preview:
  - Template listing (happy path, auth)
  - Template creation (with/without recurrence, validation, IDOR)
  - Template update (happy path, validation, IDOR, recurrence conflict)
  - Template archive (archive + soft-delete projected txns)
  - Template unarchive (restore + regenerate)
  - Recurrence preview HTMX endpoint
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType, RecurrencePattern, Status, TransactionType,
)
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.models.user import User, UserSettings
from app.routes._form_errors import GENERIC_VALIDATION_FLASH
from app.services.auth_service import hash_password
from app.services import account_service, pay_period_service, status_seam
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    END_BOUND_KINDS,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
)
from app.utils.dates import display_today
from tests._test_helpers import (
    settlement_columns,
    settlement_if_settling,
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    end_bound_payload,
    make_pattern_rule,
    make_transfer_template,
)


# ── Helpers ──────────────────────────────────────────────────────────

#: Which ``seed_periods_today`` period contains today, stated once.
#:
#: That fixture generates 10 biweekly periods "so today falls in period 4", so
#: indices 0-3 are CLOSED.  A create form defaults its first occurrence to
#: today (plan step R7c-b), and a closing bound below that date is refused as
#: "ends before it starts" -- so a test wanting a reachable bound counts
#: FORWARD from here rather than naming a low index.
_TODAYS_PERIOD_INDEX = 4



def _create_template(seed_user, name="Rent", amount="1200.00",
                     txn_type="Expense", pattern_name=None):
    """Create a transaction template for the test user.

    Args:
        seed_user: The seed_user fixture dict.
        name: Template name.
        amount: Default amount string.
        txn_type: 'income' or 'expense'.
        pattern_name: Optional recurrence pattern name (e.g. 'every_period').

    Returns:
        TransactionTemplate: the created template.
    """
    txn_type_obj = db.session.query(TransactionType).filter_by(name=txn_type).one()
    category = seed_user["categories"]["Rent"]

    rule = None
    if pattern_name:
        # Authored through the write door, which is what plan step R7c-b made
        # the only way to make a rule: ``unit_id``, ``placement_id``,
        # ``shift_id`` and ``starts_on`` are ``NOT NULL``, so naming a pattern
        # and nothing else no longer produces a row.
        rule = make_pattern_rule(seed_user["user"].id, pattern_name)

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        transaction_type_id=txn_type_obj.id,
        recurrence_rule_id=rule.id if rule else None,
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(template)
    db.session.commit()
    return template


def _future_override_txn(seed_user, template, amount="1500.00"):
    """Generate a template's instances and override the latest (future) one.

    The latest instance is comfortably on or after today, so it sits inside
    the update route's regeneration window and a subsequent amount-change
    edit collides with it.  Returns the committed override Transaction
    (is_override=True, carrying its own ``amount``).
    """
    from app.services import recurrence_engine, pay_period_service
    scenario = seed_user["scenario"]
    periods = pay_period_service.get_all_periods(seed_user["user"].id)
    recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id)
    db.session.flush()
    txn = (
        db.session.query(Transaction)
        .filter_by(template_id=template.id)
        .order_by(Transaction.due_date.desc())
        .first()
    )
    txn.is_override = True
    txn.estimated_amount = Decimal(amount)
    db.session.commit()
    return txn


def _create_other_user_with_template():
    """Create a second user with their own template.

    Returns:
        dict with keys: user, account, category, template.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()


    # Bootstrap pay period (E-19, Commit 3): the
    # account_service factory requires the user to have at
    # least one pay period to anchor against.
    from datetime import date as _date, timedelta as _td
    from app.models.pay_period import PayPeriod as _PayPeriod
    _bootstrap = _PayPeriod(
        user_id=other_user.id,
        start_date=_date(2024, 1, 5),
        end_date=_date(2024, 1, 5) + _td(days=13),
        period_index=0,
    )
    db.session.add(_bootstrap)
    db.session.flush()
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)

    category = Category(
        user_id=other_user.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)
    db.session.flush()

    txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=other_user.id,
        account_id=account.id,
        category_id=category.id,
        transaction_type_id=txn_type.id,
        name="Other Rent",
        default_amount=Decimal("900.00"),
    )
    db.session.add(template)
    db.session.commit()

    return {
        "user": other_user,
        "account": account,
        "category": category,
        "template": template,
    }


# ── List Tests ───────────────────────────────────────────────────────


class TestTemplateList:
    """Tests for GET /templates."""

    def test_list_templates(self, app, auth_client, seed_user):
        """GET /templates renders the template list page."""
        with app.app_context():
            _create_template(seed_user, name="Car Payment")

            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert b"Car Payment" in resp.data

    def test_list_templates_empty(self, app, auth_client, seed_user):
        """GET /templates renders correctly when user has no definitions."""
        with app.app_context():
            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert b"No active recurring definitions" in resp.data
            assert b"Car Payment" not in resp.data


# ── Create Tests ─────────────────────────────────────────────────────


class TestTemplateCreate:
    """Tests for GET /templates/new and POST /templates."""

    def test_new_template_form(self, app, auth_client, seed_user, seed_periods_today):
        """GET /templates/new renders the creation form."""
        with app.app_context():
            resp = auth_client.get("/templates/new")
            assert resp.status_code == 200
            assert b"New Recurring Transaction" in resp.data
            assert b'name="name"' in resp.data
            assert b'name="default_amount"' in resp.data
            # The three cadence controls plan step R7b-2 put in place of the
            # single pattern <select>.  Both interval inputs carry the same
            # name, which is why only one of them is ever enabled.
            assert b'name="recurrence_unit"' in resp.data
            assert b'name="interval_n"' in resp.data
            assert b'name="recurrence_placement"' in resp.data

    def test_create_no_recurrence_with_the_payload_a_browser_posts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Does not repeat" saves when every rendered control submits.

        **The regression for a 500 the whole suite was green across**, found
        by the browser drive (``tests/manual/verify_recurrence_form.py``) at
        plan step R7b-4 and fixed in the same commit.  The "Starts on" box
        lives inside ``#recurrence-fields``, which is HIDDEN when the form
        says "does not repeat" -- and a hidden input still submits, so a real
        save posted ``start_date=""``.  The F-24 helper's no-cadence branch
        did not pop it, and the key reached ``TransactionTemplate(**data)``,
        whose constructor has no such keyword.

        Every hand-written payload in this suite omitted the key, because a
        person writing one includes the fields they are thinking about.  This
        one is written the other way round: it carries every control the page
        renders, empty where the user touched nothing, which is what the wire
        actually holds.

        The script now DISABLES the box when the definition does not repeat,
        so the key no longer arrives -- and this test keeps the server half
        honest anyway.  Disabling is the affordance; popping is the rule.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Browser Shaped No Recurrence",
                "default_amount": "24.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                # "Does not repeat" -- an empty unit, with every other
                # recurrence control posting the value it renders with.
                "recurrence_unit": "",
                "recurrence_placement": "",
                "interval_n": "1",
                "day_of_month": "",
                "due_day_of_month": "",
                "month_of_year": "",
                "start_date": "",
                "recurrence_end_mode": "never",
                "end_date": "",
                "max_occurrences": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Browser Shaped No Recurrence")
                .one()
            )
            assert template.recurrence_rule_id is None

    def test_create_with_a_starts_on_date_bounds_the_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The positive twin: a stated "Starts on" reaches the rule.

        Without this the case above would pass against a route that dropped
        the key on EVERY branch rather than only the no-cadence one -- which
        would silently discard the opening bound the user typed.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]
            starts_on = seed_periods_today[1].start_date

            resp = auth_client.post("/templates", data={
                "name": "Browser Shaped With Start",
                "default_amount": "24.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                "starts_on": starts_on.isoformat(),
                "recurrence_end_mode": "never",
                "end_date": "",
                "max_occurrences": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Browser Shaped With Start")
                .one()
            )
            assert template.recurrence_rule is not None
            assert template.recurrence_rule.starts_on == starts_on

    def test_create_template_no_recurrence(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates creates a template without recurrence."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Internet Bill",
                "default_amount": "79.99",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Internet Bill"
            ).one()
            assert template.default_amount == Decimal("79.99")
            assert template.recurrence_rule_id is None

    def test_create_template_with_recurrence(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates creates a template with recurrence and generates transactions."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Rent Payment",
                "default_amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent Payment"
            ).one()
            assert template.recurrence_rule is not None

            # An every-paycheck rule generates one transaction per period FROM
            # ITS FIRST OCCURRENCE ONWARD.  ``seed_periods_today`` creates 10
            # biweekly periods with today in index 4, and a create form
            # defaults the first occurrence to today -- so the rule fires in
            # periods 4..9 and NOT in the four that have already closed.
            #
            # It asserted 10 until plan step R7c-b, which is the defect that
            # step closed rather than a count that drifted: an unstated opening
            # bound meant "start with the schedule", and the create routes
            # generate with no lower window bound, so a $2,000.00 rent template
            # created today wrote backdated rows into pay periods the user had
            # already reconciled.  ``TestACreateDoesNotBackfillClosedPayPeriods``
            # states the property; this is the same fact seen from the count.
            expected = len(seed_periods_today) - _TODAYS_PERIOD_INDEX
            assert expected == 6
            txns = db.session.query(Transaction).filter_by(
                template_id=template.id
            ).all()
            assert len(txns) == expected
            # Each transaction maps to a distinct period, and none of them is
            # one that closed before the rule began.
            period_ids = {txn.pay_period_id for txn in txns}
            assert len(period_ids) == expected
            assert period_ids.isdisjoint({
                period.id
                for period in seed_periods_today[:_TODAYS_PERIOD_INDEX]
            })

    def test_create_template_validation_error(self, app, auth_client, seed_user):
        """POST /templates with missing required fields shows validation error."""
        with app.app_context():
            resp = auth_client.post("/templates", data={
                # Missing name, amount, category, type, account.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_create_template_invalid_account(self, app, auth_client, seed_user):
        """POST /templates with another user's account is rejected."""
        with app.app_context():
            other = _create_other_user_with_template()
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Sneaky Template",
                "default_amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": other["account"].id,  # Other user's account.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account" in resp.data

    def test_create_template_invalid_category(self, app, auth_client, seed_user):
        """POST /templates with another user's category is rejected."""
        with app.app_context():
            other = _create_other_user_with_template()
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/templates", data={
                "name": "Sneaky Template",
                "default_amount": "100.00",
                "category_id": other["category"].id,  # Other user's category.
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid category" in resp.data

    def test_create_template_on_loan_account_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates targeting a loan account is refused; no template made.

        A template on a loan would have the recurrence engine generate raw
        transactions onto the loan account (``recurrence_engine`` copies
        ``template.account_id``) -- the N-11 shape the create routes already
        refuse.  The shared ``_validate_template_form`` gate closes this
        second source.  The negative control is
        ``test_create_template_no_recurrence``: the identical form on a
        checking account is accepted.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session,
                principal=Decimal("200000.00"), rate=Decimal("0.06"),
                origination_date=date(2026, 1, 1), name="Loan Template Target",
            )
            db.session.commit()
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "On A Loan",
                "default_amount": "300.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": loan.id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"not a transaction sum" in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="On A Loan",
            ).count() == 0


# ── Update Tests ─────────────────────────────────────────────────────


class TestTemplateUpdate:
    """Tests for GET /templates/<id>/edit and POST /templates/<id>."""

    def test_edit_template_form(self, app, auth_client, seed_user):
        """GET /templates/<id>/edit renders the edit form."""
        with app.app_context():
            template = _create_template(seed_user)

            resp = auth_client.get(f"/templates/{template.id}/edit")
            assert resp.status_code == 200
            assert b"Rent" in resp.data

    def test_update_template_success(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates/<id> updates template fields."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Updated Rent",
                "default_amount": "1300.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(template)
            assert template.name == "Updated Rent"
            assert template.default_amount == Decimal("1300.00")

    def test_update_template_cannot_flip_is_active_or_sort_order(
        self, app, auth_client, seed_user,
    ):
        """POST /templates/<id> never writes is_active / sort_order.

        Those columns are owned by the archive / unarchive routes (which
        pair the flag flip with the projected-transaction soft-delete this
        route does not perform).  They are absent from both
        ``TemplateUpdateSchema`` and ``_TEMPLATE_UPDATE_FIELDS``, so even a
        crafted form that submits them must leave the stored values
        untouched while a legitimate field still updates.
        """
        with app.app_context():
            template = _create_template(seed_user)
            assert template.is_active is True
            assert template.sort_order == 0

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Renamed",
                "is_active": "false",
                "sort_order": "99",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.refresh(template)
            # Legitimate field updated -- the request was processed.
            assert template.name == "Renamed"
            # Crafted is_active / sort_order keys were ignored.
            assert template.is_active is True
            assert template.sort_order == 0

    def test_update_template_validation_error(self, app, auth_client, seed_user):
        """POST /templates/<id> with invalid data shows the error out loud."""
        with app.app_context():
            template = _create_template(seed_user)

            # ``nominal_day`` fails ``Range(min=29, max=31)``.  It replaced
            # ``day_of_month`` here at plan step R7c-b, which stopped the
            # schemas declaring that field at all -- so an out-of-domain value
            # for it is dropped by ``EXCLUDE`` and this test would have been
            # asserting the banner for a key nothing reads.
            resp = auth_client.post(f"/templates/{template.id}", data={
                "nominal_day": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # **The field's OWN message, not the generic banner, since plan
            # step R7c-c**: ``nominal_day`` joined
            # ``_form_errors.ACTIONABLE_FLASH_FIELDS`` there, because the pair
            # rule beside this domain check authors a real sentence naming the
            # control and it was reaching nobody.  The stock Range message
            # rides in with it, which is the better of the two answers -- it
            # states the domain, where the banner states nothing on a redirect
            # that highlights nothing.  Only a crafted POST sees it: the
            # control is a ``<select>`` offering 29, 30 and 31.
            assert (
                b"Must be greater than or equal to 29 and less than or equal "
                b"to 31." in resp.data
            )

    def test_update_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id> for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}",
                data={"name": "Hijacked"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify original unchanged.
            db.session.refresh(other["template"])
            assert other["template"].name == "Other Rent"

    def test_edit_template_idor(self, app, auth_client, seed_user):
        """GET /templates/<id>/edit for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.get(
                f"/templates/{other['template'].id}/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_amount_change_with_override_shows_chooser(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An amount change colliding with a hand-edited instance shows the
        conflict chooser and does NOT commit the edit (the pending change is
        rolled back until Apply)."""
        with app.app_context():
            template = _create_template(
                seed_user, pattern_name="Every Period", amount="1200.00",
            )
            _future_override_txn(seed_user, template, amount="1500.00")
            tid = template.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "default_amount": "1400.00",
                **cadence_payload(),
            })
            assert resp.status_code == 200
            assert b"hand-edited" in resp.data
            assert b"Keep" in resp.data and b"Use" in resp.data
            # Rolled back: the template keeps its pre-edit amount.
            db.session.expire_all()
            assert db.session.get(
                TransactionTemplate, tid,
            ).default_amount == Decimal("1200.00")

    def test_chooser_apply_use_realigns_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply with 'use' clears the override and moves it to the new
        amount; the template edit commits.  Every-Period amount is the
        template default, so the realigned instance reads $1,400.00."""
        with app.app_context():
            template = _create_template(
                seed_user, pattern_name="Every Period", amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "default_amount": "1400.00",
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.estimated_amount == Decimal("1400.00")
            assert reloaded.is_override is False
            assert db.session.get(
                TransactionTemplate, tid,
            ).default_amount == Decimal("1400.00")

    def test_chooser_apply_keep_preserves_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply with 'keep' leaves the override untouched at its hand-edited
        $1,500.00; the template amount edit still commits."""
        with app.app_context():
            template = _create_template(
                seed_user, pattern_name="Every Period", amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "default_amount": "1400.00",
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "keep",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.estimated_amount == Decimal("1500.00")
            assert reloaded.is_override is True
            assert db.session.get(
                TransactionTemplate, tid,
            ).default_amount == Decimal("1400.00")

    def test_name_only_edit_with_override_skips_chooser(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rename that leaves the amount unchanged never shows the chooser,
        even with an override present: it commits, keeping the override's
        amount and propagating the new name to it."""
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", pattern_name="Every Period",
                amount="1200.00",
            )
            txn = _future_override_txn(seed_user, template, amount="1500.00")
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Apartment Rent",
                "default_amount": "1200.00",
                **cadence_payload(),
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"hand-edited" not in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.estimated_amount == Decimal("1500.00")
            assert reloaded.is_override is True
            assert reloaded.name == "Apartment Rent"

    def test_is_salary_linked_template_detects_active_link(
        self, app, seed_user, seed_periods_today,
    ):
        """The signal the update route uses to skip the chooser for salary
        rows: True iff an active salary profile references the template.

        A salary-linked template's instances are paycheck-calculated, so its
        ``default_amount`` is vestigial; the update route passes
        ``amount_drives_instances=False`` for it, suppressing a chooser that
        would otherwise mis-state a paycheck on 'use'.
        """
        with app.app_context():
            from app.services.template_amount_service import (
                is_salary_linked_template,
            )
            from tests._test_helpers import make_salary_profile
            template = _create_template(seed_user, txn_type="Income")
            assert is_salary_linked_template(template) is False

            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()
            db.session.refresh(template)
            assert is_salary_linked_template(template) is True

    def test_chooser_use_on_deleted_conflict_restores_with_current_name(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Apply 'use' on a soft-deleted conflict restores it at the new
        amount AND with the edit's new name.  The rename now reaches
        soft-deleted rows, so a restored instance is never stale-named."""
        with app.app_context():
            from app.services import recurrence_engine, pay_period_service
            template = _create_template(
                seed_user, name="Rent", pattern_name="Every Period",
                amount="1200.00",
            )
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id)
            db.session.flush()
            txn = (
                db.session.query(Transaction)
                .filter_by(template_id=template.id)
                .order_by(Transaction.due_date.desc())
                .first()
            )
            txn.is_deleted = True  # a soft-deleted conflict in the window
            db.session.commit()
            tid, txn_id = template.id, txn.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Apartment Rent",
                "default_amount": "1400.00",
                **cadence_payload(),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.is_deleted is False
            assert reloaded.estimated_amount == Decimal("1400.00")
            assert reloaded.name == "Apartment Rent"

    def test_amount_edit_commits_when_the_only_conflict_is_a_retained_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A RETAINED row must not swallow the edit that produced it.

        **This pins a defect that shipped and was caught by adversarial review
        of plan step R10-a.**  ``regenerate_or_conflict_chooser`` branched on
        "the amount changed", not on "there is something to decide", so a
        conflict carrying ONLY ``retained`` rendered the chooser -- whose rows
        come from ``overridden`` and ``deleted`` alone, so the page listed
        NOTHING -- and then rolled the whole pending edit back.  The owner saw
        "Some upcoming instances were hand-edited" over an empty list, and
        their amount change silently did not happen.

        A retained row is not a question: the pass already left it untouched.
        So the edit must COMMIT, and the notice must name what was skipped.
        """
        with app.app_context():
            from app.services import (
                account_service, entry_service, pay_period_service,
                recurrence_engine,
            )
            template = _create_template(
                seed_user, name="Groceries", pattern_name="Every Period",
                amount="500.00",
            )
            template.is_envelope = True  # rows track purchases, as production
            db.session.flush()
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_periods(template.user_id, periods),
                scenario.id,
            )
            db.session.flush()

            # One row carries a purchase, so the pass RETAINS it -- and there
            # is no override or soft-delete anywhere, so retained is the ONLY
            # list populated.  That is the shape the branch got wrong.
            # The CURRENT period's row, which is the only one that satisfies
            # both constraints at once: the update route sweeps from today, so
            # an earlier row is out of the window, and a purchase may not be
            # dated in the future (ruling R-M), so a later row cannot hold one.
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            txn = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id, pay_period_id=current.id,
                )
                .one()
            )
            entry_service.create_entry(
                txn.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    amount=Decimal("40.00"),
                    description="Kroger",
                    purchased_on=current.start_date,
                ),
            )
            # Moving the template's ACCOUNT is what retains the row: its
            # purchases would follow onto the new account and lose whatever
            # statement link cleared them.
            moved_to = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Second Checking",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(moved_to)
            db.session.commit()
            tid, moved_to_id = template.id, moved_to.id

            resp = auth_client.post(f"/templates/{tid}", data={
                "name": "Groceries",
                "default_amount": "650.00",
                "account_id": str(moved_to_id),
                **cadence_payload(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            # The chooser must NOT have rendered: it had nothing to ask.
            assert b"Some upcoming instances were hand-edited" not in resp.data
            # And the owner is told which rows the pass declined to change.
            assert b"kept the value it already had" in resp.data

            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, tid)
            assert reloaded.default_amount == Decimal("650.00"), (
                "the edit was rolled back by a conflict that asked nothing"
            )

    def test_rename_template_propagates_to_all_instances(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Renaming a template must sync every non-deleted instance's name.

        Before the fix, update_template() only touched template.name and
        relied on regenerate_for_template() to recreate rows with the
        new label.  Regeneration skips historic rows, overrides, and
        immutable rows, so those would keep the stale name and split
        into a second row in the grid.  This test asserts every
        non-deleted instance ends up with the new name -- across past
        and future periods alike.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", pattern_name="Every Period",
            )

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id,
            )
            db.session.commit()

            original_count = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count()
            assert original_count > 0, (
                "Seed periods should yield generated transactions"
            )

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Apartment Rent",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            names = {
                t.name for t in db.session.query(Transaction)
                .filter(
                    Transaction.template_id == template.id,
                    Transaction.is_deleted.is_(False),
                )
                .all()
            }
            assert names == {"Apartment Rent"}, (
                f"Expected every instance renamed; found {names}"
            )

    def test_rename_template_overridden_instance_follows_template(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An is_override=True row must still pick up a template rename.

        is_override only tracks amount/period customization in this
        codebase, not name edits -- so a rename must propagate through
        overridden rows to keep every view consistent.  Without this,
        the row would keep the stale name and the grid would fall back
        to displaying the old label for the overridden period.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", pattern_name="Every Period",
            )

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id,
            )
            db.session.flush()

            overridden = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            overridden.is_override = True
            db.session.commit()
            overridden_id = overridden.id

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Apartment Rent",
            }, follow_redirects=True)
            assert resp.status_code == 200

            db.session.expire_all()
            reloaded = db.session.get(Transaction, overridden_id)
            assert reloaded.name == "Apartment Rent"
            assert reloaded.is_override is True

    def test_rename_template_does_not_duplicate_grid_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End-to-end: the old label must not appear in the grid response
        after a rename, and the new label must render as a row header.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", pattern_name="Every Period",
            )

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id,
            )
            db.session.commit()

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Apartment Rent",
            }, follow_redirects=True)
            assert resp.status_code == 200

            grid = auth_client.get("/grid")
            assert grid.status_code == 200
            assert b"Apartment Rent" in grid.data
            # Old label must not reappear as a row header.  The seed
            # category is "Rent" so the word appears in the category
            # group column, but the row <th> label used the template
            # name -- verify it is absent from the rendered row.
            assert b">Rent<" not in grid.data


class TestGridRowKeyBuilder:
    """Defense-in-depth tests for the grid's row-key deduplication.

    These tests hit ``build_row_keys`` directly to verify the grid still
    collapses template-linked rows even if stale names slip through
    (legacy data, direct DB edits, future bugs in the rename flow).

    Import path updated in mobile-first v3 plan Commit 13: the helper
    moved from the private ``app.routes.grid._build_row_keys`` to the
    pure service module ``app.services.grid_view_service.build_row_keys``
    (no leading underscore -- it is now a public service API per
    plan Section 6.1).  Behaviour is unchanged; only the import path
    moved.
    """

    def test_row_key_collapses_template_instances_with_drifted_names(
        self, app, seed_user, seed_periods_today,
    ):
        """Stale txn.name values must not split a template into two rows."""
        from app.services.grid_view_service import build_row_keys
        from app.models.category import Category

        with app.app_context():
            template = _create_template(
                seed_user, name="Rent", pattern_name="Every Period",
            )

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id,
            )
            db.session.flush()

            # Simulate the pre-fix state: template renamed, half the
            # generated instances still carry the old label.
            template.name = "Apartment Rent"
            instances = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).order_by(Transaction.id).all()
            assert len(instances) >= 2, "Need at least 2 instances"
            for idx, txn in enumerate(instances):
                txn.name = "Apartment Rent" if idx % 2 else "Rent"
            db.session.flush()

            all_cats = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
            ).all()

            row_keys = build_row_keys(
                instances, all_cats, is_income_section=False,
            )

            matches = [
                rk for rk in row_keys if rk.template_id == template.id
            ]
            assert len(matches) == 1, (
                f"Expected one row for template; got {len(matches)}: "
                f"{[rk.txn_name for rk in matches]}"
            )
            assert matches[0].txn_name == "Apartment Rent"
            assert matches[0].display_name == "Apartment Rent"

    def test_row_key_keeps_standalone_txns_separate_by_name(
        self, app, seed_user, seed_periods_today,
    ):
        """Non-template transactions still dedupe by (category, name)."""
        from app.services.grid_view_service import build_row_keys
        from app.models.category import Category
        from app.models.ref import Status as StatusModel

        with app.app_context():
            rent_cat = seed_user["categories"]["Rent"]
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            projected = db.session.query(StatusModel).filter_by(
                name="Projected",
            ).one()
            period = seed_periods_today[0]
            account = seed_user["account"]
            scenario = seed_user["scenario"]

            # Two standalone expenses in the same category but different
            # names -- these must remain distinct rows.
            txn_a = Transaction(
                account_id=account.id,
                pay_period_id=period.id,
                scenario_id=scenario.id,
                status_id=projected.id,
                name="One-off A",
                category_id=rent_cat.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            txn_b = Transaction(
                account_id=account.id,
                pay_period_id=period.id,
                scenario_id=scenario.id,
                status_id=projected.id,
                name="One-off B",
                category_id=rent_cat.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add_all([txn_a, txn_b])
            db.session.flush()

            all_cats = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
            ).all()

            row_keys = build_row_keys(
                [txn_a, txn_b], all_cats, is_income_section=False,
            )

            labels = sorted(rk.txn_name for rk in row_keys)
            assert labels == ["One-off A", "One-off B"]


# ── Archive Tests ────────────────────────────────────────────────────


class TestTemplateArchive:
    """Tests for POST /templates/<id>/archive."""

    def test_archive_and_soft_deletes(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates/<id>/archive archives template and soft-deletes projected txns."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            # Generate projected transactions.
            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id)
            db.session.commit()

            txn_count = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            # every_period pattern generates 1 transaction per period; 10 seeded periods.
            assert txn_count == 10

            resp = auth_client.post(
                f"/templates/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data

            db.session.refresh(template)
            assert template.is_active is False

            # All projected transactions should be soft-deleted.
            remaining = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert remaining == 0

    def test_archive_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/archive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify template still active.
            db.session.refresh(other["template"])
            assert other["template"].is_active is True

    def test_archive_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999/archive for missing template returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 404


# ── Unarchive Tests ──────────────────────────────────────────────────


class TestTemplateUnarchive:
    """Tests for POST /templates/<id>/unarchive."""

    def test_unarchive_restores_transactions(self, app, auth_client, seed_user, seed_periods_today):
        """POST /templates/<id>/unarchive restores soft-deleted txns."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            # Generate and then delete.
            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id)
            db.session.commit()

            # Archive via the archive route.
            auth_client.post(f"/templates/{template.id}/archive")

            db.session.refresh(template)
            assert template.is_active is False

            # Now unarchive.
            resp = auth_client.post(
                f"/templates/{template.id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

            db.session.refresh(template)
            assert template.is_active is True

            # Transactions should be restored: every_period generates 1 per period,
            # seed_periods_today creates 10 biweekly periods
            active_txns = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert active_txns == 10

    def test_unarchive_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/unarchive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 404


# ── Preview Recurrence Tests ─────────────────────────────────────────


class TestPreviewRecurrence:
    """Tests for GET /templates/preview-recurrence."""

    def test_preview_monthly(self, app, auth_client, seed_user, seed_periods_today):
        """Preview for a monthly cadence returns an occurrence list."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                    "day_of_month": "15",
                },
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data or b"No matching" in resp.data

    def test_preview_an_unmodelled_unit_is_unknown_not_blank(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A ``recurrence_units`` id the app does not model previews as UNKNOWN.

        The successor to the ``Once``-``ref``-row case: since plan step R7b-2
        the preview takes the two AXES rather than a closed-set pattern id, so
        the unmodelled input it can be handed is a unit or a placement.  The
        honest answer is "unknown", not "no preview" -- it is reachable only
        through hand-crafted input, and "no preview" would read as "this is
        fine".  ONE message covers both axes because they share a disposition
        and a reachability; the ABSENT unit below keeps its own, because that
        one is what "Does not repeat" posts and users read it.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(),
                    "recurrence_unit": "999999",
                },
            )
            assert resp.status_code == 200
            assert b"Unknown cadence" in resp.data

    def test_preview_an_unmodelled_placement_is_unknown(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The second axis takes the same answer as the first."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(),
                    "recurrence_placement": "999999",
                },
            )
            assert resp.status_code == 200
            assert b"Unknown cadence" in resp.data

    def test_preview_a_named_unit_with_no_placement_is_unknown(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A placement is REQUIRED once a unit is named, not an optional refinement.

        Defaulting it would let the preview show a schedule the save would not
        produce: ``(1, MONTH)`` funded from the covering paycheck and the same
        cadence funded from the month's first paycheck are different rules.
        """
        with app.app_context():
            payload = cadence_payload(unit=RecurrenceUnitEnum.MONTH)
            del payload["recurrence_placement"]
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={**payload, "day_of_month": "15"},
            )
            assert resp.status_code == 200
            assert b"Unknown cadence" in resp.data

    def test_preview_no_pattern(self, app, auth_client, seed_user, seed_periods_today):
        """Preview with no cadence parameter returns the no-preview message."""
        with app.app_context():
            resp = auth_client.get("/templates/preview-recurrence")
            assert resp.status_code == 200
            assert b"No preview" in resp.data

    @pytest.mark.parametrize(
        ("cadence_name", "unit", "interval_n", "query"),
        [
            # A live 500 before plan step R4a, out of the authoring seam.
            (
                "Every N Periods", RecurrenceUnitEnum.PERIOD, 0, {},
            ),
            # A live 500 MEASURED at plan step R7c-b, and by a route the
            # deleted ``_MAX_START_DATE_YEAR`` no longer covered: past the
            # saved horizon the pay calendar projects the covering paycheck by
            # adding ``cadence_days`` to a start, which overflows near
            # ``date.max``.  ``OverflowError`` comes from outside the
            # recurrence package, so this endpoint's handler never saw it.
            (
                "Every Period", RecurrenceUnitEnum.PERIOD, 1,
                {"starts_on": "9999-12-31"},
            ),
            (
                "Monthly", RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "9999-12-31"},
            ),
            (
                "Monthly", RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "0001-01-01"},
            ),
            # The nominal day took the retired day / month args' place, and
            # takes their disposition: a value the date leaves no room for is
            # a second statement of a day ``starts_on`` already carries.
            (
                "Monthly", RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "2026-04-15", "nominal_day": "30"},
            ),
            (
                "Monthly", RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "2026-02-28", "nominal_day": "99"},
            ),
            (
                "Monthly", RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "2026-02-28", "nominal_day": "0"},
            ),
        ],
    )
    def test_preview_refuses_out_of_domain_arguments_without_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
        cadence_name, unit, interval_n, query,
    ):
        """Unbounded query args answer a muted line, never a stack trace.

        This endpoint reads ``interval_n`` / ``starts_on`` / ``nominal_day``
        straight from ``request.args``.  The two form schemas bound them and
        the columns carry ``ck_recurrence_rules_positive_interval`` /
        ``ck_recurrence_rules_starts_on_range`` /
        ``ck_recurrence_rules_nominal_day`` -- but nothing bounds THIS path,
        and it is reachable by anyone signed in.  The resolution door refuses
        every one by mirroring the columns' own domains, which is the rule the
        endpoint's docstring states: each bound lives on the column and its
        mirror in ``resolve``, never a third time here.

        **The ``day_of_month`` / ``month_of_year`` cases left this list at plan
        step R7c-b, and were not simply dropped.**  Those arguments name
        columns the write door now ENCODES, so the endpoint stopped reading
        them -- a case passing ``?day_of_month=-5`` would assert that an
        argument nothing reads is ignored, which is true of every string.  The
        ``starts_on`` cases replace them on the argument that took their place,
        and the first of those is a 500 this step MEASURED rather than
        inherited.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(unit=unit, interval_n=interval_n),
                    **query,
                },
            )

            assert resp.status_code == 200, (
                f"{cadence_name} with {query} answered {resp.status_code}"
            )
            assert b"No preview for this cadence" in resp.data

    @pytest.mark.parametrize(
        ("label", "unit", "interval_n"),
        [
            # 10000 YEARS is 120000 months; the walk's ordinal divides back out
            # to a year past 9999 and ``date()`` raises ValueError.
            ("years past the calendar", RecurrenceUnitEnum.YEAR, 10_000),
            ("months past the calendar", RecurrenceUnitEnum.MONTH, 120_000),
        ],
    )
    def test_a_huge_interval_previews_ONE_date_and_not_a_stack_trace(
        self, app, auth_client, seed_user, seed_periods_today,
        label, unit, interval_n,
    ):
        """A cadence whose second occurrence leaves the calendar fires once.

        **Opened by plan step R7b-2 and found by an adversarial review of it.**
        Before the step the preview posted a pattern id and ``decode_pattern``
        DISCARDED the submitted interval for every calendar pattern, so only
        the pay-period walk -- which cannot overflow -- ever saw it.  The form
        posts the interval as the cadence itself, so the raw query arg reaches
        ``months_per_step`` and then ``date()``, whose ``ValueError`` is not the
        ``RecurrenceResolutionError`` this endpoint catches.

        **The remedy MOVED at plan step R7c-c, and the expected answer with
        it.**  It used to be that the preview refuses what the save would
        refuse -- and the save refused, because no calendar unit had a storable
        interval above 6.  Freeing the interval is the whole of that step, so
        ``(10000, YEAR)`` is now perfectly savable, and the overflow it exposed
        is closed at its own root instead: ``_months.walk_months`` stops at the
        last month the application's calendar reaches rather than walking off
        the end of ``date``.

        So the honest answer is ONE occurrence -- the rule does fire once, and
        names no second date this application can hold -- and the invariant the
        case was written for still holds: no stack trace, and the preview shows
        what the save would produce.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(unit=unit, interval_n=interval_n),
        )

        assert resp.status_code == 200, label
        assert b"Next 1 occurrences" in resp.data, label

    def test_preview_refuses_a_cadence_the_save_would_refuse(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The preview must not show a schedule that cannot be saved.

        **The unsavable cadence MOVED at plan step R7c-c.**  It was
        ``(2, MONTH)`` -- which walked correctly and had no closed-set pattern
        to be stored as -- and freeing the interval is exactly what made that
        savable.  What is unsavable now is a ``(unit, placement)`` pair the
        resolver has no anchor derivation for: a year-scale cadence deferred
        onto a month's FIRST paycheck names no cycle month, and plan step R8
        owns it.

        Not reachable by clicking -- the picker offers the YEAR unit with one
        placement -- so this is the crafted-args door, pinned because the
        endpoint's own reasoning stops one case short of it.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(
                unit=RecurrenceUnitEnum.YEAR,
                placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
        )

        assert resp.status_code == 200
        assert b"No preview" in resp.data
        assert b"occurrences" not in resp.data

    def test_the_cadence_that_case_refused_is_now_SAVABLE(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``(2, MONTH)`` previews real dates, because it can now be stored.

        The negative control for the case above, and the one that says plan
        step R7c-c actually landed rather than merely moving a refusal around:
        "every other month" is the cadence this whole arc exists for -- it
        resolved and walked correctly from plan step R3 and had nowhere to be
        written -- so the preview showing its dates is the arc's own thesis
        arriving at the surface a user reads.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, interval_n=2,
            ),
        )

        assert resp.status_code == 200
        assert b"No preview" not in resp.data
        assert b"occurrences" in resp.data

    @pytest.mark.parametrize(
        ("label", "unit", "placement"),
        [
            # Resolves and WALKS -- ``_week_walk`` has existed since plan step
            # R3 -- so nothing downstream refuses it.  What refuses it is the
            # offer set, and this is the case that says the preview asks.
            (
                "the WEEK unit", RecurrenceUnitEnum.WEEK,
                PeriodPlacementEnum.CONTAINING_DATE,
            ),
            # Resolves too: the pay-period anchor does not read the placement
            # at all.  Offering it would store a choice the edit form cannot
            # preselect, which is plan ledger row D32's hazard by another door.
            (
                "an inert placement", RecurrenceUnitEnum.PERIOD,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
        ],
    )
    def test_preview_refuses_a_cadence_the_form_does_not_offer(
        self, app, auth_client, seed_user, seed_periods_today,
        label, unit, placement,
    ):
        """The preview asks the OFFER SET, not what happens to walk.

        **The load-bearing case of plan step R7c-c's refusal move.**  Both of
        these resolve and walk perfectly well; what made them unpreviewable
        before that step was ``encode_cadence``, which the preview inherited by
        building its transient rule through the write door.  Deleting the
        encoder deleted that inheritance -- measured, the WEEK unit previewed
        five real dates for a cadence the schema refuses -- so the door asks
        ``require_authorable_cadence`` instead, over the same set the picker
        renders.

        Not reachable by clicking; this is the crafted-args door.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(unit=unit, placement=placement),
        )

        assert resp.status_code == 200, label
        assert b"No preview" in resp.data, label
        assert b"occurrences" not in resp.data, label

    def test_preview_ignores_an_unparseable_end_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?end_date=garbage`` previews the unbounded rule, it does not 500.

        The one unvalidated argument the resolution door cannot refuse:
        ``date.fromisoformat`` runs BEFORE the seam, so its ``ValueError``
        never reaches ``RecurrenceResolutionError``.  A neutral review of plan
        step R4a found it after the route's docstring had already claimed the
        whole class was closed.  An unparseable bound is dropped rather than
        refused -- see ``_recurrence_preview._submitted_iso_date``, which
        parses BOTH date bounds since plan step R7b-4 gave the opening one a
        control (two copies of "parse it or drop it" is the shape that leaves
        one of them missing a fix the other got).
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                    "day_of_month": "15",
                    "end_date": "garbage",
                },
            )

            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_every_period(self, app, auth_client, seed_user, seed_periods_today):
        """Preview for the every-paycheck cadence returns an occurrence list."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string=cadence_payload(),
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_every_n_periods_without_a_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Previewing "every N paychecks" with no first paycheck chosen is a 200.

        It was a 500 before plan step R2c-1, and the cause is worth keeping:
        the route hand-built a transient rule, and a SQLAlchemy column default
        is applied at INSERT rather than at instantiation -- so
        ``offset_periods`` stayed ``None`` and period selection computed
        ``period_index - None``.  Routing the preview through the authoring
        seam fixed it incidentally, because resolution always emits an int.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string=cadence_payload(interval_n=2),
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_ignores_a_start_period_id_entirely(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """The endpoint has no start-period argument left to attack.

        **Audit finding H3 -- pay-period structure disclosure -- was closed by
        REMOVING the surface at plan step R7b-4, not by guarding it.** The
        preview owner-checked a submitted ``start_period_id`` and fell through
        to the user's own data when it was not theirs. The recurrence's opening
        bound is a DATE now, so the endpoint reads no period id at all: a
        foreign one, a nonexistent one and a garbage one are the same
        unrecognised query argument.

        Both shapes are asserted in ONE case because they now have ONE answer,
        and this replaces two tests that compared the response to a baseline
        with the argument dropped -- a comparison that became a tautology for
        ANY ignored argument, while their docstrings went on describing an
        ownership check no code performs.
        """
        with app.app_context():
            baseline = auth_client.get(
                "/templates/preview-recurrence",
                query_string=cadence_payload(),
            )
            assert baseline.status_code == 200
            assert b"occurrences" in baseline.data

            for label, period_id in (
                ("another user's", seed_second_periods[0].id),
                ("nonexistent", 999999),
            ):
                resp = auth_client.get(
                    "/templates/preview-recurrence",
                    query_string={
                        **cadence_payload(),
                        "start_period_id": period_id,
                    },
                )
                assert resp.status_code == 200, label
                assert resp.data == baseline.data, label

    def test_preview_honours_a_submitted_starts_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """What REPLACED the period argument, and it must actually bind.

        The case above only says the retired argument is ignored, which any
        unrecognised key satisfies. This says the new one is READ: a preview
        bounded to a later paycheck must not list dates before it. Without
        this pair the endpoint could be ignoring both.
        """
        with app.app_context():
            later = seed_periods_today[-1]

            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(),
                    "starts_on": later.start_date.isoformat(),
                },
            )

            assert resp.status_code == 200
            body = resp.data.decode()
            assert later.start_date.strftime("%b %d, %Y") in body
            for earlier in seed_periods_today[:-1]:
                assert earlier.start_date.strftime("%b %d, %Y") not in body

    def test_create_recurring_template_ignores_a_foreign_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /templates cannot be attacked through a start period at all.

        deep-quality-hunt #21/#24 was a real IDOR: the persist path's owner
        probe ran only for EVERY_N_PERIODS, so a recurring template wrote a
        foreign ``start_period_id`` onto its RecurrenceRule unchecked and the
        generation boundary came from the victim's pay period.  It was closed
        by making the probe universal.

        **Plan step R7b-4 removed the surface instead of guarding it.**  A
        transaction template's recurrence takes a DATE (``start_date``), and
        ``TemplateCreateSchema`` no longer declares ``start_period_id`` at all
        -- the field went to the TRANSFER schema, where its remaining job
        lives (placing a one-time transfer).  Marshmallow's ``EXCLUDE`` drops
        the key, so a crafted POST cannot express the attack: there is nothing
        to reject because there is nothing to accept.

        Asserted as an IGNORE rather than a refusal, and both halves matter --
        the template IS created (the foreign key changed nothing about a valid
        submission) and its rule carries no start period.  A test that only
        checked for a flash would pass against a route that had silently
        started storing the value again.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            category = seed_user["categories"]["Rent"]
            stated_start = date(2026, 7, 6)
            resp = auth_client.post("/templates", data={
                "name": "Recurring IDOR Template",
                "default_amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(starts_on=stated_start),
                # Second user's period, on a field this schema does not have.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Recurring IDOR Template")
                .one()
            )
            assert template.recurrence_rule is not None
            # The rule starts where THIS owner's submission said, never
            # anywhere the foreign period could put it.  ``starts_on`` is NOT
            # NULL from plan step R7c-b, so "carries no start" is no longer an
            # expressible outcome and the honest check is that the stored date
            # is the submitted one, normalised onto this owner's own paycheck.
            assert template.recurrence_rule.starts_on == (
                calendar_for(seed_user["user"].id)
                .span_containing(stated_start).start_date
            )


# ── Negative Path Tests ─────────────────────────────────────────────


class TestTemplateNegativePaths:
    """Tests for template edge cases, validation gaps, and idempotent operations."""

    def test_archive_already_archived_template(self, app, auth_client, seed_user, seed_periods_today):
        """Archiving an already-archived template is idempotent."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            # No projected transactions exist, so 0 are soft-deleted.
            assert b"0 projected transaction(s) removed" in resp.data

            db.session.refresh(template)
            assert template.is_active is False
            # NOTE: archive_template is idempotent -- no guard against
            # archiving an already-inactive template.

    def test_unarchive_already_active_template(self, app, auth_client, seed_user, seed_periods_today):
        """Unarchiving an already-active template is idempotent."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")
            assert template.is_active is True

            resp = auth_client.post(
                f"/templates/{template.id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"unarchived" in resp.data
            # No soft-deleted transactions to restore.
            assert b"0 projected transaction(s) restored" in resp.data

            db.session.refresh(template)
            assert template.is_active is True
            # NOTE: unarchive is idempotent -- no guard against
            # unarchiving an already-active template.

    def test_create_template_missing_name(self, app, auth_client, seed_user):
        """Creating a template without name fails schema validation."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "default_amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_template_missing_category(self, app, auth_client, seed_user):
        """Creating a template without category_id fails schema validation."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/templates", data={
                "name": "No Category Template",
                "default_amount": "100.00",
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_template_negative_amount(self, app, auth_client, seed_user):
        """Negative amount rejected by schema Range(min=0) validator."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Negative Test",
                "default_amount": "-100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="Negative Test",
            ).count()
            assert count == 0

    def test_edit_nonexistent_template(self, app, auth_client, seed_user):
        """GET /templates/999999/edit for missing template returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/999999/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_update_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999 for missing template returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999",
                data={"name": "Ghost"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_create_template_xss_in_name(self, app, auth_client, seed_user):
        """XSS payload in template name is escaped by Jinja2 auto-escaping."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "<script>alert(1)</script>",
                "default_amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200

            # Verify template was created with the XSS payload.
            template = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="<script>alert(1)</script>",
            ).first()
            assert template is not None

            # Jinja2 auto-escaping prevents raw script tags in output.
            assert b"<script>alert(1)</script>" not in resp.data
            assert b"&lt;script&gt;" in resp.data

    def test_create_template_with_other_users_category_idor(
        self, app, auth_client, seed_user, second_user,
    ):
        """Template creation with another user's category is rejected and DB unchanged."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            other_cat = second_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "IDOR Category Test",
                "default_amount": "100.00",
                "category_id": other_cat.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid category." in resp.data

            # Verify no template was created.
            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="IDOR Category Test",
            ).count()
            assert count == 0

    def test_create_template_with_other_users_account_idor(
        self, app, auth_client, seed_user, second_user,
    ):
        """Template creation with another user's account is rejected and DB unchanged."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "IDOR Account Test",
                "default_amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": second_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account." in resp.data

            # Verify no template was created.
            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id, name="IDOR Account Test",
            ).count()
            assert count == 0


# ── Hard Delete Tests (5A.5-2) ─────────────────────────────────────


class TestTemplateHardDelete:
    """Tests for POST /templates/<id>/hard-delete (permanent deletion)."""

    def test_hard_delete_template_no_history(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-11: Template with only Projected txns is permanently deleted."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            # Generate projected transactions.
            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods), scenario.id)
            db.session.commit()

            template_id = template.id
            txn_count = db.session.query(Transaction).filter_by(
                template_id=template_id,
            ).count()
            assert txn_count == 10

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template is gone.
            assert db.session.get(TransactionTemplate, template_id) is None

            # All linked transactions are gone.
            remaining = db.session.query(Transaction).filter_by(
                template_id=template_id,
            ).count()
            assert remaining == 0

    def test_hard_delete_template_no_transactions(self, app, auth_client, seed_user):
        """C-5A.5-11b: Template with zero transactions is permanently deleted."""
        with app.app_context():
            template = _create_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(TransactionTemplate, template_id) is None

    def test_hard_delete_template_with_history(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-12: Template with Paid txn is blocked and archived instead."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods_list = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods_list), scenario.id)
            db.session.commit()

            # Mark one transaction as Paid.
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            # Through the real seam, which writes the whole settlement record
            # in one act -- the day, the figure and how it is known (plan step
            # X-au-c3).  A bare status assign leaves a state the record's own
            # CHECKs refuse.
            status_seam.apply_status_change(
                txn, paid_status.id,
                settlement=settlement_if_settling(txn, paid_status.id),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data
            assert b"archived instead" in resp.data

            # Template still exists but is archived.
            db.session.refresh(template)
            assert template.is_active is False

            # The Paid transaction is untouched.
            db.session.refresh(txn)
            assert txn.status_id == paid_status.id
            assert txn.is_deleted is False

            # Projected transactions are soft-deleted.
            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            projected_remaining = db.session.query(Transaction).filter(
                Transaction.template_id == template.id,
                Transaction.status_id == projected_status.id,
                Transaction.is_deleted.is_(False),
            ).count()
            assert projected_remaining == 0

    def test_hard_delete_template_with_history_already_archived(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-12b: Already-archived template with Paid history stays archived without re-archiving."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods_list = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods_list), scenario.id)
            db.session.commit()

            # Mark one transaction as Paid.
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            # Through the real seam, which writes the whole settlement record
            # in one act -- the day, the figure and how it is known (plan step
            # X-au-c3).  A bare status assign leaves a state the record's own
            # CHECKs refuse.
            status_seam.apply_status_change(
                txn, paid_status.id,
                settlement=settlement_if_settling(txn, paid_status.id),
            )

            # Pre-archive the template.
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data

            # Template still exists, still archived.
            db.session.refresh(template)
            assert template.is_active is False

    def test_hard_delete_template_already_archived(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-13: Pre-archived template with no history is permanently deleted."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods_list = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods_list), scenario.id)
            db.session.commit()

            # Pre-archive via route (soft-deletes projected txns).
            auth_client.post(f"/templates/{template.id}/archive")
            db.session.refresh(template)
            assert template.is_active is False

            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template and all transactions are gone.
            assert db.session.get(TransactionTemplate, template_id) is None
            remaining = db.session.query(Transaction).filter_by(
                template_id=template_id,
            ).count()
            assert remaining == 0

    def test_hard_delete_template_idor(self, app, auth_client, seed_user):
        """C-5A.5-14: Hard-deleting another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()
            other_id = other["template"].id

            resp = auth_client.post(
                f"/templates/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Other user's template still exists.
            assert db.session.get(TransactionTemplate, other_id) is not None

    def test_hard_delete_template_received_income_blocked(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C21-1: An income template with a RECEIVED paycheck is archived, not deleted.

        End-to-end proof of the CRIT-05 fix.  Pre-fix:
        ``template_has_paid_history`` enumerated ``[DONE, SETTLED]``
        by ID and silently omitted ``RECEIVED`` -- the status every
        income paycheck is given on mark-done
        (``transactions.py:534-535``: ``if txn.is_income: status_id =
        ref_cache.status_id(StatusEnum.RECEIVED)``).  The guard fell
        through and the route at ``templates.py:615-618`` then
        permanently destroyed the paycheck while flashing "permanently
        deleted."  Post-fix the predicate filters on
        ``Status.is_settled`` (RECEIVED carries
        ``is_settled=True``), the guard fires correctly, and the
        archive-fallback runs.  Assertions cover all three observable
        post-conditions: template archived (not deleted), RECEIVED
        paycheck still present and untouched, and the flash text
        accurately says "archived instead" rather than the misleading
        "permanently deleted."
        """
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            received_status = db.session.query(Status).filter_by(name="Received").one()
            salary_cat = seed_user["categories"]["Salary"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type.id,
                name="Biweekly Paycheck",
                default_amount=Decimal("2000.00"),
            )
            db.session.add(template)
            db.session.flush()

            paycheck = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type.id,
                name="Biweekly Paycheck",
                estimated_amount=Decimal("2000.00"),
                status_id=received_status.id,
                # A settled row carries the whole record, resolved through the
                # one door a bare-built fixture uses (plan step X-au-c3).
                settled_on=seed_periods_today[0].start_date,
                **settlement_columns(
                    seed_periods_today[0].start_date, Decimal("2000.00"),
                ),
            )
            db.session.add(paycheck)
            db.session.commit()

            template_id = template.id
            paycheck_id = paycheck.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived instead" in resp.data
            # C21-8: the success "permanently deleted" flash must NOT
            # have fired.  The archive-fallback's own message
            # legitimately contains the substring "permanently deleted"
            # ("cannot be permanently deleted"), so check the literal
            # success-flash format instead.  Pre-fix the route would
            # have rendered exactly this success string while destroying
            # the paycheck.
            assert (
                b"Recurring transaction 'Biweekly Paycheck' permanently deleted"
                not in resp.data
            )

            # Template archived, not deleted.
            db.session.refresh(template)
            assert template.is_active is False
            assert db.session.get(TransactionTemplate, template_id) is not None

            # RECEIVED paycheck preserved with original amount and status.
            refreshed = db.session.get(Transaction, paycheck_id)
            assert refreshed is not None
            assert refreshed.status_id == received_status.id
            assert refreshed.is_deleted is False
            # Hand-verified: original actual_amount of $2000.00 is intact
            # (Decimal from string per coding standards).
            assert refreshed.settled_amount == Decimal("2000.00")

    def test_hard_delete_template_bulk_delete_skips_settled_rows(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """C21-5: Even if the predicate is bypassed, the bulk delete spares settled rows.

        Defense in depth (CRIT-05 / E-22): the predicate fix above
        catches every settled status, but the destructive route is
        constrained additionally to ``Status.is_settled = False`` rows
        so a future regression of the predicate, a race window between
        the guard and the delete, or a different caller that bypasses
        the guard cannot physically destroy settled financial history.
        This test forces the bypass scenario by monkey-patching
        ``template_has_paid_history`` to return False even when a
        RECEIVED row exists, then asserts the post-condition: the
        settled row is still present after the route returns.

        ``Transaction.template_id`` is a FK with ``ON DELETE SET NULL``
        (``app/models/transaction.py:132-134``), so the surviving
        RECEIVED row has its ``template_id`` cleared but its financial
        data -- amount, status, period -- is intact.  The financial
        history that CRIT-05 was destroying is preserved.
        """
        with app.app_context():
            # Mix: one Projected + one RECEIVED on the same income template.
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            received_status = db.session.query(Status).filter_by(name="Received").one()
            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            salary_cat = seed_user["categories"]["Salary"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type.id,
                name="Paycheck Bypass Scenario",
                default_amount=Decimal("1500.00"),
            )
            db.session.add(template)
            db.session.flush()

            received_paycheck = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type.id,
                name="Past Paycheck",
                estimated_amount=Decimal("1500.00"),
                status_id=received_status.id,
                # A settled row carries the whole record, resolved through the
                # one door a bare-built fixture uses (plan step X-au-c3).
                settled_on=seed_periods_today[0].start_date,
                **settlement_columns(
                    seed_periods_today[0].start_date, Decimal("1500.00"),
                ),
            )
            projected_paycheck = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type.id,
                name="Future Paycheck",
                estimated_amount=Decimal("1500.00"),
                status_id=projected_status.id,
            )
            db.session.add_all([received_paycheck, projected_paycheck])
            db.session.commit()

            template_id = template.id
            received_id = received_paycheck.id
            projected_id = projected_paycheck.id

            # Force the bypass: predicate lies and says "no history."
            # The defense-in-depth filter inside the route is what must
            # save the RECEIVED row.
            monkeypatch.setattr(
                "app.routes.templates.crud.archive_helpers.template_has_paid_history",
                lambda _template_id: False,
            )

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200

            # Settled row SURVIVES.  Its template_id is now NULL (FK
            # ON DELETE SET NULL) because the template itself was
            # deleted, but the financial data is intact.
            surviving = db.session.get(Transaction, received_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            assert surviving.settled_amount == Decimal("1500.00")

            # Non-settled (Projected) row was deleted by the route, as intended.
            assert db.session.get(Transaction, projected_id) is None

            # Template itself was deleted (the bypass path completed).
            assert db.session.get(TransactionTemplate, template_id) is None

    def test_list_separates_active_and_archived(self, app, auth_client, seed_user):
        """C-5A.5-15: List page shows active and archived in separate sections."""
        with app.app_context():
            active_1 = _create_template(seed_user, name="Active One", amount="100.00")
            active_2 = _create_template(seed_user, name="Active Two", amount="200.00")
            archived = _create_template(seed_user, name="Archived One", amount="300.00")
            archived.is_active = False
            db.session.commit()

            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active templates appear in the main table.
            assert "Active One" in html
            assert "Active Two" in html

            # Archived section exists with count indicator.
            assert "Archived (1)" in html
            assert "Archived One" in html

    def test_archive_label_in_flash(self, app, auth_client, seed_user, seed_periods_today):
        """C-5A.5-16: Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="Every Period")

            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods_list = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, GenerationSchedule.for_periods(template.user_id, periods_list), scenario.id)
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            # Must NOT contain the old terminology.
            assert b"deactivated" not in resp.data


# ── Due Day of Month Tests ──────────────────────────────────────────


class TestDueDayOfMonth:
    """Tests for due_day_of_month on template create/update."""

    def test_create_template_with_due_day(self, app, auth_client, seed_user, seed_periods_today):
        """POST template with Monthly pattern and due_day_of_month=1."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Rent w/ Due Day",
                "default_amount": "1200.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": "22",
                "due_day_of_month": "1",
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent w/ Due Day",
            ).one()
            assert template.recurrence_rule.due_day_of_month == 1

    def test_create_template_without_due_day(self, app, auth_client, seed_user, seed_periods_today):
        """POST template with Monthly pattern, no due_day -> None."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            auth_client.post("/templates", data={
                "name": "Rent No Due",
                "default_amount": "1200.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": "15",
            }, follow_redirects=True)

            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent No Due",
            ).one()
            assert template.recurrence_rule.due_day_of_month is None

    def test_update_template_add_due_day(self, app, auth_client, seed_user, seed_periods_today):
        """Update existing template to add due_day_of_month=15."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            # Create without due_day first.
            auth_client.post("/templates", data={
                "name": "Updatable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": "10",
            }, follow_redirects=True)

            template = db.session.query(TransactionTemplate).filter_by(
                name="Updatable",
            ).one()
            assert template.recurrence_rule.due_day_of_month is None

            # Update to add due_day.
            auth_client.post(f"/templates/{template.id}", data={
                "name": "Updatable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": "10",
                "due_day_of_month": "15",
            }, follow_redirects=True)

            db.session.refresh(template)
            assert template.recurrence_rule.due_day_of_month == 15

    def test_update_template_remove_due_day(self, app, auth_client, seed_user, seed_periods_today):
        """CLEARING the box removes it; NOT MENTIONING it leaves it alone.

        **Both wire states, because they are different requests** (plan step
        R7c-b, developer ruling 2026-08-15).  This asserted only the second and
        expected it to CLEAR -- while its own docstring said "empty string
        stripped by schema", which is not the payload it sent: it omitted the
        key entirely.  The distinction is not academic, because three real
        senders produce an absent key and none of them means "clear it":

        * the Due Day row is hidden for a cadence that anchors on a PAYCHECK,
          and ``recurrence_form.js`` disables it with the hiding -- a hidden
          control that still submitted was leaking a stale day into the column
          (caught by ``tests/manual/verify_recurrence_form.py``, which sees
          ``posted=['25']`` after the switch);
        * an amount-only PATCH mentions no recurrence key at all, and used to
          erase the stored due day on every one;
        * the form the user actually clears posts ``""``, which the field's
          ``allow_none`` keeps as a stated ``None`` -- and THAT clears.

        It is the same present-versus-absent rule ``starts_on`` and the closing
        bound already run on, so the three recurrence fields now agree rather
        than each carrying its own convention.  Making that rule a TYPE the
        schema emits once, instead of three hand-written ``in data`` reads, is
        its own step (ledger row **D36**).
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            # Create with due_day.
            auth_client.post("/templates", data={
                "name": "Removable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": "10",
                "due_day_of_month": "15",
            }, follow_redirects=True)

            template = db.session.query(TransactionTemplate).filter_by(
                name="Removable",
            ).one()
            assert template.recurrence_rule.due_day_of_month == 15

            # NOT MENTIONED: an amount-only shape, and a disabled control's.
            # The stored day is the user's and this submission says nothing
            # about it.
            auth_client.post(f"/templates/{template.id}", data={
                "name": "Removable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
            }, follow_redirects=True)

            db.session.refresh(template)
            assert template.recurrence_rule.due_day_of_month == 15

            # CLEARED: what the form posts when the user empties the box.  The
            # field is ``allow_none``, so the empty string survives
            # ``_normalize_empty_inputs`` as a stated ``None``.
            auth_client.post(f"/templates/{template.id}", data={
                "name": "Removable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "due_day_of_month": "",
            }, follow_redirects=True)

            db.session.refresh(template)
            assert template.recurrence_rule.due_day_of_month is None


# ── Envelope/Income Cross-Field Validation Tests (Phase 2) ──────────


class TestEnvelopeIncomeRejection:
    """Phase 2 of the carry-forward aftermath plan: reject is_envelope=True
    on income templates at the Marshmallow input boundary.

    Mirrors the spec in
    ``docs/carry-forward-aftermath-implementation-plan.md`` Phase 2.
    Schema-layer coverage and exhaustive partial-update edge cases
    live in ``tests/test_routes/test_template_flags.py``
    (``TestEnvelopeOnlyOnExpenseSchema`` and
    ``TestTrackingExpenseOnlyValidation``).  These three tests serve
    as the canonical end-to-end checkpoints for Phase 2.
    """

    def test_post_income_template_with_envelope_rejected(
        self, app, auth_client, seed_user,
    ):
        """POST /templates with income type + is_envelope=on rejects.

        After Phase 2, the cross-field schema validator catches this
        combination at the input boundary; the route surfaces the
        validator's specific message instead of the generic prompt.
        Verifies the rejection by asserting the actionable flash and
        confirming no template row was persisted.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Phase2 Bad Income Envelope",
                "default_amount": "100.00",
                "category_id": category.id,
                "transaction_type_id": income_type.id,
                "account_id": seed_user["account"].id,
                "is_envelope": "on",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert (
                b"Purchase tracking is only available for expense templates"
                in resp.data
            )

            count = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Phase2 Bad Income Envelope",
            ).count()
            assert count == 0

    def test_post_expense_template_with_envelope_succeeds(
        self, app, auth_client, seed_user,
    ):
        """POST /templates with expense type + is_envelope=on succeeds.

        Positive control for the cross-field rule: the same checkbox
        on the supported transaction type creates the template and
        sets ``is_envelope = True``.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Phase2 Good Expense Envelope",
                "default_amount": "150.00",
                "category_id": category.id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
                "is_envelope": "on",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data
            assert (
                b"Purchase tracking is only available"
                not in resp.data
            )

            template = db.session.query(TransactionTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Phase2 Good Expense Envelope",
            ).one()
            assert template.is_envelope is True
            assert template.transaction_type_id == expense_type.id

    def test_patch_envelope_to_true_on_income_template_rejected(
        self, app, auth_client, seed_user,
    ):
        """POST /templates/<id> flipping is_envelope=on for an income template rejects.

        Reproduces the plan's PATCH scenario: an existing income
        template (envelope=False) gets a form submission that ticks
        the envelope checkbox without changing the type.  The schema
        validator catches the resulting state and the template is
        unchanged.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            category = seed_user["categories"]["Rent"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Phase2 Income Template",
                default_amount=Decimal("250.00"),
                is_envelope=False,
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Phase2 Income Template",
                "default_amount": "250.00",
                "category_id": category.id,
                "transaction_type_id": income_type.id,
                "account_id": seed_user["account"].id,
                "is_envelope": "on",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert (
                b"Purchase tracking is only available for expense templates"
                in resp.data
            )

            db.session.refresh(template)
            assert template.is_envelope is False
            assert template.transaction_type_id == income_type.id


class TestRecurrenceCellLock:
    """C7 source-level locks for the ``recurrence_cell`` macro.

    The macro's whole history is a template computing what a service should
    have.  It first compared ``rr.pattern.name`` strings through an
    intermediate ``pname`` variable; the mobile-followup commit 7 (F-8) rewired
    those onto the ``REC_*`` integer id globals; the polyglot cleanup
    (TPLB/TPL-07) merged two byte-identical copies into one shared macro.  Each
    fixed the spelling and left the shape.

    **Plan step R7a removed the shape.**  The phrase is produced by
    ``app.services.recurrence.describe`` from what a recurrence MEANS -- one
    function over ``(interval_n, unit)`` -- and the macro receives it already
    worded.  So the lock is no longer "compare ids, not names"; it is that the
    macro reads NOTHING but its argument.  That is what keeps it alive through
    plan step R7c, which drops every column the old branches read.
    """

    _MACRO_PATH = ("app", "templates", "_recurrence_macros.html")

    # Every site that renders the cell: the two active sections of the
    # swappable body, and the Archived drawer on the full page.  The former
    # transfers/list.html was retired when /transfers folded into /templates.
    _RENDER_SITE_PATHS = (
        ("templates/list.html", ("app", "templates", "templates", "list.html")),
        (
            "templates/_recurring_body.html",
            ("app", "templates", "templates", "_recurring_body.html"),
        ),
    )

    # Identifiers whose presence in the macro BODY would mean the template had
    # started reading the recurrence rule again, paired with what each one
    # costs.  ``pattern`` covers ``pattern_id``, ``pattern.name`` and the
    # ``REC_*`` comparisons in one.
    _FORBIDDEN_IN_BODY = (
        ("pattern", "the closed pattern set, which plan step R7c drops"),
        ("day_of_month", "a rule column, which plan step R7c drops"),
        ("month_of_year", "a rule column, which plan step R7c drops"),
        ("interval_n", "a rule column the describer already reads"),
        ("recurrence_rule", "the rule itself; the macro takes a description"),
        (".name", "a ref-table name string used for display"),
    )

    def _read_template_source(self, parts):
        """Return the contents of a template file under ``app/templates``."""
        import pathlib  # pylint: disable=import-outside-toplevel

        path = pathlib.Path(__file__).resolve().parents[2].joinpath(*parts)
        return path.read_text(encoding="utf-8")

    def _macro_body(self):
        """Return the ``recurrence_cell`` macro's body, without its comment.

        Scoped to the body deliberately: the file's leading ``{# ... #}`` block
        NAMES the identifiers the body must not contain, because explaining
        what was removed is the point of it.  A whole-file scan would therefore
        fail on its own documentation, and the usual fix -- deleting the
        explanation -- is the wrong one.
        """
        src = self._read_template_source(self._MACRO_PATH)
        start = src.index("{% macro recurrence_cell(")
        end = src.index("{% endmacro %}", start)
        return src[start:end]

    def test_the_extractor_finds_the_real_macro_body(self):
        """The body scan is not vacuous.

        Without this, a mis-sliced extractor would return an empty string and
        every forbidden-identifier assertion below would pass while checking
        nothing -- the exact failure mode this arc's reviews keep finding in
        controls that were never shown to fire.
        """
        body = self._macro_body()

        assert "description.cadence" in body, (
            "the extracted body does not contain the phrase the macro renders; "
            "the slice bounds are wrong, so the locks below check nothing"
        )
        assert "One-time" in body, (
            "the extracted body does not contain the rule-less branch"
        )

    def test_the_macro_reads_nothing_but_its_argument(self):
        """The cell displays a produced value; it does not compute one.

        Locks plan step R7a.  Any of these identifiers reappearing means the
        template has gone back to deriving the phrase from the rule's columns
        -- which is both the "templates display, never compute" violation and
        a surface that breaks the moment plan step R7c drops those columns.
        """
        body = self._macro_body()

        for identifier, why in self._FORBIDDEN_IN_BODY:
            assert identifier not in body, (
                f"the recurrence_cell macro body reads {identifier!r} "
                f"({why}); it must render only the RecurrenceDescription it "
                "is passed"
            )

    def test_every_render_site_imports_the_shared_macro(self):
        """All four cells come from one macro, imported without context.

        The macro lives once in ``_recurrence_macros.html``; locking the import
        keeps a render site from re-inlining a private copy that could diverge.
        ``with context`` is deliberately ABSENT: it was required while the
        else-branch read the ``recurrence_pattern_labels`` context processor,
        and plan step R7a deleted both, so the macro now reads nothing from the
        render context at all.
        """
        for label, parts in self._RENDER_SITE_PATHS:
            src = self._read_template_source(parts)

            assert (
                'from "_recurrence_macros.html" import recurrence_cell' in src
            ), (
                f"{label} must import recurrence_cell from "
                "_recurrence_macros.html, not define its own copy."
            )
            assert "import recurrence_cell with context" not in src, (
                f"{label} imports recurrence_cell with context; the macro "
                "reads nothing from the context since plan step R7a, and the "
                "clause would hide a re-introduced context dependency."
            )


class TestTheEndsControlIsTheFirstWriterOfMaxOccurrences:
    """Plan step R7b-3: a rule can stop after a COUNT, and the form says so.

    ``budget.recurrence_rules.max_occurrences`` had been read by the
    occurrence walk since plan step R3 and written by NOTHING -- 0 of the 46
    live production rules carried one (measured 2026-08-13).  The "Ends"
    control is its first author, and it is ONE control for a bound with three
    shapes, so ``ck_recurrence_rules_single_end_bound`` is expressed by the
    form's shape rather than refused after it.
    """

    def _create(self, auth_client, seed_user, name, **extra):
        """POST a recurring expense template and return the response.

        Args:
            auth_client: The signed-in client.
            seed_user: The seeded owner fixture.
            name: The template's name.
            **extra: Additional form keys, typically an end-bound payload.

        Returns:
            The Flask test response.
        """
        txn_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        return auth_client.post("/templates", data={
            "name": name,
            "default_amount": "100.00",
            "category_id": seed_user["categories"]["Rent"].id,
            "transaction_type_id": txn_type.id,
            "account_id": seed_user["account"].id,
            **cadence_payload(),
            **extra,
        }, follow_redirects=True)

    def test_a_count_bound_reaches_the_column_and_stops_generation(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Ends after 3" writes the count AND generates exactly three rows.

        The column write alone would prove only that a value landed; what
        makes the control mean anything is that the occurrence walk stops --
        ``seed_periods_today`` builds ten biweekly periods, so an unbounded
        every-paycheck rule generates ten.
        """
        with app.app_context():
            resp = self._create(
                auth_client, seed_user, "Three Payments",
                **end_bound_payload(EndsAfterOccurrences(count=3)),
            )
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Three Payments",
            ).one()
            rule = template.recurrence_rule
            assert rule.max_occurrences == 3
            assert rule.end_date is None

            txns = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).all()
            assert len(txns) == 3

    def test_a_date_bound_still_reaches_its_own_column(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The shape that already existed is unmoved by gaining a sibling."""
        with app.app_context():
            # After today's period (index 4), so the rule this creates
            # can reach it -- see the comment on
            # ``_TODAYS_PERIOD_INDEX``.
            stop = seed_periods_today[_TODAYS_PERIOD_INDEX + 2].start_date
            self._create(
                auth_client, seed_user, "Until Then",
                **end_bound_payload(EndsOnDate(on=stop)),
            )

            rule = db.session.query(TransactionTemplate).filter_by(
                name="Until Then",
            ).one().recurrence_rule
            assert rule.end_date == stop
            assert rule.max_occurrences is None

    def test_the_unbounded_shape_writes_neither_column(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Never" is a positive statement, and it stores as both NULL."""
        with app.app_context():
            self._create(
                auth_client, seed_user, "Forever", **end_bound_payload(),
            )

            rule = db.session.query(TransactionTemplate).filter_by(
                name="Forever",
            ).one().recurrence_rule
            assert rule.end_date is None
            assert rule.max_occurrences is None

    def test_choosing_a_date_and_leaving_it_blank_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Refused rather than read as "never", which would leave a bill running.

        A user who picked "on a date" meant to STOP the recurrence; taking the
        blank box as indefinite would silently do the opposite of what they
        asked for.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Blank Date",
                recurrence_end_mode="on_date",
                end_date="",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Blank Date",
            ).one_or_none() is None

    def test_choosing_a_count_and_leaving_it_blank_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The count shape's own half of the refusal above."""
        with app.app_context():
            self._create(
                auth_client, seed_user, "Blank Count",
                recurrence_end_mode="after_occurrences",
                max_occurrences="",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Blank Count",
            ).one_or_none() is None

    def test_a_zero_count_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``ck_recurrence_rules_positive_max_occurrences``, at the door.

        A count of zero names no occurrence at all.  The number box carries
        ``min="1"``, and a browser that honours it is not a validator.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Zero Count",
                recurrence_end_mode="after_occurrences",
                max_occurrences="0",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Zero Count",
            ).one_or_none() is None

    def test_an_unknown_mode_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A hand-assembled POST naming no shape has nothing to save.

        The dispatch over the closed set IS the validation; there is no
        second statement of which shapes exist for it to disagree with.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Bogus Mode",
                recurrence_end_mode="whenever",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Bogus Mode",
            ).one_or_none() is None

    def test_an_edit_can_move_a_rule_from_one_shape_to_the_other(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both columns cannot be set, because the bound is ONE value.

        Editing count -> date does not merge the two: it REPLACES the shape,
        so the column the old shape wrote is cleared in the same write.  A
        rule carrying both is what ``ck_recurrence_rules_single_end_bound``
        refuses, and this is the edit that would have produced it.
        """
        with app.app_context():
            stop = seed_periods_today[_TODAYS_PERIOD_INDEX + 3].start_date
            self._create(
                auth_client, seed_user, "Switcher",
                **end_bound_payload(EndsAfterOccurrences(count=2)),
            )
            template = db.session.query(TransactionTemplate).filter_by(
                name="Switcher",
            ).one()
            assert template.recurrence_rule.max_occurrences == 2

            auth_client.post(f"/templates/{template.id}", data={
                "name": "Switcher",
                "default_amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": template.transaction_type_id,
                "account_id": seed_user["account"].id,
                "version_id": template.version_id,
                **cadence_payload(),
                **end_bound_payload(EndsOnDate(on=stop)),
            }, follow_redirects=True)

            db.session.expire_all()
            rule = db.session.get(
                TransactionTemplate, template.id,
            ).recurrence_rule
            assert rule.end_date == stop
            assert rule.max_occurrences is None

    def test_an_edit_that_states_no_bound_leaves_the_stored_one_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An absent mode is "not mine to state", never "ends never".

        What a loan payment's disabled control posts, and what an amount-only
        PATCH posts.  Reading it as "never" would clear a stop the user set
        without ever showing them the control.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Keeper",
                **end_bound_payload(EndsAfterOccurrences(count=4)),
            )
            template = db.session.query(TransactionTemplate).filter_by(
                name="Keeper",
            ).one()

            auth_client.post(f"/templates/{template.id}", data={
                "name": "Keeper renamed",
                "default_amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": template.transaction_type_id,
                "account_id": seed_user["account"].id,
                "version_id": template.version_id,
                **cadence_payload(states_a_start=False),
            }, follow_redirects=True)

            db.session.expire_all()
            rule = db.session.get(
                TransactionTemplate, template.id,
            ).recurrence_rule
            assert rule.max_occurrences == 4
            assert rule.end_date is None

    def test_an_edit_can_state_a_deliberate_never_and_clear_a_stored_bound(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The third case of present-vs-absent, and the one that CLEARS.

        "Never" is a positive statement and must remove a stored stop; an
        ABSENT mode must not.  Both are asserted, because the whole value of
        keeping them distinguishable is that they do opposite things, and a
        test of only one would pass on a design that conflated them.
        """
        with app.app_context():
            self._create(
                auth_client, seed_user, "Unbounded Again",
                **end_bound_payload(EndsAfterOccurrences(count=5)),
            )
            template = db.session.query(TransactionTemplate).filter_by(
                name="Unbounded Again",
            ).one()
            assert template.recurrence_rule.max_occurrences == 5

            auth_client.post(f"/templates/{template.id}", data={
                "name": "Unbounded Again",
                "default_amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": template.transaction_type_id,
                "account_id": seed_user["account"].id,
                "version_id": template.version_id,
                **cadence_payload(),
                **end_bound_payload(),
            }, follow_redirects=True)

            db.session.expire_all()
            rule = db.session.get(
                TransactionTemplate, template.id,
            ).recurrence_rule
            assert rule.max_occurrences is None
            assert rule.end_date is None

    def test_the_transfer_create_form_renders_the_control_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both kinds share the partial, so both must show the bound.

        The transfer form is the one that also has a LOCKED case, so a
        regression there would be invisible to the transaction form's tests.
        """
        with app.app_context():
            resp = auth_client.get("/transfers/new")

            assert b'name="recurrence_end_mode"' in resp.data
            assert b'name="max_occurrences"' in resp.data
            for kind in END_BOUND_KINDS:
                assert f'value="{kind.token}"'.encode() in resp.data

    def test_the_form_renders_every_shape_the_dispatch_accepts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The offer set reaches the page, so every shape is choosable.

        Derived from the same closed tuple ``end_bound_from_token`` dispatches
        over, so a shape that renders is a shape that saves -- the property
        plan step R7b-2 gave the cadence controls, applied to the bound.
        """
        with app.app_context():
            resp = auth_client.get("/templates/new")

            assert b'name="recurrence_end_mode"' in resp.data
            assert b'name="max_occurrences"' in resp.data
            for kind in END_BOUND_KINDS:
                assert f'value="{kind.token}"'.encode() in resp.data


def _template_with_starts_on(seed_user, txn_type, starts_on):
    """Create a recurring EXPENSE template whose rule states a first occurrence.

    A transaction template rather than a transfer, deliberately: nothing
    derives a transaction template's validity window, so these cases exercise
    the ordinary authored path rather than the locked one.

    Args:
        seed_user: The seeded owner fixture.
        txn_type: The Expense transaction type row.
        starts_on: The rule's first occurrence.  For this paycheck-space
            cadence the write door normalises it onto the payday of the
            paycheck it falls in, so a mid-period date here is stored as that
            paycheck's own opening day.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    rule = make_pattern_rule(
        seed_user["user"].id, "Every Period", starts_on=starts_on,
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=txn_type.id,
        recurrence_rule_id=rule.id,
        name="Bounded Expense",
        default_amount=Decimal("42.00"),
    )
    db.session.add(template)
    db.session.flush()
    return template


def _loan_payment_template(seed_user):
    """Create and flush a recurring LOAN PAYMENT transfer template.

    A loan payment is a transfer template carrying
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` -- decision
    B, and what ``is_loan_payment`` reads.  Its closing bound is the loan's
    projected payoff, written by ``loan_recurrence_sync``, which is what makes
    the "Ends" control on its form DERIVED rather than authored.

    Args:
        seed_user: The seeded owner fixture.

    Returns:
        The flushed :class:`~app.models.transfer_template.TransferTemplate`.
    """
    loan_account = create_loan_account(seed_user, db.session)
    rule = make_pattern_rule(
        seed_user["user"].id, "Monthly",
        fires_on_day=1, end_date=date(2030, 1, 1),
    )
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        recurrence_rule_id=rule.id,
        name="Loan Payment",
        default_amount=Decimal("500.00"),
        is_active=True,
    )
    template.settings = LoanPaymentSettings(derive_from_loan=False)
    db.session.add(template)
    db.session.commit()
    return template


class TestALoanPaymentsClosingBoundIsDerived:
    """Plan step R7b-3: the app owns a loan payment's stop, so the form does not.

    ``loan_recurrence_sync.sync_recurring_payment_bounds`` writes that rule's
    ``end_date`` from the loan's PROJECTED PAYOFF on every payoff-affecting
    edit.  A bound accepted from this form would therefore be discarded
    without a word the next time the loan changed -- so the control renders
    disabled, and a submission that states one anyway is REFUSED rather than
    silently dropped.

    Both halves are needed and neither is redundant: disabling is the
    affordance a user sees, and the refusal is the rule a crafted POST meets.
    """

    def test_the_control_renders_disabled_on_a_loan_payment(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The user is told where the value comes from rather than shown a box.

        A disabled control posts nothing, so the mode key arrives ABSENT --
        which the update path reads as "this form said nothing about the
        bound" and leaves the stored one alone.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)

            resp = auth_client.get(f"/transfers/{template.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            control = body[body.index('id="field-end-bound"'):]
            control = control[:control.index("</div>", control.index("</select>"))]
            assert 'id="recurrence_end_mode"' in control
            assert "disabled" in control
            assert "projected payoff" in body

    def test_a_crafted_submission_stating_a_bound_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Refused, and the stored bound is untouched.

        The refusal is what keeps the user's stated stop from being thrown
        away in silence -- and it is the door half of what makes a COUNT
        bound and the sync's DATE unable to meet on one row.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)
            before = template.recurrence_rule.end_date

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(),
                    **end_bound_payload(EndsAfterOccurrences(count=6)),
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"paid off" in resp.data
            db.session.expire_all()
            rule = db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule
            assert rule.max_occurrences is None
            assert rule.end_date == before

    def test_an_ordinary_edit_that_states_no_bound_still_saves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the refusal is on a STATED bound, not on loan payments.

        A user renaming their loan payment must not be blocked by a control
        their form does not show them.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)

            auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": "Renamed Payment",
                    "default_amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(states_a_start=False),
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).name == "Renamed Payment"


class TestACreateDoesNotBackfillClosedPayPeriods:
    """The create form's opening bound DEFAULTS, and the default is money.

    **The regression an adversarial review of plan step R7b-4 found.**  The
    control this step replaced was a ``<select>`` of pay periods with no empty
    option, preselecting the CURRENT period -- so every definition ever created
    carried an opening bound of "the paycheck I am in".  Replacing it with a
    date box that defaults to EMPTY silently changed that to "unbounded", and
    the create routes generate over ``GenerationSchedule.for_user`` -- every
    period the owner has, with no lower window bound -- so a rent template
    created today wrote projected debits into every pay period that had already
    closed.

    Asserted on the GENERATED ROWS rather than on the column, which is the
    whole point: the two create tests written with this step both checked
    ``start_date`` and would have passed against the regression.  The fixture
    puts today in period index 4, so four CLOSED periods exist to backfill
    into -- a suite whose fixture had no history could not see this at all.
    """

    def test_a_create_with_no_stated_start_generates_nothing_in_the_past(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Rows land from the current paycheck forward, never behind it."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None, "fixture must cover today"
            closed = [
                p for p in seed_periods_today
                if p.start_date < current.start_date
            ]
            assert closed, "fixture must have a closed period to backfill into"

            # POST what the FORM ITSELF renders, read off the page, rather
            # than a date this test chose.  That is what makes this a
            # regression guard on the DEFAULT: a hardcoded bound here would
            # pass against a form that had stopped rendering one, which is
            # precisely the defect -- measured, it did.
            form = auth_client.get("/templates/new").data.decode()
            control = form[form.index('id="field-starts-on"'):]
            control = control[:control.index("</div>", control.index("<input"))]
            rendered = re.search(r'value="([^"]*)"', control).group(1)

            resp = auth_client.post("/templates", data={
                "name": "Rent Created Today",
                "default_amount": "2000.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                "start_date": rendered,
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Rent Created Today")
                .one()
            )
            periods = {
                txn.pay_period_id
                for txn in db.session.query(Transaction).filter_by(
                    template_id=template.id,
                )
            }
            assert periods, "the template generated nothing at all"
            backfilled = {p.id for p in closed} & periods
            assert backfilled == set(), (
                f"created rows in {len(backfilled)} pay period(s) that closed "
                f"before today"
            )

    def test_the_create_form_renders_that_default_rather_than_an_empty_box(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The default is what the user SEES, so it is asserted on the page.

        The test above passes the date explicitly, which is what the rendered
        form posts -- so it is only a regression guard while the form really
        does render it.  This is the other half.

        **TODAY, not the current paycheck's payday**, since plan step R7c-b
        renamed the control's meaning: it asked for an opening BOUND and now
        asks when the thing first HAPPENS, and today is the honest reading of
        that for a MONTH or YEAR cadence.  Neither can backdate -- today is
        inside the current pay period, so the earliest row either produces is
        this one -- which is what the class as a whole is about.  See
        ``_recurrence_form_render.create_form_default_starts_on``.
        """
        with app.app_context():
            resp = auth_client.get("/templates/new")

            assert resp.status_code == 200
            body = resp.data.decode()
            control = body[body.index('id="field-starts-on"'):]
            control = control[:control.index("</div>", control.index("<input"))]
            assert f'value="{display_today().isoformat()}"' in control


class TestALoanPaymentsOpeningBoundIsDerived:
    """Plan step R7b-4: the app owns a loan payment's START, so the form does not.

    The exact mirror of :class:`TestALoanPaymentsClosingBoundIsDerived`, and it
    guards the more expensive half.  ``loan_recurrence_sync._sync_loan_cadence``
    writes that rule's ``start_date`` from the loan's FIRST CONTRACTUAL
    INSTALLMENT on every payoff-affecting edit, so a bound accepted from this
    form would be discarded without a word the next time the loan changed --
    and a bound accepted and KEPT would be worse: generation before origination
    is erased by the fold while the cash side still debits it, measured at
    ``$3,220.92`` of phantom payments on a mortgage closing one month out.

    **Which definitions lock is ``owns_validity_window``, not
    ``is_loan_payment``** (plan step R7b-4), and the difference is not
    academic: neither of the developer's real loan payments carries a
    ``loan_payment_settings`` row, so the older predicate answered False for
    both and the sibling class above was passing on a fixture that is not
    shaped like production.  These cases use the same fixture -- it satisfies
    both predicates -- and the three-arm test of the predicate itself is in
    ``tests/test_services/test_loan_recurrence_sync.py``.
    """

    def test_the_control_renders_disabled_on_a_loan_payment(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The user is told where the value comes from rather than shown a box.

        A disabled control posts nothing, so the key arrives ABSENT -- which
        the update path reads as "this form said nothing about the bound" and
        leaves the stored one alone.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)

            resp = auth_client.get(f"/transfers/{template.id}/edit")

            assert resp.status_code == 200
            body = resp.data.decode()
            control = body[body.index('id="field-starts-on"'):]
            control = control[:control.index("</div>", control.index("<input"))]
            assert 'id="starts_on"' in control
            assert "disabled" in control
            # The apostrophe arrives HTML-escaped since plan step R7c-b, which
            # renders the copy through a Jinja variable so the swappable form
            # can carry the same sentence as a data attribute.  Asserted on the
            # half that has no entity in it, rather than on ``&#39;``: the
            # subject is the WORDING, and pinning an escape spelling would go
            # red on a markup change that says nothing to the user.
            assert "first payment, and updated when the loan changes" in body

    def test_a_crafted_submission_stating_a_start_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Refused, and the stored opening bound is untouched.

        Reachable only by a crafted POST, because the control is disabled --
        and checked anyway for the reason the closing bound's twin is:
        disabling is the affordance, the refusal is the rule.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)
            before = template.recurrence_rule.starts_on

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(),
                    "starts_on": "2027-06-01",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"first installment" in resp.data
            db.session.expire_all()
            rule = db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule
            assert rule.starts_on == before

    def test_an_ordinary_edit_that_states_no_start_still_saves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the refusal is on a STATED bound, not on loan payments.

        A user renaming their loan payment must not be blocked by a control
        their form does not show them -- and the stored bound must survive the
        edit rather than being cleared by the absent key.

        The payload restates the rule's OWN cadence (monthly), which the form
        would: leaving the default every-paycheck one in place would change the
        unit as a side effect, and a paycheck-space rule's first occurrence is
        normalised onto a payday -- so the bound would move for a reason that
        has nothing to do with what this test measures.
        """
        with app.app_context():
            template = _loan_payment_template(seed_user)
            before = template.recurrence_rule.starts_on

            auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": "Renamed Loan Payment",
                    "default_amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    **cadence_payload(
                        unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                    ),
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            stored = db.session.get(TransferTemplate, template.id)
            assert stored.name == "Renamed Loan Payment"
            assert stored.recurrence_rule.starts_on == before


class TestALoanPaymentCannotBeMadeOneTime:
    """The refusal covers every loan payment, not only the settings-carrying ones.

    Clearing a loan payment's recurrence nulls ``recurrence_rule_id``, and that
    is how ``recurring_transfer_query.active_recurring_transfer_template``
    FINDS a loan's payment -- so the loan goes on amortizing with nothing
    projecting a payment against it, and its standing ``extra_principal`` (when
    it has one) stops being threaded into the balance seam.

    **The set this covers was measured wrong until plan step R7b-4.** The
    refusal asked ``is_loan_payment`` alone -- does this template carry
    ``LoanPaymentSettings`` -- and on a 2026-08-14 production clone NEITHER of
    the developer's real loan payments carries that row, so both mortgages were
    clearable. The guard now asks the UNION with
    ``loan_recurrence_sync.owns_validity_window``, and the second case below is
    the one that was live: it is the production shape, and it FAILS against the
    predicate this step replaced.
    """

    def test_a_settings_carrying_loan_payment_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The arm that already worked, kept so the union cannot lose it."""
        with app.app_context():
            template = _loan_payment_template(seed_user)
            rule_id = template.recurrence_rule_id

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "500.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    "recurrence_unit": "",
                    "recurrence_placement": "",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"repeats for the life of the loan" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule_id == rule_id

    def test_a_loan_payment_with_NO_settings_row_is_refused_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The production shape, and the one that was accepted before R7b-4.

        Identical to the case above except the template carries no
        ``LoanPaymentSettings`` -- which is how BOTH of the developer's real
        loan payments are stored. Its recurrence must survive the clear, or a
        mortgage silently leaves the forward plan.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            template = make_transfer_template(db.session, seed_user, loan)
            db.session.flush()
            assert template.settings is None, "fixture must have no settings row"
            rule_id = template.recurrence_rule_id

            resp = auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "200.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    "recurrence_unit": "",
                    "recurrence_placement": "",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"repeats for the life of the loan" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule_id == rule_id

    def test_an_ordinary_transfer_can_still_be_made_one_time(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the refusal is on LOAN payments, not on transfers.

        Without this the union could be satisfied by a guard that refused
        every clear, which would take a real affordance away from every
        savings contribution the user has.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Holiday Fund",
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.flush()

            auth_client.post(
                f"/transfers/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "200.00",
                    "from_account_id": template.from_account_id,
                    "to_account_id": template.to_account_id,
                    "version_id": template.version_id,
                    "recurrence_unit": "",
                    "recurrence_placement": "",
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).recurrence_rule_id is None


class TestAnEditStatesTheOpeningBoundOrSaysNothing:
    """PRESENT replaces, ABSENT leaves alone -- and empty is PRESENT.

    ``start_date`` is ``allow_none`` at the schema, so clearing the box arrives
    as a stated ``None`` that MUST overwrite a stored date, while a form that
    rendered the control disabled omits the key entirely and must leave the
    stored date alone.  ``_recurrence_form_helpers`` calls that asymmetry
    load-bearing, and it is: collapsing the two would make a loan edit erase
    the origination bound that keeps its payments from generating before the
    loan exists.

    The closing bound carries the identical pair of cases, which is what says
    the two bounds are one idea rather than two conventions.
    """

    def test_an_edit_that_states_no_start_leaves_the_stored_one_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An amount-only save does not touch a bound it never showed.

        Compared against what was STORED rather than against the date handed
        to the fixture: this is a paycheck-space cadence, so the write door
        normalises the requested date onto the payday of the paycheck that
        hosts it (and onto the schedule's opening payday when the date
        precedes it).  Asserting the requested date would grade the fixture's
        arithmetic instead of the edit.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            template = _template_with_starts_on(
                seed_user, txn_type, date(2026, 5, 7),
            )
            before = template.recurrence_rule.starts_on

            auth_client.post(
                f"/templates/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "42.00",
                    "category_id": template.category_id,
                    "transaction_type_id": txn_type.id,
                    "account_id": template.account_id,
                    "version_id": template.version_id,
                    **cadence_payload(states_a_start=False),
                },
                follow_redirects=True,
            )

            db.session.expire_all()
            stored = db.session.get(TransactionTemplate, template.id)
            assert stored.recurrence_rule.starts_on == before

    def test_a_cleared_box_keeps_the_stored_date_rather_than_nulling_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """There is no "no first occurrence" state left to clear TO.

        This asserted the opposite until plan step R7c-b, and the reversal is
        the step: an opening BOUND was optional, so clearing the box removed
        it, and a rule with none started with the schedule.  A first
        OCCURRENCE is what the rule repeats from and
        ``budget.recurrence_rules.starts_on`` is ``NOT NULL``, so the empty
        request has no answer to give.

        ``starts_on`` is not ``allow_none``, so ``_normalize_empty_inputs``
        DROPS an empty box rather than passing a present ``None`` -- which
        makes a cleared control and a locked one the same payload, and one
        meaning is all that payload needs: leave the stored date alone.  The
        create path has no stored date to leave, and refuses (see
        ``TestACreateDoesNotBackfillClosedPayPeriods``).
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            template = _template_with_starts_on(
                seed_user, txn_type, date(2026, 5, 7),
            )
            before = template.recurrence_rule.starts_on

            resp = auth_client.post(
                f"/templates/{template.id}",
                data={
                    "name": template.name,
                    "default_amount": "42.00",
                    "category_id": template.category_id,
                    "transaction_type_id": txn_type.id,
                    "account_id": template.account_id,
                    "version_id": template.version_id,
                    **cadence_payload(),
                    "starts_on": "",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.expire_all()
            stored = db.session.get(TransactionTemplate, template.id)
            assert stored.recurrence_rule.starts_on == before
            assert stored.default_amount == Decimal("42.00"), (
                "the rest of the edit must still have landed"
            )


class TestTheServerRendersTheEndsControlAlreadyCorrect:
    """The form is right BEFORE any script runs, and disabled means disabled.

    ``recurrence_form.js`` re-links the value inputs on every change, but the
    first render is the server's -- so a page whose script has not executed
    yet, or has failed, must still post exactly the shape it displays.  The
    same contract the two interval controls hold, which
    ``_recurrence_fields.html`` states for both.

    What this CANNOT see is what the script does afterwards: a control hidden
    by a class and one disabled by the script look identical in rendered HTML.
    That is ``tests/manual/verify_recurrence_form.py``'s, and plan step R7b-2
    shipped two defects of exactly that kind past a green suite.
    """

    def _selected_mode(self, body):
        """Return the token of the "Ends" option carrying ``selected``.

        Parsed structurally rather than matched as a substring, and an
        adversarial review of this step is why: the first version asserted a
        whitespace-normalised ``value="never" data-needs="" selected`` with an
        ``or 'value="never"' in control`` fallback -- and the fallback is true
        of every render, because the option is always emitted.  It could not
        fail for the selection it was named after, which is exactly the R7b-2
        defect the surrounding docstrings cite.

        Args:
            body: The decoded response body.

        Returns:
            The selected option's ``value``, or ``None`` when none carries
            ``selected`` -- which is itself a failure a caller should assert
            on, because a ``<select>`` with no selected option silently
            submits its FIRST.
        """
        start = body.index('id="recurrence_end_mode"')
        control = body[start:body.index("</select>", start)]
        for option in re.findall(r"<option\b[^>]*>", control):
            if re.search(r"\bselected\b", option):
                return re.search(r'value="([^"]*)"', option).group(1)
        return None

    def _input(self, body, element_id):
        """Return one input's tag text from a rendered page.

        Args:
            body: The decoded response body.
            element_id: The input's ``id``.

        Returns:
            The tag's source text.
        """
        start = body.index(f'id="{element_id}"')
        return body[body.rindex("<input", 0, start):body.index(">", start) + 1]

    def test_a_create_form_starts_unbounded_with_both_values_disabled(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Never" is selected, so neither value input may submit.

        An enabled box beside a mode that does not read it would post a value
        the chosen shape ignores -- and the shape a hand-assembled POST then
        names decides which of them the door believes.
        """
        with app.app_context():
            body = auth_client.get("/templates/new").data.decode()

            assert self._selected_mode(body) == NeverEnds.token
            assert "disabled" in self._input(body, "end_date")
            assert "disabled" in self._input(body, "max_occurrences")

    def test_an_edit_form_enables_only_the_stored_shapes_input(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A count-bounded rule prefills the count box and disables the date.

        The prefill comes from ``edit_form_end_bound``, which reads the two
        columns through the one seam rather than letting the template decide
        which shape a row holds.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            auth_client.post("/templates", data={
                "name": "Counted",
                "default_amount": "100.00",
                "category_id": seed_user["categories"]["Rent"].id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(),
                **end_bound_payload(EndsAfterOccurrences(count=7)),
            }, follow_redirects=True)
            template = db.session.query(TransactionTemplate).filter_by(
                name="Counted",
            ).one()

            body = auth_client.get(
                f"/templates/{template.id}/edit",
            ).data.decode()

            count_input = self._input(body, "max_occurrences")
            assert 'value="7"' in count_input
            assert "disabled" not in count_input
            assert "disabled" in self._input(body, "end_date")
            assert self._selected_mode(body) == EndsAfterOccurrences.token


class TestThePreviewHonoursTheClosingBound:
    """The preview must show what SAVING would produce, bound included.

    Its module docstring states that contract, and plan step R7b-3 broke it
    for one review cycle: the endpoint composed the bound through the
    submission door and defaulted the mode to "never", while the script sent
    the two VALUE keys and no mode.  So every preview was unbounded, and a
    user setting "ends on a date" was shown occurrences running past it -- on
    the one surface whose whole job is to say when a commitment stops.

    Neither the frozen oracle nor the 46-rule round trip could see it: both
    instruments read a rule, and this defect lived in a query string.
    """

    def _preview(self, auth_client, seed_user, **extra):
        """GET the preview fragment for an every-paycheck cadence.

        ``starts_on`` rides with the cadence because the endpoint has nothing
        to preview without one since plan step R7c-b: a rule cannot be authored
        without stating when it first happens, so a query naming no start gets
        the same muted line a query naming no cadence does.

        Args:
            auth_client: The signed-in client.
            seed_user: The seeded owner fixture.
            **extra: Additional query args, typically the bound's controls.

        Returns:
            The decoded fragment body.
        """
        return auth_client.get("/templates/preview-recurrence", query_string={
            "recurrence_unit": str(
                ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
            ),
            "recurrence_placement": str(
                ref_cache.period_placement_id(
                    PeriodPlacementEnum.CONTAINING_DATE,
                ),
            ),
            "interval_n": "1",
            "starts_on": display_today().isoformat(),
            **extra,
        }).data.decode()

    def test_an_unbounded_preview_lists_occurrences(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: without a bound there are periods to show."""
        with app.app_context():
            assert "occurrences" in self._preview(auth_client, seed_user)

    def test_a_date_bound_narrows_the_preview(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A bound BEFORE the schedule opens leaves nothing to show.

        The unbounded case above lists occurrences from the same schedule, so
        the difference is the bound and nothing else.
        """
        with app.app_context():
            body = self._preview(
                auth_client, seed_user,
                recurrence_end_mode="on_date",
                end_date="2000-01-01",
            )

            assert "No matching periods found" in body

    def test_a_count_bound_narrows_the_preview(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"After 1 occurrence" shows exactly one, where unbounded shows five.

        The count is applied from the rule's FIRST OCCURRENCE, which the query
        states, so a one-occurrence rule fires once ON that date and stops.
        The preview opens the list there too (plan step R7c-b: showing five
        occurrences the user cannot see the start of answers a question nobody
        asked), so the single occurrence is visible rather than already past.

        It asserted "No matching periods found" until that step, when the
        anchor was the SCHEDULE's opening rather than an authored date -- the
        one occurrence then landed on the first payday, behind today's window.
        The count is what this measures either way: read against the unbounded
        case above, which lists five from the very same schedule, the
        difference is the bound and nothing else.  Before the mode reached this
        endpoint at all, both answered identically.
        """
        with app.app_context():
            body = self._preview(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences",
                max_occurrences="1",
            )

            assert "Next 1 occurrences" in body
            assert "Next 5 occurrences" in self._preview(
                auth_client, seed_user,
            ), "the unbounded control must differ, or this measures nothing"

    def test_a_query_stating_both_bounds_is_a_muted_line_not_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A hand-crafted query naming two bounds is refused, not crashed.

        It reaches the SUBMISSION door, so it earns the user-input refusal
        rather than being reported as a row written around the CHECK -- there
        is no row.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    "recurrence_unit": str(
                        ref_cache.recurrence_unit_id(
                            RecurrenceUnitEnum.PERIOD,
                        ),
                    ),
                    "recurrence_placement": str(
                        ref_cache.period_placement_id(
                            PeriodPlacementEnum.CONTAINING_DATE,
                        ),
                    ),
                    "recurrence_end_mode": "whenever",
                },
            )

            assert resp.status_code == 200
            assert b"No preview for this cadence" in resp.data


class TestTheBoundsRefusalsReachTheUser:
    """A refusal nobody reads is copy, not a refusal.

    Plan step R7b-3 authored three sentences for the "Ends" control and
    allowlisted none of them, so a user whose bound was refused was redirected
    to a blank form and told to "correct the highlighted errors" -- on a page
    that highlights nothing.  It is the R7b-2 defect this project already paid
    for, and the gate written against it could not see these because they live
    in a ``@post_load`` hook that ``Schema.validate`` skips.
    """

    def _post(self, auth_client, seed_user, **extra):
        """POST a recurring expense template and return the followed response.

        Args:
            auth_client: The signed-in client.
            seed_user: The seeded owner fixture.
            **extra: Additional form keys.

        Returns:
            The decoded response body.
        """
        txn_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        return auth_client.post("/templates", data={
            "name": "Refused",
            "default_amount": "100.00",
            "category_id": seed_user["categories"]["Rent"].id,
            "transaction_type_id": txn_type.id,
            "account_id": seed_user["account"].id,
            **cadence_payload(),
            **extra,
        }, follow_redirects=True).data.decode()

    def test_a_blank_date_says_which_control_to_fill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Not the generic prompt: the sentence names the box and the way out."""
        with app.app_context():
            body = self._post(
                auth_client, seed_user,
                recurrence_end_mode="on_date", end_date="",
            )

            assert "Choose the date this stops repeating" in body
            assert GENERIC_VALIDATION_FLASH not in body

    def test_a_blank_count_says_which_control_to_fill(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The count shape's half of the same promise."""
        with app.app_context():
            body = self._post(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences", max_occurrences="",
            )

            assert "Enter how many times this repeats" in body
            assert GENERIC_VALIDATION_FLASH not in body

    def test_a_zero_count_gets_the_SHAPES_own_message(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The type's refusal, not marshmallow's "Must be greater than...".

        The schema carries no lower bound on the count precisely so this
        reaches ``EndsAfterOccurrences.__post_init__``, whose message names
        the control and states the offending value.
        """
        with app.app_context():
            body = self._post(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences", max_occurrences="0",
            )

            assert "at least 1, not 0" in body

    def test_a_count_too_large_for_the_column_is_a_400_not_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An unstorable count is a designed refusal, not a database error.

        ``max_occurrences`` is a Postgres ``integer``; a larger value dies at
        the flush with ``NumericValueOutOfRange``, which is the ``MarkDoneSchema``
        defect one schema over.  The schema's upper bound is what keeps it a
        refusal the form can report.
        """
        with app.app_context():
            self._post(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences",
                max_occurrences="2147483648",
            )

            assert db.session.query(TransactionTemplate).filter_by(
                name="Refused",
            ).one_or_none() is None
