"""
Shekel Budget App -- Balance-at-T seam parity tests.

Structural-equality coverage for :mod:`app.services.balance_at`, the Level 1
balance seam.  The seam is new code that nothing calls yet (Commit 2 of the
balance-architecture plan), so these tests prove only ONE thing per account
kind: the seam's internal input assembly reproduces the EXISTING producer
path exactly.  Each test asserts ``seam output == existing producer called
with the same manually-assembled inputs`` -- so they need no hand-computed
money values for the parity itself (the kernel and resolver already own
those), only for the few sanity checks that confirm the right dispatch
branch ran.

**Parity is not the whole file, and must not be.**  A structural-equality test
proves two producers AGREE; it says nothing about whether either is right, and
where the two share code it degenerates to ``f(x) == f(x)``.  So the classes that
pin the seam's BEHAVIOR against an independent oracle -- the B-9 fix (an unpaid
overdue installment pays nothing down), the confirmed-present seeding
(:class:`TestForwardFoldSeedsFromTheConfirmedPresent`) -- are load-bearing in a
way the parity classes are not: they are what stands between a shared-code defect
and production.  The forward fold's exact ARITHMETIC is pinned by hand-computed
oracle in ``test_loan_plan_forward_oracle.py`` (fold values) and
``test_loan_plan_assembly.py`` (plan assembly); do not re-prove it here with a
producer.  Read the plan's Section 7.2 before adding a test that proves a producer
with a producer.

The five account kinds are seeded with the suite's established factory
patterns: a Checking (PLAIN), an HYSA + InterestParams (INTEREST), a
Mortgage + LoanParams + origination event/rate (AMORTIZING), a 401(k) +
InvestmentParams (INVESTMENT), and a Property + AssetAppreciationParams
(APPRECIATING).  ``seed_periods_today`` places today in period index 4 so
``get_current_period`` is deterministic and an account can be anchored in
the past (period 2) or at the current period (period 4).
"""

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, CalcMethod, DeductionTiming
from app.models.transaction import Transaction
from app.services import (
    growth_engine,
    account_service,
    anchor_service,
    balance_at,
    cash_ledger,
    income_service,
    pay_period_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.projection_inputs import (
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
)
from app.services.savings_dashboard_service._data import _load_account_params
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.money import round_money
from app.services.balance_at import BalanceContext
from app.services.balance_at._inputs import (
    _contribution_inputs_for_account,
    _contribution_inputs_for_accounts,
)
from app.services.balance_at._resolution import (
    configured_loan,
    resolved_loan,
)
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    create_hysa_account,
    create_loan_account,
    create_settled_transfer,
    insert_trueup_event,
    loan_params_for,
    make_appreciating_account,
    make_investment_account,
    make_salary_profile,
    posted_loan_balance_at,
    restamp_latest_assertion,
    restamp_opening_assertion,
    settle_instant_on,
)


def _no_baseline(user_id):
    """Return a BalanceContext with NO baseline scenario.

    The honest stand-in for the bare ``None`` scenario the seam entries used to
    take: ``get_baseline_scenario`` returns ``None`` for a fresh user, and the
    seam's fail-loud contract turns that into a ``ValueError`` rather than a deep
    ``AttributeError`` on ``scenario.id`` (or a silent ``$0``).  The guard now
    lives on the context, so the no-baseline state is expressed by a context
    carrying ``scenario=None`` -- not by passing ``None`` where a context goes.
    """
    return BalanceContext(user_id=user_id, scenario=None, as_of=date.today())


def _make_hysa(db, seed_user, anchor_period, balance):
    """Create an HYSA account (INTEREST) via the shared factory (5% APY daily).

    Thin adapter over :func:`tests._test_helpers.create_hysa_account` that
    keeps this suite's ``(db, seed_user, ...)`` call convention while the HYSA
    construction itself lives in the one shared home.
    """
    return create_hysa_account(seed_user, db.session, anchor_period, balance)


def _make_mortgage(
    db, seed_user, anchor_period, balance, origination_date, name="Mortgage",
):
    """Create a Mortgage (AMORTIZING) through the shared loan factory.

    ``name`` is parameterised so a test can seed two mortgages in one user
    without colliding on the ``(user_id, name)`` unique constraint.  Returns
    ``(account, loan_params)`` so a caller can append a trueup event (e.g. to
    drive the loan to paid-off / empty-schedule, or to re-anchor it today).

    Delegates to :func:`create_loan_account` rather than re-rolling the
    account-factory + ``LoanParams`` + rate block: the hand-rolled copy this
    replaces never opened the loan's genesis posting ledger, so every mortgage in
    this suite ran on the no-ledger fallback -- a path production never takes.
    """
    acct = create_loan_account(
        seed_user, db.session, name=name, principal=balance,
        rate=Decimal("0.06500"), term=360,
        origination_date=origination_date, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE, anchor_period=anchor_period,
    )
    return acct, loan_params_for(db.session, acct.id)


# The paid-loan fixture's own arithmetic.  The divisor is the $2,000.00 of cash
# the payment actually moved -- NOT the loan's ~$1,498.88 contractual P&I -- because
# the ledger splits the REAL cash and its rows are what the schedule carries for the
# confirmed region (``_payoff.py:285``).  At 6%/yr = 0.005/mo on a $250,000.00
# origination:
#   02-01: interest 250000.00 * 0.005 = 1250.00 -> principal  750.00
#          remaining 250000.00 -  750.00 = 249250.00
#   03-01: interest 249250.00 * 0.005 = 1246.25 -> principal  753.75
#          remaining 249250.00 -  753.75 = 248496.25
PAID_LOAN_LAST_CONFIRMED_REMAINING = Decimal("248496.25")
PAID_LOAN_TRUED_UP_TO = Decimal("200000.00")


def _paid_then_trued_loan(seed_user, db_session, periods):
    """Create a loan with two SETTLED payments and a LATER true-up.

    The shape the forward producers exist for, and the one the suite's fixture
    matrix lacked (plan Section 7.4): real settled cash -- so the schedule
    carries CONFIRMED rows at all -- followed by an operator true-up dated after
    the last of them, so the ledger's confirmed balance and those rows'
    ``remaining_balance`` genuinely disagree.  Without the true-up both derive
    from the same ledger and every assertion over them is vacuous.

    Shared by :class:`TestScalarAndMapAgree` (which needs the shape present) and
    :class:`TestForwardFoldSeedsFromTheConfirmedPresent` (which pins its seeding),
    so the two cannot drift on what "a paid loan" means.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        periods: The ``seed_periods`` list (payments land in indices 1 and 3).

    Returns:
        The committed loan :class:`~app.models.account.Account`.
    """
    loan = create_loan_account(
        seed_user, db_session, name="Paid Then Trued",
        principal=Decimal("250000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=date(2025, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
    )
    # Settled payments due 2026-02-01 (period 1) and 2026-03-01 (period 3);
    # both pay periods have begun by the frozen 2026-03-20, so the replay
    # confirms both.  Explicit paid instants keep the fixture off the wall clock
    # (``create_settled_transfer`` otherwise settles at ``now()``).
    for idx, paid in ((1, date(2026, 2, 1)), (3, date(2026, 3, 1))):
        create_settled_transfer(
            seed_user, db_session, seed_user["account"], loan,
            periods[idx], amount=Decimal("2000.00"),
            paid_at=datetime(
                paid.year, paid.month, paid.day, 12, 0, tzinfo=timezone.utc,
            ),
        )
    db_session.commit()
    insert_trueup_event(
        loan_params_for(db_session, loan.id),
        PAID_LOAN_TRUED_UP_TO, date(2026, 3, 15),
    )
    db_session.commit()
    return loan


def _add_flat_deduction(db, profile, account, amount):
    """Add an active flat pre-tax paycheck deduction targeting *account*.

    The growth engine's contribution feed: a flat per-period employee
    contribution into an investment account, picked up by
    :func:`load_active_deductions_for_accounts` (active profile + active
    deduction + ``target_account_id``).  Flushed; the caller commits.
    """
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
    pre_tax_timing = (
        db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
    )
    ded = PaycheckDeduction(
        salary_profile_id=profile.id,
        target_account_id=account.id,
        name=f"Contribution {account.name}",
        amount=amount,
        calc_method_id=flat_method.id,
        deduction_timing_id=pre_tax_timing.id,
        is_active=True,
    )
    db.session.add(ded)
    db.session.flush()
    return ded


class TestBalanceMapCash:
    """``balance_map`` reproduces the kernel cash path (PLAIN / INTEREST)."""

    def test_plain_checking_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A PLAIN checking map equals the kernel called with the same inputs.

        The seam assembles no debt schedule, no investment params, and no
        deductions for a checking account, and supplies the engine gross;
        the result must equal calling
        :func:`net_worth_kernel.build_account_balance_map` directly with
        exactly those inputs.  This proves the seam's internal assembly
        reproduces the existing inputs.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(account, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                account, bctx, periods,
                ContributionInputs(salary_gross_biweekly=gross),
            )

            assert seam is not None
            assert seam == expected
            # No transactions -> the flat $1,000 anchor at every period.
            assert seam[periods[0].id] == Decimal("1000.00")

    def test_interest_hysa_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST (HYSA) map equals the kernel's interest path.

        The HYSA routes through the kernel's interest path -- the cash FOLD
        with :mod:`app.services.balance_at._interest`'s accrual layered on
        (plan step X-c2b2, which moved that layering out of the retired
        ``calculate_balances_with_interest`` and beside the accrual window it
        needs).  The seam must reproduce it, and the accrual means the closing
        balance sits above the flat anchor (proving the interest branch -- not
        the plain fall-through -- ran).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(hysa, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                hysa, bctx, periods,
                ContributionInputs(salary_gross_biweekly=gross),
            )

            assert seam is not None
            assert seam == expected
            # Interest accrues forward, so the last period exceeds the anchor.
            assert seam[periods[-1].id] > Decimal("5000.00")


class TestInterestBeginsAtTheLatestAssertion:
    """Ruling R-L / plan step X-c2a, through the seam rather than the engine.

    The engine-level arithmetic is pinned in
    ``test_balance_calculator_hysa.py``; what these pin is the INPUT the
    kernel derives -- that the accrual window opens at the account's latest
    balance ASSERTION (its ``AccountAnchorHistory`` row's UTC civil day, read
    through the dated source of truth) and not at the anchor PERIOD's start,
    which is the date the code used before and which precedes it by up to a
    full period.  Both readers of that one walk are covered, because the
    balance map and the account-detail "Interest, next 12 mo" chip share it
    (plan finding N-47).

    Every APY here is 5% daily on a 14-day period, so a full period on
    $10,000 earns ``Q(10000 * ((1 + 0.05/365) ** 14 - 1))`` = ``$19.20`` and
    the halves below are visibly less than that.
    """

    @staticmethod
    def _hysa_asserted_on(db, seed_user, anchor_period, balance, day):
        """Build an HYSA whose OPENING assertion instant is pinned to *day*."""
        account = create_hysa_account(
            seed_user, db.session, anchor_period, balance,
        )
        restamp_opening_assertion(
            db.session, account, settle_instant_on(day),
        )
        db.session.commit()
        return account

    def test_the_anchor_period_accrues_only_from_the_assertion_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account opened mid-period earns only that period's remainder.

        The anchor period runs 14 days; the balance is asserted on its 8th
        day, so 7 days of it are already inside the asserted figure and only
        the last 7 accrue:

            Q(10000 * ((1 + 0.05/365) ** 7 - 1)) = Q(9.5915..) = 9.59

        against ``$19.20`` for the whole period -- which is what this same
        fixture produced before ruling R-L, and what the firing control
        (reverting the ``max`` in ``_layer_interest``) restores.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            anchor = periods[0]
            hysa = self._hysa_asserted_on(
                db, seed_user, anchor, Decimal("10000.00"),
                anchor.start_date + timedelta(days=7),
            )

            balances = balance_at.balance_map(hysa, bctx, periods)

            assert balances[anchor.id] == Decimal("10009.59")

    def test_a_later_true_up_moves_the_window_forward(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The LATEST assertion opens the window, not the opening one.

        The account opens in period 0 at $6,000.00 and the user trues it up in
        period 2 (through the production ``stage_anchor_true_up`` path, so the
        ``current_anchor_*`` cache and the history row agree exactly as they
        do in production).  Periods 0 and 1 then precede the newest assertion
        entirely and accrue nothing; period 2 accrues from the true-up day.

        Trued up on period 2's 8th day at $10,000:

            Q(10000 * ((1 + 0.05/365) ** 7 - 1)) = 9.59
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = create_hysa_account(
                seed_user, db.session, periods[0], Decimal("6000.00"),
            )
            anchor_service.stage_anchor_true_up(
                account=hysa,
                new_balance=Decimal("10000.00"),
                anchor_period=periods[2],
                notes="test true-up",
            )
            restamp_latest_assertion(
                db.session, hysa,
                settle_instant_on(periods[2].start_date + timedelta(days=7)),
            )
            db.session.commit()

            balances = balance_at.balance_map(hysa, bctx, periods)

            # Pre-assertion periods are PRESENT and accrue nothing: the fold
            # back-projects the opening over the records it contains (ruling
            # R-I), so they carry the $6,000.00 the account demonstrably held,
            # and the R-L window earns zero across days the true-up covers.
            assert balances[periods[0].id] == Decimal("6000.00")
            assert balances[periods[1].id] == Decimal("6000.00")
            assert balances[periods[2].id] == Decimal("10009.59")

    def test_the_interest_chip_reads_the_same_window(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The account-detail chip follows the assertion clock too (N-47).

        ``interest_by_period_for_account`` and the balance map are ONE walk,
        so the "Interest, next 12 mo" figure cannot keep accruing over days
        the balance map has stopped accruing over.  Same fixture as the first
        test: the anchor period earns the 7-day figure, not the 14-day one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            anchor = periods[0]
            hysa = self._hysa_asserted_on(
                db, seed_user, anchor, Decimal("10000.00"),
                anchor.start_date + timedelta(days=7),
            )
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id)
                .one()
            )

            chip = balance_at.interest_by_period_for_account(
                hysa, bctx, periods,
            )
            balances = balance_at.balance_map(hysa, bctx, periods)

            assert chip[anchor.id] == Decimal("9.59")
            # And the two agree: the chip's accrual IS the balance's premium
            # over the flat asserted figure in that first period.
            assert (
                balances[anchor.id] - Decimal("10000.00") == chip[anchor.id]
            )


class TestBalanceMapLoan:
    """``balance_map`` folds an amortizing loan's per-period balances (C3b3)."""

    def test_pre_first_payment_uses_current_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The map folds: post-anchor holds C flat, pre-anchor reads the ledger.

        The seam folds the loan's SOURCE events
        (:func:`app.services.balance_at.positions`), which step B2 proves equals
        the sum-of-postings reader on every day.  A balance true-up dated today
        re-anchors the resolver, so its schedule is today-forward, and the map
        splits on the date the balance was ASSERTED:

        * A period that had not ended when the true-up landed, and that still
          precedes the first scheduled payment, reports the trued-up
          current_balance ($200,000) held flat -- NEVER the $240,000 original
          principal.
        * A period that ENDED before the true-up reports what the fold knew then:
          the $240,000 opening, undisturbed, because not one payment was ever
          recorded.  The past belongs to the fold, and the trued-up balance is
          not back-projected across it.

        Both halves are verified against the real dev clone, where the Mortgage's
        past periods likewise step down at each recorded event rather than
        carrying today's balance backward.

        This does NOT fence PR #44 / aba0242 (a forward projection seeded with
        ``original_principal``): both periods above have BEGUN, so the fold answers
        them from source events and no forward projection is consulted at all.  That
        fence is STRUCTURAL -- C2b deleted the schedule-only map, C3b3 retired the
        per-period forward map, and C6b deleted the schedule-forward primitives
        entirely; the forward seed is now single-sourced from the opening anchor
        (never ``original_principal``) in ``net_worth_kernel._projection_seed``, so
        there is no seed-argument call site left to police (the W9905 checker that
        once did retired with those primitives at C6b).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            # A true-up to a balance distinct from the $240,000 origination
            # principal, anchored today, makes the schedule today-forward so
            # the early periods are genuinely pre-first-payment AND the
            # current_balance ($200,000) is observably not the principal.
            insert_trueup_event(
                params, Decimal("200000.00"), anchor_date=date.today(),
            )
            db.session.commit()

            schedule = net_worth_kernel.generate_debt_schedules(
                [mortgage], bctx,
            )[mortgage.id]

            seam = balance_at.balance_map(mortgage, bctx, periods)

            assert seam is not None

            anchor_date = date.today()
            first_payment = min(
                row.payment_date for row in schedule.schedule
            )

            # A period still open when the balance was asserted, and before the
            # first scheduled payment: the trued-up balance, held flat.
            post_anchor = [
                p for p in periods
                if anchor_date <= p.end_date < first_payment
            ]
            assert post_anchor, "expected a post-anchor pre-first-payment period"
            assert seam[post_anchor[0].id] == schedule.projection_seed
            # ...which is the trued-up current balance, never the original
            # principal (the PR #44 / aba0242 boundary bug).
            assert schedule.projection_seed == Decimal("200000.00")
            assert schedule.projection_seed != Decimal("240000.00")

            # A period that ENDED before the true-up: the ledger's answer.  The
            # loan opened at $240,000 and no payment was ever recorded, so that is
            # what it owed then.  The $200,000 assertion is dated today and is NOT
            # back-projected over the past.
            pre_anchor = [p for p in periods if p.end_date < anchor_date]
            assert pre_anchor, "expected a pre-anchor period"
            assert seam[pre_anchor[-1].id] == Decimal("240000.00")

    def test_paid_off_empty_schedule_uses_current_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A paid-off loan (empty schedule) holds its current_balance flat.

        A balance trueup to $0 leaves the resolver with an empty schedule and a
        $0 current balance.  The seam's map dispatch routes the loan to the fold
        on ``_resolution.configured_loan`` -- an empty schedule is still a
        CONFIGURED loan, not a fall-through to cash -- and it reports $0 at
        every period.

        This is one half of the pair that makes plan step X-g3b-0's gate change
        safe (the other is
        ``TestBrokenLoanFailsLoud.test_an_amortizing_account_with_no_loan_params_does_NOT_raise``):
        the retired gate tested MEMBERSHIP in a ``debt_schedules`` map, and a
        loan whose schedule is EMPTY is exactly the shape a careless
        reimplementation drops to cash.  ``TestTheLoanGateIsOneQuestion`` below
        asserts the two spellings agree on this shape directly.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            schedule = net_worth_kernel.generate_debt_schedules(
                [loan], bctx,
            )[loan.id]

            seam = balance_at.balance_map(loan, bctx, periods)

            assert seam is not None
            # Paid off -> empty schedule -> $0 current balance everywhere.
            assert schedule.schedule == []
            assert schedule.projection_seed == Decimal("0.00")
            assert seam[periods[0].id] == Decimal("0.00")
            assert seam[periods[-1].id] == Decimal("0.00")

    def test_future_period_reads_the_amortized_schedule_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A future period past the first payment reads the amortized schedule row.

        The map's FORWARD region, pinned by VALUE.  For a period after today whose
        end is past the loan's first scheduled payment, the seam reads the forward
        projection: the schedule's ``remaining_balance`` for the LAST installment
        due by that period's end -- a specific reduced balance, strictly below the
        seed.  Independently recomputes the expected row here (last unconfirmed row
        on-or-before the period end) rather than trusting the producer, so a
        regression in the map's forward SAMPLING (wrong period date) or the walk's
        row SELECTION would fire.  Restores at the seam the after-payment forward
        pin the retired savings dispatcher unit tests carried on synthetic
        schedules.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            # Trued up today -> schedule is today-forward, seed $200,000, so every
            # future period genuinely amortizes DOWN from a known balance.
            insert_trueup_event(
                params, Decimal("200000.00"), anchor_date=date.today(),
            )
            db.session.commit()

            schedule = net_worth_kernel.generate_debt_schedules(
                [loan], bctx,
            )[loan.id]
            seam = balance_at.balance_map(loan, bctx, periods)

            first_payment = min(
                row.payment_date for row in schedule.schedule
                if not row.is_confirmed
            )
            future = [
                p for p in periods
                if p.start_date > bctx.as_of and p.end_date > first_payment
            ]
            assert future, "expected a future period past the first payment"
            fp = future[0]
            # The last unconfirmed installment due by the period's end -- what the
            # forward walk reduces the balance to -- recomputed independently.
            due_by_end = [
                row for row in schedule.schedule
                if not row.is_confirmed and row.payment_date <= fp.end_date
            ]
            assert due_by_end, "expected an installment due by the future period"
            expected = max(
                due_by_end, key=lambda row: row.payment_date,
            ).remaining_balance
            assert seam[fp.id] == expected
            # A real reduction: strictly below the $200,000 seed, still owing.
            assert Decimal("0.00") < expected < schedule.projection_seed
            assert schedule.projection_seed == Decimal("200000.00")


