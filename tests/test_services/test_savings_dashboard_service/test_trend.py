"""Savings dashboard service tests: the trend periods, and the trend window gating each loan from
its recorded start.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date, timedelta
from decimal import Decimal

from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import DerivedPeriod


class TestBuildTrendPeriods:
    """Tests for the trend's honest history window (build_trend_periods).

    The window leads with a short "actual" history tail then the forward
    projection.  The tail reaches back only as far as every CASH account
    (PLAIN / INTEREST -- the kinds whose dense map omits pre-anchor
    periods) has a real balance, i.e. to the LATEST such anchor, capped at
    the history cap.  These unit tests drive the helper with synthetic
    periods + accounts so the window arithmetic is pinned independently of
    the projection engines.
    """

    @staticmethod
    def _period(period_index):
        """Synthetic PayPeriod stand-in (id, period_index, end_date reads).

        The id is offset (``100 + index``) so an id/index swap in the
        production code would surface rather than coincide.  ``end_date`` is
        biweekly-spaced and distinct per index so the loan gate
        (``_loan_record_start_index``, which matches a loan's recorded start
        to a period by ``end_date``) resolves unambiguously.
        """
        return DerivedPeriod(
            period_id=100 + period_index,
            period_index=period_index,
            start_date=date(2026, 1, 1) + timedelta(days=14 * period_index),
            end_date=date(2026, 1, 14) + timedelta(days=14 * period_index),
            end_is_projected=False,
        )

    @staticmethod
    def _account(kind, anchor_period_index, account_id=1):
        """Synthetic Account whose type flags ``classify_account`` reads.

        ``anchor_period_index`` is mapped to the matching ``_period`` id
        (``100 + index``); ``None`` leaves the account unanchored.
        ``account_id`` keys the loan gate's ``loan_starts`` lookup.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.account_projection import AccountProjectionKind
        acct_type = SimpleNamespace(
            has_amortization=kind is AccountProjectionKind.AMORTIZING,
            has_interest=kind is AccountProjectionKind.INTEREST,
            has_appreciation=kind is AccountProjectionKind.APPRECIATING,
            has_parameters=kind is AccountProjectionKind.INVESTMENT,
        )
        # ``current_anchor_period_id`` went with ruling R-EH; the stand-in
        # carries only what a producer still reads off an account.
        return SimpleNamespace(id=account_id, account_type=acct_type)

    @staticmethod
    def _recorded_start(start_period_index, periods):
        """A loan whose record starts in a period: that period's ``end_date``.

        The loan gate resolves the loan's honest start to that period's index.
        It reads the day the loan's record starts (ruling R-R111); until plan
        step recurrence:R16-c-2 it read the earliest row of the loan's
        schedule, which this helper built as a one-row schedule dated the
        same day.
        """
        return periods[start_period_index].end_date

    def test_history_reaches_back_past_the_cash_anchor(self):
        """A cash account's anchor no longer bounds the history (N-44).

        Periods 0..9, today at index 5, one PLAIN account anchored at
        index 2, no loans.  Its balance is a fold over its own assertions, so
        it is real at every period and constrains nothing: the honest start is
        0 and only the ``_TREND_HISTORY_PERIODS`` cap (5 - 6 = -1, which does
        not bind here) limits the tail.  Window indices 0..9, ``current_index``
        the count below 5 -> 0, 1, 2, 3, 4 = 5.

        Before plan step X-c2b2 the projection omitted every pre-anchor period,
        so this same shape gated at index 2 and drew 3 history points; the two
        periods it refused are exactly the ones the fold now answers.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 2)]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert [p.period_index for p in window] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert current_index == 5
        assert honest_start == 0

    def test_a_cash_anchor_in_the_current_period_still_draws_history(self):
        """A cash account trued up TODAY still has a real past (N-44).

        PLAIN anchored at index 5, today at index 5 -- the shape both real
        production cash accounts are in, and the one the retired cash gate hurt
        most: it equalled the current index, so ``/savings`` drew ZERO history
        points on an account with four months of recorded activity.  The fold
        replays every assertion, so the window is the full
        ``_TREND_HISTORY_PERIODS`` tail.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 5)]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert [p.period_index for p in window] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert current_index == 5
        assert honest_start == 0

    def test_tail_capped_at_history_cap(self):
        """The history tail is capped by ``_TREND_HISTORY_PERIODS``.

        PLAIN anchored at index 0, today at index 9, periods 0..12.  The
        honest start (0) loses to the cap (9 - 6 = 3), so the tail is
        indices 3..8 (6 points) and ``current_index`` is 6.  With cash no
        longer gating, the cap is what bounds the tail for a loan-free user
        -- so this is also the test that would fail if the fold's history
        were allowed to run all the way back.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(13)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 0)]

        window, current_index, _ = build_trend_periods(
            accounts, periods, periods[9], {},
        )

        # 6 history points (indices 3..8) then today (9) onward -- the cap
        # binds even though the cash anchor (index 0) is further back.
        assert current_index == 6
        assert window[0].period_index == 3

    def test_no_current_period_is_empty(self):
        """No current period yields an empty window and indices 0."""
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [self._account(AccountProjectionKind.PLAIN, 0)]

        assert build_trend_periods(accounts, periods, None, {}) == ([], 0, 0)

    def test_an_investment_anchor_does_not_gate_the_history_start(self):
        """Neither an INVESTMENT's anchor nor a cash one shortens the history.

        A PLAIN account is anchored at index 1 and an INVESTMENT at index 4,
        today at index 5.  Neither gates: an investment is defined pre-anchor
        (reverse-projected) and cash is a fold over its own assertions, so the
        honest start is 0 and the window runs 0..9.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1),
            self._account(AccountProjectionKind.INVESTMENT, 4),
        ]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert window[0].period_index == 0
        assert current_index == 5
        assert honest_start == 0

    def test_two_cash_anchors_neither_bounds_the_history(self):
        """Two cash accounts, two different anchors, neither gates (N-44).

        PLAIN at index 1 and PLAIN at index 3, today at index 5.  The retired
        rule took the LATER anchor (3) so that no period could miss a cash
        balance; the fold gives BOTH accounts a real balance at every period,
        so there is nothing to miss and the window runs 0..9.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1),
            self._account(AccountProjectionKind.PLAIN, 3),
        ]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert window[0].period_index == 0
        assert current_index == 5
        assert honest_start == 0

    def test_a_modelled_only_set_still_draws_no_history(self):
        """An investment / property-only set draws NO backward run.

        Nothing in the set has a RECORDED past: an investment's pre-anchor
        values are a reverse growth projection and a property's are a flat
        anchor carry, so a backward run would be modelled figures presented as
        actual history.  The no-history default therefore survives the cash
        arm's removal -- and this is the test that fails if a cash account is
        dropped from the gate loop entirely instead of participating in it
        unconstrained, because a LOAN-FREE cash user would then land here too.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.INVESTMENT, 1),
            self._account(AccountProjectionKind.APPRECIATING, 2),
        ]

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[5], {},
        )

        assert [p.period_index for p in window] == [5, 6, 7, 8, 9]
        assert current_index == 0
        assert honest_start == 5

    def test_a_loans_recorded_start_gates_history(self):
        """A loan's recorded start gates the history past the cash.

        A PLAIN account is anchored at index 1, but an AMORTIZING loan's
        record starts in period 5 (today at index 7).  Before it the loan's
        map carries its origination principal flat (not its real past), so
        the loan gates the honest start at index 5 (ruling R-R111; this was
        ``test_loan_schedule_start_gates_history``, when the gate read the
        loan's first schedule row, until plan step recurrence:R16-c-2).
        Window indices 5..9, ``current_index`` 2 (indices 5, 6).  Without the
        loan gate the honest start would be 0 (cash constrains nothing since
        plan step X-c2b2), the cap would bind at 7 - 6 = 1 and
        ``current_index`` would be 6 -- so this pins the loan gate, which is
        the ONLY arm left.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1, account_id=1),
            self._account(AccountProjectionKind.AMORTIZING, 0, account_id=8),
        ]
        loan_starts = {8: self._recorded_start(5, periods)}

        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[7], loan_starts,
        )

        assert honest_start == 5
        assert current_index == 2
        assert window[0].period_index == 5

    def test_an_amortizing_account_with_no_loan_terms_does_not_gate(self):
        """An amortizing account absent from ``loan_starts`` does not gate history.

        The dashboard passes a start for every CONFIGURED loan
        (``_orchestrator._build_trend_window``, ruling R-R112), so the one
        amortizing account absent here is one whose loan terms were never set
        up: it has no ``LoanParams``, so it has no recorded start to pass.
        PLAIN anchored at index 1, that account, today at index 7: nothing
        gates, so the honest start is 0 and the ``_TREND_HISTORY_PERIODS`` cap
        bounds the tail at 7 - 6 = 1.  Window indices 1..9, ``current_index``
        6.

        This was ``test_empty_loan_schedule_does_not_gate`` until plan step
        recurrence:R16-c-2: the dashboard passed no start for a PAID-OFF loan
        (an empty schedule), and this pinned that exception.  Ruling R-R112
        deleted it -- every loan gates from its recorded start, paid off or not
        -- and the opposite assertion, through a real paid-off loan, is
        ``TestTheTrendWindowGatesEachLoanFromItsRecordedStart``.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_projection import AccountProjectionKind
        from app.services.savings_dashboard_service._net_worth import (
            build_trend_periods,
        )
        periods = [self._period(i) for i in range(10)]
        accounts = [
            self._account(AccountProjectionKind.PLAIN, 1, account_id=1),
            self._account(AccountProjectionKind.AMORTIZING, 0, account_id=8),
        ]
        window, current_index, honest_start = build_trend_periods(
            accounts, periods, periods[7], {},
        )

        assert honest_start == 0
        assert current_index == 6
        assert window[0].period_index == 1


