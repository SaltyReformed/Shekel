"""
Shekel Budget App - Data Isolation Integration Tests

Verifies that each user sees only their own data on every page and
endpoint. Two users with complete, distinguishable datasets are
created via fixtures, and every user-facing page is tested to confirm
that neither user can see the other's data in the HTTP response body.

These tests catch missing user_id filters in queries, template context
leaks, and any other path that would expose one user's financial data
to another user.
"""

# Fixture parameters are injected by pytest and intentionally not
# referenced in the function body when they only need to trigger
# data creation.
# pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments

from datetime import date as _real_date


def _freeze_today_to_period_5(monkeypatch):
    """Patch date.today() in pay_period_service to return a date in period 5.

    The seed_periods fixture generates 10 biweekly periods starting
    2026-01-02. Period 5 runs 2026-03-13 to 2026-03-26. Freezing today
    to 2026-03-20 keeps the grid offset calculations stable regardless
    of the actual wall-clock date.
    """
    target = _real_date(2026, 3, 20)

    class _FrozenDate(_real_date):
        """Date subclass with a fixed today() for test isolation."""

        @classmethod
        def today(cls):
            """Return the frozen date."""
            return target

    monkeypatch.setattr("app.services.pay_period_service.date", _FrozenDate)


class TestGridIsolation:
    """Verify the budget grid (/grid) shows only the logged-in user's transactions."""

    def test_user_a_sees_own_transactions(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data,
        monkeypatch,
    ):
        """User A sees 'Rent Payment' but not 'Second User Rent' on the grid."""
        _freeze_today_to_period_5(monkeypatch)
        with app.app_context():
            # Navigate back to show period 0 where the fixture transaction
            # was created (default view starts at the current period).
            response = auth_client.get("/grid?offset=-5")
            assert response.status_code == 200

            # Positive: User A's transaction name is present (in aria-label
            # or title attribute of the transaction cell).
            assert b"Rent Payment" in response.data

            # Negative: User B's transaction name is absent.
            assert b"Second User Rent" not in response.data

    def test_user_b_sees_own_transactions(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data,
        monkeypatch,
    ):
        """User B sees 'Second User Rent' but not 'Rent Payment' on the grid."""
        _freeze_today_to_period_5(monkeypatch)
        with app.app_context():
            # Navigate back to show period 0 where the fixture transaction
            # was created.
            response = second_auth_client.get("/grid?offset=-5")
            assert response.status_code == 200

            # Positive: User B's transaction name is present.
            assert b"Second User Rent" in response.data

            # Negative: User A's transaction name is absent.
            assert b"Rent Payment" not in response.data


