"""
Shekel Budget App -- Dashboard Entry Progress Tests (OP-1)

Tests the spending progress indicator on the dashboard's upcoming
bills section for tracked (entry-capable) transactions.

Covers:
  - Route-level: progress format "$X / $Y" for tracked + projected + entries.
  - Route-level: under-budget and over-budget visual treatment.
  - Route-level: tracked with no entries shows standard display.
  - Route-level: non-tracked regression (standard display unchanged).
  - Route-level: credit-only entries render progress correctly.
  - Route-level: HTMX bills-section partial reflects progress.
  - Service-level: bill dict entry fields are populated correctly.
  - Service-level: remaining balance and over-budget flag.
  - Regression: dashboard loads with mixed tracked/plain bills.
  - Regression: mark-paid response suppresses progress display.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import dashboard_service, pay_period_service


# -- Helpers ---------------------------------------------------------


def _current_period_for(user_id, seed_periods):
    """Return the current period for the user, falling back to seed_periods[0].

    Mirrors the pattern used in test_dashboard.py so tests work even
    when today's date does not fall inside a seed_periods entry.
    """
    period = pay_period_service.get_current_period(user_id)
    if period is None:
        period = seed_periods[0]
    return period


def _create_tracked_txn_in_period(
    seed_user, period, name="Groceries",
    estimated=Decimal("500.00"),
):
    """Create a tracked expense transaction in the given period.

    Builds a template with track_individual_purchases=True, then a
    projected expense transaction bound to that template in the
    supplied period.  The transaction's due_date is the period start
    date so it sorts predictably in the bills list.

    Returns:
        tuple of (Transaction, TransactionTemplate).
    """
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    projected = db.session.query(Status).filter_by(name="Projected").one()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=estimated,
        track_individual_purchases=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        template_id=template.id,
        estimated_amount=estimated,
        due_date=period.start_date,
    )
    db.session.add(txn)
    db.session.flush()
    return txn, template


def _create_plain_txn_in_period(
    seed_user, period, name="Rent", estimated=Decimal("1200.00"),
):
    """Create a non-tracked ad-hoc expense transaction in the given period.

    No template, so track_individual_purchases is implicitly absent.
    Used to verify that non-tracked bills render with the standard
    amount display (no progress indicator).
    """
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    projected = db.session.query(Status).filter_by(name="Projected").one()

    category_key = name if name in seed_user["categories"] else "Rent"

    txn = Transaction(
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name=name,
        category_id=seed_user["categories"][category_key].id,
        transaction_type_id=expense_type.id,
        estimated_amount=estimated,
        due_date=period.start_date,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _add_entry(
    txn, seed_user, amount, is_credit=False, description="Purchase",
):
    """Add a purchase entry to a transaction."""
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        entry_date=date(2026, 4, 12),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


# -- Route-level: progress display ----------------------------------


class TestDashboardEntryProgressDisplay:
    """Route tests for the progress display on the dashboard bills section."""

    def test_tracked_with_entries_shows_progress(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked + projected + entries: bill row shows '$X / $Y' format.

        Arithmetic: 150 + 80 debit + 100 credit = 330 total on 500 budget.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$330.00 / $500.00" in html

    def test_tracked_with_no_entries_shows_standard(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked txn with no entries: standard amount (no progress)."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            _create_tracked_txn_in_period(
                seed_user, period, name="Groceries",
                estimated=Decimal("500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Groceries" in html
            assert "$500.00" in html
            # No slash progress format when entries are absent.
            assert " / $500.00" not in html

    def test_non_tracked_shows_standard_regression(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Non-tracked txn: standard amount unchanged (regression)."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            _create_plain_txn_in_period(
                seed_user, period, name="Rent",
                estimated=Decimal("1200.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Rent" in html
            assert "$1,200.00" in html
            assert " / $1,200.00" not in html

    def test_tracked_over_budget_uses_text_danger(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked txn over budget: progress span has text-danger styling.

        Arithmetic: 300 + 250 = 550 spent on 500 budget, 50 over.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("300.00"))
            _add_entry(txn, seed_user, Decimal("250.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$550.00 / $500.00" in html
            # The progress span should have the over-budget styling.
            assert "text-danger fw-semibold" in html

    def test_tracked_under_budget_no_text_danger(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked txn under budget: progress span does NOT use text-danger."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$200.00 / $500.00" in html
            # Over-budget styling must not be present when under budget.
            assert "text-danger fw-semibold" not in html

    def test_tracked_credit_only_entries_show_progress(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked txn with only credit entries: progress uses credit sum."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("75.00"), is_credit=True)
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$75.00 / $500.00" in html

    def test_progress_title_tooltip_contains_remaining_and_count(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Progress span has a title tooltip with remaining and entry count.

        Arithmetic: 200 spent on 500 budget, 300 remaining, 2 entries.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("125.00"))
            _add_entry(txn, seed_user, Decimal("75.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The title attribute embeds a human-readable tooltip.
            assert "$300.00 remaining" in html
            assert "2 entries" in html


# -- Service-level: data computation --------------------------------


class TestDashboardServiceEntryFields:
    """Tests the entry progress fields on the bill dict from the service."""

    def test_bill_dict_entry_fields_for_tracked_with_entries(
        self, app, seed_user, seed_periods,
    ):
        """Bill dict has correct entry fields for tracked txn with entries.

        Arithmetic: 150 + 80 debit + 100 credit = 330 total, remaining 170.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            data = dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            bills = data["upcoming_bills"]
            bill = next((b for b in bills if b["id"] == txn.id), None)
            assert bill is not None
            assert bill["is_tracked"] is True
            # 150 + 80 + 100 = 330
            assert bill["entry_total"] == Decimal("330.00")
            assert bill["entry_count"] == 3
            # 500 - 330 = 170
            assert bill["entry_remaining"] == Decimal("170.00")
            assert bill["entry_over_budget"] is False

    def test_bill_dict_entry_fields_tracked_no_entries(
        self, app, seed_user, seed_periods,
    ):
        """Tracked txn without entries has is_tracked=True but null progress."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            db.session.commit()

            data = dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            bills = data["upcoming_bills"]
            bill = next((b for b in bills if b["id"] == txn.id), None)
            assert bill is not None
            assert bill["is_tracked"] is True
            assert bill["entry_total"] is None
            assert bill["entry_count"] == 0
            assert bill["entry_remaining"] is None
            assert bill["entry_over_budget"] is False

    def test_bill_dict_entry_fields_non_tracked(
        self, app, seed_user, seed_periods,
    ):
        """Non-tracked txn has is_tracked=False and null progress fields."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn = _create_plain_txn_in_period(
                seed_user, period, name="Rent",
                estimated=Decimal("1200.00"),
            )
            db.session.commit()

            data = dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            bills = data["upcoming_bills"]
            bill = next((b for b in bills if b["id"] == txn.id), None)
            assert bill is not None
            assert bill["is_tracked"] is False
            assert bill["entry_total"] is None
            assert bill["entry_count"] == 0
            assert bill["entry_remaining"] is None
            assert bill["entry_over_budget"] is False

    def test_bill_dict_over_budget_flag_and_negative_remaining(
        self, app, seed_user, seed_periods,
    ):
        """Tracked over-budget: entry_over_budget=True, remaining is negative.

        Arithmetic: single entry of 550 on 500 budget -> remaining -50.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("550.00"))
            db.session.commit()

            data = dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            bills = data["upcoming_bills"]
            bill = next((b for b in bills if b["id"] == txn.id), None)
            assert bill is not None
            assert bill["entry_total"] == Decimal("550.00")
            assert bill["entry_remaining"] == Decimal("-50.00")
            assert bill["entry_over_budget"] is True

    def test_bill_dict_exact_at_budget_not_over(
        self, app, seed_user, seed_periods,
    ):
        """Tracked txn with entries summing exactly to budget: not over.

        Arithmetic: 200 + 300 = 500 on 500 budget -> remaining 0, not over.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("200.00"))
            _add_entry(txn, seed_user, Decimal("300.00"))
            db.session.commit()

            data = dashboard_service.compute_dashboard_data(
                seed_user["user"].id,
            )
            bills = data["upcoming_bills"]
            bill = next((b for b in bills if b["id"] == txn.id), None)
            assert bill is not None
            assert bill["entry_total"] == Decimal("500.00")
            assert bill["entry_remaining"] == Decimal("0.00")
            assert bill["entry_over_budget"] is False


