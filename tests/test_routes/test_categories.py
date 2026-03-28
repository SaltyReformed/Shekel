"""
Shekel Budget App -- Category Route Tests

Tests for category CRUD endpoints:
  - Listing categories grouped by group_name
  - Creating categories (regular + HTMX)
  - Duplicate detection
  - Deleting categories (unused, in-use by template/transaction, IDOR)
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import AccountType, TransactionType, Status
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


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
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()

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

    def test_delete_category_in_use_by_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /categories/<id>/delete for a category used by a transaction is rejected."""
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            txn_type = db.session.query(TransactionType).filter_by(name="expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
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
        """POST /categories/<id>/delete for another user's category is rejected."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"not found" in resp.data

            # Verify other user's category still exists.
            assert db.session.get(Category, other["category"].id) is not None

    def test_delete_nonexistent_category(self, app, auth_client, seed_user):
        """POST /categories/999999/delete for missing category redirects."""
        with app.app_context():
            resp = auth_client.post(
                "/categories/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"not found" in resp.data

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
                name="expense"
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
        self, app, auth_client, seed_user, seed_periods,
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
                name="expense"
            ).one()
            projected = db.session.query(Status).filter_by(
                name="Projected"
            ).one()
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
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
        self, app, auth_client, seed_user, seed_periods,
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
                name="expense"
            ).one()
            projected = db.session.query(Status).filter_by(
                name="Projected"
            ).one()
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
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