class TestAccountsIsolation:
    """Verify accounts pages show only the logged-in user's data."""

    def test_user_a_savings_dashboard(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A sees 'Emergency Fund' goal but not 'Vacation Fund' on savings."""
        with app.app_context():
            response = auth_client.get("/savings")
            assert response.status_code == 200

            # Positive: User A's savings goal edit button is present.
            # Using aria-label to avoid matching the static "Emergency
            # Fund Coverage" heading.
            assert b"Edit Emergency Fund" in response.data

            # Negative: User B's savings goal is absent.
            assert b"Vacation Fund" not in response.data

    def test_user_b_savings_dashboard(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B sees 'Vacation Fund' goal but not User A's goals on savings."""
        with app.app_context():
            response = second_auth_client.get("/savings")
            assert response.status_code == 200

            # Positive: User B's savings goal is present.
            assert b"Vacation Fund" in response.data

            # Negative: User A's savings goal edit button is absent.
            # Note: "Emergency Fund Coverage" is a static heading that
            # appears for all users; check the goal-specific marker.
            assert b"Edit Emergency Fund" not in response.data

    def test_user_a_manage_accounts(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's accounts page does not contain User B's account IDs."""
        with app.app_context():
            response = auth_client.get("/accounts")
            assert response.status_code == 200

            # Positive: User A's account edit URL is present.
            user_a_acct_id = seed_full_user_data["account"].id
            assert (
                f"/accounts/{user_a_acct_id}/edit".encode() in response.data
            )

            # Negative: User B's account edit URL is absent.
            user_b_acct_id = seed_full_second_user_data["account"].id
            assert (
                f"/accounts/{user_b_acct_id}/edit".encode()
                not in response.data
            )

    def test_user_b_manage_accounts(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's accounts page does not contain User A's account IDs."""
        with app.app_context():
            response = second_auth_client.get("/accounts")
            assert response.status_code == 200

            # Positive: User B's account edit URL is present.
            user_b_acct_id = seed_full_second_user_data["account"].id
            assert (
                f"/accounts/{user_b_acct_id}/edit".encode() in response.data
            )

            # Negative: User A's account edit URL is absent.
            user_a_acct_id = seed_full_user_data["account"].id
            assert (
                f"/accounts/{user_a_acct_id}/edit".encode()
                not in response.data
            )


class TestTemplatesIsolation:
    """Verify the templates list shows only the logged-in user's templates."""

    def test_user_a_sees_own_templates(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A sees 'Rent Payment' but not 'Second User Rent' on templates."""
        with app.app_context():
            response = auth_client.get("/templates")
            assert response.status_code == 200

            # Positive: User A's template name is present.
            assert b"Rent Payment" in response.data

            # Negative: User B's template name is absent.
            assert b"Second User Rent" not in response.data

    def test_user_b_sees_own_templates(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B sees 'Second User Rent' but not 'Rent Payment' on templates."""
        with app.app_context():
            response = second_auth_client.get("/templates")
            assert response.status_code == 200

            # Positive: User B's template name is present.
            assert b"Second User Rent" in response.data

            # Negative: User A's template name is absent.
            assert b"Rent Payment" not in response.data


class TestTransfersIsolation:
    """Verify the transfers list shows only the logged-in user's transfers."""

    def test_user_a_sees_own_transfers(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A sees 'Monthly Savings' but not 'Bi-Weekly Savings' on transfers."""
        with app.app_context():
            response = auth_client.get("/transfers")
            assert response.status_code == 200

            # Positive: User A's transfer template name is present.
            assert b"Monthly Savings" in response.data

            # Negative: User B's transfer template name is absent.
            assert b"Bi-Weekly Savings" not in response.data

    def test_user_b_sees_own_transfers(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B sees 'Bi-Weekly Savings' but not 'Monthly Savings' on transfers."""
        with app.app_context():
            response = second_auth_client.get("/transfers")
            assert response.status_code == 200

            # Positive: User B's transfer template name is present.
            assert b"Bi-Weekly Savings" in response.data

            # Negative: User A's transfer template name is absent.
            assert b"Monthly Savings" not in response.data


class TestSalaryIsolation:
    """Verify the salary list shows only the logged-in user's profiles."""

    def test_user_a_sees_own_profiles(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A sees 'Day Job' but not 'Second Job' on salary."""
        with app.app_context():
            response = auth_client.get("/salary")
            assert response.status_code == 200

            # Positive: User A's salary profile name is present.
            assert b"Day Job" in response.data

            # Negative: User B's salary profile name is absent.
            assert b"Second Job" not in response.data

    def test_user_b_sees_own_profiles(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B sees 'Second Job' but not 'Day Job' on salary."""
        with app.app_context():
            response = second_auth_client.get("/salary")
            assert response.status_code == 200

            # Positive: User B's salary profile name is present.
            assert b"Second Job" in response.data

            # Negative: User A's salary profile name is absent.
            assert b"Day Job" not in response.data


class TestSavingsGoalsIsolation:
    """Verify savings goals on /savings show only the logged-in user's goals."""

    def test_user_a_sees_own_goals(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A sees 'Emergency Fund' goal but not 'Vacation Fund'."""
        with app.app_context():
            response = auth_client.get("/savings")
            assert response.status_code == 200

            # Positive: User A's savings goal edit button is present.
            assert b"Edit Emergency Fund" in response.data

            # Negative: User B's savings goal is absent.
            assert b"Vacation Fund" not in response.data

    def test_user_b_sees_own_goals(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B sees 'Vacation Fund' goal but not User A's goals."""
        with app.app_context():
            response = second_auth_client.get("/savings")
            assert response.status_code == 200

            # Positive: User B's savings goal is present.
            assert b"Vacation Fund" in response.data

            # Negative: User A's savings goal edit button is absent.
            assert b"Edit Emergency Fund" not in response.data


class TestCategoriesIsolation:
    """Verify the categories section shows only the logged-in user's categories."""

    def test_user_a_sees_own_categories(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's categories page loads and shows the Categories heading."""
        with app.app_context():
            response = auth_client.get("/settings?section=categories")
            assert response.status_code == 200

            # Positive: the categories section rendered.
            assert b"Categories" in response.data

            # Positive: a known category item is present.
            assert b"Groceries" in response.data

    def test_user_b_sees_own_categories(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's categories page loads and shows the Categories heading."""
        with app.app_context():
            response = second_auth_client.get("/settings?section=categories")
            assert response.status_code == 200

            # Positive: the categories section rendered.
            assert b"Categories" in response.data

            # Positive: a known category item is present.
            assert b"Groceries" in response.data

    def test_categories_not_doubled(
        self, app, auth_client, second_auth_client,
        seed_full_user_data, seed_full_second_user_data
    ):
        """Each user sees exactly 5 category delete buttons, not 10."""
        with app.app_context():
            response_a = auth_client.get("/settings?section=categories")
            assert response_a.status_code == 200
            # Each category has a delete form with action containing
            # /categories/<id>/delete. Count occurrences of the delete
            # action pattern.
            delete_count_a = response_a.data.count(b"/delete")
            assert delete_count_a == 5, (
                f"User A sees {delete_count_a} delete actions, expected 5"
            )

            response_b = second_auth_client.get("/settings?section=categories")
            assert response_b.status_code == 200
            delete_count_b = response_b.data.count(b"/delete")
            assert delete_count_b == 5, (
                f"User B sees {delete_count_b} delete actions, expected 5"
            )


class TestChartsIsolation:
    """Verify /charts redirects to /analytics (Section 8 cleanup)."""

    def test_charts_redirects_for_user_a(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A gets 301 redirect from /charts to /analytics."""
        with app.app_context():
            response = auth_client.get("/charts")
            assert response.status_code == 301
            assert "/analytics" in response.headers["Location"]

    def test_charts_redirects_for_user_b(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B gets 301 redirect from /charts to /analytics."""
        with app.app_context():
            response = second_auth_client.get("/charts")
            assert response.status_code == 301
            assert "/analytics" in response.headers["Location"]


class TestSettingsIsolation:
    """Verify settings pages show only the logged-in user's data."""

    def test_user_a_sees_own_display_name(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's settings page shows 'Test User' (via navbar) not 'Second User'."""
        with app.app_context():
            response = auth_client.get("/settings")
            assert response.status_code == 200

            # Positive: User A's display name is in the navbar.
            assert b"Test User" in response.data

            # Negative: User B's display name is absent.
            assert b"Second User" not in response.data

    def test_user_b_sees_own_display_name(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's settings page shows 'Second User' (via navbar) not 'Test User'."""
        with app.app_context():
            response = second_auth_client.get("/settings")
            assert response.status_code == 200

            # Positive: User B's display name is in the navbar.
            assert b"Second User" in response.data

            # Negative: User A's display name is absent.
            assert b"Test User" not in response.data


class TestRetirementIsolation:
    """Verify the retirement dashboard loads per-user with no cross-user leaks."""

    def test_user_a_retirement_loads(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A can load the retirement dashboard."""
        with app.app_context():
            response = auth_client.get("/retirement")
            assert response.status_code == 200

            # Positive: the retirement page heading is present.
            assert b"Retirement" in response.data

    def test_user_b_retirement_loads(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B can load the retirement dashboard."""
        with app.app_context():
            response = second_auth_client.get("/retirement")
            assert response.status_code == 200

            # Positive: the retirement page heading is present.
            assert b"Retirement" in response.data

    def test_retirement_no_cross_user_data(
        self, app, auth_client, second_auth_client,
        seed_full_user_data, seed_full_second_user_data
    ):
        """Neither user's retirement page leaks the other user's salary data."""
        with app.app_context():
            response_a = auth_client.get("/retirement")
            assert response_a.status_code == 200

            # User A's page should not contain User B's salary profile name.
            assert b"Second Job" not in response_a.data

            response_b = second_auth_client.get("/retirement")
            assert response_b.status_code == 200

            # User B's page should not contain User A's salary profile name.
            assert b"Day Job" not in response_b.data
