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
where the two share code it degenerates to ``f(x) == f(x)``.  So the classes
that pin VALUES against hand-computed arithmetic --
:class:`TestForwardWalkExcludesLedgerBookedRows` above all -- are load-bearing
in a way the parity classes are not: they are what stands between a shared-code
defect and production.  Read the plan's Section 7.2 before adding a test here
that proves a producer with a producer.

The five account kinds are seeded with the suite's established factory
patterns: a Checking (PLAIN), an HYSA + InterestParams (INTEREST), a
Mortgage + LoanParams + origination event/rate (AMORTIZING), a 401(k) +
InvestmentParams (INVESTMENT), and a Property + AssetAppreciationParams
(APPRECIATING).  ``seed_periods_today`` places today in period index 4 so
``get_current_period`` is deterministic and an account can be anchored in
the past (period 2) or at the current period (period 4).
"""

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
    account_service,
    balance_at,
    balance_calculator,
    balance_resolver,
    income_service,
    net_worth_investment,
    net_worth_kernel,
    pay_period_service,
)
from app.services.account_projection import balance_from_schedule_at_date
from app.services.projection_inputs import (
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
)
from app.services.savings_dashboard_service._data import _load_account_params
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.money import round_money
from app.services.resolution_context import BalanceContext
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
    :class:`TestForwardWalkExcludesLedgerBookedRows` (which pins its value), so
    the two cannot drift on what "a paid loan" means.

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
                investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected
            # No transactions -> the flat $1,000 anchor at every period.
            assert seam[periods[0].id] == Decimal("1000.00")

    def test_interest_hysa_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST (HYSA) map equals the kernel's interest path.

        The HYSA routes through
        :func:`balance_calculator.calculate_balances_with_interest` inside
        the kernel; the seam must reproduce that, and the interest accrual
        means the closing balance sits above the flat anchor (proving the
        interest branch -- not the plain resolver -- ran).
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
                investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected
            # Interest accrues forward, so the last period exceeds the anchor.
            assert seam[periods[-1].id] > Decimal("5000.00")


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

        This does NOT fence PR #44 / aba0242 (a schedule map seeded with
        ``original_principal``): both periods above have BEGUN, so the fold answers
        them from source events and no schedule map is consulted at all.  That
        fence is STRUCTURAL -- C2b deleted the schedule-only map and C3b3 retired
        the per-period forward map -- backed by the W9905
        ``shekel-original-principal-as-balance`` checker on the surviving forward
        producers.
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
        on MEMBERSHIP in ``inputs.debt_schedules`` (an empty schedule is still a
        configured loan, not a fall-through to cash), and it reports $0 at every
        period.
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
                investment_params=params,
                deductions=deductions, salary_gross_biweekly=gross,
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
                investment_params=params,
                deductions=deductions, salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected
            # Reverse projection below the anchor, forward growth above it.
            assert seam[periods[0].id] < seam[periods[2].id]
            assert seam[periods[-1].id] > seam[periods[2].id]

    def test_investment_seed_map_is_cash_basis_pre_growth(
        self, app, db, seed_user, seed_periods_today,
    ):
        """investment_seed_map is the kernel cash-basis seed, below the modeled map.

        The seam's seed accessor delegates to the kernel's
        ``investment_base_balance_map`` verbatim (one definition of the
        pre-growth seed), and that seed is the CASH BASIS -- anchor carried
        flat, NO modeled growth -- so it sits strictly below the growth-modeled
        ``balance_map`` at every post-anchor period.  Seeding a growth chart
        from the modeled map instead would compound growth on growth; this pins
        the seed as the pre-growth figure the chart consumers must read.  (The
        kernel producer is fenced behind the seam now -- ``investment_seed_map``
        is the only sanctioned read -- so this also documents the wrapper's
        contract.)
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )

            seed = balance_at.investment_seed_map(inv, bctx, periods)
            # Delegation parity: the seam returns the producer's seed verbatim
            # (investment_base_balance_map lives in net_worth_investment, the
            # investment growth sub-chain extracted from the kernel).
            assert seed == net_worth_investment.investment_base_balance_map(
                inv, scenario, periods,
            )
            # Cash basis: anchor $10,000.00 carried flat (no contributions, no
            # modeled growth) at every post-anchor period.
            assert seed[periods[2].id] == Decimal("10000.00")
            assert seed[periods[-1].id] == Decimal("10000.00")
            # Strictly below the growth-modeled map -- the seed is pre-growth.
            modeled = balance_at.balance_map(inv, bctx, periods)
            assert modeled[periods[-1].id] > seed[periods[-1].id]


