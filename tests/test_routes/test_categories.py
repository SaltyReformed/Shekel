"""
Shekel Budget App — Category Route Tests

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
            projected = db.session.query(Status).filter_by(name="projected").one()

            txn = Transaction(
                template_id=None,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
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
