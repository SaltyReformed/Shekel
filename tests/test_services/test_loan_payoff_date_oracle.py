"""C8b oracle: the derived payoff date is a fold to zero.

Plan step C8b (``docs/audits/balance_architecture/README.md``).  The seam's
``balance_at.loan_payoff_date`` folds a loan's forward plan from its
confirmed-present seed and returns the date the balance reaches ``0.00`` -- the
DERIVED payoff the arc substitutes for the blind-schedule copies
(``LoanState.payoff_date``, ``RecurrenceRule.end_date``).  This proves it two
ways:

* :class:`TestPlanPayoffDate` -- the pure fold (``_plan.plan_payoff_date``) on
  hand-built plans, so the reaches-zero / retired-seed / negative-amortization /
  due-order rules are pinned to arithmetic anyone can check.
* :class:`TestLoanPayoffDateSeam` -- the seam entry against the resolver's OWN
  committed payoff (``compute_payoff_scenarios``, an independent producer) on real
  loans, so a HEALTHY or OVERPAYING loan's derived payoff EQUALS the one shown
  today (those cutovers at C8c move no baseline) and a standing extra beats the
  contractual date (the fold-to-zero, not ``plan[-1].date``).

**Baseline parity is a healthy/overpaying claim, NOT universal.** An UNDERPAYING
loan (a balance behind the contractual schedule) clears a few months PAST the
contractual date -- the ESTIMATED tail's post-contractual extension (C8c,
:class:`TestPayoffTailExtension`) -- so it derives a LATER date than the
resolver's ``is_last_month``-forced contractual one (the deliberate move C8c
makes).  Only a drift too severe to clear within the extension folds to ``None``.
The PURE fold-to-zero on a truncated plan (``plan_payoff_date`` handed a plan with
no further installments) still returns ``None``, pinned below; the drift that
produces an underpayment is what C7's payment-drift warning surfaces.

The producer is ADDITIVE and UNWIRED at C8b -- only this oracle reads it.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, RecurrencePatternEnum
from app.extensions import db
from app import ref_cache
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_at,
    loan_loaders,
    loan_payment_service,
    loan_resolver,
)
from app.services.balance_at._positions import loan_payoff_date
from app.services.balance_at._plan import PlannedPayment, plan_payoff_date
from app.services.loan_resolution import contractual_schedule_from_origination
from app.services.resolution_context import BalanceContext
from app.utils.dates import add_months
from tests._test_helpers import (
    create_loan_account,
    insert_trueup_event,
    loan_params_for,
)

_PRINCIPAL = Decimal("300000.00")
_RATE = Decimal("0.06000")
_TERM = 360


def _payment(due, cash, *, rate="0.00", escrow="0.00", effective=None):
    """Build one :class:`PlannedPayment` for the pure-fold unit tests."""
    return PlannedPayment(
        due_date=due,
        effective_date=effective if effective is not None else due,
        cash=Decimal(cash),
        escrow=Decimal(escrow),
        annual_rate=Decimal(rate),
        is_estimated=False,
    )



class TestPlanPayoffDate:
    """The pure fold-to-zero, on hand-built plans (arithmetic anyone can check)."""

    def test_folds_to_zero_returns_the_clearing_installments_due_date(self):
        """The DUE date of the first payment whose running balance hits zero.

        At 0% interest every dollar is principal.  Seed $1000: after a $600
        payment the balance is $400; the next $600 payment clears the $400
        (principal capped at the balance), so the loan is paid off on the SECOND
        installment's due date.
        """
        seed = Decimal("1000.00")
        plan = [
            _payment(date(2027, 1, 1), "600.00"),
            _payment(date(2027, 2, 1), "600.00"),
        ]
        # 1000 - 600 = 400 (2027-01-01); 400 - 400 = 0 (2027-02-01).
        assert plan_payoff_date(seed, plan) == date(2027, 2, 1)

    def test_folds_in_due_order_not_input_order(self):
        """The fold sorts by due date, so payoff keys on the contract, not input.

        Given a $900 payment due 01-01 and a $600 payment due 02-01, in REVERSE
        input order: due-order folds 900 first (1000 -> 100), then 600 (100 -> 0)
        on 02-01.  Folding in input order would clear it on 01-01 instead -- so
        asserting 02-01 proves the due-order sort.
        """
        seed = Decimal("1000.00")
        plan = [
            _payment(date(2027, 2, 1), "600.00"),
            _payment(date(2027, 1, 1), "900.00"),
        ]
        # Due order: 1000 - 900 = 100 (01-01); 100 - 100 = 0 (02-01).
        assert plan_payoff_date(seed, plan) == date(2027, 2, 1)

    def test_already_retired_seed_returns_none(self):
        """A seed at or below zero has no forward crossing -- ``None``, not a date.

        A retired loan owes nothing at the projection seed; the first planned
        payment must NOT be mistaken for a payoff (the caller badges it via
        ``is_retired`` instead).
        """
        plan = [_payment(date(2027, 1, 1), "600.00")]
        assert plan_payoff_date(Decimal("0.00"), plan) is None
        assert plan_payoff_date(Decimal("-5.00"), plan) is None

    def test_negative_amortization_never_reaches_zero_returns_none(self):
        """A payment below the period interest grows the balance -- ``None``.

        Seed $1000 at 12%/yr accrues $10.00 interest a month; a $5.00 payment
        leaves principal -$5.00, so the balance rises to $1005 and never clears.
        """
        seed = Decimal("1000.00")
        plan = [
            _payment(date(2027, m, 1), "5.00", rate="0.12000")
            for m in range(1, 7)
        ]
        assert plan_payoff_date(seed, plan) is None

    def test_empty_plan_returns_none(self):
        """No payments means the balance never moves to zero -- ``None``."""
        assert plan_payoff_date(Decimal("1000.00"), []) is None

    def test_truncated_plan_that_never_clears_returns_none(self):
        """The PURE fold on a plan whose installments run out above zero -- ``None``.

        ``plan_payoff_date`` folds exactly the plan it is handed: two $300 payments
        at 0% take $1000 down to $400, then the plan ENDS with the balance still
        positive, so no installment drives it to ``<= 0``.  This is the pure
        function's contract (a plan too short -> ``None``), independent of the
        SEAM's ESTIMATED tail, which extends past the contractual date so a real
        underpaying loan clears there instead (:class:`TestPayoffTailExtension`).
        """
        seed = Decimal("1000.00")
        plan = [
            _payment(date(2027, 1, 1), "300.00"),
            _payment(date(2027, 2, 1), "300.00"),
        ]
        # 1000 - 300 = 700 (01-01); 700 - 300 = 400 (02-01); plan ends above zero.
        assert plan_payoff_date(seed, plan) is None


def _create_loan(seed_user, period, origination_date, *, name):
    """Create a fixed mortgage originating on *origination_date*, anchored to *period*."""
    account = create_loan_account(
        seed_user, db.session, name=name,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=origination_date, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE, anchor_period=period,
    )
    return account, loan_params_for(db.session, account.id)


def _attach_derive_extra(seed_user, loan_account, extra):
    """Attach a derive-from-loan recurring payment carrying a standing extra."""
    user = seed_user["user"]
    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.MONTHLY,
        ),
        day_of_month=1,
    )
    db.session.add(rule)
    db.session.flush()
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        recurrence_rule_id=rule.id,
        name="Mortgage Payment",
        default_amount=Decimal("1.00"),
    )
    template.settings = LoanPaymentSettings(
        derive_from_loan=True, extra_principal=extra,
    )
    db.session.add(template)
    db.session.commit()


def _committed_payoff(loan_params, scenario_id, as_of, extra):
    """Return (committed payoff, pure-contractual payoff) from the resolver.

    The independent reference: ``compute_payoff_scenarios`` computes the payoff via
    ``project_forward`` -- a different code path from the fold's
    ``split_payment_cash`` -- so agreement is meaningful, not tautological.
    """
    ctx_loan = loan_payment_service.load_loan_context(
        loan_params.account_id, scenario_id, loan_params,
    )
    anchor_events = loan_loaders.load_loan_anchor_facts(loan_params)
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params, anchor_events, ctx_loan.payments, ctx_loan.rate_changes,
        ),
        extra_monthly=Decimal("0.00"),
        as_of=as_of,
        confirmed_view=loan_payment_service.confirmed_loan_view(
            loan_params, scenario_id, as_of,
        ),
        extra_principal=extra,
    )
    return (
        scenarios.payoff_date_committed,
        scenarios.original_forward[-1].payment_date,
    )


def _current_period(periods, today):
    """The seeded pay period containing *today*."""
    return next(
        period for period in periods
        if period.start_date <= today <= period.end_date
    )


class TestLoanPayoffDateSeam:
    """The seam entry on real loans, against the resolver's own committed payoff."""

    def test_healthy_loan_matches_resolver_committed_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A healthy loan's derived payoff EQUALS the resolver's -- baseline unmoved.

        The loan originates at the current period (clean past), so the fold and
        the committed schedule agree on the timeline.  ``loan_payoff_date`` (fold
        to zero) must equal ``payoff_date_committed`` (the value the loan card
        shows today), so the C8c cutover moves nothing.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Healthy",
            )
            scenario_id = seed_user["scenario"].id
            committed_payoff, _ = _committed_payoff(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) == committed_payoff

    def test_standing_extra_matches_resolver_and_beats_contractual(
        self, app, seed_user, seed_periods_today,
    ):
        """With a standing extra the payoff is the resolver's committed date --

        and STRICTLY BEFORE the pure-contractual date, proving the fold-to-zero
        reaches the accelerated payoff rather than returning ``plan[-1].date`` (the
        last contractual installment).  Depends on C8a: the ESTIMATED tail now
        folds the extra past the shadow horizon.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Extra",
            )
            _attach_derive_extra(seed_user, account, Decimal("500.00"))
            scenario_id = seed_user["scenario"].id
            committed_payoff, contractual_payoff = _committed_payoff(
                loan_params, scenario_id, today, Decimal("500.00"),
            )
            assert committed_payoff < contractual_payoff, (
                "extra did not accelerate payoff; the test would be vacuous"
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            derived = balance_at.loan_payoff_date(account, ctx)
            assert derived == committed_payoff
            assert derived < contractual_payoff

    def test_retired_loan_returns_none(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan trued up to $0 has no forward payoff -- ``None`` (badge via is_retired).

        The seed (``projection_seed`` = the ledger-confirmed $0.00) is not
        positive, so there is no crossing to date; ``loan_payoff_date`` returns
        ``None`` rather than inventing a future date from the contractual tail.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Retired",
            )
            insert_trueup_event(loan_params, Decimal("0.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) is None

    def test_not_yet_originated_loan_folds_to_contractual_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan not yet originated folds its whole contractual timeline to payoff.

        With no confirmed past and no extra, its plan is the pure contractual
        schedule, so the derived payoff is that schedule's last installment -- the
        contractual payoff, a future date.
        """
        with app.app_context():
            today = date.today()
            future = next(
                period for period in seed_periods_today
                if period.start_date > today
            )
            account, loan_params = _create_loan(
                seed_user, future, future.start_date, name="Payoff Future",
            )
            assert loan_params.origination_date > today
            rate_changes = loan_loaders.load_rate_changes(account.id)
            contractual = contractual_schedule_from_origination(
                loan_params, rate_changes,
            )
            expected = contractual[-1].payment_date

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) == expected

    def test_non_loan_account_raises(self, app, seed_user):
        """A non-loan account is a caller error -- fail loud, like ``positions``."""
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            with pytest.raises(ValueError, match="requires a configured loan"):
                balance_at.loan_payoff_date(seed_user["account"], ctx)