class TestInvestmentGrowthSinceAnchor:
    """``investment_growth_since_anchor`` decomposes growth vs contributions.

    The chip's contract: ``growth + contributed`` must reconcile to the cent
    with the DISPLAYED balance change since the anchor
    (``balance_map[current] - balance_map[anchor_period]``), because both are
    read from the SAME forward projection the modeled map uses.  These tests
    are the load-bearing guard against the map and the decomposition drifting
    apart (they assemble inputs three different ways -- the map, the seam, and
    the raw producer -- yet must agree).
    """

    def test_reconciles_with_displayed_balance_change(
        self, app, db, seed_user, seed_periods_today,
    ):
        """growth + contributed == balance_map[current] - anchor balance.

        Anchored well before the current period so the window
        ``(anchor, current]`` is non-empty and spans real growth; the
        decomposition must sum to the modeled balance delta EXACTLY (the
        telescoping identity), so the chip can never disagree with the hero.
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
                inv, bctx, periods, current,
            )
            assert result is not None
            growth, contributed = result

            balances = balance_at.balance_map(inv, bctx, periods)
            anchor_balance = balances[periods[0].id]
            current_balance = balances[current.id]
            # The reconciliation identity, to the cent.
            assert growth + contributed == current_balance - anchor_balance
            # A growing account with no negative movements grows: growth > 0.
            assert growth > Decimal("0.00")

    def test_seam_matches_raw_producer(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The seam returns the raw producer's decomposition verbatim.

        Delegation parity: the seam assembles the params / deductions / gross
        via ``_assemble_inputs`` and hands them to
        ``net_worth_investment.investment_growth_since_anchor``; calling that
        producer with the same manually-assembled inputs must agree.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            current = pay_period_service.get_current_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            params = load_investment_params_for_accounts([inv]).get(inv.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv.id],
            ).get(inv.id, [])
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.investment_growth_since_anchor(
                inv, bctx, periods, current,
            )
            raw = net_worth_investment.investment_growth_since_anchor(
                inv, params, scenario, periods, deductions, gross, current,
            )
            assert seam == raw
            assert seam is not None

    def test_none_when_anchored_at_current_period(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No post-anchor window yet (anchored this period) -> None (chip hidden).

        With the anchor AT the current period there are zero periods after the
        anchor and at or before current, so no growth has accrued since the
        anchor: the decomposition returns None rather than a spurious $0 chip.
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
                inv, bctx, periods, current,
            )
            assert result is None

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
                inv, bctx, periods, None,
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
                investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected
            # Forward appreciation above the anchor; flat-carry backward.
            assert seam[periods[-1].id] > seam[periods[2].id]
            assert seam[periods[0].id] == seam[periods[2].id]


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
          map, proving the batch assembly routed the loan to its fold (kept it in
          ``debt_schedules`` rather than dropping it to the cash path).  The loan's
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
                    investment_params=params.investment_params_map.get(
                        account.id,
                    ),
                    deductions=deductions_by_account.get(account.id, []),
                    salary_gross_biweekly=salary_gross_biweekly,
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

    def test_cash_equals_balance_as_of_date(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For cash, balance_at delegates to balance_as_of_date verbatim."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date  # inside a known period

            seam = balance_at.balance_at(account, bctx, as_of)
            expected = balance_resolver.balance_as_of_date(
                account, scenario.id, as_of,
            )
            assert seam == expected

    def test_interest_accrues_equals_period_map_not_cash(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an HYSA, balance_at accrues interest (== balance_map), NOT cash.

        The kind-correct scalar must agree with the kind-correct MAP for an
        interest-bearing account: both accrue.  Anchor a 5% APY HYSA at
        ``periods[2]`` with no transactions, then value it at ``periods[6]``
        (4 periods of accrual later).  ``balance_at`` reads the
        period-granular ``balance_map`` value at the containing period --
        strictly above the flat $5,000.00 cash carry that
        ``balance_as_of_date`` (and ``cash_balance_at``) return for the same
        date.  Asserting that divergence is what locks the scalar onto the
        accruing path: were INTEREST routed back to the cash producer (the
        pre-fix behavior), ``balance_at`` would equal the cash value and this
        test fails.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[2], Decimal("5000.00"))
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(hysa, bctx, as_of)
            full_map = balance_at.balance_map(hysa, bctx, periods)
            # Kind-correct scalar == kind-correct map at the containing period.
            assert seam == full_map[periods[6].id]
            # And it ACCRUES: strictly above the flat no-interest cash carry
            # (anchor $5,000.00 with no rows) the cash producer returns for the
            # same date -- the Fork-B lock that the scalar is not on the cash
            # path for INTEREST.
            cash = balance_resolver.balance_as_of_date(
                hysa, scenario.id, as_of,
            )
            assert cash == Decimal("5000.00")
            assert seam > cash

    def test_loan_equals_schedule_lookup(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a loan, balance_at == balance_from_schedule_at_date."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            schedule = net_worth_kernel.generate_debt_schedules(
                [mortgage], bctx,
            )[mortgage.id]
            as_of = periods[7].end_date

            seam = balance_at.balance_at(mortgage, bctx, as_of)
            expected = balance_from_schedule_at_date(
                schedule.schedule, as_of, schedule.projection_seed,
            )
            assert seam == expected

    def test_investment_equals_period_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an investment, balance_at reads the INDEPENDENTLY-KNOWN period.

        De-tautologized: the expected value is keyed by ``periods[6].id``
        (the period that by construction contains ``periods[6].start_date``),
        NOT by re-running ``find_period_containing_date`` -- so a
        period-selection bug inside ``balance_at`` is detectable.  Neighbor
        periods differ (so an off-by-one would change the number), and the
        value exceeds the anchor balance (so it read a post-anchor period,
        not period 0).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(inv, bctx, as_of)
            full_map = balance_at.balance_map(inv, bctx, periods)
            assert seam == full_map[periods[6].id]
            # Neighbors differ -> an off-by-one in period selection would show.
            assert full_map[periods[5].id] != full_map[periods[7].id]
            # Read a post-anchor (grown) period, not the period-0 / anchor value.
            assert seam > Decimal("10000.00")

    def test_property_equals_period_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a property, balance_at reads the INDEPENDENTLY-KNOWN period.

        De-tautologized like the investment case: keyed by ``periods[6].id``
        (which by construction contains ``periods[6].start_date``), neighbors
        differ, and the value exceeds the anchor market value (a post-anchor
        appreciated period was read, not period 0).
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
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(prop, bctx, as_of)
            full_map = balance_at.balance_map(prop, bctx, periods)
            assert seam == full_map[periods[6].id]
            # Neighbors differ -> an off-by-one in period selection would show.
            assert full_map[periods[5].id] != full_map[periods[7].id]
            # Post-anchor appreciation above the anchor market value.
            assert seam > Decimal("400000.00")


class TestAmountOverridesPassthrough:
    """``balance_map`` threads amount_overrides to the cash producer."""

    def test_passthrough_matches_balances_for(
        self, app, db, seed_user, seed_periods_today,
    ):
        """balance_map(..., amount_overrides=OV) == balances_for(..., OV).balances.

        A constructed override on a projected income transaction must flow
        through the seam to the cash producer unchanged, and it must
        actually change the projection (proving the threading is real, not a
        silent no-op): the $100 stored bonus becomes $9,999.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            bonus = add_txn(
                db.session, seed_user, periods[5], "Bonus", "100.00",
                is_income=True,
            )
            db.session.commit()
            overrides = {bonus.id: Decimal("9999.00")}

            seam = balance_at.balance_map(
                account, bctx, periods, amount_overrides=overrides,
            )
            expected = balance_resolver.balances_for(
                account, scenario.id, periods, amount_overrides=overrides,
            ).balances
            assert seam == expected

            # The override changed the projection: $1,000 anchor + $9,999
            # bonus = $10,999 at period 5, vs $1,000 + $100 = $1,100 without.
            no_override = balance_at.balance_map(account, bctx, periods)
            assert no_override[periods[5].id] == Decimal("1100.00")
            assert seam[periods[5].id] == Decimal("10999.00")