class TestTheLoanGateIsOneQuestion:
    """The seam asks "is this a configured loan?" ONE way (plan step X-g3b-0).

    The scalar (``_kind_correct.balance_at``) has always asked
    ``classify_account(...) is AMORTIZING and resolved_loan(account, ctx) is not
    None``.  The per-period map asked a DIFFERENT expression -- membership in an
    ``_AssembledInputs.debt_schedules`` map the seam built and then discarded the
    values of -- and the forward liability band decomposed the same rule into two
    guard clauses.  The equivalence between the three was recorded in a docstring
    rather than enforced.  X-g3b-0 deleted the bundle and pointed all three at
    one named predicate, ``_resolution.configured_loan``.

    **What these cases lock, stated so the class is not over-trusted.**  The
    SHIPPED side calls the production predicate, so a mutation of it fires here.
    The RETIRED side is rebuilt from ``generate_debt_schedules``, which is still
    live for its other callers -- so the pair also fires if that producer ever
    gains a filter the resolver lacks (dropping a loan with no schedule rows,
    say), which is the drift that would otherwise surface only as a moved
    balance on a screen.  What they do NOT lock is that the map and the band
    still CALL the predicate: that is
    ``TestBalanceMapLoan`` / ``TestBrokenLoanFailsLoud``'s job, by value.
    """

    @staticmethod
    def _both_spellings(account, ctx):
        """Return ``(retired_gate, shipped_gate)`` for *account*.

        The retired spelling is rebuilt exactly as it stood -- the kind test AND
        membership in the schedule map, over the AMORTIZING-filtered subset the
        assembly passed -- because dropping the kind conjunct would compare a
        LOOSER rule and report a false divergence for a ``LoanParams`` row on a
        non-amortizing account, which is a data defect both surfaces are
        supposed to degrade identically.

        The shipped spelling is the PRODUCTION function, not a copy of it, so a
        mutation of the predicate fires these cases rather than passing a
        reimplementation.
        """
        retired = (
            classify_account(account) is AccountProjectionKind.AMORTIZING
            and account.id in net_worth_kernel.generate_debt_schedules(
                [account], ctx,
            )
        )
        return retired, configured_loan(account, ctx) is not None

    def test_a_configured_loan_is_a_loan_under_both(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The ordinary case: a Mortgage with LoanParams takes the loan arm."""
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            loan, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            db.session.commit()

            retired, shipped = self._both_spellings(loan, bctx)

            assert retired is True
            assert shipped is True
            assert retired == shipped

    def test_a_paid_off_loan_with_an_empty_schedule_is_still_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The discriminating shape: configured, but its schedule is EMPTY.

        A trueup to $0 leaves the resolver with no amortization rows at all.
        Both spellings must still say "loan" -- a gate that tested the SCHEDULE
        rather than the RESOLUTION would drop this account onto the cash
        producer, where its balance would become the sum of its payment
        transfers read as income (finding B-3's shape).
        """
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            resolved = resolved_loan(loan, bctx)
            retired, shipped = self._both_spellings(loan, bctx)

            # The shape really is the discriminating one.
            assert resolved is not None
            assert resolved.state.schedule == []
            assert retired is True
            assert shipped is True
            assert retired == shipped

    def test_a_mortgage_with_no_loan_params_is_not_a_loan_under_either(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The other side: Mortgage-typed, terms never entered -> NOT a loan."""
        # Pylint: ``import-outside-toplevel`` -- the shared factory is imported
        # lazily here exactly as the sibling fall-through test does it.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_account_of_type

        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Terms Never Entered",
                anchor_balance=Decimal("150000.00"),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)

            retired, shipped = self._both_spellings(acct, bctx)

            assert classify_account(acct) is AccountProjectionKind.AMORTIZING
            assert retired is False
            assert shipped is False
            assert retired == shipped

    def test_a_non_loan_account_is_not_a_loan_under_either(
        self, app, seed_user, seed_periods_today,
    ):
        """A plain checking account: neither spelling admits it."""
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)

            retired, shipped = self._both_spellings(seed_user["account"], bctx)

            assert retired is False
            assert shipped is False
            assert retired == shipped

    def test_loan_terms_on_a_non_amortizing_account_are_not_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The CLASSIFIER half: ``LoanParams`` alone must not make a loan.

        Without this case the kind conjunct inside ``configured_loan`` is
        unpinned -- deleting it leaves every other case in this class green,
        because none of them carries loan terms on an account the classifier
        refuses.  The resolver half is pinned by the params-less Mortgage above;
        this is its opposite number.

        **The shape is production-reachable, not hypothetical.**  A user can
        edit an existing account type and flip ``has_amortization`` off
        (``routes/accounts/types.py:151-157``); the boundary guard refuses only
        while a linked ledger carries postings, and nothing deletes the
        ``LoanParams`` rows of accounts already on that type.  What is left is
        exactly this: loan terms on an account the classifier no longer calls
        amortizing.  Both spellings must refuse it, or ``positions`` would
        amortize an account whose balance is its transaction rows.
        """
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            loan, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            db.session.commit()
            # It IS a configured loan until the type flag moves.
            assert configured_loan(loan, bctx) is not None

            loan.account_type.has_amortization = False
            db.session.commit()
            db.session.refresh(loan)
            bctx = BalanceContext.build(seed_user["user"].id)

            retired, shipped = self._both_spellings(loan, bctx)

            # The loan terms are still there -- only the classifier moved.
            assert resolved_loan(loan, bctx) is not None
            assert classify_account(loan) is not AccountProjectionKind.AMORTIZING
            assert retired is False
            assert shipped is False
            assert retired == shipped

    def test_the_control_the_equivalence_would_fail(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """THE CONTROL: the comparison can distinguish the two spellings.

        Every case above asserts the pair AGREES, and a pair of expressions that
        could never disagree would pass all four vacuously.  This one PATCHES
        the retired spelling's producer to drop a loan whose schedule is empty
        -- the exact filter a careless reimplementation would add -- and asserts
        ``_both_spellings`` then reports a DISAGREEMENT on the same fixture the
        case above found agreement on.  So the helper can return an unequal
        pair, and ``retired == shipped`` is a real assertion rather than a
        tautology.
        """
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            # Unpatched, the pair agrees (the case above, restated so the
            # patched result below is a change and not a fresh observation).
            assert self._both_spellings(loan, bctx) == (True, True)

            real = net_worth_kernel.generate_debt_schedules

            def _schedule_rows_only(accounts, ctx):
                """The careless filter: a loan with no rows is dropped."""
                return {
                    account_id: schedule
                    for account_id, schedule in real(accounts, ctx).items()
                    if schedule.schedule
                }

            monkeypatch.setattr(
                net_worth_kernel, "generate_debt_schedules",
                _schedule_rows_only,
            )

            # The retired spelling now says "not a loan" while the shipped one
            # still says "loan" -- the divergence the agreement cases exist to
            # catch, shown to be reachable.
            assert self._both_spellings(loan, bctx) == (False, True)


class TestAFeedIsTheSameWhoeverItIsLoadedBeside:
    """One account's contribution feed does not depend on its batch mates.

    The property :func:`._inputs._contribution_inputs_for_accounts` promises in
    its Returns clause, and it needed a test rather than a comment (plan step
    X-g3b-0's second adversarial review).  The gross FETCH is scoped to the SET
    -- it is skipped entirely when no account in it has investment params -- so
    handing the set's gross to every member would make a checking account's feed
    read ``$0`` alone and the user's real gross beside a 401(k).

    Nothing consumes the field on such an account today (the contribution tier
    short-circuits on kind and params first), which is exactly why the batch
    shape is the only place the difference is observable and exactly why the
    single-account case below cannot stand in for it: on a one-element set the
    set-level gate returns ``ZERO`` on its own, so that assertion passes with
    the per-account arm deleted.
    """

    def test_a_checking_accounts_feed_is_identical_alone_and_in_a_batch(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The contract, asserted as the identity it is.

        The 401(k) beside it is what makes the case non-vacuous: it forces the
        gross fetch to happen, so the checking account's ``0`` is the
        per-account arm's doing and not the set gate's.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = pay_period_service.get_all_periods(user_id)
            checking = seed_user["account"]
            roth = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )
            # A real salary, or the gross is $0 for everyone and the case
            # cannot tell the per-account arm from the set-level gate.  The
            # non-vacuity assertion below is what caught its absence.
            make_salary_profile(seed_user, db.session)
            db.session.commit()
            gross = income_service.get_current_gross_biweekly(user_id)

            alone = _contribution_inputs_for_account(checking)
            batched = _contribution_inputs_for_accounts([checking, roth])

            # Non-vacuity: the batch really did fetch a gross to hand out.
            assert gross > Decimal("0")
            assert batched[roth.id].salary_gross_biweekly == gross
            # The arm: the account that cannot consume one carries none, in
            # BOTH shapes -- so the two reads are the same object's worth of
            # facts, which is the Returns clause stated as an assertion.
            assert batched[checking.id].salary_gross_biweekly == Decimal("0")
            assert alone == batched[checking.id]


class TestBalanceMapInvestment:
    """``balance_map`` reproduces the kernel growth path (INVESTMENT)."""

    def test_anchor_at_current_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An investment anchored at the current period equals the kernel.

        The seam assembles the InvestmentParams, the (empty) deductions
        scoped to the params map, and the engine gross, then delegates to
        the growth path; it must equal the kernel called with those same
        manually-assembled inputs.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, current, Decimal("10000.00"),
            )

            params = load_investment_params_for_accounts([inv]).get(inv.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv.id],
            ).get(inv.id, [])
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(inv, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                inv, bctx, periods,
                ContributionInputs(
                    investment_params=params, deductions=deductions,
                    salary_gross_biweekly=gross,
                ),
            )

            assert seam is not None
            assert seam == expected

    def test_anchor_in_past_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An anchor-in-past investment equals the kernel (forward + reverse).

        Anchored at period index 2, the kernel reverse-projects the two
        pre-anchor periods below the anchor and forward-projects the
        post-anchor periods above it.  The seam must reproduce the whole
        map, exercising both projection directions.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            params = load_investment_params_for_accounts([inv]).get(inv.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv.id],
            ).get(inv.id, [])
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(inv, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                inv, bctx, periods,
                ContributionInputs(
                    investment_params=params, deductions=deductions,
                    salary_gross_biweekly=gross,
                ),
            )

            assert seam is not None
            assert seam == expected
            # Reverse projection below the anchor, forward growth above it.
            assert seam[periods[0].id] < seam[periods[2].id]
            assert seam[periods[-1].id] > seam[periods[2].id]


