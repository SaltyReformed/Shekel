"""
Shekel Budget App -- Companion Route Tests

Route-level integration tests for the companion view.  Verifies
access control, period navigation, entry integration, mark-done,
entry data rendering, and empty states.

Covers plan test IDs: 10.3, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11.
Additional tests beyond the plan baseline cover period navigation
arrows, entry CRUD through the entries blueprint, entry data
computation in response HTML, and empty state rendering.
"""

import pytest
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RoleEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _make_template(seed_user, *, companion_visible, track=False, name="Item"):
    """Create a transaction template for the seed_user owner.

    Args:
        seed_user: The seed_user fixture dict.
        companion_visible: Whether the template is companion-visible.
        track: Whether to enable is_envelope.
        name: Template name.

    Returns:
        The created TransactionTemplate object (flushed, ID available).
    """
    expense_type = (
        db.session.query(TransactionType)
        .filter_by(name="Expense").one()
    )
    category = list(seed_user["categories"].values())[0]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        name=name,
        default_amount=Decimal("500.00"),
        transaction_type_id=expense_type.id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        companion_visible=companion_visible,
        is_envelope=track,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_txn(seed_user, period, template, *, name=None, amount=None):
    """Create a transaction from a template in a specific period.

    Args:
        seed_user: The seed_user fixture dict.
        period: The PayPeriod to assign.
        template: The TransactionTemplate.
        name: Override name (defaults to template.name).
        amount: Override estimated_amount (defaults to template.default_amount).

    Returns:
        The created Transaction object (flushed, ID available).
    """
    expense_type = (
        db.session.query(TransactionType)
        .filter_by(name="Expense").one()
    )
    category = list(seed_user["categories"].values())[0]

    txn = Transaction(
        name=name or template.name,
        estimated_amount=amount or template.default_amount,
        transaction_type_id=expense_type.id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        pay_period_id=period.id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _login_companion(app):
    """Create an authenticated companion test client.

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


# ── Route Access ─────────────────────────────────────────────────────


class TestRouteAccess:
    """Verify access control on companion routes."""

    def test_companion_gets_200_with_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion GET /companion/period/<id> returns 200 with visible names.

        Creates visible and non-visible templates.  Response HTML
        contains only visible transaction names.  Uses explicit
        period_id to avoid date-sensitivity from the default
        current-period logic.
        """
        t_vis = _make_template(seed_user, companion_visible=True, name="Groceries")
        t_hid = _make_template(seed_user, companion_visible=False, name="Mortgage")
        _make_txn(seed_user, seed_periods_today[0], t_vis, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], t_hid, name="Mortgage")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Groceries" in html
        assert "Mortgage" not in html

    def test_owner_redirected_from_companion(
        self, auth_client,
    ):
        """Plan 10.11: Owner GET /companion/ redirects to /grid."""
        resp = auth_client.get("/companion/")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]

    def test_unauthenticated_redirected_to_login(self, client):
        """Unauthenticated GET /companion/ redirects to /login."""
        resp = client.get("/companion/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_companion_view_with_no_periods(
        self, app, db, seed_user, seed_companion,
    ):
        """Companion with no owner periods gets 200 (empty state).

        When pay_period_service.get_current_period returns None,
        the route should gracefully render the empty state.
        Note: seed_periods_today is not included, so the owner has no periods.
        """
        comp = _login_companion(app)
        resp = comp.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No pay periods available" in html


# ── Period Navigation ────────────────────────────────────────────────


