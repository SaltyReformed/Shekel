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

from app.extensions import db
from app.models.ref import AccountType
from app.services import account_service, net_worth_kernel, pay_period_service
from app.services.scenario_resolver import get_baseline_scenario


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
            all_periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]

            balances = net_worth_kernel.build_account_balance_map(
                account, scenario, all_periods,
                debt_schedule=None,
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
                debt_schedule=None,
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            ) is None

    def test_liability_loan_uses_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """An amortizing loan's dense map comes from its debt schedule.

        A $240,000 mortgage (originated 2025-01-01, 6.5%, 30yr) projected
        with its resolver schedule yields a current-period balance BELOW
        the $240,000 origination principal -- the amortization has paid
        principal down by the test clock's current period -- proving the
        schedule path drives the map, not the static anchor.
        """
        # Pylint: import-outside-toplevel -- the date / LoanParams / test
        # helpers load inside the test, the file-wide deferred-import
        # convention that keeps the top-level import block minimal.
        # pylint: disable=import-outside-toplevel
        from datetime import date as _date
        from app.models.loan_params import LoanParams
        from tests._test_helpers import (
            insert_origination_event,
            insert_origination_rate,
        )
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            all_periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)

            mortgage_type = (
                db.session.query(AccountType).filter_by(name="Mortgage").one()
            )
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=user_id,
                    account_type_id=mortgage_type.id,
                    name="Mtg",
                    anchor_balance=Decimal("240000.00"),
                    anchor_period_id=all_periods[0].id,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            params = LoanParams(
                account_id=acct.id,
                original_principal=Decimal("240000.00"),
                current_principal=Decimal("240000.00"),
                term_months=360,
                origination_date=_date(2025, 1, 1),
                payment_day=1,
            )
            db.session.add(params)
            db.session.flush()
            insert_origination_event(params)
            insert_origination_rate(params, Decimal("0.06500"))
            db.session.commit()

            schedule = net_worth_kernel.generate_debt_schedules(
                [acct], scenario.id,
            )[acct.id]
            balances = net_worth_kernel.build_account_balance_map(
                acct, scenario, all_periods,
                debt_schedule=schedule,
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            )

            assert balances is not None
            # Amortization has paid principal down below the origination
            # $240,000 by the current period (schedule path, not anchor).
            assert balances[current.id] < Decimal("240000.00")
            assert balances[current.id] > Decimal("0.00")

    def test_amortizing_empty_schedule_uses_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An amortizing loan with an EMPTY schedule holds its principal.

        A loan that resolves to an empty schedule (LoanParams present, no
        payment events) must still route to the loan path and return its
        ORIGINAL PRINCIPAL at every period, NOT fall through to the
        entries-aware resolver (which would report the anchor balance).
        The dispatch gate is membership (``debt_schedule is not None``),
        not truthiness: an empty list ``[]`` is a resolved-but-unpaid loan,
        distinct from ``None`` (not a resolved amortizing schedule).

        The anchor balance is deliberately set to $200,000 -- different
        from the $240,000 original principal -- so the loan path ($240,000
        original principal) is distinguishable from the resolver path
        (the $200,000 flat anchor).  Pre-fix (truthiness gate) the empty
        list fell through and this read $200,000.
        """
        # Pylint: import-outside-toplevel -- the date / LoanParams models
        # load inside the test, the file-wide deferred-import convention
        # that keeps the top-level import block minimal.
        # pylint: disable=import-outside-toplevel
        from datetime import date as _date
        from app.models.loan_params import LoanParams
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            all_periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)

            mortgage_type = (
                db.session.query(AccountType).filter_by(name="Mortgage").one()
            )
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=user_id,
                    account_type_id=mortgage_type.id,
                    name="Unpaid Mtg",
                    anchor_balance=Decimal("200000.00"),
                    anchor_period_id=all_periods[0].id,
                ),
            )
            db.session.add(acct)
            db.session.flush()
            params = LoanParams(
                account_id=acct.id,
                original_principal=Decimal("240000.00"),
                current_principal=Decimal("240000.00"),
                term_months=360,
                origination_date=_date(2025, 1, 1),
                payment_day=1,
            )
            db.session.add(params)
            db.session.commit()

            balances = net_worth_kernel.build_account_balance_map(
                acct, scenario, all_periods,
                debt_schedule=[],
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            )

            assert balances is not None
            # Empty schedule -> loan path -> original principal $240,000 at
            # every period, NOT the $200,000 anchor (resolver fallthrough).
            assert balances[current.id] == Decimal("240000.00")
            assert balances[all_periods[-1].id] == Decimal("240000.00")