class TestTheTrendWindowGatesEachLoanFromItsRecordedStart:
    """Rulings R-R111 and R-R112 at the dashboard, through real loans.

    ``_orchestrator._build_trend_window`` hands the net-worth gate the day each
    configured loan's record starts (``LoanTerms.recorded_start``), and EVERY
    loan gates from it, paid off or not.  Before its record starts the ledger
    holds nothing but the loan's origination principal, so the trend would draw
    that principal as the loan's balance through a past the app never recorded.
    Until plan step recurrence:R16-c-2 the gate read each loan's schedule rows:
    the earliest row's date stood in for the start (ruling R-R111 replaced it)
    and a loan whose schedule was EMPTY -- paid off -- did not gate at all
    (ruling R-R112 deleted the exception; this class replaces the monkeypatched
    orchestrator test that pinned it, on review 4's advice to grade a real loan
    through the dashboard's own window).

    Both tests run on :func:`seed_periods` (paydays every 14 days from
    2026-01-02, so the periods end Jan 15, Jan 29, Feb 12, Feb 26, Mar 12,
    Mar 26, Apr 9, Apr 23, May 7, May 21) with today frozen on Apr 20, in the
    period ending Apr 23 (index 7).  Nothing else gates: the checking account
    constrains nothing (finding N-44), so an ungated loan leaves the history at
    the six-period cap, periods 1..6.
    """

    @staticmethod
    def _series(seed_user):
        """The /savings page's net-worth trend, built the way the page builds it."""
        return savings_dashboard_service.compute_dashboard_data(
            BalanceContext.build(seed_user["user"].id),
        )["net_worth"].series

    def test_a_paid_off_loan_gates_from_its_recorded_start(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Ruling R-R112's own example: no $20,000 swing that never happened.

        A $20,000.00 loan from 2024-01-22, due the 22nd, first tracked on
        2026-04-01 at $0.00 (already paid off), beside the owner's $1,000.00
        checking.  Before Apr 1 the ledger holds only the $20,000.00
        origination principal, so an ungated trend read -$19,000.00 at every
        history point from Jan 29 to Mar 26 and then +$1,000.00.  Gated from
        its recorded start, the history begins in the period ending Apr 9 (the
        first whose end reaches Apr 1) and every point reads $1,000.00.

        The loan's schedule is EMPTY (the precondition below), which is the
        case the gate used to skip: restore that exception in
        ``_build_trend_window`` and this test fails with the -$19,000.00 run.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.balance_at._resolution import resolve_loan_bundle
        from tests._test_helpers import create_loan_account, freeze_today

        freeze_today(monkeypatch, date(2026, 4, 20))
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Paid-off car",
                principal=Decimal("20000.00"), term=60,
                origination_date=date(2024, 1, 22), payment_day=22,
                tracked_balance=Decimal("0.00"), tracked_from=date(2026, 4, 1),
            )
            resolved = resolve_loan_bundle(
                loan, BalanceContext.build(seed_user["user"].id),
            )
            assert resolved.state.schedule == [], (
                "precondition: a paid-off loan's schedule is empty -- the case "
                "the gate skipped until ruling R-R112"
            )

            series = self._series(seed_user)

            assert [point.end_date for point in series.periods] == [
                date(2026, 4, 9), date(2026, 4, 23),
                date(2026, 5, 7), date(2026, 5, 21),
            ]
            assert series.current_index == 1
            assert series.net == [Decimal("1000.00")] * 4

    def test_a_tracked_loan_gates_from_its_tracking_start_not_its_first_row(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Ruling R-R111: the gate reads the tracking start, not the first row's date.

        A $20,000.00 loan from 2024-01-22, due the 22nd, first tracked on
        2026-03-01 at $15,000.00 and still owing.  Its record starts Mar 1, so
        the history begins in the period ending Mar 12.  The stand-in the gate
        read until plan step recurrence:R16-c-2 -- the first schedule row, the
        Mar 22 installment -- would have begun it in the period ending Mar 26,
        and ignoring the tracking start (reading the origination) would leave
        the history at the cap, beginning Jan 29.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_loan_account, freeze_today

        freeze_today(monkeypatch, date(2026, 4, 20))
        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Tracked car",
                principal=Decimal("20000.00"), term=60,
                origination_date=date(2024, 1, 22), payment_day=22,
                tracked_balance=Decimal("15000.00"),
                tracked_from=date(2026, 3, 1),
            )

            series = self._series(seed_user)

            assert series.periods[0].end_date == date(2026, 3, 12)
            assert series.current_index == 3