class TestInvestmentGrowthSinceAnchor:
    """``investment_growth_since_anchor`` decomposes growth vs contributions.

    The chip's contract, RESTATED at plan step X-g2b (ruling R-AC): ``growth +
    contributed`` reconciles to the cent with the displayed balance minus the
    balance the user ASSERTED, because both are readings of one replay whose
    two modelled tiers exist only forward of that assertion (rulings R-L / R-Y
    for the accrual, R-Z for the contribution).  It used to reconcile against
    ``balance_map[anchor_period]`` instead -- the anchor PERIOD's end balance --
    which quietly excluded the anchor period's own growth, because the shipped
    producer could not model any.

    These tests are the load-bearing guard against the map and the
    decomposition drifting apart.  The parity check against the raw producer
    that used to live here is GONE: that producer (``_investment``) is the
    incumbent the replay replaced, kept unwired until plan step X-g4, so
    asserting they agree would assert the defect back into place.
    """

    def test_reconciles_with_displayed_balance_change(
        self, app, db, seed_user, seed_periods_today,
    ):
        """growth + contributed == balance_map[current] - the ASSERTED balance.

        Anchored well before the current period so the window spans real
        growth.  The decomposition sums to the modelled balance change EXACTLY,
        so the chip can never disagree with the hero it explains.

        **The right-hand side is the user's own asserted $10,000.00**, not
        ``balance_map[anchor_period]`` (ruling R-AC).  Both modelled tiers start
        at the assertion's own day (rulings R-Y / R-Z), so the cumulative total
        at any date IS the total since the anchor -- while the anchor PERIOD's
        end balance already contains the days between the assertion and that
        period's end, which the old right-hand side silently dropped.  Reading
        the map at ``periods[0]`` here would understate the left side by exactly
        that period's accrual, so this form is the stronger one AND the only
        one the replay makes true.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)
            # Anchor at the first period, strictly before the current one.
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            result = balance_at.investment_growth_since_anchor(
                inv, bctx, current,
            )
            assert result is not None
            growth, contributed = result

            balances = balance_at.balance_map(inv, bctx, periods)
            # The reconciliation identity, to the cent, against the balance the
            # user ASSERTED rather than against another producer's output.
            assert growth + contributed == (
                balances[current.id] - Decimal("10000.00")
            )
            # A growing account with no negative movements grows: growth > 0.
            assert growth > Decimal("0.00")
            # And the anchor period is NOT excluded: its own end balance is
            # already above the assertion, which is what ruling R-Y bought and
            # what makes the identity above differ from the retired one.
            assert balances[periods[0].id] > Decimal("10000.00")

    def test_the_chip_is_read_where_the_headline_is(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The chip reports through the current period's END, not through today.

        The headline it explains is ``balance_map[current_period]`` -- a
        period-END figure, the convention every net-worth surface has used since
        plan step X-c2b2 -- so a chip read at TODAY would explain a balance the
        page is not showing, by the accrual of the days between (measured $9.65
        to $26.05 on the three real accounts, finding N-81).

        The two dates are made to DISAGREE here (the fixture's current period
        runs past today), so this cannot pass by them coinciding: the chip
        equals the period-end reconciliation and is strictly greater than the
        same decomposition read at today.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            assert current.end_date > date.today()  # the dates differ

            growth, contributed = balance_at.investment_growth_since_anchor(
                inv, bctx, current,
            )
            balances = balance_at.balance_map(inv, bctx, periods)
            assert growth + contributed == (
                balances[current.id] - Decimal("10000.00")
            )
            # Read at TODAY instead and it is strictly smaller -- the days
            # between today and the period's end have not accrued yet.
            at_today = balance_at.balance_at(inv, bctx, date.today())
            assert at_today - Decimal("10000.00") < growth + contributed

    def test_none_when_anchored_at_current_period(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Anchored THIS period -> the chip SHOWS, with that period's own accrual.

        **This arm inverted at plan step X-g2b (ruling R-AC / R-Y).**  The
        shipped producer split its periods on ``period_index > anchor_idx``, so
        an account anchored in the current period had no post-anchor window and
        the page hid the chip.  Ruling R-Y removes that premise: the assertion's
        own day accrues, so such an account HAS earned something -- measured
        $105.26 on the real Roth IRA, $44.95 on the Traditional IRA and $76.59
        on the Empower 401(k) at their anchor periods -- and hiding the chip
        would deny a figure the balance beside it already contains, which is the
        visible contradiction ruling R-K refused to ship.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, current, Decimal("10000.00"),
            )

            result = balance_at.investment_growth_since_anchor(
                inv, bctx, current,
            )
            assert result is not None
            growth, contributed = result
            # It earned its own days, and the balance beside it says the same.
            assert growth > Decimal("0.00")
            assert contributed == Decimal("0.00")
            balances = balance_at.balance_map(inv, bctx, periods)
            assert growth + contributed == (
                balances[current.id] - Decimal("10000.00")
            )

    def test_none_when_current_period_is_none(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No current period -> None (the caller hides the chip)."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            result = balance_at.investment_growth_since_anchor(
                inv, bctx, None,
            )
            assert result is None


class TestBalanceMapProperty:
    """``balance_map`` reproduces the kernel appreciation path (APPRECIATING)."""

    def test_property_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property map equals the kernel's appreciation path.

        A Property classifies APPRECIATING, so the seam supplies
        ``investment_params=None`` (the loader excludes it) and the kernel
        reads the appreciation rate off the account's params backref.  The
        market value compounds forward above the anchor and flat-carries
        backward at the anchor value.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(prop, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                prop, bctx, periods,
                ContributionInputs(salary_gross_biweekly=gross),
            )

            assert seam is not None
            assert seam == expected
            # Forward appreciation above the anchor period.
            assert seam[periods[-1].id] > seam[periods[2].id]
            # Pre-anchor periods flat-carry the asserted market value (ruling
            # R-S: a manually-asserted point-in-time value has no historical
            # basis to compound backward from) ...
            assert seam[periods[0].id] == Decimal("400000.00")
            # ... while the ANCHOR period itself now accrues its own days
            # (ruling R-Y).  These two used to be EQUAL, because the shipped
            # producer split on ``period_index > anchor_idx`` and served the
            # anchor period from the flat cash base -- so a Property earned
            # nothing at all in the period it was valued in, and earned it
            # again from scratch every time the user re-asserted.
            assert seam[periods[2].id] > Decimal("400000.00")


class TestBuildMaps:
    """``build_maps`` reproduces the savings net-worth producer batch build."""

    def test_mixed_set_matches_net_worth_maps(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a mixed account set, build_maps dispatches every account correctly.

        Two oracles, because the kernel dispatch no longer answers loans (C3b3
        moved the AMORTIZING branch into the seam):

        * **Non-loan accounts** (cash / interest / investment / appreciation): the
          batch seam per-id map must equal the direct kernel
          ``build_account_balance_map`` under the orchestrator's manual assembly,
          which locks the batch's input assembly and its deduction-scoping rule
          (both scope to the InvestmentParams map's keys).  The oracle is the
          direct kernel call, NOT the rerouted ``build_account_net_worth_maps``
          (which delegates to ``build_maps`` -- comparing would be tautological).
        * **Loan accounts**: the batch seam map must equal the SINGLE-account seam
          map, proving the batch loop routed the loan to its fold (the shared
          ``configured_loan`` gate admitted it rather than dropping it to the
          cash path).  The loan's
          VALUE correctness is ``TestBalanceMapLoan``, ``TestMultiLoanIsolation``,
          and the B2 fold oracle.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)

            _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))
            make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )

            accounts = (
                db.session.query(Account)
                .filter_by(user_id=user_id, is_active=True)
                .order_by(Account.sort_order, Account.name)
                .all()
            )

            # Assemble the inputs the way the balance_at seam does, so the
            # per-account kernel dispatch below is fed the same inputs
            # build_maps feeds the kernel internally.
            params = _load_account_params(accounts)
            loan_ids = {
                a.id for a in accounts if a.id in params.loan_params_map
            }
            # Deductions + engine gross are no longer on _AccountParams (the
            # seam assembles them); source them from the same shared loaders
            # the seam uses, with its investment-only deduction scoping.
            inv_ids = list(params.investment_params_map.keys())
            deductions_by_account = (
                load_active_deductions_for_accounts(user_id, inv_ids)
                if inv_ids else {}
            )
            salary_gross_biweekly = income_service.get_current_gross_biweekly(
                user_id,
            )
            # Independent oracle for NON-loan accounts: the kernel dispatch the
            # savings net-worth producer ran inline pre-reroute, fed by the
            # orchestrator's manual assembly.  Loans are excluded -- the kernel
            # has no loan branch since C3b3, so it would degrade them to the cash
            # path; they are checked against the single-account seam below.
            expected_by_id = {}
            for account in accounts:
                if account.id in loan_ids:
                    continue
                balances = net_worth_kernel.build_account_balance_map(
                    account, bctx, periods,
                    ContributionInputs(
                        investment_params=params.investment_params_map.get(
                            account.id,
                        ),
                        deductions=deductions_by_account.get(account.id, []),
                        salary_gross_biweekly=salary_gross_biweekly,
                    ),
                )
                if balances is not None:
                    expected_by_id[account.id] = balances

            seam_maps = balance_at.build_maps(accounts, bctx, periods)

            # All five seeded accounts have anchors, so none is omitted.
            assert len(seam_maps) == 5
            # Every NON-loan account: the batch seam == the manual kernel dispatch.
            for acct_id, expected_balances in expected_by_id.items():
                assert seam_maps[acct_id] == expected_balances
            # Every loan: the batch seam == the single-account seam (the batch
            # routed it to the fold, not the cash path).
            for loan_id in loan_ids:
                loan = next(a for a in accounts if a.id == loan_id)
                assert seam_maps[loan_id] == balance_at.balance_map(
                    loan, bctx, periods,
                )

    def test_omits_account_with_no_anchor(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account with no anchor period is omitted from build_maps.

        Mirrors the kernel's ``build_account_balance_map`` returning None
        for a no-anchor account and the net-worth section's ``balances is
        None`` skip.  A stand-in with ``current_anchor_period_id=None`` (and
        no account type, so it classifies PLAIN and the loaders skip it) is
        dropped while the real checking account is kept.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            checking = seed_user["account"]
            no_anchor = SimpleNamespace(
                id=-1, user_id=user_id, account_type=None,
                current_anchor_period_id=None,
            )

            seam_maps = balance_at.build_maps(
                [checking, no_anchor], bctx, periods,
            )

            assert checking.id in seam_maps
            assert no_anchor.id not in seam_maps


class TestBalanceAt:
    """``balance_at`` dispatches to the correct date-granular producer."""

    def test_cash_equals_the_cash_flow_scalar(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a PLAIN account the kind-correct scalar IS the cash-flow scalar.

        Finding N-47's coupling, asserted: ``balance_at``'s PLAIN branch and
        ``cash_balance_at`` reach the same ``_cash_fold`` call, so /savings (the
        kind-correct scalar) and the dashboard (the cash-flow one) cannot answer
        one date two ways.  This was pinned against ``balance_as_of_date`` until
        plan step X-c2b3 deleted it; the property is unchanged and the reference
        is now the SEAM entry a screen actually reads, which is what makes the
        test fire if either branch is re-routed rather than only if the retired
        producer was.

        **The $250.00 row is what gives the equality teeth.**  Without a row the
        account holds only its $1,000.00 assertion, so ANY producer that returns
        the anchor satisfies both sides and the test passes for the wrong
        reason -- measured: replacing the PLAIN branch with a bare
        ``resolve_anchor`` read left it green.  With a row the two figures are
        the anchor MINUS a reservation the anchor read cannot know about, so the
        equality can only hold if both branches folded.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date  # inside a known period
            add_txn(
                db.session, seed_user, periods[4], "Rent", "250.00",
            )
            db.session.commit()

            seam = balance_at.balance_at(account, bctx, as_of)
            expected = balance_at.cash_balance_at(account, bctx, as_of)
            assert seam == expected
            # Non-vacuity: $1,000.00 anchor less the $250.00 still-projected
            # row, so neither side is the bare anchor.
            assert seam == Decimal("750.00")

    def test_interest_accrues_equals_period_map_not_cash(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an HYSA, balance_at accrues interest, NOT cash -- at the DATE.

        The kind-correct scalar must accrue where the cash-flow scalar does
        not.  Anchor a 5% APY HYSA at ``periods[2]`` with no transactions, then
        value it inside ``periods[6]``: strictly above the flat $5,000.00 cash
        carry ``cash_balance_at`` returns for the same date.  Asserting that
        divergence is what locks the scalar onto the accruing path: were
        INTEREST routed back to the cash producer, ``balance_at`` would equal
        the cash value and this test fails.

        **It meets the MAP at the map's own grain** (plan step X-g2b, finding
        N-71): the map is the fold sampled at each period's ``end_date``, and
        the scalar answers whatever date it is asked.  Reading it on the
        period's FIRST day used to return the period's END balance -- a whole
        period of accrual credited on day one.

        The cash reference was ``balance_as_of_date`` until plan step X-c2b3
        deleted it, and the $5,000.00 is unchanged by the swap for a reason
        worth stating: the HYSA holds ONE asserted balance and no transaction
        rows at all, so the fold has exactly the assertion to replay and the
        retired anchor-carry had exactly the same anchor to carry.  The two
        bases only diverge where money has settled or a second assertion
        exists, and this fixture has neither.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[2], Decimal("5000.00"))
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(hysa, bctx, as_of)
            full_map = balance_at.balance_map(hysa, bctx, periods)
            # Kind-correct scalar == kind-correct map at the period's END,
            # and STRICTLY BELOW it on the period's first day.
            assert balance_at.balance_at(
                hysa, bctx, periods[6].end_date,
            ) == full_map[periods[6].id]
            assert seam < full_map[periods[6].id]
            # And it ACCRUES: strictly above the flat no-interest cash carry
            # (anchor $5,000.00 with no rows) the cash-flow scalar returns for
            # the same date -- the Fork-B lock that the scalar is not on the
            # cash path for INTEREST.
            cash = balance_at.cash_balance_at(hysa, bctx, as_of)
            assert cash == Decimal("5000.00")
            assert seam > cash

    def test_loan_scalar_folds_the_forward_plan_not_the_contractual_schedule(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The loan scalar's FUTURE value folds the PLAN, crediting no unpaid installment.

        A mortgage originated in the past with NO payment records carries a
        schedule of overdue unconfirmed installments.  The retired forward walk
        (``balance_from_schedule_at_date`` over the resolver's schedule) credited
        every one of them, paying the loan down for installments nobody paid
        (finding B-9).  The seam now folds the loan's forward PLAN, which
        synthesizes only FUTURE contractual installments, so an unpaid overdue one
        no longer reduces a future balance.  Pinned by the divergence -- the seam
        owes STRICTLY MORE than the contractual walk that over-credited -- so a
        regression back to the schedule walk would fire here.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            schedule = net_worth_kernel.generate_debt_schedules(
                [mortgage], bctx,
            )[mortgage.id]
            as_of = periods[7].end_date  # future under seed_periods_today

            seam = balance_at.balance_at(mortgage, bctx, as_of)

            # Independent oracle: the retired forward walk credited EVERY
            # unconfirmed installment due by as_of, overdue ones included.
            forward_rows = sorted(
                (r for r in schedule.schedule if not r.is_confirmed),
                key=lambda r: r.payment_date,
            )
            due_by = [r for r in forward_rows if r.payment_date <= as_of]
            contractual_walk = (
                due_by[-1].remaining_balance if due_by
                else schedule.projection_seed
            )
            overdue = [r for r in forward_rows if r.payment_date <= bctx.as_of]
            assert overdue, "fixture must carry overdue unconfirmed installments"

            # The seam credits none of the overdue installments, so it owes MORE
            # than the contractual walk, and still amortizes below its seed.
            assert seam > contractual_walk
            assert seam <= schedule.projection_seed

    def test_investment_is_date_precise_and_meets_the_map_at_period_ends(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An investment answers the DATE, and the map is that fold at period ends.

        **The old contract was the defect** (finding N-71, closed at plan step
        X-g2b).  ``balance_at`` used to resolve the date to its pay period and
        return the map's value there, so it answered the IDENTICAL figure on a
        period's first day and its last -- measured at period 30 on the
        prod-shape clone, $328.50 of growth in that period landed entirely on
        day one.  The replay has a step for every day.

        So the two producers still meet, but at the map's OWN grain: the map is
        the fold sampled at each period's ``end_date``.  Asserting both halves
        is what makes this stronger than the identity it replaces -- the old one
        held while the scalar was period-flat, which is precisely the state
        being deleted.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            full_map = balance_at.balance_map(inv, bctx, periods)
            # The map IS the scalar at each period's end date.
            assert balance_at.balance_at(
                inv, bctx, periods[6].end_date,
            ) == full_map[periods[6].id]
            # And the scalar is DATE-precise: strictly less on the period's
            # first day than on its last, where it used to be equal.
            first_day = balance_at.balance_at(inv, bctx, periods[6].start_date)
            assert first_day < full_map[periods[6].id]
            # Neighbors differ -> an off-by-one in period selection would show.
            assert full_map[periods[5].id] != full_map[periods[7].id]
            # Read a post-anchor (grown) period, not the period-0 / anchor value.
            assert first_day > Decimal("10000.00")

    def test_property_is_date_precise_and_meets_the_map_at_period_ends(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property answers the DATE too -- the same closure of finding N-71.

        The appreciating kind carried the identical period-flat wart, and it is
        the one whose figure is largest: a $400,000.00 market value at 3% moves
        about $32 a period, all of which used to land on the period's first day.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )

            full_map = balance_at.balance_map(prop, bctx, periods)
            assert balance_at.balance_at(
                prop, bctx, periods[6].end_date,
            ) == full_map[periods[6].id]
            first_day = balance_at.balance_at(prop, bctx, periods[6].start_date)
            assert first_day < full_map[periods[6].id]
            # Neighbors differ -> an off-by-one in period selection would show.
            assert full_map[periods[5].id] != full_map[periods[7].id]
            # Post-anchor appreciation above the anchor market value.
            assert first_day > Decimal("400000.00")


class TestTheSeamOwnsTheIncomeBasis:
    """Ruling R-Q: the live override map is the seam's, not the caller's.

    ``balance_map`` and ``cash_balance_map`` used to take an
    ``amount_overrides`` argument, and its ``None``-handling differed by kind:
    the plain path auto-built a LIVE map while the interest path fell back to
    the STORED ``estimated_amount``.  One account could therefore be valued on
    two income bases in one render, and the difference surfaced on the grid as
    interest.  The fold builds its own map over its own plan, so the argument
    is gone and these tests pin what replaced it -- live income, without being
    asked for.
    """

    def test_a_stale_stored_amount_is_priced_live_without_being_asked(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A salary row's stale estimate never reaches the balance.

        The stored ``estimated_amount`` is $1.00 against a $104,000 profile
        whose live net is $4,000.00 (104000 / 26, hand-computed).  Both the
        kind-correct map and the cash-flow map must carry the LIVE figure with
        no argument passed: $1,000 anchor + $4,000 = $5,000.00 at period 5.
        """
        # pylint: disable=import-outside-toplevel
        from tests.test_services.test_income_service import (
            _create_profile,
            _make_salary_template,
            _make_txn,
        )

        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            txn = _make_txn(
                seed_user, periods[5], template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            assert txn.estimated_amount == Decimal("1.00")
            assert balance_at.balance_map(
                account, bctx, periods,
            )[periods[5].id] == Decimal("5000.00")
            assert balance_at.cash_balance_map(
                account, bctx, periods,
            )[periods[5].id] == Decimal("5000.00")

    def test_an_interest_account_is_on_the_same_live_basis_as_a_plain_one(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The kind that used to read STORED income reads LIVE income too.

        This is the asymmetry ruling R-Q deleted.  An HYSA's salary row carries
        the same stale $1.00 estimate; its balance must be built on the same
        $4,000.00 live net a plain account gets, so the grid's premium over the
        cash basis is pure interest rather than an income mismatch.
        """
        # pylint: disable=import-outside-toplevel
        from tests.test_services.test_income_service import (
            _create_profile,
            _make_salary_template,
        )

        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            db.session.add(Transaction(
                account_id=hysa.id,
                template_id=template.id,
                pay_period_id=periods[5].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="HYSA paycheck",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1.00"),
            ))
            db.session.commit()

            kind_correct = balance_at.balance_map(hysa, bctx, periods)
            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            # The live $4,000.00 lands in the CASH basis...
            assert cash[periods[5].id] - cash[periods[4].id] == Decimal(
                "4000.00",
            )
            # ...and the kind-correct balance is that basis plus interest.
            # The FIRST period's accrual is hand-derivable and pins the rate
            # and the 14-day count exactly:
            #   Q(5000 * ((1 + 0.05/365) ** 14 - 1)) = 9.60
            # (a 13-day window -- the day-count regression this catches --
            # gives 8.91).
            assert (
                kind_correct[periods[0].id] - cash[periods[0].id]
            ) == Decimal("9.60")
            # By period 5 the compounded premium is $65.54, nowhere near the
            # ~$3,999 an income-basis mismatch would have produced.  The
            # compounding itself is graded hand-computed in
            # ``test_interest_accrual.py``; this is its regression pin.
            assert (
                kind_correct[periods[5].id] - cash[periods[5].id]
            ) == Decimal("65.54")


class TestMultiLoanIsolation:
    """build_maps keeps each loan's schedule separate (no shared/positional bug)."""

    def test_two_loans_keep_distinct_current_balances(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two trued-up loans in one build_maps keep DISTINCT balances, past AND future.

        A shared or positional debt-schedule forward would collapse both loans onto
        one balance.  Loan A is trued up to $200,000 today and loan B to $50,000, so
        the seam must report each loan's OWN balance -- every producer beneath the
        batch loop is asked per account, never positionally.

        The seam folds each loan from its OWN memoized walk
        (:meth:`~app.services.balance_at.BalanceContext.loan_walk`, keyed
        by account) for begun periods and projects its OWN per-account re-derived
        schedule for the future, so two loans in one ``build_maps`` cannot
        cross-contaminate.  Pinning all three regions catches a regression that
        shared or positionally mis-keyed a loan's balance -- it would surface as
        one loan drifting toward the other's, and the FUTURE tail (where the
        balances diverge most) is where it would show first.

        So all three regions are pinned: each loan's own opening at a pre-anchor
        period ($240,000 / $180,000), its own trued-up balance at the anchor period
        ($200,000 / $50,000), and its own forward projection -- which must still
        straddle the two loans' wildly different balances after today.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            loan_a, params_a = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1), name="Mortgage A",
            )
            loan_b, params_b = _make_mortgage(
                db, seed_user, periods[0], Decimal("180000.00"),
                date(2024, 1, 1), name="Mortgage B",
            )
            insert_trueup_event(
                params_a, Decimal("200000.00"), anchor_date=date.today(),
            )
            insert_trueup_event(
                params_b, Decimal("50000.00"), anchor_date=date.today(),
            )
            db.session.commit()

            seam_maps = balance_at.build_maps([loan_a, loan_b], bctx, periods)

            anchor_date = date.today()
            anchor_period = next(
                p for p in periods
                if p.start_date <= anchor_date <= p.end_date
            )
            # The period the true-ups landed in -> each loan's OWN trued-up
            # balance (both are still pre-first-payment there).
            assert seam_maps[loan_a.id][anchor_period.id] == Decimal("200000.00")
            assert seam_maps[loan_b.id][anchor_period.id] == Decimal("50000.00")

            # A period that ended before the true-ups -> each loan's OWN ledger
            # opening (no payment was recorded against either).
            pre_anchor = [p for p in periods if p.end_date < anchor_date]
            assert pre_anchor, "expected a pre-anchor period"
            earlier = pre_anchor[-1].id
            assert seam_maps[loan_a.id][earlier] == Decimal("240000.00")
            assert seam_maps[loan_b.id][earlier] == Decimal("180000.00")

            # The FUTURE tail -- the only region that consumes the per-loan
            # DebtSchedule bundle, and so the only one where a positional/shared
            # mix-up can surface.  Each loan must still amortize down from its OWN
            # trued-up balance, so A stays far above B and neither drifts toward the
            # other's schedule.
            future = [p for p in periods if p.start_date > anchor_date]
            assert future, "expected a future period"
            later = future[-1].id
            a_future = seam_maps[loan_a.id][later]
            b_future = seam_maps[loan_b.id][later]
            # A was trued up to 200k and B to 50k; a few biweekly periods of
            # amortization cannot move either near the other.
            assert Decimal("190000.00") < a_future <= Decimal("200000.00"), (
                f"loan A's forward projection {a_future!r} did not amortize from "
                f"its own $200,000 balance; the debt schedules are crossed"
            )
            assert Decimal("45000.00") < b_future <= Decimal("50000.00"), (
                f"loan B's forward projection {b_future!r} did not amortize from "
                f"its own $50,000 balance; the debt schedules are crossed"
            )


class TestInvestmentContributions:
    """Deductions and employer match flow through the seam's growth path."""

    def test_deduction_increases_balance_and_is_scoped(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An active deduction raises the 401(k) balance; checking is untouched.

        Baseline (no deduction) vs a $200/period flat contribution: the
        post-anchor balance must be strictly higher (the contribution is
        consumed).  The seam == the kernel called with the same loaded
        deduction.  In a mixed build_maps, the checking account -- which has
        no deduction -- is unaffected (the deduction is scoped to the 401(k)
        by target_account_id, not leaked).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            baseline = balance_at.balance_map(inv, bctx, periods)

            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            _add_flat_deduction(db, profile, inv, Decimal("200.0000"))
            db.session.commit()

            with_ded = balance_at.balance_map(inv, bctx, periods)
            # Post-anchor period reflects the consumed contribution.
            assert with_ded[periods[-1].id] > baseline[periods[-1].id]

            # seam == kernel with the SAME manually-loaded deduction.
            params = load_investment_params_for_accounts([inv]).get(inv.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv.id],
            ).get(inv.id, [])
            gross = income_service.get_current_gross_biweekly(user_id)
            expected = net_worth_kernel.build_account_balance_map(
                inv, bctx, periods,
                ContributionInputs(
                    investment_params=params, deductions=deductions,
                    salary_gross_biweekly=gross,
                ),
            )
            assert with_ded == expected
            assert len(deductions) == 1  # the deduction was actually loaded

            # Scope: a non-investment account in the same batch is untouched.
            checking = seed_user["account"]
            maps = balance_at.build_maps([inv, checking], bctx, periods)
            assert maps[checking.id] == balance_at.balance_map(
                checking, bctx, periods,
            )

    def test_employer_match_driven_by_gross_exceeds_no_match(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A gross-capped employer match raises the balance above a no-match peer.

        Two identical 401(k)s ($10k, period-2 anchor, $200/period employee
        contribution) differ only in employer type: one matches 50% up to 6%
        of gross, the other has none.  With a real salary (gross > 0) the
        match cap is positive, so the matched account's post-anchor balance
        exceeds the no-match account's.  A zero / wrong gross would zero the
        cap and collapse the difference.  The seam == the kernel for the
        matched account.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv_match = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
                name="401k Match", employer_type="match",
                match_pct=Decimal("0.5000"), match_cap_pct=Decimal("0.0600"),
            )
            inv_none = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
                name="401k None",
            )
            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            _add_flat_deduction(db, profile, inv_match, Decimal("200.0000"))
            _add_flat_deduction(db, profile, inv_none, Decimal("200.0000"))
            db.session.commit()

            gross = income_service.get_current_gross_biweekly(user_id)
            assert gross > Decimal("0.00")  # the match cap basis must be real

            match_map = balance_at.balance_map(inv_match, bctx, periods)
            none_map = balance_at.balance_map(inv_none, bctx, periods)
            assert match_map[periods[-1].id] > none_map[periods[-1].id]

            # seam == kernel for the matched account.
            params = load_investment_params_for_accounts(
                [inv_match],
            ).get(inv_match.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv_match.id],
            ).get(inv_match.id, [])
            expected = net_worth_kernel.build_account_balance_map(
                inv_match, bctx, periods,
                ContributionInputs(
                    investment_params=params, deductions=deductions,
                    salary_gross_biweekly=gross,
                ),
            )
            assert match_map == expected


