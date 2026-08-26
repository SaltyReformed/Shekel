"""
Shekel Budget App -- Category Route Tests

Tests for category CRUD endpoints:
  - Listing categories grouped by group_name
  - Creating categories (regular + HTMX)
  - Duplicate detection
  - Deleting categories (unused, in-use by template/transaction, IDOR)
  - Archive helper history-detection functions (5A.5-1)
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, TransactionType, Status
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services import account_service
from app.utils.archive_helpers import (
    account_has_history,
    category_has_usage,
    template_has_paid_history,
    transfer_template_has_paid_history,
)
from tests._test_helpers import (
    current_pay_period,
    select_option_values,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _create_other_user_category():
    """Create a second user with their own category.

    Returns:
        dict with keys: user, category.
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

    category = Category(
        user_id=other_user.id,
        group_name="Other",
        item_name="Other Item",
    )
    db.session.add(category)
    db.session.commit()

    return {"user": other_user, "category": category}


# ── List Tests ───────────────────────────────────────────────────────


class TestCategoryList:
    """Tests for GET /categories."""

    def test_list_categories_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /categories returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = auth_client.get("/categories")
            assert resp.status_code == 302
            assert "/settings" in resp.headers["Location"]
            assert "section=categories" in resp.headers["Location"]


# ── Create Tests ─────────────────────────────────────────────────────


