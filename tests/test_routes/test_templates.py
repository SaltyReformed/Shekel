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

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
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
        """GET /templates renders correctly when user has no templates."""
        with app.app_context():
            resp = auth_client.get("/templates")
            assert resp.status_code == 200
            assert b"No active recurring transactions" in resp.data
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

    def test_create_template_with_recurrence(self, app, auth_client, seed_user, seed_periods):
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
            template = _create_template(seed_user, pattern_name="Every Period")

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

            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()
            resp = auth_client.post(f"/templates/{template.id}", data={
                "default_amount": "1400.00",
                "recurrence_pattern": str(every_period.id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            # Should flash the conflict warning.
            assert b"overridden" in resp.data or b"updated" in resp.data


# ── Archive Tests ────────────────────────────────────────────────────


class TestTemplateArchive:
    """Tests for POST /templates/<id>/archive."""

    def test_archive_and_soft_deletes(self, app, auth_client, seed_user, seed_periods):
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
        """POST /templates/<id>/archive for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data

            # Verify template still active.
            db.session.refresh(other["template"])
            assert other["template"].is_active is True

    def test_archive_nonexistent_template(self, app, auth_client, seed_user):
        """POST /templates/999999/archive for missing template redirects."""
        with app.app_context():
            resp = auth_client.post(
                "/templates/999999/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Recurring transaction not found" in resp.data


# ── Unarchive Tests ──────────────────────────────────────────────────


class TestTemplateUnarchive:
    """Tests for POST /templates/<id>/unarchive."""

    def test_unarchive_restores_transactions(self, app, auth_client, seed_user, seed_periods):
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
            # seed_periods creates 10 biweekly periods
            active_txns = db.session.query(Transaction).filter_by(
                template_id=template.id, is_deleted=False,
            ).count()
            assert active_txns == 10

    def test_unarchive_template_idor(self, app, auth_client, seed_user):
        """POST /templates/<id>/unarchive for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            resp = auth_client.post(
                f"/templates/{other['template'].id}/unarchive",
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
            monthly = db.session.query(RecurrencePattern).filter_by(
                name="Monthly"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence"
                f"?recurrence_pattern={monthly.id}&day_of_month=15"
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data or b"No matching" in resp.data

    def test_preview_once_pattern(self, app, auth_client, seed_user, seed_periods):
        """Preview for 'once' pattern returns no-preview message."""
        with app.app_context():
            once = db.session.query(RecurrencePattern).filter_by(
                name="Once"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence?recurrence_pattern={once.id}"
            )
            assert resp.status_code == 200
            assert b"No preview" in resp.data

    def test_preview_unknown_pattern(self, app, auth_client, seed_user, seed_periods):
        """Preview for unknown pattern ID returns unknown message."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence?recurrence_pattern=999999"
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
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()
            resp = auth_client.get(
                f"/templates/preview-recurrence?recurrence_pattern={every_period.id}"
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_rejects_other_users_start_period(
        self, app, auth_client, seed_user, seed_periods,
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

    def test_preview_with_own_start_period(
        self, app, auth_client, seed_user, seed_periods,
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
                    "start_period_id": seed_periods[0].id,
                },
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_nonexistent_start_period_falls_back(
        self, app, auth_client, seed_user, seed_periods,
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

    def test_archive_already_archived_template(self, app, auth_client, seed_user, seed_periods):
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

    def test_unarchive_already_active_template(self, app, auth_client, seed_user, seed_periods):
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

    def test_hard_delete_template_no_history(self, app, auth_client, seed_user, seed_periods):
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

    def test_hard_delete_template_with_history(self, app, auth_client, seed_user, seed_periods):
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
        self, app, auth_client, seed_user, seed_periods,
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

    def test_hard_delete_template_already_archived(self, app, auth_client, seed_user, seed_periods):
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
        """C-5A.5-14: Hard-deleting another user's template returns 'not found'."""
        with app.app_context():
            other = _create_other_user_with_template()
            other_id = other["template"].id

            resp = auth_client.post(
                f"/templates/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"not found" in resp.data

            # Other user's template still exists.
            assert db.session.get(TransactionTemplate, other_id) is not None

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

    def test_archive_label_in_flash(self, app, auth_client, seed_user, seed_periods):
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

    def test_create_template_with_due_day(self, app, auth_client, seed_user, seed_periods):
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

    def test_create_template_without_due_day(self, app, auth_client, seed_user, seed_periods):
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

    def test_update_template_add_due_day(self, app, auth_client, seed_user, seed_periods):
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

    def test_update_template_remove_due_day(self, app, auth_client, seed_user, seed_periods):
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