class TestScenarioGuard:
    """All three entry points fail loud on a None scenario (C1)."""

    def test_none_scenario_raises_value_error(
        self, app, db, seed_user, seed_periods_today,
    ):
        """balance_map / build_maps / balance_at each raise ValueError on None.

        ``get_baseline_scenario`` can return None (fresh user); the seam is
        the defensive contract that turns that into a clear failure rather
        than a deep AttributeError on ``scenario.id`` or a silent $0.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            with pytest.raises(ValueError):
                balance_at.balance_map(account, _no_baseline(user_id), periods)
            with pytest.raises(ValueError):
                balance_at.build_maps([account], _no_baseline(user_id), periods)
            with pytest.raises(ValueError):
                balance_at.balance_at(account, _no_baseline(user_id), as_of)


class TestBalanceAtDegrade:
    """balance_at's documented fallbacks (no-schedule loan, before-horizon)."""

    def test_loan_without_schedule_degrades_to_cash_producer(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An amortizing account with no LoanParams degrades to the cash fold.

        ``resolved_loan`` returns None (no LoanParams / anchor events), so
        ``balance_at`` falls back to the cash fold over the loan's own rows --
        the documented degrade, and the second of the two branches finding N-47
        moved onto the fold at plan step X-c2b2.  Referenced against the SEAM
        entry since plan step X-c2b3 deleted ``balance_as_of_date``.

        **The $300.00 row is what gives the equality teeth**, for the reason
        ``test_cash_equals_the_cash_flow_scalar`` records: an account holding
        only its opening assertion is answered identically by any producer that
        reads the anchor, so the degrade could be re-routed anywhere and this
        would stay green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage_type = (
                db.session.query(AccountType).filter_by(name="Mortgage").one()
            )
            acct = account_service.create_account(account_service.AccountSpec(
                user_id=user_id, account_type_id=mortgage_type.id,
                name="Unconfigured Loan", anchor_balance=Decimal("5000.00"),
                anchor_period_id=periods[0].id,
            ))
            db.session.add(acct)
            add_txn(
                db.session, seed_user, periods[4], "Payment", "300.00",
                account=acct,
            )
            db.session.commit()
            as_of = periods[5].start_date

            seam = balance_at.balance_at(acct, bctx, as_of)
            expected = balance_at.cash_balance_at(acct, bctx, as_of)
            assert seam == expected
            # Non-vacuity: $5,000.00 opening less the $300.00 still-projected
            # row, so neither side is the bare anchor.
            assert seam == Decimal("4700.00")

    def test_before_horizon_returns_anchor_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An as_of before the whole period horizon returns the canonical anchor.

        For an investment whose date precedes every period, no containing
        period exists, so balance_at returns the resolved anchor balance
        (rounded).  Note this is the KIND-CORRECT scalar's own fall-through,
        not the cash view's: the fold answers a pre-assertion date by
        back-projecting the first assertion over the records it already
        contains (ruling R-I), which is a different and deliberately separate
        rule.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            seam = balance_at.balance_at(inv, bctx, date(2000, 1, 1))
            expected = round_money(
                cash_ledger.resolve_anchor(inv, scenario.id).balance,
            )
            assert seam == expected
            assert seam == Decimal("10000.00")  # the 401k's anchor balance


def _configured_annual_rate(account) -> Decimal:
    """Return the annual return the account itself carries.

    Read off the account's own params row rather than restated here, so this
    test's bound moves with the fixture instead of pinning a literal the
    fixture is free to change.
    """
    params = getattr(account, "asset_appreciation_params", None)
    if params is not None:
        return params.annual_appreciation_rate
    return load_investment_params_for_accounts(
        [account],
    )[account.id].assumed_annual_return


class TestOnlyALoanIsNotATransactionSum:
    """A typed row moves a MODELLED asset and never a loan (ruling R-W).

    **This class inverted at plan step X-g2b, for two of its three kinds.**  It
    used to assert that a row typed on an investment or a property moved
    nothing, and that was true of the shipped producer: those maps were a
    growth curve spliced over a cash base, so an ad-hoc row landed in the base
    and the splice preferred the curve.  ``_grid.py`` recorded the same
    objection as the reason the grid left those kinds on the cash-flow view.

    Under ONE replay a typed row IS an event in the same stream -- a modelled
    asset is its cash fold plus a rate -- so it moves the balance exactly as it
    moves an HYSA's, and ruling R-W is what makes that the intended answer
    rather than a regression.  A LOAN is the one kind where the old assertion
    survives, and for a reason that is about the loan rather than about the
    producer: its balance is an amortization schedule, not a transaction sum,
    which is why ruling D4 refuses such a row at every write door.
    """

    def test_a_typed_income_row_moves_the_modelled_kinds_but_never_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The map before and after adding an income row to each kind.

        (A loan REFUSES such a row at the write door since plan step BG /
        ruling R-E; this inserts one directly to prove the READ side ignores it
        too, which is what makes the write guard a belt rather than the only
        thing standing between a schedule and a transaction sum.)

        The modelled halves assert the row is counted ONCE and compounded --
        strictly more than the row's own $9,999.00 by the period it lands in
        and after, and unchanged before it -- so a double count or a dropped
        row both fail here, where "moved at all" would pass either.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage, _p = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            before = {
                acct.id: balance_at.balance_map(acct, bctx, periods)
                for acct in (mortgage, inv, prop)
            }
            for acct in (mortgage, inv, prop):
                db.session.add(Transaction(
                    account_id=acct.id,
                    pay_period_id=periods[5].id,
                    scenario_id=scenario.id,
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    name="typed row",
                    transaction_type_id=ref_cache.txn_type_id(
                        TxnTypeEnum.INCOME,
                    ),
                    estimated_amount=Decimal("9999.00"),
                ))
            db.session.commit()

            # The LOAN is unmoved: its balance is its schedule (ruling D4).
            assert balance_at.balance_map(
                mortgage, bctx, periods,
            ) == before[mortgage.id], "the loan moved on a typed row"

            # The MODELLED kinds count it, once, from the period it lands in.
            for acct in (inv, prop):
                after = balance_at.balance_map(acct, bctx, periods)
                # Untouched before the row's own period ...
                assert after[periods[4].id] == before[acct.id][periods[4].id], (
                    f"{acct.name} moved BEFORE the typed row's period"
                )
                # ... counted ONCE in it, and compounded after.  The bound is
                # derived, not guessed: the row is worth $9,999.00 and can earn
                # at most one period of the account's own configured return on
                # top of it (it lands inside the period, so at most that), which
                # is strictly less than counting it twice.  A dropped row fails
                # the lower bound and a double count fails the upper.
                one_period = Decimal("9999.00") * (
                    1 + growth_engine.period_return_rate(
                        _configured_annual_rate(acct), periods[5],
                    )
                )
                landed = after[periods[5].id] - before[acct.id][periods[5].id]
                assert Decimal("9999.00") <= landed <= one_period, (
                    f"{acct.name} counted the typed row as {landed}"
                )
                grown = after[periods[-1].id] - before[acct.id][periods[-1].id]
                assert grown > landed, f"{acct.name} did not compound the row"


class TestCashPreAnchorPeriodsAreAnswered:
    """The headline cash contract, REVERSED at plan step X-c2b2.

    Cash balances used to be materialized roll-forwards from the anchor, so a
    period before it had no balance at all -- absent, not zero.  That is
    finding cash D3 / B-18: on production the period map omitted eight past
    columns while the scalar fabricated today's balance for the same dates.
    The fold replays every assertion, so a pre-anchor period reads the balance
    in force then and EVERY requested period is present.
    """

    def test_an_interest_account_answers_its_pre_anchor_periods(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An HYSA anchored mid-window carries a balance in every period.

        The account is anchored at period 2 with $5,000.00 and has no rows, so
        ruling R-I's back-projection holds that assertion flat backwards:
        periods 0 and 1 read $5,000.00 -- the balance it demonstrably held --
        rather than being absent.  The seam == the kernel.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[2], Decimal("5000.00"))
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(hysa, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                hysa, bctx, periods,
                ContributionInputs(salary_gross_biweekly=gross),
            )
            assert seam is not None
            assert seam[periods[0].id] == Decimal("5000.00")
            assert seam[periods[1].id] == Decimal("5000.00")
            assert periods[2].id in seam  # the anchor period is present
            assert len(seam) == len(periods)
            assert seam == expected


class TestBalanceMapEdgeCases:
    """Empty-set, empty-periods, and direct no-anchor contracts."""

    def test_build_maps_empty_accounts_is_empty(
        self, app, db, seed_user, seed_periods_today,
    ):
        """build_maps over no accounts returns an empty dict (no query needed)."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            assert balance_at.build_maps([], bctx, periods) == {}

    def test_balance_map_empty_periods_is_empty_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An anchored account over no periods yields an empty (not None) map."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            account = seed_user["account"]
            result = balance_at.balance_map(account, bctx, [])
            assert result is not None
            assert len(result) == 0

    def test_balance_map_no_anchor_account_is_none(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A no-anchor account yields None directly from balance_map."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            no_anchor = SimpleNamespace(
                id=-1, user_id=user_id, account_type=None,
                current_anchor_period_id=None,
            )
            assert balance_at.balance_map(no_anchor, bctx, periods) is None


class TestCashFlowView:
    """``cash_balance_map`` / ``cash_balance_at`` -- the pure-cash view.

    The single-account cash-flow surfaces (the budget grid, obligations
    panel, calendar, and checking-detail page) read these instead of the
    kind-correct ``balance_map`` / ``balance_at``: they must show the
    account's pure transaction running-balance regardless of its kind, so
    the projected balance reconciles with the surface's own transaction
    rows.  These tests prove (1) the three entries are ONE fold read at three
    grains, so a map column, the scalar at that column's end date and the
    daily series' last day of it cannot disagree, and (2) they do NOT dispatch
    by kind: an INTEREST account's cash map omits the interest the
    kind-correct map accrues, which is the whole reason these entries exist
    (Level-1 Commit 8).
    """

    def test_cash_balance_map_is_the_scalar_at_every_period_end(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Each column equals the scalar read at that column's end date.

        The map and the scalar were separate producers until plan step X-c2b2
        and had to be kept in step; they are one running total sampled at two
        grains now, so this holds by construction -- and stating it is what
        would catch a future reader that re-introduced a second walk.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]

            seam = balance_at.cash_balance_map(account, bctx, periods)
            assert len(seam) == len(periods)  # the loop is not vacuous
            for period in periods:
                assert seam[period.id] == balance_at.cash_balance_at(
                    account, bctx, period.end_date,
                ), f"map and scalar disagree at period {period.id}"

    def test_cash_map_omits_interest_unlike_kind_correct_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an HYSA, the cash map is the no-interest running balance.

        ``cash_balance_map`` must NOT accrue interest (it is the cash-flow
        view): its values stay flat at the $5,000 anchor (no transactions),
        strictly below the kind-correct ``balance_map``, which layers the
        modelled accrual on that same fold.  This is the divergence the cash
        entry exists for: a HYSA grid account whose balance row accrued
        interest would leave a change the rows on screen cannot explain.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))

            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            kind_correct = balance_at.balance_map(hysa, bctx, periods)

            # No transactions + no interest -> flat at the anchor.
            assert set(cash.values()) == {Decimal("5000.00")}
            # The kind-correct view accrues interest strictly above it.
            assert kind_correct[periods[-1].id] > cash[periods[-1].id]

    def test_a_settled_post_anchor_row_is_counted_not_flagged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The stale-anchor banner's whole subject is now just counted.

        The grid used to render a warning when a settled row existed in a
        post-anchor period: those rows contributed nothing to the projection,
        so the balance might be wrong and only the user could fix it by
        re-anchoring (they did, 52 times in 119 days on the real account).
        The fold counts the row, so there is nothing left to warn about and
        plan step X-c2b2 deleted the flag, its banner and its detector.

        The seed account is anchored at ``periods[0]``; a RECEIVED income row
        of $500.00 in period 3 must RAISE the balance from period 3 on --
        $1,000 anchor + $500 = $1,500.00 -- while periods before it are
        unmoved.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            add_txn(
                db.session, seed_user, periods[3], "Deposit", "500.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            seam = balance_at.cash_balance_map(account, bctx, periods)
            assert seam[periods[2].id] == Decimal("1000.00")
            assert seam[periods[3].id] == Decimal("1500.00")
            assert seam[periods[-1].id] == Decimal("1500.00")

    def test_cash_balance_at_is_the_daily_series_on_that_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The scalar equals the daily series' value for the same day.

        The third grain of the same fold.  These were two producers that
        measured $15.96 apart on the real Checking account the day before the
        cutover (finding cash D2); one is a sampling of the other now.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            series = balance_at.cash_daily_balance_series(
                account, bctx, as_of, as_of,
            )
            assert balance_at.cash_balance_at(
                account, bctx, as_of,
            ) == series[as_of]

    def test_cash_balance_at_is_no_interest_for_hysa(
        self, app, db, seed_user, seed_periods_today,
    ):
        """cash_balance_at is the no-interest scalar even for an HYSA.

        Mirrors the map case: the scalar cash view never layers interest and
        stays flat at the anchor for a transaction-free HYSA -- the calendar's
        month-end figure must be this cash-flow balance, not an
        interest-accrued one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            as_of = periods[-1].end_date

            cash = balance_at.cash_balance_at(hysa, bctx, as_of)
            # No transactions, no interest -> flat at the anchor, strictly
            # below the kind-correct scalar which layers the accrual.
            assert cash == Decimal("5000.00")
            assert balance_at.balance_at(hysa, bctx, as_of) > cash

    def test_a_datetime_is_refused_where_a_civil_date_is_required(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A ``datetime`` fails loud at the seam, not three layers down.

        ``datetime`` SUBCLASSES ``date``, so an ``isinstance`` guard accepts
        one -- it reads like a type check and is not one.  The fold's step
        boundaries are civil dates, so a ``datetime`` that got past would die
        inside ``bisect_right`` with a comparison error naming neither the
        entry nor the argument.  Both dated entries refuse it by exact type,
        and the message names the argument.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            account = seed_user["account"]
            instant = datetime(2026, 3, 20, 12, tzinfo=timezone.utc)

            with pytest.raises(TypeError, match="as_of"):
                balance_at.cash_balance_at(account, bctx, instant)
            with pytest.raises(TypeError, match="first_day"):
                balance_at.cash_daily_balance_series(
                    account, bctx, instant, date(2026, 3, 21),
                )
            with pytest.raises(TypeError, match="last_day"):
                balance_at.cash_daily_balance_series(
                    account, bctx, date(2026, 3, 20), instant,
                )
            # ...and a plain date is accepted, so the guard is not blanket.
            assert isinstance(
                balance_at.cash_balance_at(account, bctx, date(2026, 3, 20)),
                Decimal,
            )

    def test_cash_entries_raise_on_none_scenario(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Both cash entries fail loud on a None scenario (C1 contract)."""
        with app.app_context():
            user_id = seed_user["user"].id
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            with pytest.raises(ValueError):
                balance_at.cash_balance_map(account, _no_baseline(user_id), periods)
            with pytest.raises(ValueError):
                balance_at.cash_balance_at(account, _no_baseline(user_id), as_of)


class TestASettledRowMovesEveryCashAnswerTogether:
    """The shape the cutover exists for, on every entry that must agree.

    A row SETTLED after the account's latest assertion is finding cash D1: the
    retired producers counted it nowhere, so the money left the bank and stayed
    on the screen.  Both tests below seed exactly that shape, because it is the
    only one on which the retired producers and the fold give different answers
    -- an account with no settled post-anchor activity cannot tell them apart,
    which is why the rest of this suite's fixtures could not.
    """

    def test_the_kind_correct_scalar_and_the_cash_scalar_are_one_call(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For PLAIN, ``balance_at`` and ``cash_balance_at`` are the same fold.

        A plain account's kind-correct balance IS its cash-flow balance, so the
        two scalars must be one call (plan finding N-47): /savings reads the
        first and the dashboard reads the second, and a shape where they differ
        is one where those two pages contradict each other.

        Hand-computed: $1,000.00 anchor, one $250.00 expense settled after it,
        so both read $750.00.  On the retired date-precise producer the settled
        row contributed nothing and both would read $1,000.00 -- which is why
        this needs a settled row to have teeth at all.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_cash_transaction

        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, db.session, periods[4], Decimal("250.00"),
                name="settled after the anchor",
                # The instant is PASSED, not left to the status seam: it stamps
                # ``paid_at`` from the DATABASE clock, which ``freeze_today``
                # (Python-level) does not patch -- so an unpinned settle lands
                # months after the frozen read and outside every seeded period.
                paid_at=settle_instant_on(
                    periods[4].start_date + timedelta(days=1),
                ),
            )
            db.session.commit()

            kind_correct = balance_at.balance_at(account, bctx, bctx.as_of)
            cash = balance_at.cash_balance_at(account, bctx, bctx.as_of)

            assert kind_correct == cash == Decimal("750.00")

    def test_the_grids_interest_row_is_not_the_settled_row_in_disguise(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST account's accrual stays interest-sized (finding N-49).

        The grid derives its "Interest" row from what the modelled accrual adds
        to the cash basis.  If the accrual's SEED lagged the cash map -- if it
        still walked forward from the ``current_anchor_balance`` cache while
        the cash map folded -- the row would absorb every settled row the cache
        never saw and label it earnings.  Measured on the real Money Market
        before the cutover: ``$2,007.01`` of "interest" in one column, which is
        the ``$2,000.00`` that had actually left the account.

        Hand-computed: a 5% HYSA anchored at $50,000.00 with a $2,000.00
        expense settled after that assertion.  The cash basis drops to
        $48,000.00, the displayed balance follows it, and the whole horizon's
        accrual stays a few hundred dollars of real interest -- never the
        $2,000.00 gap.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_cash_transaction

        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("50000.00"))
            create_settled_cash_transaction(
                seed_user, db.session, periods[4], Decimal("2000.00"),
                account=hysa, name="settled after the anchor",
                # Pinned for the reason the sibling test above documents.
                paid_at=settle_instant_on(
                    periods[4].start_date + timedelta(days=1),
                ),
            )
            db.session.commit()

            view = balance_at.grid_balance_view(hysa, bctx, periods)
            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            current = pay_period_service.get_current_period(user_id)

            # The settled row IS in the cash basis...
            assert cash[current.id] == Decimal("48000.00")
            # ...and in the displayed balance, which is that basis plus the
            # accrual -- never the basis the cache would have carried.
            column = view.columns[current.id]
            assert column.balance - cash[current.id] == sum(
                other.accrual for pid, other in view.columns.items()
                if pid in cash
                and list(cash).index(pid) <= list(cash).index(current.id)
            )
            # The accrual is interest, not the missing $2,000.00 -- and it is
            # pinned to the cent rather than to a band, because a band wide
            # enough to hold ten periods of compounding is also wide enough to
            # hold a day-count regression.  The first period is hand-derived:
            #   Q(50000 * ((1 + 0.05/365) ** 14 - 1)) = 95.98
            # (a 13-day window gives 89.11), and the ten-period total
            # compounds from there, stepping down when the $2,000.00 settles.
            # The first period is UNCHANGED by the daily grain, and that is
            # not a coincidence: compounding a daily rate over 14 days IS
            # ``(1 + 0.05/365) ** 14``, so the two rules are the same curve
            # re-grouped whenever no money moves inside the period.
            assert view.columns[periods[0].id].accrual == Decimal("95.98")
            total_accrual = sum(
                col.accrual for col in view.columns.values()
            )
            # $944.94 until plan step X-g2b, and the $0.28 it gained is DERIVED
            # rather than observed.  The per-period layer accrued a whole period
            # on that period's END balance, so period 4 earned all 14 of its
            # days on the post-settlement balance; the daily replay applies the
            # day's cash step first and then accrues on what is actually held,
            # so period 4's FIRST day still earns on the $2,000.00 that had not
            # left yet.  That is 2000 * 0.05 / 365 = $0.2740, plus $0.0032 of
            # its own compounding over the ~6 periods left in the horizon:
            # $0.28.  The two accrual BASES this arc found disagreeing -- the
            # interest path's period END and the growth path's period START --
            # collapse here into "the balance held on the day", which is what
            # leaves no boundary convention left to pin the wrong one.
            assert total_accrual == Decimal("945.22")
            _assert_grid_view_reconciles(view)


class TestTheInterestChipAndTheBalanceAreOneWalk:
    """The account-detail page's two seam calls report ONE projection.

    The cash detail page reads ``balance_map`` for an interest account's
    balances and ``interest_by_period_for_account`` for the "Interest, next
    12 mo" chip.  They are separate entries because interest EARNED is not a
    balance-at-T -- but they must be the same walk, or the page would show an
    accrual that does not explain its own balance change.  Since plan step
    X-c2b2 both go through ``_kernel._account_interest_projection``, so the
    property is structural; asserting it is what would catch a reader that
    gave one of them a second base.

    (This class replaces a parity check against
    ``calculate_balances_with_interest``, the composition X-c2b2 retired when
    the accrual's base became the cash fold.  Parity with a deleted producer
    cannot be asserted; the invariant it was standing in for can.)
    """

    def test_the_chip_explains_the_balance_change_exactly(
        self, app, db, seed_user, seed_periods_today,
    ):
        """balance[p] - balance[p-1] == cash[p] - cash[p-1] + interest[p].

        Seeds an HYSA (5% APY) anchored at ``periods[0]`` with a $1,000
        deposit at ``periods[6]`` so the running balance moves and interest
        accrues on it.  Every period's interest-accrued change must decompose
        into the cash change plus that period's chip figure, to the cent.

        The deposit is dated FORWARD of the read's as-of (``seed_periods_today``
        places today in period 4) so it lands in its own column: ruling R-G
        clamps a still-projected row whose date has passed up to ``as_of + 1``,
        which would put it in the current period and make the non-vacuity
        assertion below measure the wrong column.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("8000.00"))
            db.session.add(Transaction(
                account_id=hysa.id,
                pay_period_id=periods[6].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Deposit",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1000.00"),
            ))
            db.session.commit()
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id)
                .one()
            )

            accrued = balance_at.balance_map(hysa, bctx, periods)
            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            interest = net_worth_kernel.interest_by_period_for_account(
                hysa, bctx, periods,
            )

            assert len(periods) > 1  # the loop is not vacuous
            for previous, period in zip(periods, periods[1:]):
                assert (
                    accrued[period.id] - accrued[previous.id]
                    == (cash[period.id] - cash[previous.id])
                    + interest[period.id]
                ), f"period {period.id} does not decompose"

            # The projection is real, not flat: interest accrued and the
            # deposit raised the balance, so the identity is non-trivial.
            assert any(v > Decimal("0.00") for v in interest.values())
            assert cash[periods[6].id] - cash[periods[5].id] == Decimal(
                "1000.00",
            )


def _all_columns(view):
    """Return the view's columns in order, as ``(period_id, column)`` pairs.

    EVERY requested period carries a balance since plan step X-c2b2 -- the fold
    is total -- so there is nothing to filter out.  It used to skip periods the
    projection could not reach (``balance is None``, the pre-anchor columns the
    retired producer omitted), which is exactly the omission finding cash D3
    names; keeping a name for "the columns that have a balance" would now be a
    name for "all of them".
    """
    return list(view.columns.items())


def _assert_grid_view_reconciles(view):
    """Assert the view's rows reconcile to the cent, off the view alone.

    For every adjacent pair of periods the displayed balance delta must equal
    the column's own net plus its remainder plus BOTH modelled tiers -- ruling
    R-K's identity in the four-term form ruling R-AH measured::

        balance[p] - balance[p-1]
            == net[p] + reconciliation[p] + contribution[p] + accrual[p]

    The fourth term is the CONTRIBUTION, and it is not decoration: the
    three-term form breaks on 53 of 59 period pairs on the real Empower 401(k),
    worst $181.59 a column (a flat-percentage employer match), while the
    four-term form holds on all 59.  It is written here at plan step X-g3a,
    where the grid's kind gate still admits only INTEREST accounts and the term
    is therefore identically $0.00, so that the oracle is already in its final
    form when X-g3b's cutover puts a figure in it.  X-g3b supplies the
    producer-side control that can distinguish the two forms.

    It reads ONE GridColumn per period rather than re-running the subtotal
    producer beside the balance producer, which is the point of plan steps
    X-c2b1 / X-c2b2: the identity is a property of the row set, so an oracle
    that re-derived one side from a second producer would be testing that the
    two producers agree rather than that the row set is coherent.
    """
    items = _all_columns(view)
    assert len(items) >= 2, "need >= 2 periods to reconcile a delta"
    for (_prev_id, prev), (pid, column) in zip(items, items[1:]):
        expected = (
            column.net + column.reconciliation
            + column.contribution + column.accrual
        )
        assert column.balance - prev.balance == expected, (
            f"period {pid}: balance delta "
            f"{column.balance - prev.balance} != net {column.net} + "
            f"reconciliation {column.reconciliation} + contribution "
            f"{column.contribution} + accrual {column.accrual}"
        )