# -- Regression tests ------------------------------------------------


class TestDashboardRegressionWithEntries:
    """Regression tests for dashboard behavior with entry-capable transactions."""

    def test_dashboard_loads_no_tracked_transactions(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Dashboard loads when only non-tracked transactions exist."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            _create_plain_txn_in_period(seed_user, period, name="Rent")
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Rent" in resp.data
            assert b"Upcoming Bills" in resp.data

    def test_dashboard_loads_tracked_with_no_entries(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Dashboard loads when a tracked txn exists but has no entries."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Groceries" in resp.data
            html = resp.data.decode()
            assert "$500.00" in html
            assert " / $500.00" not in html

    def test_dashboard_mixed_tracked_and_plain_bills(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Dashboard renders tracked and plain bills side-by-side correctly."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            tracked, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(tracked, seed_user, Decimal("200.00"))
            _create_plain_txn_in_period(
                seed_user, period, name="Rent",
                estimated=Decimal("1200.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Tracked bill shows progress format.
            assert "$200.00 / $500.00" in html
            # Plain bill shows standard format, no progress.
            assert "Rent" in html
            assert "$1,200.00" in html
            assert " / $1,200.00" not in html

    def test_dashboard_htmx_bills_refresh_shows_progress(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HTMX bills-section partial renders progress for tracked txns."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("125.50"))
            db.session.commit()

            resp = auth_client.get(
                "/dashboard/bills",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$125.50 / $500.00" in html

    def test_dashboard_balance_info_present_with_tracked_entries(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Balance and runway section still renders when tracked entries exist.

        Ensures OP-1 did not break the balance/runway pipeline for
        dashboards with entry data.  Does not assert specific balance
        values -- balance correctness with entries is covered by
        test_balance_calculator_entries.py.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Balance section is always present when a default account
            # exists.  Confirm the section header and the amount area.
            assert "Balance" in html


# -- Mark-paid regression -------------------------------------------


class TestMarkPaidWithTrackedBill:
    """Tests that mark_paid handles tracked transactions correctly."""

    def test_mark_paid_tracked_returns_paid_row_without_progress(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Mark-paid on a tracked bill returns a paid row with no progress."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn_id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"bill-row--paid" in resp.data
            html = resp.data.decode()
            # Paid rows must not show the progress format even for
            # tracked bills -- the status has left PROJECTED and the
            # template suppresses the progress span.
            assert " / $500.00" not in html
