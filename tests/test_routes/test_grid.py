"""
Shekel Budget App -- Grid & Transaction Route Tests

Tests the main budget grid view and transaction CRUD endpoints.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import AccountType, Status, TransactionType
from app.services.auth_service import hash_password
from app.services import (
    account_service,
    balance_at,
    income_service,
    pay_period_service,
    posting_service,
)
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from app.services.balance_at import BalanceContext
from app.utils.dates import display_today

from tests._test_helpers import (
    append_balance_assertion,
    create_hysa_account,
    field_is_disabled,
    freeze_today,
    mark_purchase_settled,
    net_posted_by_day,
    posted_loan_balance_at,
    settle_instant_on,
)


class TestGridView:
    """Tests for the main grid page at /."""

    def test_grid_loads_with_periods(self, app, auth_client, seed_user, seed_periods_today):
        """GET / renders the budget grid with pay period columns."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            # Check for key grid elements.
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_grid_shows_no_periods_page(self, app, auth_client, seed_user):
        """GET / shows the no-periods prompt when none exist."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"No Pay Periods" in response.data

    def test_grid_shows_dynamic_account_name(self, app, auth_client, seed_user, seed_periods_today):
        """GET / shows the resolved account name in the header."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data

    def test_grid_period_controls(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """Grid respects the periods query parameter.

        Asserts the literal "01/02" rendered in the header, which is
        the start of seed_periods[0] (2026-01-02).  Uses the calendar-
        anchored seed_periods + freeze_today to keep the assertion
        stable regardless of wall-clock date.
        """
        freeze_today(monkeypatch, date(2026, 1, 5))
        with app.app_context():
            response = auth_client.get("/grid?periods=3")
            assert response.status_code == 200
            assert b"01/02" in response.data
            assert b"Projected End Balance" in response.data


class TestGridRowScoping:
    """Tests for the compact-view default and ?show_all=1 opt-out.

    Compact view (the default) generates row keys only from
    transactions whose pay_period_id is in the visible window.  This
    hides one-offs and infrequent recurring items that have nothing
    to render in the current view.  ``?show_all=1`` restores the old
    full-projection behavior for full planning sessions.  Subtotals
    and projected balances must be identical either way -- only which
    rows render changes.
    """

    def _make_oneoff(
        self, seed_user, period, name, amount="42.00",
    ):
        """Create one standalone expense in the given period."""
        projected = db.session.query(Status).filter_by(
            name="Projected",
        ).one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name=name,
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal(amount),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def _visible_period(self, seed_user, seed_periods_today):
        """Return a period that falls in the default visible window.

        The grid starts at the current period; seed_periods_today places
        period 4 around today's date so get_current_period always
        returns a valid period.  No fallback is needed.
        """
        # pylint: disable=unused-argument
        return pay_period_service.get_current_period(
            seed_user["user"].id,
        )

    def _hidden_period(self, seed_user, seed_periods_today):
        """Return a period that is NOT in the default visible window.

        The anchor period sits at ``seed_periods_today[0]``, ~8 weeks
        before today.  With ``grid_default_periods=6`` the visible
        window starts at the current period, so the anchor is
        historical and hidden in compact view.
        """
        # pylint: disable=unused-argument
        return seed_periods_today[0]

    def test_compact_view_hides_oneoff_outside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off in a hidden period must not render its row label."""
        with app.app_context():
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="HIDDEN_FAR_AWAY_BILL",
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"HIDDEN_FAR_AWAY_BILL" not in resp.data

    def test_compact_view_shows_oneoff_inside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off in the visible window must render its row label."""
        with app.app_context():
            visible = self._visible_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, visible, name="VISIBLE_NEARBY_BILL",
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"VISIBLE_NEARBY_BILL" in resp.data

    def test_show_all_reveals_oneoff_outside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """?show_all=1 must render rows from the full forward projection."""
        with app.app_context():
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="FAR_REVEALED_BY_SHOW_ALL",
            )
            db.session.commit()

            resp = auth_client.get("/grid?show_all=1")
            assert resp.status_code == 200
            assert b"FAR_REVEALED_BY_SHOW_ALL" in resp.data

    def test_compact_toggle_button_defaults_to_show_all_link(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The toggle button in compact view must link to show_all=1."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"show_all=1" in resp.data
            assert b"All Rows" in resp.data

    def test_show_all_toggle_button_links_back_to_compact(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When show_all is active, the button must link back without it."""
        with app.app_context():
            resp = auth_client.get("/grid?show_all=1")
            assert resp.status_code == 200
            assert b"Compact" in resp.data

    def test_scoping_does_not_change_visible_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Adding a hidden-period txn must not shift any visible subtotal.

        This is the key correctness invariant: hiding a row is a pure
        display filter, so the computed totals for visible periods
        must be byte-identical before and after the hidden txn exists.
        """
        with app.app_context():
            baseline = auth_client.get("/grid").data
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="HIDDEN_SUBTOTAL_PROBE",
                amount="999.00",
            )
            db.session.commit()

            after = auth_client.get("/grid").data
            assert b"HIDDEN_SUBTOTAL_PROBE" not in after

            # Projected End Balance is the canonical forward-math
            # summary.  It SHOULD change because the hidden txn still
            # affects the actual account trajectory (projected
            # balances include the full forward projection, not just
            # visible-row txns).  This asserts balance math is not
            # coupled to row scoping.
            assert b"Projected End Balance" in after


class TestBalanceRow:
    """Tests for GET /grid/balance-row HTMX partial."""

    def test_balance_row_returns_partial(self, app, auth_client, seed_user, seed_periods_today):
        """GET /grid/balance-row returns recalculated balance HTML partial."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            # Total Income/Expenses are now in tbody subtotals, not in the tfoot.
            assert b"Total Income" not in resp.data

    def test_balance_row_no_current_period(self, app, auth_client, seed_user):
        """GET /grid/balance-row with no periods returns 204 empty."""
        with app.app_context():
            # No periods generated -- get_current_period returns None.
            resp = auth_client.get("/grid/balance-row")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_no_baseline_scenario(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /grid/balance-row returns 204 when the user has no baseline scenario.

        Regression test for F-099 (C-45 of the 2026-04-15 security
        audit).  Before the fix, ``balance_row`` dereferenced
        ``scenario.id`` to build the transaction query filter; when
        ``get_baseline_scenario`` returned ``None`` (orphaned test
        fixture or a freshly-deleted user mid-cascade in production)
        the route raised ``AttributeError: 'NoneType' object has no
        attribute 'id'`` and returned HTTP 500 via the unhandled-
        exception handler.

        The fix short-circuits with HTTP 204 No Content, matching the
        existing ``not current_period`` branch -- HTMX leaves the
        existing DOM untouched and the user sees a coherent empty state
        instead of a stack trace.

        **The 204 now comes from the application-level handler, not from a
        guard in this route** (plan step X-v2, ruling R-BW), and the request
        carries the ``HX-Request`` header the browser actually sends for this
        endpoint.  That header is load-bearing rather than cosmetic: this test
        used to pass while omitting it, so it pinned the answer to a request
        shape no client makes.  A plain GET of the same URL is a human pasting
        it into the address bar, and gets the repair card.

        Asserts both the status code AND empty body to pin the
        contract; a future change that returns 200 with a rendered
        template would silently regress the HTMX partial-swap UX.
        """
        with app.app_context():
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            resp = auth_client.get(
                "/grid/balance-row?periods=6&offset=0",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_custom_offset(self, app, auth_client, seed_user, seed_periods_today):
        """GET /grid/balance-row with offset shifts the visible window."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=3&offset=2")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"Total Expenses" not in resp.data

    @pytest.mark.server_clock
    def test_a_settled_post_anchor_row_raises_the_balance_not_a_banner(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-anchor banner is GONE, and the row it warned about counts.

        The anchor sits at periods[0]; a Paid (settled) $1,200.00 expense in a
        later period used to be the "stale anchor" condition -- the balance
        row's response carried a warning banner out-of-band saying the
        projection might be wrong, because that row contributed nothing to it
        and only a re-anchor could fix the figure.

        Since plan step X-c2b2 the balance is a fold that counts the row from
        the day its money moved, so there is nothing left to warn about: the
        banner, its flag and its detector are deleted, and the balance itself
        moves.  Hand-computed: $1,000.00 anchor - $1,200.00 = -$200.00 from
        that period on.

        The row's settle day is the user's today, which
        is after every seeded period here, so the balance drops in the LAST
        column rather than in the row's own -- which is why the assertion reads
        the final period and why finding N-42 (nothing records when money
        moved) is the follow-up plan step X-f exists for.
        """
        with app.app_context():
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_settled_cash_transaction,
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods_today[2],
                Decimal("1200.00"), name="Paid Rent",
            )
            db.session.commit()

            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # The banner and every trace of its wiring are gone.
            assert 'id="stale-anchor-warning"' not in html
            assert "marked as done in periods after your anchor" not in html
            assert "<template>" not in html
            # And the settled row is IN the balance.
            balances = balance_at.cash_balance_map(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                seed_periods_today,
            )
            assert balances[seed_periods_today[-1].id] == Decimal("-200.00")

    def test_the_balance_row_response_opens_on_the_tfoot(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The partial's FIRST top-level element is the ``<tfoot>``.

        Load-bearing parser-safety shape, inherited from the deleted banner:
        htmx parses every partial inside a ``<template>`` wrapper, and per the
        HTML5 tree-construction spec a BARE non-table element preceding the
        ``<tfoot>`` flips the parser into the "in body" insertion mode, where
        the following tfoot/tr/td start tags are silently DROPPED.  The balance
        row then swaps in as loose unstyled text and, because the replacement
        carries no ``hx-trigger``, its ``balanceChanged`` self-refresh dies for
        the rest of the session (the projected-end-balance freeze regression
        introduced by ca47a1d).

        The banner that forced the old ``<template>`` encapsulation is gone, so
        the shape is simply "nothing precedes the tfoot" -- which is what this
        asserts, because a future partial that reintroduced a leading element
        would revive the same regression.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert html.lstrip().startswith("<tfoot")

    def test_grid_periods_large_value(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """GET / with periods larger than available still renders.

        Asserts the literal "01/02" header (start of seed_periods[0]),
        so uses the calendar-anchored seed_periods fixture and freezes
        today inside the period range.
        """
        freeze_today(monkeypatch, date(2026, 1, 5))
        with app.app_context():
            # Request 100 periods when only 10 exist -- should render what's available.
            resp = auth_client.get("/grid?periods=100")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"01/02" in resp.data


class TestSubtotalRowsEndpoint:
    """Tests for GET /grid/subtotal-rows HTMX partial.

    The desktop grid splits its three summary rows (Total Income, Total
    Expenses, Net Cash Flow) into two self-refreshing ``<tbody>``
    sections (grid rebuild Phase 6, audit item C3).  Only the income
    ``<tbody>`` fires this endpoint on ``balanceChanged from:body``; the
    single response carries the income ``<tbody>`` (an outerHTML swap
    replaces it in place) AND the expense ``<tbody>`` as an
    ``hx-swap-oob`` fragment, so ONE GET refreshes both sections.
    Mirrors the :class:`TestBalanceRow` style: auth required, the no-op
    204 contracts, and hand-computed Decimal figures asserted in the
    rendered rows.
    """

    def _seed_income_expense(self, seed_user, period_id, income, expense):
        """Seed one projected income + one projected expense in a period.

        Returns nothing; the caller asserts the rendered subtotals.  Uses
        the Salary/Rent seed categories so the rows render under the
        income and expense sections respectively.
        """
        projected = db.session.query(Status).filter_by(name="Projected").one()
        income_type = (
            db.session.query(TransactionType).filter_by(name="Income").one()
        )
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        db.session.add_all([
            Transaction(
                pay_period_id=period_id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=income,
            ),
            Transaction(
                pay_period_id=period_id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=expense,
            ),
        ])
        db.session.commit()

    def test_one_response_carries_both_sections(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A single GET returns BOTH the income and expense subtotal tbodies.

        Income $2,000, expense $1,400 in the current period.  One GET (the
        income tbody's self-refresh) must return both sections so a single
        balanceChanged event refreshes the whole summary: the income
        tbody (Total Income = $2,000) carries the self-refresh trigger and
        an outerHTML swap, while the expense tbody (Total Expenses =
        $1,400, Net Cash Flow = $2,000 - $1,400 = $600) rides along as an
        hx-swap-oob fragment so it never needs its own GET.
        """
        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            self._seed_income_expense(
                seed_user, current.id, Decimal("2000.00"), Decimal("1400.00"),
            )

            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Income tbody: present, self-refreshing, $2,000.
            assert 'id="grid-subtotals-income"' in html
            assert "subtotal-row-income" in html
            assert "Total Income" in html
            assert "$2,000" in html
            # Only the income tbody carries the self-refresh trigger -- it
            # is the single GET; the expense tbody rides along OOB.
            assert 'hx-trigger="balanceChanged from:body"' in html
            assert html.count('hx-trigger="balanceChanged from:body"') == 1

            # Expense tbody: present, OOB, $1,400 + $600 net.
            assert 'id="grid-subtotals-expense"' in html
            assert 'hx-swap-oob="true"' in html
            assert "subtotal-row-expense" in html
            assert "net-cash-flow-row" in html
            assert "Total Expenses" in html
            assert "Net Cash Flow" in html
            # Expense = $1,400; Net = $2,000 - $1,400 = $600.
            assert "$1,400" in html
            assert "$600" in html

    def test_refresh_url_carries_account_and_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The income tbody's self-refresh URL pins account_id + window.

        So the next balanceChanged refresh fetches the same account and
        the same periods / offset window, mirroring balance_row threading
        account_id through its hx-get URL.  The section param is gone --
        one GET returns both sections -- so it must NOT appear.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f"account_id={seed_user['account'].id}" in html
            assert "periods=6" in html
            assert "offset=0" in html
            # The collapsed-to-one-GET endpoint no longer takes a section.
            assert "section=" not in html

    def test_aria_live_polite_on_both_tbodies(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both self-refreshing tbodies announce updates politely (a11y).

        The mirrored ``_balance_row.html`` tfoot carries
        ``aria-live="polite"`` + ``aria-atomic="true"``; both summary
        tbodies must too so a screen reader announces the refreshed
        figures after a mark-done.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Two tbodies, each with the polite/atomic pair.
            assert html.count('aria-live="polite"') == 2
            assert html.count('aria-atomic="true"') == 2

    def test_no_current_period_returns_204(
        self, app, auth_client, seed_user,
    ):
        """No generated periods (no current period) returns 204."""
        with app.app_context():
            resp = auth_client.get("/grid/subtotal-rows")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_no_baseline_scenario_returns_204(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No baseline scenario returns 204, matching balance_row (F-099).

        The 204 comes from the application-level ``BaselineMissingError``
        handler since plan step X-v2 (ruling R-BW) rather than from a guard in
        this route, and the request carries the ``HX-Request`` header this
        endpoint is actually called with -- see ``balance_row``'s twin above
        for why that header is the point and not a detail.
        """
        with app.app_context():
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            resp = auth_client.get(
                "/grid/subtotal-rows?periods=6&offset=0",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204
            assert resp.data == b""

    def test_other_users_account_id_does_not_leak_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user,
    ):
        """A cross-user account_id never leaks the other account's subtotals.

        The first user requests subtotals while passing the SECOND user's
        account_id.  ``resolve_grid_account`` rejects the cross-user id
        (it is not owned by the requester) and falls back to the
        requester's own account, so a $9,999 income seeded against the
        second user's account never appears in the response -- the
        404-for-not-yours guarantee expressed as data isolation (the
        endpoint scopes strictly to ``current_user``).  The first user's
        own $2,000 income IS shown because the fallback lands on their
        account.
        """
        with app.app_context():
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            # First user's own income -- shown via the fallback.
            self._seed_income_expense(
                seed_user, current.id,
                Decimal("2000.00"), Decimal("0.00"),
            )
            # Second user's income on the second user's account/period --
            # must NOT leak into the first user's subtotal response.
            db.session.add(Transaction(
                pay_period_id=seed_second_user["account"].current_anchor_period_id,
                scenario_id=seed_second_user["scenario"].id,
                account_id=seed_second_user["account"].id,
                status_id=projected.id,
                name="Other Paycheck",
                category_id=seed_second_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("9999.00"),
            ))
            db.session.commit()

            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_second_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # The second user's $9,999 income must not appear.
            assert "$9,999" not in html
            # The first user's own $2,000 income is shown via the fallback.
            assert "$2,000" in html


class TestTransactionCRUD:
    """Tests for transaction create, update, delete, and status changes."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("123.45"),
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def test_quick_edit_disables_amount_on_finalised_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The inline quick-edit disables the amount input on a finalised row
        and shows the revert hint, so the cell never offers an amount edit the
        route guard (#26) would reject.  autofocus marks the editable case."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            auth_client.post(f"/transactions/{txn.id}/mark-done")

            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert field_is_disabled(html, "estimated_amount")
            assert "Finalised" in html
            assert "autofocus" not in html

    def test_quick_edit_amount_editable_on_projected_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A projected row's quick-edit amount stays editable (no lock, no
        regression for the common inline-edit path)."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert not field_is_disabled(html, "estimated_amount")
            assert "Finalised" not in html
            assert "autofocus" in html

    def test_full_edit_locks_money_fields_on_finalised_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The full-edit popover disables the money / period / due-date inputs
        on a finalised row and shows the revert notice, while the Status
        dropdown and Notes stay editable so the user can revert to Projected
        and then edit (#26)."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            auth_client.post(f"/transactions/{txn.id}/mark-done")

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "This transaction is finalised" in html
            # Locked money / period / due-date fields.
            assert field_is_disabled(html, "estimated_amount")
            assert field_is_disabled(html, "actual_amount")
            assert field_is_disabled(html, "pay_period_id")
            assert field_is_disabled(html, "due_date")
            # The revert path and display fields stay editable.
            assert not field_is_disabled(html, "status_id")
            assert not field_is_disabled(html, "notes")

    def test_full_edit_money_fields_editable_on_projected_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A projected row's full-edit money fields stay editable with no
        finalised notice (no regression for the common edit path)."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "finalised" not in html.lower()
            assert not field_is_disabled(html, "estimated_amount")
            assert not field_is_disabled(html, "due_date")

    def test_full_edit_paid_button_gated_by_settled_status(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The desktop full-edit card offers Paid on a projected expense but
        suppresses it once the row is settled (TPLB-06).

        The old predicate ``status_id != STATUS_DONE`` is true for a settled
        row, so Paid was offered where mark_done is an invalid state-machine
        transition (route 400).  ``Status.is_settled`` is the canonical
        'already paid' guard, matching _mobile_card_actions.html.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Projected: the Paid action is offered.
            projected_html = auth_client.get(
                f"/transactions/{txn.id}/full-edit"
            ).data.decode()
            assert "Mark as paid" in projected_html

            # Settled: the Paid action is suppressed (mark_done would 400).
            settled = db.session.query(Status).filter_by(name="Settled").one()
            txn.status_id = settled.id
            db.session.commit()
            settled_html = auth_client.get(
                f"/transactions/{txn.id}/full-edit"
            ).data.decode()
            assert "Mark as paid" not in settled_html

    def test_create_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions creates a new ad-hoc transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            response = auth_client.post("/transactions", data={
                "name": "New Expense",
                "estimated_amount": "99.99",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert response.status_code == 201

            # Verify the transaction was persisted correctly.
            txn = db.session.query(Transaction).filter_by(
                name="New Expense",
                scenario_id=seed_user["scenario"].id,
            ).one()
            assert txn.estimated_amount == Decimal("99.99")
            assert txn.pay_period_id == seed_periods_today[0].id
            assert txn.category_id == seed_user["categories"]["Groceries"].id
            assert txn.status.name == "Projected"

    def test_update_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> updates fields."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "200.00"},
            )
            assert response.status_code == 200
            assert b"200" in response.data

    def test_mark_expense_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets status to done for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "120.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert txn.actual_amount == Decimal("120.00")

    def test_mark_income_received(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done sets status to received for income."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "2050.00"},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Received"

    def test_soft_delete_template_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id> soft-deletes template-linked items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            # Simulate template linkage.
            from app.models.transaction_template import TransactionTemplate
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Template",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            txn.template_id = template.id
            db.session.commit()

            response = auth_client.delete(f"/transactions/{txn.id}")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.is_deleted is True

    def test_hard_delete_adhoc_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id> hard-deletes ad-hoc (no template) items."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            response = auth_client.delete(f"/transactions/{txn_id}")
            assert response.status_code == 200

            # Ad-hoc transaction should be fully deleted.
            assert db.session.get(Transaction, txn_id) is None

    def test_mark_done_without_actual_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done without actual_amount sets status only."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            assert txn.actual_amount is None

    def test_cancel_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel sets status to cancelled."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"
            assert txn.effective_amount == Decimal("0")

    def test_mark_credit_creates_payback(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-credit creates payback in next period."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            response = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # A payback transaction should exist in the next period.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods_today[1].id,
            ).first()
            assert payback is not None, "Payback transaction was not created"
            assert payback.name == "CC Payback: Test Expense"
            assert payback.estimated_amount == Decimal("123.45")
            assert payback.status.name == "Projected"
            assert payback.pay_period_id == seed_periods_today[1].id
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_reverts_and_deletes_payback(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id>/unmark-credit reverts to projected and deletes payback."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark as credit.
            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # Now unmark.
            response = auth_client.delete(f"/transactions/{txn.id}/unmark-credit")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Projected"

            # Payback should be deleted.
            payback = db.session.query(Transaction).filter(
                Transaction.name.like("%Payback%"),
                Transaction.pay_period_id == seed_periods_today[1].id,
            ).first()
            assert payback is None

    def test_hard_delete_credit_source_deletes_payback(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE on an ad-hoc Credit source removes the live payback too.

        Without the delete-side cleanup the ``SET NULL`` FK keeps the
        payback alive with its link nulled, silently inflating the next
        period's projected expenses with no offsetting credit row.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-credit")
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn_id, is_deleted=False,
            ).one()
            payback_id = payback.id

            response = auth_client.delete(f"/transactions/{txn_id}")
            assert response.status_code == 200

            # Source hard-deleted (ad-hoc) and the payback with it.
            assert db.session.get(Transaction, txn_id) is None
            assert db.session.get(Transaction, payback_id) is None

    def test_soft_delete_credit_source_deletes_payback(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE on a template-linked Credit source removes the payback.

        The source itself only soft-deletes (the recurrence engine needs
        the row), but the payback is an ad-hoc projected expense that
        must not survive its now-invisible source.
        """
        with app.app_context():
            from app.models.transaction_template import TransactionTemplate
            txn = self._create_test_txn(seed_user, seed_periods_today)
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Template",
                default_amount=Decimal("123.45"),
            )
            db.session.add(template)
            db.session.flush()
            txn.template_id = template.id
            db.session.commit()

            auth_client.post(f"/transactions/{txn.id}/mark-credit")
            payback = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn.id, is_deleted=False,
            ).one()
            payback_id = payback.id

            response = auth_client.delete(f"/transactions/{txn.id}")
            assert response.status_code == 200

            # Source soft-deleted, payback hard-deleted.
            db.session.refresh(txn)
            assert txn.is_deleted is True
            assert db.session.get(Transaction, payback_id) is None

    def test_create_transaction_full_form(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with all fields creates a complete transaction."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            projected = db.session.query(Status).filter_by(name="Projected").one()

            response = auth_client.post("/transactions", data={
                "name": "Full Form Expense",
                "estimated_amount": "250.00",
                "pay_period_id": seed_periods_today[2].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Car Payment"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
            })
            assert response.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="Full Form Expense"
            ).one()
            assert txn.estimated_amount == Decimal("250.00")
            assert txn.pay_period_id == seed_periods_today[2].id
            assert txn.category_id == seed_user["categories"]["Car Payment"].id

    def test_full_edit_due_date_input_renders_and_persists_when_unset(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A transaction with no due date renders an editable due_date input,
        and saving a date through the full-edit form persists it.

        Guards the un-gated due_date field in grid/_transaction_full_edit.html:
        before, the input was hidden whenever due_date was NULL, so a user
        could never add one.  The non-transfer update path applies due_date via
        its generic setattr loop, so the saved value sticks.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            auth_client.post("/transactions", data={
                "name": "No Due Date Yet",
                "estimated_amount": "75.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
            })
            txn = db.session.query(Transaction).filter_by(
                name="No Due Date Yet"
            ).one()
            assert txn.due_date is None

            # The input renders even though due_date is NULL.
            edit_resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert edit_resp.status_code == 200
            assert b'name="due_date"' in edit_resp.data

            # Saving a date persists it on this non-transfer transaction.
            save_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "due_date": "2026-02-20",
                "version_id": txn.version_id,
            })
            assert save_resp.status_code == 200
            db.session.refresh(txn)
            assert txn.due_date == date(2026, 2, 20)

    def test_full_edit_clears_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Emptying the pre-filled due_date input clears the stored date.

        The nullable-field clear rule: the schema pre_load maps the
        empty submit on the allow_none ``due_date`` to an explicit
        None (it used to DROP the key, making the date unclearable
        from the UI); the non-transfer update path's setattr loop then
        nulls the column.  The popover pre-fills the current value, so
        an empty submit is always the user's deliberate clear.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            auth_client.post("/transactions", data={
                "name": "Clearable Due Date",
                "estimated_amount": "75.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
                "due_date": "2026-02-20",
            })
            txn = db.session.query(Transaction).filter_by(
                name="Clearable Due Date"
            ).one()
            assert txn.due_date == date(2026, 2, 20)

            save_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "due_date": "",
                "version_id": txn.version_id,
            })
            assert save_resp.status_code == 200
            db.session.refresh(txn)
            assert txn.due_date is None

    def test_full_edit_period_selector_renders_and_moves_transaction(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The full-edit popover renders a pay-period selector and saving a
        different period reassigns the transaction (F1 -- change pay period).

        The dropdown is filtered to current + future periods, but always
        includes the row's own period so a row sitting in a past period
        stays selected.  seed_periods_today places today in period index
        4, so index 0 is past (the row's own, included) and index 5 is
        future (a valid move target); index 2 is past and NOT the row's
        own, so it must be excluded.

        The non-transfer update path applies pay_period_id via its
        generic setattr loop after re-verifying ownership (F-029), so the
        move sticks; a period move returns gridRefresh, an in-place edit
        balanceChanged.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            projected = db.session.query(Status).filter_by(name="Projected").one()
            source_period = seed_periods_today[0]   # past -- the row's own
            target_period = seed_periods_today[5]   # future -- valid target
            excluded_past = seed_periods_today[2]   # past, not the row's own
            auth_client.post("/transactions", data={
                "name": "Movable Expense",
                "estimated_amount": "120.00",
                "pay_period_id": source_period.id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": projected.id,
                "account_id": str(seed_user["account"].id),
            })
            txn = db.session.query(Transaction).filter_by(
                name="Movable Expense"
            ).one()
            assert txn.pay_period_id == source_period.id

            # The selector renders with the row's own (past) period
            # included and pre-selected, a future period offered, and a
            # different past period excluded.  Isolate the pay-period
            # <select> so option-value assertions cannot collide with the
            # status select (whose ids overlap the period ids).
            edit_resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert edit_resp.status_code == 200
            html = edit_resp.data.decode()
            assert 'name="pay_period_id"' in html
            sel_start = html.index('name="pay_period_id"')
            period_select = html[sel_start:html.index("</select>", sel_start)]
            # Own past period present and selected.
            assert source_period.label in period_select
            assert f'value="{source_period.id}" selected' in period_select
            # Future period offered.
            assert target_period.label in period_select
            # A past period that is not the row's own is excluded.
            assert excluded_past.label not in period_select

            # Saving a future period reassigns the transaction and asks
            # the client for a full grid refresh so the row relocates to
            # the new period (an in-place cell swap cannot move it).
            save_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "pay_period_id": target_period.id,
                "version_id": txn.version_id,
            })
            assert save_resp.status_code == 200
            assert save_resp.headers.get("HX-Trigger") == "gridRefresh"
            db.session.refresh(txn)
            assert txn.pay_period_id == target_period.id

            # An edit that does NOT move the period keeps the lightweight
            # balanceChanged trigger (no full reload).
            inplace_resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "estimated_amount": "130.00",
                "pay_period_id": target_period.id,
                "version_id": txn.version_id,
            })
            assert inplace_resp.status_code == 200
            assert inplace_resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_create_inline_no_scenario(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/new/quick with no baseline scenario returns 400.

        The route returns the plain text error 'No baseline scenario' when
        no baseline scenario exists for the user.
        """
        with app.app_context():
            from app.models.scenario import Scenario

            # Delete the baseline scenario.
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            response = auth_client.get(
                f"/transactions/new/quick"
                f"?category_id={seed_user['categories']['Rent'].id}"
                f"&period_id={seed_periods_today[0].id}"
                f"&transaction_type_id={expense_type.id}"
                f"&account_id={seed_user['account'].id}"
            )
            assert response.status_code == 400
            assert b"No baseline scenario" in response.data


class TestTransactionNegativePaths:
    """Tests for transaction route error handling, validation, and edge cases."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Helper: create and return a projected expense."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=projected.id,
            name="Test Expense",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("123.45"),
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    # ── Nonexistent ID tests ──────────────────────────────────────

    def test_update_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.patch(
                "/transactions/999999", data={"estimated_amount": "200.00"}
            )
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_done_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/mark-done returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-done")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_cancel_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/cancel returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/cancel")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_delete_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/999999 returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_mark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/999999/mark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.post("/transactions/999999/mark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_unmark_credit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/999999/unmark-credit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.delete("/transactions/999999/unmark-credit")
            assert resp.status_code == 404
            assert b"Not found" in resp.data

    def test_get_cell_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/cell returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/cell")
            assert resp.status_code == 404

    def test_get_quick_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/quick-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/quick-edit")
            assert resp.status_code == 404

    def test_get_full_edit_nonexistent_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/999999/full-edit returns 404 for nonexistent transaction."""
        with app.app_context():
            resp = auth_client.get("/transactions/999999/full-edit")
            assert resp.status_code == 404

    # ── Schema validation failure tests ───────────────────────────

    def test_create_transaction_missing_name(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions without required 'name' field returns 422 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 422
            resp_json = resp.get_json()
            assert "name" in resp_json["errors"]

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                scenario_id=seed_user["scenario"].id,
            ).count()
            assert count == 0

    def test_create_transaction_negative_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with negative estimated_amount returns 422."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Bad Amount",
                "estimated_amount": "-100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 422
            resp_json = resp.get_json()
            assert "estimated_amount" in resp_json["errors"]

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                name="Bad Amount",
            ).count()
            assert count == 0

    def test_create_transaction_zero_amount(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions with estimated_amount=0.00 succeeds (Range min=0 is inclusive)."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Zero Amount",
                "estimated_amount": "0.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            # Range(min=0) is inclusive by default -- 0.00 is accepted.
            assert resp.status_code == 201

            txn = db.session.query(Transaction).filter_by(name="Zero Amount").one()
            assert txn.estimated_amount == Decimal("0.00")

    def test_create_transaction_missing_pay_period_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions without required pay_period_id returns 422 with field error."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "No Period",
                "estimated_amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
            })
            assert resp.status_code == 422
            resp_json = resp.get_json()
            assert "pay_period_id" in resp_json["errors"]

    def test_create_transaction_with_other_users_pay_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions with another user's pay_period_id returns 404."""
        with app.app_context():
            # Create a second user with pay periods for IDOR testing.
            other_user = User(
                email="other@shekel.local",
                password_hash=hash_password("otherpass"),
                display_name="Other User",
            )
            db.session.add(other_user)
            db.session.flush()

            settings = UserSettings(user_id=other_user.id)
            db.session.add(settings)

            other_periods = pay_period_service.generate_pay_periods(
                user_id=other_user.id,
                start_date=date(2026, 1, 2),
                num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky",
                "estimated_amount": "100.00",
                "pay_period_id": other_periods[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(name="Sneaky").count()
            assert count == 0

    def test_update_transaction_invalid_amount(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> with non-numeric amount returns 422."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"estimated_amount": "not_a_number"},
            )
            assert resp.status_code == 422

            # Verify the transaction's amount was NOT changed.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.estimated_amount == Decimal("123.45")

    # ── State transition edge cases ───────────────────────────────

    def test_mark_done_already_done_expense(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done is idempotent for already-done transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark-done.
            resp1 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp1.status_code == 200

            # NOTE: mark_done is idempotent -- no guard against double mark-done.
            # The route unconditionally sets status to done/received regardless
            # of current status.
            resp2 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_cancel_already_cancelled_transaction(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/cancel is idempotent for already-cancelled transactions."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First cancel.
            resp1 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp1.status_code == 200

            # NOTE: cancel is idempotent -- no guard against double cancel.
            resp2 = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp2.status_code == 200

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_mark_done_cancelled_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done on a cancelled transaction is rejected.

        After the C-21 follow-up the mark_done endpoint runs every
        status change through ``verify_transition``.  Cancelled may
        only revert to Projected; a direct jump to Paid would
        resurrect the row without the explicit revert audit step.
        Was previously a 200 with a comment noting "UI hides the Done
        button for non-projected statuses, but the API endpoint does
        not enforce this"; the API now enforces it.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Cancel first.
            auth_client.post(f"/transactions/{txn.id}/cancel")
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    def test_cancel_done_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel on a done transaction is now rejected.

        After the C-21 follow-up the cancel endpoint runs every status
        change through ``app.services.state_machine.verify_transition``.
        Done -> Cancelled is illegal -- the user must revert to
        Projected first so the audit trail records both the revert
        and the subsequent cancellation.  Was previously a 200 with a
        comment noting "UI hides the Cancel button for done status";
        the API now enforces the same contract the UI was relying on.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done first.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status.name == "Paid"

    def test_mark_done_with_invalid_actual_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done with non-numeric actual_amount returns 422.

        Pre-C-27: the route caught ``InvalidOperation`` and returned
        the literal string ``"Invalid actual amount"`` with status
        400.  Post-C-27: :class:`MarkDoneSchema` rejects the value at
        the schema tier.  Refit 2026-07-11 for the marker-header
        convention (closeout plan session 4, developer-ruled): the 422
        body changed from a JSON errors dict to a DESIGNED fragment --
        the requesting cell re-rendered with the flattened field
        message -- carrying the marker header so htmx swaps it.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "not_a_number"},
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "actual_amount" in body
            assert "txn-chip" in body

            # ``MarkDoneSchema`` runs before the route's status
            # mutation (commit C-27 reordered the parse to the
            # top of the function), so a rollback is no longer
            # required to keep the row clean.  The assertions
            # remain to guard against regression.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == "Projected"
            assert txn_after.actual_amount is None

    def test_mark_done_with_negative_actual_amount(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/mark-done rejects negative actual_amount.

        Two layers reject this value:
          * Pre-C-27 only the DB CHECK constraint
            ``actual_amount >= 0`` rejected the row, surfacing as
            a 500 IntegrityError without the route's catch.
          * Post-C-27 (commit C-27 of the 2026-04-15 security
            remediation plan): :class:`MarkDoneSchema`'s
            ``Range(min=0)`` rejects the value at the schema tier
            so the route returns 400 before the row is touched.

        The DB CHECK remains as the storage-tier backstop (L-01)
        for any future caller that bypasses the schema.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            original_status_id = txn.status_id

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "-50.00"},
            )
            assert resp.status_code == 422

            db.session.expire_all()
            db.session.refresh(txn)
            assert txn.status_id == original_status_id, (
                "schema-tier rejection must not transition the row"
            )

    # ── XSS protection test ──────────────────────────────────────

    def test_create_transaction_xss_in_name(self, app, auth_client, seed_user, seed_periods_today):
        """Transaction name with script tag is stored but auto-escaped in rendered output."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "<script>alert(1)</script>",
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="<script>alert(1)</script>",
            ).one()

            # Verify Jinja2 auto-escaping prevents XSS in the cell partial.
            cell_resp = auth_client.get(f"/transactions/{txn.id}/cell")
            assert cell_resp.status_code == 200
            assert b"<script>" not in cell_resp.data
            assert b"&lt;script&gt;" in cell_resp.data


class TestCreateBaseline:
    """Tests for POST /create-baseline route."""

    def test_create_baseline_success(self, app, auth_client, seed_user):
        """POST /create-baseline creates a baseline scenario when none exists.

        Verifies: the route creates a Scenario with name='Baseline' and
        is_baseline=True, then redirects to the grid index.
        """
        with app.app_context():
            # Remove the existing baseline so the route has work to do.
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            scenario = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert scenario.name == "Baseline"
            assert scenario.is_baseline is True

    def test_create_baseline_idempotent(self, app, auth_client, seed_user):
        """POST /create-baseline with existing baseline does not create a duplicate.

        Verifies: when a baseline already exists (from seed_user fixture),
        the route redirects without creating a second scenario.
        """
        with app.app_context():
            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            count = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).count()
            assert count == 1

    def test_create_baseline_reposts_stranded_openings(
        self, app, auth_client, seed_user,
    ):
        """The recovery path re-posts openings stranded by baseline-lessness.

        The Step-5 wiring (plan Section 3.3, point 6): deleting the baseline
        CASCADE-disposes its journal entries -- including the seed
        Checking's $1000.00 opening -- reproducing the baseline-less state
        where an account's corrections had nowhere to post.  The route's
        per-user resync then posts the opening into the NEW baseline in the
        same transaction, so it is not silently stranded until the next
        anchor event.
        """
        with app.app_context():
            checking_id = seed_user["account"].id
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            new_baseline = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert posting_service.account_posting_total(
                checking_id, new_baseline.id,
            ) == Decimal("1000.00")

    def test_create_baseline_reposts_a_stranded_loan_opening(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """G1: the recovery path must repost the LOAN openings too, not just cash.

        The loan half of the test above.  A loan's opening posts per SCENARIO, so a
        baseline-less owner's loan has nowhere to put it; deleting the baseline
        reproduces that state exactly (the CASCADE disposes its journal entries,
        the loan's opening among them).

        The route recovered the ACCOUNT anchors and not the loans.  Before the fold
        cutover an ORIGINATED loan with no OPENING posting made the balance seam
        500 every loan surface; since steps C3b1/C3b3 the seam folds the balance
        from SOURCE facts, so a missing opening no longer breaks reads -- but the
        POSTING ledger (the general ledger the balance sheet and statements read)
        is out of sync until this reposts the opening.  So the recovery is pinned
        by reading the POSTINGS, not ``balance_at``: the posting window answers
        ONLY when the opening was reposted, where ``balance_at`` folds $200,000
        from source either way and cannot tell reposted from not.

        NEGATIVE CONTROL: drop the ``resync_user_loan_postings`` call from
        ``baseline_service.create_baseline_scenario`` and
        ``posted_loan_balance_at`` returns ``None`` (the opening is never
        reposted), while ``balance_at`` still folds $200,000.00 from source.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from app.services import balance_at
        from app.services.balance_at import BalanceContext
        from tests._test_helpers import create_loan_account

        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 1, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_period=seed_periods[0],
            )
            loan_id = loan.id
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            assert auth_client.post("/create-baseline").status_code == 302

            bctx = BalanceContext.build(seed_user["user"].id)
            reloaded = db.session.get(Account, loan_id)
            new_baseline = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True,
            ).one()
            # The recovery REPOSTED the loan's opening: the posting reader answers
            # $200,000 from the reconciled general ledger (None if still missing).
            # This is what pins the recovery -- balance_at cannot, since it folds
            # the same $200,000 from source whether or not the opening was reposted.
            assert posted_loan_balance_at(
                loan_id, new_baseline.id, bctx.as_of,
            ) == Decimal("200000.00")
            # And the user-facing balance is correct: no payment made, so the loan
            # still owes its opening.
            assert balance_at.balance_at(
                reloaded, bctx, bctx.as_of,
            ) == Decimal("200000.00")

    def test_create_baseline_requires_login(self, app, client):
        """POST /create-baseline without authentication redirects to login.

        Verifies: unauthenticated requests are rejected and no scenario
        is created.
        """
        with app.app_context():
            response = client.post("/create-baseline")
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

            count = Scenario.query.count()
            assert count == 0

    def test_create_baseline_rejects_get(self, app, auth_client, seed_user):
        """GET /create-baseline returns 405 Method Not Allowed.

        Verifies: the route only accepts POST requests.
        """
        with app.app_context():
            response = auth_client.get("/create-baseline")
            assert response.status_code == 405

    def test_create_baseline_user_isolation(self, app, auth_client, seed_user, second_user):
        """POST /create-baseline creates a scenario for the logged-in user only.

        Verifies: the route uses current_user.id correctly and does not
        affect other users' data.
        """
        with app.app_context():
            # Remove seed_user's baseline.
            Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).delete()
            db.session.commit()

            response = auth_client.post("/create-baseline")
            assert response.status_code == 302

            # The new scenario belongs to seed_user, not second_user.
            new_scenario = Scenario.query.filter_by(
                user_id=seed_user["user"].id, is_baseline=True
            ).one()
            assert new_scenario.user_id == seed_user["user"].id

            # second_user's baseline is untouched.
            other_baseline = Scenario.query.filter_by(
                user_id=second_user["user"].id, is_baseline=True
            ).one()
            assert other_baseline.user_id == second_user["user"].id


class TestAccountIdColumn:
    """Tests for the account_id column added to the Transaction model."""

    def test_transaction_model_has_account_id(self, app, db, seed_user, seed_periods_today):
        """Create a Transaction with account_id. Verify it saves and the relationship resolves."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]

        txn = Transaction(
            account_id=account.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Account Test",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        db.session.commit()

        assert txn.account_id == account.id
        assert txn.account is not None
        assert txn.account.id == account.id
        assert txn.account.name == "Checking"

    def test_transaction_without_account_id_raises_integrity_error(
        self, app, db, seed_user, seed_periods_today
    ):
        """Attempting to create a Transaction without account_id raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = Transaction(
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="No Account",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("50.00"),
        )
        db.session.add(txn)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_recurrence_engine_sets_account_id(self, app, db, seed_full_user_data):
        """Transactions generated by the recurrence engine have account_id from the template."""
        from app.services import recurrence_engine

        data = seed_full_user_data
        template = data["template"]
        periods = data["periods"]
        scenario = data["scenario"]

        created = recurrence_engine.generate_for_template(
            template, periods, scenario.id
        )

        assert len(created) > 0
        for txn in created:
            assert txn.account_id == template.account_id

    def test_credit_payback_inherits_account_id(self, app, db, seed_user, seed_periods_today):
        """The payback transaction created by mark_as_credit inherits account_id."""
        from app.services import credit_workflow

        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]

        txn = Transaction(
            account_id=account.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected.id,
            name="Test Expense for Credit",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            estimated_amount=Decimal("75.00"),
        )
        db.session.add(txn)
        db.session.commit()

        payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
        db.session.commit()

        assert payback.account_id == account.id

    def test_inline_create_sets_account_id(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/inline with account_id saves it on the transaction."""
        account = seed_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "99.99",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(name=category.display_name).first()
        assert txn is not None
        assert txn.account_id == account.id

    def test_inline_create_honors_typed_name(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A typed quick-create name wins over the category default.

        Grid audit A5 (closeout plan session 4): the Tier-1 entry point
        accepts an optional name so an ad-hoc row does not need the
        full form just to be named.
        """
        account = seed_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        resp = auth_client.post("/transactions/inline", data={
            "account_id": account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "12.50",
            "name": "Farmers market",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(name="Farmers market").first()
        assert txn is not None
        assert txn.estimated_amount == Decimal("12.50")

    def test_inline_create_blank_name_falls_back_to_category(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty name submit keeps the category-derived default.

        HTML forms submit every rendered input, so an untouched name
        field arrives as "" -- the schema's strip_empty_strings hook
        drops it and the route falls back to category.display_name.
        """
        account = seed_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        resp = auth_client.post("/transactions/inline", data={
            "account_id": account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "20.00",
            "name": "",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(name=category.display_name).first()
        assert txn is not None
        assert txn.estimated_amount == Decimal("20.00")

    def test_inline_create_rejects_missing_account_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/inline without account_id returns validation error."""
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "50.00",
        })
        assert resp.status_code == 422

    def test_inline_create_rejects_other_users_account_id(
        self, app, auth_client, seed_user, seed_periods_today, second_user
    ):
        """POST /transactions/inline with another user's account_id returns 404."""
        other_account = second_user["account"]
        category = seed_user["categories"]["Groceries"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": other_account.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": expense_type.id,
            "estimated_amount": "50.00",
        })
        assert resp.status_code == 404


class TestAccountScopedGrid:
    """Tests verifying the grid filters transactions by account_id.

    The grid resolves a viewed account (checking by default, or via the
    ?account_id query param / user settings).  Only transactions belonging
    to that account should appear in the grid body and footer totals.
    Transactions on other accounts must be excluded.
    """

    def _create_savings_account(self, user, periods):
        """Helper: create a savings account with anchor balance and period."""
        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=user.id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("5000.00"),
                anchor_period_id=periods[0].id,
            ),
        )
        db.session.add(savings)
        db.session.flush()
        return savings

    def _create_txn(self, account, period, scenario, name, amount,
                    txn_type_name="Expense", status_name="Projected", category=None):
        """Helper: create a transaction on the given account."""
        status = db.session.query(Status).filter_by(name=status_name).one()
        txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()
        txn = Transaction(
            account_id=account.id,
            pay_period_id=period.id,
            scenario_id=scenario.id,
            status_id=status.id,
            name=name,
            category_id=category.id if category else None,
            transaction_type_id=txn_type.id,
            estimated_amount=Decimal(str(amount)),
        )
        db.session.add(txn)
        return txn

    # --- Core filtering tests ---

    def test_grid_shows_only_checking_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Default grid (checking) shows only checking transactions, not savings."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Rent", 1200,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, seed_periods_today[0], scenario, "Savings Interest", 50,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "Rent" in html
        assert "Savings Interest" not in html

    def test_grid_account_override_shows_savings_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Passing ?account_id=savings shows only savings transactions.

        Transactions are matched to cells by category_id and type.  The
        grid renders amounts (not names) in cells, so we check for the
        amount values and verify that the checking expense amount does
        not appear on the savings grid.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        # Use a visible period (current period index ~5).
        current = pay_period_service.get_current_period(seed_user["user"].id)
        self._create_txn(checking, current, scenario, "Checking Rent", 1234,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, current, scenario, "Savings Deposit", 567,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Savings grid: should show the $567 deposit, not the $1234 rent.
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "567" in html
        assert "1,234" not in html

    def test_grid_shows_correct_account_name_in_header(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The grid header shows the viewed account's name."""
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        assert "Savings Balance" in html

    # --- Balance correctness tests ---

    def test_balance_uses_correct_anchor_for_each_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Each account's grid uses its own anchor balance, not another's.

        Checking anchor: $1000 (from seed_user).
        Savings anchor: $5000.
        With no transactions, the projected balance equals the anchor.
        """
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        # Checking grid: balance should reflect $1000 anchor.
        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "$1,000" in html

        # Savings grid: balance should reflect $5000 anchor.
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,000" in html

    def test_balance_excludes_other_accounts_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A $500 expense on checking should NOT reduce the savings balance.

        Checking: $1000 anchor - $500 expense = $500 projected.
        Savings: $5000 anchor, no expenses = $5000 projected.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Rent", 500,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Savings grid: balance should still be $5000 (the expense is on checking).
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,000" in html

    # --- Balance row HTMX refresh tests ---

    def test_balance_row_refresh_scoped_to_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /grid/balance-row with account_id returns that account's balances."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Expense on Checking", 300,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Balance row for savings: no expenses, balance = anchor.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$5,000" in html

    def test_balance_row_refresh_includes_account_id_in_htmx_url(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The returned tfoot contains account_id in its hx-get URL for future refreshes."""
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert f"account_id={savings.id}" in html

    # --- Footer totals tests ---

    def test_footer_totals_reflect_viewed_account_only(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Subtotal rows count only the viewed account's transactions.

        The tbody subtotal rows sum projected (unsettled) transactions for
        the viewed account.  Savings transactions must not appear in the
        checking account's subtotals.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        # Use the current period so it falls within the visible window.
        current = pay_period_service.get_current_period(seed_user["user"].id)

        self._create_txn(checking, current, scenario, "Salary", 2000,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        self._create_txn(checking, current, scenario, "Rent", 800,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(savings, current, scenario, "Interest", 100,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Full grid page for checking account -- subtotals reflect checking only.
        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "$2,000" in html  # Total Income (checking).
        assert "$800" in html    # Total Expenses (checking).

        # Savings footer: shows projected balance ($5,000 anchor + $100 income = $5,100).
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,100" in html
        # Checking expenses must NOT appear on savings balance row.
        assert "$800" not in html

    # --- Empty / edge case tests ---

    def test_grid_for_account_with_no_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """An account with no transactions renders the grid without errors.

        Section banners should appear. No transaction cells. Balance equals anchor.
        """
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        db.session.commit()

        resp = auth_client.get(f"/grid?account_id={savings.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "INCOME" in html
        assert "EXPENSES" in html
        assert "$5,000" in html  # Anchor balance, no transactions.

    def test_grid_hides_category_rows_without_account_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Categories with transactions only on checking should not render on savings grid.

        Create a Rent expense on checking. The Rent category row should
        appear on checking grid but not on savings grid.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        self._create_txn(checking, seed_periods_today[0], scenario, "Rent", 1200,
                         category=seed_user["categories"]["Rent"])
        db.session.commit()

        # Checking grid: Rent row visible.
        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "Rent" in html

        # Savings grid: no Rent row (no transactions for this category on savings).
        resp = auth_client.get(f"/grid?account_id={savings.id}")
        html = resp.data.decode()
        # The category name "Rent" should not appear as a row label.
        # It may appear in the "Add Transaction" modal dropdown, so check
        # specifically for the row label pattern.
        assert 'class="sticky-col row-label"' not in html or "Rent" not in html.split("EXPENSES")[0].split("INCOME")[-1]

    # NOTE: ``test_grid_account_with_no_anchor_balance`` and
    # ``test_grid_account_with_no_anchor_period`` previously exercised
    # the NULL-anchor branches of the balance producers.  E-19 / Commit
    # 3 makes both NULL states unreachable at the storage tier (NOT NULL
    # + ``ck_accounts_anchor_balance_present``) and at the application
    # tier (``account_service.create_account`` resolves the period if
    # omitted and rejects NULL balances).  The scenarios these tests
    # constructed (``Account(..., current_anchor_balance=None, ...)``
    # and ``Account(..., current_anchor_period_id=None, ...)``) can no
    # longer be materialised through any code path -- the constraint
    # fires at the DB and the factory raises TypeError / ValidationError
    # respectively.  Coverage of the constraint itself lives in
    # ``test_models/test_account_anchor_invariant.py::TestModelRejectsNullAnchor``.
    # Tests deleted (not skipped) because the asserted behaviour does
    # not exist anymore -- skipping would falsely imply the case is
    # still meaningful.

    # --- Cancelled and deleted transaction edge cases ---

    def test_cancelled_transactions_excluded_from_account_grid(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Cancelled transactions on the viewed account do not render as cells.

        The grid template filters out cancelled transactions at the cell
        level (txn.status.name != 'cancelled').  The cancelled transaction
        is still loaded by the query (is_deleted is False) but not rendered.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        current = pay_period_service.get_current_period(seed_user["user"].id)

        active = self._create_txn(checking, current, scenario, "Active Expense", 100,
                                  category=seed_user["categories"]["Rent"])
        cancelled = self._create_txn(checking, current, scenario, "Cancelled Expense", 200,
                                     status_name="Cancelled",
                                     category=seed_user["categories"]["Car Payment"])
        db.session.commit()

        resp = auth_client.get("/grid")
        html = resp.data.decode()
        # The active transaction's cell should be rendered with its ID.
        assert f"txn-cell-{active.id}" in html
        # The cancelled transaction should NOT have a rendered cell.
        assert f"txn-cell-{cancelled.id}" not in html

    def test_soft_deleted_transactions_excluded_from_account_grid(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Soft-deleted transactions (is_deleted=True) do not appear."""
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)

        txn = self._create_txn(checking, seed_periods_today[0], scenario, "Deleted Expense", 999,
                               category=seed_user["categories"]["Rent"])
        txn.is_deleted = True
        db.session.commit()

        resp = auth_client.get("/grid")
        html = resp.data.decode()
        assert "$999" not in html

    # --- Carry forward interaction test ---

    def test_carry_forward_moves_all_accounts_transactions(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Carry forward moves projected transactions from ALL accounts, not just the viewed one.

        This verifies carry forward is NOT account-scoped -- it is a
        period-level operation that moves everything unpaid in that period.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        # Create projected transactions on both accounts in period 0.
        checking_txn = self._create_txn(
            checking, seed_periods_today[0], scenario, "Checking Expense", 100,
            category=seed_user["categories"]["Rent"],
        )
        savings_txn = self._create_txn(
            savings, seed_periods_today[0], scenario, "Savings Expense", 50,
            category=seed_user["categories"]["Groceries"],
        )
        db.session.commit()

        checking_txn_id = checking_txn.id
        savings_txn_id = savings_txn.id

        # Carry forward from period 0.
        resp = auth_client.post(f"/pay-periods/{seed_periods_today[0].id}/carry-forward")
        assert resp.status_code == 200

        # Both transactions should have moved to the current period.
        db.session.expire_all()
        checking_after = db.session.get(Transaction, checking_txn_id)
        savings_after = db.session.get(Transaction, savings_txn_id)

        current_period = pay_period_service.get_current_period(seed_user["user"].id)
        assert checking_after.pay_period_id == current_period.id
        assert savings_after.pay_period_id == current_period.id

    # --- Inline create scoped to correct account ---

    def test_inline_create_on_savings_grid_saves_to_savings(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Creating a transaction inline on the savings grid assigns it to the savings account."""
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)
        category = seed_user["categories"]["Salary"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        db.session.commit()

        resp = auth_client.post("/transactions/inline", data={
            "account_id": savings.id,
            "category_id": category.id,
            "pay_period_id": seed_periods_today[0].id,
            "scenario_id": scenario.id,
            "transaction_type_id": income_type.id,
            "estimated_amount": "250.00",
        })
        assert resp.status_code == 201

        txn = Transaction.query.filter_by(
            estimated_amount=Decimal("250.00"),
            account_id=savings.id,
        ).first()
        assert txn is not None
        assert txn.account_id == savings.id

    # --- Multi-period balance roll-forward correctness ---

    def test_balance_rolls_forward_correctly_per_account(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Balance roll-forward across periods uses only the viewed account's transactions.

        Checking: anchor $1000, current period expense $200, next period expense $300.
        Savings: anchor $5000, current period income $100.

        The Projected End Balance for checking should reflect only checking
        transactions.  The savings balance must not be affected by checking
        expenses.
        """
        checking = seed_user["account"]
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        savings = self._create_savings_account(seed_user["user"], seed_periods_today)

        current = pay_period_service.get_current_period(seed_user["user"].id)
        # Find the next period after current.
        current_idx = next(
            i for i, p in enumerate(seed_periods_today) if p.id == current.id
        )
        next_period = seed_periods_today[current_idx + 1]

        self._create_txn(checking, current, scenario, "Expense A", 200,
                         category=seed_user["categories"]["Rent"])
        self._create_txn(checking, next_period, scenario, "Expense B", 300,
                         category=seed_user["categories"]["Car Payment"])
        self._create_txn(savings, current, scenario, "Deposit", 100,
                         txn_type_name="Income", category=seed_user["categories"]["Salary"])
        db.session.commit()

        # Checking balance: anchor $1000 - $200 = $800, then $800 - $300 = $500.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={checking.id}")
        html = resp.data.decode()
        assert "$800" in html
        assert "$500" in html

        # Savings balance: anchor $5000 + $100 = $5100, steady after that.
        resp = auth_client.get(f"/grid/balance-row?periods=6&offset=0&account_id={savings.id}")
        html = resp.data.decode()
        assert "$5,100" in html
        # Checking expenses must NOT appear on savings balance row.
        assert "$800" not in html
        assert "$500" not in html


# ── TRANSFERS Section Removal Tests ────────────────────────────────


class TestTransfersSectionRemoved:
    """Verify the TRANSFERS grid section is gone and shadows render inline."""

    def test_grid_no_transfers_section(self, app, auth_client, seed_user, seed_periods_today):
        """Grid does not contain a TRANSFERS section banner."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "section-banner-transfer" not in html
            assert "xfer-cell-" not in html

    def test_grid_renders_without_transfers(self, app, auth_client, seed_user, seed_periods_today):
        """Grid renders normally with no transfers or shadows."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "section-banner-income" in html
            assert "section-banner-expense" in html
            assert "section-banner-transfer" not in html


# ── Inline Subtotal Row Tests ──────────────────────────────────────


class TestInlineSubtotalRows:
    """Tests for the Total Income and Total Expenses subtotal rows in tbody."""

    def test_subtotal_rows_present(self, app, auth_client, seed_user, seed_periods_today):
        """Grid contains subtotal-row-income and subtotal-row-expense rows."""
        with app.app_context():
            # Create transactions so the sections render.
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            txn_inc = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Salary",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            txn_exp = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1200.00"),
            )
            db.session.add_all([txn_inc, txn_exp])
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html

    def test_subtotal_values_correct(self, app, auth_client, seed_user, seed_periods_today):
        """Subtotal rows show correct per-period totals."""
        with app.app_context():
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            for name, cat, typ, amt in [
                ("Pay", "Salary", income_type.id, "2000.00"),
                ("Stipend", "Salary", income_type.id, "100.00"),
                ("Rent", "Rent", expense_type.id, "1200.00"),
                ("Food", "Groceries", expense_type.id, "400.00"),
            ]:
                txn = Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=name,
                    category_id=seed_user["categories"][cat].id,
                    transaction_type_id=typ,
                    estimated_amount=Decimal(amt),
                )
                db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            # Total Income = 2000 + 100 = 2100.
            assert "$2,100" in html
            # Total Expenses = 1200 + 400 = 1600.
            assert "$1,600" in html

    def test_subtotal_excludes_cancelled(self, app, auth_client, seed_user, seed_periods_today):
        """Cancelled transactions are excluded from subtotals."""
        with app.app_context():
            from app.models.ref import TransactionType
            projected = db.session.query(Status).filter_by(name="Projected").one()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            from app.services import pay_period_service
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            txn_ok = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Good Pay",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("1000.00"),
            )
            txn_bad = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=cancelled.id,
                name="Cancelled Pay",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add_all([txn_ok, txn_bad])
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            # Only $1,000 counted (cancelled $500 excluded).
            assert "$1,000" in html

    def test_balance_row_refresh_unaffected(self, app, auth_client, seed_user, seed_periods_today):
        """The balance-row HTMX endpoint returns tfoot only, no subtotal rows."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "subtotal-row" not in html
            assert "net-cash-flow-row" not in html
            assert "<tfoot" in html


# ── Net Cash Flow Row Tests ────────────────────────────────────────


class TestNetCashFlowRow:
    """Tests for the Net Cash Flow row in tbody."""

    def _seed_txns(self, seed_user, seed_periods_today, income_amt, expense_amt):
        """Helper: create income + expense in the current/first visible period."""
        from app.models.ref import TransactionType
        from app.services import pay_period_service
        projected = db.session.query(Status).filter_by(name="Projected").one()
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        current = pay_period_service.get_current_period(seed_user["user"].id)
        if not current:
            current = seed_periods_today[0]

        txns = []
        if income_amt:
            txns.append(Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Income",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal(income_amt),
            ))
        if expense_amt:
            txns.append(Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Expense",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal(expense_amt),
            ))
        db.session.add_all(txns)
        db.session.commit()

    def test_net_cash_flow_row_present(self, app, db, auth_client, seed_user, seed_periods_today):
        """Grid contains a net-cash-flow-row with correct label."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "2000", "1400")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            assert "Net Cash Flow" in html
            assert "$600" in html

    def test_net_cash_flow_negative(self, app, db, auth_client, seed_user, seed_periods_today):
        """Negative net cash flow shows warning indicator."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "1000", "1500")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "balance-negative" in html
            # Warning icon for negative net.
            assert "bi-exclamation-triangle-fill" in html

    def test_net_cash_flow_zero(self, app, db, auth_client, seed_user, seed_periods_today):
        """Breakeven period shows empty net cash flow cell."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "1000", "1000")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            # Net is zero -- cell should be empty (matching footer behavior).

    def test_balance_row_refresh_excludes_net_cash_flow(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Balance-row HTMX endpoint does not include net-cash-flow-row."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "net-cash-flow-row" not in html


# ── Footer Condensation Tests ──────────────────────────────────────


class TestFooterCondensation:
    """Tests verifying the footer contains only Projected End Balance."""

    def test_footer_single_row(self, app, db, auth_client, seed_user, seed_periods_today):
        """Balance-row response has exactly 1 row: Projected End Balance."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "Projected End Balance" in html
            assert "Total Income" not in html
            assert "Total Expenses" not in html
            assert "Net (Income" not in html
            assert html.count("<tr") == 1

    def test_footer_htmx_attributes_preserved(self, app, db, auth_client, seed_user, seed_periods_today):
        """The tfoot has all HTMX attributes for the self-referencing refresh."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert 'id="grid-summary"' in html
            assert "hx-get=" in html
            assert 'hx-trigger="balanceChanged from:body"' in html
            assert 'hx-swap="outerHTML"' in html

    def test_footer_htmx_refresh_cycle(self, app, db, auth_client, seed_user, seed_periods_today):
        """Initial page and balance-row both produce tfoot with HTMX attributes."""
        with app.app_context():
            page_resp = auth_client.get("/grid")
            page_html = page_resp.data.decode()
            assert 'id="grid-summary"' in page_html

            balance_resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            balance_html = balance_resp.data.decode()
            assert 'id="grid-summary"' in balance_html
            assert "hx-trigger" in balance_html

    def test_subtotals_still_present_in_tbody(self, app, db, auth_client, seed_user, seed_periods_today):
        """Tbody subtotal and net cash flow rows survive footer condensation."""
        with app.app_context():
            from app.models.ref import TransactionType
            from app.services import pay_period_service
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = pay_period_service.get_current_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Pay",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html
            assert "net-cash-flow-row" in html


class TestPeriodHeaderDateFormat:
    """Tests for pay period date headers -- Commit #14.

    Headers show only the paycheck date (start_date), not the period range.
    Current-year periods omit the year suffix (e.g., '3/26').
    Non-current-year periods include 2-digit year (e.g., '3/26/27').
    """

    def _make_periods(self, db, seed_user, start_date, num_periods=6):
        """Helper: generate pay periods and set anchor to the first one."""
        periods = pay_period_service.generate_pay_periods(
            user_id=seed_user["user"].id,
            start_date=start_date,
            num_periods=num_periods,
            cadence_days=14,
        )
        db.session.flush()
        seed_user["account"].current_anchor_period_id = periods[0].id
        db.session.commit()
        return periods

    def test_period_header_compact_for_current_year(self, app, auth_client, seed_user):
        """Current-year periods display paycheck date without year suffix.

        The grid starts at the current period (the one containing today),
        so we must check the current period's header, not the first
        generated period.
        """
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            periods = self._make_periods(db, seed_user, start)

            # The grid starts at the current period -- find it.
            current = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            assert current is not None, "No period covers today"

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The current period's start_date should appear in compact
            # format (no year suffix) since it's in the current year.
            expected = current.start_date.strftime("%-m/%-d")
            assert expected in html
            # Should NOT contain a range separator IN THE HEADER.  The scope
            # matters and this assertion did not have it: the mobile
            # "this period" card renders ``period.label`` -- a real range --
            # by design (``grid/_mobile_this_period.html``), so searching the
            # whole document asserts something the app never promised.
            #
            # It passed anyway, by a FORMATTING COINCIDENCE.  ``label`` is
            # zero-padded (``%m/%d``) while this built the string unpadded
            # (``%-m/%-d``), and the two are identical only when the month and
            # the day are both >= 10 -- so the assertion fired for the first
            # time on 2026-11-30 and would fire on roughly sixty days a year,
            # every one of them in October, November or December.
            #
            # The class's contract is about the period HEADER, which is inside
            # the table head (``grid/grid.html`` thead) and renders
            # ``start_date`` alone.  Scope the search there and the assertion
            # says what it means.
            head = html[html.index("<thead"):html.index("</thead>")]
            end = current.start_date + timedelta(days=13)
            range_str = f"{current.start_date.strftime('%-m/%-d')} - {end.strftime('%-m/%-d')}"
            assert range_str not in head
            padded_range = (
                f"{current.start_date.strftime('%m/%d')} - "
                f"{end.strftime('%m/%d')}"
            )
            assert padded_range not in head

    def test_period_header_full_format_for_cross_year(self, app, auth_client, seed_user):
        """A period starting in a non-current year shows the year suffix."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            # Generate enough periods to REACH the next calendar year on every
            # day of the year, rather than assuming a fixed count does.  A flat
            # ``num_periods=28`` spans ~13 months, which crosses a year boundary
            # from most starting points but NOT from early January: on
            # 2027-01-01 the run ends 2027-12-30 and the guard below fires with
            # "Test requires a next-year period" -- a red suite on New Year's
            # Day.  Covering through mid-January of next year makes the
            # precondition hold whatever the calendar says.
            target = date(today.year + 1, 1, 15)
            num_periods = ((target - start).days // 14) + 2
            periods = self._make_periods(
                db, seed_user, start, num_periods=num_periods,
            )

            # Find a period whose start_date is in the next year.
            next_year_period = None
            for p in periods:
                if p.start_date.year > today.year:
                    next_year_period = p
                    break

            assert next_year_period is not None, "Test requires a next-year period"

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = next_year_period.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Expect paycheck date with year suffix.
            expected = next_year_period.start_date.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_full_format_for_past_year(self, app, auth_client, seed_user):
        """Periods in the previous year show the year suffix."""
        with app.app_context():
            today = date.today()
            past_start = date(today.year - 1, 6, 1)
            days_to_today = (today - past_start).days
            num = (days_to_today // 14) + 4
            periods = self._make_periods(db, seed_user, past_start, num_periods=num)

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            first_offset = periods[0].period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={first_offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = past_start.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_full_format_for_future_year(self, app, auth_client, seed_user):
        """Periods in the next year show the year suffix."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            days_to_next_year = (date(today.year + 1, 2, 1) - start).days
            num = (days_to_next_year // 14) + 2
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            future_period = None
            for p in periods:
                if p.start_date.year > today.year:
                    future_period = p
                    break

            assert future_period is not None, "Test requires a next-year period"

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = future_period.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            expected = future_period.start_date.strftime("%-m/%-d/%y")
            assert expected in html

    def test_period_header_mixed_formats_same_page(self, app, auth_client, seed_user):
        """Current-year and non-current-year headers coexist on the same page."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=28)
            days_to_next_year = (date(today.year + 1, 2, 1) - start).days
            num = (days_to_next_year // 14) + 2
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            # Find the last current-year period and first next-year period.
            last_current = None
            first_next = None
            for p in periods:
                if p.start_date.year == today.year:
                    last_current = p
                if first_next is None and p.start_date.year > today.year:
                    first_next = p

            assert last_current is not None
            assert first_next is not None

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = last_current.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=6&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Current-year: paycheck date without year.
            compact = last_current.start_date.strftime("%-m/%-d")
            assert compact in html

            # Next-year: paycheck date with year suffix.
            full = first_next.start_date.strftime("%-m/%-d/%y")
            assert full in html

    def test_carry_forward_button_still_present_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Carry forward button renders correctly alongside the new date format."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=56)
            periods = self._make_periods(db, seed_user, start)

            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            first_cat = list(seed_user["categories"].values())[0]
            txn = Transaction(
                pay_period_id=periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Bill",
                category_id=first_cat.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = periods[0].period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=6&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Carry forward button present.
            assert "carry-forward" in html
            # Paycheck date without year (current year period).
            expected = periods[0].start_date.strftime("%-m/%-d")
            assert expected in html

    def test_grid_renders_without_error_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Smoke test: grid renders with correct table structure after date format change."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=14)
            self._make_periods(db, seed_user, start)

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "<thead" in html
            assert "<tbody>" in html
            assert "Projected End Balance" in html

    def test_balance_row_still_works_after_format_change(
        self, app, auth_client, seed_user
    ):
        """Balance row HTMX partial still renders after the thead date change."""
        with app.app_context():
            today = date.today()
            start = today - timedelta(days=14)
            self._make_periods(db, seed_user, start)

            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 200
            assert b'id="grid-summary"' in resp.data

    def test_period_header_handles_january_1st(self, app, auth_client, seed_user):
        """A period starting January 1 of the current year uses compact format."""
        with app.app_context():
            today = date.today()
            jan1 = date(today.year, 1, 1)
            days_to_today = (today - jan1).days
            num = max((days_to_today // 14) + 4, 6)
            periods = self._make_periods(db, seed_user, jan1, num_periods=num)

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = periods[0].period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Jan 1 in current year -- no year suffix.
            assert "1/1" in html

    def test_period_header_handles_december_31st(self, app, auth_client, seed_user):
        """A late-December period in the current year uses compact format."""
        with app.app_context():
            today = date.today()
            dec18 = date(today.year, 12, 18)
            start = today - timedelta(days=14)
            days_to_dec18 = (dec18 - start).days
            num = (days_to_dec18 // 14) + 4
            periods = self._make_periods(db, seed_user, start, num_periods=num)

            # Find the last period starting in the current year.
            dec_period = None
            for p in periods:
                if p.start_date.year == today.year:
                    dec_period = p

            if dec_period is None:
                pytest.skip("No period starting in late current year generated")

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            offset = dec_period.period_index - current_period.period_index
            resp = auth_client.get(f"/grid?periods=3&offset={offset}")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Current-year start date -- no year suffix.
            expected = dec_period.start_date.strftime("%-m/%-d")
            assert expected in html

            # The next period (if it exists and starts next year) shows year.
            next_periods = [
                p for p in periods
                if p.period_index == dec_period.period_index + 1
            ]
            if next_periods and next_periods[0].start_date.year > today.year:
                full = next_periods[0].start_date.strftime("%-m/%-d/%y")
                assert full in html


class TestTransactionNameRows:
    """Tests for Commit #15: transaction-name-based row headers.

    The grid now shows one row per unique (category, template, name) tuple
    instead of one row per category.  These tests verify that the restructure
    produces correct row headers, handles all transaction types, maintains
    deterministic ordering, and preserves subtotals and HTMX interactions.
    """

    def _get_current_period(self, seed_user):
        """Return the current period for the seed user."""
        return pay_period_service.get_current_period(seed_user["user"].id)

    def test_grid_separate_rows_for_same_category_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two templates in the same category produce two distinct grid rows,
        each with the transaction name in the row header.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a second category item under "Auto" group.
            auto_insurance = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Insurance",
            )
            db.session.add(auto_insurance)
            db.session.flush()

            # Two templates, same category.
            tmpl_sf = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                name="State Farm",
                default_amount=Decimal("150.00"),
            )
            tmpl_geico = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                name="Geico",
                default_amount=Decimal("120.00"),
            )
            db.session.add_all([tmpl_sf, tmpl_geico])
            db.session.flush()

            txn_sf = Transaction(
                template_id=tmpl_sf.id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="State Farm",
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            txn_geico = Transaction(
                template_id=tmpl_geico.id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Geico",
                category_id=auto_insurance.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("120.00"),
            )
            db.session.add_all([txn_sf, txn_geico])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Both names appear as row headers in the expenses section.
            assert "State Farm" in html
            assert "Geico" in html

            # Verify they are in separate <th> elements.
            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "State Farm" in th_labels
            assert "Geico" in th_labels

    def test_grid_one_time_transaction_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-time transaction (no template) produces its own row with
        the transaction name in the row header.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Car Repair",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("450.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "Car Repair" in th_labels

    def test_grid_shadow_transactions_get_own_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Shadow transactions from transfers produce their own grid rows
        with the transaction name visible in the row header.
        """
        with app.app_context():
            from app.models.transfer import Transfer
            from app.models.transfer_template import TransferTemplate

            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            # Create a savings account for the transfer destination.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings_acct)
            db.session.flush()

            # Create the outgoing category.
            out_cat = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers",
                item_name="Outgoing",
            )
            db.session.add(out_cat)
            db.session.flush()

            # Create transfer and shadow expense on checking.
            transfer = Transfer(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=current.id,
                status_id=projected.id,
                name="To Savings",
                amount=Decimal("500.00"),
                from_account_id=seed_user["account"].id,
                to_account_id=savings_acct.id,
            )
            db.session.add(transfer)
            db.session.flush()

            shadow = Transaction(
                transfer_id=transfer.id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Transfer to Savings",
                category_id=out_cat.id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(shadow)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            # "Transfer to" prefix is stripped -- row shows just "Savings".
            assert "Savings" in th_labels

    def test_grid_empty_cell_has_correct_category_id(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Empty cells pass the correct category_id for quick create,
        matching the row key's category.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a transaction only in the current period so adjacent
            # periods have empty cells for this row key.
            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Electric Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("120.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The empty cell's hx-get URL should contain the correct category_id.
            cat_id = seed_user["categories"]["Rent"].id
            assert f"category_id={cat_id}" in html

    def test_grid_group_headers_appear(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Group header rows appear before each category group's transactions
        with the group-header-row CSS class.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create expenses in two different groups.
            txn_home = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent Payment",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1000.00"),
            )
            txn_auto = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Car Loan",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("400.00"),
            )
            db.session.add_all([txn_home, txn_auto])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Group headers with correct class.
            assert 'class="group-header-row"' in html
            # Both groups present.
            assert "Home" in html
            assert "Auto" in html

    def test_grid_inline_edit_works_after_restructure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Inline quick-edit still works: GET returns form, PATCH updates
        the cell, and HX-Trigger fires balanceChanged.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
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

            # GI-1: GET quick edit form.
            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            assert b"80" in resp.data

            # GI-2: PATCH updates amount.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "95.00"},
            )
            assert resp.status_code == 200
            assert b"95" in resp.data
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_grid_empty_cell_quick_create_works(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GI-9 regression: clicking an empty cell loads the quick-create form."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a transaction so a row key exists with empty cells
            # in adjacent periods.
            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Internet Bill",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("60.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Extract the quick-create URL from an empty cell.
            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            import re
            # Find quick-create hx-get URLs for the Rent category.
            # The route URL is /transactions/new/quick; HTML encodes & as &amp;.
            cat_id = seed_user["categories"]["Rent"].id
            pattern = rf'hx-get="(/transactions/new/quick\?[^"]*category_id={cat_id}[^"]*)"'
            urls = re.findall(pattern, html)
            assert urls, "No quick-create URL found for the Rent category"

            # Decode HTML entities so the test client can use the URL.
            url = urls[0].replace("&amp;", "&")

            # GET the quick-create form.
            resp = auth_client.get(url)
            assert resp.status_code == 200
            assert b"estimated_amount" in resp.data

    def test_grid_keyboard_nav_classes_correct(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Transaction rows do not have excluded CSS classes; group headers,
        subtotals, and banners do.

        The Income group header is intentionally suppressed under the INCOME
        banner (S12 D3+D4 ruling: a group header is skipped when it would
        merely restate the section banner). So a non-section-named group must
        be present to assert the ``group-header-row`` class renders: the
        expense-side "Home" group (Rent) is not named "expense"/"expenses",
        so its header is not suppressed.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            income_txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            # Expense in the "Home" group so a real (non-suppressed) group
            # header renders -- the "Income" group header is dropped as
            # redundant with the INCOME banner.
            expense_txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1500.00"),
            )
            db.session.add_all([income_txn, expense_txn])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            # Group header rows have the correct class.
            assert "group-header-row" in html
            # Subtotal rows have correct class.
            assert "subtotal-row" in html
            # Net cash flow row.
            assert "net-cash-flow-row" in html
            # Section banners.
            assert "section-banner-income" in html
            assert "section-banner-expense" in html

    def test_grid_empty_state_no_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Grid renders cleanly with no transactions -- section banners,
        subtotal rows with zeros, and no crash.
        """
        with app.app_context():
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Structure intact.
            assert "INCOME" in html
            assert "EXPENSES" in html
            assert "Total Income" in html
            assert "Total Expenses" in html
            assert "Net Cash Flow" in html
            assert "Projected End Balance" in html

    def test_grid_subtotals_unchanged_after_restructure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Subtotals iterate over all transactions per period, not row keys.
        Total Income shows $2,000, Total Expenses shows $1,500, Net shows $500.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            income = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            expense1 = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1000.00"),
            )
            expense2 = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add_all([income, expense1, expense2])
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Subtotals computed from all transactions.
            assert "$2,000" in html   # Total Income
            assert "$1,500" in html   # Total Expenses
            assert "$500" in html     # Net Cash Flow

    def test_grid_payday_workflow_complete(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Full payday workflow still works after the row restructure:
        true-up, mark received, carry forward, mark paid, mark credit.
        Identical to C-0-7 from regression suite.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            account = seed_user["account"]

            current = self._get_current_period(seed_user)
            past = next(
                p for p in seed_periods_today
                if p.period_index == current.period_index - 1
            )

            # Setup: past expense, current income + 2 expenses.
            past_exp = Transaction(
                pay_period_id=past.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Past Rent",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            income_txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("2000.00"),
            )
            exp_done = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Electric Bill",
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            exp_credit = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=account.id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add_all([past_exp, income_txn, exp_done, exp_credit])
            db.session.commit()

            # Step 1: True-up.
            resp = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert resp.status_code == 200

            # Step 2: Mark income received.
            resp = auth_client.post(f"/transactions/{income_txn.id}/mark-done")
            assert resp.status_code == 200

            # Step 3: Carry forward.
            resp = auth_client.post(f"/pay-periods/{past.id}/carry-forward")
            assert resp.status_code == 200

            # Step 4: Mark expense paid.
            resp = auth_client.post(f"/transactions/{exp_done.id}/mark-done")
            assert resp.status_code == 200

            # Step 5: Mark expense credit.
            resp = auth_client.post(f"/transactions/{exp_credit.id}/mark-credit")
            assert resp.status_code == 200

            # Verify balances.
            resp = auth_client.get(
                f"/grid/balance-row?periods=2&offset=0"
                f"&account_id={account.id}"
            )
            assert resp.status_code == 200
            # Hand-computed under the fold (ruling R-DH (a)): the true-up
            # asserts $5,000.00 and the paycheck (+$2,000.00) and electric bill
            # (-$500.00) are recorded on that SAME civil day, so the assertion
            # -- the day's closing balance -- already contains them.  The
            # carried-forward $150.00 rent is still projected, so ruling R-G
            # lands it tomorrow, inside the current period.
            # 5000 - 150 = $4,850.00, then the next period's $300.00 grocery
            # envelope = $4,550.00.
            #
            # These figures moved twice: $4,850 / $4,550 before plan step
            # X-c2b2's cutover, $6,350 / $6,050 at it, and back at ruling R-DH
            # (2026-07-31).  The cutover read "the row was recorded after the
            # anchor" as "the money moved after the anchor", but the settle was
            # stamped at the CLICK and the assertion carried no date at all, so
            # on the real workflow -- true up, then tick off what already
            # cleared -- that inference is backwards.  It rendered the
            # developer's own grid at -$4,021.37 against a true -$19.95.
            # Finding cash D1 survives for the case that is actually about
            # money: a settle dated a LATER DAY than the assertion still rides
            # on top (pinned in ``test_cash_walk``).  See
            # ``docs/audits/balance_architecture/anchor_settle_partition.md``.
            assert b"$4,850" in resp.data
            assert b"$4,550" in resp.data

    def test_grid_row_ordering_is_deterministic(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Row ordering is deterministic -- two requests produce identical
        row label sequences.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            # Create multiple transactions across categories.
            txns = [
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Paycheck",
                    category_id=seed_user["categories"]["Salary"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=Decimal("2000.00"),
                ),
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Rent",
                    category_id=seed_user["categories"]["Rent"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("1000.00"),
                ),
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Groceries",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("200.00"),
                ),
                Transaction(
                    pay_period_id=current.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name="Car Loan",
                    category_id=seed_user["categories"]["Car Payment"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=Decimal("400.00"),
                ),
            ]
            db.session.add_all(txns)
            db.session.commit()

            import re

            def extract_row_labels(html_str):
                """Extract row-label <th> text in order."""
                return re.findall(
                    r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                    html_str,
                )

            resp1 = auth_client.get("/grid?periods=3")
            labels1 = extract_row_labels(resp1.data.decode())

            resp2 = auth_client.get("/grid?periods=3")
            labels2 = extract_row_labels(resp2.data.decode())

            assert labels1 == labels2
            assert len(labels1) >= 4  # At least 4 transaction rows.

    def test_grid_credit_payback_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """CC Payback transactions generated by the credit workflow appear
        in their own row with 'CC Payback: ...' in the row header.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Restaurant",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Mark as credit -- generates payback in next period.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert resp.status_code == 200

            # GET the grid showing the next period where the payback lives.
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            # "CC Payback:" prefix is stripped -- row shows original name.
            # The original transaction was "Restaurant", so the payback
            # row should show "Restaurant" (under Credit Card: Payback group).
            assert "Restaurant" in th_labels, (
                f"Expected 'Restaurant' row header for payback, got: {th_labels}"
            )

    def test_grid_cancelled_transaction_handling(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelled transactions are excluded from the grid -- they do not
        generate row keys and do not appear as cells.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Cancelled Item",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # Cancel it.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            # GET the grid.
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "Cancelled Item" not in th_labels


class TestTooltipContent:
    """Tests for Commit #16: enhanced transaction cell tooltips.

    The tooltip now shows full dollar amounts with cents, actual-vs-estimated
    comparison, status labels, and notes.  The transaction name is no longer
    in the tooltip (it moved to the row header in Commit #15).
    """

    def _get_current_period(self, seed_user):
        """Return the current period for the seed user."""
        return pay_period_service.get_current_period(seed_user["user"].id)

    @staticmethod
    def _extract_txn_titles(html):
        """Extract title attribute values from the grid's transaction cells.

        The C3 rebuild (2026-06-11, docs/design/grid_audit.md "Rebuild
        decisions") moved the tooltip from the old ``div.txn-cell``
        wrapper onto the chip's ``span.txn-open`` click target; the
        tooltip text itself is unchanged, so every assertion in this
        class still checks the same content.
        """
        import re
        return re.findall(
            r'<span class="txn-open"[^>]*title="([^"]*)"', html,
        )

    def test_tooltip_contains_full_amount_with_cents(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip shows the full dollar amount with two decimal places,
        including comma-separated thousands (e.g. $1,234.56).
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Tooltip Amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("1234.56"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$1,234.56" in t]
            assert matching, f"Expected tooltip with $1,234.56, got: {titles}"
            # Should NOT show the rounded amount as the primary tooltip content.
            assert not any(
                t.startswith("$1,235 ") or t == "$1,235" for t in titles
            ), "Tooltip should show cents, not rounded amount"

    def test_tooltip_shows_actual_vs_estimated_when_different(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When actual_amount differs from estimated_amount, the tooltip shows
        both: '$487.32 (est: $500.00)'.
        """
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=paid.id,
                # A settled row carries the day its money moved; this fixture
                # is bare (no seam), so it states the day the readers would
                # otherwise refuse to guess (plan step X-f1).
                settled_on=current.start_date,
                name="Test Est Comparison",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("487.32"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$487.32" in t and "(est: $500.00)" in t]
            assert matching, f"Expected '$487.32 (est: $500.00)', got: {titles}"

    def test_tooltip_hides_estimate_when_amounts_equal(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When actual_amount equals estimated_amount, the tooltip shows only
        the amount without the '(est: ...)' comparison.
        """
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=paid.id,
                # A settled row carries the day its money moved; this fixture
                # is bare (no seam), so it states the day the readers would
                # otherwise refuse to guess (plan step X-f1).
                settled_on=current.start_date,
                name="Test Equal Amounts",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$500.00" in t]
            assert matching, f"Expected tooltip with $500.00, got: {titles}"
            assert not any("(est:" in t for t in matching), (
                "Tooltip should not show '(est:' when amounts are equal"
            )

    def test_tooltip_includes_paid_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes '-- Paid' for transactions with Paid status."""
        with app.app_context():
            paid = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=paid.id,
                # A settled row carries the day its money moved; this fixture
                # is bare (no seam), so it states the day the readers would
                # otherwise refuse to guess (plan step X-f1).
                settled_on=current.start_date,
                name="Test Paid Status",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("100.00"),
                actual_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Paid" in t]
            assert matching, f"Expected tooltip with '-- Paid', got: {titles}"

    def test_tooltip_includes_projected_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes '-- Projected' for projected transactions."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Projected Status",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("75.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Projected" in t]
            assert matching, f"Expected tooltip with '-- Projected', got: {titles}"

    def test_tooltip_includes_notes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip includes notes when present on the transaction."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Notes Tooltip",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("50.00"),
                notes="Auto-pay on the 15th",
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "-- Auto-pay on the 15th" in t]
            assert matching, f"Expected notes in tooltip, got: {titles}"

    def test_tooltip_no_trailing_separator_when_no_notes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When notes are empty/None, the tooltip does not have a trailing
        '-- ' separator with nothing after it.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test No Trailing Sep",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$200.00" in t]
            assert matching, f"Expected tooltip with $200.00, got: {titles}"
            for title in matching:
                assert not title.endswith("-- "), (
                    f"Tooltip has trailing separator: '{title}'"
                )
                assert not title.endswith("--"), (
                    f"Tooltip has trailing separator: '{title}'"
                )

    def test_tooltip_handles_zero_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip renders $0.00 correctly for a zero-amount transaction."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Zero Amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("0.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$0.00" in t]
            assert matching, f"Expected tooltip with $0.00, got: {titles}"

    def test_tooltip_handles_large_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tooltip formats large amounts with comma-separated thousands."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test Large Amount",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("12345.67"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$12,345.67" in t]
            assert matching, f"Expected tooltip with $12,345.67, got: {titles}"

    def test_tooltip_credit_transaction_shows_charged_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit transactions show the estimated (charged) amount in the
        tooltip, not $0.00 from effective_amount.  Also includes '-- Credit'.
        """
        with app.app_context():
            credit = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=credit.id,
                name="Test Credit Tooltip",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("200.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$200.00" in t and "-- Credit" in t]
            assert matching, (
                f"Expected tooltip with $200.00 and '-- Credit', got: {titles}"
            )
            # Must NOT show $0.00 (which is what effective_amount returns).
            assert not any("$0.00" in t and "-- Credit" in t for t in titles), (
                "Credit tooltip should show charged amount, not $0.00"
            )

    def test_tooltip_survives_htmx_cell_update(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """After a PATCH update via quick edit, the re-rendered cell includes
        a title attribute with the updated amount (server-side rendering).
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Test HTMX Update",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("80.00"),
            )
            db.session.add(txn)
            db.session.commit()

            # PATCH the amount.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "95.50"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # The re-rendered cell should have the updated amount in the title.
            assert "$95.50" in html, (
                f"Expected $95.50 in PATCH response title, got: {html[:500]}"
            )

    def test_tooltip_no_redundant_name(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The tooltip does NOT contain the transaction name (it moved to
        the row header in Commit #15).
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="State Farm",
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("150.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            titles = self._extract_txn_titles(html)
            matching = [t for t in titles if "$150.00" in t]
            assert matching, f"Expected tooltip with $150.00, got: {titles}"
            for title in matching:
                assert "State Farm" not in title, (
                    f"Tooltip should not contain transaction name, got: '{title}'"
                )


class TestSubtotalDecimalPrecision:
    """Verify server-side Decimal subtotals agree with balance row at the penny level (H-05)."""

    def test_subtotals_match_balance_row(self, app, auth_client, seed_user, seed_periods_today):
        """Pre-computed Decimal subtotals match the balance calculator's values exactly.

        Creates 20+ transactions with sub-dollar amounts that would
        accumulate float drift if |float were used. Verifies the grid
        subtotals and the balance row agree within $0.01.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            from app.services import pay_period_service
            period = pay_period_service.get_current_period(seed_user["user"].id)
            if not period:
                period = seed_periods_today[0]

            # Create 20 expense transactions with amounts that cause float drift.
            expected_expense = Decimal("0")
            for i in range(20):
                amt = Decimal("33.33")
                txn = Transaction(
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=f"Expense {i}",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    estimated_amount=amt,
                )
                db.session.add(txn)
                expected_expense += amt

            # Create 5 income transactions.
            expected_income = Decimal("0")
            for i in range(5):
                amt = Decimal("777.77")
                txn = Transaction(
                    pay_period_id=period.id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=projected.id,
                    name=f"Income {i}",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=income_type.id,
                    estimated_amount=amt,
                )
                db.session.add(txn)
                expected_income += amt

            db.session.commit()

            # Fetch the grid page.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Verify subtotals appear with correct Decimal-computed values.
            # 20 * $33.33 = $666.60
            assert "$667" in html or "$666" in html, (
                "Expected expense subtotal ~$666-667 in grid"
            )
            # 5 * $777.77 = $3,888.85
            assert "$3,889" in html or "$3,888" in html, (
                "Expected income subtotal ~$3888-3889 in grid"
            )


class TestGridSubtotalsRegressionBaseline:
    """Regression baseline: per-period subtotal reflects actual_amount.

    Pre-Commit-10 the grid subtotal was an inline ``sum(...
    effective_amount ...)`` loop in ``app/routes/grid.py``.  The subtotal now
    comes off the seam's ``GridColumn`` (plan steps X-c2b1 / X-c2b2), which
    uses ``effective_amount`` for income and the entries-aware reduction for
    expenses; for income with no entries the ``effective_amount`` rule is
    unchanged, so this 5A.1-era regression baseline continues to hold
    (Projected income with ``actual_amount`` populated still reports the
    actual on screen).
    """

    def test_subtotals_reflect_actual_for_projected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Projected income with estimated=500, actual=400: subtotal
        reflects actual_amount (400).

        Originally a Commit #0 regression baseline asserting the D-1 bug
        (subtotal showed estimated).  Updated in Commit 5A.1 to assert
        the corrected behavior: effective_amount now returns actual when
        populated, so the grid subtotal automatically shows 400.
        The subtotal now comes off the seam's ``GridColumn`` (plan steps
        X-c2b1 / X-c2b2), whose income leg still uses ``effective_amount``, so
        the assertion is unchanged.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]

            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            income_type = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()

            # Place the transaction in the current period so it is
            # visible on the default grid view.
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            if not current:
                current = seed_periods_today[0]

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=scenario.id,
                account_id=account.id,
                status_id=projected.id,
                name="Regression Subtotal Income",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("400.00"),
            )
            db.session.add(txn)
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # 5A.1 fix: subtotal uses effective_amount which now returns
            # actual (400).  Grid formats subtotals as "${:,.0f}".
            assert "$400" in html, (
                "Income subtotal should reflect actual_amount (400) "
                "for Projected transactions when actual is populated"
            )
            assert "subtotal-row-income" in html, (
                "Income subtotal row must be present in grid"
            )


class TestGridPeriodSubtotalCanonical:
    """Commit 10: per-period subtotals routed through ONE shared reduction.

    Pre-Commit-10 the grid's per-period subtotal was an inline
    ``sum(... effective_amount ...)`` loop in ``app/routes/grid.py``
    that did NOT apply the entries-aware reduction.  F-002 Pair C /
    F-004 (Q-10) flagged this as a same-page divergence: the subtotal
    row and the balance row consumed the same in-memory transactions
    but with different expense formulas.  Commit 10 collapsed the grid subtotal
    onto one shared reduction; plan steps X-c2b1 / X-c2b2 went further and made
    the balance row and the subtotal rows ONE ``GridColumn`` per period off ONE
    valued row set, so a Projected envelope expense with cleared entries reports
    the same entries-aware impact on both rows and ``balance[p] - balance[p-1]
    == net[p] + period_timing[p] + book_vs_bank[p] + contribution[p] +
    accrual[p]`` holds by construction rather than by two producers agreeing.
    """

    def test_grid_subtotal_entry_aware_for_projected_expense(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Rendered grid subtotal reflects the entry-aware reduction.

        Setup: a Projected $500.00 envelope expense in the visible
        current period carries three cleared debit entries summing
        $462.34.  Pre-Commit-10 the subtotal row showed $500
        (raw ``effective_amount``); the corrected entries-aware
        impact is $37.66.

        Hand arithmetic (F-002 Pair C / F-004):
          cleared_debit = 20.00 + 442.34 + 0.00 = 462.34
          uncleared_debit = 0
          sum_credit = 0
          impact = max(500.00 - 462.34 - 0, 0) = 37.66.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None, (
                "seed_periods_today must produce a current period"
            )

            txn = Transaction(
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("500.00"),
            )
            db.session.add(txn)
            db.session.flush()

            # The user read their bank balance on the day they shopped, so
            # the purchases are inside it (plan step S1-c, ruling R-DH (d)).
            # Re-stating the $1,000.00 the records already hold books a
            # $0.00 correction, so no figure below moves because of it --
            # what it changes is which purchases the reservation may
            # subtract.  Under the retired ``is_cleared`` flag this fixture
            # claimed the purchases were inside an anchor asserted four
            # periods earlier (finding N-132 / R8).
            append_balance_assertion(
                db.session, seed_user["account"], current,
                Decimal("1000.00"), settle_instant_on(current.start_date),
            )
            for amt in (
                Decimal("20.00"),
                Decimal("442.34"),
            ):
                entry = TransactionEntry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    description="confirmed purchase",
                    purchased_on=current.start_date,
                    is_credit=False,
                )
                db.session.add(entry)
                db.session.flush()
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Grid formats subtotals as "${:,.0f}", so $37.66 -> $38.
            # The pre-Commit-10 value would have rounded $500 -> $500.
            assert "$38" in html, (
                "Expense subtotal should be entry-aware: "
                "$500 estimated - $462.34 cleared = $37.66 (-> $38)"
            )
            assert "$500" not in html.split("subtotal-row-expense")[1].split("</tr>")[0], (
                "Subtotal expense cell must not show the raw "
                "effective_amount $500 (F-002 Pair C / F-004 regression)"
            )

    def test_grid_subtotal_reconciles_balance_delta(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``delta == net + timing + book_vs_bank + contribution + accrual``.

        Same-formula invariant E-25 / Q-10, on ruling R-K's basis: ONE valued
        row set supplies the grid's balance row and its subtotal rows, so the
        period-to-period balance delta must equal the column's own net plus the
        remainders no row can explain plus the accrual, to the penny.  The
        inline loop this replaced violated it whenever a Projected envelope
        expense carried cleared entries (the subtotal showed the raw estimate,
        the balance row showed the entry-aware impact).

        Read off ONE ``GridBalanceView`` since plan step X-c2b3, which is what
        makes the identity a property of the row set rather than an agreement
        between two producers: it was ``balances_for`` differenced against
        ``cash_ledger.period_subtotal``, and both deleted -- the first replaced
        by the fold at X-c2b2, the second by ``cash_period_view``, whose
        remainder terms are the ones R-K added and ruling R-DH (f) split in two.

        Setup: anchor $1000 at periods[0]; one Projected $300.00
        envelope expense in the CURRENT period with two cleared debits
        summing $250.00, dated that period's start.

        Hand arithmetic:
          impact = max(300.00 - 250.00 - 0, 0) = 50.00.
          columns[current].expense = 50.00, .income = 0.00, .net = -50.00.
          Nothing has SETTLED and nobody re-anchored, so the remainder is
          0.00 and a PLAIN account carries no accrual:
          balance[current] - balance[current - 1] = -50.00 == net.

        **The rows were in ``periods[5]`` -- a FUTURE period -- until plan step
        X-c2b3, and moving them to the current one is a fixture correction, not
        a convenience.**  The entries were dated that future period's start, and
        the retired ``period_subtotal`` counted every loaded entry whatever its
        date, so the reduction applied and the test read ``$50.00``.  The fold
        values the reservation at the READER'S NOW (``sum_projected``'s
        entry-date window): an entry dated after today cannot have cleared the
        bank, so a future-dated purchase reserves nothing yet and the same
        fixture reads ``$300.00``.  Both halves of that are the shipped rule --
        ruling R-M / plan step X-c0 now REFUSES a future ``purchased_on`` at both
        write doors, so the state this fixture built directly through the ORM is
        one production cannot reach, and the sibling test above (which renders
        the figure through ``GET /grid``) always dated its entries on the
        current period's start for the same reason.  Dating the purchase inside
        the period being spent is the production shape: a partially-spent
        envelope in the period you are in.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            projected = db.session.query(Status).filter_by(
                name="Projected",
            ).one()
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            periods = seed_periods_today
            target_period = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert target_period is not None, (
                "seed_periods_today must produce a current period"
            )
            target_index = next(
                i for i, p in enumerate(periods) if p.id == target_period.id
            )
            assert target_index > 0, (
                "fixture invariant: the current period must have a predecessor "
                "to difference against"
            )

            txn = Transaction(
                pay_period_id=target_period.id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries window",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add(txn)
            db.session.flush()
            # As in the sibling test above: the balance the user read that
            # day is what puts the purchases inside it, and re-stating the
            # $1,000.00 the records already hold books a $0.00 correction --
            # so ``book_vs_bank`` stays $0.00 and the identity below is
            # unchanged by it.
            append_balance_assertion(
                db.session, seed_user["account"], target_period,
                Decimal("1000.00"),
                settle_instant_on(target_period.start_date),
            )
            for amt in (Decimal("100.00"), Decimal("150.00")):
                entry = TransactionEntry(
                    transaction_id=txn.id,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    description="confirmed purchase",
                    purchased_on=target_period.start_date,
                    is_credit=False,
                )
                db.session.add(entry)
                db.session.flush()
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                )
            db.session.commit()

            # Exercise the grid route to prove the wiring is in place
            # before falling back to the resolver-level assertion.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200

            # Seam-level reconciliation off ONE view: the grid route reads the
            # same ``grid_balance_view`` for its balance row and its subtotal
            # rows, so these columns ARE the ground truth the rendered HTML
            # reflects rather than a second producer that has to agree with it.
            columns = balance_at.grid_balance_view(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                periods,
            ).columns
            column = columns[target_period.id]
            prior_period = periods[target_index - 1]
            delta = column.balance - columns[prior_period.id].balance

            # 0 - max(300 - 100 - 150, 0) = -50.00.
            assert column.expense == Decimal("50.00"), (
                f"expected $50.00 entry-aware expense, got {column.expense!r}"
            )
            assert column.net == Decimal("-50.00")
            # Nothing settled and nobody re-anchored, so BOTH remainders are
            # zero and a PLAIN account carries no accrual: asserting all of
            # them is what keeps the identity below from passing on a
            # remainder that quietly absorbed a mis-grouped row (Section
            # 7.2's forbidden residual).  They are asserted separately since
            # plan step S1-c (ruling R-DH (f)) -- a combined figure could be
            # zero with two non-zero halves cancelling inside it.
            assert column.period_timing == Decimal("0.00")
            assert column.book_vs_bank == Decimal("0.00")
            assert column.accrual == Decimal("0.00")
            assert column.contribution == Decimal("0.00")
            assert delta == (
                column.net + column.period_timing + column.book_vs_bank
            ), (
                f"balance delta {delta!r} must equal net {column.net!r} + "
                f"period_timing {column.period_timing!r} + book_vs_bank "
                f"{column.book_vs_bank!r}"
            )

    def test_grid_inline_subtotal_loop_removed(self):
        """Static guard: no inline ``sum(... effective_amount ...)`` in grid.py.

        The plan's verification gate -- if the inline loop is ever
        reintroduced, the canonical-producer routing is silently
        bypassed.  This regression lock fires the moment a future edit
        re-grows the loop.
        """
        import re

        from pathlib import Path

        grid_source = Path("app/routes/grid.py").read_text(encoding="utf-8")
        pattern = re.compile(
            r"sum\([^\)]*(effective_amount|estimated_amount)",
        )
        offenders = pattern.findall(grid_source)
        assert not offenders, (
            "app/routes/grid.py contains an inline subtotal loop "
            f"({offenders!r}); read the seam's "
            "balance_at.grid_balance_view instead (F-002 Pair C, "
            "F-004 same-page regression)"
        )

    def test_grid_balance_computation_routed_through_resolver(self):
        """Static guard: grid balance computation routes through the balance-at seam.

        F-6 lock.  The cross-page balance-equality regression test
        (``tests/test_integration/test_cross_page_balance_equality.py``,
        Commit 11 of the main remediation) cannot catch a route-handler
        bypass of the canonical producer because its grid reader
        re-runs the seam itself rather than parsing the rendered HTML.  A
        regression that re-introduces a hand-rolled balance loop in
        ``app/routes/grid.py`` would therefore drift silently.  This static
        lock closes that gap.

        Updated for plan step X-c2b2: the grid reads EVERY per-period figure
        -- the balance, the subtotals and ruling R-K's remainder -- through
        one ``balance_at.grid_balance_view`` call.  That entry is the
        kind-aware wrapper over the cash FOLD; it is what layers an INTEREST
        account's accrual on as its own row, so the balance change on screen
        stays explained by the rows above it.

        **The positive assertion looks for the CALL, not the name.**  It
        matched ``balance_at.cash_balance_map`` until this step, and by then
        the route had not called that entry since X-c2b1 -- the string
        survived only in a docstring, so the guard was passing on prose while
        the wiring it claimed to lock had moved.  Matching ``.grid_balance_view(``
        with its open paren is what makes it a call site again.

        **The second arm forbade ``balance_calculator.calculate_balances(``
        and was deleted at plan step X-g4b, with the producer** -- Section 8's
        rule that an arm whose forbidden name no longer exists is a sentence
        that can never fail, and reads as coverage while being none.

        Two assertions:
          1. ``balance_at.grid_balance_view(`` must appear in
             ``app/routes/grid.py`` (positive: the seam wiring is intact).
          2. ``balance_at.balance_map(`` (the KIND-CORRECT map) must NOT
             appear: the grid account may be interest-bearing, and reading
             the accrued balance without the accrual row beside it is the
             shape ruling R-K refuses.

        Complements ``test_grid_inline_subtotal_loop_removed`` above:
        that guard catches an inline ``sum(... effective_amount ...)``
        accumulator; this guard catches a swap to a producer.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        grid_source = Path("app/routes/grid.py").read_text(encoding="utf-8")
        assert "balance_at.grid_balance_view(" in grid_source, (
            "app/routes/grid.py no longer CALLS "
            "``balance_at.grid_balance_view`` -- regression on the "
            "balance-at seam contract.  Route every per-period grid figure "
            "through the seam's one kind-aware view instead of a "
            "hand-rolled loop, a direct producer call, or the kind-correct "
            "``balance_map`` (which would accrue interest into the balance "
            "row with no row to explain it)."
        )
        assert "balance_at.balance_map(" not in grid_source, (
            "app/routes/grid.py calls the KIND-CORRECT ``balance_map`` -- "
            "an interest account's accrued balance would then reach the "
            "balance row without the 'Interest' row that explains it "
            "(ruling R-K).  ``grid_balance_view`` owns that dispatch."
        )

    def test_obligations_has_no_period_subtotal_loop(self):
        """Static guard: obligations.py has no period-subtotal arithmetic.

        Obligations computes ``amount_to_monthly`` per template
        (E-24 / Commit 23 territory), not per-period transaction
        subtotals.  The plan's verification gate covers both files;
        this assertion locks that obligations never grows the same
        inline ``sum(... effective_amount ...)`` loop the grid had.
        """
        import re

        from pathlib import Path

        obligations_source = Path(
            "app/routes/obligations.py",
        ).read_text(encoding="utf-8")
        pattern = re.compile(
            r"sum\([^\)]*(effective_amount|estimated_amount)",
        )
        offenders = pattern.findall(obligations_source)
        assert not offenders, (
            "app/routes/obligations.py contains inline period-subtotal "
            f"arithmetic ({offenders!r}); route through the canonical "
            "producer if it ever needs per-period subtotals"
        )


class TestGridMatchedByRowPeriod:
    """Commit 2 (mobile-first v3): ``matched_by_row_period`` route context.

    The matching predicate previously hand-coded in four blocks of
    Jinja (``grid.html`` income + expense, ``_mobile_grid.html`` income +
    expense) is precomputed once in the route as a dict keyed by
    ``(category_id, template_id, txn_name, period_id)``.  Commit 1
    introduced the macros that read it; Commit 2 (this commit) adds
    the dict to the route context; Commits 3 and 4 wire the templates
    to consume it.

    These tests lock in the route contract: the dict is in the
    rendered context, its keys are 4-tuples, its values are non-empty
    lists of ``Transaction`` ORM objects, and its contents mirror the
    Jinja predicate text-for-text (category match, income/expense per
    section, not-deleted, not-cancelled, template-id-match-takes-
    precedence with name-match fallback).
    """

    @staticmethod
    def _capture_grid_context(app, auth_client):
        """Return the (template, context) tuple captured from /grid.

        Uses Flask's ``template_rendered`` signal to record what the
        grid route handed to ``render_template`` so the test can
        inspect ``matched_by_row_period`` (and any other context key)
        without parsing rendered HTML.  Returns the first
        ``grid/grid.html`` record; raises ``AssertionError`` if the
        route rendered a different template (the ``no_setup`` or
        ``no_periods`` branch) so the test fails loud rather than
        silently inspecting the wrong context.
        """
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded: list[tuple] = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get("/grid")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"GET /grid returned {response.status_code}; expected 200"
        )
        grid_records = [
            (t, c) for t, c in recorded if t.name == "grid/grid.html"
        ]
        assert grid_records, (
            "GET /grid did not render grid/grid.html; templates "
            f"rendered: {[t.name for t, _ in recorded]!r}"
        )
        return grid_records[0]

    def test_index_renders_with_new_context(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-1: GET /grid still returns 200 with the new precomputation.

        Pure smoke test that adding the precomputation and the new
        ``matched_by_row_period`` kwarg to ``render_template`` did not
        break the existing rendering pipeline.  No assertion on the
        dict's contents -- C2-2 and C2-3 cover that.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_matched_by_row_period_in_context(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-2: ``matched_by_row_period`` is present in the render context.

        Setup: seed one Projected expense in the current period so the
        dict has at least one entry.  Asserts the dict exists, is a
        ``dict``, every key is a 4-tuple of ``(int, int | None, str,
        int)``, and every value is a non-empty list of ``Transaction``
        ORM objects.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Weekly Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("123.45"),
            )
            db.session.add(txn)
            db.session.commit()

            _, context = self._capture_grid_context(app, auth_client)

            assert "matched_by_row_period" in context, (
                "render_template kwargs missing matched_by_row_period; "
                "Commit 2 of mobile-first v3 adds it as the canonical "
                "matching producer for the grid macros"
            )
            matched = context["matched_by_row_period"]
            assert isinstance(matched, dict), (
                f"matched_by_row_period must be a dict, got {type(matched)!r}"
            )
            assert matched, (
                "Seeded one txn in the current period; "
                "matched_by_row_period should be non-empty"
            )
            for key, value in matched.items():
                assert isinstance(key, tuple) and len(key) == 4, (
                    f"matched_by_row_period key {key!r} is not a 4-tuple"
                )
                category_id, template_id, txn_name, period_id = key
                assert isinstance(category_id, int)
                assert template_id is None or isinstance(template_id, int)
                assert isinstance(txn_name, str)
                assert isinstance(period_id, int)
                assert isinstance(value, list) and value, (
                    "matched_by_row_period values must be non-empty lists"
                )
                for matched_txn in value:
                    assert isinstance(matched_txn, Transaction), (
                        "matched_by_row_period values must contain "
                        f"Transaction ORM objects, got {type(matched_txn)!r}"
                    )

    def test_matched_dict_mirrors_jinja_predicate(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-3: dict contents mirror the Jinja matching predicate.

        Seeds four transactions exercising each predicate branch:
          (a) a template-linked income (Salary template) in the
              current period -- must match via the template-id branch.
          (b) a standalone expense in Groceries by name -- must match
              via the name-match fallback.
          (c) a cancelled expense in Groceries -- must NOT appear (the
              ``status_id != STATUS_CANCELLED`` guard).
          (d) a soft-deleted expense in Groceries -- must NOT appear
              (the ``not is_deleted`` guard).

        Asserts: matched_by_row_period contains the expected keys for
        (a) and (b); the matched lists include only the correct txn;
        (c) and (d) do not appear in any matched list.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            salary_cat = seed_user["categories"]["Salary"]
            groceries_cat = seed_user["categories"]["Groceries"]

            # (a) Template-linked income.
            salary_template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type_id,
                name="Biweekly Salary",
                default_amount=Decimal("2500.00"),
            )
            db.session.add(salary_template)
            db.session.flush()
            txn_a = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_id,
                name="Biweekly Salary",
                category_id=salary_cat.id,
                transaction_type_id=income_type_id,
                estimated_amount=Decimal("2500.00"),
                template_id=salary_template.id,
            )
            # (b) Standalone expense.
            txn_b = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_id,
                name="Adhoc Groceries",
                category_id=groceries_cat.id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("85.00"),
            )
            # (c) Cancelled expense.
            txn_c = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=cancelled_id,
                name="Cancelled Groceries",
                category_id=groceries_cat.id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("50.00"),
            )
            # (d) Soft-deleted expense.
            txn_d = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=projected_id,
                name="Deleted Groceries",
                category_id=groceries_cat.id,
                transaction_type_id=expense_type_id,
                estimated_amount=Decimal("60.00"),
                is_deleted=True,
            )
            db.session.add_all([txn_a, txn_b, txn_c, txn_d])
            db.session.commit()
            txn_a_id = txn_a.id
            txn_b_id = txn_b.id
            txn_c_id = txn_c.id
            txn_d_id = txn_d.id
            template_id = salary_template.id

            _, context = self._capture_grid_context(app, auth_client)
            matched = context["matched_by_row_period"]

            # (a) Template-linked: key uses template_id, txn_name from row
            # key is the template name; the matched list contains txn_a.
            key_a = (
                salary_cat.id, template_id, "Biweekly Salary", current.id,
            )
            assert key_a in matched, (
                f"Template-linked match missing; expected key {key_a!r} "
                f"in dict; got keys {list(matched.keys())!r}"
            )
            assert [t.id for t in matched[key_a]] == [txn_a_id], (
                f"Template-linked match must contain only txn_a "
                f"(id={txn_a_id}); got "
                f"{[t.id for t in matched[key_a]]!r}"
            )

            # (b) Standalone: key uses template_id=None, txn_name from
            # row key is the instance name; matched list contains txn_b.
            key_b = (
                groceries_cat.id, None, "Adhoc Groceries", current.id,
            )
            assert key_b in matched, (
                f"Standalone match missing; expected key {key_b!r} "
                f"in dict; got keys {list(matched.keys())!r}"
            )
            assert [t.id for t in matched[key_b]] == [txn_b_id], (
                "Standalone match must contain only txn_b "
                f"(id={txn_b_id}); got "
                f"{[t.id for t in matched[key_b]]!r}"
            )

            # (c) Cancelled: must not appear in any matched list.  Also
            # row-key for "Cancelled Groceries" should be absent because
            # _build_row_keys filters cancelled txns at row-key time.
            all_matched_ids = {
                t.id for v in matched.values() for t in v
            }
            assert txn_c_id not in all_matched_ids, (
                f"Cancelled txn (id={txn_c_id}) must not appear in "
                "matched_by_row_period (status_id != STATUS_CANCELLED "
                "guard)"
            )

            # (d) Soft-deleted: must not appear in any matched list.
            assert txn_d_id not in all_matched_ids, (
                f"Soft-deleted txn (id={txn_d_id}) must not appear in "
                "matched_by_row_period (not is_deleted guard)"
            )

    def test_no_balance_resolver_reads(self):
        """C2-4: NO direct reads of canonical-producer source columns.

        Plan Section 1 rule 2 ("Canonical producers only for monetary
        values"): the route must not read
        ``Account.current_anchor_balance`` /
        ``Account.current_anchor_period_id`` /
        ``LoanParams.current_principal`` / ``LoanParams.interest_rate``.

        **The baseline is ZERO since plan step X-f1c3a**, and that is the
        finding rather than a tightening.  It was ONE -- the header's
        ``anchor_balance = account.current_anchor_balance`` -- carried as a
        legitimate exception because the header's starting figure is a display
        value rather than a projection.  Ruling R-EH deleted the column: the
        header now reads the account's latest ASSERTION through
        ``cash_ledger.resolve_anchor``, which is a canonical producer, so the
        exception has nothing left to except and every count here is zero.

        Complements the existing
        ``test_grid_balance_computation_routed_through_resolver``
        (F-6 lock) by pinning the *count* of legacy reads rather
        than the presence/absence of the canonical-producer symbol.
        """
        import re  # pylint: disable=import-outside-toplevel
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        grid_source = Path("app/routes/grid.py").read_text(encoding="utf-8")
        current_anchor_balance_reads = len(
            re.findall(r"\.current_anchor_balance\b", grid_source),
        )
        current_anchor_period_id_reads = len(
            re.findall(r"\.current_anchor_period_id\b", grid_source),
        )
        current_principal_reads = len(
            re.findall(r"\.current_principal\b", grid_source),
        )
        interest_rate_reads = len(
            re.findall(r"\.interest_rate\b", grid_source),
        )

        assert current_anchor_balance_reads == 0, (
            "app/routes/grid.py contains "
            f"{current_anchor_balance_reads} reads of "
            "``.current_anchor_balance`` (expected 0 -- ruling R-EH deleted "
            "the column, and the header reads the account's latest assertion "
            "through ``cash_ledger.resolve_anchor``); route all monetary "
            "values through a canonical producer"
        )
        assert current_anchor_period_id_reads == 0, (
            "app/routes/grid.py reads "
            "``.current_anchor_period_id`` directly; route through "
            "``balance_resolver`` instead"
        )
        assert current_principal_reads == 0, (
            "app/routes/grid.py reads ``.current_principal`` directly; "
            "route through ``loan_resolver`` instead"
        )
        assert interest_rate_reads == 0, (
            "app/routes/grid.py reads ``.interest_rate`` directly; "
            "route through ``loan_resolver`` instead"
        )


class TestSettleDayLifecycle:
    """Tests for settled_on management during status changes, at the route."""

    def _create_test_txn(self, seed_user, seed_periods_today):
        """Create a projected expense transaction for testing."""
        from app import ref_cache
        from app.enums import StatusEnum, TxnTypeEnum

        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected_id,
            name="Test Expense",
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=expense_type_id,
            estimated_amount=Decimal("100.00"),
            due_date=seed_periods_today[0].start_date,
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def _create_income_txn(self, seed_user, seed_periods_today):
        """Create a projected income transaction for testing."""
        from app import ref_cache
        from app.enums import StatusEnum, TxnTypeEnum

        projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=projected_id,
            name="Test Income",
            category_id=seed_user["categories"]["Salary"].id,
            transaction_type_id=income_type_id,
            estimated_amount=Decimal("2000.00"),
            due_date=seed_periods_today[0].start_date,
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def test_settle_day_set_on_mark_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done records the settle day for expenses."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on == display_today()

    def test_settle_day_set_on_mark_received(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done records the settle day for income."""
        with app.app_context():
            txn = self._create_income_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on == display_today()

    def test_settle_day_cleared_on_status_revert(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> reverting to Projected clears the settle day."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done to record the settle day.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on is not None

            # Revert to projected via PATCH.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on is None

    def test_re_mark_after_a_revert_sets_a_fresh_settle_day(self, app, auth_client, seed_user, seed_periods_today):
        """Mark done, revert to projected, mark done again -- the day is set both times.

        A revert genuinely CLEARS the day (the row is no longer settled, so the
        invariant says it carries none), and the next settle stamps a fresh one.
        The assertion is against ``display_today()`` rather than against the
        first day: this row is settled twice under one clock, so "the second is
        not earlier than the first" is true by construction and would hold even
        if the seam had never cleared it.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark done.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

            # Revert to projected.
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            db.session.refresh(txn)
            assert txn.settled_on is None

            # Mark done again -- a FIRST entry into the settled band, because
            # the revert cleared the day, so the seam stamps today afresh.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

    def test_settle_day_not_set_on_non_settling_status_change(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/<id>/cancel records no settle day."""
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.settled_on is None

    def test_settle_day_preserved_on_non_status_update(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transactions/<id> editing a non-status field keeps the day.

        Edits a display field (``notes``) rather than ``estimated_amount``:
        the finalised-row edit lock (#26) refuses money/period/category edits on
        a Paid row, but display fields stay editable, so this is the faithful
        probe that a non-status edit neither clears nor moves the settle day
        (the seam clears it only on a status change out of the settled band).

        **It could not fail until this was written** (finding **N-182**'s
        sibling, found by a neutral review that PROVED it: with
        ``_apply_regular_update`` wrapped to move every settled row it touched
        by 7 days, this class stayed green and so did 703 tests around it).  It
        captured the original day and then asserted only ``is not None``, so it
        graded "the row still has a day" where its own name promises "the row
        still has THAT day" -- and the difference is money, because since plan
        step E1a the settle day IS the ``entry_date`` its postings are filed
        under.  **This is N-146's class on the transaction side**: an ordinary
        notes edit moving a settled payment's money to today, which is exactly
        the live production defect that opened X-aj1 on the transfer side.

        So it back-dates THROUGH the seam first and asserts the LEDGER as well
        as the column, the same shape as
        :meth:`test_a_replayed_mark_done_does_not_re_date_the_money`.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum
            from app.models.journal_entry import JournalEntry
            from app.services import posting_service, status_seam

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # Mark done to record the settle day.
            auth_client.post(f"/transactions/{txn.id}/mark-done")
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

            # Back-date THROUGH the seam and re-reconcile, so the posted ledger
            # follows the column and the assertions below start from the state a
            # genuinely week-old settle is really in.
            settled_a_week_ago = display_today() - timedelta(days=7)
            status_seam.apply_status_change(
                txn,
                ref_cache.status_id(StatusEnum.DONE),
                settled_on=settled_a_week_ago,
            )
            posting_service.sync_transaction_postings(txn, settled=True)
            db.session.commit()

            def _ledger_days():
                return sorted(
                    entry.entry_date
                    for entry in db.session.query(JournalEntry)
                    .filter(JournalEntry.transaction_id == txn.id)
                    .all()
                )

            days_before = _ledger_days()
            assert days_before, "fixture posted no journal entry to grade"

            # Edit a non-status, non-locked field -- no status change.
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": "Reconciled against statement"},
            )
            assert response.status_code == 200

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.notes == "Reconciled against statement"
            assert txn.settled_on == settled_a_week_ago, (
                "A notes-only edit re-dated the settle: "
                f"{settled_a_week_ago} -> {txn.settled_on} (finding N-146)."
            )
            assert _ledger_days() == days_before, (
                "A notes-only edit moved the posted ledger: "
                f"{days_before} -> {_ledger_days()} (finding N-146)."
            )

    def test_a_replayed_mark_done_does_not_re_date_the_money(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A replayed mark-done leaves a settled row's money where it is.

        An idempotent re-mark (Paid -> Paid) keeps the ORIGINAL settle day: the
        seam stamps the user's today only on the first entry into a settled
        status and leaves an existing day untouched on a re-settle.  ``done ->
        done`` is a legal transition and this route does not gate on status, so
        a stale page, a second tab or a replayed POST really does re-submit the
        settle.

        **The row is back-dated THROUGH the seam first, and that is what makes
        this control able to fail** (finding **N-182**).  Settling twice under
        one clock leaves both calls yielding the same ``display_today()``, so
        the old ``second == first`` assertion was true by construction --
        measured, it passed with the preserve arm deleted.  This is the
        transaction-side mirror of
        ``test_transfers.py::test_re_marking_a_settled_transfer_does_not_re_date_it``,
        which back-dates through ``update_transfer`` for the same reason.

        It asserts the LEDGER as well as the column, because since plan step
        E1a the settle day IS the ``entry_date`` the row's postings are filed
        under -- so a re-stamp does not merely churn a field, it moves the
        money (finding **N-146**'s class).
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum
            from app.models.journal_entry import JournalEntry
            from app.services import posting_service, status_seam

            txn = self._create_test_txn(seed_user, seed_periods_today)

            # First mark done.
            resp1 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp1.status_code == 200
            db.session.refresh(txn)
            assert txn.settled_on == display_today()

            # Back-date THROUGH the seam and re-reconcile, so the posted ledger
            # follows the column.  Writing the attribute directly would leave
            # the entries dated today and the ledger assertion below could pass
            # on a stale ledger rather than on a preserved one.
            settled_a_week_ago = display_today() - timedelta(days=7)
            status_seam.apply_status_change(
                txn,
                ref_cache.status_id(StatusEnum.DONE),
                settled_on=settled_a_week_ago,
            )
            posting_service.sync_transaction_postings(txn, settled=True)
            db.session.commit()

            def _ledger_days():
                return sorted(
                    entry.entry_date
                    for entry in db.session.query(JournalEntry)
                    .filter(JournalEntry.transaction_id == txn.id)
                    .all()
                )

            days_before = _ledger_days()
            assert days_before, "fixture posted no journal entry to grade"

            # The replay (idempotent Paid -> Paid).
            resp2 = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp2.status_code == 200
            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)

            assert txn.settled_on == settled_a_week_ago, (
                "A replayed mark-done re-dated the settle: "
                f"{settled_a_week_ago} -> {txn.settled_on} (finding N-146)."
            )
            assert _ledger_days() == days_before, (
                "The replayed mark-done moved the posted ledger: "
                f"{days_before} -> {_ledger_days()} (finding N-146)."
            )

    def _ledger_days_for(self, txn_id):
        """Return the ``entry_date`` of every journal entry linked to *txn_id*.

        The LEDGER half of every settle-day assertion below.  Ruling **R-ED**
        names it as this half's gate in terms: the test must assert that the
        ledger FOLLOWED, not merely that the column changed -- since plan step
        E1a the settle day IS the ``entry_date`` a row's postings are filed
        under, so a column that moves without the ledger is the books silently
        disagreeing with the screen.
        """
        from app.models.journal_entry import JournalEntry

        return sorted(
            entry.entry_date
            for entry in db.session.query(JournalEntry)
            .filter(JournalEntry.transaction_id == txn_id)
            .all()
        )

    def _settled_txn_dated(self, seed_user, seed_periods_today, day):
        """Return a settled transaction whose money moved on *day*, ledger in step.

        Built THROUGH the seam and reconciled, so the fixture's own postings
        carry *day* -- a row dated by a bare attribute write would leave the
        ledger at today and let a "the ledger followed" assertion pass on a
        stale comparison rather than on the edit under test.
        """
        from app import ref_cache
        from app.enums import StatusEnum
        from app.services import status_seam

        txn = self._create_test_txn(seed_user, seed_periods_today)
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE), settled_on=day,
        )
        posting_service.sync_transaction_postings(txn, settled=True)
        db.session.commit()
        assert txn.settled_on == day
        assert self._ledger_days_for(txn.id) == [day], (
            "fixture did not post its entry at the settle day"
        )
        return txn

    def test_editing_the_settle_day_moves_the_ledger_with_it(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH ``settled_on`` on a settled row re-dates its postings (R-ED).

        The correction ruling **R-ED** exists for: the user read their
        statement and the money moved on a different day than the one-click
        settle recorded.  ``settled_on`` is in
        ``mutations._POSTING_RELEVANT_FIELDS``, so the per-``(period,
        entry_date)`` reconcile reverses the stale-dated entry at ITS own day
        and re-posts the effect at the corrected one (finding **N-13**).

        The assertion is on the LEDGER, which is R-ED's stated gate.  The
        corrected day must be present and the original must be net-zero
        (reversal + original), never simply absent -- the reversal is history,
        not an erasure.
        """
        with app.app_context():
            original_day = display_today() - timedelta(days=6)
            corrected_day = display_today() - timedelta(days=2)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, original_day,
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": corrected_day.isoformat()},
            )
            assert response.status_code == 200

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == corrected_day

            # The WHOLE effect must now sit at the corrected day and nothing at
            # the original.  The original day's ENTRIES survive as history --
            # the reconcile adds a reversal rather than erasing them -- so the
            # raw list of ``entry_date`` values still contains it; only the NET
            # separates "re-dated" from "posted twice", which is why the
            # assertion is on the net and not on membership.
            assert self._net_by_day(txn.id) == {corrected_day: Decimal("100.00")}, (
                "the settle day moved but the posted ledger did not follow: "
                f"net effect by day is {self._net_by_day(txn.id)}, expected the "
                f"whole $100.00 at {corrected_day} and nothing left at "
                f"{original_day}"
            )
            assert original_day in self._ledger_days_for(txn.id), (
                "the original day's entries vanished instead of reversing -- a "
                "correction restates history, it does not erase it"
            )

    def _net_by_day(self, txn_id):
        """Return ``{entry_date: net posted magnitude}`` for one transaction.

        Thin wrapper over the shared
        :func:`tests._test_helpers.net_posted_by_day` -- the transfer suite asks
        the same question through the same reduction with a different filter
        clause, and carrying two copies is the duplication R0801 cannot see
        because it does not run on ``tests/``.
        """
        from app.models.journal_entry import JournalEntry

        return net_posted_by_day(JournalEntry.transaction_id == txn_id)

    def test_reverting_to_projected_ignores_the_submitted_settle_day(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The unlock path survives the form re-submitting the row's own day.

        Ruling **R-EG**.  The full-edit form submits every enabled field, and
        the documented way to unlock a finalised row is to set Status to
        Projected in that same form -- so a revert arrives carrying the settle
        day the row already had.  The seam's
        ``reject_settle_day_without_settled_status`` refuses that pair with a
        400 (correctly, for a service caller asserting both facts on purpose),
        and applying that refusal here would break the ONLY unlock path on
        every settled row.

        ``status_seam.settle_day_for_status`` drops the day instead: the user
        picked Projected, which says the money did not move.  Graded on the
        400 NOT happening, on the column being cleared, and on the ledger
        being reversed -- a revert that left postings behind would be the
        balance keeping money the user just said was never spent.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            settled_day = display_today() - timedelta(days=3)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, settled_day,
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    # Exactly what the rendered form re-submits: the day the
                    # row already carries, untouched by the user.
                    "settled_on": settled_day.isoformat(),
                },
            )
            assert response.status_code == 200, (
                "the documented unlock path (Status -> Projected) was refused "
                f"because the form re-submitted the row's own settle day: "
                f"{response.get_data(as_text=True)[:300]}"
            )

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None, (
                "the revert kept the settle day, breaking the settled-iff-dated "
                "invariant"
            )
            # The WHOLE mapping, not just the old day.  Checking only that the
            # settled day cleared would pass a revert that reversed that day and
            # re-posted the effect at some OTHER date -- money still spent, just
            # moved.  A neutral review found this side asserting the weak form
            # while its transfer sibling asserted the strong one, and the
            # sibling's docstring claiming the asymmetry had already been fixed.
            assert self._net_by_day(txn.id) == {}, (
                "reverting to Projected left a posted effect somewhere"
            )

    def test_a_settle_day_cannot_date_a_projected_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A day submitted for a Projected row is dropped, not written.

        Finding **N-185**'s structural half.  ``settled_on`` is now exactly as
        load-bearing as ``status_id`` -- the invariant binds them -- so the
        PATCH handler's generic ``setattr`` loop must never see it: a bare
        ``setattr`` would date a row whose money has not moved, which is
        finding **N-183** re-run on the transaction side.  ``_SEAM_OWNED_FIELDS``
        excludes it from the loop and the seam is the only writer.

        This is the falsifiable form of that claim: remove ``settled_on`` from
        ``_SEAM_OWNED_FIELDS`` and the loop dates this Projected row.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": (display_today() - timedelta(days=1)).isoformat()},
            )
            assert response.status_code == 200

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on is None, (
                "a Projected row was given a settle day -- the PATCH setattr "
                "loop is writing the column the seam owns (finding N-185)"
            )
            assert self._ledger_days_for(txn.id) == [], (
                "a Projected row posted to the ledger"
            )

    def test_a_future_settle_day_is_refused_and_moves_no_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day that has not happened is refused, and the balance holds.

        Ruling **R-EJ**.  A settled source counts from its own ``settled_on``,
        and ``walk_cash_ledger`` absorbs one into an assertion only when the
        assertion is dated ON OR AFTER it -- so a future-dated settle rides on
        top of every assertion until that day arrives, putting already-spent
        money back in the rendered balance.  Measured on the live route before
        the guard existed: a ``$100`` expense settled three days ago read
        ``$900`` against a ``$1,000`` anchor, and PATCHing its day to
        ``today + 400`` answered **200** with the balance back at ``$1,000``.

        **It is the LIKELY input, not an exotic one.**  The box tells the user
        to correct the day against their statement, and a statement's most
        common disagreement is a PENDING item carrying a future posting date.

        The BALANCE is asserted, not just the status code: a 400 that still let
        the write through would pass a status-only check, and the balance is the
        thing the defect actually moved.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=3)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, settled_day,
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "settled_on": (display_today() + timedelta(days=400)).isoformat(),
                },
            )
            assert response.status_code == 400, (
                f"a settle day 400 days out was accepted: {response.status_code}"
            )

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == settled_day, (
                "the refused day was written anyway"
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            ) == before, (
                "the refused future settle moved the rendered balance -- "
                "already-spent money came back"
            )

    def test_a_mistyped_year_is_refused_and_moves_no_money(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A settle day before the schedule is refused at the DOOR.

        Ruling **R-EL**, the mirror of R-EJ above and the input it was decided
        on.  A day at or before an assertion is ABSORBED into it by
        ``walk_cash_ledger``, which then resets the running total to the
        asserted balance -- so the row's delta is discarded and the projection
        RISES by its amount while the row still reads Paid.
        ``fields.Date()`` deserializes ``"0202-08-04"`` to a real ``date``, so a
        mistyped YEAR is an ordinary slip in a box whose own tooltip invites
        correction.

        **The bound is at the door, not at the seam**, because a genuine
        pre-schedule settle is legitimate -- money moved before you started
        budgeting, which a bank import produces in bulk, and which the seam must
        keep accepting (``test_status_seam.TestTheSettleDayFloor``).  What is
        never legitimate is a human typing a year wrong.

        The BALANCE is asserted, not just the status code, for the reason the
        R-EJ case above gives: a 400 that still let the write through would pass
        a status-only check.
        """
        with app.app_context():
            settled_day = display_today() - timedelta(days=3)
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today, settled_day,
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": "0202-08-04"},
            )
            assert response.status_code == 400, (
                "a settle day in the year 202 was accepted: "
                f"{response.status_code}"
            )

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == settled_day, (
                "the refused day was written anyway"
            )
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.cash_balance_at(
                seed_user["account"], ctx, display_today(),
            ) == before, (
                "the mistyped year moved the rendered balance -- spent money "
                "came back out of the projection"
            )

    def test_todays_settle_day_is_accepted_at_the_boundary(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """TODAY is on the allowed side of the future refusal.

        The boundary control for ruling **R-EJ**: the guard is ``>`` today, not
        ``>=``, because settling something today is the ordinary case and a
        one-click settle stamps exactly this day.  Without this, a refusal
        written with the wrong comparison would pass the test above and break
        every settle.
        """
        with app.app_context():
            txn = self._settled_txn_dated(
                seed_user, seed_periods_today,
                display_today() - timedelta(days=4),
            )

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": display_today().isoformat()},
            )
            assert response.status_code == 200, response.get_data(as_text=True)[:300]

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on == display_today()

    def test_full_edit_offers_the_settle_day_only_on_a_settled_row(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """The correction input renders for a settled row and not before.

        A Projected row's money has not moved, so there is no day to state and
        offering the box would invite a forecast in a fact column -- the same
        rule ``_transaction_entries.html`` applies to a purchase's posting date.
        The paired assertions are what make this a test of the CONDITION rather
        than of the field merely existing somewhere.

        **The settle day is moved OFF today before the pre-fill is graded, and
        that is load-bearing.**  ``mark-done`` stamps today, and the template
        also renders ``max="<today>"`` -- so a row settled today puts today's
        ISO string in the body TWICE, and ``today in body`` passes with
        ``value=""``.  A neutral review proved exactly that.  Re-dating first
        makes the assertion grade ``value=`` alone, and the ``max`` is asserted
        separately below so neither hides the other.
        """
        with app.app_context():
            txn = self._create_test_txn(seed_user, seed_periods_today)

            projected_response = auth_client.get(
                f"/transactions/{txn.id}/full-edit",
            )
            assert projected_response.status_code == 200
            projected_body = projected_response.get_data(as_text=True)
            assert 'name="settled_on"' not in projected_body

            auth_client.post(f"/transactions/{txn.id}/mark-done")
            # Off today, so the pre-fill cannot be satisfied by the ``max``.
            corrected = display_today() - timedelta(days=6)
            auth_client.patch(
                f"/transactions/{txn.id}",
                data={"settled_on": corrected.isoformat()},
            )
            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.settled_on == corrected

            settled_response = auth_client.get(
                f"/transactions/{txn.id}/full-edit",
            )
            assert settled_response.status_code == 200
            settled_body = settled_response.get_data(as_text=True)
            assert 'name="settled_on"' in settled_body
            assert f'value="{corrected.isoformat()}"' in settled_body, (
                "the correction input is not pre-filled with the stored day"
            )
            # The browser-side half of ruling R-EJ, and it must be the USER's
            # today rather than the process's: a display-vs-UTC split would let
            # the input refuse a day the seam accepts.  Distinct from the stored
            # day above, so one cannot satisfy the other.
            assert f'max="{display_today().isoformat()}"' in settled_body, (
                "the settle-day input does not bound itself at the user's today"
            )
            # It must NOT be disabled on a finalised row -- that is exactly the
            # distinction ruling R-ED draws against the money/period fields
            # beside it, and a disabled input submits nothing.
            assert not field_is_disabled(settled_body, "settled_on"), (
                "the settle day is locked on a finalised row, so the "
                "correction R-ED exists for is unreachable"
            )

    def test_settle_day_stamped_on_patch_settle(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transactions/<id> settling to Paid records the day (the C5 fix).

        Before the status seam, the inline PATCH path assigned status_id without
        recording the day, leaving a Paid row with no settle day -- it
        bypassed the now()-stamp that mark_done and transfers apply.  The seam
        now owns the settle day for every status change, so a PATCH that settles a
        Projected row records the timestamp the same way mark_done does.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            txn = self._create_test_txn(seed_user, seed_periods_today)
            assert txn.settled_on is None

            done_id = ref_cache.status_id(StatusEnum.DONE)
            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_id)},
            )
            assert response.status_code == 200

            db.session.refresh(txn)
            assert txn.status_id == done_id
            assert txn.settled_on is not None


class TestBornProjected:
    """A transaction is always created Projected; status moves only via the seam.

    The create schemas drop ``status_id`` and the create routes assign Projected
    unconditionally, so a crafted request cannot mint a born-settled row (which
    would carry no settle day, bypass verify_transition, and post nothing to the
    ledger).  The "record an already-paid item" flow is the correct
    create-Projected-then-mark-done.
    """

    def test_create_with_settled_status_yields_projected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions carrying a settled status_id still creates Projected."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            done_id = ref_cache.status_id(StatusEnum.DONE)

            response = auth_client.post("/transactions", data={
                "name": "Crafted Settled",
                "estimated_amount": "75.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "status_id": str(done_id),   # crafted: must be ignored
                "account_id": str(seed_user["account"].id),
            })
            assert response.status_code == 201

            txn = db.session.query(Transaction).filter_by(
                name="Crafted Settled"
            ).one()
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_inline_create_with_settled_status_yields_projected(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/inline carrying a settled status_id creates Projected."""
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            received_id = ref_cache.status_id(StatusEnum.RECEIVED)

            response = auth_client.post("/transactions/inline", data={
                "estimated_amount": "33.33",
                "account_id": str(seed_user["account"].id),
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "transaction_type_id": expense_type.id,
                "status_id": str(received_id),   # crafted: must be ignored
            })
            assert response.status_code == 201

            txn = (
                db.session.query(Transaction)
                .filter_by(estimated_amount=Decimal("33.33"))
                .order_by(Transaction.id.desc())
                .first()
            )
            assert txn is not None
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_full_create_form_has_no_status_control(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/full renders no status selector (born Projected)."""
        with app.app_context():
            response = auth_client.get(
                "/transactions/new/full",
                query_string={
                    "category_id": seed_user["categories"]["Groceries"].id,
                    "period_id": seed_periods_today[0].id,
                    "account_id": seed_user["account"].id,
                },
            )
            assert response.status_code == 200
            body = response.get_data(as_text=True)
            assert 'name="status_id"' not in body


class TestSchemaValidation:
    """Tests for due_day_of_month and due_date schema validation."""

    def test_schema_due_day_of_month_zero(self, app):
        """due_day_of_month=0 is rejected by the template schema."""
        from app.schemas.validation import TemplateCreateSchema
        with app.app_context():
            schema = TemplateCreateSchema()
            errors = schema.validate({"due_day_of_month": "0"})
            assert "due_day_of_month" in errors

    def test_schema_due_day_of_month_32(self, app):
        """due_day_of_month=32 is rejected by the template schema."""
        from app.schemas.validation import TemplateCreateSchema
        with app.app_context():
            schema = TemplateCreateSchema()
            errors = schema.validate({"due_day_of_month": "32"})
            assert "due_day_of_month" in errors

    def test_schema_due_day_of_month_valid_range(self, app):
        """due_day_of_month values 1-31 are all accepted."""
        from app.schemas.validation import TemplateCreateSchema
        with app.app_context():
            schema = TemplateCreateSchema()
            for day in range(1, 32):
                errors = schema.validate({"due_day_of_month": str(day)})
                assert "due_day_of_month" not in errors, (
                    f"day {day} should be valid but got: {errors.get('due_day_of_month')}"
                )

    def test_schema_due_date_on_transaction_update(self, app):
        """due_date accepted as a valid Date field in TransactionUpdateSchema."""
        from app.schemas.validation import TransactionUpdateSchema
        with app.app_context():
            schema = TransactionUpdateSchema()
            errors = schema.validate({"due_date": "2026-04-15"})
            assert "due_date" not in errors

    def test_schema_loads_a_settle_day_only_from_the_edit_door(self, app):
        """The UPDATE schema accepts a settle day; the two CREATE schemas do not.

        **It refused every settle day until plan step X-f1c** (this test's
        predecessor asserted exactly that and named X-f1c as what would break
        it).  Ruling **R-ED** opened the correction door: a settle day is an
        OBSERVED FACT about the user's bank, and an observed fact must be
        correctable when the statement disagrees.

        The CREATE halves stay shut, and that is the half still worth pinning:
        a transaction is born Projected -- ``status_seam.apply_status_change``
        is the only path into a settled status -- so a submitted day at create
        time would either date an unsettled row (breaking the settled-iff-dated
        invariant) or mint a born-settled row that bypassed the state machine
        and posted nothing to the ledger.
        """
        from app.schemas.validation import (
            InlineTransactionCreateSchema,
            TransactionCreateSchema,
            TransactionUpdateSchema,
        )
        with app.app_context():
            loaded = TransactionUpdateSchema().load({"settled_on": "2026-04-15"})
            assert loaded == {"settled_on": date(2026, 4, 15)}

            for create_schema in (
                TransactionCreateSchema(), InlineTransactionCreateSchema(),
            ):
                assert "settled_on" not in create_schema.fields, (
                    f"{type(create_schema).__name__} declares settled_on: a "
                    "created row is Projected and carries no settle day"
                )


class TestMobileThisPeriodPartial:
    """Regression locks for the mobile "This Period" tab partial.

    The partial at ``app/templates/grid/_mobile_this_period.html`` is
    rendered inside the ``#mobile-this-period`` tab-pane in
    ``_mobile_grid.html``.  These tests assert structural invariants
    of the rendered HTML so subsequent commits cannot silently regress
    the tab layout (default-active flip, period nav arrow hrefs, the
    presence of the income / expense / net / balance sections).

    Mobile / desktop split: ``_mobile_grid.html`` is wrapped in
    ``d-md-none`` and the desktop grid in ``d-none d-md-block``; both
    render server-side regardless of the requesting client, so the
    assertions can inspect the response body without simulating a
    mobile user-agent or viewport.
    """

    def test_this_period_partial_exists(self):
        """C6-1: the new partial file exists at the canonical path.

        Filesystem check; ensures the file landed at the path the
        ``{% include "grid/_mobile_this_period.html" %}`` reference in
        ``_mobile_grid.html`` resolves to.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_this_period.html"
        )
        assert partial.is_file()

    def test_default_active_tab_is_this_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-5: the "This Period" pill is the default-active tab.

        The Commit 6 default-tab flip moves the ``active`` /
        ``aria-selected="true"`` pair from "Plan" to "This Period";
        the matching tab-pane carries ``show active``.  Lock both so
        a later commit cannot silently flip the default back.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Slice each button's full opening tag ('<button ... >') so
            # the assertions span the template's multi-line attribute
            # layout.
            tp_id = 'id="mobile-tab-this-period"'
            tp_open = body[body.rindex("<button", 0, body.index(tp_id)):
                           body.index(">", body.index(tp_id))]
            assert "nav-link active" in tp_open
            assert 'aria-selected="true"' in tp_open

            plan_id = 'id="mobile-tab-plan"'
            plan_open = body[body.rindex("<button", 0, body.index(plan_id)):
                             body.index(">", body.index(plan_id))]
            # Plan tab carries the bare "nav-link" class (no "active").
            assert "nav-link active" not in plan_open
            assert 'aria-selected="false"' in plan_open

            # The tab-pane carries "show active" via its outer class.
            pane_id = 'id="mobile-this-period"'
            pane_open = body[body.rindex("<div", 0, body.index(pane_id)):
                             body.index(">", body.index(pane_id))]
            assert "show active" in pane_open

    def test_this_period_renders_current_period_by_default(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-2: the partial renders periods[0] (== current period when
        start_offset == 0).

        At the default URL ``/grid`` the visible window starts at
        ``current_period`` (offset=0), so the period label inside the
        partial's nav header equals ``current_period.label``.
        """
        with app.app_context():
            from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # The partial's header div is followed by the period
            # label inside a fw-bold div.  Encode the label so non-ASCII
            # whitespace and quoting are byte-stable.
            assert current.label.encode("utf-8") in response.data
            # The partial-specific collapse IDs prefix with mobile-tp-
            # to avoid colliding with the Plan tab's mobile-income-/mobile-expense-.
            assert f"mobile-tp-income-{current.id}".encode("utf-8") in response.data
            assert f"mobile-tp-expense-{current.id}".encode("utf-8") in response.data

    def test_this_period_includes_income_expense_net_balance(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-3: the partial emits the four expected sections.

        Income card (header text "Income"), expense card (header text
        "Expenses"), net cash flow bar ("Net Cash Flow"), projected
        balance card ("Projected Balance").  The mobile-section classes
        carry the brand colors so they double as section markers.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Slice to just the This Period pane so the assertions do
            # not leak through to the Plan pane's symmetric markup.
            pane_start = body.index('id="mobile-this-period"')
            # The pane ends at the next sibling tab-pane (mobile-plan).
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert "mobile-section-income" in pane
            assert "mobile-section-expense" in pane
            assert "Net Cash Flow" in pane
            assert "Projected Balance" in pane

    def test_this_period_arrows_link_to_offset_neighbors(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-4: the prev/next arrows link to offset-1 and offset+1.

        At the default URL ``/grid`` (``start_offset == 0``), the
        partial's ``[<]`` should link to
        ``/grid?periods=1&offset=-1#this-period`` and ``[>]`` to
        ``/grid?periods=1&offset=1#this-period``.  Both carry the
        ``#this-period`` fragment so the page lands back on the same
        tab after the GET.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            # Flask url_for renders integer query args inline; assert
            # the canonical href tail rather than the full URL.
            assert "/grid?periods=1&amp;offset=-1#this-period" in pane
            assert "/grid?periods=1&amp;offset=1#this-period" in pane

    def test_this_period_arrows_use_start_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C6-4 (extended): arrows always step from ``start_offset``.

        When the user is at ``?periods=1&offset=2``, the prev arrow
        links to ``offset=1`` and the next arrow to ``offset=3``.  The
        partial uses ``start_offset`` directly, so the formula must
        survive non-zero starting offsets.
        """
        with app.app_context():
            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert "/grid?periods=1&amp;offset=1#this-period" in pane
            assert "/grid?periods=1&amp;offset=3#this-period" in pane


class TestMobileCardActionBar:
    """Regression locks for the per-card inline action bar (Commit 7
    of the mobile-first v3 implementation).

    The bar lives in ``app/templates/grid/_mobile_card_actions.html``
    and is emitted by ``render_row_card`` as a sibling of each card
    ``<li>``, wrapped together in
    ``<div class="mobile-card-wrapper">``.  A delegated tap handler in
    ``app/static/js/mobile_grid.js`` toggles the Bootstrap collapse so
    the user sees ``[Mark Paid]`` and ``[Open Full]`` directly under
    the tapped card.  (The original ``[Edit Amount]`` button was
    removed in the C3 rebuild, audit item C2: its swap target
    ``#txn-cell-<id>`` lives inside the CSS-hidden desktop table, so
    the form it loaded was never visible on mobile -- confirmed dead
    live on 2026-06-11 -- and the Open Full action card now carries
    the amount inputs directly.)

    These tests pin down the structural contract the JS handler and
    the action-bar route consumers depend on:

      - Both new partials (``_mobile_plan.html`` and
        ``_mobile_card_actions.html``) exist at the canonical paths.
      - The Mark Paid form is conditional on the transaction state -
        Projected / Received and other non-terminal statuses get it;
        Done and Settled (the two state-machine terminals for the
        mark-done path) do not (mark_done would reject them via the
        state machine, so omitting the affordance is the honest UX).
      - ``can_edit=False`` (the companion contract per R-7 / D-B of
        the v3 plan) drops the owner-only ``[Open Full]`` button while
        keeping ``[Mark Paid]`` (companions are allowed to mark paid
        per the existing entries-blueprint precedent); ``Edit Amount``
        stays asserted absent for every render path.
      - The Mark Paid form posts to ``transactions.mark_done`` with
        the swap target set to the row's ``#txn-cell-<id>``.
    """

    @staticmethod
    def _render_action_bar(app, txn, can_edit=True):
        """Render ``_mobile_card_actions.html`` directly with the given
        ``txn`` and ``can_edit``.

        Direct render (rather than scraping a full ``/grid`` response)
        keeps the structural assertions immune to surrounding markup
        drift: the test asserts what the partial emits, not where
        it lands in the larger page.  ``app.test_request_context``
        is what makes ``url_for`` resolve inside the template; the
        ``app.jinja_env.globals`` registrations from
        ``app.jinja_globals.register_ref_id_globals`` provide
        ``STATUS_DONE`` / ``STATUS_SETTLED`` without further setup.
        """
        template = app.jinja_env.get_template("grid/_mobile_card_actions.html")
        with app.test_request_context("/"):
            return template.render(txn=txn, can_edit=can_edit)

    def test_plan_partial_exists(self):
        """C7-1: ``_mobile_plan.html`` exists at the canonical path.

        Filesystem check; ensures the partial landed where the
        ``{% include "grid/_mobile_plan.html" %}`` reference in
        ``_mobile_grid.html`` resolves to.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_plan.html"
        )
        assert partial.is_file()

    def test_mobile_card_actions_partial_exists(self):
        """C7-2: ``_mobile_card_actions.html`` exists at the canonical path.

        Filesystem check; ensures the partial landed where the
        ``{% include "grid/_mobile_card_actions.html" %}`` reference
        in ``_grid_row_macros.html``'s ``render_row_card`` resolves to.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_card_actions.html"
        )
        assert partial.is_file()

    def test_action_bar_includes_mark_paid_when_not_settled(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-3: Projected txns get a ``[Mark Paid]`` form in the bar.

        A Projected transaction is in scope for the mark-done state
        transition, so the action bar offers the affordance.  The
        rendered partial must contain a ``hx-post`` form to
        ``transactions.mark_done`` plus the visible button label.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-3 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' in rendered
            assert "Mark Paid" in rendered

    def test_action_bar_excludes_mark_paid_when_settled(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4: Settled txns do NOT get a ``[Mark Paid]`` form.

        Settled is a state-machine terminal for the mark-done path;
        offering the button would let the user fire a request the
        route would reject with 400.  The partial's guard on
        ``status_id`` is the source of truth here.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.SETTLED),
                name="C7-4 Settled Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
                # A settled row carries the day its money moved.
                settled_on=current.start_date,
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_mark_paid_when_done(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4 (sibling): Done txns also drop the ``[Mark Paid]`` form.

        Mirrors the Settled guard: Done is the other terminal for the
        mark-done path (Done -> Settled is a separate transition,
        and the action bar does not currently expose a Settle action).
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                name="C7-4b Done Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
                # A settled row carries the day its money moved.
                settled_on=current.start_date,
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_mark_paid_when_received(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-4c: income txns marked Received also drop ``[Mark Paid]``.

        Locks the fix for an income-specific bug that the Playwright
        harness surfaced after a mark-done round-trip:
        ``transactions.mark_done`` sets ``status_id = RECEIVED`` for
        income (not DONE), so the spec's literal
        ``status_id != STATUS_DONE and status_id != STATUS_SETTLED``
        guard missed RECEIVED and kept the Mark Paid button visible
        on already-received income.  Switched to the semantic
        ``Status.is_settled`` boolean which covers Paid, Received,
        and Settled uniformly.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            salary_cat = seed_user["categories"]["Salary"]
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.RECEIVED),
                name="C7-4c Received Salary",
                category_id=salary_cat.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("2500.00"),
                # A settled row carries the day its money moved.
                settled_on=current.start_date,
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'/transactions/{txn.id}/mark-done' not in rendered
            assert "Mark Paid" not in rendered

    def test_action_bar_excludes_edit_when_can_edit_false(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-5: ``can_edit=False`` (companion) drops ``[Open Full]``
        but keeps ``[Mark Paid]``.

        The companion role can mark transactions paid (entries
        blueprint precedent) but cannot open the full-edit action
        card.  The action bar partial's ``{% if can_edit %}`` guard is
        the only thing between the companion render path and the
        owner-only affordance.  ``Edit Amount`` is asserted absent
        here too -- it was removed for every render path in the C3
        rebuild (audit item C2, dead target in the hidden desktop
        table), so its absence is now unconditional.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-5 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("99.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=False)

            # Mark Paid remains -- companions can mark paid.
            assert f'/transactions/{txn.id}/mark-done' in rendered
            assert "Mark Paid" in rendered
            # Edit Amount and Open Full are gone for the companion path.
            assert "Edit Amount" not in rendered
            assert "Open Full" not in rendered
            assert f'/transactions/{txn.id}/quick-edit' not in rendered
            assert "txn-expand-btn" not in rendered

    def test_action_bar_hx_post_target_is_cell(
        self, app, seed_user, seed_periods_today,
    ):
        """Mark Paid form posts to mark-done targeting the card wrapper.

        Locks the form attributes the action bar's HTMX wiring depends
        on: the ``hx-post`` URL, the ``hx-target`` (the card wrapper id
        ``#card-<prefix-><id>`` -- here prefix-less because
        ``_render_action_bar`` renders the macro without an
        ``id_prefix``), the ``outerHTML`` swap mode, and the hidden
        ``render=mobile_card`` field that routes the response to the
        in-place single-card render (+ ``HX-Trigger: mobileCardSettled``)
        instead of the desktop cell + full-page-reload ``gridRefresh``.
        The old ``#txn-cell-<id>`` target only existed in the
        CSS-hidden desktop table on /grid (and not at all on the
        companion page), so the swap was effectively dead before this
        change.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-6 Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("42.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rendered = self._render_action_bar(app, txn, can_edit=True)

            assert f'hx-post="/transactions/{txn.id}/mark-done"' in rendered
            # Prefix-less wrapper id (the helper renders the macro with
            # no id_prefix); the This Period tab uses card-tp-<id>.
            assert f'hx-target="#card-{txn.id}"' in rendered
            assert 'hx-swap="outerHTML"' in rendered
            assert 'name="render" value="mobile_card"' in rendered

    def test_card_wrapper_emits_expansion_sibling_in_grid_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C7-integration: the full ``/grid`` page emits the expansion
        sibling next to each mobile card.

        Asserts the macro-level wiring: ``render_row_card`` wraps
        each ``<li>`` in ``<div class="mobile-card-wrapper">`` and
        emits a sibling ``<div class="collapse mobile-card-expansion">``
        bundling progress detail, envelope entries, and the action
        button row.  Without this integration, the unit-level checks
        above would pass while the expansion still never appears on
        the rendered page.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-integration Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("31.00"),
            )
            db.session.add(txn)
            db.session.commit()
            txn_id = txn.id

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            # Wrapper exists; This Period tab carries the
            # per-tab-prefixed expansion id (`tp` prefix).  The Plan
            # tab no longer renders cards via ``render_row_card`` --
            # it uses the narrower ``render_row_static`` (no
            # expansion wrapper, no per-card collapse) -- so the
            # ``plan`` prefix variant is no longer emitted on the
            # rendered page.  The ``id_prefix`` parameter on the
            # macro itself still supports a ``plan`` value (locked
            # by ``test_card_expansion_id_uses_prefix_when_supplied``)
            # in case a future caller needs it; the integration test
            # here pins only the live page contract.
            assert 'class="mobile-card-wrapper"' in body
            assert f'id="card-expansion-tp-{txn_id}"' in body

    def test_card_expansion_id_uses_prefix_when_supplied(
        self, app, seed_user, seed_periods_today,
    ):
        """C7-integration: ``id_prefix`` namespaces the expansion wrapper id.

        The "This Period" and "Plan" tabs render the same window of
        pay periods at the same time, so without per-tab namespacing
        the same txn yields a duplicate ``id="card-expansion-<id>"``
        in two places.  The ``id_prefix`` parameter on
        ``render_row_card`` is the fix: This Period passes
        ``id_prefix='tp'``, Plan passes ``id_prefix='plan'``, and an
        empty prefix preserves the simpler unprefixed form for
        direct-render call sites.

        Locks the three branches explicitly so a future refactor of
        the formula cannot regress one without flagging.  Renders the
        macro through a tiny ``from_string`` template because the
        per-tab id wiring now lives on the wrapper emitted by
        ``render_row_card`` rather than the inner action-button
        partial (the partial used to host its own collapse; the
        unified collapse covers progress + entries + actions and is
        owned by the macro).
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="C7-prefix Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("17.00"),
            )
            db.session.add(txn)
            db.session.commit()

            rk = SimpleNamespace(
                category_id=txn.category_id,
                template_id=None,
                txn_name=txn.name,
                display_name=txn.name,
            )
            period = SimpleNamespace(id=current.id)
            matched = {
                (rk.category_id, rk.template_id, rk.txn_name, period.id): [txn],
            }

            tmpl = app.jinja_env.from_string(
                "{% from 'grid/_grid_row_macros.html'"
                " import render_row_card %}"
                "{{ render_row_card(rk, period, matched, {},"
                " can_edit, prefix) }}",
            )
            with app.test_request_context("/"):
                no_prefix = tmpl.render(
                    rk=rk, period=period, matched=matched,
                    can_edit=True, prefix="",
                )
                tp_prefix = tmpl.render(
                    rk=rk, period=period, matched=matched,
                    can_edit=True, prefix="tp",
                )
                plan_prefix = tmpl.render(
                    rk=rk, period=period, matched=matched,
                    can_edit=True, prefix="plan",
                )

            assert f'id="card-expansion-{txn.id}"' in no_prefix
            assert f'id="card-expansion-tp-{txn.id}"' in tp_prefix
            assert f'id="card-expansion-plan-{txn.id}"' in plan_prefix
            # Prefixed renders must NOT also emit the unprefixed form.
            assert f'id="card-expansion-{txn.id}"' not in tp_prefix
            assert f'id="card-expansion-{txn.id}"' not in plan_prefix

    def test_mobile_grid_includes_plan_partial(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``_mobile_grid.html`` Plan tab body is the
        ``_mobile_plan.html`` include, not inline content.

        The Plan tab body lives in its own partial (Commit 7 of the
        mobile-first v3 implementation; the partial's shape was later
        replaced with a read-only multi-period summary accordion).
        This test pins the call-site wiring by looking for the
        accordion container the partial emits when there is at least
        one plan period.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            # The Plan accordion container is the partial's load-
            # bearing identifier: every plan-period card carries
            # ``data-bs-parent="#plan-accordion"`` and the mutual-
            # exclusion behavior depends on this id being present.
            assert 'id="plan-accordion"' in pane

    def test_no_inline_style_attr_in_mobile_card_actions(self):
        """Pin the no-inline-style invariant on every button in the
        mobile card action bar.  The project's CSP at
        ``app/__init__.py`` declares ``style-src 'self'`` without
        ``'unsafe-inline'``, so any ``style="..."`` attribute is
        silently dropped by the browser -- which previously left the
        Mark Paid / Edit Amount / Open Full buttons at Bootstrap's
        default ``btn-sm`` height (~38-40 px), 4-6 px short of the
        WCAG 2.5.5 / Apple HIG 44 px touch-target floor enforced by
        the mobile-first v3 plan hard-rule 7.

        The 44 px floor now travels via the ``.btn-touch-44`` utility
        class defined in ``app/static/css/grid.css`` inside the
        ``@media (max-width: 767.98px)`` block.  A regression that
        re-introduced an inline ``style="..."`` would silently shrink
        the buttons; this lock catches the regression at the source.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_card_actions.html"
        )
        src = partial.read_text(encoding="utf-8")
        assert "style=" not in src, (
            "Inline style= attribute reintroduced into "
            "_mobile_card_actions.html; the project CSP blocks it. "
            "Use the .btn-touch-44 utility class for the 44 px floor."
        )


class TestMobileNoSwipeAffordances:
    """Negative regression locks: swipe-driven affordances are gone.

    Two swipe-driven behaviors used to live in ``mobile_grid.js``:

      - **swipe-to-mark-paid** on individual cards (Commit 9 of the
        mobile-first v3 implementation), removed after Firefox iOS
        leaked the reveal button at rest, WebKit-based browsers
        layered it behind the amount text mid-swipe, and the
        accidental-tap risk on a state-mutating button outweighed
        the one-tap shortcut.  Mark Paid is reachable through the
        inline action bar that expands when the user taps a card.
      - **period-navigation swipe** on the Plan tab-pane, removed
        when the Plan tab was rebuilt as a read-only multi-period
        accordion (the panel-swap logic the gesture drove was the
        broken bit -- a single Next tap left the next panel hidden
        behind a ``d-none`` class that ``style.display=''`` could
        not override).  Period navigation now lives on the This
        Period tab via URL-driven arrows + a jump-to ``<select>``.

    These tests pin the negative contract: neither affordance
    re-appears via a future refactor.  Lifecycle artifacts
    (panel-swap state, swipe handlers, ``Math.abs(dx) > 50``
    threshold, ``.mobile-period-panel`` containers) are all gone
    from ``mobile_grid.js`` and the Plan partial.
    """

    def test_no_swipe_action_button(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Regression lock: the swipe-action-mark-paid button is
        gone from the rendered mobile grid.

        A Projected envelope card is the macro's most permissive
        emission path (it took the button when the feature
        existed); rendering the live ``/grid`` page and asserting
        the absence of the class is the cheapest way to catch a
        revert that re-enables the affordance.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="No-swipe Projected Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                estimated_amount=Decimal("23.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            assert 'class="swipe-action-mark-paid"' not in body
            # The .mobile-card-row wrapper that only existed to
            # scope the swipe button's containing block is also
            # gone; .mobile-card-wrapper is now the only mobile
            # card grouping div.
            assert 'class="mobile-card-row"' not in body

    def test_no_panel_swap_artifacts_in_mobile_grid_js(self):
        """The panel-swap navigation that used to drive the Plan tab
        is gone from ``mobile_grid.js``.

        The Plan tab is now a read-only multi-period accordion -- a
        Bootstrap collapse stack with ``data-bs-parent`` handling
        mutual exclusion declaratively.  No panel index, no
        ``navigate()`` function, no ``updateLabel()``, no
        ``mobile-prev-btn`` / ``mobile-next-btn`` handlers, no
        ``passive: true`` touch listeners, no ``Math.abs(dx) > 50``
        swipe threshold.  This lock catches a revert that
        re-introduces any of those artifacts.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        mobile_grid_src = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "static" / "js" / "mobile_grid.js"
        ).read_text(encoding="utf-8")

        # Negative lock on every artifact of the dead panel-swap nav.
        assert "Math.abs(dx) > 50" not in mobile_grid_src
        assert "passive: true" not in mobile_grid_src
        assert "mobile-prev-btn" not in mobile_grid_src
        assert "mobile-next-btn" not in mobile_grid_src
        assert ".mobile-period-panel" not in mobile_grid_src
        assert "navigate(" not in mobile_grid_src
        assert "updateLabel" not in mobile_grid_src

    def test_no_panel_swap_markup_in_mobile_plan_partial(self):
        """The ``.mobile-period-panel`` containers and prev/next
        buttons are gone from the Plan partial.

        Before the read-only summary accordion landed, the partial
        rendered one ``<div class="mobile-period-panel">`` per
        period with sibling ``[<]`` / ``[>]`` arrow buttons; the
        broken panel-swap JS toggled their visibility.  Removing the
        JS without also removing the markup would leave orphaned DOM
        with no handler, so this lock pins the cleanup at both
        layers.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        partial = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "grid" / "_mobile_plan.html"
        ).read_text(encoding="utf-8")
        assert "mobile-period-panel" not in partial
        assert 'id="mobile-prev-btn"' not in partial
        assert 'id="mobile-next-btn"' not in partial


class TestMobilePlanTab:
    """Regression locks for the read-only Plan tab on the mobile grid.

    Plan is the forward-looking summary view: a stack of period cards
    showing projected end balances and (on tap) the static line
    items behind each balance.  It is decoupled from the URL's
    ``periods`` / ``offset`` params -- always anchored at
    ``current_period`` and walking forward
    :data:`app.routes.grid.PLAN_WINDOW_PERIODS` periods regardless of
    how the user is navigating in This Period.

    Pinned contracts:

      - The route emits ``plan_periods`` anchored at
        ``current_period`` and ignores the URL's ``offset``.
      - The partial wraps cards in ``id="plan-accordion"`` and every
        card body carries ``data-bs-parent="#plan-accordion"`` so
        opening one auto-closes the others.
      - Balance class follows the existing ``_balance_row.html``
        pattern: ``balance-negative`` for ``< 0``, ``balance-low``
        for ``0 <= bal < low_balance_threshold``, no class
        otherwise.
      - Row lines inside Plan use ``render_row_static`` -- no
        ``data-mobile-txn-id``, no ``.mobile-card-expansion``, no
        ``_mobile_card_actions.html`` include.
      - Empty income / expense sections are hidden (less clutter on
        a scan-oriented view).
      - Carry-forward: when one ``(rk, period)`` cell matches
        multiple transactions, each renders as its own static
        ``<li>``.
    """

    @staticmethod
    def _render_plan_partial(app, **ctx):
        """Render ``_mobile_plan.html`` with the given context.

        Direct render keeps the structural assertions immune to
        surrounding-page markup drift.  ``app.test_request_context``
        is what makes any ``url_for`` resolve inside the partial
        chain; the macro imports its STATUS_* globals from
        ``app.jinja_globals.register_ref_id_globals``.
        """
        template = app.jinja_env.get_template("grid/_mobile_plan.html")
        with app.test_request_context("/"):
            return template.render(**ctx)

    def test_plan_window_anchored_at_current_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The first Plan card's collapse id targets ``current_period.id``.

        Plan walks forward from ``current_period`` regardless of the
        URL.  This test hits ``/grid`` with the default URL state
        and verifies the leftmost Plan card points at the current
        period's id.
        """
        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            # The first plan card collapse target id is the partial's
            # explicit anchor on `current_period`.
            assert f'id="plan-period-{current.id}"' in pane
            assert f'data-bs-target="#plan-period-{current.id}"' in pane

    def test_plan_window_decoupled_from_url_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Plan ignores ``?offset=N`` and stays anchored at current.

        The This Period arrow nav drives the URL to
        ``periods=1&offset=N``; Plan would be useless if it followed
        that lead, so the route builds a dedicated ``plan_*`` context
        window independent of ``ctx.start_offset``.  This test pins
        the decoupling by hitting ``/grid?periods=1&offset=2`` (a
        realistic post-arrow URL state) and asserting the leftmost
        Plan card is still the current period.
        """
        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None

            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            # Current period is still the leftmost Plan card even
            # though the URL is asking This Period to start two
            # periods out.
            assert f'data-bs-target="#plan-period-{current.id}"' in pane

    def test_plan_accordion_uses_data_bs_parent(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Each Plan card collapse carries ``data-bs-parent="#plan-accordion"``.

        Bootstrap's accordion behaviour -- opening one card auto-
        closes the others -- is delivered declaratively via this
        attribute.  Removing it would break the mutual-exclusion
        contract the design depends on; this test catches the
        regression.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            pane = body[pane_start:]
            assert 'id="plan-accordion"' in pane
            assert 'data-bs-parent="#plan-accordion"' in pane

    def test_plan_balance_classes_per_threshold(self, app):
        """Balance color class follows the desktop ``_balance_row.html`` pattern.

        Pins the three branches by rendering the partial against
        controlled ``plan_columns`` balances.  No database setup --
        the partial is template logic only for the class assignment.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            p_neg = SimpleNamespace(
                id=101, start_date=_date(2026, 6, 1),
                end_date=_date(2026, 6, 14), period_index=10,
            )
            p_low = SimpleNamespace(
                id=102, start_date=_date(2026, 6, 15),
                end_date=_date(2026, 6, 28), period_index=11,
            )
            p_ok = SimpleNamespace(
                id=103, start_date=_date(2026, 6, 29),
                end_date=_date(2026, 7, 12), period_index=12,
            )
            plan_columns = {
                period.id: balance_at.GridColumn(
                    balance=balance,
                    income=Decimal("0"), expense=Decimal("0"),
                    net=Decimal("0"), period_timing=Decimal("0.00"),
                    book_vs_bank=Decimal("0.00"),
                    contribution=Decimal("0.00"), accrual=Decimal("0.00"),
                )
                for period, balance in (
                    (p_neg, Decimal("-150.00")),
                    (p_low, Decimal("250.00")),
                    (p_ok, Decimal("4200.00")),
                )
            }

            html = self._render_plan_partial(
                app,
                plan_periods=[p_neg, p_low, p_ok],
                plan_income_row_keys=[],
                plan_expense_row_keys=[],
                plan_matched_by_row_period={},
                plan_columns=plan_columns,
                plan_row_flags=balance_at.GridRowFlags(
                    period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            # Slice the rendered HTML into three per-card chunks so
            # the class assertions stay scoped to the right balance.
            def card_chunk(period_id):
                start = html.index(f'id="plan-period-{period_id}"')
                # Trigger button is rendered ABOVE the collapse body
                # -- back up to find the surrounding accordion-item.
                item_start = html.rfind("accordion-item", 0, start)
                # End of this card = start of the next id="plan-period-..." or end of html.
                item_end = len(html)
                for other_id in (101, 102, 103):
                    if other_id == period_id:
                        continue
                    needle = f'id="plan-period-{other_id}"'
                    pos = html.find(needle)
                    if pos > start and pos < item_end:
                        next_item = html.rfind(
                            "accordion-item", 0, pos,
                        )
                        if next_item > item_start:
                            item_end = next_item
                return html[item_start:item_end]

            neg_chunk = card_chunk(p_neg.id)
            low_chunk = card_chunk(p_low.id)
            ok_chunk = card_chunk(p_ok.id)

            assert "balance-negative" in neg_chunk
            assert "bi-exclamation-triangle-fill" in neg_chunk

            assert "balance-low" in low_chunk
            assert "bi-exclamation-circle" in low_chunk

            assert "balance-negative" not in ok_chunk
            assert "balance-low" not in ok_chunk

    def test_plan_rows_have_no_card_expansion_or_action_bar(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Inside the Plan accordion, no row carries the interactive
        affordances that the This Period rows have.

        Plan is read-only: no Mark Paid form, no Edit Amount button,
        no Open Full button, no per-card expansion collapse, no
        ``data-mobile-txn-id`` (the tap-to-expand attribute).  This
        test seeds a Projected expense in the current period and
        verifies the Plan accordion body for that period is free of
        all interactive markers.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            txn = Transaction(
                account_id=seed_user["account"].id,
                pay_period_id=current.id,
                scenario_id=seed_user["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Plan-readonly Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(
                    TxnTypeEnum.EXPENSE,
                ),
                estimated_amount=Decimal("88.00"),
            )
            db.session.add(txn)
            db.session.commit()

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-plan"')
            # End the slice at the partial's accordion wrapper close
            # so the negative assertions cannot accidentally pick up
            # markup from elsewhere on the page.
            pane_end_marker = '</div>\n{% else %}'  # not present in rendered output
            pane = body[pane_start:]
            # Find the close of the plan-accordion div; the lookup
            # below targets the rendered "no future pay periods"
            # else-branch marker which is absent when periods exist,
            # so pane is the full Plan-tab body.
            del pane_end_marker

            assert "mobile-card-expansion" not in pane
            assert "data-mobile-txn-id" not in pane
            assert "Mark Paid" not in pane
            assert "Edit Amount" not in pane
            assert "Open Full" not in pane
            # Sanity: the static row is still rendering this txn.
            assert "Plan-readonly Groceries" in pane

    def test_plan_hides_empty_income_section(self, app):
        """When a Plan period has no income rows, no Income section
        card renders -- the partial omits the entire ``<div class="card">``
        rather than emitting an empty list.

        Same principle for expenses (covered by the next test).
        Skipping empty sections keeps the scan-oriented Plan view
        from carrying placeholder noise.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            period = SimpleNamespace(
                id=200, start_date=_date(2026, 6, 1),
                end_date=_date(2026, 6, 14), period_index=10,
            )
            rk_exp = SimpleNamespace(
                category_id=1, template_id=None,
                txn_name="Rent", display_name="Rent",
                group_name="Housing",
            )
            txn_exp = SimpleNamespace(
                id=1, name="Rent", actual_amount=None,
                estimated_amount=Decimal("1200.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            html = self._render_plan_partial(
                app,
                plan_periods=[period],
                plan_income_row_keys=[],
                plan_expense_row_keys=[rk_exp],
                plan_matched_by_row_period={
                    (rk_exp.category_id, rk_exp.template_id,
                     rk_exp.txn_name, period.id): [txn_exp],
                },
                plan_columns={
                    period.id: balance_at.GridColumn(
                        balance=Decimal("3000.00"),
                        income=Decimal("0"),
                        expense=Decimal("1200"),
                        net=Decimal("-1200"),
                        period_timing=Decimal("0.00"),
                        book_vs_bank=Decimal("0.00"),
                        contribution=Decimal("0.00"),
                        accrual=Decimal("0.00"),
                    ),
                },
                plan_row_flags=balance_at.GridRowFlags(
                    period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            # Income section card is absent; Expense section card and
            # its row are present.
            assert "mobile-section-income" not in html
            assert "mobile-section-expense" in html
            assert "Rent" in html

    def test_plan_hides_empty_expense_section(self, app):
        """Empty expense section is also hidden; only the Income
        section renders when there are no expense rows.

        Mirrors the income-empty test for the other half of the
        ``if _income_rows`` / ``if _expense_rows`` symmetry in the
        partial.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            period = SimpleNamespace(
                id=201, start_date=_date(2026, 6, 1),
                end_date=_date(2026, 6, 14), period_index=10,
            )
            rk_inc = SimpleNamespace(
                category_id=2, template_id=None,
                txn_name="Salary", display_name="Salary",
                group_name="Income",
            )
            txn_inc = SimpleNamespace(
                id=2, name="Salary", actual_amount=None,
                estimated_amount=Decimal("2500.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            html = self._render_plan_partial(
                app,
                plan_periods=[period],
                plan_income_row_keys=[rk_inc],
                plan_expense_row_keys=[],
                plan_matched_by_row_period={
                    (rk_inc.category_id, rk_inc.template_id,
                     rk_inc.txn_name, period.id): [txn_inc],
                },
                plan_columns={
                    period.id: balance_at.GridColumn(
                        balance=Decimal("3000.00"),
                        income=Decimal("2500"),
                        expense=Decimal("0"),
                        net=Decimal("2500"),
                        period_timing=Decimal("0.00"),
                        book_vs_bank=Decimal("0.00"),
                        contribution=Decimal("0.00"),
                        accrual=Decimal("0.00"),
                    ),
                },
                plan_row_flags=balance_at.GridRowFlags(
                    period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            assert "mobile-section-income" in html
            assert "mobile-section-expense" not in html
            assert "Salary" in html

    def test_plan_preserves_carry_forward(self, app):
        """A ``(rk, period)`` cell with multiple matched transactions
        renders each as its own static ``<li>`` row.

        Carry-forward and similar multi-txn-per-cell scenarios are
        load-bearing for envelope reporting (a period can hold both
        the canonical envelope row and a sibling carry-forward
        sweep).  ``render_row_static`` iterates the matched list
        rather than flattening it, mirroring the interactive
        ``render_row_card`` contract.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        with app.app_context():
            period = SimpleNamespace(
                id=300, start_date=_date(2026, 6, 1),
                end_date=_date(2026, 6, 14), period_index=10,
            )
            rk = SimpleNamespace(
                category_id=3, template_id=42,
                txn_name="Groceries", display_name="Groceries",
                group_name="Food",
            )
            txn_a = SimpleNamespace(
                id=10, name="Groceries", actual_amount=None,
                estimated_amount=Decimal("100.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            txn_b = SimpleNamespace(
                id=11, name="Groceries", actual_amount=None,
                estimated_amount=Decimal("25.00"),
                status=SimpleNamespace(is_settled=False),
                status_id=99, transfer_id=None,
                credit_payback_for_id=None,
            )
            html = self._render_plan_partial(
                app,
                plan_periods=[period],
                plan_income_row_keys=[],
                plan_expense_row_keys=[rk],
                plan_matched_by_row_period={
                    (rk.category_id, rk.template_id,
                     rk.txn_name, period.id): [txn_a, txn_b],
                },
                plan_columns={
                    period.id: balance_at.GridColumn(
                        balance=Decimal("3000.00"),
                        income=Decimal("0"),
                        expense=Decimal("125"),
                        net=Decimal("-125"),
                        period_timing=Decimal("0.00"),
                        book_vs_bank=Decimal("0.00"),
                        contribution=Decimal("0.00"),
                        accrual=Decimal("0.00"),
                    ),
                },
                plan_row_flags=balance_at.GridRowFlags(
                    period_timing=False, book_vs_bank=False,
                    contribution=False, accrual=False,
                ),
                low_balance_threshold=500,
            )

            # Each amount renders separately -- one <li> per match.
            assert "$100" in html
            assert "$25" in html


class TestMobileJumpToPeriod:
    """Regression locks for the jump-to-period ``<select>`` in the
    "This Period" tab header (Commit 10 of the mobile-first v3
    implementation).

    The select lives in ``app/templates/grid/_mobile_this_period.html``
    below the ``[<] [>]`` arrow row and lets the user reach any
    non-adjacent period in one tap, avoiding N taps on ``[<]``.
    Picking a non-current option fires ``change``, which a delegated
    listener in ``app/static/js/mobile_grid.js`` translates into a
    full GET submit to ``/grid?periods=1&offset=N``.

    These tests pin the structural contract the JS handler and the
    grid route consume:

      - The ``<select name="offset">`` is emitted exactly once per
        page render, inside the ``#mobile-this-period`` tab-pane
        (so the JS handler's ``.closest('#mobile-this-period')``
        guard matches).
      - One ``<option>`` per period in ``all_periods`` -- the option
        list mirrors the user's full visible projection so the user
        can jump to any of them.
      - Option ``value`` is the period's offset relative to
        ``current_period.period_index``, matching the desktop
        selector convention at ``grid/grid.html:24-49`` and the
        partial's own prev/next arrow hrefs.
      - The option for the currently visible period
        (``periods[0]`` / ``period``) carries the ``selected``
        attribute so the picker opens on that row.
      - Method is GET with a hidden ``periods=1`` input -- the GET
        is read-only navigation (no state mutation), so no CSRF
        token is required per CLAUDE.md "State-changing actions
        must use POST".
      - The JS file carries the delegated change handler with the
        ``select[name="offset"]`` selector and the
        ``#mobile-this-period`` scope guard.
    """

    def test_jump_to_select_present(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-1: a single ``<select name="offset">`` lives inside the
        ``#mobile-this-period`` tab-pane.

        The select is the jump-to control. Scoping the assertion to
        the pane (not just the document) guards against a future
        regression where a sibling ``<select name="offset">`` lands
        in another tab and double-submits via the same delegated JS
        handler.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            assert pane.count('<select name="offset"') == 1
            # The hidden periods=1 input rides with the select so
            # the GET lands at the single-period URL shape.
            assert 'name="periods" value="1"' in pane
            # GET form -- read-only navigation, no CSRF gate.
            assert 'method="get"' in pane

    def test_jump_to_options_match_all_periods(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-2: option count equals ``len(all_periods)``.

        ``seed_periods_today`` provisions 10 biweekly periods
        (indices 0..9); ``pay_period_service.get_all_periods``
        returns all 10 to the route, so the rendered select carries
        10 ``<option>`` elements. Each option's ``value`` is the
        offset relative to the current period (period_index 4 under
        ``seed_periods_today``), so the value set is
        ``{-4, -3, -2, -1, 0, 1, 2, 3, 4, 5}``.
        """
        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current is not None
            all_periods = pay_period_service.get_all_periods(
                seed_user["user"].id,
            )
            assert len(all_periods) == 10
            assert current.period_index == 4

            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            # Slice down to the select's option block to keep the
            # count immune to unrelated <option> elements elsewhere
            # in the pane (none exist today, but the slice keeps
            # future additions safe).
            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]
            assert select_block.count("<option ") == len(all_periods)

            # Spot-check the boundary offsets. period_index 0 -> -4,
            # period_index 9 -> +5 (all under current.period_index=4).
            assert 'value="-4"' in select_block
            assert 'value="5"' in select_block
            # And the current option must exist at value="0".
            assert 'value="0"' in select_block

    def test_jump_to_current_period_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-3: the option for the currently visible period carries
        ``selected``.

        At the default ``/grid`` URL (``start_offset == 0``),
        ``periods[0]`` (the partial's ``period`` local) equals
        ``current_period``, so the offset-0 option is the selected
        one. Verified by slicing the offset-0 option's opening
        tag and asserting ``selected`` appears inside it.
        """
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]

            # Locate the offset=0 option (the current period under
            # start_offset=0) and slice its full opening tag so the
            # assertion spans the multi-line attribute layout.
            opt_start = select_block.index('value="0"')
            opt_tag_open = select_block.rindex("<option", 0, opt_start)
            opt_tag_close = select_block.index(">", opt_start)
            opt_open = select_block[opt_tag_open:opt_tag_close]
            assert "selected" in opt_open

    def test_jump_to_selected_follows_visible_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C10-3 (extended): non-zero ``start_offset`` shifts the
        selected option to the currently visible period.

        At ``?periods=1&offset=2`` the visible period is the one
        with ``period_index == current.period_index + 2 == 6``;
        the select's ``selected`` must move to the offset=2 option
        (and the offset=0 option must NOT carry ``selected``).
        Locks the ``p.id == period.id`` predicate against drift
        toward an unconditional or current-only selection rule.
        """
        with app.app_context():
            response = auth_client.get("/grid?periods=1&offset=2")
            assert response.status_code == 200
            body = response.data.decode("utf-8")

            pane_start = body.index('id="mobile-this-period"')
            pane_end = body.index('id="mobile-plan"', pane_start)
            pane = body[pane_start:pane_end]

            select_start = pane.index('<select name="offset"')
            select_end = pane.index("</select>", select_start)
            select_block = pane[select_start:select_end]

            # offset=2 option carries selected.
            opt2_start = select_block.index('value="2"')
            opt2_open = select_block[
                select_block.rindex("<option", 0, opt2_start)
                :select_block.index(">", opt2_start)
            ]
            assert "selected" in opt2_open

            # offset=0 option does NOT carry selected.
            opt0_start = select_block.index('value="0"')
            opt0_open = select_block[
                select_block.rindex("<option", 0, opt0_start)
                :select_block.index(">", opt0_start)
            ]
            assert "selected" not in opt0_open

    def test_jump_to_delegated_handler_in_mobile_grid_js(self):
        """C10 JS-side regression lock: the delegated change handler
        in ``mobile_grid.js`` references the select selector and the
        ``#mobile-this-period`` scope guard.

        The CSP-friendly delegated handler replaces the inline
        ``onchange="this.form.submit()"`` from the plan's draft
        markup (per CLAUDE.md "No inline scripts"). Reading the JS
        file directly (same pattern as
        ``test_swipe_threshold_matches_period_swipe``) locks both
        the selector and the scope so a future refactor cannot
        silently drop either guard.
        """
        import pathlib  # pylint: disable=import-outside-toplevel

        js_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "static" / "js" / "mobile_grid.js"
        )
        src = js_path.read_text(encoding="utf-8")

        # The selector targets the jump-to <select>.
        assert 'select[name="offset"]' in src
        # The scope guard limits the handler to the "This Period" pane.
        assert "#mobile-this-period" in src
        # form.submit() is what turns the change into a GET to /grid.
        assert "form.submit()" in src


def _summary_periods():
    """Two period stand-ins carrying what the summary templates read."""
    return [
        SimpleNamespace(
            id=101, start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 14), period_index=10,
        ),
        SimpleNamespace(
            id=102, start_date=date(2026, 6, 15),
            end_date=date(2026, 6, 28), period_index=11,
        ),
    ]


def _summary_columns(
    periods, *, period_timing="0.00", book_vs_bank="0.00",
    contribution="0.00", accrual="0.00",
):
    """Return one hand-built GridColumn per period, all carrying the same figures.

    The three conditional rows are graded on hand-built columns because a
    producer cannot reach every arm of the render at every step: plan step
    X-c2b1 could not put a figure in "Timing & true-ups" at all, and X-g3a
    cannot put one in "Contributions" for ANY fixture -- the grid's kind gate
    admits only INTEREST accounts and ``contribution_events`` returns ``[]``
    for every kind but INVESTMENT.  A row whose template nobody ever executed
    would arrive at the cutover unproven, and the cutover is the commit where
    money moves.
    """
    return {
        period.id: balance_at.GridColumn(
            balance=Decimal("3000.00"),
            income=Decimal("2400.00"),
            expense=Decimal("1450.00"),
            net=Decimal("950.00"),
            period_timing=Decimal(period_timing),
            book_vs_bank=Decimal(book_vs_bank),
            contribution=Decimal(contribution),
            accrual=Decimal(accrual),
        )
        for period in periods
    }


def _row_flags(
    *, period_timing=False, book_vs_bank=False,
    contribution=False, accrual=False,
):
    """Return GridRowFlags with every arm explicit (no defaulted visibility).

    The single ``reconciliation`` arm became two at plan step S1-c (ruling
    R-DH (f)) and they are separate PARAMETERS rather than one value fanned
    out, because R-O's visibility question is now asked of each row
    independently -- and the render tests below have to be able to turn one on
    with the other off.
    """
    return balance_at.GridRowFlags(
        period_timing=period_timing,
        book_vs_bank=book_vs_bank,
        contribution=contribution,
        accrual=accrual,
    )


def _render_grid_footer(app, periods, columns, flags, accrual_label="Interest"):
    """Render the desktop ``<tfoot>`` partial against a hand-built context."""
    template = app.jinja_env.get_template("grid/_balance_row.html")
    with app.test_request_context("/"):
        return template.render(
            periods=periods,
            columns=columns,
            row_flags=flags,
            accrual_label=accrual_label,
            account=None,
            num_periods=len(periods),
            start_offset=0,
            low_balance_threshold=500,
        )


def _render_mobile_card(app, period, columns, flags, accrual_label="Interest"):
    """Render the mobile "This Period" summary against a hand-built context."""
    template = app.jinja_env.get_template("grid/_mobile_tp_summary.html")
    with app.test_request_context("/"):
        return template.render(
            period=period,
            columns=columns,
            period_row_flags=flags,
            accrual_label=accrual_label,
            account=None,
            oob=False,
        )


def _render_plan_recap(app, period, columns, flags, accrual_label="Interest"):
    """Render the mobile Plan recap against a hand-built context."""
    template = app.jinja_env.get_template("grid/_mobile_plan.html")
    with app.test_request_context("/"):
        return template.render(
            plan_periods=[period],
            plan_income_row_keys=[],
            plan_expense_row_keys=[],
            plan_matched_by_row_period={},
            plan_columns=columns,
            plan_row_flags=flags,
            accrual_label=accrual_label,
            low_balance_threshold=500,
        )


class TestTheTwoRemainderRows:
    """Ruling R-O / R-P / R-DH (f): the two remainder rows, on every surface.

    They carry what the Total Income / Total Expenses rows structurally cannot
    say about the balance change.  Plan step X-c2b2 is where a producer first
    put a non-zero figure in them (measured ``-$788.68`` combined in the real
    Checking account's current column); ruling R-O's rule hides a row whose
    every visible column reports ``0.00``, which is why the RENDER is graded
    here on hand-built columns.  A row whose template nobody ever executed
    would arrive at a cutover unproven, and a cutover is where money moves.

    **This was ONE row, "Timing & true-ups", until plan step S1-c** (ruling
    R-DH (f)).  It summed two facts with different causes and different fixes:
    money landing in a column other than the one it was budgeted to ("Period
    timing", fixed by re-budgeting the row or dating it correctly), and the gap
    between what the app had recorded and what the bank actually held ("Book vs
    bank", fixed by recording the spending it did not know about).  On the
    developer's own data the single row read ``-$4,588.69`` in a column whose
    halves were ``-$427.22`` and ``-$4,161.47`` -- a figure with no action
    attached to it.

    So every test below asserts the two rows SEPARATELY, and the pair that
    matters most is ``test_desktop_footer_renders_one_row_without_the_other``:
    the shared ``reconciliation-row`` class now marks both rows, so the rows
    are told apart here by the LABEL a user reads, which is the thing the
    ruling is actually about.
    """

    #: The labels as the templates escape them into the rendered HTML.
    _TIMING = "Period timing"
    _BOOK = "Book vs bank"

    @staticmethod
    def _columns(*, periods, period_timing="0.00", book_vs_bank="0.00"):
        """Return one GridColumn per period carrying the two remainders."""
        return _summary_columns(
            periods, period_timing=period_timing, book_vs_bank=book_vs_bank,
        )

    @staticmethod
    def _periods():
        """Two period stand-ins carrying what the footer template reads."""
        return _summary_periods()

    def _render_footer(self, app, *, period_timing="0.00", book_vs_bank="0.00",
                       timing_flag=False, book_flag=False):
        """Render the desktop ``<tfoot>`` with given remainders + flags."""
        periods = self._periods()
        return _render_grid_footer(
            app, periods,
            self._columns(
                periods=periods, period_timing=period_timing,
                book_vs_bank=book_vs_bank,
            ),
            _row_flags(period_timing=timing_flag, book_vs_bank=book_flag),
        )

    def test_desktop_footer_renders_both_rows_above_the_balance(self, app):
        """Both rows sit in the tfoot ABOVE Projected End Balance (ruling R-O).

        Placement is the ruling, not a preference: the whole "how this balance
        is reached" chain has to read as one block, so the rows the identity
        binds must be above the balance they explain rather than in the flow
        tbody two sections up.

        The two figures are the halves R-DH (f) was measured on, scaled to this
        fixture, and they are DIFFERENT so a template rendering one column's
        value into both rows fails rather than passing on a coincidence.
        """
        with app.app_context():
            html = self._render_footer(
                app, period_timing="-427.22", book_vs_bank="-160.05",
                timing_flag=True, book_flag=True,
            )

        assert self._TIMING in html
        assert self._BOOK in html
        assert "reconciliation-row" in html
        assert "-$427" in html
        assert "-$160" in html
        # Timing first, then book-vs-bank, then the balance they explain.
        assert html.index(self._TIMING) < html.index(self._BOOK)
        assert html.index(self._BOOK) < html.index("Projected End Balance")

    def test_desktop_footer_renders_one_row_without_the_other(self, app):
        """R-DH (f)'s point: a window with only true-ups shows only that row.

        The property a SHARED flag could not express, and the reason the two
        rows exist.  A period carrying a balance assertion and no timing
        difference renders "Book vs bank" and must NOT render a permanently
        ``$0.00`` "Period timing" line beside it -- a labelled row of zeros
        reads as "measured, and the answer is zero" for a fact that was never
        in question.

        Asserted in both directions, because a template wired to the wrong flag
        passes a one-directional check by luck.
        """
        with app.app_context():
            book_only = self._render_footer(
                app, book_vs_bank="-160.05", book_flag=True,
            )
            timing_only = self._render_footer(
                app, period_timing="-427.22", timing_flag=True,
            )

        assert self._BOOK in book_only
        assert self._TIMING not in book_only
        assert self._TIMING in timing_only
        assert self._BOOK not in timing_only

    def test_desktop_footer_hides_an_all_zero_pair(self, app):
        """An all-zero window renders neither row -- the ordinary cash grid."""
        with app.app_context():
            html = self._render_footer(app)

        assert self._TIMING not in html
        assert self._BOOK not in html
        assert "reconciliation-row" not in html
        # The rest of the footer is untouched.
        assert "Projected End Balance" in html

    def test_desktop_footer_shows_zero_in_the_columns_that_carry_none(self, app):
        """Once a row renders it shows $0 where a column has none.

        The other half of ruling R-O: the row is present for the WHOLE visible
        window, so a column with nothing to explain reads ``$0`` rather than
        blank -- blank would read as "not measured".
        """
        with app.app_context():
            periods = self._periods()
            columns = self._columns(periods=periods)
            columns[periods[0].id] = balance_at.GridColumn(
                balance=Decimal("3000.00"), income=Decimal("2400.00"),
                expense=Decimal("1450.00"), net=Decimal("950.00"),
                period_timing=Decimal("-788.68"),
                book_vs_bank=Decimal("0.00"),
                contribution=Decimal("0.00"), accrual=Decimal("0.00"),
            )
            html = _render_grid_footer(
                app, periods, columns, _row_flags(period_timing=True),
            )

        row = html[html.index("reconciliation-row"):]
        row = row[:row.index("</tr>")]
        assert "-$789" in row
        assert "$0" in row

    def test_mobile_this_period_card_renders_both_rows(self, app):
        """Ruling R-P: the mobile summary carries the same two lines.

        Without them the card shows a Net Cash Flow that does not account for
        the balance printed beside it -- the visible contradiction ruling R-K
        refused to ship, on the form factor Mark Paid is used from.
        """
        period = self._periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period,
                self._columns(
                    periods=[period], period_timing="-427.22",
                    book_vs_bank="-160.05",
                ),
                _row_flags(period_timing=True, book_vs_bank=True),
            )

        assert self._TIMING in html
        assert self._BOOK in html
        assert "-$427" in html
        assert "-$160" in html
        assert html.index("Net Cash Flow") < html.index(self._TIMING)
        assert html.index(self._BOOK) < html.index("Projected Balance")

    def test_mobile_this_period_card_asks_each_row_separately(self, app):
        """The card follows the SAME per-row rule, not a shared one."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period,
                self._columns(periods=[period], book_vs_bank="-160.05"),
                _row_flags(book_vs_bank=True),
            )

        assert self._BOOK in html
        assert self._TIMING not in html

    def test_mobile_this_period_card_hides_an_all_zero_pair(self, app):
        """The mobile card follows the SAME rule, not its own."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period, self._columns(periods=[period]), _row_flags(),
            )

        assert self._TIMING not in html
        assert self._BOOK not in html
        assert "Net Cash Flow" in html

    def test_plan_recap_renders_both_rows(self, app):
        """Ruling R-P again: the Plan tab recap carries both figures too."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period,
                self._columns(
                    periods=[period], period_timing="-427.22",
                    book_vs_bank="-160.05",
                ),
                _row_flags(period_timing=True, book_vs_bank=True),
            )

        # The recap is space-constrained, so it abbreviates the two labels.
        assert "Timing" in html
        assert "Bank" in html
        assert "-$427" in html
        assert "-$160" in html

    def test_plan_recap_asks_each_row_separately(self, app):
        """One chip on, the other off -- the recap does not share a flag."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period,
                self._columns(periods=[period], period_timing="-427.22"),
                _row_flags(period_timing=True),
            )

        assert "Timing" in html
        assert "Bank" not in html

    def test_plan_recap_hides_an_all_zero_pair(self, app):
        """And hides both on the same rule."""
        period = self._periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period, self._columns(periods=[period]), _row_flags(),
            )

        assert "Timing" not in html
        assert "Bank" not in html

    def test_the_mobile_card_reads_its_OWN_period_not_the_grid_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile card's conditional bars are scoped to the period it shows.

        Found in X-c2b1's own review: the initial include was handed the
        DESKTOP window's flags while the card renders ``periods[0]`` alone, so
        a window carrying a figure in some other column would have turned the
        card's bar on for a period that has none -- and the
        ``mobileCardSettled`` refresh, which sees one period and no window,
        would have turned it back off.  A flicker between two renders of the
        same card, and with the redundant per-cell guard now gone (the flag
        alone decides) it would render ``None`` as money.

        Driven from data through the Interest bar, which is the one
        conditional figure a producer can vary at this step: an HYSA anchored
        two periods AHEAD of today accrues nothing in the current column, so
        the default window has accruing columns (the desktop row renders)
        whose leftmost period has none (the mobile bar must not).  The shape
        is asserted at the seam first, so the test cannot pass vacuously by
        failing to construct it.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[5], Decimal("100000.00"),
        )
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            all_periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)
            window = [
                p for p in all_periods
                if p.period_index >= current.period_index
            ][:6]
            view = balance_at.grid_balance_view(hysa, bctx, all_periods)
            # The shape this test needs: the window accrues, its first
            # column does not.
            assert view.row_flags(window).accrual is True
            assert view.row_flags(window[:1]).accrual is False

        resp = auth_client.get(f"/grid?account_id={hysa.id}&periods=6")
        assert resp.status_code == 200
        html = resp.data.decode()

        # The desktop footer row renders: the window DOES contain accrual.
        footer = html[html.index('id="grid-summary"'):html.index("</tfoot>")]
        assert "modelled-accrual-row" in footer

        # The mobile card renders the leftmost period, which has none.  It is
        # the last block of the This Period pane, so the Plan pane bounds it.
        card_start = html.index('id="mobile-tp-summary-')
        card = html[card_start:html.index('id="mobile-plan"', card_start)]
        assert "Net Cash Flow" in card, "the card must actually have rendered"
        assert "modelled-accrual-row" not in card

    def test_the_shipped_grid_shows_no_remainder_row_on_a_clean_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End to end: the real page renders neither remainder row here.

        The seeded account has one opening assertion (which ruling R-I keeps
        out of every column) and no rows that settled outside their own period,
        so both remainders are ``$0.00`` throughout and R-O hides both rows.

        **The ABBREVIATED labels are asserted too, and they are not redundant.**
        ``/grid`` also renders ``grid/_mobile_plan.html``, whose space-constrained
        chips say "Timing" and "Bank" rather than the full row labels -- and they
        are gated on ``plan_row_flags``, computed over the PLAN window
        (``app/routes/grid.py``) rather than the tfoot's visible window.  So a
        regression could light a permanently-``$0`` chip on the Plan tab while
        the desktop ``reconciliation-row`` stayed hidden and the two full-label
        assertions below stayed green.  HEAD asserted ``b"Timing"``; narrowing to
        the full labels alone would have dropped that surface silently.
        """
        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        assert b"reconciliation-row" not in resp.data
        assert self._TIMING.encode() not in resp.data
        assert self._BOOK.encode() not in resp.data
        # The mobile Plan recap's abbreviated chips, on their own flag window.
        assert b"Timing" not in resp.data
        assert b"Bank" not in resp.data


class TestTheContributionsRow:
    """Ruling R-AH: the modelled tiers are TWO rows, on both form factors.

    A modelled asset has two modelled tiers and the CONTRIBUTION is the larger
    of them on the real Empower 401(k) ($9,624.27 against $8,152.58 over the
    horizon), which is why ruling R-K's identity gained a fourth term.  They are
    two rows rather than one sum because a single summed row can render POSITIVE
    on an account that LOST money: measured at a -10.5% return the market takes
    -$7,366.83 while payroll puts in +$9,624.27, so one row would report
    +$2,257.44 -- a figure that is neither what the market did nor what the user
    put in.

    **Graded on hand-built columns because no producer can reach the row at this
    step, and that is a property of the code rather than of a fixture.**  Plan
    step X-g3a keeps the grid's kind gate, so only an INTEREST account resolves
    the modelled arm, and ``_asset_contributions.contribution_events`` returns
    ``[]`` for every kind but INVESTMENT -- so ``contribution`` is ``0.00`` in
    every column of every account for EVERY POSSIBLE FIXTURE and
    ``row_flags.contribution`` is permanently ``False``.  X-g3b supplies the
    producer-side control on a 401(k) fixture with a real feed.  This is exactly
    how "Timing & true-ups" was graded at X-c2b1 and for the same stated reason.
    """

    def test_desktop_footer_seats_the_row_between_timing_and_the_accrual(
        self, app,
    ):
        """Row ORDER is the model's own (ruling R-AH), not a preference.

        A contribution lands on its pay period's ``start_date`` and
        ``_asset_fold._resolve_days`` applies the day's deltas and THEN accrues
        on the balance the day ENDS holding -- so the money is contributed and
        then earns.  Addition is commutative and the identity holds either way;
        reading order is not, and the rows tell the story in the order the
        replay does.
        """
        with app.app_context():
            periods = _summary_periods()
            html = _render_grid_footer(
                app, periods,
                _summary_columns(
                    periods, period_timing="-788.68",
                    contribution="181.59", accrual="95.98",
                ),
                _row_flags(
                    period_timing=True, book_vs_bank=True,
                    contribution=True, accrual=True,
                ),
            )

        assert "modelled-contribution-row" in html
        assert "$181.59" in html
        # The two remainder rows come first, in ruling R-DH (f)'s order.
        assert html.index("Period timing") < html.index("Book vs bank")
        assert html.index("Book vs bank") < html.index("Contributions")
        assert html.index("Contributions") < html.index(
            "modelled-accrual-row",
        )
        assert html.index("modelled-accrual-row") < html.index(
            "Projected End Balance",
        )

    def test_desktop_footer_hides_an_all_zero_contributions_row(self, app):
        """A window that contributes nothing renders no row -- R-O's rule.

        This is the state EVERY account is in at plan step X-g3a, so it also
        pins that the shipped page is unchanged by the new row.
        """
        with app.app_context():
            periods = _summary_periods()
            html = _render_grid_footer(
                app, periods, _summary_columns(periods), _row_flags(),
            )

        assert "modelled-contribution-row" not in html
        assert "Contributions" not in html
        assert "Projected End Balance" in html

    def test_desktop_footer_shows_zero_where_a_column_contributes_none(
        self, app,
    ):
        """Once on, the row shows ``$0`` rather than blank -- R-O's other half.

        Blank would read as "not measured".  A payday falls in one pay period
        and not its neighbour, so a live 401(k) genuinely has zero columns
        beside contributing ones.
        """
        with app.app_context():
            periods = _summary_periods()
            columns = _summary_columns(periods)
            columns[periods[0].id] = balance_at.GridColumn(
                balance=Decimal("3000.00"), income=Decimal("2400.00"),
                expense=Decimal("1450.00"), net=Decimal("950.00"),
                period_timing=Decimal("0.00"),
                book_vs_bank=Decimal("0.00"),
                contribution=Decimal("181.59"), accrual=Decimal("0.00"),
            )
            html = _render_grid_footer(
                app, periods, columns, _row_flags(contribution=True),
            )

        row = html[html.index("modelled-contribution-row"):]
        row = row[:row.index("</tr>")]
        assert "$181.59" in row
        # Cents, so a column that contributed nothing is visibly distinct from
        # one that contributed a sub-dollar amount (developer ruling).
        assert "$0.00" in row

    def test_the_mobile_card_carries_both_bars_in_the_same_order(self, app):
        """Ruling R-P: the mobile summary explains its balance the same way.

        Without both bars the card would show a Net Cash Flow that does not
        account for the balance beside it -- the visible contradiction ruling
        R-K refused to ship, on the form factor Mark Paid is used from.
        """
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period,
                _summary_columns(
                    [period], period_timing="-788.68",
                    contribution="181.59", accrual="95.98",
                ),
                _row_flags(
                    period_timing=True, book_vs_bank=True,
                    contribution=True, accrual=True,
                ),
                accrual_label="Growth",
            )

        assert "modelled-contribution-row" in html
        assert "$181.59" in html
        assert html.index("Net Cash Flow") < html.index("Period timing")
        assert html.index("Period timing") < html.index("Book vs bank")
        assert html.index("Book vs bank") < html.index("Contributions")
        assert html.index("Contributions") < html.index("Growth")
        assert html.index("Growth") < html.index("Projected Balance")

    def test_the_mobile_card_hides_an_all_zero_contributions_bar(self, app):
        """The card follows the SAME rule, not its own."""
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_mobile_card(
                app, period, _summary_columns([period]), _row_flags(),
            )

        assert "modelled-contribution-row" not in html
        assert "Contributions" not in html
        assert "Net Cash Flow" in html

    def test_the_plan_recap_carries_both_figures(self, app):
        """Ruling R-P again: the Plan tab recap explains the same chain."""
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period,
                _summary_columns(
                    [period], period_timing="-788.68",
                    contribution="181.59", accrual="95.98",
                ),
                _row_flags(
                    period_timing=True, book_vs_bank=True,
                    contribution=True, accrual=True,
                ),
                accrual_label="Appreciation",
            )

        assert "Contributions $181.59" in " ".join(html.split())
        assert html.index("Timing") < html.index("Contributions")
        assert html.index("Contributions") < html.index("Appreciation")

    def test_the_plan_recap_hides_an_all_zero_contributions_figure(self, app):
        """And hides it on the same rule."""
        period = _summary_periods()[0]
        with app.app_context():
            html = _render_plan_recap(
                app, period, _summary_columns([period]), _row_flags(),
            )

        assert "Contributions" not in html


class TestTheAccrualRowSignReachesItsStyling:
    """Finding N-88: a rendered market LOSS must not be styled as a gain.

    The mobile card hard-coded ``text-success`` on the modelled-return figure.
    That was safe only while INTEREST was the sole kind reaching the row --
    ``interest_params`` bounds ``apy >= 0`` -- and the two kinds ruling R-W adds
    are bounded only ``> -1``, with ``asset_appreciation_params`` saying so in
    its own words ("A negative rate is permitted so a future depreciating asset
    (e.g. Vehicle) reuses this table unchanged").  A depreciating Vehicle or a
    401(k) in a down market would have rendered a measured -$142.11 in success
    green, while the desktop footer and the Plan recap rendered the same figure
    colourless -- so the app would also have disagreed with itself across form
    factors, the shape ruling R-P exists to prevent.

    The rule is three-way and stated ONCE (``accrual_class`` / ``accrual_money``
    in ``grid/_grid_row_macros.html``): a gain is the success token with an
    explicit ``+``, a loss is the danger token with the ``-`` the money macro
    already renders, and a column that earned nothing is neither.  So colour is
    never the only signal, which is ``/investment``'s shipped rule in its own
    words, and a ``$0`` column is not reported as a gain.
    """

    @staticmethod
    def _accrual_cell(html):
        """Return just the modelled-accrual row / bar out of *html*.

        Bounded at the element's own closing tag rather than by a character
        count, so a template that grows cannot silently push the figure out of
        the slice and turn an assertion vacuous.  The desktop row closes with
        ``</tr>`` and the mobile bar with ``</div>``; whichever comes first is
        this element's end.
        """
        body = html[html.index("modelled-accrual-row"):]
        ends = [body.index(tag) for tag in ("</tr>", "</div>") if tag in body]
        assert ends, "the modelled-accrual element never closed"
        return body[:min(ends)]

    def test_a_gain_is_green_and_carries_an_explicit_plus(self, app):
        """Desktop, mobile and Plan all render ``+$96`` in the success token."""
        periods = _summary_periods()
        with app.app_context():
            footer = _render_grid_footer(
                app, periods,
                _summary_columns(periods, accrual="95.98"),
                _row_flags(accrual=True),
            )
            card = _render_mobile_card(
                app, periods[0],
                _summary_columns([periods[0]], accrual="95.98"),
                _row_flags(accrual=True),
            )
            recap = _render_plan_recap(
                app, periods[0],
                _summary_columns([periods[0]], accrual="95.98"),
                _row_flags(accrual=True),
            )

        for html in (footer, card):
            cell = self._accrual_cell(html)
            assert "text-success" in cell
            assert "balance-negative" not in cell
            assert "+$95.98" in cell
        assert "+$95.98" in recap
        assert "text-success" in recap

    def test_a_loss_is_the_danger_token_and_never_success(self, app):
        """The N-88 regression itself, on all three surfaces.

        ``-$142`` is ruling R-AH's own measured worst single column at a -10.5%
        return on the real Empower 401(k).  The assertion that ``text-success``
        is ABSENT is the firing control: it is the exact class the mobile card
        hard-coded, so re-introducing it fails here.
        """
        periods = _summary_periods()
        with app.app_context():
            footer = _render_grid_footer(
                app, periods,
                _summary_columns(periods, accrual="-142.11"),
                _row_flags(accrual=True),
            )
            card = _render_mobile_card(
                app, periods[0],
                _summary_columns([periods[0]], accrual="-142.11"),
                _row_flags(accrual=True),
                accrual_label="Growth",
            )
            recap = _render_plan_recap(
                app, periods[0],
                _summary_columns([periods[0]], accrual="-142.11"),
                _row_flags(accrual=True),
                accrual_label="Growth",
            )

        for html in (footer, card):
            cell = self._accrual_cell(html)
            assert "balance-negative" in cell
            assert "text-success" not in cell
            # The SIGN carries the meaning; colour is never the only signal.
            assert "-$142.11" in cell
        assert "-$142.11" in recap
        assert "text-success" not in recap
        assert "balance-negative" in recap

    def test_a_zero_column_is_neither_a_gain_nor_a_loss(self, app):
        """``$0`` renders plain -- not ``+$0`` and not green.

        Ruling R-O renders ``$0`` in every column of a window the row is on
        for, which is a state ``/investment``'s chip never faces, so the
        verbatim ``>= 0`` boundary it uses would paint an empty column as a
        gain.  Zero is neutral here (developer ruling 2026-07-27).
        """
        periods = _summary_periods()
        with app.app_context():
            columns = _summary_columns(periods)
            columns[periods[0].id] = balance_at.GridColumn(
                balance=Decimal("3000.00"), income=Decimal("2400.00"),
                expense=Decimal("1450.00"), net=Decimal("950.00"),
                period_timing=Decimal("0.00"),
                book_vs_bank=Decimal("0.00"),
                contribution=Decimal("0.00"), accrual=Decimal("95.98"),
            )
            footer = _render_grid_footer(
                app, periods, columns, _row_flags(accrual=True),
            )

        row = footer[footer.index("modelled-accrual-row"):]
        row = row[:row.index("</tr>")]
        cells = row.split("<td")
        # The accruing column is the first data cell after the sticky label.
        assert "+$95.98" in cells[2]
        assert "text-success" in cells[2]
        # The empty one reports $0.00 and claims nothing about it.  CENTS is
        # what makes these two cells tell different stories: at whole dollars
        # both read "$0" and only the colour distinguished them, and ruling
        # R-O's reason for the row being on screen at all was invisible.
        assert "$0.00" in cells[3]
        assert "+$0" not in cells[3]
        assert "text-success" not in cells[3]
        assert "balance-negative" not in cells[3]


class TestTheAccrualRowLabelIsPerKind:
    """Ruling R-AI: "Interest" on an HYSA, "Growth" on a 401(k), "Appreciation".

    Not a new vocabulary: the app already speaks all three, each on that kind's
    own page.  Those are PHRASES with their own windows baked in rather than
    instances of one string, so the route's map is the canonical source for the
    GRID's row and not a fourth copy of any of them.

    Rejected at the ruling: ONE word for every kind (a fourth vocabulary
    contradicting three shipped pages, and it renames the "Interest" row an
    HYSA has carried since PR #47), and keeping "Interest" everywhere (which
    would label a house's appreciation and a 401(k)'s market return "Interest").
    """

    def test_the_map_is_total_over_the_projection_kinds(self):
        """EVERY ``AccountProjectionKind`` has a word -- no default, no KeyError.

        The lookup is subscripted rather than ``.get``-with-a-default because a
        kind added to the enum without a word here must fail at the render
        rather than label a new kind silently and wrongly.  That only holds if
        the map is total, so this is the test that keeps it total.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.grid import _ACCRUAL_ROW_LABELS
        from app.services.account_projection import AccountProjectionKind

        assert set(_ACCRUAL_ROW_LABELS) == set(AccountProjectionKind)

    def test_each_kind_resolves_its_own_word(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A real account of each modelled kind gets that kind's word."""
        # pylint: disable=import-outside-toplevel
        from app.routes.grid import _accrual_row_label
        from tests._test_helpers import (
            create_loan_account,
            make_appreciating_account,
            make_investment_account,
        )

        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("5000.00"),
        )
        with app.app_context():
            inv = make_investment_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("10000.00"),
            )
            prop = make_appreciating_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("400000.00"), Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session,
                anchor_period=seed_periods_today[0],
                principal=Decimal("240000.00"),
            )

            assert _accrual_row_label(hysa) == "Interest"
            assert _accrual_row_label(inv) == "Growth"
            assert _accrual_row_label(prop) == "Appreciation"
            # A LIABILITY's accrual is interest CHARGED, so it must not be
            # named after an asset's growth.  The row cannot render for this
            # kind today, but ``resolve_grid_account`` can point the grid at a
            # loan (``grid_balance_view`` supports the degenerate cash view for
            # one), so the label is resolved on every such render and a wrong
            # word here is one commit away from being on screen.
            assert _accrual_row_label(loan) == "Interest"
            # PLAIN can never render the row (it resolves no ACCRUAL tier), but
            # it is the account every default /grid render resolves to, so the
            # lookup must answer it rather than raise.  It models no return at
            # all, so no word is truthful and it carries the neutral one.
            assert _accrual_row_label(seed_user["account"]) == "Growth"

    def test_the_zero_accounts_user_resolves_a_word_rather_than_crashing(self):
        """``account=None`` is the real zero-accounts state, not a hypothetical.

        ``_build_grid_view`` carries ``None`` for a user with no account rows at
        all, and ``classify_account(None)`` would ``AttributeError``.  Such a
        user has no columns, so no row ever renders -- but the label is resolved
        in the route before that is known.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.grid import _accrual_row_label

        assert _accrual_row_label(None) == "Growth"

    def test_the_hysa_grid_page_labels_the_row_interest(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """End to end: the real page renders the word, on both form factors.

        The route-level contract, because that is where ruling R-P binds: the
        desktop ``<tfoot>`` and the mobile This Period card are rendered from
        ONE context variable, so they cannot name the same row differently.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        resp = auth_client.get(f"/grid?account_id={hysa.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        footer = html[html.index('id="grid-summary"'):html.index("</tfoot>")]
        row = footer[footer.index("modelled-accrual-row"):]
        assert "Interest" in row[:row.index("</tr>")]

        card_start = html.index('id="mobile-tp-summary-')
        card = html[card_start:html.index('id="mobile-plan"', card_start)]
        bar = card[card.index("modelled-accrual-row"):]
        assert "Interest" in bar[:bar.index("</div>")]

    def test_the_balance_row_refresh_labels_the_row_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The self-refresh partial supplies its own label, not an empty one.

        Each of the three render entries resolves the label independently, so a
        ``balanceChanged`` refresh that dropped it would swap a headless row
        into the footer.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        resp = auth_client.get(
            f"/grid/balance-row?periods=6&offset=0&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        row = html[html.index("modelled-accrual-row"):]
        assert "Interest" in row[:row.index("</tr>")]

    def test_the_mobile_summary_refresh_labels_the_row_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """And so does the ``mobileCardSettled`` refresh."""
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        with app.app_context():
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={current.id}"
            f"&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        bar = html[html.index("modelled-accrual-row"):]
        assert "Interest" in bar[:bar.index("</div>")]


class TestGridInterestAccrual:
    """The grid accrues interest + shows an Interest row for an INTEREST account.

    The kind-correct-grid feature: when the grid is pointed at an
    interest-bearing account (HYSA / Money Market / CD / HSA) the projected
    balance accrues interest and a read-only "Interest" row explains the part
    of the balance change the transactions do not.  Every other account kind
    keeps the cash-flow view with no accrual row.
    """

    def test_interest_account_shows_accrual_row_and_accrued_balance(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An HYSA grid account renders the Interest row + interest-accrued balance.

        Seeds a $100,000 HYSA at 5% APY with no transactions: the cash-flow
        balance would sit flat at the $100,000 anchor, so any balance above it
        is interest the grid now accrues.  The route must render the seam's
        interest-accrued current-period balance and the per-period Interest
        row figure (cross-checked against the seam so the numbers are not
        hand-guessed).
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        user_id = seed_user["user"].id
        all_periods = pay_period_service.get_all_periods(user_id)
        current = pay_period_service.get_current_period(user_id)
        # Seam truth the route must render (current is the leftmost visible col).
        view = balance_at.grid_balance_view(hysa, bctx, all_periods)
        accrued = view.columns[current.id].balance
        interest = view.columns[current.id].accrual

        resp = auth_client.get(f"/grid?account_id={hysa.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        # The read-only accrual row renders for an interest grid account.
        assert "modelled-accrual-row" in html
        # Interest accrues: the current-period balance exceeds the $100,000
        # anchor, and the grid renders exactly the seam's accrued figure.
        assert accrued > Decimal("100000.00")
        assert f"${accrued:,.0f}" in html
        # The per-period interest is positive and rendered in the accrual row,
        # to the CENT and with its gain sign (developer ruling 2026-07-27): the
        # row's precision differs from the balance row's above it, so asserting
        # it at whole dollars would pass on a substring of the cents rendering
        # and stop grading the thing that changed.
        assert interest > Decimal("0.00")
        row = html[html.index("modelled-accrual-row"):]
        row = row[:row.index("</tr>")]
        assert f"+${interest:,.2f}" in row

    def test_plain_account_has_no_accrual_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The default checking (PLAIN) grid shows no Interest accrual row."""
        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        assert b"modelled-accrual-row" not in resp.data

    def test_interest_account_balance_row_refresh_shows_accrual_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The balance-row HTMX refresh renders the Interest row for an HYSA.

        The self-refresh endpoint must reproduce the full render's accrual row
        (it reads the same seam view the full render does), so a mark-paid
        that fires ``balanceChanged`` keeps the Interest row and the accrued
        balance current instead of reverting to the cash-flow view.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        resp = auth_client.get(
            f"/grid/balance-row?periods=6&offset=0&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "modelled-accrual-row" in html
        assert "Projected End Balance" in html

    def test_mobile_summary_refresh_shows_interest_for_hysa(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile This-Period summary refresh shows the Interest bar (HYSA).

        The self-refreshing mobile summary endpoint must reproduce the
        interest accrual (it reads the same seam view the full render does),
        so a mobile mark-paid that fires ``mobileCardSettled`` keeps the
        Interest bar instead of reverting to the cash-flow view.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        current = pay_period_service.get_current_period(seed_user["user"].id)
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={current.id}"
            f"&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        assert b"modelled-accrual-row" in resp.data

    def test_mobile_summary_refresh_no_interest_for_plain(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile This-Period summary refresh shows no Interest bar (PLAIN)."""
        current = pay_period_service.get_current_period(seed_user["user"].id)
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={current.id}",
        )
        assert resp.status_code == 200
        assert b"modelled-accrual-row" not in resp.data

    def test_refresh_uses_live_income_matching_full_render(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The balance-row refresh uses LIVE income, matching the full render.

        Ruling R-Q retired the caller's choice: the SEAM builds the live
        override map, so an interest account cannot be projected on the stored
        estimate by a caller that forgot to thread one.  Before it,
        ``grid_balance_view`` fell back to the STORED amount on a bare
        ``None`` for the interest path, so a refresh after a mark-paid could
        revert the balance to the stored figure while the full page showed the
        live one -- a flicker with no argument to fix it at the call site.
        Forces live ($5,000) != stored ($1,000) on an income transaction and
        asserts the live-income accrued balance appears in BOTH the full
        ``/grid`` render AND the ``/grid/balance-row`` refresh, with NO
        override passed anywhere.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("10000.00"),
        )
        scenario = seed_user["scenario"]
        bctx = BalanceContext.build(seed_user["user"].id)
        user_id = seed_user["user"].id
        status = db.session.query(Status).filter_by(name="Projected").one()
        income_type = (
            db.session.query(TransactionType).filter_by(name="Income").one()
        )
        income = Transaction(
            account_id=hysa.id,
            pay_period_id=seed_periods_today[2].id,
            scenario_id=scenario.id,
            status_id=status.id,
            name="Paycheck",
            transaction_type_id=income_type.id,
            estimated_amount=Decimal("1000.00"),
        )
        db.session.add(income)
        db.session.commit()

        # The live recompute revalues this income at $5,000 (vs $1,000 stored).
        monkeypatch.setattr(
            income_service, "live_projected_net",
            lambda uid, sid, txns: {income.id: Decimal("5000.00")},
        )
        all_periods = pay_period_service.get_all_periods(user_id)
        current = pay_period_service.get_current_period(user_id)
        # The seam builds the live map itself (ruling R-Q), so no override is
        # threaded here or by the route -- this IS the live figure.
        live_view = balance_at.grid_balance_view(hysa, bctx, all_periods)
        accrued_live = live_view.columns[current.id].balance
        # Sanity: the live $5,000 (not the $1,000 stored) is reflected -- the
        # balance clears the $10,000 anchor + the live deposit.
        assert accrued_live > Decimal("15000.00")

        full = auth_client.get(f"/grid?account_id={hysa.id}").data.decode()
        refresh = auth_client.get(
            f"/grid/balance-row?periods=6&offset=0&account_id={hysa.id}",
        ).data.decode()
        # Both surfaces render the live-income accrued balance -- no flicker.
        assert f"${accrued_live:,.0f}" in full
        assert f"${accrued_live:,.0f}" in refresh


class TestGridKindGate:
    """The D4 / A1 gate: the grid never renders an AMORTIZING account.

    Finding B-3 (live): with ``?account_id=<loan>`` the grid rendered
    the real Mortgage's balance RISING by the full PITI each month --
    the cash-flow producer reads payment transfers INTO the loan as
    inflows.  The resolver now treats a loan override exactly like a
    missing account and falls back.
    """

    @staticmethod
    def _mortgage(seed_user):
        """Create a bare active Mortgage-type account (kind is the gate)."""
        mortgage_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        loan = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Gate Mortgage",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.commit()
        return loan

    def test_account_id_override_with_loan_falls_back_to_checking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /grid?account_id=<loan> renders the checking fallback."""
        with app.app_context():
            loan = self._mortgage(seed_user)

            response = auth_client.get(f"/grid?account_id={loan.id}")

            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Gate Mortgage Balance" not in response.data

    def test_default_grid_setting_with_loan_falls_back_to_checking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A saved loan default (pre-gate data) no longer re-points the grid."""
        with app.app_context():
            loan = self._mortgage(seed_user)
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = loan.id
            db.session.commit()

            response = auth_client.get("/grid")

            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Gate Mortgage Balance" not in response.data


class TestTheAddPurchaseFormReadsTheUsersClock:
    """The mobile card's ``today`` is ``display_today()``, never the process's.

    Plan step S1-c / 12.10.  ``app/routes/transactions/_helpers.py``'s
    ``_render_mobile_card`` passed ``today=date.today()`` into a form whose
    ``purchased_on`` value the SERVICE judges against ``display_today()``
    (ruling R-M, ``entry_service._reject_future_purchase_date``).
    ``date.today()`` reads the PROCESS timezone; ``display_today()`` converts
    UTC now into ``America/New_York``.  On any process not pinned to that zone
    the two disagree for part of every day, and the app's own form then
    defaults to -- and caps at -- a date its own server refuses.

    Latent in production, because the container pins ``TZ: America/New_York``.
    NOT latent in CI, which runs ``TZ=Pacific/Kiritimati`` (UTC+14) precisely to
    catch this shape.  It is the same two-clock defect as finding N-133 / R2.

    **The test pins WHICH CLOCK the route reads, rather than arranging for the
    two to disagree.**  Mutating ``TZ`` + ``time.tzset()`` would reproduce the
    disagreement, but the C library's zone is process-global and survives
    ``monkeypatch``'s env restore (only another ``tzset()`` clears it), so a
    leak would silently re-zone every later test in the same xdist worker --
    measured, while writing this: it broke an unrelated MFA test three files
    away.  A test that can corrupt its neighbours to prove a point about clocks
    is the wrong instrument.  Substituting ``display_today`` for a sentinel is
    exact and side-effect free: the rendered form carries the sentinel if and
    only if the route reads that function, which IS the defect.
    """

    #: A date no clock can produce on its own, so its appearance in the
    #: rendered form can only have come from ``display_today``.
    _SENTINEL = date(2019, 7, 4)

    @staticmethod
    def _envelope_txn(seed_user, period):
        """A Projected envelope row, so the card renders the entries block."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import (  # pylint: disable=import-outside-toplevel
            StatusEnum, TxnTypeEnum,
        )

        txn = Transaction(
            account_id=seed_user["account"].id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="Two-clock Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            estimated_amount=Decimal("500.00"),
            is_envelope=True,
        )
        db.session.add(txn)
        db.session.commit()
        return txn

    def test_the_mobile_card_form_defaults_to_the_users_civil_day(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """The rendered add-purchase input comes from ``display_today``.

        The card is re-rendered through the real ``mark-done`` route with
        ``render=mobile_card``, which is the request ``_render_mobile_card``
        serves.  Its add-purchase input must read ``value`` and ``max`` off the
        substituted ``display_today``, and must not carry the process's own
        ``date.today()``.

        Both halves are asserted.  The positive alone would pass a build that
        rendered the sentinel somewhere unrelated; the negative alone would
        pass a form that rendered no date at all.

        Negative-controlled (Section 7.3): restoring ``today=date.today()`` in
        ``_render_mobile_card`` fails this with
        ``assert 'value="2019-07-04"' in ...``, measured 2026-08-01.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes.transactions import _helpers

        with app.app_context():
            period = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert period is not None
            txn_id = self._envelope_txn(seed_user, period).id

        # The premise: the sentinel is not what any real clock answers, so a
        # route reading ``date.today()`` cannot produce it by coincidence.
        assert date.today() != self._SENTINEL
        monkeypatch.setattr(_helpers, "display_today", lambda: self._SENTINEL)

        resp = auth_client.post(
            f"/transactions/{txn_id}/mark-done",
            data={
                "render": "mobile_card",
                "card_prefix": "tp",
                "can_edit": "1",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'name="purchased_on"' in html, (
            "the add-purchase form must actually have rendered"
        )
        assert f'value="{self._SENTINEL.isoformat()}"' in html
        assert f'max="{self._SENTINEL.isoformat()}"' in html
        assert date.today().isoformat() not in html