class TestTheContributionRowOnARealFeed:
    """Plan step X-g3b's PRODUCER-side control for the fourth term.

    X-g3a seated ruling R-K's four-term identity and could only grade it on
    HAND-BUILT views (``TestTheReconciliationOracleSeesAllFourTerms``), because
    with the kind gate in place no producer could put a figure in
    ``contribution``: only an INTEREST account reached the replay, and only an
    INVESTMENT can have a payroll feed.  This is the first commit in which one
    can, so this is where the term is proven end to end -- from a real
    ``PaycheckDeduction`` through the replay to the column the grid renders.

    The figures are hand-computed off a $104,000 salary, which is $4,000.00 per
    period on the fixture's 26-period year -- chosen over the helper's default
    $75,000 precisely so the employer arithmetic below lands on whole cents and
    the pin is on the RULE rather than on a rounding path.
    """

    _EMPLOYEE = Decimal("200.00")
    _ANNUAL_SALARY = Decimal("104000.00")
    _GROSS = Decimal("4000.00")  # 104000 / 26

    def _401k_with_feed(self, db, seed_user, periods, **params):
        """Return a 401(k) anchored at ``periods[2]`` with a $200/period feed.

        The anchor matters: the opening assertion is stamped at the anchor
        period's first day, and a modelled CONTRIBUTION exists only STRICTLY
        after it (ruling R-Z), so ``periods[2]`` contributes nothing and every
        later period contributes.
        """
        account = make_investment_account(
            seed_user, db.session, periods[2], Decimal("10000.00"), **params,
        )
        profile = make_salary_profile(
            seed_user, db.session, annual_salary=self._ANNUAL_SALARY,
        )
        db.session.flush()
        _add_flat_deduction(db, profile, account, self._EMPLOYEE)
        db.session.commit()
        return account

    def test_the_employee_feed_lands_in_the_contribution_column(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A $200/period deduction renders $200.00 in every period after the anchor.

        Hand-computed: a flat pre-tax $200.00 deduction contributes $200.00 per
        pay period, with no employer configured and no annual limit set, so the
        column IS the deduction.  The anchor period itself carries $0.00 --
        ruling R-Z's strict boundary, since a contribution on or before the
        asserted day is money the assertion already contains.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = self._401k_with_feed(db, seed_user, periods)

            view = balance_at.grid_balance_view(account, bctx, periods)

            assert view.columns[periods[2].id].contribution == Decimal("0.00")
            assert view.columns[periods[3].id].contribution == self._EMPLOYEE
            assert view.columns[periods[4].id].contribution == self._EMPLOYEE
            # And the row therefore renders (ruling R-O's visibility rule).
            assert view.row_flags(periods).contribution is True
            _assert_grid_view_reconciles(view)

    def test_an_employer_match_is_in_the_same_column(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A 100%-to-6% match doubles the column, and the arithmetic is pinned.

        Hand-computed on the $4,000.00 per-period gross:
          matchable = 4000.00 * 0.06 = 240.00
          matched   = min(employee 200.00, 240.00) * 1.00 = 200.00
          column    = employee 200.00 + employer 200.00 = 400.00

        **That gross is the DEDUCTION-derived one**, ``round_money(annual /
        pay_periods_per_year)`` from
        ``investment_projection._compute_deduction_per_period`` -- NOT the
        raise-aware ``salary_gross_biweekly``, which
        ``deduction_contribution_per_period`` uses only as the fallback when no
        deduction supplies one, and this fixture has one.  The two agree here
        ($104,000 / 26), which is why the fixture picks that salary; stating
        which one the arithmetic actually consumes keeps the pin on the rule.

        The employer half is what makes the row more than a restatement of the
        user's own deduction -- and on the developer's real Empower 401(k) it is
        a flat 5% that no employee amount is needed to trigger at all, which is
        the term ruling R-AH measured breaking the three-term identity on 53 of
        59 period pairs.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = self._401k_with_feed(
                db, seed_user, periods,
                employer_type="match",
                match_pct=Decimal("1.0000"),
                match_cap_pct=Decimal("0.0600"),
            )

            view = balance_at.grid_balance_view(account, bctx, periods)

            assert view.columns[periods[3].id].contribution == Decimal("400.00")
            _assert_grid_view_reconciles(view)

    def test_the_three_term_identity_breaks_on_this_fixture(
        self, app, db, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the fourth term is load-bearing on a real producer.

        ``_assert_grid_view_reconciles`` carries four terms, and X-g3a proved it
        can distinguish them on hand-built columns.  What no test could show
        until now is that a PRODUCER puts a figure in the fourth one -- so this
        recomputes the retired THREE-term form on this fixture's real columns
        and asserts it FAILS, by exactly the contribution.  Without this, the
        four-term oracle above could be passing because every producer-built
        column still carries ``$0.00`` there, which is the state X-g3a shipped
        in and could not escape.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = self._401k_with_feed(db, seed_user, periods)

            view = balance_at.grid_balance_view(account, bctx, periods)
            previous = view.columns[periods[3].id]
            column = view.columns[periods[4].id]
            delta = column.balance - previous.balance
            three_term = (
                column.net + column.reconciliation + column.accrual
            )

            # The retired form is short by the whole contribution, and the
            # four-term form closes it exactly.
            assert column.contribution == self._EMPLOYEE
            assert delta != three_term
            assert delta - three_term == column.contribution


class TestGridBalanceView:
    """``grid_balance_view`` -- the kind-aware grid + obligations view."""

    def test_plain_is_cash_flow_with_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A PLAIN account returns the cash-flow view verbatim, no accrual."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]

            view = balance_at.grid_balance_view(account, bctx, periods)
            cash = balance_at.cash_balance_map(account, bctx, periods)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(cash)
            # No accrual row for a plain cash account.
            assert all(
                column.accrual == Decimal("0.00")
                and column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )

    def test_loan_stays_cash_flow_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan grid account stays on the cash-flow view (no accrual row).

        Loans cannot reconcile on a transaction grid (payments land as
        income while the balance is schedule-driven), so ``grid_balance_view``
        leaves them on the cash-flow view exactly like a PLAIN account -- no
        kind-correct walk, no accrual row.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )

            view = balance_at.grid_balance_view(mortgage, bctx, periods)
            cash = balance_at.cash_balance_map(mortgage, bctx, periods)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(cash)
            assert all(
                column.accrual == Decimal("0.00")
                and column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )

    def test_interest_balances_kind_correct_and_reconcile(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An HYSA shows the interest-accrued balance + a reconciling accrual.

        Seeds a 5% HYSA anchored at ``periods[0]`` with a $1,000 deposit at
        ``periods[3]`` so the cash flow is non-trivial (net != 0) AND interest
        accrues on top.  The displayed balances are the rounded kind-correct
        map; the per-period accrual reconciles the rows to the cent; and the
        telescoped total accrual equals the kernel's interest within a cent
        (proving it is real interest, not a residual absorbing a bug).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("8000.00"))
            db.session.add(Transaction(
                account_id=hysa.id,
                pay_period_id=periods[6].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Deposit",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1000.00"),
            ))
            db.session.commit()

            view = balance_at.grid_balance_view(hysa, bctx, periods)
            kc = balance_at.balance_map(hysa, bctx, periods)
            cash = balance_at.cash_balance_map(hysa, bctx, periods)

            # Displayed balances are the rounded kind-correct (interest-accrued)
            # map, strictly above the no-interest cash balance by the horizon.
            projected = dict(_all_columns(view))
            assert set(projected) == set(cash)
            for pid, column in projected.items():
                assert column.balance == round_money(kc[pid])
            assert (
                projected[periods[-1].id].balance
                > cash[periods[-1].id]
            )

            # The rows reconcile to the cent off the view's own columns.
            _assert_grid_view_reconciles(view)

            # The accrual is real interest: the telescoped total equals the
            # final premium and matches the kernel's interest within a cent
            # (the two round on slightly different paths).
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id).one()
            )
            kernel_interest = net_worth_kernel.interest_by_period_for_account(
                hysa, bctx, periods,
            )
            total_accrual = sum(
                column.accrual for _pid, column in _all_columns(view)
            )
            assert total_accrual == (
                projected[periods[-1].id].balance
                - cash[periods[-1].id]
            )
            assert abs(total_accrual - sum(kernel_interest.values())) <= Decimal("0.02")
            assert total_accrual > Decimal("0.00")

    def test_investment_renders_the_modelled_balance_and_reconciles(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INVESTMENT grid account shows the MODELLED balance (ruling R-W).

        **This test asserted the opposite until plan step X-g3b**, on the
        reasoning that an investment's balance is projection-driven rather than
        a sum of its transactions, so "an ad-hoc grid row would not move a
        kind-correct balance".  Under one event replay that is no longer true --
        a typed grid row IS an event in the same stream, measured on the real
        Empower 401(k) at ``$1,003.84`` in its own column and ``$1,211.04`` at
        the horizon, the difference being the accrual ON the new money.  So the
        objection that justified the cash basis is gone, and what remained was
        one account answered two ways: ``$31,070.06`` here against ``$48,712.19``
        on ``/savings`` (finding N-76).  The developer's ruling R-W is the
        authority that the expected behaviour changed.

        The grid balance must now EQUAL the modelled map -- not merely exceed
        the cash basis -- because equality is what closes N-76, and it must
        still reconcile off the view's own rows.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )

            view = balance_at.grid_balance_view(inv, bctx, periods)
            cash = balance_at.cash_balance_map(inv, bctx, periods)
            modelled = balance_at.balance_map(inv, bctx, periods)

            # THE unification: the grid and /savings are the same producer's
            # answer, in every column.
            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(modelled)
            # Non-vacuous: the modelled answer is genuinely above the cash one,
            # so an unmoved grid would fail rather than agree by accident.
            assert view.columns[periods[-1].id].balance > cash[periods[-1].id]
            # And the growth is explained on screen rather than implied.
            assert any(
                column.accrual > Decimal("0.00")
                for column in view.columns.values()
            )
            _assert_grid_view_reconciles(view)

    def test_property_renders_the_modelled_balance_and_reconciles(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An APPRECIATING grid account shows the MODELLED value (ruling R-W).

        The sibling inversion, for the kind whose accrual row reads
        "Appreciation".  It asserted the flat market value until plan step
        X-g3b; a house that appreciates has that appreciation on the grid now,
        with a row explaining it, and the figure equals what ``/savings`` and
        the net-worth trend already showed.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )

            view = balance_at.grid_balance_view(prop, bctx, periods)
            cash = balance_at.cash_balance_map(prop, bctx, periods)
            modelled = balance_at.balance_map(prop, bctx, periods)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(modelled)
            assert view.columns[periods[-1].id].balance > cash[periods[-1].id]
            assert any(
                column.accrual > Decimal("0.00")
                for column in view.columns.values()
            )
            # A Property has no payroll feed, whatever else it models.
            assert all(
                column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )
            _assert_grid_view_reconciles(view)

    def test_none_scenario_raises(
        self, app, db, seed_user, seed_periods_today,
    ):
        """grid_balance_view fails loud on a None scenario (seam contract)."""
        with app.app_context():
            user_id = seed_user["user"].id
            periods = pay_period_service.get_all_periods(user_id)
            with pytest.raises(ValueError):
                balance_at.grid_balance_view(
                    seed_user["account"], _no_baseline(user_id), periods,
                )

    def test_an_interest_account_with_no_params_models_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST account with no params row shows cash, no accrual row.

        A HYSA whose ``InterestParams`` is missing models no rate, so inventing
        one would put interest on screen the account has never earned.  The
        view must show the folded cash balance with a ``0.00`` accrual in every
        column, never raise -- the degenerate arm of
        ``_interest.accrual_params``.  ``0.00`` and not ``None``: ruling
        R-AJ (c) made both modelled fields total, so "models no rate" and
        "earned nothing this window" report the same figure and ruling R-O's
        rule hides the row for both.

        (This replaces a test that forced ``balance_map`` to return ``None``
        and asserted the grid degraded to cash.  ``grid_balance_view`` does not
        call ``balance_map`` any more -- it folds once and layers -- so the
        branch that test drove no longer exists; the params-less account is the
        reachable state that still exercises the same "no accrual" outcome.)
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            db.session.query(InterestParams).filter_by(
                account_id=hysa.id,
            ).delete()
            db.session.commit()
            db.session.refresh(hysa)

            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            view = balance_at.grid_balance_view(hysa, bctx, periods)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(cash)
            assert all(
                column.accrual == Decimal("0.00")
                and column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )

    def test_the_accrual_is_pure_interest_on_one_income_basis(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The accrual stays pure interest when live income != stored (M1).

        Regression guard for the income-basis trap ruling R-Q closes at the
        root.  The cash walk auto-built a LIVE income map while the
        kind-correct walk used the STORED amounts, so an interest account left
        on the defaults had its premium absorb the income recompute instead of
        being pure interest.  There is ONE walk now and it builds ONE map, so
        the divergence has no argument to arrive through -- and this pins the
        consequence.

        Forces live ($1,500) != stored ($1,000) on a real income transaction
        in a FUTURE period (so ruling R-G lands it in its own column) and
        asserts the accrual telescopes exactly to the premium over the cash
        basis, with the $500 income delta nowhere in it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            income_txn = Transaction(
                account_id=hysa.id,
                pay_period_id=periods[6].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Paycheck deposit",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1000.00"),
            )
            db.session.add(income_txn)
            db.session.commit()

            # Force live != stored: the live seam revalues this income at
            # $1,500 (vs the $1,000 stored).
            live = {income_txn.id: Decimal("1500.00")}
            monkeypatch.setattr(
                income_service, "live_projected_net",
                lambda uid, sid, txns: dict(live),
            )

            view = balance_at.grid_balance_view(hysa, bctx, periods)
            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            last = periods[-1].id

            # The shape has teeth only if the live basis genuinely lands: the
            # cash column carries the $1,500, not the stored $1,000.
            assert cash[periods[6].id] - cash[periods[5].id] == Decimal(
                "1500.00",
            )

            # The accrual telescopes to the premium over that SAME basis, and
            # the first period's figure is hand-derived so the pin is on the
            # rate and the day count, not on a band a regression could sit in:
            #   Q(5000 * ((1 + 0.05/365) ** 14 - 1)) = 9.60
            # On the STORED basis the premium would carry the $500 income
            # delta as well, which is the divergence ruling R-Q deleted.
            projected = dict(_all_columns(view))
            assert view.columns[periods[0].id].accrual == Decimal("9.60")
            total_accrual = sum(
                column.accrual for _pid, column in _all_columns(view)
            )
            assert total_accrual == projected[last].balance - cash[last]
            assert total_accrual < Decimal("500.00"), (
                "the accrual absorbed the income delta instead of being "
                "interest"
            )


def _column(
    *, balance="0.00", income="0.00", expense="0.00", net="0.00",
    reconciliation="0.00", contribution="0.00", accrual="0.00",
):
    """Build one hand-specified :class:`GridColumn` for the flag tests.

    The visibility rule is pure logic over a column's values and says nothing
    about where they came from, so it is graded on hand-built columns: a
    data-driven oracle would have to seed a shape for each of the rule's arms
    and would still only assert the rule through whatever those shapes happen
    to produce.  ``TestTheRemainderIsWhatTheRowsCannotExplain`` grades the
    producer-built remainder itself.

    Every parameter is a STRING and every field is a ``Decimal``: neither
    modelled tier is optional since plan step X-g3a (ruling R-AJ (c)), so
    ``None`` is not a value this builder can produce and no test can assert a
    shape the producer cannot be in.
    """
    return balance_at.GridColumn(
        balance=Decimal(balance),
        income=Decimal(income),
        expense=Decimal(expense),
        net=Decimal(net),
        reconciliation=Decimal(reconciliation),
        contribution=Decimal(contribution),
        accrual=Decimal(accrual),
    )


class TestGridRowFlags:
    """``GridBalanceView.row_flags`` -- ruling R-O's conditional-row rule.

    A conditional row renders for the whole visible window when at least ONE
    visible column carries a non-zero value.  Stated once here because the same
    rule governs three rows on four windows (the visible grid, the Plan tab, the
    mobile This Period card, and the two self-refresh partials), and a template
    deciding it per surface is how one form factor ends up rendering a balance
    its own figures cannot explain (ruling R-P).
    """

    @staticmethod
    def _view(columns):
        """Wrap *columns* in a view (the flags read nothing else)."""
        return balance_at.GridBalanceView(
            columns=OrderedDict(columns), amount_overrides={},
        )

    @staticmethod
    def _periods(*ids):
        """Return period stand-ins carrying only the id the rule reads."""
        return [SimpleNamespace(id=pid) for pid in ids]

    def test_all_zero_window_renders_no_row(self):
        """Every column zero -> all three rows hidden (the ordinary cash grid)."""
        view = self._view({1: _column(), 2: _column()})
        flags = view.row_flags(self._periods(1, 2))
        assert flags.reconciliation is False
        assert flags.contribution is False
        assert flags.accrual is False

    def test_one_non_zero_column_renders_the_row_for_the_window(self):
        """A single non-zero column turns the row on for the whole window."""
        view = self._view({
            1: _column(),
            2: _column(reconciliation="-788.68"),
            3: _column(),
        })
        assert view.row_flags(self._periods(1, 2, 3)).reconciliation is True

    def test_the_rule_is_per_window_not_per_account(self):
        """A window that excludes the non-zero column hides the row.

        The flag is asked of the VISIBLE periods, so navigating away from the
        period that carries a remainder drops the row rather than leaving a
        permanently-zero line on the forward-looking windows -- the half of
        ruling R-O that rejected always-on.
        """
        view = self._view({1: _column(reconciliation="12.00"), 2: _column()})
        assert view.row_flags(self._periods(2)).reconciliation is False
        assert view.row_flags(self._periods(1, 2)).reconciliation is True

    def test_a_negative_remainder_counts_as_non_zero(self):
        """The rule is non-zero, not positive -- timing swings both ways."""
        view = self._view({1: _column(reconciliation="-0.01")})
        assert view.row_flags(self._periods(1)).reconciliation is True

    def test_an_all_zero_accrual_window_hides_the_accrual_row(self):
        """A window that earns nothing hides the row -- a labelled row of zeros.

        Both an account that models NO return and one whose visible window
        happens to earn nothing report ``0.00`` here, and ruling R-O removes the
        row in both cases.  The two used to be distinguishable -- ``None`` for
        the first, ``0.00`` for the second -- and ruling R-AJ (c) deleted that
        distinction: under one replay every column carries a ``Decimal``, so
        ``None`` is a state the producer cannot be in and a flag rule testing
        for it would be testing an unreachable arm.
        """
        view = self._view({1: _column(), 2: _column(accrual="0.00")})
        assert view.row_flags(self._periods(1, 2)).accrual is False

    def test_a_non_zero_accrual_renders_the_accrual_row(self):
        """One accruing column turns the modelled-return row on."""
        view = self._view({1: _column(accrual="0.00"), 2: _column(accrual="7.01")})
        assert view.row_flags(self._periods(1, 2)).accrual is True

    def test_a_negative_accrual_renders_the_accrual_row(self):
        """A market LOSS turns the row on -- the rule is non-zero, not positive.

        The two kinds ruling R-W adds to this row are bounded only ``> -1``
        (``investment_params`` / ``asset_appreciation_params``), so a
        depreciating asset or a down market accrues negative.  A rule that
        tested for a positive figure would hide exactly the column the user most
        needs to see (finding N-88's other half; the rendering of that sign is
        graded in ``tests/test_routes/test_grid.py``).
        """
        view = self._view({1: _column(accrual="-142.11")})
        assert view.row_flags(self._periods(1)).accrual is True

    def test_a_non_zero_contribution_renders_the_contribution_row(self):
        """One contributing column turns the Contributions row on.

        The third flag is its own predicate over its own field: the two modelled
        tiers answer different questions (what the market did, and what the user
        put in), and a single summed row can render POSITIVE on an account that
        LOST money -- measured on the real Empower 401(k) at a -10.5% return,
        ``-$7,366.83`` of market against ``+$9,624.27`` of payroll (ruling
        R-AH).  So the two rows appear and disappear independently.
        """
        view = self._view({
            1: _column(contribution="0.00"),
            2: _column(contribution="181.59"),
        })
        flags = view.row_flags(self._periods(1, 2))
        assert flags.contribution is True
        assert flags.accrual is False

    def test_an_accruing_window_that_contributes_nothing_hides_only_that_row(self):
        """An HYSA accrues and never contributes -- one row, not two.

        ``_asset_contributions.contribution_events`` returns ``[]`` for every
        kind but INVESTMENT, so an interest-bearing account's Contributions row
        can never render.  Pinned as a flag property rather than left to the
        producer, because it is the rule that keeps a permanently-zero row off
        the HYSA grid the developer actually uses.
        """
        view = self._view({1: _column(accrual="95.98"), 2: _column(accrual="96.30")})
        flags = view.row_flags(self._periods(1, 2))
        assert flags.accrual is True
        assert flags.contribution is False

    def test_a_period_outside_the_view_contributes_nothing(self):
        """A window period the projection never produced cannot flip a flag."""
        view = self._view({1: _column(reconciliation="5.00")})
        assert view.row_flags(self._periods(99)).reconciliation is False


class TestTheReconciliationOracleSeesAllFourTerms:
    """``_assert_grid_view_reconciles`` is the CUTOVER's oracle, so grade it.

    Plan step X-g3b moves every modelled account's grid balance, and this
    helper is what will police that the rows still explain it.  At X-g3a no
    PRODUCER can put a figure in ``contribution`` -- the kind gate admits only
    INTEREST accounts and their contribution feed is empty by construction --
    so an oracle written in the three-term form and one written in the four-term
    form are indistinguishable on every fixture in the suite.  That is exactly
    the state in which a term gets dropped and nothing notices.

    The helper takes a VIEW, not an account, so the control needs no producer
    and no fixture: a hand-built view whose balance delta contains a
    contribution reconciles under the four-term form and cannot reconcile under
    the three-term one.  Section 7.3 -- every guard gets a negative control that
    is shown to fire -- and this is the guard that guards the money.
    """

    @staticmethod
    def _view(columns):
        """Wrap *columns* in a view (the oracle reads nothing else)."""
        return balance_at.GridBalanceView(
            columns=OrderedDict(columns), amount_overrides={},
        )

    # The figures are ruling R-AH's own: $181.59 is the employer's flat 5% of
    # $3,631.74 -- the term whose omission breaks the identity on 53 of 59 real
    # period pairs on the Empower 401(k) -- and $95.98 is the HYSA accrual this
    # file hand-derives elsewhere.
    _NET = "100.00"
    _RECONCILIATION = "10.00"
    _CONTRIBUTION = "181.59"
    _ACCRUAL = "95.98"

    def _two_columns(self, *, contribution):
        """Return an opening column and one whose delta is all four terms."""
        opening = _column(balance="1000.00")
        moved = _column(
            balance=str(
                Decimal("1000.00") + Decimal(self._NET)
                + Decimal(self._RECONCILIATION) + Decimal(contribution)
                + Decimal(self._ACCRUAL)
            ),
            net=self._NET,
            reconciliation=self._RECONCILIATION,
            contribution=contribution,
            accrual=self._ACCRUAL,
        )
        return self._view({1: opening, 2: moved})

    def test_a_contributing_column_reconciles(self):
        """The four-term form holds when all four terms carry a figure."""
        _assert_grid_view_reconciles(
            self._two_columns(contribution=self._CONTRIBUTION),
        )

    def test_dropping_the_contribution_term_breaks_the_identity(self):
        """THE CONTROL: a delta short its contribution must not reconcile.

        Built by moving the contribution OUT of the column while leaving it in
        the balance delta, which is precisely what a three-term oracle would
        wave through -- and what X-g3b would ship if the term were dropped from
        the helper.  The failure message must name the term, so a real break
        points at the tier rather than at "the numbers disagree".
        """
        broken = self._two_columns(contribution=self._CONTRIBUTION)
        columns = OrderedDict(broken.columns)
        moved = columns[2]
        columns[2] = balance_at.GridColumn(
            balance=moved.balance,
            income=moved.income,
            expense=moved.expense,
            net=moved.net,
            reconciliation=moved.reconciliation,
            contribution=Decimal("0.00"),
            accrual=moved.accrual,
        )

        # The pattern matches the MESSAGE, not the repr.  ``match=`` searches
        # the whole rendered AssertionError, and pytest's rewriting appends the
        # GridColumn repr -- which contains the literal ``contribution=`` for
        # every column -- so a bare ``match="contribution"`` passes even when
        # the message never names the term.  ``+ contribution `` (a space, no
        # equals) appears only in the oracle's own sentence.
        with pytest.raises(AssertionError, match=r"\+ contribution "):
            _assert_grid_view_reconciles(self._view(columns))

    def test_the_accrual_term_is_load_bearing_too(self):
        """The same control for the tier the row already renders.

        Stated so the oracle is graded on BOTH modelled terms rather than only
        the new one: an oracle that had quietly lost its accrual term would
        also pass every INTEREST fixture whose window happens not to accrue.
        """
        view = self._two_columns(contribution=self._CONTRIBUTION)
        columns = OrderedDict(view.columns)
        moved = columns[2]
        columns[2] = balance_at.GridColumn(
            balance=moved.balance,
            income=moved.income,
            expense=moved.expense,
            net=moved.net,
            reconciliation=moved.reconciliation,
            contribution=moved.contribution,
            accrual=Decimal("0.00"),
        )

        with pytest.raises(AssertionError, match=r"\+ accrual "):
            _assert_grid_view_reconciles(self._view(columns))


class TestTheViewOwnsTheLiveOverrideMap:
    """Ruling R-Q: the seam builds the live map and hands it back.

    The map is a balance INPUT, so the producer that folds it owns it.  Before
    this it was the caller's argument and the grid route built a second copy for
    its cells -- "provably identical" by an argument about which rows each side
    filters (finding N-48), which is the agreeing-by-coincidence shape this arc
    exists to end.  Handing the same object back makes the cells and the balance
    row one map by construction.
    """

    def test_the_view_returns_the_map_it_projected_with(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """``amount_overrides`` is the live map, not an empty courtesy field."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            income_txn = Transaction(
                account_id=account.id,
                pay_period_id=periods[1].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Paycheck",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1000.00"),
            )
            db.session.add(income_txn)
            db.session.commit()
            monkeypatch.setattr(
                income_service, "live_projected_net",
                lambda uid, sid, txns: {income_txn.id: Decimal("1500.00")},
            )

            view = balance_at.grid_balance_view(account, bctx, periods)

            assert view.amount_overrides == {income_txn.id: Decimal("1500.00")}
            # And the projection actually used it: the live $1,500 lands in
            # the column, not the stored $1,000.
            assert view.columns[periods[1].id].income == Decimal("1500.00")


class TestTheRemainderIsWhatTheRowsCannotExplain:
    """Ruling R-K's remainder, measured on producer-built columns.

    This class asserted the opposite until plan step X-c2b2: the remainder was
    a constant ``0.00``, and that was a true claim about the SHIPPING
    producers rather than a placeholder, because the balance row and the
    subtotal row both counted exactly the still-unpaid rows of one
    anchor-seeded walk.  Neither could see a settled row at all, so neither
    clock had anything to disagree about -- and every past column read ``$0.00``
    income and ``$0.00`` expenses while thousands of dollars moved through it
    (finding N-41).

    Now the subtotals count EVERY row attributed to the period and the balance
    counts money that MOVED, so the remainder carries what the two clocks
    disagree about.  What is asserted here is the pair of properties that makes
    it trustworthy: it is ``0.00`` exactly when nothing needs explaining, and
    it is the exact size of the disagreement when something does.
    """

    def test_a_period_where_everything_lands_in_its_own_column_has_no_remainder(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Still-projected rows landing in their own columns explain themselves.

        A projected income and a projected expense, both in FUTURE periods so
        ruling R-G's clamp leaves them where they were budgeted: the budget
        clock and the cash clock agree on every column, so the remainder is
        ``0.00`` throughout and ruling R-O hides the row.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            _seed_grid_activity(db, seed_user, periods)

            view = balance_at.grid_balance_view(account, bctx, periods)

            assert view.columns
            moved = [
                pid for pid, column in view.columns.items()
                if column.net != Decimal("0.00")
            ]
            assert moved, "the fixture must move money or this proves nothing"
            assert all(
                column.reconciliation == Decimal("0.00")
                for column in view.columns.values()
            )
            assert view.row_flags(periods).reconciliation is False
            _assert_grid_view_reconciles(view)

    def test_a_row_that_settled_in_another_column_is_exactly_the_remainder(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Budgeted here, paid there: both columns carry the timing difference.

        A ``$300.00`` expense budgeted to period 1 but settled inside period 3.
        Hand-computed: period 1 counts it on the BUDGET clock, so its net is
        ``-$300.00`` while no money moved there -- remainder ``+$300.00``.
        Period 3 counts nothing on the budget clock while ``-$300.00`` moved
        through it -- remainder ``-$300.00``.  The two net to zero across the
        window, which is why the row is called "Timing & true-ups" and not a
        correction.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_cash_transaction

        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, db.session, periods[1], Decimal("300.00"),
                paid_at=settle_instant_on(periods[3].start_date),
                name="paid two columns late",
            )
            db.session.commit()

            view = balance_at.grid_balance_view(account, bctx, periods)

            assert view.columns[periods[1].id].net == Decimal("-300.00")
            assert view.columns[periods[1].id].reconciliation == Decimal(
                "300.00",
            )
            assert view.columns[periods[3].id].net == Decimal("0.00")
            assert view.columns[periods[3].id].reconciliation == Decimal(
                "-300.00",
            )
            assert view.row_flags(periods).reconciliation is True
            _assert_grid_view_reconciles(view)


def _seed_grid_activity(db, seed_user, periods):
    """Add one projected income and one projected expense to the grid account.

    Gives the columns something to be non-zero ABOUT: an all-empty account
    reports zeros for every figure, so the identity would hold vacuously and
    the assertions above would prove nothing.

    Both rows are dated FORWARD of the read's as-of (``seed_periods_today``
    places today in period 4), so ruling R-G's clamp leaves them in the columns
    they were budgeted to.  A past-dated still-projected row would land at
    ``as_of + 1`` instead -- correct, and the subject of the sibling test.
    """
    scenario = get_baseline_scenario(seed_user["user"].id)
    account = seed_user["account"]
    db.session.add(Transaction(
        account_id=account.id,
        pay_period_id=periods[6].id,
        scenario_id=scenario.id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name="Paycheck",
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        estimated_amount=Decimal("2400.00"),
    ))
    db.session.add(Transaction(
        account_id=account.id,
        pay_period_id=periods[7].id,
        scenario_id=scenario.id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name="Rent",
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal("1450.00"),
    ))
    db.session.commit()


class TestLiabilityOwedAtDates:
    """``liability_owed_at_dates``: the seam's FORWARD multi-date liability view.

    The seam entry that closed the W9906 fence hole
    (``docs/audits/balance_architecture/followup_fence_loan_owed_at_dates.md``):
    the horizon liability band used to reach past the seam into
    ``net_worth_kernel.loan_owed_at_dates`` and hold half the boundary rule (the
    non-amortizing flat carry) itself.  These tests pin BOTH forward rules, the
    today-point source, the sign convention, and the forward-only domain.
    """

    def test_amortizing_loan_amortizes_across_future_dates(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A mortgage's owed balance strictly declines across future sample dates."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            samples = [
                today,
                date(today.year + 1, 12, 31),
                date(today.year + 2, 12, 31),
            ]

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, samples,
                {acct.id: Decimal("200000.00")},
            )

            series = owed[acct.id]
            assert len(series) == len(samples)
            # Today is the caller's confirmed balance; each later year end has
            # had another year of scheduled principal applied, so the owed
            # balance falls monotonically.
            assert series[0] == Decimal("200000.00")
            assert series[1] < series[0]
            assert series[2] < series[1]

    def test_today_point_is_the_callers_confirmed_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The today point comes from *current_balances*, never a schedule walk.

        Load-bearing: the caller's current balance is the ledger-confirmed figure
        the net-worth hero renders, so a band built on this reconciles with the
        hero at index 0 by construction.  A schedule walk at ``today`` would
        instead report the balance net of any OVERDUE unconfirmed payment (the
        due-basis rows stay in the forward walk), UNDERSTATING the debt.

        Pinned by passing a current balance the schedule could not possibly
        produce: it must come back verbatim at index 0.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            sentinel = Decimal("123456.78")

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [date.today()], {acct.id: sentinel},
            )

            assert owed[acct.id] == [sentinel]

    def test_non_amortizing_liability_holds_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A revolving Credit Card has no forward model: flat owed magnitude.

        The rule that used to live in the horizon consumer.  Also pins the sign
        convention: a card's cash balance is NEGATIVE, and the seam returns the
        POSITIVE owed magnitude (matching the net-worth reduction's
        liability-minus rule, ``abs(bal)`` subtracted from the asset side).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            today = date.today()
            samples = [today, date(today.year + 1, 12, 31),
                       date(today.year + 5, 12, 31)]

            owed = balance_at.liability_owed_at_dates(
                [card], bctx, samples, {card.id: Decimal("-500.00")},
            )

            # abs(-500) held flat at every sample -- no forward model.
            assert owed[card.id] == [Decimal("500.00")] * 3

    def test_no_baseline_scenario_holds_every_liability_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``scenario=None`` is the degenerate case of the SAME rule, not an error.

        No baseline means no loan is resolvable, so every liability -- including
        an amortizing mortgage -- falls to the no-forward-model flat hold.  This
        is the one public seam entry that does NOT raise on a None scenario: it
        has a correct answer, and raising would force each caller to re-derive
        the flat hold (the very duplication the seam exists to prevent).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            samples = [today, date(today.year + 1, 12, 31)]

            owed = balance_at.liability_owed_at_dates(
                [acct], _no_baseline(user_id), samples, {acct.id: Decimal("200000.00")},
            )

            assert owed[acct.id] == [Decimal("200000.00")] * 2

    def test_liability_missing_from_current_balances_is_zero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account absent from *current_balances* reads 0, not a KeyError."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            owed = balance_at.liability_owed_at_dates(
                [card], bctx, [date.today()], {},
            )

            assert owed[card.id] == [Decimal("0")]

    def test_past_sample_date_raises(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A past date is a LEDGER read, not a projection -- the seam refuses it.

        The forward-only domain is a runtime invariant, not a docstring note.  A
        consumer that wants a loan's past balance must ask ``balance_at``, which
        routes an amortizing account's past to the genesis ledger (the only
        complete record -- it books the true-ups that have no schedule row).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            yesterday = date.today() - timedelta(days=1)

            with pytest.raises(ValueError, match="FORWARD"):
                balance_at.liability_owed_at_dates(
                    [acct], bctx, [yesterday, date.today()],
                    {acct.id: Decimal("200000.00")},
                )

    def test_projection_is_joined_by_date_not_by_position(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Sample dates are joined BY DATE, so their ORDER cannot mis-value the band.

        The producer's returned list must never be consumed positionally.  If it
        were, de-duplicating or sorting the sample dates inside
        ``loan_owed_at_dates`` -- a natural optimization, since the schedule walk
        is the expensive part -- would silently shift every point of the
        liability band with no crash and no failing test.

        Pinned by passing the samples OUT of chronological order: the +2y point
        must still owe LESS than the +1y point, whatever order they arrive in.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            plus_one = date(today.year + 1, 12, 31)
            plus_two = date(today.year + 2, 12, 31)

            # Deliberately unsorted: today, +2y, +1y.
            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [today, plus_two, plus_one],
                {acct.id: Decimal("200000.00")},
            )

            series = owed[acct.id]
            assert series[0] == Decimal("200000.00")
            # series[1] is the +2y sample and series[2] the +1y sample, so the
            # LATER date must owe strictly less -- the opposite of what a
            # positional join against a sorted producer list would return.
            assert series[1] < series[2], (
                "the +2y sample owes more than the +1y sample: the forward "
                "projection was joined by POSITION, not by date"
            )

    def test_mixed_liability_set_in_one_call(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan and a card in ONE call: both forward rules coexist in one result.

        The batch shape the sole caller actually passes.  The amortizing account
        must amortize while the non-amortizing one holds flat, in the same result
        dict -- the case where the splice and the flat carry have to coexist.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            today = date.today()
            samples = [today, date(today.year + 1, 12, 31)]

            owed = balance_at.liability_owed_at_dates(
                [acct, card], bctx, samples,
                {acct.id: Decimal("200000.00"), card.id: Decimal("-500.00")},
            )

            assert set(owed) == {acct.id, card.id}
            # The loan amortizes; the card has no forward model and holds flat.
            assert owed[acct.id][1] < owed[acct.id][0]
            assert owed[card.id] == [Decimal("500.00"), Decimal("500.00")]

    def test_forward_owed_credits_only_future_installments_not_overdue_ones(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The band's forward point folds the PLAN: overdue unpaid installments pay nothing (B-9).

        A mortgage originated a year ago with NO payment records carries a year of
        overdue unconfirmed installments.  The retired forward walk credited every
        one of them, reporting the loan paid down for installments nobody made
        (finding B-9); the band now folds the forward PLAN, which synthesizes only
        installments due AFTER today, so an overdue-unpaid one no longer shrinks a
        future point.  Pinned by the divergence from the contractual walk (the band
        owes STRICTLY MORE, by the overdue principal the walk over-credited) and by
        the amortization the FUTURE installments still produce.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            acct, _params = _make_mortgage(
                db, seed_user,
                pay_period_service.get_all_periods(user_id)[0],
                Decimal("200000.00"), date.today() - timedelta(days=365),
            )
            today = date.today()
            far_out = date(today.year + 1, 12, 31)

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [today, far_out],
                {acct.id: Decimal("200000.00")},
            )

            debt = net_worth_kernel.generate_debt_schedules(
                [acct], bctx,
            )[acct.id]
            forward_rows = sorted(
                (row for row in debt.schedule if not row.is_confirmed),
                key=lambda row: row.payment_date,
            )
            # The fixture really is in the hazardous state: a year of overdue
            # unconfirmed installments the retired walk would have credited.
            overdue = [r for r in forward_rows if r.payment_date <= today]
            assert len(overdue) > 6, "expected a run of overdue unconfirmed rows"
            due_by = [r for r in forward_rows if r.payment_date <= far_out]
            contractual_walk = (
                due_by[-1].remaining_balance if due_by
                else debt.projection_seed
            )

            # The band credits NONE of the overdue installments, so its forward
            # point owes strictly more than the contractual walk that credited them.
            assert owed[acct.id][1] > contractual_walk
            # It still amortizes the FUTURE installments below today's balance.
            assert owed[acct.id][1] < owed[acct.id][0]

    def test_today_point_ignores_overdue_rows_that_would_understate_the_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An OVERDUE unpaid payment must not shrink the today point.

        This fixture is the real hazard, not a contrived one: the mortgage was
        originated a year ago with NO payments recorded, so its schedule carries
        a dozen UNCONFIRMED rows already past due.  The retired forward walk
        deliberately kept overdue rows in its walk (the project's due-basis
        treatment), so a schedule walk AT TODAY reports the balance net of
        payments that were never made -- thousands of dollars less than the loan
        actually owes.

        The seam must not do that.  Its today point is the caller's
        ledger-confirmed balance, full stop.  Pinned by asserting the two
        differ: the contractual schedule walk (recomputed inline as an independent
        oracle) understates, and the seam does not follow it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            confirmed = Decimal("200000.00")

            debt = net_worth_kernel.generate_debt_schedules(
                [acct], bctx,
            )[acct.id]
            forward_rows = sorted(
                (row for row in debt.schedule if not row.is_confirmed),
                key=lambda row: row.payment_date,
            )
            overdue = [row for row in forward_rows if row.payment_date <= today]
            # The fixture really is in the hazardous state.
            assert overdue, "expected overdue unconfirmed rows in this fixture"
            # The contractual walk (last unconfirmed row on-or-before today),
            # recomputed inline -- the understating value the seam must NOT follow.
            walk_at_today = overdue[-1].remaining_balance
            assert walk_at_today < confirmed, (
                "a schedule walk at today should understate the debt here"
            )

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [today], {acct.id: confirmed},
            )

            # The seam reports what is OWED, not what the schedule wishes was paid.
            assert owed[acct.id] == [confirmed]
            assert owed[acct.id][0] != walk_at_today


class TestLoanNotYetOriginated:
    """An upcoming loan owes NOTHING until it originates.

    ``origination_date`` carries no not-future validator (unlike a true-up's
    ``anchor_date``), so configuring a mortgage that closes next month is a
    legitimate, reachable state -- and the developer ruled (2026-07-13) that the
    app must SUPPORT it.

    Before the origination-event fix the app reported the loan's FULL PRINCIPAL as
    owed at every pay period, for months before it closed.  The cause was not in
    the seam: ``select_latest_anchor`` picks the latest anchor BY DATE with no
    ``as_of`` filter, so the resolver seeded ``current_balance`` from an anchor
    dated in the FUTURE.  Six surfaces outside the seam read that field.

    The loan here originates 2026-04-15 -- AFTER the suite's frozen today
    (2026-03-20) and INSIDE the seeded period range -- so all four regions of its
    trajectory are assertable:

      1. before origination                 -> $0.00 (it does not exist)
      2. originated, before first payment   -> $200,000.00 (the opening balance)
      3. after the first payment            -> amortizing
      4. ``current_balance`` today          -> $0.00 (it owes nothing)

    Arithmetic (200,000 @ 5.000% / 360 months, payment day 1):
      monthly P&I = 1,073.64
      first payment 2026-05-01: interest = round(200000 * 0.05/12) = 833.33;
                                principal = 1073.64 - 833.33 = 240.31;
                                remaining = 200000.00 - 240.31 = 199,759.69
    """

    ORIGINATION = date(2026, 4, 15)
    OPENING = Decimal("200000.00")
    AFTER_FIRST_PAYMENT = Decimal("199759.69")
    ZERO = Decimal("0.00")

    def _upcoming_mortgage(self, seed_user, db_session, periods):
        """Create the mortgage that has not closed yet."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account

        return create_loan_account(
            seed_user, db_session, name="Closing In April",
            principal=self.OPENING, rate=Decimal("0.05000"),
            term=360, origination_date=self.ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
        )

    def test_scalar_owes_nothing_before_origination_then_its_opening(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_at across all four regions of the trajectory."""
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            assert bctx.as_of == date(2026, 3, 20)

            # 1. The PAST, before it exists.  The ledger has no opening posting
            #    for it -- correctly, nothing has happened -- and the projection
            #    answers the honest zero.
            assert balance_at.balance_at(
                acct, bctx, date(2026, 2, 1)) == self.ZERO
            # ...including TODAY.  This read $200,000.00 before the fix.
            assert balance_at.balance_at(
                acct, bctx, bctx.as_of) == self.ZERO
            # 2. The FUTURE, still before it exists.
            assert balance_at.balance_at(
                acct, bctx, date(2026, 4, 14)) == self.ZERO
            # 3. Originated, before the first payment: the full opening balance.
            assert balance_at.balance_at(
                acct, bctx, date(2026, 4, 20)) == self.OPENING
            assert balance_at.balance_at(
                acct, bctx, date(2026, 4, 30)) == self.OPENING
            # 4. After the first payment (2026-05-01): amortizing.
            assert balance_at.balance_at(
                acct, bctx, date(2026, 5, 7)) == self.AFTER_FIRST_PAYMENT

    def test_period_map_owes_nothing_before_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-period map walks the same four regions as the scalar.

        Periods 0-5 have BEGUN (they start on or before the frozen today), so
        they read the ledger -- which knows nothing about this loan, and whose
        zero is TRUE here: the loan did not exist in any of them.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            bmap = balance_at.balance_map(acct, bctx, periods)

            # Periods 0-5: BEGUN, all before origination.  Every one read
            # $200,000.00 before the fix.
            for period in periods[:6]:
                assert period.start_date <= bctx.as_of
                assert bmap[period.id] == self.ZERO
            # Period 6 (2026-03-27..2026-04-09): future, still pre-origination.
            assert bmap[periods[6].id] == self.ZERO
            # Period 7 (2026-04-10..2026-04-23): contains the 2026-04-15
            # origination, and its end precedes the first payment (2026-05-01),
            # so the loan owes exactly its opening balance.
            assert bmap[periods[7].id] == self.OPENING
            # Periods 8-9: the first payment (2026-05-01) has landed.
            assert bmap[periods[8].id] == self.AFTER_FIRST_PAYMENT
            assert bmap[periods[9].id] == self.AFTER_FIRST_PAYMENT

    def test_current_period_agrees_with_the_hero_when_origination_is_inside_it(
        self, app, db, seed_user, seed_periods,
    ):
        """The cross-page guard: origination INSIDE the current pay period.

        This is the shape that killed the first design of the fix.  The forward
        projection is period-END keyed.  The current period runs
        2026-03-13..2026-03-26 and today is 2026-03-20, so a loan originating
        2026-03-25 sits inside it -- AFTER today but BEFORE the period ends.

        Valuing the current period at its END therefore reports the full
        $200,000.00 while the hero (``balance_at`` today) reports $0.00: one page
        contradicting itself about one loan on one day, which is the exact failure
        this arc exists to delete.  The map's current-period CLAMP -- sampling
        ``positions`` at ``min(period.end, ctx.as_of)`` -- is what prevents it,
        keeping the current period on today's value, and this test is why the
        clamp holds for EVERY loan with no exception.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from app.services.balance_at import positions
        from tests._test_helpers import create_loan_account

        with app.app_context():
            periods = seed_periods
            acct = create_loan_account(
                seed_user, db.session, name="Closing Friday",
                principal=self.OPENING, rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 3, 25), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
            )
            bctx = BalanceContext.build(seed_user["user"].id)
            current = next(
                p for p in periods
                if p.start_date <= bctx.as_of <= p.end_date
            )
            assert current.start_date == date(2026, 3, 13)
            assert current.end_date == date(2026, 3, 26)

            hero = balance_at.balance_at(acct, bctx, bctx.as_of)
            trend = balance_at.balance_map(acct, bctx, periods)[current.id]
            assert hero == self.ZERO
            assert trend == hero, (
                "the /savings hero and the net-worth trend's current-period "
                f"point disagree: hero={hero} trend={trend}"
            )

            # Negative control: sampling positions() at the period END -- what the
            # map would do WITHOUT the current-period clamp -- really does report
            # the full opening here (period end 2026-03-26 is after origination
            # 2026-03-25).  The clamp to ctx.as_of is load-bearing, not decorative.
            unclamped = positions(acct, bctx, [current.end_date])
            assert unclamped[current.end_date] == self.OPENING

    def test_liability_band_owes_nothing_before_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """The long-horizon liability band walks the same trajectory."""
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            current = balance_at.balance_at(acct, bctx, bctx.as_of)

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx,
                [date(2026, 4, 14), date(2026, 4, 20), date(2026, 5, 7)],
                {acct.id: current},
            )
            assert owed[acct.id] == [
                self.ZERO, self.OPENING, self.AFTER_FIRST_PAYMENT,
            ]

    def test_seam_reports_nothing_owed_today(
        self, app, db, seed_user, seed_periods,
    ):
        """An unclosed mortgage owes $0.00 on every surface -- the fold's rule.

        The loan detail page's "current principal", the payoff and refinance
        calculators, home equity, and the property equity chart all read the
        seam's folded balance (plan step D2a deleted the resolver's
        ``current_balance`` field; the fold answers ``0.00`` for a date before
        any event -- the honest fold of an empty prefix).  The pre-D2a resolver
        guard existed because its replay reported $200,000.00 for a mortgage
        that had not closed; the fold needs no guard, since a future-dated
        origination event simply has not happened yet.

        The SCHEDULE must survive: it is what the projection walks once
        the loan closes, and filtering the anchor out of ``_replay_from_anchor``
        (rather than the balance following the events) would have collapsed it
        to nothing.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            resolved = resolved_loan(acct, bctx)

            assert balance_at.balance_at(acct, bctx, bctx.as_of) == self.ZERO
            # The schedule is intact: 360 contractual rows from the first payment.
            assert len(resolved.state.schedule) == 360
            assert resolved.state.schedule[0].payment_date == date(2026, 5, 1)
            assert (resolved.state.schedule[0].remaining_balance
                    == self.AFTER_FIRST_PAYMENT)

    def test_not_paid_off_even_with_a_confirmed_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan that has not originated is NOT retired -- the R3 guard.

        ``_is_retired`` is ``is_originated AND (current_balance <= 0)``, and
        ``_is_paid_off`` is that PLUS "the ledger shows a confirmed payment".
        Zeroing ``current_balance`` (above) satisfies the balance clause, and a
        confirmed payment satisfies the payment clause.  **Only the origination
        guard is left standing between an unclosed mortgage and RETIRED**: badged
        paid off, dropped from the debt card's total, gone from the Horizon, and
        erased from the property equity chart.

        This is the ONE shape in which that guard is the guard doing the work --
        every other unclosed-mortgage test has no confirmed payment, so the payment
        clause would carry it regardless.  NEGATIVE CONTROL: delete the
        ``_is_originated`` check from ``_is_retired`` and this test goes red.

        This bug did not exist before the origination fix; the fix CREATES it, and
        this is the test that keeps it dead.

        **How the confirmed payment is built changed at plan step C9b, and the
        new construction is the more honest one.**  It used to be a settled
        transfer into the loan placed in ``periods[4]`` -- whose installment
        (2026-03-01, from the period-start fallback) precedes the 2026-04-15
        origination.  Ruling R-C now REFUSES that write at the transfer boundary,
        because the fold erases such a payment: it splits against a zero balance,
        books $0.00 principal, routes the whole amount to a Refund Receivable, and
        the origination anchor then resets over it -- while the cash still leaves
        checking (finding FU-5).  A test may not rest on a write production
        forbids.

        So the payment is now one production CAN produce: its installment
        (2026-05-01, from ``periods[8]``) falls AFTER origination, so the boundary
        allows it, and it is SETTLED EARLY -- ``paid_at`` 2026-03-10, before the
        loan closes.  Paying an installment ahead of closing is legitimate and
        the fold splits it correctly.  The state under test is unchanged: one
        confirmed payment, a ``0.00`` balance, and a loan that has not
        originated.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import (
            create_account_of_type, create_settled_transfer, settle_instant_on,
        )

        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            checking = create_account_of_type(
                seed_user, db.session, "Checking", "Chk",
                anchor_balance=Decimal("9000.00"),
            )
            db.session.commit()
            # periods[8] starts 2026-04-24, so this payment's installment is
            # 2026-05-01 -- after the 2026-04-15 origination, hence writable --
            # while its settle instant precedes the loan's existence.
            assert periods[8].start_date > self.ORIGINATION
            create_settled_transfer(
                seed_user, db.session, checking, acct, periods[8],
                amount=Decimal("1200.00"),
                paid_at=settle_instant_on(date(2026, 3, 10)),
            )
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            resolved = resolved_loan(acct, bctx)
            # The two clauses that would otherwise conspire.
            assert any(p.is_confirmed for p in resolved.context.payments)
            assert balance_at.balance_at(acct, bctx, bctx.as_of) == self.ZERO

            figures = balance_at.loan_figures(acct, bctx)
            assert figures.is_paid_off is False
            # And the chart's drop rule holds the same line, from the same guard:
            # an unborrowed mortgage is not RETIRED either, so it stays charted.
            assert figures.is_retired is False

    def test_the_day_it_closes_the_seam_answers_without_a_re_sync(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """B-1: the loan must not break simply because its closing date arrived.

        The loan is configured on 2026-03-20 (the suite's frozen today) to close
        on 2026-04-15.  Then the CLOCK MOVES to 2026-05-07 and nothing else
        happens -- which is production: no chokepoint fires because a date
        arrived, so no sync re-runs between configuring the loan and reading it.

        Before A3 the write walk dropped any anchor dated after its as-of, so the
        params-create sync posted NO OPENING for this loan.  The moment the clock
        passed 2026-04-15 the seam considered it originated, found no opening, and
        raised the fail-loud error -- taking the loan card, /savings, the net-worth
        trend, the Horizon and the property equity chart down with a 500, fired by
        the clock alone, with the user's data untouched and correct.  (A3 posts the
        opening, so no raise fires here now; and since the seam folds a missing
        opening from source, steps C3b1/C3b3, that read-outage class is retired
        entirely.)

        NEGATIVE CONTROL: restore the ``anchor.anchor_date <= as_of`` filter in
        ``loan_ledger.merge_anchor_and_payment_events`` and both asserts below
        raise.

        $200,000.00 owed, not $199,759.69: with the clock at 2026-05-07 the loan
        has originated, so the 2026-05-01 installment is now a PAST due date -- and
        with NO payment record behind it, an installment nobody paid pays nothing
        down (finding B-9 / plan D1).  The map's last period is future, but its
        window holds no future installment WITH a record, so the balance holds at
        the opening rather than amortizing a payment that never happened.  (In the
        sibling tests the clock stays at 2026-03-20, where the whole schedule is
        still ahead, so every installment is a projected ESTIMATED slot and the
        05-01 one DOES pay down -- hence ``AFTER_FIRST_PAYMENT`` there.)
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import freeze_today

        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)

            freeze_today(monkeypatch, date(2026, 5, 7))
            bctx = BalanceContext.build(seed_user["user"].id)
            assert bctx.as_of == date(2026, 5, 7)

            assert balance_at.balance_at(acct, bctx, bctx.as_of) == self.OPENING
            assert balance_at.balance_map(acct, bctx, periods)[
                periods[-1].id
            ] == self.OPENING


