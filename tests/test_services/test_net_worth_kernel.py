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

from datetime import date
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
        """An amortizing loan's dense map comes from its debt schedule -- FORWARD.

        A $240,000 mortgage (originated 2025-01-01, 6.5%, 30yr) with NO recorded
        payment.  The map is answered by two different producers, and the schedule
        drives only one of them:

        * **A period that has BEGUN** reads the confirmed ledger.  Not one payment
          was ever made, so the loan still owes the full $240,000.  It previously
          asserted a balance BELOW $240,000 here -- i.e. that ~14 unpaid, purely
          PROJECTED installments had paid principal down.  That is the phantom
          paydown: reporting principal the borrower never paid, and understating
          the debt.  It is the same defect ``loan_owed_at_dates`` refuses to commit
          ("silently UNDERSTATING the debt"), and the one
          ``TestUnpaidScheduleRowsNeverReduceTheDebt`` -- in this very file --
          asserts must never happen.  The two tests contradicted each other; this
          is the side that was wrong.
        * **A FUTURE period** reads the projection, which genuinely does amortize
          down from the ledger balance.  That is where the schedule path is real,
          so that is where it is asserted -- and it still fails if the map were to
          fall through to the static anchor.
        """
        # Pylint: import-outside-toplevel -- the date / test helpers load
        # inside the test, the file-wide deferred-import convention that
        # keeps the top-level import block minimal.
        # pylint: disable=import-outside-toplevel
        from datetime import date as _date
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            all_periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)

            acct = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("240000.00"), rate=Decimal("0.06500"),
                term=360, origination_date=_date(2025, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_period=all_periods[0],
            )

            schedule = net_worth_kernel.generate_debt_schedules(
                [acct], bctx,
            )[acct.id]
            balances = net_worth_kernel.build_account_balance_map(
                acct, bctx, all_periods,
                debt_schedule=schedule,
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            )

            assert balances is not None
            # The current period has BEGUN -> the confirmed ledger.  No payment
            # was ever recorded, so the full opening is still owed.  Unpaid
            # scheduled rows must not pay it down.
            assert balances[current.id] == Decimal("240000.00")

            # A FUTURE period -> the projection, which DOES amortize down from
            # that balance.  This is where the schedule path legitimately drives
            # the map, and it is still distinguishable from the static anchor.
            future = [p for p in all_periods if p.start_date > bctx.as_of]
            assert future, "expected a future period"
            assert balances[future[-1].id] < Decimal("240000.00")
            assert balances[future[-1].id] > Decimal("0.00")

    def test_amortizing_empty_schedule_uses_current_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """An amortizing loan with an EMPTY schedule holds its current balance.

        A :class:`DebtSchedule` whose ``schedule`` is empty (a paid-off or
        fully-resolved loan with no remaining rows) must still route to the
        loan path and return its resolver-derived CURRENT balance at every
        period, NOT fall through to the entries-aware resolver (which would
        report the anchor balance).  The dispatch gate is membership
        (``debt_schedule is not None``), not the schedule's truthiness: a
        DebtSchedule carrying ``[]`` is distinct from ``None`` (not a
        resolved amortizing schedule).

        The DebtSchedule's current_balance is deliberately set to $240,000 --
        different from the $200,000 account anchor -- so the loan path
        ($240,000 current balance) is distinguishable from the resolver
        fallthrough (the $200,000 flat anchor).
        """
        # Pylint: import-outside-toplevel -- the date / test helpers load
        # inside the test, the file-wide deferred-import convention that
        # keeps the top-level import block minimal.
        # pylint: disable=import-outside-toplevel
        from datetime import date as _date
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            all_periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)

            # The account's $200,000 anchor is deliberately DIFFERENT from the
            # loan's $240,000 principal: it is the decoy that makes this test
            # falsifiable.  Falling through to the entries-aware resolver would
            # report the anchor; the loan path reports the debt schedule's
            # current balance.  Production can set these apart too -- the account
            # is created first, and the loan params configured afterwards.
            acct = create_loan_account(
                seed_user, db.session, name="Unpaid Mtg",
                principal=Decimal("240000.00"), rate=Decimal("0.06500"),
                term=360, origination_date=_date(2025, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
                anchor_period=all_periods[0],
                anchor_balance=Decimal("200000.00"),
            )

            balances = net_worth_kernel.build_account_balance_map(
                acct, bctx, all_periods,
                debt_schedule=net_worth_kernel.DebtSchedule(
                    schedule=[],
                    projection_seed=Decimal("240000.00"),
                    owed_from=date(2024, 1, 1),
                ),
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            )

            assert balances is not None
            # Empty schedule -> loan path -> current balance $240,000 at
            # every period, NOT the $200,000 anchor (resolver fallthrough).
            assert balances[current.id] == Decimal("240000.00")
            assert balances[all_periods[-1].id] == Decimal("240000.00")


class TestAmortizingReadSwitch:
    """The C9 genesis per-period read switch: ledger past, projection future.

    ``_build_amortizing_balance_map`` overlays the confirmed genesis ledger on
    every period that has begun by today and keeps the re-seeded schedule
    projection for the future, via the pure
    :func:`app.services.account_projection.splice_confirmed_and_projected_loan_balances`.
    One class pins the pure splice boundary rule directly, and one pins the
    whole builder end-to-end on an off-schedule genesis loan (where the ledger
    and the schedule replay genuinely diverge).
    """

    def test_splice_reads_ledger_for_begun_periods_projection_after(self):
        """Periods begun by as_of read confirmed_map; later periods read projected_map.

        Three periods with deliberately DIFFERENT confirmed vs projected values
        so the per-period source choice is observable; ``as_of`` is the middle
        period's start, so the ``<=`` boundary is inclusive (a period starting
        ON as_of has begun and reads the ledger):
          - p1 start 2026-01-01 <= as_of -> confirmed 100.00 (not projected 999)
          - p2 start 2026-06-15 == as_of -> confirmed  90.00 (not projected 888)
          - p3 start 2026-12-31 >  as_of -> projected  80.00 (not confirmed 777)
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention keeps the top-level import block minimal.
        # pylint: disable=import-outside-toplevel
        from collections import OrderedDict
        from datetime import date as _date
        from types import SimpleNamespace

        from app.services.account_projection import (
            splice_confirmed_and_projected_loan_balances,
        )

        as_of = _date(2026, 6, 15)
        periods = [
            SimpleNamespace(id=1, start_date=_date(2026, 1, 1)),
            SimpleNamespace(id=2, start_date=_date(2026, 6, 15)),
            SimpleNamespace(id=3, start_date=_date(2026, 12, 31)),
        ]
        confirmed = OrderedDict([
            (1, Decimal("100.00")),
            (2, Decimal("90.00")),
            (3, Decimal("777.00")),
        ])
        projected = OrderedDict([
            (1, Decimal("999.00")),
            (2, Decimal("888.00")),
            (3, Decimal("80.00")),
        ])

        result = splice_confirmed_and_projected_loan_balances(
            periods, confirmed, projected, as_of,
        )

        # p1 and p2 have begun (start <= as_of) -> confirmed ledger; p3 is
        # future (start > as_of) -> schedule projection.
        assert result[1] == Decimal("100.00")
        assert result[2] == Decimal("90.00")   # start == as_of is "begun"
        assert result[3] == Decimal("80.00")
        # Keyed by period.id in the given period order.
        assert list(result.keys()) == [1, 2, 3]

    def test_builder_splices_ledger_past_and_projection_future(
        self, app, cross_page_loan_off_schedule_ctx,
    ):
        """build_account_balance_map reads the ledger for begun periods, projection after.

        The shared off-schedule genesis fixture: one settled payment far above
        the scheduled P&I in a past period, so the confirmed ledger (REAL
        principal) diverges from the schedule replay.  Over a window spanning
        past AND future, the dense map must equal the confirmed-ledger map for
        every period begun by today and the schedule-projection map for every
        future period -- the plan Section 9 splice, verified in the real
        builder (not just the pure helper above).

        Non-vacuous both ways: at least one begun period's confirmed ledger
        differs from the schedule replay (the off-schedule divergence the read
        switch exists for), and at least one future period's projection differs
        from the flat-carried confirmed balance (so "future reads projection"
        is not silently equal to "future reads the carried-flat ledger").
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention keeps the top-level import block minimal.
        # pylint: disable=import-outside-toplevel
        from datetime import date as _date

        from app.services.account_projection import (
            compute_forward_loan_period_balance_map,
        )
        from app.services.loan_posting_service import confirmed_loan_balance_map

        with app.app_context():
            ctx = cross_page_loan_off_schedule_ctx
            loan = ctx["account"]
            scenario = ctx["scenario"]
            periods = ctx["all_periods"]
            today = _date.today()
            bctx = BalanceContext(
                user_id=loan.user_id, scenario=scenario, as_of=today,
            )

            debt_schedule = net_worth_kernel.generate_debt_schedules(
                [loan], bctx,
            )[loan.id]
            dense = net_worth_kernel.build_account_balance_map(
                loan, bctx, periods,
                debt_schedule=debt_schedule,
                investment_params=None,
                deductions=[],
                salary_gross_biweekly=Decimal("0.00"),
            )
            confirmed = confirmed_loan_balance_map(
                loan.id, scenario.id, periods,
            )
            projected = compute_forward_loan_period_balance_map(
                debt_schedule.schedule, periods,
                debt_schedule.projection_seed, debt_schedule.owed_from,
            )

            assert dense is not None
            # The genesis fixture opened the loan in the ledger.
            assert confirmed is not None

            begun_divergence = False
            future_divergence = False
            for period in periods:
                if period.start_date <= today:
                    assert dense[period.id] == confirmed[period.id], (
                        f"begun period {period.id} ({period.start_date}) read "
                        f"{dense[period.id]!r}, expected the confirmed ledger "
                        f"{confirmed[period.id]!r}"
                    )
                    if confirmed[period.id] != projected[period.id]:
                        begun_divergence = True
                else:
                    assert dense[period.id] == projected[period.id], (
                        f"future period {period.id} ({period.start_date}) read "
                        f"{dense[period.id]!r}, expected the schedule "
                        f"projection {projected[period.id]!r}"
                    )
                    if projected[period.id] != confirmed[period.id]:
                        future_divergence = True

            assert begun_divergence, (
                "no begun period had confirmed != projected; the "
                "ledger-vs-replay assertion is vacuous (loan not off-schedule)"
            )
            assert future_divergence, (
                "no future period had projection != confirmed; the "
                "projection-vs-carry-flat assertion is vacuous"
            )


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
