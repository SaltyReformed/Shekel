"""
Shekel Budget App -- Net-Worth Kernel Tests (Loop B Phase 1)

Direct coverage for the shared :mod:`app.services.balance_at._kernel`
promoted out of the year-end summary package: the per-account balance-map
dispatch over the canonical entries-aware resolver.  These tests pin the
kernel's public contract independently of its consumers.

The asset-plus / liability-minus net-worth reduction is NOT covered here.
It lives with its consumer in ``savings_dashboard_service._net_worth``
(``_sum_composition_at_period`` / ``compute_net_worth_today``) and is
covered on the live path by ``test_savings_dashboard_service.py``:
``test_assets_minus_liabilities`` and
``test_total_liabilities_is_positive_magnitude`` for the reduction, plus one
control per ``abs`` site for a negatively-stored liability --

    hero            test_a_negative_balance_liability_still_adds_its_magnitude
    per-period band test_series_liability_band_holds_a_negative_balance_magnitude

The kernel's own
``sum_net_worth_at_period`` was deleted as dead code: it had no production
caller, and its unit tests graded it against hand-built dicts rather than
against anything a screen renders.
"""

from decimal import Decimal

from app.services import pay_period_service
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext


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
    the calculator) is locked end-to-end by the HYSA interest tests in
    ``test_balance_at.py`` and the account-detail route tests in
    ``test_accounts.py`` -- the accessor's only consumer is that page's
    "Interest, next 12 mo" chip.  (It was the year-end savings-progress
    section until plan step F2 deleted that package.)  This pins the
    contract those tests cannot reach: the no-anchor short-circuit that
    returns the empty map.
    """

    def test_no_anchor_period_returns_empty(self, app, db, seed_user):
        """An account with no anchor period earns no projectable interest.

        A stand-in with ``current_anchor_period_id = None`` short-circuits
        before any engine call to the empty map, so the consumer's windowed
        sum is ``Decimal("0")`` -- the prior inline
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