class TestUpcomingLoanDoesNotCorruptTheSurfaces:
    """The consumers that read a $0.00 balance as "this debt is gone".

    ``balance_at`` correctly answers ``$0.00`` for a loan that has not been
    borrowed yet.  Three surfaces then read that zero as "repaid" and reported the
    OPPOSITE of the truth.  All three were found by an adversarial review of the
    origination fix, which CREATED them -- the pre-fix code reported the loan's
    full principal as owed, which was wrong in a different direction and happened
    not to trip these particular consumers.

    ``LoanFigures.is_originated`` is the one fact that separates "owes nothing"
    from "has no debt ahead of it", and each test below is the negative control for
    one consumer that needs it.
    """

    ORIGINATION = date(2026, 4, 15)
    MORTGAGE = Decimal("200000.00")
    AUTO = Decimal("100000.00")

    def _both_loans(self, seed_user, db_session, periods):
        """An unclosed mortgage beside a real, never-paid auto loan."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import create_loan_account

        auto = create_loan_account(
            seed_user, db_session, name="Auto",
            principal=self.AUTO, rate=Decimal("0.06000"),
            term=60, origination_date=date(2026, 1, 5), payment_day=5,
            account_type=AcctTypeEnum.AUTO_LOAN, anchor_period=periods[0],
        )
        mortgage = create_loan_account(
            seed_user, db_session, name="Closing In April",
            principal=self.MORTGAGE, rate=Decimal("0.05000"),
            term=360, origination_date=self.ORIGINATION, payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
        )
        return auto, mortgage

    def test_debt_track_does_not_count_an_unborrowed_loan_as_repaid(
        self, app, db, seed_user, seed_periods,
    ):
        """The dashboard debt marker: 0% repaid, not 66.67%.

        ``_compute_principal_paid_fraction`` is an ALL-LOANS-EVER basis: every loan
        contributes its ``original_principal`` to the denominator, and a loan owing
        $0.00 therefore counts as fully repaid.  An unclosed $200,000 mortgage owes
        $0.00, so it landed in the denominator with its whole principal in the
        "paid" portion: beside a never-paid $100,000 auto loan the marker read
        200000/300000 = **66.67% of principal repaid** for a borrower who had
        repaid nothing.

        It also broke the marker's one design invariant -- monotonicity.  On
        closing day the mortgage's balance steps $0 -> $200,000 and the fraction
        would COLLAPSE from 66.67% to 0%.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._metrics import (
            _compute_principal_paid_fraction,
        )

        with app.app_context():
            periods = seed_periods
            auto, mortgage = self._both_loans(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            def _ad(acct):
                # This dict is hand-built, which is finding B-17: the
                # production builder (``_project_one_account``) is never
                # executed here, so a change to what IT publishes cannot fail
                # this test.  Plan step X-h owns the repair.  Plan step X-q
                # then MEASURED the cost of the pattern -- moving
                # ``_compute_principal_paid_fraction`` onto ``is_retired``
                # raised ``KeyError`` here, because the copy was one field
                # behind the builder it mirrors -- so the key is added and the
                # shape is left for the step that owns it.
                figures = balance_at.loan_figures(acct, bctx)
                return {
                    "loan_params": resolved_loan(acct, bctx).params,
                    "current_balance": balance_at.balance_at(
                        acct, bctx, bctx.as_of),
                    "is_retired": figures.is_retired,
                    "is_paid_off": figures.is_paid_off,
                    "is_originated": figures.terms.is_originated,
                }

            # The fixture really is in the hazardous state.
            assert _ad(mortgage)["current_balance"] == Decimal("0.00")
            assert _ad(mortgage)["is_retired"] is False
            assert _ad(mortgage)["is_paid_off"] is False
            assert _ad(mortgage)["is_originated"] is False
            assert _ad(auto)["is_originated"] is True

            fraction = _compute_principal_paid_fraction(
                [_ad(auto), _ad(mortgage)],
            )
            # The auto loan is originated and never paid: 0 of 100,000 repaid.
            # The mortgage is in NEITHER sum -- it has not been borrowed.
            assert fraction == Decimal("0")

    def test_property_chart_keeps_a_mortgage_that_closes_next_month(
        self, app, db, seed_user, seed_periods,
    ):
        """The equity chart: the upcoming mortgage is charted, not dropped.

        The chart dropped any loan whose ``current_balance <= 0`` -- a test whose
        INTENT is "retired", and which an unborrowed loan satisfies.  So a $200,000
        mortgage closing in 26 days vanished, the no-outstanding-debt fallback
        fired, and the page drew ten years of debt-free equity on a house that is
        about to carry a mortgage.

        Today's $0.00 debt is right.  The chart is a FORWARD projection, and it was
        omitting a real liability for its entire horizon.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from app.services import balance_at, property_equity_chart
        from tests._test_helpers import create_account_of_type

        with app.app_context():
            periods = seed_periods
            _auto, mortgage = self._both_loans(seed_user, db.session, periods)
            # The mortgage is secured BY a Property, as production's is: the seam
            # walks ``property_account.secured_loans``, so the fixture has to build
            # the real collateral link rather than hand the producer a list.
            house = create_account_of_type(
                seed_user, db.session, AcctTypeEnum.PROPERTY.value, "House",
                anchor_balance=Decimal("400000.00"),
            )
            db.session.flush()
            mortgage.collateral_account_id = house.id
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            series = balance_at.secured_loan_series(house, bctx)
            assert len(series) == 1
            loan = series[0]
            assert loan.account_id == mortgage.id
            # It owes nothing today -- and it is emphatically NOT done.  The seam's
            # ONE predicate carries the origination guard; the series no longer
            # carries a balance for the chart to re-derive "retired" from.
            assert loan.is_retired is False
            assert balance_at.balance_at(
                mortgage, bctx, bctx.as_of,
            ) == Decimal("0.00")

            chart = property_equity_chart.build_property_equity_chart(
                series, Decimal("400000.00"), Decimal("0.03000"), bctx.as_of,
            )
            assert chart.chart_state != property_equity_chart.CHART_STATE_NO_LOANS
            # The mortgage really is drawn: the debt line reaches its principal
            # once the loan closes.
            assert max(chart.debt) >= self.MORTGAGE - Decimal("500.00")


class TestBrokenLoanFailsLoud:
    """A configured, ORIGINATED loan whose POSTING ledger is missing.

    The seam used to fall back to the loan's amortization SCHEDULE when the genesis
    ledger could not answer for a past date.  A schedule row is a payment the
    borrower was SUPPOSED to make, not one they did, so the fallback quietly paid
    the debt down with money that never moved: a $240,000 loan originated 18 months
    ago and never paid read as $236,544.21 owed by the per-period map, while the
    scalar said $240,000 -- two producers, one loan, one day, $3,455.79 apart.  A
    wrong balance that looks plausible is worse than a page that fails, so both
    producers were briefly made to RAISE rather than walk the schedule.

    **Neither raises now -- both FOLD the loan's SOURCE facts (steps C3b1/C3b3).**
    The scalar ``balance_at.balance_at`` (C3b1) and the per-period map
    ``balance_at.balance_map`` (C3b3) both read
    :func:`~app.services.balance_at.positions`, which folds the loan's origination
    anchor and settled shadows -- neither of which lives in the posting ledger --
    so a missing OPENING posting is a repairable cache inconsistency (plan step
    E1), not a read-time outage (B-8).  They answer the CORRECT balance the
    schedule-walk fallback could not, without the posting cache; the fail-loud
    ``LoanLedgerNotOpenedError`` is retired.  The class name is kept for the B-21
    finding its cash-degrade value assertion pins.

    Replaces ``TestUnpaidScheduleRowsNeverReduceTheDebt``, which pinned the
    behaviour of the fallback this deletes.
    """

    def _broken_loan(self, seed_user, db_session, periods):
        """A configured loan whose genesis POSTING ledger has been removed."""
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import clear_loan_ledger, create_loan_account

        acct = create_loan_account(
            seed_user, db_session, name="Broken",
            principal=Decimal("240000.00"), rate=Decimal("0.06000"),
            term=360, origination_date=date(2024, 9, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
        )
        # The ONE way to build a ledger-less loan: production cannot make one.
        # It clears only the POSTINGS; the source facts (LoanParams + shadows) that
        # the fold reads are untouched.
        clear_loan_ledger(acct.id)
        return acct

    def test_scalar_folds_source_events_when_the_posting_ledger_is_missing(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_at answers a broken loan from the fold, not a raise (step C3b).

        The $240,000 loan originated 2024-09-01 and never paid; its posting ledger
        is cleared.  The scalar folds the loan's SOURCE facts -- the synthesized
        origination anchor ($240,000) plus its (empty) settled shadows -- and
        returns $240,000.00 held flat: the honest balance of a loan borrowed and
        never paid down.  The missing posting is a repairable cache miss (plan E1),
        not the read-time outage the old fail-loud raised, and NOT the
        schedule-walk's phantom $236,544.21 paid down by installments never made.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._broken_loan(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            # Originated 2024-09-01, no payments: the fold holds the origination
            # principal flat -- no debt paid, because no cash moved.
            assert balance_at.balance_at(acct, bctx, bctx.as_of) == (
                Decimal("240000.00")
            )

    def test_map_folds_source_events_when_the_posting_ledger_is_missing(
        self, app, db, seed_user, seed_periods,
    ):
        """balance_map on a broken loan folds from source, like the scalar (C3b3).

        The per-period map cut over to :func:`~app.services.balance_at.positions`
        at step C3b3, so it no longer raises for a broken loan -- it folds the same
        SOURCE facts the scalar does.  Originated 2024-09-01, never paid: every
        begun period holds the origination principal ($240,000.00) flat, no debt
        paid because no cash moved.  Same E1 repairable-cache decision as C3b1's
        scalar.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._broken_loan(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            result = balance_at.balance_map(acct, bctx, periods)
            begun = [p for p in periods if p.start_date <= bctx.as_of]
            assert begun, "expected a begun period"
            # No payment ever recorded -> the origination principal held flat, the
            # same $240,000.00 the scalar folds (not a fail-loud raise).
            assert result[begun[-1].id] == Decimal("240000.00")

    def test_broken_loan_figures_follow_the_fold_not_the_replay(
        self, app, db, seed_user, seed_periods,
    ):
        """A broken loan's is_retired / payoff read the FOLD (plan step D2a).

        The control for the D2a re-route: with the posting ledger cleared, the
        pre-D2a ``LoanState.current_balance`` fell back to the money-blind
        anchor replay (it advances one SCHEDULED step per confirmed payment and
        discards the cash), while every displayed balance already folded the
        SOURCE facts -- one loan, one page, two derivations.  D2a deleted the
        field and pointed the seam's last two readers (the ``is_retired``
        predicate and the forward projection's seed) at the fold.

        Arithmetic, hand-computed.  $240,000 at 6% for 360 months (scheduled
        P&I 1,438.92); ONE settled payment of $241,200.00 cash:

            interest  = 240000.00 * 0.06/12          =   1,200.00
            principal = 241200.00 - 1200.00          = 240,000.00
            fold      = 240000.00 - 240000.00        =       0.00  (paid off)

        The replay instead advances one scheduled step:

            principal = 1438.92 - 1200.00            =     238.92
            replay    = 240000.00 - 238.92           = 239,761.08

        So under the old reader this loan reads as owing $239,761.08 --
        ``is_retired`` False and a REAL payoff date -- while its page shows
        $0.00.  Under D2a both follow the fold: retired, paid off, no forward
        payoff to date.  The replay figure is pinned in-test so the divergence
        (the control's teeth) is proven, not assumed.
        """
        # pylint: disable=import-outside-toplevel
        from app.services import (
            loan_loaders, loan_payment_service, loan_resolver,
        )
        from app.services.loan_resolver._periods import _replay_from_anchor
        from app.utils.money import round_money
        from tests._test_helpers import (
            clear_loan_ledger, create_settled_transfer, settle_instant_on,
        )

        with app.app_context():
            periods = seed_periods
            acct = self._broken_loan(seed_user, db.session, periods)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], acct,
                periods[1], amount=Decimal("241200.00"),
                paid_at=settle_instant_on(date(2024, 10, 1)),
            )
            db.session.commit()
            # Re-break the cache: settling re-synced the loan's postings.
            clear_loan_ledger(acct.id)
            db.session.commit()

            # The control's teeth: the money-blind replay genuinely diverges.
            params = loan_loaders.load_loan_params(acct.id)
            ctx = loan_payment_service.load_loan_context(
                acct.id, seed_user["scenario"].id, params,
            )
            inputs = loan_resolver.LoanInputs(
                params, loan_loaders.load_loan_anchor_facts(params),
                ctx.payments, ctx.rate_changes,
            )
            replayed = round_money(_replay_from_anchor(
                inputs,
                loan_resolver.resolve_periods(params, inputs.rate_changes),
                date.today(),
            ).balance_as_of)
            assert replayed == Decimal("239761.08")

            bctx = BalanceContext.build(seed_user["user"].id)
            # The fold: the whole cash paid the loan off.
            assert balance_at.balance_at(acct, bctx, bctx.as_of) == (
                Decimal("0.00")
            )
            figures = balance_at.loan_figures(acct, bctx)
            assert figures is not None
            # Both re-routed readers follow the fold, not the $239,761.08 replay:
            # retired (fold <= 0) with a confirmed payment behind it, and no
            # forward payoff left to date (the seed folds to 0.00).
            assert figures.is_retired is True
            assert figures.is_paid_off is True
            assert figures.payoff_date is None

    def test_an_amortizing_account_with_no_loan_params_does_NOT_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """A Mortgage account whose loan terms were never filled in routes to CASH.

        Two clicks in the UI produce it, and it has no OPENING posting either -- so
        the ledger answers ``None`` for it exactly as a broken loan does.  What
        keeps them apart is ORDER: the seam resolves the loan BEFORE reading the
        ledger, and no schedule means "not a configured loan", not "corrupt".

        Without that ordering the fail-loud raise would 500 the /savings page for a
        user who did nothing wrong.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_account_of_type

        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Terms Never Entered",
                anchor_balance=Decimal("150000.00"),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)

            # It really is in the hazardous state: the ledger cannot answer for it.
            # pylint: disable=import-outside-toplevel
            assert posted_loan_balance_at(
                acct.id, bctx.scenario.id, bctx.as_of) is None

            # And it degrades to the cash producer instead of raising -- pinned
            # by VALUE, not by ``is not None``.  The account carries a
            # $150,000.00 anchor and no transactions, so the cash producer owes
            # exactly the anchor; asserting only non-None would have passed on
            # any number the fallback invented, including a $0.00 that would
            # render this Mortgage debt-free (B-21).
            assert balance_at.balance_at(
                acct, bctx, bctx.as_of,
            ) == Decimal("150000.00")

            # The per-period MAP degrades identically (C3b3 hazard 1): the seam's
            # map dispatch reads ``_resolution.configured_loan``, which is None for
            # an unconfigured loan, so it falls through to the cash producer
            # rather than reaching positions()'s fail-loud for a schedule-less
            # loan.  Pinned by value at the current period ($150,000.00 anchor,
            # held flat).
            periods = pay_period_service.get_all_periods(seed_user["user"].id)
            current = pay_period_service.get_current_period(seed_user["user"].id)
            loan_map = balance_at.balance_map(acct, bctx, periods)
            assert loan_map is not None
            assert loan_map[current.id] == Decimal("150000.00")


class TestScalarAndMapAgree:
    """The scalar and the per-period map agree on every day of seven loan shapes.

    Defect 2a was a $3,455.79 divergence between ``balance_at`` and ``balance_map``
    for the same loan on the same day -- two producers the code's own docstrings
    called siblings, with nothing comparing them.  Since the cutover BOTH read the
    one loan producer :func:`~app.services.balance_at.positions` (the scalar at
    step C3b1, the per-period map at C3b3), so a divergence from two DIFFERENT
    readers is now structurally impossible -- which is the cutover's whole point.

    **What this now pins, stated plainly so the next reader does not over-trust
    it.**  Both sides call ``positions``, so this is no longer a two-reader
    comparison; it is a SAMPLING-consistency check.  It pins that the map's
    per-period sampler (:func:`~app.services.balance_at.positions_period_map`)
    asks ``positions`` at the SAME date the scalar is asked at for each period --
    ``min(period.end, as_of)`` for a begun period (the current-period clamp) and
    ``period.end`` for a future one.  A regression in that sampling -- most
    dangerously dropping the current-period clamp, which would hand the current
    period to the forward projection -- would surface here across all seven shapes,
    including the origination-inside-the-current-period keying trap (shape 6).  The
    balance VALUES themselves are pinned elsewhere: B2's fold-vs-reader oracle and
    the C6a hand-computed fold oracles (``test_loan_plan_forward_oracle.py`` and
    ``test_loan_plan_assembly.py``) -- plan Section 7.2: never two producers that
    share code proving each other.

    "Every loan shape the app can produce" is what this docstring used to claim.
    It is a fixture matrix, not a proof of exhaustiveness, and the shapes are
    enumerated rather than generated.

    **The probe date follows each period's OWN keying, and that is not a detail.**
    The map is period-END keyed since step C2 (a posting counts from its own
    ``entry_date``, which can fall mid period, so a payment settled during a period
    must count in it): a BEGUN period's confirmed value is the ledger at its END,
    and a FUTURE period's is the projection at its END.  The scalar reader,
    though, RAISES / PROJECTS for a future date, so the current period (begun, but
    its end is future) is probed at TODAY -- where the confirmed map, carrying every
    posting flat, equals the confirmed scalar.  Probing the current period at its
    END instead would compare the confirmed map to ``balance_at(period.end_date)``,
    which projects FORWARD (reducing by an overdue-but-unpaid installment -- FU-7)
    and is a different, smaller number.  Hence ``min(period.end_date, today)`` for a
    begun period, ``period.end_date`` for a future one.
    """

    # pylint: disable=too-many-arguments
    def _assert_agrees(self, acct, bctx, periods, shape):
        """Every period: map[p] == balance_at at the day that period is keyed to."""
        bmap = balance_at.balance_map(acct, bctx, periods)
        for period in periods:
            begun = period.start_date <= bctx.as_of
            # Period-END keyed (C2), clamped to today for the current period,
            # whose end is future (the scalar would project there, not confirm).
            probe = (
                min(period.end_date, bctx.as_of) if begun else period.end_date
            )
            scalar = balance_at.balance_at(acct, bctx, probe)
            assert bmap[period.id] == scalar, (
                f"{shape}: period {period.start_date}..{period.end_date} "
                f"({'begun' if begun else 'future'}) -- "
                f"map={bmap[period.id]} scalar@{probe}={scalar}"
            )

    def test_every_enumerated_loan_shape(self, app, db, seed_user, seed_periods):
        """Seven shapes, including the two this arc was built to handle.

        The frozen 2026-03-20 clock (this package's autouse fixture) puts six of
        ``seed_periods`` in the past and four ahead, so both splice branches run
        for every shape.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import (
            create_loan_account, insert_tracking_start_event,
            insert_trueup_event, loan_params_for,
        )

        with app.app_context():
            periods = seed_periods
            bctx_user = seed_user["user"].id

            # 1. Never paid, originated long ago -- the shape defect 2a lived in.
            never_paid = create_loan_account(
                seed_user, db.session, name="Never Paid",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2024, 9, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
            )
            # 2. Trued up (the operator asserted a balance).
            trued_up = create_loan_account(
                seed_user, db.session, name="Trued Up",
                principal=Decimal("100000.00"), rate=Decimal("0.05000"),
                term=120, origination_date=date(2025, 2, 1), payment_day=1,
                account_type=AcctTypeEnum.AUTO_LOAN, anchor_period=periods[0],
            )
            insert_trueup_event(
                loan_params_for(db.session, trued_up.id),
                Decimal("88000.00"), anchor_date=date(2026, 2, 10),
            )
            # 3. Mid-life import (the ledger opens at a tracking start).
            mid_life = create_loan_account(
                seed_user, db.session, name="Mid Life",
                principal=Decimal("300000.00"), rate=Decimal("0.04500"),
                term=360, origination_date=date(2019, 6, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
            )
            insert_tracking_start_event(
                loan_params_for(db.session, mid_life.id),
                Decimal("240000.00"), date(2026, 1, 20),
            )
            # 4. Paid off (a $0.00 true-up -- the operator's explicit action).
            paid_off = create_loan_account(
                seed_user, db.session, name="Paid Off",
                principal=Decimal("15000.00"), rate=Decimal("0.07000"),
                term=48, origination_date=date(2025, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.AUTO_LOAN, anchor_period=periods[0],
            )
            insert_trueup_event(
                loan_params_for(db.session, paid_off.id),
                Decimal("0.00"), anchor_date=date(2026, 3, 1),
            )
            # 5. Not yet originated (C2's shape).
            upcoming = create_loan_account(
                seed_user, db.session, name="Upcoming",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
            )
            # 6. Originating INSIDE the current pay period -- the keying trap.
            closing_now = create_loan_account(
                seed_user, db.session, name="Closing Now",
                principal=Decimal("180000.00"), rate=Decimal("0.05500"),
                term=360, origination_date=date(2026, 3, 25), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE, anchor_period=periods[0],
            )
            db.session.commit()
            # 7. PAID, then trued up -- the shape the forward producers exist
            # for, and the one this matrix lacked (plan Section 7.4).  Its
            # CONFIRMED rows are what make the begun half a real two-reader
            # check rather than a walk over an all-unconfirmed schedule.
            paid_then_trued = _paid_then_trued_loan(
                seed_user, db.session, periods,
            )

            bctx = BalanceContext.build(bctx_user)
            for acct, shape in [
                (never_paid, "never-paid"),
                (trued_up, "trued-up"),
                (mid_life, "mid-life import"),
                (paid_off, "paid-off"),
                (upcoming, "not-yet-originated"),
                (closing_now, "originating inside the current period"),
                (paid_then_trued, "paid, then trued up"),
            ]:
                self._assert_agrees(acct, bctx, periods, shape)


class TestForwardFoldSeedsFromTheConfirmedPresent:
    """The forward fold starts from the confirmed present, not a stale schedule row.

    Since step C6b the seam's forward projection is a FOLD from the loan's
    confirmed-present seed over its :func:`~app.services.balance_at._plan.loan_plan`
    (payment records + contractual synthesis), not a walk of the resolver's
    schedule rows.  A trued-up loan's confirmed present is the TRUE-UP balance, and
    the fold seeds from exactly that -- never from a CONFIRMED schedule row's
    ``remaining_balance``, which is what the loan owed BEFORE the true-up and is
    arbitrarily stale (here by $48,496.25).

    This is the seam-level guard for that seeding.  The retired schedule walk had a
    ``_forward_rows`` ``is_confirmed`` filter to drop confirmed rows before walking
    (finding B-4); the fold has no such filter to get wrong -- it seeds from the
    ledger-confirmed balance and folds only PLANNED/ESTIMATED records forward, so a
    confirmed row's stale balance has no path into a future answer by construction.
    The unit-level proof that a settled payment is not re-folded as a future record
    is C6a's ``test_an_early_settled_payment_is_not_re_synthesized_as_estimated``;
    this pins the property at the seam, on a trued-up loan whose stale confirmed row
    would be the wrong answer.
    """

    def test_a_confirmed_rows_stale_balance_never_answers_a_future_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Inside the window, the forward fold answers the TRUE-UP, not a paid row.

        On 2026-03-25 the loan owes exactly what the operator asserted on 03-15
        ($200,000.00): nothing is due until 04-01, so the fold has no record to
        apply and holds at its confirmed-present seed.  A walk that reached back to
        the 03-01 payment row would report $248,496.25 -- the balance the loan owed
        three weeks BEFORE the true-up, and $48,496.25 too much -- which is exactly
        the stale-row answer the fold's seeding avoids.
        """
        with app.app_context():
            periods = seed_periods
            loan = _paid_then_trued_loan(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            # ``debt_schedule_rows`` is the fence-clean accessor for an
            # out-of-cluster reader: rows, carrying no balance.  Every balance
            # below comes from the seam, which is the architecture this suite
            # exists to defend.
            rows = sorted(
                net_worth_kernel.debt_schedule_rows([loan], bctx)[loan.id],
                key=lambda row: row.payment_date,
            )
            confirmed = [row for row in rows if row.is_confirmed]
            unconfirmed = [row for row in rows if not row.is_confirmed]

            # The fixture really is the shape this guards (non-vacuity): the
            # ledger booked two payments, and the stale balance their rows carry
            # is NOT the balance the loan actually owes.
            assert len(confirmed) == 2
            assert confirmed[-1].payment_date == date(2026, 3, 1)
            stale = confirmed[-1].remaining_balance
            assert stale == PAID_LOAN_LAST_CONFIRMED_REMAINING
            assert balance_at.balance_at(
                loan, bctx, bctx.as_of,
            ) == PAID_LOAN_TRUED_UP_TO

            # The probe sits just AFTER the resolver's now and BEFORE the next
            # installment falls due, so the fold has no record to apply in the
            # window and must hold at the confirmed-present seed.
            probe = date(2026, 3, 25)
            assert bctx.as_of < probe < unconfirmed[0].payment_date

            owed = balance_at.balance_at(loan, bctx, probe)

            # THE CONTROL, and the only line here that can fail: nothing is due
            # between 03-20 and 03-25, so the fold holds at the trued-up balance.
            # A forward path that seeded from the 03-01 confirmed row instead would
            # read $248,496.25 -- the stale pre-true-up balance -- which is the
            # answer the fold's confirmed-present seeding structurally avoids.
            assert owed == PAID_LOAN_TRUED_UP_TO
            # Documentation, not a control: with both operands pinned to
            # literals above, the arithmetic below holds however wrong the
            # producer is.  It records the blast radius -- $48,496.25, and
            # unbounded in the size of the true-up -- next to the code that
            # bounds it.
            assert stale - owed == Decimal("48496.25")


class TestLoanTermsAreScenarioIndependent:
    """Plan C8e: a loan's CONTRACT terms need no baseline scenario.

    ``LoanFigures`` used to carry the payment and the rate alongside the
    scenario-scoped predicates, and nothing exposed the mixture while every field
    happened to be scenario-independent.  Step C8d added the DERIVED payoff -- the
    first field that folds the loan's projected payments -- and the loan's
    non-balance WRITE surfaces (escrow editing, the rate-history swap, the
    recurring-payment amount) began raising the seam's ``require_scenario`` for a
    user whose baseline is missing.  Splitting the value along the dependency it
    actually has is the fix; these pin both halves of it.
    """

    def _loan(self, db, seed_user, periods):
        """A resolvable 24-month $12,000 loan at 5%, originated 2026-01-01."""
        return create_loan_account(
            seed_user, db.session, name="Terms Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1),
            anchor_period=periods[0],
        )

    def _drop_baseline(self, db, seed_user):
        """Remove the user's baseline scenario -- the state baseline_service repairs."""
        # Pylint: ``import-outside-toplevel`` -- model import local to the one
        # helper that needs it, matching this suite's convention.
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
        db.session.query(Scenario).filter_by(
            user_id=seed_user["user"].id, is_baseline=True,
        ).delete()
        db.session.commit()

    def test_terms_answer_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The payment and rate resolve with no scenario at all.

        Hand-checked: $12,000 over 24 months at 5%/12 amortizes to a level
        payment of $526.46, and the rate in effect is the origination rate.
        """
        with app.app_context():
            account = self._loan(db, seed_user, seed_periods)
            db.session.commit()
            self._drop_baseline(db, seed_user)

            ctx = BalanceContext.build(seed_user["user"].id)
            assert ctx.scenario is None
            terms = balance_at.loan_terms(account, ctx)
            assert terms is not None
            assert terms.monthly_payment == Decimal("526.46")
            assert terms.current_rate == Decimal("0.05000")
            assert terms.is_originated is True
            assert terms.is_arm is False

    def test_figures_still_fail_loud_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The scenario-scoped bundle still refuses to answer -- deliberately.

        The split narrows the dependency; it does not soften the guard.  A payoff
        folded without the loan's projected payments would be a different loan's
        answer, so this raises rather than inventing one.
        """
        with app.app_context():
            account = self._loan(db, seed_user, seed_periods)
            db.session.commit()
            self._drop_baseline(db, seed_user)

            ctx = BalanceContext.build(seed_user["user"].id)
            with pytest.raises(ValueError, match="baseline scenario"):
                balance_at.loan_figures(account, ctx)

    def test_a_non_loan_answers_none_before_any_guard(
        self, app, db, seed_user, seed_periods,
    ):
        """Both entries keep the not-a-loan test AHEAD of the scenario guard.

        ``home_equity_service`` uses the ``None`` return as its configured-loan
        test for a user with no baseline, so an account with no ``LoanParams``
        must answer rather than raise.
        """
        with app.app_context():
            self._drop_baseline(db, seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_terms(seed_user["account"], ctx) is None
            assert balance_at.loan_figures(seed_user["account"], ctx) is None

    def test_figures_compose_the_same_terms(
        self, app, db, seed_user, seed_periods,
    ):
        """The wider bundle carries the SAME terms value, not a copy of its fields.

        Composition is what keeps the two from drifting: a consumer reading the
        payment off ``figures.terms`` and one reading it off ``loan_terms`` are
        reading one derivation.
        """
        with app.app_context():
            account = self._loan(db, seed_user, seed_periods)
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(account, ctx)
            terms = balance_at.loan_terms(account, ctx)
            assert figures is not None
            assert figures.terms == terms
