"""
Shekel Budget App -- Transaction Entry Route Tests

Tests the entry CRUD endpoints (list, create, update, delete),
ownership and companion access controls, HTMX response format,
entry-transaction mismatch guards, and popover integration.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrencePattern, Status, TransactionType
from app.models.user import User, UserSettings
from app.services import pay_period_service
from app.services.auth_service import hash_password


def _add_entry(txn, user, amount, description,
               entry_date=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Args:
        txn: Parent Transaction object.
        user: dict with 'user' key (seed_user shape) or User object.
        amount: Decimal-compatible string or Decimal.
        description: Entry description.
        entry_date: Date object (defaults to 2026-01-05).
        is_credit: Boolean.

    Returns:
        Committed TransactionEntry object.
    """
    uid = user["user"].id if isinstance(user, dict) else user.id
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=uid,
        amount=Decimal(str(amount)),
        description=description,
        entry_date=entry_date or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _create_visible_tracked_txn(seed_user, seed_periods):
    """Create a tracked, companion-visible template and transaction.

    Unlike seed_entry_template, this sets companion_visible=True at
    creation time to avoid session-expiry issues with in-place
    modification.

    Args:
        seed_user: The seed_user fixture dict.
        seed_periods: List of PayPeriod objects.

    Returns:
        dict with keys: template, transaction, category.
    """
    every_period = db.session.query(RecurrencePattern).filter_by(
        name="Every Period",
    ).one()
    expense_type = db.session.query(TransactionType).filter_by(
        name="Expense",
    ).one()
    projected = db.session.query(Status).filter_by(
        name="Projected",
    ).one()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    category = seed_user["categories"]["Groceries"]
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Weekly Groceries",
        default_amount=Decimal("500.00"),
        track_individual_purchases=True,
        companion_visible=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Weekly Groceries",
        category_id=category.id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("500.00"),
    )
    db.session.add(txn)
    db.session.commit()

    return {"template": template, "transaction": txn, "category": category}


def _login_companion(app):
    """Create a test client and log in as the companion user.

    Must be called within an app context, after seed_companion has
    created the companion user.

    Args:
        app: The Flask application.

    Returns:
        An authenticated FlaskClient for the companion user.
    """
    comp = app.test_client()
    resp = comp.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"Companion login failed with status {resp.status_code}"
    )
    return comp