class TestCategoryCreate:
    """Tests for POST /categories."""

    def test_create_category_success(self, app, auth_client, seed_user):
        """POST /categories creates a category and redirects."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Utilities",
                "item_name": "Electric",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                item_name="Electric",
            ).one()
            assert cat.group_name == "Utilities"

    def test_create_category_htmx(self, app, auth_client, seed_user):
        """POST /categories with HX-Request returns partial HTML row."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Subscriptions",
                "item_name": "Netflix",
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            # HTMX response is a partial HTML row, not a redirect.
            assert b"Netflix" in resp.data

    def test_create_category_htmx_group_names_exclude_archived_only(
        self, app, auth_client, seed_user,
    ):
        """deep-quality-hunt #41: the HTMX create response's group_names
        dropdown excludes a group that exists only on archived
        categories, matching the active-only set the settings GET render
        shows (otherwise the two paths offer different selectable groups).
        """
        with app.app_context():
            # A group that lives ONLY on an archived (is_active=False)
            # category -- the user does not see it on a settings reload.
            db.session.add(Category(
                user_id=seed_user["user"].id,
                group_name="GhostGroup",
                item_name="OnlyArchived",
                is_active=False,
            ))
            db.session.commit()

            resp = auth_client.post("/categories", data={
                "group_name": "Home",
                "item_name": "Internet",
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            # The archived-only group must NOT appear in the row's
            # group dropdown.
            assert b"GhostGroup" not in resp.data

    def test_create_category_validation_error(self, app, auth_client, seed_user):
        """POST /categories with missing fields shows validation error."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                # Missing group_name and item_name.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_create_category_duplicate(self, app, auth_client, seed_user):
        """POST /categories with existing group+item shows duplicate warning."""
        with app.app_context():
            # "Home" / "Rent" already exists from seed_user.
            resp = auth_client.post("/categories", data={
                "group_name": "Home",
                "item_name": "Rent",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"already exists" in resp.data

    def test_create_category_htmx_validation_error(self, app, auth_client, seed_user):
        """POST /categories via HTMX with missing fields returns 400 JSON."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                # Missing required fields.
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 400
            data = resp.get_json()
            assert "errors" in data
            assert "group_name" in data["errors"]
            assert "item_name" in data["errors"]


# ── Delete Tests ─────────────────────────────────────────────────────


class TestCategoryDelete:
    """Tests for POST /categories/<id>/delete."""

    def test_delete_unused_category(self, app, auth_client, seed_user):
        """POST /categories/<id>/delete removes an unused category."""
        with app.app_context():
            # Create a fresh category not used by any template/transaction.
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Deletable",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deleted" in resp.data

            # Verify actually deleted (hard delete).
            assert db.session.get(Category, cat.id) is None

    def test_delete_category_in_use_by_template(self, app, auth_client, seed_user):
        """POST /categories/<id>/delete for a category used by a template is rejected."""
        with app.app_context():
            category = seed_user["categories"]["Rent"]
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="Rent Template",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data

            # Category should still exist.
            assert db.session.get(Category, category.id) is not None

    def test_delete_category_in_use_by_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /categories/<id>/delete for a category used by a transaction is rejected."""
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="Grocery Trip",
                estimated_amount=Decimal("85.00"),
                status_id=projected.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data

    def test_delete_category_idor(self, app, auth_client, seed_user):
        """POST /categories/<id>/delete for another user's category returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify other user's category still exists.
            assert db.session.get(Category, other["category"].id) is not None

    def test_delete_nonexistent_category(self, app, auth_client, seed_user):
        """POST /categories/999999/delete for missing category returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/categories/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_delete_allowed_when_only_other_user_has_template(
        self, app, auth_client, seed_user, seed_second_user,
    ):
        """User A can delete a category even if User B has templates (M6).

        The in-use check must be scoped by user_id so that User B's
        templates do not block User A's category deletion.
        """
        with app.app_context():
            # Create a fresh deletable category for User A.
            cat_a = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="OnlyMine",
            )
            db.session.add(cat_a)
            db.session.flush()

            # User B creates a template referencing User B's OWN category.
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            tpl_b = TransactionTemplate(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                category_id=seed_second_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                name="B Rent Template",
                default_amount=Decimal("1000.00"),
            )
            db.session.add(tpl_b)
            db.session.commit()

            # User A deletes their own unused category -- should succeed.
            resp = auth_client.post(
                f"/categories/{cat_a.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deleted" in resp.data
            assert db.session.get(Category, cat_a.id) is None

    def test_delete_blocked_by_soft_deleted_transaction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Category cannot be deleted when even soft-deleted transactions reference it.

        The DB FK constraint blocks deletion regardless of is_deleted
        status, so the in-use check correctly includes soft-deleted
        transactions to give a friendly error instead of a DB crash.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="SoftDeleteTest",
            )
            db.session.add(category)
            db.session.flush()

            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(
                name="Projected"
            ).one()
            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="Soft Deleted Expense",
                estimated_amount=Decimal("50.00"),
                status_id=projected.id,
                is_deleted=True,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data
            assert db.session.get(Category, category.id) is not None

    def test_delete_blocked_by_active_transaction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Category cannot be deleted when active transactions reference it."""
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="ActiveTxnTest",
            )
            db.session.add(category)
            db.session.flush()

            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(
                name="Projected"
            ).one()
            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="Active Expense",
                estimated_amount=Decimal("100.00"),
                status_id=projected.id,
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data
            assert db.session.get(Category, category.id) is not None


# ── Negative Path Tests ─────────────────────────────────────────────


class TestCategoryNegativePaths:
    """Tests for category edge cases, validation, and XSS protection."""

    def test_create_category_double_submit(self, app, auth_client, seed_user):
        """Double-submitting the same category is caught by duplicate check."""
        with app.app_context():
            data = {"group_name": "Test", "item_name": "Double"}

            # First submit succeeds.
            resp1 = auth_client.post("/categories", data=data, follow_redirects=True)
            assert resp1.status_code == 200
            assert b"created" in resp1.data

            # Second submit detected as duplicate.
            resp2 = auth_client.post("/categories", data=data, follow_redirects=True)
            assert resp2.status_code == 200
            assert b"Category already exists." in resp2.data

            # Only one category with this group+item exists.
            count = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="Test",
                item_name="Double",
            ).count()
            assert count == 1

    def test_create_category_max_length_group_name(self, app, auth_client, seed_user):
        """Group name exceeding 100 chars is rejected by schema Length validator."""
        with app.app_context():
            long_name = "A" * 101
            resp = auth_client.post("/categories", data={
                "group_name": long_name,
                "item_name": "Valid",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                item_name="Valid",
            ).count()
            assert count == 0

    def test_create_category_max_length_item_name(self, app, auth_client, seed_user):
        """Item name exceeding 100 chars is rejected by schema Length validator."""
        with app.app_context():
            long_name = "B" * 101
            resp = auth_client.post("/categories", data={
                "group_name": "Valid",
                "item_name": long_name,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="Valid",
            ).count()
            assert count == 0

    def test_create_category_empty_group_name_after_trim(self, app, auth_client, seed_user):
        """Whitespace-only group name rejected after server-side strip."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "   ",
                "item_name": "ValidItem",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Category names cannot be blank." in resp.data

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                item_name="ValidItem",
            ).first()
            assert cat is None

    def test_create_category_empty_item_name_after_trim(self, app, auth_client, seed_user):
        """Whitespace-only item name rejected after server-side strip."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "ValidGroup",
                "item_name": "   ",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Category names cannot be blank." in resp.data

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="ValidGroup",
            ).first()
            assert cat is None

    def test_create_category_special_characters(self, app, auth_client, seed_user):
        """Special characters in category names are stored and auto-escaped on render."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Test & 'Quotes'",
                "item_name": 'Item "Special" <tag>',
            }, follow_redirects=True)

            assert resp.status_code == 200

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="Test & 'Quotes'",
            ).first()
            assert cat is not None
            assert cat.item_name == 'Item "Special" <tag>'

            # Verify Jinja2 auto-escaping on settings page.
            settings_resp = auth_client.get("/settings?section=categories")
            assert settings_resp.status_code == 200
            assert b"<tag>" not in settings_resp.data
            assert b"&lt;tag&gt;" in settings_resp.data


class TestCategoryManagementBaseline:
    """Regression baseline for Section 5A.4/5A.5.

    Locks down category management behavior before the category
    overhaul and CRUD consistency changes.
    """

    def test_category_delete_preserves_other_categories(
        self, app, auth_client, seed_user,
    ):
        """Deleting one category does not affect sibling categories
        in the same group.

        Guards against cascade bugs that could inadvertently remove
        related categories when one is deleted.  Important because
        Section 5A.5 introduces new delete/archive patterns.
        """
        with app.app_context():
            user = seed_user["user"]

            # Create two categories in the same group.
            cat_a = Category(
                user_id=user.id,
                group_name="TestGroup",
                item_name="ItemA",
            )
            cat_b = Category(
                user_id=user.id,
                group_name="TestGroup",
                item_name="ItemB",
            )
            db.session.add_all([cat_a, cat_b])
            db.session.commit()

            cat_a_id = cat_a.id
            cat_b_id = cat_b.id

            # Delete cat_a.
            resp = auth_client.post(
                f"/categories/{cat_a_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deleted" in resp.data

            # cat_a is gone.
            assert db.session.get(Category, cat_a_id) is None, (
                "Deleted category should no longer exist in the database"
            )

            # cat_b is untouched.
            surviving = db.session.get(Category, cat_b_id)
            assert surviving is not None, (
                "Sibling category in the same group must not be affected "
                "by deleting another category"
            )
            assert surviving.group_name == "TestGroup"
            assert surviving.item_name == "ItemB"


# ── Edit Tests (5A.4-1) ────────────────────────────────────────────


class TestCategoryEdit:
    """Tests for POST /categories/<id>/edit (rename and re-parent)."""

    def test_edit_category_rename(self, app, auth_client, seed_user):
        """Renaming item_name preserves group_name and updates the item."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "Fuel"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(cat)
            assert cat.item_name == "Fuel"
            assert cat.group_name == "Auto"

    def test_edit_category_reparent(self, app, auth_client, seed_user):
        """Changing group_name moves the category to a different group."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Toll Pass",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Travel", "item_name": "Toll Pass"},
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(cat)
            assert cat.group_name == "Travel"
            assert cat.item_name == "Toll Pass"

    def test_edit_category_rename_and_reparent(self, app, auth_client, seed_user):
        """Changing both group_name and item_name in a single edit."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Travel", "item_name": "Fuel"},
            )

            db.session.refresh(cat)
            assert cat.group_name == "Travel"
            assert cat.item_name == "Fuel"

    def test_edit_category_preserves_transaction_association(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Renaming a category does not break transaction FK references.

        Transactions reference categories by integer category_id, so
        changing group_name or item_name on the Category row leaves
        all transaction associations intact.
        """
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.flush()

            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(
                name="Projected"
            ).one()
            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=cat.id,
                transaction_type_id=txn_type.id,
                name="Fill Up",
                estimated_amount=Decimal("45.00"),
                status_id=projected.id,
            )
            db.session.add(txn)
            db.session.commit()
            cat_id = cat.id
            txn_id = txn.id

            auth_client.post(
                f"/categories/{cat_id}/edit",
                data={"group_name": "Travel", "item_name": "Fuel"},
            )

            db.session.refresh(txn)
            assert txn.category_id == cat_id
            assert txn.category.group_name == "Travel"
            assert txn.category.item_name == "Fuel"

    def test_edit_category_duplicate_blocked(self, app, auth_client, seed_user):
        """Editing a category to match an existing group+item is rejected."""
        with app.app_context():
            cat_gas = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            cat_insurance = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Insurance",
            )
            db.session.add_all([cat_gas, cat_insurance])
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat_gas.id}/edit",
                data={"group_name": "Auto", "item_name": "Insurance"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already exists" in resp.data

            db.session.refresh(cat_gas)
            assert cat_gas.item_name == "Gas"

    def test_edit_category_blank_name_rejected(self, app, auth_client, seed_user):
        """Empty or whitespace-only names are rejected after server-side strip."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            # Empty item_name.
            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "   "},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"cannot be blank" in resp.data

            db.session.refresh(cat)
            assert cat.item_name == "Gas"

    def test_edit_category_idor(
        self, app, auth_client, seed_user,
    ):
        """Editing another user's category returns 404 (security: same as nonexistent)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/edit",
                data={"group_name": "Hacked", "item_name": "Pwned"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

            db.session.refresh(other["category"])
            assert other["category"].group_name == "Other"

    def test_edit_category_nonexistent(self, app, auth_client, seed_user):
        """Editing a nonexistent category returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/categories/999999/edit",
                data={"group_name": "Ghost", "item_name": "Phantom"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_edit_category_no_op_same_values(self, app, auth_client, seed_user):
        """Submitting the same values is not flagged as a duplicate."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "Gas"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(cat)
            assert cat.group_name == "Auto"
            assert cat.item_name == "Gas"

    def test_edit_category_strips_whitespace(self, app, auth_client, seed_user):
        """Leading and trailing whitespace is stripped before saving."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "  Travel  ", "item_name": "  Fuel  "},
            )

            db.session.refresh(cat)
            assert cat.group_name == "Travel"
            assert cat.item_name == "Fuel"

    def test_edit_category_max_length(self, app, auth_client, seed_user):
        """Item name exceeding 100 characters is rejected by schema validation."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "X" * 101},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.refresh(cat)
            assert cat.item_name == "Gas"

    def test_edit_category_preserves_sort_order(self, app, auth_client, seed_user):
        """Editing name fields does not reset the sort_order column."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
                sort_order=5,
            )
            db.session.add(cat)
            db.session.commit()

            auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "Fuel"},
            )

            db.session.refresh(cat)
            assert cat.item_name == "Fuel"
            assert cat.sort_order == 5