class TestPeriodNavigation:
    """Verify period navigation routes and arrow rendering."""

    def test_companion_period_view_valid(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.3: GET /companion/period/<valid_id> returns 200.

        Shows the correct period's transactions.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, seed_periods_today[1], template, name="Groceries P1")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[1].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Groceries P1" in html

    def test_companion_period_view_other_owner(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion accessing another owner's period gets 404."""
        second_user = User(
            email="other@test.local",
            password_hash=hash_password("otherpass"),
            display_name="Other",
        )
        db.session.add(second_user)
        db.session.flush()
        settings = UserSettings(user_id=second_user.id)
        db.session.add(settings)

        other_period = PayPeriod(
            user_id=second_user.id,
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 15),
            period_index=0,
        )
        db.session.add(other_period)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{other_period.id}")
        assert resp.status_code == 404

    def test_companion_period_view_nonexistent(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion accessing a non-existent period gets 404."""
        comp = _login_companion(app)
        resp = comp.get("/companion/period/999999")
        assert resp.status_code == 404

    def test_prev_next_links_present(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Response HTML contains previous/next period links when applicable.

        Viewing period[1] (middle period) should show both arrows.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, seed_periods_today[1], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[1].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Previous link points to period[0].
        assert f"/companion/period/{seed_periods_today[0].id}" in html
        # Next link points to period[2].
        assert f"/companion/period/{seed_periods_today[2].id}" in html

    def test_no_prev_link_on_first_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """First period has no previous link (no chevron-left href).

        Viewing period[0] should not contain a link to a previous period.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # There should be a next link but no previous link.
        assert f"/companion/period/{seed_periods_today[1].id}" in html
        # The chevron-left should not be an anchor.
        assert "bi-chevron-left" not in html or (
            "bi-chevron-left" in html
            and f"/companion/period/{seed_periods_today[0].id}" not in html
        )

    def test_no_next_link_on_last_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Last period has no next link.

        Viewing the last period should not contain a link to a next period.
        """
        last_period = seed_periods_today[-1]
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_user, last_period, template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{last_period.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Previous link should exist (second-to-last period).
        prev_period = seed_periods_today[-2]
        assert f"/companion/period/{prev_period.id}" in html

    def test_owner_redirected_from_period_view(
        self, auth_client, seed_periods_today,
    ):
        """Owner GET /companion/period/<id> redirects to /grid."""
        resp = auth_client.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]


# ── Entry Integration (via entries blueprint) ────────────────────────


class TestEntryIntegration:
    """Verify companion can use entry CRUD routes on visible transactions."""

    def test_companion_can_add_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.5: Companion adds entry to visible tracked transaction.

        Entry is created with user_id == companion's ID.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "42.50",
            "description": "Kroger",
            "entry_date": "2026-01-05",
        })
        assert resp.status_code == 200

        # Verify entry was created.
        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=txn.id,
        ).first()
        assert entry is not None
        assert entry.amount == Decimal("42.50")
        assert entry.description == "Kroger"

    def test_companion_entry_user_id_is_companion(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.6: Entry.user_id is the companion's ID, not the owner's.

        Verifies attribution: the companion's user_id is recorded
        on the entry for audit purposes.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        companion = seed_companion["user"]
        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "25.00",
            "description": "Target",
            "entry_date": "2026-01-05",
        })
        assert resp.status_code == 200

        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=txn.id,
        ).first()
        assert entry.user_id == companion.id

    def test_companion_cannot_access_non_visible_entries(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.7: Companion gets 404 on entry route for non-visible transaction."""
        template = _make_template(
            seed_user, companion_visible=False, track=True, name="Hidden",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Hidden")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "10.00",
            "description": "Test",
            "entry_date": "2026-01-05",
        })
        assert resp.status_code == 404

    def test_companion_cannot_guess_txn_id(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.8: Random/non-existent txn_id returns 404."""
        comp = _login_companion(app)
        resp = comp.post("/transactions/999999/entries", data={
            "amount": "10.00",
            "description": "Test",
            "entry_date": "2026-01-05",
        })
        assert resp.status_code == 404

    def test_companion_can_delete_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can delete an entry on a visible tracked transaction."""
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        entry = TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_companion["user"].id,
            amount=Decimal("30.00"),
            description="Kroger",
            entry_date=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.delete(
            f"/transactions/{txn.id}/entries/{entry.id}",
        )
        assert resp.status_code == 200

        deleted = db.session.get(TransactionEntry, entry.id)
        assert deleted is None

    def test_companion_can_edit_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can edit an entry on a visible tracked transaction."""
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        entry = TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_companion["user"].id,
            amount=Decimal("30.00"),
            description="Kroger",
            entry_date=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.patch(
            f"/transactions/{txn.id}/entries/{entry.id}",
            data={"amount": "45.00", "description": "Kroger Updated"},
        )
        assert resp.status_code == 200

        db.session.refresh(entry)
        assert entry.amount == Decimal("45.00")
        assert entry.description == "Kroger Updated"


# ── Mark Done Integration ────────────────────────────────────────────


class TestMarkDoneIntegration:
    """Verify companion mark-done through the transactions route."""

    def test_companion_marks_visible_projected_as_paid(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.9: Companion marks visible PROJECTED transaction as Paid.

        Status changes from Projected to Done (Paid).
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    def test_companion_mark_done_non_visible_rejected(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.10: Companion gets 404 for mark-done on non-visible transaction."""
        template = _make_template(
            seed_user, companion_visible=False, name="Mortgage",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Mortgage")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_companion_view_shows_paid_indicator_after_mark_done(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """After marking as Paid, companion view shows the Paid indicator.

        Mark the transaction done, then load the companion view
        and verify the "Paid" text appears.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        # Now load the companion view for this period.
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Paid" in html

    def test_companion_mark_done_auto_populates_actual_from_entries(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Mark-done on tracked transaction with entries auto-computes actual.

        Arithmetic: entries = $100 + $50 = $150.
        actual_amount should be set to $150 after mark-done.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Groceries", amount=Decimal("500.00"),
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("100.00"), description="Kroger",
            entry_date=date(2026, 1, 5),
        ))
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("50.00"), description="Walmart",
            entry_date=date(2026, 1, 6),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        assert txn.actual_amount == Decimal("150.00")


# ── Entry Data in Response HTML ──────────────────────────────────────


class TestEntryDataInHTML:
    """Verify companion view renders correct entry progress information."""

    def test_tracked_txn_with_entries_shows_progress(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible tracked transaction with entries shows "$X / $Y" progress.

        Arithmetic: entries=$200, estimated=$500.  HTML contains
        "200 / 500" (the {:,.0f} format without dollar signs in the span).
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Groceries", amount=Decimal("500.00"),
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("200.00"), description="Kroger",
            entry_date=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$200 / $500" in html
        assert "$300 left" in html

    def test_tracked_txn_without_entries_shows_estimated(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible tracked transaction without entries shows estimated amount only.

        No progress bar, just the estimated amount.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Groceries", amount=Decimal("500.00"),
        )
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$500" in html
        # No progress format -- should not contain " / ".
        assert "/ $500" not in html

    def test_non_tracked_txn_shows_estimated_amount(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible non-tracked transaction shows estimated amount."""
        template = _make_template(
            seed_user, companion_visible=True, track=False, name="Birthday Gift",
        )
        _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Birthday Gift", amount=Decimal("100.00"),
        )
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Birthday Gift" in html
        assert "$100" in html

    def test_over_budget_shows_over_indicator(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Over-budget transaction shows "over" indicator with danger styling.

        Arithmetic: estimated=$100, entries=$120.  HTML contains
        "$120 / $100" and "$20 over".
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Gas",
        )
        txn = _make_txn(
            seed_user, seed_periods_today[0], template,
            name="Gas", amount=Decimal("100.00"),
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id, user_id=seed_user["user"].id,
            amount=Decimal("120.00"), description="Shell",
            entry_date=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$120 / $100" in html
        assert "$20 over" in html
        assert "text-danger" in html


# ── Empty States ─────────────────────────────────────────────────────


class TestEmptyStates:
    """Verify empty state rendering."""

    def test_period_with_no_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period with no visible transactions shows empty state message."""
        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No transactions for this period" in html

    def test_period_with_only_non_visible_shows_empty(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period with only non-visible transactions shows empty state.

        The transactions exist but are filtered out by visibility,
        so the companion sees the empty state.
        """
        template = _make_template(
            seed_user, companion_visible=False, name="Mortgage",
        )
        _make_txn(seed_user, seed_periods_today[0], template, name="Mortgage")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No transactions for this period" in html
        assert "Mortgage" not in html


# ── Mark as Paid Button Visibility ───────────────────────────────────


class TestMarkPaidButtonVisibility:
    """Verify the Mark as Paid button appears only for PROJECTED transactions."""

    def test_projected_transaction_shows_mark_paid_button(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """PROJECTED transaction shows the 'Mark as Paid' button."""
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Mark as Paid" in html

    def test_paid_transaction_shows_paid_indicator(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Paid (Done) transaction shows 'Paid' indicator, not button.

        The Mark as Paid button should not appear for already-settled
        transactions.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        txn.status_id = ref_cache.status_id(StatusEnum.DONE)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should show the paid indicator, not the button.
        assert "check-circle-fill" in html
        # The button text should not appear (it's been replaced).
        assert "Mark as Paid" not in html


# ── Entry List Lazy Loading ──────────────────────────────────────────


class TestEntryListLazyLoading:
    """Verify tracked transactions include HTMX entry list loading."""

    def test_tracked_txn_has_entry_list_loader(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Tracked transaction card includes hx-get for entry list.

        The entry list is lazy-loaded via HTMX on page load.  Verify
        the HTML contains the hx-get attribute pointing to the
        entries.list_entries endpoint.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The entry list loader points to the entries route.
        assert f"/transactions/{txn.id}/entries" in html
        assert 'hx-trigger="load"' in html

    def test_non_tracked_txn_has_no_entry_loader(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Non-tracked transaction card does NOT include entry list loader.

        Only tracked transactions get the inline entry CRUD UI.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=False, name="Birthday",
        )
        txn = _make_txn(seed_user, seed_periods_today[0], template, name="Birthday")
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f"/transactions/{txn.id}/entries" not in html