def _create_other_user_txn():
    """Create a second owner with a pay period and an ad-hoc transaction.

    Used for cross-user isolation tests.  The transaction has no
    template and is not entry-capable.

    Returns:
        dict with keys: user, transaction.
    """
    other_user = User(
        email="other_owner@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other Owner",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    account = Account(
        user_id=other_user.id,
        account_type_id=checking_type.id,
        name="Other Checking",
        current_anchor_balance=Decimal("500.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Family",
        item_name="Groceries",
    )
    db.session.add(category)
    db.session.flush()

    periods = pay_period_service.generate_pay_periods(
        user_id=other_user.id,
        start_date=date(2026, 1, 2),
        num_periods=3,
        cadence_days=14,
    )
    db.session.flush()
    account.current_anchor_period_id = periods[0].id

    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = db.session.query(TransactionType).filter_by(
        name="Expense",
    ).one()

    txn = Transaction(
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected.id,
        name="Other Groceries",
        category_id=category.id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("300.00"),
    )
    db.session.add(txn)
    db.session.commit()

    return {"user": other_user, "transaction": txn}


# ---- List entries (GET) -------------------------------------------------

class TestListEntries:
    """Tests for GET /transactions/<txn_id>/entries."""

    def test_returns_partial_with_entries(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET with entries returns 200 and HTML containing descriptions and amounts."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _add_entry(txn, seed_user, "50.00", "Kroger")
            _add_entry(txn, seed_user, "30.00", "Target")

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Kroger" in resp.data
            assert b"Target" in resp.data
            assert b"50.00" in resp.data
            assert b"30.00" in resp.data

    def test_empty_state_message(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET with no entries shows empty state message."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"No purchases recorded yet" in resp.data

    def test_shows_remaining_balance(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET shows correct remaining balance (estimated - sum of entries)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Estimated = $500, entry = $200 -> remaining = $300.
            _add_entry(txn, seed_user, "200.00", "Costco")

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"300.00" in resp.data
            assert b"Remaining" in resp.data

    def test_remaining_negative_overspent(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Overspent remaining is displayed with text-danger class."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Estimated = $500, entries total = $530 -> remaining = -$30.
            _add_entry(txn, seed_user, "300.00", "Costco")
            _add_entry(txn, seed_user, "230.00", "Target")

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"text-danger" in resp.data
            assert b"30.00" in resp.data

    def test_nonexistent_transaction_returns_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """GET for nonexistent transaction returns 404."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/entries")
            assert resp.status_code == 404

    def test_other_user_transaction_returns_404(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """GET for another user's transaction returns 404 (IDOR guard)."""
        with app.app_context():
            other = _create_other_user_txn()
            resp = auth_client.get(
                f"/transactions/{other['transaction'].id}/entries",
            )
            assert resp.status_code == 404

    def test_html_contains_entry_list_container(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Response HTML contains the #entry-list-{txn_id} container div."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert f'id="entry-list-{txn.id}"'.encode() in resp.data

    def test_editing_param_shows_edit_form(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET with ?editing=<entry_id> renders an inline edit form."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry.id}",
            )
            assert resp.status_code == 200
            # Edit form uses hx-patch for submission.
            assert b"hx-patch" in resp.data

    def test_credit_entry_shows_cc_badge(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Credit entries display a CC badge in the entry list."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _add_entry(txn, seed_user, "40.00", "Amazon", is_credit=True)

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"CC" in resp.data
            assert b"bg-warning-subtle" in resp.data

    def test_out_of_period_entry_shows_warning(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Entries with dates outside the pay period show a warning icon."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Period 0 is 2026-01-02 to 2026-01-15.
            # Use a date well outside.
            _add_entry(
                txn, seed_user, "50.00", "Late Purchase",
                entry_date=date(2026, 2, 15),
            )

            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Date outside pay period range" in resp.data


# ---- Create entry (POST) ------------------------------------------------

class TestCreateEntry:
    """Tests for POST /transactions/<txn_id>/entries."""

    def test_create_with_valid_data(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with valid form data creates an entry and returns updated list."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            assert b"Kroger" in resp.data
            assert b"50.00" in resp.data

            # Verify database state.
            entries = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).all()
            assert len(entries) == 1
            assert entries[0].amount == Decimal("50.00")
            assert entries[0].description == "Kroger"

    def test_create_credit_entry(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with is_credit=on creates a credit entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "80.00",
                    "description": "Amazon",
                    "entry_date": "2026-01-06",
                    "is_credit": "on",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.is_credit is True
            assert b"CC" in resp.data

    def test_create_minimum_fields(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with only required fields uses default is_credit=False."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "25.50",
                    "description": "Gas Station",
                    "entry_date": "2026-01-04",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.is_credit is False
            assert entry.amount == Decimal("25.50")

    def test_validation_error_zero_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with amount=0 returns 422 validation error."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "0",
                    "description": "Test",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 422

    def test_validation_error_negative_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with negative amount returns 422 validation error."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "-5.00",
                    "description": "Test",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 422

    def test_validation_error_missing_fields(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with no form data returns 422 (missing required fields)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={},
            )
            assert resp.status_code == 422

    def test_validation_error_empty_description(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST with empty description returns 422 (stripped by pre_load, then missing)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 422

    def test_non_tracked_transaction_returns_400(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST on a non-tracked transaction returns 400 (service ValidationError)."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            # Ad-hoc transaction (no template, not entry-capable).
            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Phone Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Test",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 400

    def test_hx_trigger_balance_changed(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """POST returns HX-Trigger: balanceChanged header."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_entry_user_id_matches_current_user(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Created entry's user_id matches the authenticated user."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "entry_date": "2026-01-05",
                },
            )

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.user_id == seed_user["user"].id


# ---- Update entry (PATCH) -----------------------------------------------

class TestUpdateEntry:
    """Tests for PATCH /transactions/<txn_id>/entries/<entry_id>."""

    def test_update_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with new amount updates the entry, other fields unchanged."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.amount == Decimal("75.00")
            assert entry.description == "Kroger"

    def test_update_description(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with new description updates only that field."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"description": "Walmart"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.description == "Walmart"
            assert entry.amount == Decimal("50.00")

    def test_update_toggle_credit_on(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with is_credit=true toggles a debit entry to credit."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"is_credit": "true"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.is_credit is True

    def test_update_toggle_credit_off(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with is_credit=false toggles a credit entry to debit."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Amazon", is_credit=True,
            )

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"is_credit": "false"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.is_credit is False

    def test_update_validation_error_zero_amount(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with amount=0 returns 422 validation error."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "0"},
            )
            assert resp.status_code == 422

    def test_hx_trigger_balance_changed(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH returns HX-Trigger: balanceChanged header."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_nonexistent_entry_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH for nonexistent entry returns 404."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/999999",
                data={"amount": "75.00"},
            )
            assert resp.status_code == 404


# ---- Delete entry (DELETE) -----------------------------------------------

class TestDeleteEntry:
    """Tests for DELETE /transactions/<txn_id>/entries/<entry_id>."""

    def test_delete_removes_from_database(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE removes the entry from the database."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            entry_id = entry.id

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry_id}",
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_delete_returns_updated_list(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE response contains updated list without the deleted entry."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry1 = _add_entry(txn, seed_user, "50.00", "Kroger")
            _add_entry(txn, seed_user, "30.00", "Target")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry1.id}",
            )
            assert resp.status_code == 200
            assert b"Kroger" not in resp.data
            assert b"Target" in resp.data

    def test_nonexistent_entry_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE for nonexistent entry returns 404."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/999999",
            )
            assert resp.status_code == 404

    def test_hx_trigger_balance_changed(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE returns HX-Trigger: balanceChanged header."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry.id}",
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_delete_last_entry_shows_empty_state(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Deleting the last entry shows the empty state message."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry.id}",
            )
            assert resp.status_code == 200
            assert b"No purchases recorded yet" in resp.data


# ---- Manual is_cleared toggle ------------------------------------------

class TestToggleCleared:
    """Tests for PATCH /transactions/<txn_id>/entries/<entry_id>/cleared."""

    def test_toggle_from_false_to_true(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCHing toggles an uncleared entry to cleared."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            assert entry.is_cleared is False

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}/cleared",
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.expire_all()
            reloaded = db.session.get(TransactionEntry, entry.id)
            assert reloaded.is_cleared is True

    def test_toggle_from_true_to_false(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCHing toggles a cleared entry back to uncleared."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            entry.is_cleared = True
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}/cleared",
            )
            assert resp.status_code == 200

            db.session.expire_all()
            reloaded = db.session.get(TransactionEntry, entry.id)
            assert reloaded.is_cleared is False

    def test_toggle_nonexistent_entry_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH for a nonexistent entry returns 404."""
        with app.app_context():
            txn = seed_entry_template["transaction"]

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/999999/cleared",
            )
            assert resp.status_code == 404

    def test_toggle_cross_transaction_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with entry_id that doesn't belong to txn_id returns 404."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            # Create a second tracked transaction.
            other = _create_visible_tracked_txn(seed_user, seed_periods)
            other_txn = other["transaction"]

            resp = auth_client.patch(
                f"/transactions/{other_txn.id}/entries/{entry.id}/cleared",
            )
            assert resp.status_code == 404

            db.session.expire_all()
            # Entry is unchanged.
            assert db.session.get(
                TransactionEntry, entry.id,
            ).is_cleared is False

    def test_toggle_other_users_entry_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH on another user's entry returns 404."""
        with app.app_context():
            other = _create_other_user_txn()
            # Manually add an entry to the other user's transaction.
            other_entry = TransactionEntry(
                transaction_id=other["transaction"].id,
                user_id=other["user"].id,
                amount=Decimal("50.00"),
                description="Other",
                entry_date=date(2026, 1, 5),
                is_credit=False,
            )
            db.session.add(other_entry)
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{other['transaction'].id}"
                f"/entries/{other_entry.id}/cleared",
            )
            assert resp.status_code == 404

            db.session.expire_all()
            # Entry is unchanged.
            assert db.session.get(
                TransactionEntry, other_entry.id,
            ).is_cleared is False


# ---- Entry / transaction ID mismatch ------------------------------------

class TestEntryTransactionMismatch:
    """Tests that entry_id must belong to the txn_id in the URL."""

    def _create_second_tracked_txn(self, seed_user, seed_periods,
                                   seed_entry_template):
        """Create a second tracked transaction using the same template.

        Returns:
            Transaction object in the second pay period.
        """
        projected = db.session.query(Status).filter_by(
            name="Projected",
        ).one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()

        txn2 = Transaction(
            template_id=seed_entry_template["template"].id,
            pay_period_id=seed_periods[1].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Weekly Groceries",
            category_id=seed_entry_template["category"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("500.00"),
        )
        db.session.add(txn2)
        db.session.commit()
        return txn2

    def test_patch_entry_wrong_txn_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCH with entry_id from a different transaction returns 404."""
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            txn2 = self._create_second_tracked_txn(
                seed_user, seed_periods, seed_entry_template,
            )
            # Entry belongs to txn2.
            entry = _add_entry(txn2, seed_user, "50.00", "Aldi")

            # Try to PATCH via txn1's URL.
            resp = auth_client.patch(
                f"/transactions/{txn1.id}/entries/{entry.id}",
                data={"amount": "75.00"},
            )
            assert resp.status_code == 404

    def test_delete_entry_wrong_txn_returns_404(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """DELETE with entry_id from a different transaction returns 404."""
        with app.app_context():
            txn1 = seed_entry_template["transaction"]
            txn2 = self._create_second_tracked_txn(
                seed_user, seed_periods, seed_entry_template,
            )
            entry = _add_entry(txn2, seed_user, "50.00", "Aldi")

            resp = auth_client.delete(
                f"/transactions/{txn1.id}/entries/{entry.id}",
            )
            assert resp.status_code == 404

    def test_companion_mismatch_blocks_non_visible_entry(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template, seed_companion,
    ):
        """Companion cannot modify entries from non-visible txns via URL confusion.

        Attack scenario: companion sends PATCH to a visible transaction's
        URL but includes an entry_id belonging to a non-visible transaction.
        The entry-transaction mismatch guard returns 404.
        """
        with app.app_context():
            # Make the first template companion-visible.
            template_visible = seed_entry_template["template"]
            template_visible.companion_visible = True
            db.session.commit()

            txn_visible = seed_entry_template["transaction"]

            # Create a second template that is NOT companion-visible.
            every_period = db.session.query(
                RecurrencePattern,
            ).filter_by(name="Every Period").one()
            expense_type = db.session.query(
                TransactionType,
            ).filter_by(name="Expense").one()
            projected = db.session.query(
                Status,
            ).filter_by(name="Projected").one()

            rule2 = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_period.id,
            )
            db.session.add(rule2)
            db.session.flush()

            template_hidden = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_entry_template["category"].id,
                recurrence_rule_id=rule2.id,
                transaction_type_id=expense_type.id,
                name="Secret Groceries",
                default_amount=Decimal("300.00"),
                track_individual_purchases=True,
                companion_visible=False,
            )
            db.session.add(template_hidden)
            db.session.flush()

            txn_hidden = Transaction(
                template_id=template_hidden.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Secret Groceries",
                category_id=seed_entry_template["category"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add(txn_hidden)
            db.session.commit()

            # Entry on the hidden transaction.
            entry = _add_entry(txn_hidden, seed_user, "50.00", "Secret Store")

            # Companion logs in and uses visible txn URL with hidden entry ID.
            comp = _login_companion(app)
            resp = comp.patch(
                f"/transactions/{txn_visible.id}/entries/{entry.id}",
                data={"amount": "75.00"},
            )
            assert resp.status_code == 404


# ---- Companion access ----------------------------------------------------

class TestCompanionAccess:
    """Tests for companion user access to entry routes."""

    def test_companion_lists_entries_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can list entries on a companion-visible transaction."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            _add_entry(txn, seed_user, "50.00", "Kroger")

            comp = _login_companion(app)
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Kroger" in resp.data

    def test_companion_creates_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can create entries; entry.user_id is the companion's ID."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]

            comp = _login_companion(app)
            resp = comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "45.00",
                    "description": "Aldi",
                    "entry_date": "2026-01-05",
                },
            )
            assert resp.status_code == 200

            entry = db.session.query(TransactionEntry).filter_by(
                transaction_id=txn.id,
            ).one()
            assert entry.user_id == seed_companion["user"].id

    def test_companion_updates_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can update entries on companion-visible transactions."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            comp = _login_companion(app)
            resp = comp.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "60.00"},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            entry = db.session.get(TransactionEntry, entry.id)
            assert entry.amount == Decimal("60.00")

    def test_companion_deletes_entry_on_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion can delete entries on companion-visible transactions."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            entry_id = entry.id

            comp = _login_companion(app)
            resp = comp.delete(
                f"/transactions/{txn.id}/entries/{entry_id}",
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(TransactionEntry, entry_id) is None

    def test_companion_rejected_for_non_visible_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template, seed_companion,
    ):
        """Companion gets 404 for transactions not flagged companion_visible."""
        with app.app_context():
            # companion_visible defaults to False.
            txn = seed_entry_template["transaction"]
            comp = _login_companion(app)
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 404

    def test_companion_rejected_for_other_owner_txn(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Companion gets 404 for transactions belonging to a different owner."""
        with app.app_context():
            other = _create_other_user_txn()
            comp = _login_companion(app)
            resp = comp.get(
                f"/transactions/{other['transaction'].id}/entries",
            )
            assert resp.status_code == 404

    def test_both_users_see_all_entries(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """Both owner and companion see all entries on a shared transaction."""
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            # Owner creates an entry.
            _add_entry(txn, seed_user, "50.00", "Kroger")
            # Companion creates an entry via route.
            comp = _login_companion(app)
            comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "30.00",
                    "description": "Aldi",
                    "entry_date": "2026-01-06",
                },
            )

            # Owner sees both.
            resp = auth_client.get(f"/transactions/{txn.id}/entries")
            assert b"Kroger" in resp.data
            assert b"Aldi" in resp.data

            # Companion sees both.
            resp = comp.get(f"/transactions/{txn.id}/entries")
            assert b"Kroger" in resp.data
            assert b"Aldi" in resp.data


# ---- Popover integration ------------------------------------------------

class TestPopoverIntegration:
    """Tests for entry section rendering in the full edit popover."""

    def test_tracked_txn_has_entries_section(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Full edit popover for tracked transaction contains entries section."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" in resp.data
            # Verify the lazy-load hx-get URL is present.
            expected_url = f"/transactions/{txn.id}/entries"
            assert expected_url.encode() in resp.data

    def test_non_tracked_txn_no_entries_section(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Full edit popover for non-tracked transaction has no entries section."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()

            txn = Transaction(
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Phone Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" not in resp.data
