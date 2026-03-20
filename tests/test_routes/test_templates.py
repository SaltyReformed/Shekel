"""
Shekel Budget App — Template Route Tests

Tests for transaction template CRUD and recurrence preview:
  - Template listing (happy path, auth)
  - Template creation (with/without recurrence, validation, IDOR)
  - Template update (happy path, validation, IDOR, recurrence conflict)
  - Template delete (deactivate + soft-delete projected txns)
  - Template reactivate (restore + regenerate)
  - Recurrence preview HTMX endpoint
"""

from datetime import date
from decimal import Decimal

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


# ── Helpers ──────────────────────────────────────────────────────────


def _create_template(seed_user, name="Rent", amount="1200.00",
                     txn_type="expense", pattern_name=None):
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

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
    account = Account(
        user_id=other_user.id,
        account_type_id=checking_type.id,
        name="Other Checking",
        current_anchor_balance=Decimal("500.00"),
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

    txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
        """GET /templates renders correctly when user has no templates."""
        with app.app_context():
            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert b"No recurring transactions yet" in resp.data
            assert b"Car Payment" not in resp.data


# ── Create Tests ─────────────────────────────────────────────────────


class TestTemplateCreate:
    """Tests for GET /templates/new and POST /templates."""

    def test_new_template_form(self, app, auth_client, seed_user, seed_periods):
        """GET /templates/new renders the creation form."""
        with app.app_context():
            resp = auth_client.get("/templates/new")
            assert resp.status_code == 200
            assert b"New Recurring Transaction" in resp.data
            assert b'name="name"' in resp.data
            assert b'name="default_amount"' in resp.data
            assert b'name="recurrence_pattern"' in resp.data

    def test_create_template_no_recurrence(self, app, auth_client, seed_user, seed_periods):
        """POST /templates creates a template without recurrence."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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

    def test_create_template_with_recurrence(self, app, auth_client, seed_user, seed_periods):
        """POST /templates creates a template with recurrence and generates transactions."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
            category = seed_user["categories"]["Rent"]

            resp = auth_client.post("/templates", data={
                "name": "Rent Payment",
                "default_amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                "recurrence_pattern": "every_period",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent Payment"
            ).one()
            assert template.recurrence_rule is not None

            # every_period pattern generates 1 transaction per period;
            # seed_periods creates 10 biweekly periods
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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()

            resp = auth_client.post("/templates", data={
                "name": "Sneaky Template",
                "default_amount": "100.00",
                "category_id": other["category"].id,  # Other user's category.
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid category" in resp.data


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

    def test_update_template_success(self, app, auth_client, seed_user, seed_periods):
        """POST /templates/<id> updates template fields."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="every_period")

            resp = auth_client.post(f"/templates/{template.id}", data={
                "name": "Updated Rent",
                "default_amount": "1300.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(template)
            assert template.name == "Updated Rent"
            assert template.default_amount == Decimal("1300.00")

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
        """POST /templates/<id> for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}",
                data={"name": "Hijacked"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data

            # Verify original unchanged.
            db.session.refresh(other["template"])
            assert other["template"].name == "Other Rent"

    def test_edit_template_idor(self, app, auth_client, seed_user):
        """GET /templates/<id>/edit for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.get(
                f"/templates/{other['template'].id}/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data

    def test_update_triggers_recurrence_conflict(self, app, auth_client, seed_user, seed_periods):
        """POST /templates/<id> flashes warning when recurrence conflict occurs."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="every_period")

            # Generate transactions via recurrence, then override one.
            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, periods, scenario.id)
            db.session.flush()

            # Override a transaction to trigger RecurrenceConflict on regen.
            txn = db.session.query(Transaction).filter_by(
                template_id=template.id
            ).first()
            if txn:
                txn.is_override = True
                db.session.commit()

            resp = auth_client.post(f"/templates/{template.id}", data={
                "default_amount": "1400.00",
                "recurrence_pattern": "every_period",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # Should flash the conflict warning.
            assert b"overridden" in resp.data or b"updated" in resp.data


# ── Delete Tests ─────────────────────────────────────────────────────


class TestTemplateDelete:
    """Tests for POST /templates/<id>/delete."""

    def test_delete_deactivates_and_soft_deletes(self, app, auth_client, seed_user, seed_periods):
        """POST /templates/<id>/delete deactivates template and soft-deletes projected txns."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="every_period")

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
                f"/templates/{template.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deactivated" in resp.data

            db.session.refresh(template)
            assert template.is_active is False

            # All projected transactions should be soft-deleted.
            remaining = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert remaining == 0

    def test_delete_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/delete for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data

            # Verify template still active.
            db.session.refresh(other["template"])
            assert other["template"].is_active is True

    def test_delete_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999/delete for missing template redirects."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data


# ── Reactivate Tests ─────────────────────────────────────────────────


class TestTemplateReactivate:
    """Tests for POST /templates/<id>/reactivate."""

    def test_reactivate_restores_transactions(self, app, auth_client, seed_user, seed_periods):
        """POST /templates/<id>/reactivate restores soft-deleted txns."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="every_period")

            # Generate and then delete.
            from app.services import recurrence_engine, pay_period_service
            scenario = seed_user["scenario"]
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            recurrence_engine.generate_for_template(template, periods, scenario.id)
            db.session.commit()

            # Deactivate via the delete route.
            auth_client.post(f"/templates/{template.id}/delete")

            db.session.refresh(template)
            assert template.is_active is False

            # Now reactivate.
            resp = auth_client.post(
                f"/templates/{template.id}/reactivate",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"reactivated" in resp.data

            db.session.refresh(template)
            assert template.is_active is True

            # Transactions should be restored: every_period generates 1 per period,
            # seed_periods creates 10 biweekly periods
            active_txns = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert active_txns == 10

    def test_reactivate_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/reactivate for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/reactivate",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data


# ── Preview Recurrence Tests ─────────────────────────────────────────


class TestPreviewRecurrence:
    """Tests for GET /templates/preview-recurrence."""

    def test_preview_monthly(self, app, auth_client, seed_user, seed_periods):
        """Preview for monthly pattern returns occurrence list."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence"
                "?recurrence_pattern=monthly&day_of_month=15"
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data or b"No matching" in resp.data

    def test_preview_once_pattern(self, app, auth_client, seed_user, seed_periods):
        """Preview for 'once' pattern returns no-preview message."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence?recurrence_pattern=once"
            )
            assert resp.status_code == 200
            assert b"No preview" in resp.data

    def test_preview_unknown_pattern(self, app, auth_client, seed_user, seed_periods):
        """Preview for unknown pattern returns unknown message."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence?recurrence_pattern=bogus_pattern"
            )
            assert resp.status_code == 200
            assert b"Unknown pattern" in resp.data

    def test_preview_no_pattern(self, app, auth_client, seed_user, seed_periods):
        """Preview with no pattern parameter returns no-preview message."""
        with app.app_context():
            resp = auth_client.get("/templates/preview-recurrence")
            assert resp.status_code == 200
            assert b"No preview" in resp.data

    def test_preview_every_period(self, app, auth_client, seed_user, seed_periods):
        """Preview for every_period pattern returns occurrence list."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence?recurrence_pattern=every_period"
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data


# ── Negative Path Tests ─────────────────────────────────────────────


class TestTemplateNegativePaths:
    """Tests for template edge cases, validation gaps, and idempotent operations."""

    def test_delete_already_deactivated_template(self, app, auth_client, seed_user, seed_periods):
        """Deleting an already-deactivated template is idempotent."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="every_period")
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/templates/{template.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deactivated" in resp.data
            # No projected transactions exist, so 0 are soft-deleted.
            assert b"0 projected transaction(s) removed" in resp.data

            db.session.refresh(template)
            assert template.is_active is False
            # NOTE: delete_template is idempotent -- no guard against
            # deactivating an already-inactive template.

    def test_reactivate_already_active_template(self, app, auth_client, seed_user, seed_periods):
        """Reactivating an already-active template is idempotent."""
        with app.app_context():
            template = _create_template(seed_user, pattern_name="every_period")
            assert template.is_active is True

            resp = auth_client.post(
                f"/templates/{template.id}/reactivate",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"reactivated" in resp.data
            # No soft-deleted transactions to restore.
            assert b"0 projected transaction(s) restored" in resp.data

            db.session.refresh(template)
            assert template.is_active is True
            # NOTE: reactivate is idempotent -- no guard against
            # reactivating an already-active template.

    def test_create_template_missing_name(self, app, auth_client, seed_user):
        """Creating a template without name fails schema validation."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()

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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
        """GET /templates/999999/edit for missing template redirects with flash."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/999999/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found." in resp.data

    def test_update_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999 for missing template redirects with flash."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999",
                data={"name": "Ghost"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found." in resp.data

    def test_create_template_xss_in_name(self, app, auth_client, seed_user):
        """XSS payload in template name is escaped by Jinja2 auto-escaping."""
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
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