class TestPayoffTailExtension:
    """C8c (N-16): the fold pays past the contractual date until the loan clears."""

    def test_underpaying_loan_clears_in_the_extension(
        self, app, seed_user, seed_periods_today,
    ):
        """A loan behind the schedule clears a few months PAST the contractual date.

        A true-up leaves the balance $500 above where the contractual schedule
        expects it (an underpayment / drift), so the fold does not reach zero at
        the contractual installment.  Before C8c that residue made
        ``loan_payoff_date`` return ``None``; the post-contractual extension now
        clears it, so it derives a REAL date -- and a LATER one than the resolver's
        ``is_last_month``-forced contractual payoff (the deliberate N-16 move).
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Underpaid",
            )
            # $500 behind the contractual schedule -- the underpayment residue.
            insert_trueup_event(loan_params, _PRINCIPAL + Decimal("500.00"))
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            committed_payoff, _ = _committed_payoff(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            derived = balance_at.loan_payoff_date(account, ctx)
            assert derived is not None, (
                "an underpaying loan must clear in the post-contractual extension, "
                "not report None (finding N-16)"
            )
            # The $500-behind balance compounds at 6% over the ~30-year term to a
            # residue of ~$500 * 1.005**360 ~= $3,010 at the contractual payoff,
            # which two level-payment (~$1,798 P&I) extension installments clear
            # (~$1,783 principal each), so payoff lands two months past the
            # resolver's forced contractual date.
            assert derived == add_months(committed_payoff, 2), (
                f"underpaid payoff {derived} should be two extension installments "
                f"past the resolver's forced contractual {committed_payoff}"
            )

    def test_severe_underpayment_never_amortizes_returns_none(
        self, app, seed_user, seed_periods_today,
    ):
        """A balance so high the level payment cannot cover its interest -- ``None``.

        Trued up to $400k against a $300k contractual P&I (~$1,798), the monthly
        interest (~$2,000) EXCEEDS the payment, so the balance grows every month
        and the extension never clears it: the fold reports ``None`` rather than
        inventing a payoff.  (The resolver's ``is_last_month`` would force a date
        by absorbing the grown balance in a phantom final payment; the fold does
        not.)
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Negam",
            )
            insert_trueup_event(loan_params, Decimal("400000.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_payoff_date(account, ctx) is None

    def test_healthy_loan_is_not_resurrected_past_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """A healthy loan's balance stays $0 past its payoff -- the extension is inert.

        The extension appends installments PAST the contractual date; on a healthy
        loan (already zero there) they must fold to no-ops.  A year past the
        contractual payoff the balance is still ``$0.00`` -- the extension neither
        moves the payoff (still the contractual date, ``test_healthy_loan...``) nor
        resurrects a paid-off balance.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Payoff Healthy2",
            )
            scenario_id = seed_user["scenario"].id
            _, contractual_payoff = _committed_payoff(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            past_payoff = add_months(contractual_payoff, 12)
            assert balance_at.balance_at(account, ctx, past_payoff) == (
                Decimal("0.00")
            )


class TestPayoffCutover:
    """Plan C8d: the seam's figures carry the DERIVED payoff, derived ONCE.

    The oracle above grades the producer.  These grade the CUTOVER -- that the
    figure every consumer reads (:attr:`~app.services.balance_at.LoanFigures.payoff_date`,
    the single funnel behind the loan card's chip, the /savings cockpit and
    Horizon, and the equity chart's axis) IS that producer's answer and not the
    resolver's schedule endpoint it used to be.
    """

    def test_figures_payoff_is_the_derived_payoff(
        self, app, seed_user, seed_periods_today,
    ):
        """``LoanFigures.payoff_date`` equals ``loan_payoff_date`` on a healthy loan."""
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Cutover Healthy",
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            assert figures is not None
            assert figures.payoff_date == balance_at.loan_payoff_date(
                account, ctx,
            )

    def test_figures_payoff_leaves_the_schedule_endpoint_behind(
        self, app, seed_user, seed_periods_today,
    ):
        """On an UNDERPAYING loan the figure is the fold's date, not the schedule's.

        The control that makes the test above non-vacuous: for a healthy loan the
        two producers agree, so equality alone cannot show which one is wired.
        A loan trued up $500 above the contractual schedule clears two
        installments LATER than the resolver's ``is_last_month``-forced
        contractual payoff (the C8c extension), so the two answers differ and the
        figure must carry the fold's.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Cutover Underpaid",
            )
            insert_trueup_event(loan_params, _PRINCIPAL + Decimal("500.00"))
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            committed_payoff, _ = _committed_payoff(
                loan_params, scenario_id, today, Decimal("0.00"),
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            assert figures is not None
            assert figures.payoff_date == add_months(committed_payoff, 2)
            assert figures.payoff_date != committed_payoff, (
                "the figure still reports the resolver's committed schedule "
                "endpoint, so the cutover is not wired"
            )

    def test_a_retired_loan_reports_no_payoff_but_is_retired(
        self, app, seed_user, seed_periods_today,
    ):
        """A retired loan's figure is ``None`` + ``is_retired`` -- the badge state.

        Finding B-20: it used to report the loan's ORIGINATION date as a
        "payoff" (the resolver's empty-schedule fallback), which is a past date
        presented as a future event.  The two fields together are what let the
        chip badge "Paid off" instead of inventing a date.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, loan_params = _create_loan(
                seed_user, current, current.start_date, name="Cutover Retired",
            )
            insert_trueup_event(loan_params, Decimal("0.00"))
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            assert figures is not None
            assert figures.payoff_date is None
            assert figures.is_retired is True
            assert figures.payoff_date != loan_params.origination_date

    def test_the_payoff_is_derived_once_per_read_pass(
        self, app, seed_user, seed_periods_today,
    ):
        """Reading the figures twice folds the plan to zero ONCE.

        The memo (:meth:`~app.services.resolution_context.BalanceContext.loan_payoff`)
        exists because a single ``/savings`` render asks two callers for the same
        loan's figures.  Proven by inspecting the pass's memo directly: after
        ``loan_figures`` runs, the slot keyed by the REAL deriver is populated, so
        the second read is served from it rather than re-folding.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Cutover Memo",
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            assert not ctx._payoffs, "the memo starts empty"  # pylint: disable=protected-access
            first = balance_at.loan_figures(account, ctx)
            assert first is not None

            # The slot the seam's own deriver filled -- the wiring claim.
            key = (account.id, loan_payoff_date)
            assert key in ctx._payoffs  # pylint: disable=protected-access
            assert ctx._payoffs[key] == first.payoff_date  # pylint: disable=protected-access

            second = balance_at.loan_figures(account, ctx)
            assert second is not None
            assert second.payoff_date == first.payoff_date

    def test_a_second_deriver_gets_its_own_slot(
        self, app, seed_user, seed_periods_today,
    ):
        """A DIFFERENT deriver never reads the first one's answer.

        The memo keys on ``(account, derive)``, so the "every caller must pass the
        same function" rule is structural rather than a note in a docstring.  Keyed
        by account alone, this sentinel would silently receive the real payoff --
        the exact silent-wrong-answer shape ``resolution_context`` exists to
        replace.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Memo Slots",
            )
            sentinel = date(1999, 12, 31)

            ctx = BalanceContext.build(seed_user["user"].id)
            real = balance_at.loan_figures(account, ctx)
            assert real is not None
            assert real.payoff_date != sentinel

            assert ctx.loan_payoff(
                account, lambda _account, _ctx: sentinel,
            ) == sentinel

    def test_a_fresh_pass_derives_again(
        self, app, seed_user, seed_periods_today,
    ):
        """The memo is scoped to ONE read pass, never cached across passes.

        The counterpart to the test above, and the property that lets a WRITE
        path (``loan_recurrence_sync``) build a context mid-mutation and see the
        post-write loan: a new context derives from scratch.
        """
        with app.app_context():
            today = date.today()
            current = _current_period(seed_periods_today, today)
            account, _ = _create_loan(
                seed_user, current, current.start_date, name="Cutover Fresh",
            )

            derived = []

            def _counting(account_arg, ctx_arg):
                derived.append(account_arg.id)
                return balance_at.loan_payoff_date(account_arg, ctx_arg)

            first_ctx = BalanceContext.build(seed_user["user"].id)
            first_ctx.loan_payoff(account, _counting)
            first_ctx.loan_payoff(account, _counting)
            assert derived == [account.id], "one pass must derive once"

            second_ctx = BalanceContext.build(seed_user["user"].id)
            second_ctx.loan_payoff(account, _counting)
            assert derived == [account.id, account.id], (
                "a NEW pass must derive again -- the memo is per-pass, and a "
                "write path depends on that to see its own writes"
            )
