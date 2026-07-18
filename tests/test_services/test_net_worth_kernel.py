"""
Shekel Budget App -- Net-Worth Kernel Tests (Loop B Phase 1)

Direct coverage for the shared :mod:`app.services.net_worth_kernel`
promoted out of the year-end summary package: the asset-plus /
liability-minus net-worth sum, and the per-account balance-map dispatch
over the canonical entries-aware resolver.  The year-end net-worth tests
in ``test_year_end_summary_service.py`` are the behavior-preserving
no-drift guard for the move; these tests pin the kernel's public
contract independently of either consumer.
"""

from decimal import Decimal

from app.services import net_worth_kernel, pay_period_service
from app.services.scenario_resolver import get_baseline_scenario
from app.services.resolution_context import BalanceContext


class TestSumNetWorthAtPeriod:
    """Tests for ``sum_net_worth_at_period`` (asset-plus / liability-minus)."""

    def test_asset_minus_abs_liability(self):
        """Assets add their balance; liabilities subtract their magnitude.

        One asset at 1,000.00 and one liability at 250.00 for period id 5:
          1000.00 - abs(250.00) = 750.00.
        """
        account_data = [
            {"balances": {5: Decimal("1000.00")}, "is_liability": False},
            {"balances": {5: Decimal("250.00")}, "is_liability": True},
        ]
        # 1000.00 - abs(250.00) = 750.00
        assert net_worth_kernel.sum_net_worth_at_period(
            5, account_data,
        ) == Decimal("750.00")

    def test_liability_stored_negative_still_subtracts_magnitude(self):
        """A liability stored as a negative balance subtracts its magnitude.

        ``-abs(bal)`` makes the sign of the stored liability irrelevant:
        a liability at -250.00 reduces net worth by 250.00, identically to
        one stored at +250.00:
          1000.00 - abs(-250.00) = 750.00.
        """
        account_data = [
            {"balances": {5: Decimal("1000.00")}, "is_liability": False},
            {"balances": {5: Decimal("-250.00")}, "is_liability": True},
        ]
        # 1000.00 - abs(-250.00) = 750.00
        assert net_worth_kernel.sum_net_worth_at_period(
            5, account_data,
        ) == Decimal("750.00")

    def test_missing_period_contributes_zero(self):
        """An account with no balance at the period contributes zero.

        The asset has 400.00 at period 5 but the liability map has no key
        5 (only key 9), so the liability contributes its ZERO default:
          400.00 - abs(0) = 400.00.
        """
        account_data = [
            {"balances": {5: Decimal("400.00")}, "is_liability": False},
            {"balances": {9: Decimal("100.00")}, "is_liability": True},
        ]
        # 400.00 - abs(0) = 400.00 (period 5 absent from the liability map)
        assert net_worth_kernel.sum_net_worth_at_period(
            5, account_data,
        ) == Decimal("400.00")

    def test_no_accounts_is_zero(self):
        """An empty account list sums to zero."""
        assert net_worth_kernel.sum_net_worth_at_period(
            5, [],
        ) == Decimal("0")


class TestBuildAccountBalanceMap:
    """Tests for ``build_account_balance_map`` over the plain resolver path."""

    def test_plain_checking_map_seeds_anchor_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A plain checking account's dense map carries its flat anchor.

        The seed Checking account ($1,000) has no transactions, so every
        period in its dense map holds the flat 1,000.00 anchor balance
        (the canonical entries-aware resolver path).  Asserting the
        current period's entry pins the resolver dispatch.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            all_periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]

            balances = net_worth_kernel.build_account_balance_map(
                account, bctx, all_periods,
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            )

            assert balances is not None
            # No transactions -> flat anchor at every period.
            assert balances[all_periods[0].id] == Decimal("1000.00")
            assert balances[all_periods[-1].id] == Decimal("1000.00")

    def test_no_anchor_period_returns_none(self, app, db, seed_user):
        """An account with no anchor period yields None (no dense map).

        A stand-in object with ``current_anchor_period_id = None`` short-
        circuits before any engine call, matching the year-end section's
        ``balances is None`` skip for un-anchored accounts.
        """
        # Pylint: import-outside-toplevel -- deferred so the stand-in
        # type is built only inside the test (the file-wide convention).
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        with app.app_context():
            account = SimpleNamespace(current_anchor_period_id=None)
            assert net_worth_kernel.build_account_balance_map(
                account, object(), [],
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            ) is None


class TestInterestByPeriodForAccount:
    """Tests for ``interest_by_period_for_account`` (interest-earned accessor).

    The interest VALUE behavior (an account's per-period accrual matching
    the calculator) is locked end-to-end by the HYSA savings-progress
    tests in ``test_year_end_summary_service.py``, the accessor's only
    consumer.  This pins the contract those tests cannot reach: the
    no-anchor short-circuit that returns the empty map.
    """

    def test_no_anchor_period_returns_empty(self, app, db, seed_user):
        """An account with no anchor period earns no projectable interest.

        A stand-in with ``current_anchor_period_id = None`` short-circuits
        before any engine call to the empty map, so the year-end consumer's
        year-filtered sum is ``Decimal("0")`` -- the prior inline
        ``current_anchor_period_id is None -> ZERO`` early-out, preserved.
        """
        # Pylint: import-outside-toplevel -- deferred so the stand-in type
        # is built only inside the test (the file-wide convention).
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        with app.app_context():
            account = SimpleNamespace(current_anchor_period_id=None)
            assert net_worth_kernel.interest_by_period_for_account(
                account, object(), [], None,
            ) == {}
