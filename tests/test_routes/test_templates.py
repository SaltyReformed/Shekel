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

from datetime import date
from decimal import Decimal

import pytest

from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType, RecurrencePattern, Status, TransactionType,
)
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services import account_service
from tests._test_helpers import create_loan_account


# ── Helpers ──────────────────────────────────────────────────────────


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
        pattern = db.session.query(RecurrencePattern).filter_by(name=pattern_name).one()
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=pattern.id,
            interval_n=1,
            offset_periods=0,
        )
        db.session.add(rule)
        db.session.flush()

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


def _pattern_id(name="Every Period"):
    """Return a recurrence pattern's id by name (display lookup, test-only)."""
    return db.session.query(RecurrencePattern).filter_by(name=name).one().id


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
    recurrence_engine.generate_for_template(template, periods, scenario.id)
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
            assert b'name="recurrence_pattern"' in resp.data

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
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()

            resp = auth_client.post("/templates", data={
                "name": "Rent Payment",
                "default_amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(every_period.id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent Payment"
            ).one()
            assert template.recurrence_rule is not None

            # every_period pattern generates 1 transaction per period;
            # seed_periods_today creates 10 biweekly periods
            txns = db.session.query(Transaction).filter_by(
                template_id=template.id
            ).all()
            assert len(txns) == 10
            # Each transaction maps to a distinct period
            period_ids = {txn.pay_period_id for txn in txns}
            assert len(period_ids) == 10

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
        """POST /templates/<id> with invalid data shows error."""
        with app.app_context():
            template = _create_template(seed_user)

            resp = auth_client.post(f"/templates/{template.id}", data={
                "day_of_month": "0",  # Fails Range(min=1, max=31).
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

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
                "recurrence_pattern": str(_pattern_id()),
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
                "recurrence_pattern": str(_pattern_id()),
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
                "recurrence_pattern": str(_pattern_id()),
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
                "recurrence_pattern": str(_pattern_id()),
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
            from app.services.recurrence_engine import (
                is_salary_linked_template,
            )
            from tests._test_helpers import make_salary_profile
            template = _create_template(seed_user, txn_type="Income")
            assert is_salary_linked_template(template.id) is False

            profile = make_salary_profile(seed_user, db.session)
            profile.template_id = template.id
            profile.is_active = True
            db.session.commit()
            assert is_salary_linked_template(template.id) is True

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
            recurrence_engine.generate_for_template(template, periods, scenario.id)
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
                "recurrence_pattern": str(_pattern_id()),
                "conflict_apply": "1",
                f"conflict_decision_{txn_id}": "use",
            }, follow_redirects=True)
            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.is_deleted is False
            assert reloaded.estimated_amount == Decimal("1400.00")
            assert reloaded.name == "Apartment Rent"

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
                template, periods, scenario.id,
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
                template, periods, scenario.id,
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
                template, periods, scenario.id,
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
                template, periods, scenario.id,
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
            recurrence_engine.generate_for_template(template, periods, scenario.id)
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
            recurrence_engine.generate_for_template(template, periods, scenario.id)
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
        """Preview for monthly pattern returns occurrence list."""
        with app.app_context():
            monthly = db.session.query(RecurrencePattern).filter_by(
                name="Monthly"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence"
                f"?recurrence_pattern={monthly.id}&day_of_month=15"
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data or b"No matching" in resp.data

    def test_preview_the_retired_once_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The surviving ``Once`` ``ref`` row previews as UNKNOWN, not blank.

        It used to have its own "No preview for this pattern" branch beside
        the empty-submission one, because it was a modelled pattern that did
        not recur.  Plan step R2e-3 retired the enum member and kept the row
        (ruling R-R11), so the row is now simply a pattern the application
        does not model -- the same answer as any other unmodelled id, and the
        honest one: it reached the preview at all only through hand-crafted
        input, and saying "no preview" would read as "this is fine".
        """
        with app.app_context():
            once = db.session.query(RecurrencePattern).filter_by(
                name="Once"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence?recurrence_pattern={once.id}"
            )
            assert resp.status_code == 200
            assert b"Unknown pattern" in resp.data

    def test_preview_unknown_pattern(self, app, auth_client, seed_user, seed_periods_today):
        """Preview for unknown pattern ID returns unknown message."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence?recurrence_pattern=999999"
            )
            assert resp.status_code == 200
            assert b"Unknown pattern" in resp.data

    def test_preview_no_pattern(self, app, auth_client, seed_user, seed_periods_today):
        """Preview with no pattern parameter returns no-preview message."""
        with app.app_context():
            resp = auth_client.get("/templates/preview-recurrence")
            assert resp.status_code == 200
            assert b"No preview" in resp.data

    @pytest.mark.parametrize(
        ("pattern_name", "query"),
        [
            # Live 500s before plan step R4a: the first two raised
            # ``ValueError`` out of the matcher it deleted, the third out of
            # the authoring seam.
            ("Annual", "month_of_year=13&day_of_month=15"),
            ("Monthly", "day_of_month=-5"),
            ("Every N Periods", "interval_n=0"),
            # Worse than a crash: 200 with a silently clamped or modulo-wrapped
            # date the user never named.
            ("Quarterly", "month_of_year=99&day_of_month=15"),
            ("Monthly", "day_of_month=32"),
            ("Monthly", "day_of_month=0"),
        ],
    )
    def test_preview_refuses_out_of_domain_arguments_without_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
        pattern_name, query,
    ):
        """Unbounded query args answer a muted line, never a stack trace.

        This endpoint reads ``interval_n`` / ``day_of_month`` /
        ``month_of_year`` straight from ``request.args``.  The two form
        schemas bound them (``Range(min=1, max=31)`` / ``(1, 12)``) and the
        columns carry ``ck_recurrence_rules_dom`` /
        ``ck_recurrence_rules_moy`` / ``ck_recurrence_rules_positive_interval``
        -- but nothing bounded THIS path, and it is reachable by anyone signed
        in.  Three were measured as live 500s and three answered 200 with a
        date the rule never named; the comments above say which is which.
        Plan step R4a's resolution door refuses all six by mirroring the
        columns' own domains.
        """
        with app.app_context():
            pattern = db.session.query(RecurrencePattern).filter_by(
                name=pattern_name
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence"
                f"?recurrence_pattern={pattern.id}&{query}"
            )

            assert resp.status_code == 200, (
                f"{pattern_name} with {query} answered {resp.status_code}"
            )
            assert b"No preview for this pattern" in resp.data

    def test_preview_ignores_an_unparseable_end_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?end_date=garbage`` previews the unbounded rule, it does not 500.

        The one unvalidated argument the resolution door cannot refuse:
        ``date.fromisoformat`` runs BEFORE the seam, so its ``ValueError``
        never reaches ``RecurrenceResolutionError``.  A neutral review of plan
        step R4a found it after the route's docstring had already claimed the
        whole class was closed.  An unparseable bound is dropped rather than
        refused -- see ``_recurrence_preview._submitted_end_date``.
        """
        with app.app_context():
            pattern = db.session.query(RecurrencePattern).filter_by(
                name="Monthly"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence"
                f"?recurrence_pattern={pattern.id}&day_of_month=15"
                f"&end_date=garbage"
            )

            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_every_period(self, app, auth_client, seed_user, seed_periods_today):
        """Preview for every_period pattern returns occurrence list."""
        with app.app_context():
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence?recurrence_pattern={every_period.id}"
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
        ``offset_periods`` stayed ``None`` and ``match_periods`` computed
        ``period_index - None``.  Routing the preview through the authoring
        seam fixed it incidentally, because resolution always emits an int.
        """
        with app.app_context():
            every_n = db.session.query(RecurrencePattern).filter_by(
                name="Every N Periods"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence"
                f"?recurrence_pattern={every_n.id}&interval_n=2"
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_tolerates_a_zero_day_of_month(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?day_of_month=0`` answers 200, as it did before the seam.

        ``<input type="number" min="1">`` does not stop a user typing 0, and
        the endpoint reads the value straight from ``request.args``.  The
        engine coerces a 0 day with ``or 1``; resolution mirrors that exactly,
        so this stays a preview rather than becoming a 500 on
        ``date(y, m, 0)``.
        """
        with app.app_context():
            monthly = db.session.query(RecurrencePattern).filter_by(
                name="Monthly"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence"
                f"?recurrence_pattern={monthly.id}&day_of_month=0"
            )
            assert resp.status_code == 200

    def test_preview_rejects_other_users_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """Passing another user's start_period_id falls through to own data.

        The endpoint returns 200 (graceful fallback), not an error.
        The response must match what the user would see with no
        start_period_id (i.e. the ownership check caused the foreign
        period to be ignored).  This prevents pay period structure
        disclosure (H3).
        """
        with app.app_context():
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()

            # Baseline: request with no start_period_id.
            baseline_resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={"recurrence_pattern": every_period.id},
            )

            # Request with User B's period ID -- should fall through
            # to the same result as no start_period_id.
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    "recurrence_pattern": every_period.id,
                    "start_period_id": seed_second_periods[0].id,
                },
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data
            # The foreign period was ignored -- same output as baseline.
            assert resp.data == baseline_resp.data

    def test_create_recurring_template_rejects_other_users_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /templates rejects a foreign start_period on a recurring pattern.

        deep-quality-hunt #21/#24: the create-path counterpart to the
        preview IDOR test above.  The read-only preview route owner-gated
        the start period for every pattern, but the PERSIST path's probe
        used to run only for EVERY_N_PERIODS -- so a recurring template
        (here "Every Period") wrote a foreign ``start_period_id`` onto its
        RecurrenceRule unchecked, and ``recurrence_engine`` then read that
        victim period's ``start_date`` as the generation boundary.  The
        shared F-24 builder probe now rejects before any row is written;
        this pins the persist path closed end-to-end (the sibling preview
        test only proves the read path ignores the foreign period).
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            category = seed_user["categories"]["Rent"]
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()

            resp = auth_client.post("/templates", data={
                "name": "Recurring IDOR Template",
                "default_amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(every_period.id),
                # Second user's period on a recurring (non-EVERY_N) pattern.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid start period" in resp.data

            # No template was persisted.
            assert (
                db.session.query(TransactionTemplate)
                .filter_by(name="Recurring IDOR Template")
                .first()
            ) is None

    def test_preview_with_own_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Passing own start_period_id works normally (positive regression)."""
        with app.app_context():
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    "recurrence_pattern": every_period.id,
                    "start_period_id": seed_periods_today[0].id,
                },
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_nonexistent_start_period_falls_back(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Nonexistent start_period_id falls back to own periods (no 500).

        The endpoint must handle a start_period_id that does not exist
        in the database at all.  The ownership check naturally rejects it
        (db.session.get returns None), and the endpoint falls through to
        the user's own period list.
        """
        with app.app_context():
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()

            # Baseline: no start_period_id.
            baseline_resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={"recurrence_pattern": every_period.id},
            )

            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    "recurrence_pattern": every_period.id,
                    "start_period_id": 999999,
                },
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data
            # Nonexistent period ignored -- same output as baseline.
            assert resp.data == baseline_resp.data


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
            recurrence_engine.generate_for_template(template, periods, scenario.id)
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
            recurrence_engine.generate_for_template(template, periods_list, scenario.id)
            db.session.commit()

            # Mark one transaction as Paid.
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            txn.status_id = paid_status.id
            txn.actual_amount = txn.estimated_amount
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
            recurrence_engine.generate_for_template(template, periods_list, scenario.id)
            db.session.commit()

            # Mark one transaction as Paid.
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).first()
            txn.status_id = paid_status.id
            txn.actual_amount = txn.estimated_amount

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
            recurrence_engine.generate_for_template(template, periods_list, scenario.id)
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
                actual_amount=Decimal("2000.00"),
                status_id=received_status.id,
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
            assert refreshed.actual_amount == Decimal("2000.00")

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
                actual_amount=Decimal("1500.00"),
                status_id=received_status.id,
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
                "app.routes.templates.archive_helpers.template_has_paid_history",
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
            assert surviving.actual_amount == Decimal("1500.00")

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
            recurrence_engine.generate_for_template(template, periods_list, scenario.id)
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
            monthly = db.session.query(RecurrencePattern).filter_by(name="Monthly").one()

            resp = auth_client.post("/templates", data={
                "name": "Rent w/ Due Day",
                "default_amount": "1200.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(monthly.id),
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
            monthly = db.session.query(RecurrencePattern).filter_by(name="Monthly").one()

            auth_client.post("/templates", data={
                "name": "Rent No Due",
                "default_amount": "1200.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(monthly.id),
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
            monthly = db.session.query(RecurrencePattern).filter_by(name="Monthly").one()

            # Create without due_day first.
            auth_client.post("/templates", data={
                "name": "Updatable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(monthly.id),
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
                "recurrence_pattern": str(monthly.id),
                "day_of_month": "10",
                "due_day_of_month": "15",
            }, follow_redirects=True)

            db.session.refresh(template)
            assert template.recurrence_rule.due_day_of_month == 15

    def test_update_template_remove_due_day(self, app, auth_client, seed_user, seed_periods_today):
        """Update template to remove due_day_of_month (set to None)."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]
            monthly = db.session.query(RecurrencePattern).filter_by(name="Monthly").one()

            # Create with due_day.
            auth_client.post("/templates", data={
                "name": "Removable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(monthly.id),
                "day_of_month": "10",
                "due_day_of_month": "15",
            }, follow_redirects=True)

            template = db.session.query(TransactionTemplate).filter_by(
                name="Removable",
            ).one()
            assert template.recurrence_rule.due_day_of_month == 15

            # Update without due_day (empty string stripped by schema).
            auth_client.post(f"/templates/{template.id}", data={
                "name": "Removable",
                "default_amount": "1000.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": str(monthly.id),
                "day_of_month": "10",
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

    Guards CLAUDE.md "Reference Tables -- IDs for logic, strings for
    display only" against silent regression.  The macro historically
    compared ``rr.pattern.name`` strings (via an intermediate ``pname``
    set variable) to drive eight elif branches.  Commit 7 of the
    mobile-followup plan (F-8) rewired the comparisons onto integer
    pattern IDs sourced from the ``REC_*`` Jinja globals registered by
    ``app.jinja_globals.register_ref_id_globals``.

    The polyglot cleanup (TPLB/TPL-07) then consolidated the two
    byte-identical copies that lived in
    ``app/templates/templates/list.html`` and
    ``app/templates/transfers/list.html`` into one shared macro at
    ``app/templates/_recurrence_macros.html``, which both list
    templates now import.  These tests therefore lock the single
    source of truth: no ``.name ==`` or ``pname ==`` substrings (the
    comparison patterns the rewrite eliminated), every recurrence
    pattern enum member has a matching ``REC_*`` comparison so a
    future "added a new pattern but forgot to wire the branch"
    regression fails the lock instead of silently falling through to
    the else branch, and both list templates still import the shared
    macro so the consolidation cannot silently regress into a divergent
    re-inlined copy.
    """

    _MACRO_PATHS = (
        (
            "_recurrence_macros.html",
            ("app", "templates", "_recurrence_macros.html"),
        ),
    )

    # The unified Recurring surface (Loop B) is the single list template
    # that renders recurrence descriptions; the former transfers/list.html
    # was retired when /transfers folded into /templates.
    _LIST_TEMPLATE_PATHS = (
        ("templates/list.html", ("app", "templates", "templates", "list.html")),
    )

    # Derived from the enum rather than mirrored: plan step R2e-3 deleted
    # ``ONCE`` from both, and a hand-written copy would have had to be edited
    # in step -- which is what this class exists to make unnecessary.
    _EXPECTED_REC_CONSTANTS = tuple(
        f"REC_{member.name}" for member in RecurrencePatternEnum
    )

    def _read_macro_source(self, parts):
        """Return the contents of a template file under ``app/templates``."""
        import pathlib  # pylint: disable=import-outside-toplevel

        path = pathlib.Path(__file__).resolve().parents[2].joinpath(*parts)
        return path.read_text(encoding="utf-8")

    def test_no_string_name_comparisons_in_recurrence_cells(self):
        """Both macros must not equality-compare against pattern.name strings.

        Locks the F-8 rewrite: each macro previously did
        ``{% set pname = rr.pattern.name %}`` then compared
        ``pname == 'Every Period'`` etc.  Both substrings must be
        absent post-commit; comparisons drive off ``rr.pattern_id``.
        """
        for label, parts in self._MACRO_PATHS:
            src = self._read_macro_source(parts)

            assert ".name ==" not in src, (
                f"{label} must not compare against pattern.name "
                "strings; use the REC_* integer ID globals instead."
            )
            assert "pname ==" not in src, (
                f"{label} must not compare against the pname "
                "intermediate variable; use rr.pattern_id == REC_* "
                "instead."
            )

    def test_recurrence_cells_use_rec_id_globals(self):
        """Every RecurrencePatternEnum member maps to a REC_* lookup.

        Positive complement of the no-strings lock: confirms the
        rewrite did NOT collapse branches by removing comparisons
        outright.  Each enum member must appear as a
        ``rr.pattern_id == REC_<MEMBER>`` comparison in each macro;
        a future refactor that drops one branch fails this test.
        """
        for label, parts in self._MACRO_PATHS:
            src = self._read_macro_source(parts)

            for constant in self._EXPECTED_REC_CONSTANTS:
                assert f"rr.pattern_id == {constant}" in src, (
                    f"{label} recurrence_cell macro must compare "
                    f"rr.pattern_id against {constant}; missing "
                    "branch would silently fall through to the else "
                    "fallback."
                )

    def test_list_templates_import_shared_recurrence_macro(self):
        """The unified list template imports the consolidated macro (TPLB/TPL-07).

        The recurrence_cell macro lives once in ``_recurrence_macros.html``.
        Locking the import ensures the unified Recurring list does not
        re-inline a private copy that could diverge from the single source
        the two tests above guard.  The
        ``with context`` clause is required: the macro's else-branch
        fallback reads ``recurrence_pattern_labels``, an
        ``@app.context_processor`` variable invisible to a context-less
        import.
        """
        for label, parts in self._LIST_TEMPLATE_PATHS:
            src = self._read_macro_source(parts)

            assert (
                'from "_recurrence_macros.html" import recurrence_cell '
                "with context" in src
            ), (
                f"{label} must import recurrence_cell from "
                "_recurrence_macros.html with context, not define its "
                "own copy."
            )
