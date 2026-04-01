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

    def test_delete_category_in_use_by_transaction(self, app, auth_client, seed_user, seed_periods):
        """POST /categories/<id>/delete for a category used by a transaction is rejected."""
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
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
                name="Expense"
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
                name="Expense"
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
        self, app, auth_client, seed_user, seed_periods,
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
                pay_period_id=seed_periods[0].id,
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
        """Editing another user's category returns 'not found' (same as nonexistent)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/edit",
                data={"group_name": "Hacked", "item_name": "Pwned"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"not found" in resp.data

            db.session.refresh(other["category"])
            assert other["category"].group_name == "Other"

    def test_edit_category_nonexistent(self, app, auth_client, seed_user):
        """Editing a nonexistent category returns 'not found'."""
        with app.app_context():
            resp = auth_client.post(
                "/categories/999999/edit",
                data={"group_name": "Ghost", "item_name": "Phantom"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"not found" in resp.data

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