# ── Group Dropdown Tests (5A.4-2) ──────────────────────────────────


class TestCategoryGroupDropdown:
    """Tests for the group name dropdown on category add and edit forms."""

    def test_add_form_shows_group_dropdown(self, app, auth_client, seed_user):
        """Add form contains a select dropdown with existing groups and 'Add new group'."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # The add form has a <select> for group selection.
            assert 'id="add-group-select"' in html

            # Existing groups appear as options (seed_user has 5 groups).
            assert '<option value="Auto">' in html
            assert '<option value="Home">' in html
            assert '<option value="Income">' in html

            # Sentinel option for adding a new group.
            assert '__new__' in html
            assert "Add new group" in html

    def test_add_form_groups_sorted_alphabetically(self, app, auth_client, seed_user):
        """Dropdown options are sorted alphabetically, with 'Add new group' last."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # Isolate the add form's select to avoid matching edit form dropdowns.
            select_start = html.index('id="add-group-select"')
            select_end = html.index("</select>", select_start)
            select_html = html[select_start:select_end]

            # seed_user groups: Auto, Credit Card, Family, Home, Income
            auto_pos = select_html.index('value="Auto"')
            credit_pos = select_html.index('value="Credit Card"')
            family_pos = select_html.index('value="Family"')
            home_pos = select_html.index('value="Home"')
            income_pos = select_html.index('value="Income"')
            new_pos = select_html.index('value="__new__"')

            assert auto_pos < credit_pos < family_pos < home_pos < income_pos < new_pos

    def test_add_to_existing_group_via_dropdown(self, app, auth_client, seed_user):
        """Creating a category with an existing group name works via hidden input."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Auto",
                "item_name": "Insurance",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            auto_cats = (
                db.session.query(Category)
                .filter_by(user_id=seed_user["user"].id, group_name="Auto")
                .all()
            )
            assert len(auto_cats) == 2  # Car Payment + Insurance

    def test_add_with_new_group(self, app, auth_client, seed_user):
        """Creating a category with a new group name adds a new group."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Travel",
                "item_name": "Airline",
            }, follow_redirects=True)

            assert resp.status_code == 200

            groups = set(
                row[0] for row in
                db.session.query(Category.group_name)
                .filter_by(user_id=seed_user["user"].id)
                .distinct()
            )
            assert "Travel" in groups

    def test_edit_form_preselects_current_group(self, app, auth_client, seed_user):
        """Edit form dropdown pre-selects the category's current group."""
        with app.app_context():
            cat = seed_user["categories"]["Car Payment"]

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # Find the edit form's select for this category.
            select_id = f'id="edit-group-select-{cat.id}"'
            assert select_id in html

            # The "Auto" option within this category's edit select has 'selected'.
            # Locate the select, then find the selected option within it.
            select_start = html.index(select_id)
            # The closing </select> after this select.
            select_end = html.index("</select>", select_start)
            select_html = html[select_start:select_end]

            assert 'value="Auto" selected' in select_html

    def test_no_existing_groups(self, app, auth_client, seed_user):
        """With no categories, only 'Add new group' option and text field is visible."""
        with app.app_context():
            # Remove all seed categories (no templates reference them).
            db.session.query(Category).filter_by(
                user_id=seed_user["user"].id
            ).delete()
            db.session.commit()

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # The add form select exists with only the sentinel option.
            assert 'id="add-group-select"' in html
            assert '__new__' in html

            # The custom text field is visible (no d-none class on container).
            # When group_names is empty, the div should NOT have d-none.
            custom_div_start = html.index('id="add-group-custom"')
            # Walk back to find the opening tag.
            div_start = html.rfind("<div", 0, custom_div_start)
            div_tag = html[div_start:custom_div_start + len('id="add-group-custom"')]
            assert "d-none" not in div_tag

    def test_edit_form_dropdown_ids_unique_per_category(self, app, auth_client, seed_user):
        """Each edit form has a distinct set of dropdown element IDs."""
        with app.app_context():
            cats = list(seed_user["categories"].values())
            cat_a = cats[0]
            cat_b = cats[1]

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # Both categories have their own select elements.
            assert f'id="edit-group-select-{cat_a.id}"' in html
            assert f'id="edit-group-select-{cat_b.id}"' in html

            # Both have their own hidden inputs.
            assert f'id="edit-group-name-{cat_a.id}"' in html
            assert f'id="edit-group-name-{cat_b.id}"' in html

            # Both have their own custom divs.
            assert f'id="edit-group-custom-{cat_a.id}"' in html
            assert f'id="edit-group-custom-{cat_b.id}"' in html

    def test_add_form_hidden_input_name_is_group_name(self, app, auth_client, seed_user):
        """Only the hidden input has name='group_name'; the select does not."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # The add form's select should NOT have name="group_name".
            select_start = html.index('id="add-group-select"')
            select_tag_start = html.rfind("<select", 0, select_start)
            select_tag = html[select_tag_start:select_start + len('id="add-group-select"')]
            assert 'name="group_name"' not in select_tag

            # The hidden input has both the id and name.
            assert 'id="add-group-name" name="group_name"' in html

    def test_group_dropdown_reflects_newly_created_group(self, app, auth_client, seed_user):
        """Creating a category with a new group adds it to the dropdown on next load."""
        with app.app_context():
            auth_client.post("/categories", data={
                "group_name": "NewGroup",
                "item_name": "NewItem",
            })

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            assert '<option value="NewGroup">' in html

    def test_htmx_row_partial_includes_dropdown(self, app, auth_client, seed_user):
        """HTMX-created category row includes group dropdown in its edit form."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Auto",
                "item_name": "Tolls",
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            html = resp.data.decode()

            # The partial should contain a select for the edit form.
            assert "edit-group-select-" in html
            # Existing groups should appear as options.
            assert '<option value="Auto"' in html
            assert '__new__' in html