class TestMultiLoanIsolation:
    """build_maps keeps each loan's schedule separate (no shared/positional bug)."""

    def test_two_loans_keep_distinct_current_balances(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two trued-up loans in one build_maps keep DISTINCT balances, past AND future.

        A shared or positional debt-schedule forward would collapse both loans onto
        one balance.  Loan A is trued up to $200,000 today and loan B to $50,000, so
        the seam must report each loan's OWN balance -- the ``debt_schedules`` map is
        keyed by account id, not positional.

        The seam folds each loan from its OWN memoized walk
        (:meth:`~app.services.resolution_context.BalanceContext.loan_walk`, keyed
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
                investment_params=params, deductions=deductions,
                salary_gross_biweekly=gross,
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
                investment_params=params, deductions=deductions,
                salary_gross_biweekly=gross,
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
        """An amortizing account with no LoanParams degrades to balance_as_of_date.

        ``generate_debt_schedules`` returns no entry (no LoanParams / anchor
        events), so balance_at falls back to the cash producer over the
        loan's own rows -- the documented degrade.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
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
            db.session.commit()
            as_of = periods[5].start_date

            seam = balance_at.balance_at(acct, bctx, as_of)
            expected = balance_resolver.balance_as_of_date(
                acct, scenario.id, as_of,
            )
            assert seam == expected

    def test_before_horizon_returns_anchor_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An as_of before the whole period horizon returns the canonical anchor.

        For an investment whose date precedes every period, no containing
        period exists, so balance_at returns the resolver anchor balance
        (rounded) -- mirroring balance_as_of_date's pre-anchor convention.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            seam = balance_at.balance_at(inv, bctx, date(2000, 1, 1))
            expected = round_money(
                balance_resolver.resolve_anchor(inv, scenario.id).balance,
            )
            assert seam == expected
            assert seam == Decimal("10000.00")  # the 401k's anchor balance


class TestAmountOverridesScope:
    """amount_overrides reaches the interest path but never the non-cash kinds."""

    def test_ignored_on_loan_investment_property(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A bogus override changes nothing for loan / investment / property.

        Only the cash path forwards amount_overrides; the loan / investment /
        appreciation branches never pass it to any producer, so the same map
        results with or without the override.
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
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            overrides = {999999: Decimal("99999.00")}
            for acct in (mortgage, inv, prop):
                assert balance_at.balance_map(
                    acct, bctx, periods, amount_overrides=overrides,
                ) == balance_at.balance_map(acct, bctx, periods)

    def test_interest_path_override_changes_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An override on an HYSA income txn changes the interest-path balance.

        The kernel diff threads amount_overrides through
        ``calculate_balances_with_interest``, so an override on an income
        transaction belonging to the HYSA must raise that period's balance --
        previously only the PLAIN path was covered.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            txn = Transaction(
                account_id=hysa.id,
                pay_period_id=periods[5].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="HYSA Bonus",
                transaction_type_id=income_type_id,
                estimated_amount=Decimal("100.00"),
            )
            db.session.add(txn)
            db.session.commit()
            overrides = {txn.id: Decimal("9999.00")}

            with_ov = balance_at.balance_map(
                hysa, bctx, periods, amount_overrides=overrides,
            )
            without_ov = balance_at.balance_map(hysa, bctx, periods)
            # The override ($9,999) replaces the stored $100 -> ~$9,899 higher.
            assert with_ov[periods[5].id] > without_ov[periods[5].id]
            assert (
                with_ov[periods[5].id] - without_ov[periods[5].id]
                > Decimal("9000.00")
            )


class TestCashPreAnchorOmission:
    """The headline cash contract: pre-anchor periods are omitted."""

    def test_interest_account_omits_pre_anchor_periods(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An HYSA anchored mid-window omits pre-anchor periods from its map.

        Cash balances are materialized roll-forwards from the anchor; periods
        before the anchor have no balance (they are absent, not zero), and the
        anchor period onward are present.  The seam == the kernel.
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
                investment_params=None, deductions=[],
                salary_gross_biweekly=gross,
            )
            assert seam is not None
            assert periods[0].id not in seam
            assert periods[1].id not in seam
            assert periods[2].id in seam  # the anchor period is present
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
    rows.  These tests prove (1) the cash entries reproduce the canonical
    producers verbatim -- including the ``stale_anchor_warning`` flag the
    grid banner reads -- and (2) they do NOT dispatch by kind: an INTEREST
    account's cash map omits the interest the kind-correct map accrues,
    which is the whole reason these entries exist (Level-1 Commit 8).
    """

    def test_cash_balance_map_equals_balances_for(
        self, app, db, seed_user, seed_periods_today,
    ):
        """cash_balance_map returns the producer's BalanceResult verbatim.

        Both the balances map and the stale-anchor flag must match
        ``balance_resolver.balances_for`` for the same account / scenario /
        periods -- the cash entry is a thin fence-compliant pass-through.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]

            seam = balance_at.cash_balance_map(account, bctx, periods)
            expected = balance_resolver.balances_for(
                account, scenario.id, periods,
            )
            assert seam.balances == expected.balances
            assert seam.stale_anchor_warning == expected.stale_anchor_warning

    def test_cash_map_omits_interest_unlike_kind_correct_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an HYSA, the cash map is the no-interest running balance.

        ``cash_balance_map`` must NOT accrue interest (it is the cash-flow
        view): its values equal the entries-aware ``balances_for`` and stay
        flat at the $5,000 anchor (no transactions), strictly below the
        kind-correct ``balance_map`` which routes the HYSA through
        ``calculate_balances_with_interest``.  This is the divergence the
        cash entry fences: a HYSA grid account whose balance row accrued
        interest would break the grid's balance-vs-subtotal invariant.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))

            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            kind_correct = balance_at.balance_map(hysa, bctx, periods)
            plain = balance_resolver.balances_for(
                hysa, scenario.id, periods,
            ).balances

            # Cash view == the no-interest producer, exactly.
            assert cash.balances == plain
            # No transactions + no interest -> flat at the anchor.
            assert cash.balances[periods[-1].id] == Decimal("5000.00")
            # The kind-correct view accrues interest strictly above it.
            assert kind_correct[periods[-1].id] > cash.balances[periods[-1].id]

    def test_cash_balance_map_passes_stale_anchor_warning(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A settled post-anchor txn surfaces stale_anchor_warning via the seam.

        The grid reads this flag for its stale-anchor banner.  The seed
        account is anchored at ``periods[0]``; a RECEIVED (is_settled)
        income row in a later period sets the flag, and cash_balance_map
        must carry it through identically to ``balances_for``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            add_txn(
                db.session, seed_user, periods[3], "Deposit", "500.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            seam = balance_at.cash_balance_map(account, bctx, periods)
            expected = balance_resolver.balances_for(
                account, scenario.id, periods,
            )
            assert seam.stale_anchor_warning is True
            assert seam.stale_anchor_warning == expected.stale_anchor_warning

    def test_cash_balance_map_threads_amount_overrides(
        self, app, db, seed_user, seed_periods_today,
    ):
        """cash_balance_map forwards amount_overrides to the producer (grid parity).

        The grid threads its pre-built live projected-income map through the
        cash entry; the override must reach ``balances_for`` and move the
        number ($1,000 anchor + a $9,999 override on the period-5 bonus).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            bonus = add_txn(
                db.session, seed_user, periods[5], "Bonus", "100.00",
                is_income=True,
            )
            db.session.commit()
            overrides = {bonus.id: Decimal("9999.00")}

            seam = balance_at.cash_balance_map(
                account, bctx, periods, amount_overrides=overrides,
            )
            expected = balance_resolver.balances_for(
                account, scenario.id, periods, amount_overrides=overrides,
            )
            assert seam.balances == expected.balances
            # $1,000 anchor + $9,999 override (not the stored $100) = $10,999.
            assert seam.balances[periods[5].id] == Decimal("10999.00")

    def test_cash_balance_at_equals_balance_as_of_date(
        self, app, db, seed_user, seed_periods_today,
    ):
        """cash_balance_at delegates to balance_as_of_date verbatim."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            seam = balance_at.cash_balance_at(account, bctx, as_of)
            expected = balance_resolver.balance_as_of_date(
                account, scenario.id, as_of,
            )
            assert seam == expected

    def test_cash_balance_at_is_no_interest_for_hysa(
        self, app, db, seed_user, seed_periods_today,
    ):
        """cash_balance_at is the no-interest scalar even for an HYSA.

        Mirrors the map case: the scalar cash view equals
        ``balance_as_of_date`` (which never layers interest) and stays flat
        at the anchor for a transaction-free HYSA -- the calendar's
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
            assert cash == balance_resolver.balance_as_of_date(
                hysa, scenario.id, as_of,
            )
            # No transactions, no interest -> flat at the anchor.
            assert cash == Decimal("5000.00")

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


class TestInterestDetailRerouteParity:
    """The interest_detail reroute preserves the prior producer's numbers.

    interest_detail is the one materially-changed path in Commit 8: it
    swapped a single SoT-anchored
    ``balance_calculator.calculate_balances_with_interest`` call for
    ``balance_at.balance_map`` (the kernel's interest path, cache-anchored)
    plus ``net_worth_kernel.interest_by_period_for_account``.  In the normal
    case (the anchor cache equals the dated ``AccountAnchorHistory`` SoT --
    what every factory-built account has) the two paths MUST produce
    identical period balances AND identical per-period interest.  This pins
    that behavior-preservation with a real, non-flat projection, so a future
    drift between the kernel interest path and the route's old contract is
    caught (the cross-page oracle has no interest-bearing surface).
    """

    def test_seam_path_equals_old_producer_path(
        self, app, db, seed_user, seed_periods_today,
    ):
        """balance_map + interest accessor == the old calculate_balances_with_interest.

        Seeds an HYSA (5% APY) anchored at ``periods[0]`` with a $1,000
        deposit at ``periods[3]`` so the running balance moves and interest
        accrues on it.  The NEW route path (``balance_map`` for balances,
        ``interest_by_period_for_account`` for interest) must equal the OLD
        route path (one ``calculate_balances_with_interest`` call seeded from
        the dated-SoT anchor over the account's transactions), proving the
        SoT->cache anchor switch and the two-call split changed no number.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("8000.00"))
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            db.session.add(Transaction(
                account_id=hysa.id,
                pay_period_id=periods[3].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Deposit",
                transaction_type_id=income_type_id,
                estimated_amount=Decimal("1000.00"),
            ))
            db.session.commit()
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id)
                .one()
            )

            # OLD interest_detail path: the dated-SoT anchor over the
            # account's transactions, scoped exactly as the (now deleted)
            # ``_load_account_transactions`` helper scoped them.
            anchor = balance_resolver.resolve_anchor(hysa, scenario.id)
            old_txns = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == hysa.id,
                    Transaction.pay_period_id.in_([p.id for p in periods]),
                    Transaction.scenario_id == scenario.id,
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )
            old_balances, old_interest = (
                balance_calculator.calculate_balances_with_interest(
                    anchor_balance=anchor.balance,
                    anchor_period_id=anchor.period.id,
                    periods=periods,
                    transactions=old_txns,
                    interest_params=params,
                )
            )

            # NEW interest_detail path: the seam + the kernel interest accessor.
            new_balances = balance_at.balance_map(hysa, bctx, periods)
            new_interest = net_worth_kernel.interest_by_period_for_account(
                hysa, scenario, periods, params,
            )

            assert new_balances == old_balances
            assert new_interest == old_interest
            # The projection is real, not flat: interest accrued and the
            # deposit raised the balance, so the equivalence is non-trivial.
            assert any(v > Decimal("0.00") for v in new_interest.values())
            # $8,000 anchor + $1,000 deposit + accrued interest.
            assert new_balances[periods[-1].id] > Decimal("9000.00")


def _assert_grid_view_reconciles(account, scenario, periods, view):
    """Assert the kind-aware view's three rows reconcile to the cent.

    For every adjacent pair of projected periods the displayed balance delta
    must equal the transaction subtotal net plus the accrual increment --
    the by-construction invariant
    ``balances[p] - balances[q] == period_subtotal[p].net + increments[p]``
    (the E-25 invariant carries the cash leg; the accrual carries the rest).
    Only meaningful for an accruing account (``increments`` populated).
    """
    subtotals = balance_resolver.period_subtotals(account, scenario.id, periods)
    items = list(view.balances.items())
    assert len(items) >= 2, "need >= 2 projected periods to reconcile a delta"
    for (_prev_id, prev_bal), (pid, bal) in zip(items, items[1:]):
        assert bal - prev_bal == subtotals[pid].net + view.increments[pid], (
            f"period {pid}: balance delta {bal - prev_bal} != net "
            f"{subtotals[pid].net} + increment {view.increments[pid]}"
        )


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

            assert view.balances == cash.balances
            assert view.stale_anchor_warning == cash.stale_anchor_warning
            # No accrual row for a plain cash account.
            assert view.increments == {}

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

            assert view.balances == cash.balances
            assert view.increments == {}

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
                pay_period_id=periods[3].id,
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
            assert set(view.balances.keys()) == set(cash.balances.keys())
            for pid in view.balances:
                assert view.balances[pid] == round_money(kc[pid])
            assert view.balances[periods[-1].id] > cash.balances[periods[-1].id]

            # Rows reconcile to the cent (net + accrual == balance delta).
            _assert_grid_view_reconciles(hysa, scenario, periods, view)

            # The accrual is real interest: the telescoped total equals the
            # final premium and matches the kernel's interest within a cent
            # (the two round on slightly different paths).
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id).one()
            )
            kernel_interest = net_worth_kernel.interest_by_period_for_account(
                hysa, scenario, periods, params,
            )
            total_accrual = sum(view.increments.values())
            assert total_accrual == (
                view.balances[periods[-1].id] - cash.balances[periods[-1].id]
            )
            assert abs(total_accrual - sum(kernel_interest.values())) <= Decimal("0.02")
            assert total_accrual > Decimal("0.00")

    def test_investment_stays_cash_flow_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An investment grid account stays on the cash-flow view (no accrual).

        Investment balances are projection-driven (the growth engine), not a
        sum of the account's transactions, so an ad-hoc grid row would not
        move a kind-correct balance -- the same projection-vs-transaction
        mismatch that excludes loans.  By decision (INTEREST-only grid
        accrual) an investment grid account therefore shows the cash-flow
        running-balance with no accrual row, exactly like PLAIN and AMORTIZING.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )

            view = balance_at.grid_balance_view(inv, bctx, periods)
            cash = balance_at.cash_balance_map(inv, bctx, periods)

            assert view.balances == cash.balances
            assert view.increments == {}
            # It is the cash-flow (transaction) balance, NOT the growth-modeled
            # one: balance_map accrues growth above the flat cash basis at the
            # horizon, and the grid view deliberately does not.
            modeled = balance_at.balance_map(inv, bctx, periods)
            assert view.balances[periods[-1].id] < modeled[periods[-1].id]

    def test_property_stays_cash_flow_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A property grid account stays on the cash-flow view (no accrual).

        Property balances are appreciation-projected, not a sum of
        transactions, so (like loans and investments) a property grid account
        shows the cash-flow running-balance -- the flat market value -- with no
        accrual row, per the INTEREST-only grid-accrual decision.
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

            view = balance_at.grid_balance_view(prop, bctx, periods)
            cash = balance_at.cash_balance_map(prop, bctx, periods)

            assert view.balances == cash.balances
            assert view.increments == {}
            # Cash-flow (flat market value), NOT the appreciated projection.
            modeled = balance_at.balance_map(prop, bctx, periods)
            assert view.balances[periods[-1].id] < modeled[periods[-1].id]

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

    def test_accruing_kc_none_degrades_to_cash(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """An accruing account whose kind-correct map is None degrades to cash.

        ``balance_map`` documents a ``None`` return for an account with no
        anchor period.  The NOT NULL anchor columns make that unreachable for
        a real account, so the seam's fall-through is guarded behavior, not a
        live path; it is exercised here by forcing the ``None`` return.
        ``grid_balance_view`` must then return the cash-flow view with no
        accrual row, never raise.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))

            cash = balance_at.cash_balance_map(hysa, bctx, periods)
            # Force the documented (NOT-NULL-unreachable) None return.  Patch the
            # DEFINING module (``_kind_correct``), not the package re-export:
            # ``grid_balance_view`` looks the producer up through its owning
            # module, so that is where the substitution has to land.
            monkeypatch.setattr(
                balance_at._kind_correct, "balance_map", lambda *a, **k: None,
            )
            view = balance_at.grid_balance_view(hysa, bctx, periods)

            assert view.balances == cash.balances
            assert view.increments == {}

    def test_interest_increment_pure_when_live_differs_from_stored(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """INTEREST accrual stays pure interest when live income != stored (M1).

        Regression guard for the income-basis trap the ``None`` normalization
        fixes: an INTEREST account's cash walk auto-builds a live income map
        from ``None`` while its kc walk (calculate_balances_with_interest)
        uses stored income -- so if the two were left to diverge the premium
        would absorb the income recompute instead of being pure interest.
        Forces live ($1,500) != stored ($1,000) on a real income transaction
        and asserts, via the default ``None`` path, that the total accrual
        still equals the kernel's interest (both on one stored basis); a
        regression to a live cash baseline would skew it by the $500 delta.

        (Only INTEREST is exercised: investment / property contributions are
        not live-recomputed -- the live seam covers salary income and
        loan-transfer shadows -- so live == stored there and the trap cannot
        arise; their walks are also both live from ``None`` regardless.)
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            income_txn = Transaction(
                account_id=hysa.id,
                pay_period_id=periods[3].id,
                scenario_id=scenario.id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Paycheck deposit",
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                estimated_amount=Decimal("1000.00"),
            )
            db.session.add(income_txn)
            db.session.commit()

            # Force live != stored: the live seam revalues this income at
            # $1,500 (vs the $1,000 stored).  Both the cash walk and the kc
            # walk must end up on ONE basis or the premium is polluted.
            monkeypatch.setattr(
                income_service, "live_projected_net",
                lambda uid, sid, txns: {income_txn.id: Decimal("1500.00")},
            )

            view = balance_at.grid_balance_view(hysa, bctx, periods)
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id).one()
            )
            kernel_interest = net_worth_kernel.interest_by_period_for_account(
                hysa, scenario, periods, params,
            )
            # Pure interest: the total accrual matches the kernel's interest
            # within a cent.  A live cash baseline (the M1 bug) would skew the
            # premium by the $500 income delta and blow this tolerance.
            total_accrual = sum(view.increments.values())
            assert abs(total_accrual - sum(kernel_interest.values())) <= Decimal("0.02")
            assert total_accrual > Decimal("0.00")


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
        POSITIVE owed magnitude (matching ``sum_net_worth_at_period``'s
        ``total -= abs(bal)``).
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

    def test_forward_balance_equals_the_scheduled_principal_arithmetic(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The projected owed balance equals the schedule's own principal arithmetic.

        An inequality (``series[1] < series[0]``) would pass even if the
        projection applied ONE month of principal instead of every month due, or
        projected the wrong loan.  This pins the actual dollars with the identity
        the amortization schedule itself satisfies:

            owed(T) == current_balance - sum(principal of every payment due by T)

        which is date-independent (it holds however many payments fall due by T),
        so the assertion cannot rot as the suite's clock advances.  It is also the
        SAME figure the debt card and the ``2 years`` band read, since all three
        walk one resolver schedule.
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
            due_by_target = [
                row for row in forward_rows if row.payment_date <= far_out
            ]
            principal_retired = sum(
                (row.principal for row in due_by_target), Decimal("0.00"),
            )

            # The seam's answer IS the schedule's arithmetic, to the cent.
            assert owed[acct.id][1] == (
                debt.projection_seed - principal_retired
            )
            # And it agrees with the shared primitive every loan surface reads.
            assert owed[acct.id][1] == balance_from_schedule_at_date(
                forward_rows, far_out, debt.projection_seed,
            )
            # Sanity on the oracle: many payments fell due, not zero and not one.
            assert len(due_by_target) > 12
            assert principal_retired > Decimal("2000.00")

    def test_today_point_ignores_overdue_rows_that_would_understate_the_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An OVERDUE unpaid payment must not shrink the today point.

        This fixture is the real hazard, not a contrived one: the mortgage was
        originated a year ago with NO payments recorded, so its schedule carries
        a dozen UNCONFIRMED rows already past due.  ``forward_balance_at_date``
        deliberately keeps overdue rows in its walk (the project's due-basis
        treatment), so a schedule walk AT TODAY reports the balance net of
        payments that were never made -- thousands of dollars less than the loan
        actually owes.

        The seam must not do that.  Its today point is the caller's
        ledger-confirmed balance, full stop.  Pinned by asserting the two
        differ: the schedule walk understates, and the seam does not follow it.
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
            walk_at_today = balance_from_schedule_at_date(
                forward_rows, today, debt.projection_seed,
            )
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

    def test_resolver_reports_nothing_owed_today(
        self, app, db, seed_user, seed_periods,
    ):
        """``LoanState.current_balance`` is the number six surfaces read.

        The loan detail page's "current principal", the payoff and refinance
        calculators, home equity, and the property equity chart all read this
        field directly.  It reported $200,000.00 for a mortgage that had not
        closed; the guard lives in the resolver precisely so all of them are fixed
        at once, which no seam-local guard could have done.

        The SCHEDULE must survive that guard: it is what the projection walks once
        the loan closes, and filtering the anchor out of ``_replay_from_anchor``
        (rather than guarding the derived balance) would have collapsed it to
        nothing.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            resolved = bctx.resolved_loan(acct)

            assert resolved.state.current_balance == self.ZERO
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
        settled transfer INTO the loan -- a down payment, an earnest deposit --
        satisfies the payment clause through the ordinary ``transfer_service``.
        **Only the origination guard is left standing between an unclosed mortgage
        and RETIRED**: badged paid off, dropped from the debt card's total, gone
        from the Horizon, and erased from the property equity chart.

        This is the ONE shape in which that guard is the guard doing the work --
        every other unclosed-mortgage test has no confirmed payment, so the payment
        clause would carry it regardless.  NEGATIVE CONTROL: delete the
        ``_is_originated`` check from ``_is_retired`` and this test goes red.

        This bug did not exist before the origination fix; the fix CREATES it, and
        this is the test that keeps it dead.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import (
            create_account_of_type, create_settled_transfer,
        )

        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            checking = create_account_of_type(
                seed_user, db.session, "Checking", "Chk",
                anchor_balance=Decimal("9000.00"),
            )
            db.session.commit()
            create_settled_transfer(
                seed_user, db.session, checking, acct, periods[4],
                amount=Decimal("1200.00"),
            )
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            resolved = bctx.resolved_loan(acct)
            # The two clauses that would otherwise conspire.
            assert any(p.is_confirmed for p in resolved.context.payments)
            assert resolved.state.current_balance == self.ZERO

            figures = balance_at.loan_figures(acct, bctx)
            assert figures.is_paid_off is False
            # And the chart's drop rule holds the same line, from the same guard:
            # an unborrowed mortgage is not RETIRED either, so it stays charted.
            assert figures.is_retired is False

    def test_the_ledger_has_no_domain_for_a_loan_that_has_not_closed(
        self, app, db, seed_user, seed_periods,
    ):
        """An upcoming loan's ledger has no DOMAIN -- there is nothing to bound.

        ``loan_ledger_domain`` is the clamp a caller measuring a CHANGE across a
        window uses so it never asks the readers a question they cannot answer.
        For a loan that does not exist yet the honest answer is ``None``: its
        ledger has not begun.

        This became askable only when the write walk stopped dropping future-dated
        anchors -- such a loan now HAS an opening posting, so the underlying
        reader (which keys on one) would hand back a real ``opening_balance`` of
        $200,000.00 for a mortgage that has not closed, dated from its pay
        period's START (N-10).  A clamp that fabricates the window it is clamping
        is worse than no clamp.

        NEGATIVE CONTROL: drop the ``_is_originated`` check from
        ``balance_at.loan_ledger_domain`` and this returns a ``LoanLedgerDomain``
        carrying ``opening_balance=Decimal("200000.00")``.
        """
        with app.app_context():
            periods = seed_periods
            acct = self._upcoming_mortgage(seed_user, db.session, periods)
            bctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_ledger_domain(acct, bctx) is None

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

        $200,000.00 owed, not $199,759.69: the 2026-05-01 installment has NO
        payment record behind it, and an installment nobody paid pays nothing down
        (plan D1).  The map's last period is future, so the projection amortizes it.
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
            ] == self.AFTER_FIRST_PAYMENT


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
                figures = balance_at.loan_figures(acct, bctx)
                return {
                    "loan_params": bctx.resolved_loan(acct).params,
                    "current_balance": balance_at.balance_at(
                        acct, bctx, bctx.as_of),
                    "is_paid_off": figures.is_paid_off,
                    "is_originated": figures.is_originated,
                }

            # The fixture really is in the hazardous state.
            assert _ad(mortgage)["current_balance"] == Decimal("0.00")
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
            from app.services.loan_posting_service._reader import (
                confirmed_loan_balance_at,
            )
            assert confirmed_loan_balance_at(
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

            # The per-period MAP degrades identically (C3b3 hazard 1): an
            # unconfigured loan is absent from ``inputs.debt_schedules``, so the
            # seam's map dispatch falls through to the cash producer rather than
            # reaching positions()'s fail-loud for a schedule-less loan.  Pinned by
            # value at the current period ($150,000.00 anchor, held flat).
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
    :class:`TestForwardWalkExcludesLedgerBookedRows` (plan Section 7.2: never two
    producers that share code proving each other).

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


class TestForwardWalkExcludesLedgerBookedRows:
    """A payment the ledger already booked is not a future event.

    :func:`~app.services.account_projection._forward_rows` drops the schedule's
    CONFIRMED rows before either forward producer walks it, and that filter is
    load-bearing: a confirmed row's ``remaining_balance`` is what the loan owed
    back THEN, so admitting it to a FORWARD walk reports a historical balance for
    a future date.  A true-up recorded after the last payment makes that stale
    number arbitrarily wrong -- here by $48,496.25.

    **Nothing guarded it.**  Deleting the filter left the ENTIRE suite green
    (7,401 tests, measured 2026-07-16), for two independent reasons this class
    exists to fix:

    * :class:`TestScalarAndMapAgree` compares the scalar against the map, but on
      the forward tail BOTH sides are ``_projected_owed_at(_forward_rows(...))``
      -- literally the same call with the same arguments
      (``net_worth_kernel.py:510`` and ``:995``).  A consistency check cannot see
      a change to the code its two sides SHARE (plan Section 7.2), so no shape
      added to that matrix can catch this.
    * :func:`~app.services.account_projection.balance_from_schedule_at_date`
      returns the LAST qualifying row's ``remaining_balance`` rather than
      subtracting principal, so the filter changes the answer ONLY on dates
      between ``ctx.as_of`` and the first UNCONFIRMED row's due date.  Every
      future period end-date the suite probes (04-09, 04-23, 05-07, 05-21) lands
      PAST that window, where the filter is a measured no-op.

    So the guard needs a VALUE pinned INSIDE the window -- which is what this is.
    Recorded as B-4 in the plan's findings ledger.
    """

    def test_a_confirmed_rows_stale_balance_never_answers_a_future_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Inside the window, the forward walk answers the LEDGER, not a paid row.

        On 2026-03-25 the loan owes exactly what the operator asserted on 03-15
        ($200,000.00): nothing is due until 04-01, so nothing has paid it down.
        The unfiltered walk instead reaches back to the 03-01 payment row and
        reports $248,496.25 -- the balance the loan owed three weeks BEFORE the
        true-up, and $48,496.25 too much.
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

            # The probe sits INSIDE the only window where the filter decides
            # anything: after the resolver's now, before the next payment falls
            # due.  Outside it the walk lands on an unconfirmed row either way
            # and the filter is a measured no-op.
            probe = date(2026, 3, 25)
            assert bctx.as_of < probe < unconfirmed[0].payment_date

            owed = balance_at.balance_at(loan, bctx, probe)

            # THE CONTROL, and the only line here that can fail: nothing is due
            # between 03-20 and 03-25, so the loan still owes exactly what was
            # asserted on 03-15.  Delete the ``is_confirmed`` filter and this
            # reads $248,496.25 -- the 03-01 row's stale balance -- which is how
            # it was measured to fire (2026-07-16).
            assert owed == PAID_LOAN_TRUED_UP_TO
            # Documentation, not a control: with both operands pinned to
            # literals above, the arithmetic below holds however wrong the
            # producer is.  It records the blast radius -- $48,496.25, and
            # unbounded in the size of the true-up -- next to the code that
            # bounds it.
            assert stale - owed == Decimal("48496.25")