# ── Archive Helpers Tests (5A.5-1) ──────────────────────────────────


class TestArchiveHelpers:
    """Tests for archive history-detection utility functions.

    Verifies that template_has_paid_history, transfer_template_has_paid_history,
    account_has_history, and category_has_usage return correct boolean results
    for various data configurations.
    """

    def test_existing_categories_default_active(self, app, db, seed_user):
        """C-5A.5-1: Seed categories all have is_active=True after migration."""
        with app.app_context():
            categories = (
                db.session.query(Category)
                .filter_by(user_id=seed_user["user"].id)
                .all()
            )
            assert len(categories) > 0, "seed_user should have created categories"
            for cat in categories:
                assert cat.is_active is True, (
                    f"Category '{cat.display_name}' should default to is_active=True"
                )

    def test_template_has_paid_history_true(self, app, db, seed_user, seed_periods_today):
        """C-5A.5-2: template_has_paid_history returns True when a Paid transaction exists."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            paid_status = db.session.query(Status).filter_by(name="Paid").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Paid History Template",
                default_amount=Decimal("500.00"),
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Paid History Template",
                estimated_amount=Decimal("500.00"),
                status_id=paid_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            result = template_has_paid_history(template.id)
            assert result is True

    def test_template_has_paid_history_false(self, app, db, seed_user, seed_periods_today):
        """C-5A.5-3: template_has_paid_history returns False when only Projected txns exist."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected_status = db.session.query(Status).filter_by(name="Projected").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Projected Only Template",
                default_amount=Decimal("500.00"),
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Projected Only Template",
                estimated_amount=Decimal("500.00"),
                status_id=projected_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            result = template_has_paid_history(template.id)
            assert result is False

    def test_transfer_template_has_paid_history_true(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C-5A.5-4: transfer_template_has_paid_history returns True when Paid transfer exists."""
        with app.app_context():
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            savings_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings for Transfer Test",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings_account)
            db.session.flush()

            xfer_template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings_account.id,
                name="Paid Transfer Template",
                default_amount=Decimal("200.00"),
            )
            db.session.add(xfer_template)
            db.session.flush()

            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings_account.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=paid_status.id,
                transfer_template_id=xfer_template.id,
                name="Paid Transfer",
                amount=Decimal("200.00"),
            )
            db.session.add(xfer)
            db.session.commit()

            result = transfer_template_has_paid_history(xfer_template.id)
            assert result is True

    def test_transfer_template_has_paid_history_false(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C-5A.5-5: transfer_template_has_paid_history returns False when only Projected."""
        with app.app_context():
            projected_status = db.session.query(Status).filter_by(name="Projected").one()
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            savings_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings for Projected Transfer",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings_account)
            db.session.flush()

            xfer_template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings_account.id,
                name="Projected Transfer Template",
                default_amount=Decimal("150.00"),
            )
            db.session.add(xfer_template)
            db.session.flush()

            xfer = Transfer(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings_account.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_status.id,
                transfer_template_id=xfer_template.id,
                name="Projected Transfer",
                amount=Decimal("150.00"),
            )
            db.session.add(xfer)
            db.session.commit()

            result = transfer_template_has_paid_history(xfer_template.id)
            assert result is False

    def test_account_has_history_true(self, app, db, seed_user, seed_periods_today):
        """C-5A.5-6: account_has_history returns True when account has any non-deleted txn."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected_status = db.session.query(Status).filter_by(name="Projected").one()

            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Account History Txn",
                estimated_amount=Decimal("100.00"),
                status_id=projected_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            result = account_has_history(seed_user["account"].id)
            assert result is True

    def test_account_has_history_false(self, app, db, seed_user):
        """C-5A.5-7: account_has_history returns False when account has zero transactions."""
        with app.app_context():
            # seed_user["account"] has no transactions (tables truncated each test).
            result = account_has_history(seed_user["account"].id)
            assert result is False

    def test_category_has_usage_true(self, app, db, seed_user):
        """C-5A.5-8: category_has_usage returns True when a template references the category."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=expense_type.id,
                name="Category Usage Template",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.commit()

            result = category_has_usage(category.id, seed_user["user"].id)
            assert result is True

    def test_category_has_usage_false(self, app, db, seed_user):
        """C-5A.5-9: category_has_usage returns False when nothing references the category."""
        with app.app_context():
            # Create a fresh category with no templates or transactions.
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Unused",
                item_name="Orphan",
            )
            db.session.add(cat)
            db.session.commit()

            result = category_has_usage(cat.id, seed_user["user"].id)
            assert result is False

    def test_category_has_usage_scoped_to_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """C-5A.5-10: category_has_usage returns False when only another user's template uses it.

        User B creates a template referencing User B's category.  When
        we check category_has_usage for User A's category (same group/item
        names but different user_id), it must return False because the
        check is scoped to User A.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # User B creates a template referencing User B's own Rent category.
            user_b_cat = seed_second_user["categories"]["Rent"]
            template_b = TransactionTemplate(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                category_id=user_b_cat.id,
                transaction_type_id=expense_type.id,
                name="User B Rent Template",
                default_amount=Decimal("900.00"),
            )
            db.session.add(template_b)
            db.session.commit()

            # User A's Rent category should show no usage (User B's template
            # does not count).
            user_a_cat = seed_user["categories"]["Rent"]
            result = category_has_usage(user_a_cat.id, seed_user["user"].id)
            assert result is False


# ── Category Archive/Delete Tests (5A.5-5) ─────────────────────────


class TestCategoryArchiveDelete:
    """Tests for archive, unarchive, and enhanced delete behavior."""

    def test_archive_category(self, app, auth_client, seed_user):
        """C-5A.5-29: Archiving a category sets is_active=False."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Archive",
                item_name="TestItem",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data

            db.session.refresh(cat)
            assert cat.is_active is False

    def test_unarchive_category(self, app, auth_client, seed_user):
        """C-5A.5-30: Unarchiving a category sets is_active=True."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Archive",
                item_name="Restore",
                is_active=False,
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

            db.session.refresh(cat)
            assert cat.is_active is True

    def test_delete_category_no_usage(self, app, auth_client, seed_user):
        """C-5A.5-31: Category with no usage is permanently deleted."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Unused",
                item_name="Deletable",
            )
            db.session.add(cat)
            db.session.commit()
            cat_id = cat.id

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(Category, cat_id) is None

    def test_delete_category_with_usage_archives(self, app, auth_client, seed_user, db):
        """C-5A.5-32: Category in use by template is archived instead of deleted."""
        with app.app_context():
            category = seed_user["categories"]["Rent"]
            cat_id = category.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=cat_id,
                transaction_type_id=txn_type.id,
                name="Blocks Delete",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data
            assert b"archived instead" in resp.data

            reloaded = db.session.get(Category, cat_id)
            assert reloaded.is_active is False

    def test_archived_categories_hidden_from_settings(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-33: Settings page separates active and archived categories."""
        with app.app_context():
            # Seed categories are active. Archive one.
            rent_cat = db.session.get(Category, seed_user["categories"]["Rent"].id)
            rent_cat.is_active = False
            db.session.commit()

            resp = auth_client.get("/settings?section=categories")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active categories in group cards (Rent is gone from active).
            assert "Car Payment" in html  # Active Auto category.

            # Archived section shows the archived category.
            assert "Archived Categories (1)" in html
            assert "Rent" in html

    def test_archived_categories_hidden_from_grid_dropdown(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """C-5A.5-34: Archived categories do not appear in grid Add Transaction dropdown.

        Scopes the assertion to the ``category_id`` select in the
        grid's add-transaction modal.  See
        ``test_archived_categories_hidden_from_template_dropdown``
        for the rationale -- the page renders ``value="N"`` for
        many other dropdowns (transaction types, pay periods, etc.)
        and a whole-HTML substring check fails deterministically
        when ``rent.id`` collides with any of them.
        """
        with app.app_context():
            # Archive one category.
            rent = db.session.get(Category, seed_user["categories"]["Rent"].id)
            rent.is_active = False
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            category_options = select_option_values(html, "category_id")

            # Active categories in the dropdown.
            salary = seed_user["categories"]["Salary"]
            assert str(salary.id) in category_options, (
                f"Active category {salary.id} ({salary.item_name}) "
                f"is missing from /grid category dropdown; got "
                f"options {category_options!r}"
            )

            # Archived category NOT in the dropdown.
            assert str(rent.id) not in category_options, (
                f"Archived category {rent.id} ({rent.item_name}) is "
                f"still present in /grid category dropdown; got "
                f"options {category_options!r}"
            )

    def test_archived_category_transactions_still_render(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """C-5A.5-35: Transactions with archived categories still render in the grid."""
        with app.app_context():
            category = db.session.get(Category, seed_user["categories"]["Rent"].id)
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            # The txn must live inside the grid's default visible window so
            # the assertion targets the archived-category rendering path
            # rather than the period-scoping filter added in 8b67128.  Use
            # the period that contains today -- ``seed_periods_today`` is 10
            # biweekly periods starting 2026-01-02, so today's period is
            # always one of them.
            current_period = current_pay_period(seed_user["user"].id)
            assert current_period is not None, (
                "seed_periods_today must cover today so the txn lands in the "
                "default visible grid window"
            )

            txn = Transaction(
                pay_period_id=current_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="Rent Payment",
                estimated_amount=Decimal("1200.00"),
                status_id=projected.id,
            )
            db.session.add(txn)

            # Archive the category AFTER creating the transaction.
            category.is_active = False
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            # The transaction should still render with its name.
            assert b"Rent Payment" in resp.data

    def test_archive_category_idor(self, app, auth_client, seed_user):
        """C-5A.5-36: Archiving another user's category returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            db.session.refresh(other["category"])
            assert other["category"].is_active is True

    def test_unarchive_category_idor(self, app, auth_client, seed_user):
        """Unarchiving another user's archived category returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_category()
            other["category"].is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            db.session.refresh(other["category"])
            assert other["category"].is_active is False

    def test_delete_category_already_archived_with_usage(
        self, app, auth_client, seed_user, db,
    ):
        """Already-archived category with usage stays archived, no double-archive."""
        with app.app_context():
            category = db.session.get(Category, seed_user["categories"]["Rent"].id)
            cat_id = category.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=cat_id,
                transaction_type_id=txn_type.id,
                name="In-Use Template",
                default_amount=Decimal("500.00"),
            )
            db.session.add(template)

            category.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data

            reloaded = db.session.get(Category, cat_id)
            assert reloaded.is_active is False

    def test_delete_category_already_archived_no_usage(
        self, app, auth_client, seed_user,
    ):
        """Archived category with no usage is permanently deleted (clean up archive)."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Old",
                item_name="Cleanup",
                is_active=False,
            )
            db.session.add(cat)
            db.session.commit()
            cat_id = cat.id

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(Category, cat_id) is None

    def test_archived_categories_hidden_from_template_dropdown(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """Template creation form only shows active categories in dropdown.

        Scopes the assertion to the ``category_id`` select.  A naive
        ``f'value="{rent.id}" not in html`` check would falsely
        match the ``value="N"`` attributes of unrelated dropdowns on
        the same page -- transaction_type_id (1-2), start_period_id
        (per-test pay-period IDs), month_of_year (1-12), and
        recurrence_pattern (1-8) all share the namespace.  When
        rent.id collides with any of those values the original check
        failed despite the category dropdown being correctly filtered.
        """
        with app.app_context():
            rent = db.session.get(Category, seed_user["categories"]["Rent"].id)
            rent.is_active = False
            db.session.commit()

            resp = auth_client.get("/templates/new")
            assert resp.status_code == 200
            html = resp.data.decode()

            category_options = select_option_values(html, "category_id")

            # Active category present in the category dropdown.
            salary = seed_user["categories"]["Salary"]
            assert str(salary.id) in category_options, (
                f"Active category {salary.id} ({salary.item_name}) is "
                f"missing from /templates/new category dropdown; got "
                f"options {category_options!r}"
            )

            # Archived category absent from the category dropdown.
            assert str(rent.id) not in category_options, (
                f"Archived category {rent.id} ({rent.item_name}) is "
                f"still present in /templates/new category dropdown; "
                f"got options {category_options!r}"
            )


class TestACategoryAStandingMERCHANTRULEFilesUnderIsInUse:
    """Plan step ``bank_import:X-gd-2``, on a gap that predates it.

    ``category_has_usage`` counted templates and transactions only, so a
    category referenced ONLY by a merchant rule's *new envelope* answer read as
    unused -- and a "no" here is what permits a PERMANENT delete.
    ``fk_merchant_rules_category_owner`` cascades, so the rule went with it,
    under a flash reading "permanently deleted" that said nothing about the
    answer the owner lost.

    **The cascade itself is right** and is not what changed: an answer naming a
    category that no longer exists is not an answer, and ``RESTRICT`` was
    refused as finding **N-302**'s dead end.  What was wrong is a door calling
    the category unused while a stored decision used it -- and ruling **R-GS**
    sharpens that, because a rule row is never un-stated by the owner, so a
    silent cascade would be the only way one could vanish.

    Measured on the developer's dev database 2026-08-26: 12 new-envelope rules
    naming 6 distinct categories, every one of them ALSO used by a template or
    a transaction -- so the path is reachable and has not yet fired.
    """

    def _a_rule_filing_under(self, seed_user, category):
        """Stage a *new envelope* rule naming *category*, and return it.

        Args:
            seed_user: The seeded user bundle.
            category: The category the answer files under.

        Returns:
            The staged :class:`~app.models.merchant_rule.MerchantRule`.
        """
        merchant = Merchant(
            account_id=seed_user["account"].id, name="Public Library",
        )
        db.session.add(merchant)
        db.session.flush()
        rule = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=merchant.id,
            envelope_name="Library Fines",
            category_id=category.id,
            never_a_purchase=False,
        )
        db.session.add(rule)
        db.session.commit()
        return rule

    def test_the_helper_counts_it(self, app, db, seed_user):
        """The predicate itself, on a category nothing ELSE references."""
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Only A Rule Uses This",
            )
            db.session.add(category)
            db.session.flush()
            self._a_rule_filing_under(seed_user, category)

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is True

    def test_the_delete_door_ARCHIVES_it_and_keeps_the_rule(
        self, app, auth_client, db, seed_user,
    ):
        """The consequence, at the door where the money-shaped loss happened.

        The category survives as archived and the owner's answer survives with
        it -- which is the state ``_new_envelope_placement`` already designs
        for, reporting *the category you filed it under is archived* rather
        than filing money somewhere nobody named.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Only A Rule Uses This",
            )
            db.session.add(category)
            db.session.flush()
            rule = self._a_rule_filing_under(seed_user, category)
            rule_id, category_id = rule.id, category.id

            response = auth_client.post(
                f"/categories/{category_id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data
            surviving = db.session.get(Category, category_id)
            assert surviving is not None
            assert surviving.is_active is False
            assert db.session.get(MerchantRule, rule_id) is not None

    def test_a_category_NO_rule_names_is_still_permanently_deleted(
        self, app, auth_client, db, seed_user,
    ):
        """The firing control: the new clause narrows, it does not block.

        Without it the two cases above would be satisfied by a helper that
        answered True for everything, which would make permanent deletion
        unreachable for every category on the account.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Nothing Uses This",
            )
            db.session.add(category)
            db.session.commit()
            category_id = category.id

            response = auth_client.post(
                f"/categories/{category_id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert db.session.get(Category, category_id) is None

    def test_a_STRANGERS_rule_cannot_name_this_owners_category_at_all(
        self, app, db, seed_user, seed_second_user,
    ):
        """Why the rule clause needs no ``user_id`` term, shown structurally.

        **This case replaced one that graded nothing.** The original staged a
        stranger's rule naming the STRANGER'S OWN category and then asserted
        this owner's category was unused -- true whatever the clause said,
        because no rule anywhere named it. Deleting the clause's ``user_id``
        filter left it green. Found by adversarial review 2026-08-26.

        What is actually true is stronger than a filter:
        ``fk_merchant_rules_category_owner`` is composite over
        ``(category_id, user_id)`` against ``categories(id, user_id)``, and
        ``categories.id`` is a primary key -- so a rule naming a category
        DETERMINES its owner, and a stranger's rule naming this owner's
        category is UNWRITABLE rather than filtered out. That is what the
        clause rests on, so that is what is graded.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Mine",
            )
            db.session.add(category)
            db.session.flush()
            merchant = Merchant(
                account_id=seed_second_user["account"].id, name="Their Shop",
            )
            db.session.add(merchant)
            db.session.flush()
            db.session.add(MerchantRule(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                merchant_id=merchant.id,
                envelope_name="Theirs",
                category_id=category.id,
                never_a_purchase=False,
            ))

            with pytest.raises(Exception) as caught:
                db.session.flush()

            assert "fk_merchant_rules_category_owner" in str(caught.value)

    def test_a_rule_on_ANOTHER_ACCOUNT_of_this_owner_counts(
        self, app, db, seed_user):
        """The clause is scoped by neither owner nor ACCOUNT, and the second
        absence is the load-bearing one.

        A category is owner-scoped and a rule is account-scoped, so one owner's
        rules on two accounts may file under one category. An account term
        would answer "unused" for a category the owner's OTHER account's rule
        is using, and the permanent delete would cascade that rule away -- the
        exact defect this clause was added to close, reintroduced by narrowing
        it.
        """
        with app.app_context():
            second = Account(
                user_id=seed_user["user"].id,
                name="Second Checking",
                account_type_id=seed_user["account"].account_type_id,
            )
            db.session.add(second)
            db.session.flush()
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Only The Other Account Uses This",
            )
            db.session.add(category)
            db.session.flush()
            merchant = Merchant(account_id=second.id, name="Public Library")
            db.session.add(merchant)
            db.session.flush()
            db.session.add(MerchantRule(
                user_id=seed_user["user"].id,
                account_id=second.id,
                merchant_id=merchant.id,
                envelope_name="Library Fines",
                category_id=category.id,
                never_a_purchase=False,
            ))
            db.session.commit()

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is True
