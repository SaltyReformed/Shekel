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

The five account kinds are seeded with the suite's established factory
patterns: a Checking (PLAIN), an HYSA + InterestParams (INTEREST), a
Mortgage + LoanParams + origination event/rate (AMORTIZING), a 401(k) +
InvestmentParams (INVESTMENT), and a Property + AssetAppreciationParams
(APPRECIATING).  ``seed_periods_today`` places today in period index 4 so
``get_current_period`` is deterministic and an account can be anchored in
the past (period 2) or at the current period (period 4).
"""

from datetime import date, timedelta
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
                debt_schedule=None, investment_params=None,
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
                debt_schedule=None, investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected
            # Interest accrues forward, so the last period exceeds the anchor.
            assert seam[periods[-1].id] > Decimal("5000.00")


class TestBalanceMapLoan:
    """``balance_map`` reproduces the kernel loan path (AMORTIZING)."""

    def test_pre_first_payment_uses_current_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan map equals the kernel's; post-anchor == C, pre-anchor == the ledger.

        The seam internally generates the same debt schedule the kernel
        consumes, so the maps match.  A balance true-up dated today re-anchors the
        resolver, so its schedule is today-forward, and the map splits on the date
        the balance was ASSERTED:

        * A period that had not ended when the true-up landed, and that still
          precedes the first scheduled payment, reports the trued-up
          current_balance ($200,000) held flat -- NEVER the $240,000 original
          principal.
        * A period that ENDED before the true-up reports what the confirmed ledger
          knew then: the $240,000 opening, undisturbed, because not one payment was
          ever recorded.  The past belongs to the ledger, and the trued-up balance
          is not back-projected across it.

        Both halves are verified against the real dev clone, where the Mortgage's
        past periods likewise step down at each recorded event rather than
        carrying today's balance backward.

        This does NOT fence PR #44 / aba0242 (the schedule map seeded with
        ``original_principal``), despite reading like it: both periods above have
        BEGUN, so the confirmed ledger answers them and
        ``compute_loan_period_balance_map`` is never called.  Proven by coverage --
        its body is unexecuted here -- and by reintroducing the defect, which
        leaves this test green.  That fence is the W9905
        ``shekel-original-principal-as-balance`` checker (a build failure), plus the
        direct unit test
        ``test_savings_dashboard_service::TestLoanProjectedBalanceDispatcher::
        test_dispatcher_returns_current_balance_before_first_payment``.
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
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(mortgage, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                mortgage, bctx, periods,
                debt_schedule=schedule, investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected

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
            assert seam[post_anchor[0].id] == schedule.current_balance
            # ...which is the trued-up current balance, never the original
            # principal (the PR #44 / aba0242 boundary bug).
            assert schedule.current_balance == Decimal("200000.00")
            assert schedule.current_balance != Decimal("240000.00")

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

        A balance trueup to $0 leaves the resolver with an empty schedule
        and a $0 current balance.  The seam must route the empty-schedule
        DebtSchedule to the loan path (membership, not truthiness) and
        report $0 at every period -- equal to the kernel called with the
        same generated schedule.
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
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(loan, bctx, periods)
            expected = net_worth_kernel.build_account_balance_map(
                loan, bctx, periods,
                debt_schedule=schedule, investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected
            # Paid off -> empty schedule -> $0 current balance everywhere.
            assert schedule.schedule == []
            assert schedule.current_balance == Decimal("0.00")
            assert seam[periods[0].id] == Decimal("0.00")
            assert seam[periods[-1].id] == Decimal("0.00")


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
                debt_schedule=None, investment_params=params,
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
                debt_schedule=None, investment_params=params,
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
                debt_schedule=None, investment_params=None,
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
        """For a mixed account set, build_maps equals the kernel dispatch.

        Pre-reroute the savings net-worth producer assembled
        ``_load_account_params`` + ``generate_debt_schedules`` and fed them
        to the kernel's ``build_account_balance_map`` per account inline;
        the seam internalizes that assembly.  For every account, the seam's
        per-id map must equal that direct kernel dispatch under the
        orchestrator's manual assembly, which also locks the
        deduction-scoping rule (both scope to the InvestmentParams map's
        keys).  The oracle is the direct kernel call, NOT the rerouted
        ``build_account_net_worth_maps`` (which now delegates to
        ``build_maps`` -- comparing against it would be tautological).
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
            loan_accounts = [
                a for a in accounts if a.id in params.loan_params_map
            ]
            debt_schedules = net_worth_kernel.generate_debt_schedules(
                loan_accounts, bctx,
            )
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
            # Independent oracle: the kernel dispatch the savings net-worth
            # producer ran inline pre-reroute, fed by the orchestrator's
            # manual assembly.  This is what build_account_net_worth_maps did
            # before delegating to the seam; reproducing it here keeps the
            # comparison non-tautological -- it proves the seam's internal
            # assembly reproduces the manual assembly account-for-account.
            expected_by_id = {}
            for account in accounts:
                balances = net_worth_kernel.build_account_balance_map(
                    account, bctx, periods,
                    debt_schedule=debt_schedules.get(account.id),
                    investment_params=params.investment_params_map.get(
                        account.id,
                    ),
                    deductions=deductions_by_account.get(account.id, []),
                    salary_gross_biweekly=salary_gross_biweekly,
                )
                if balances is not None:
                    expected_by_id[account.id] = balances

            seam_maps = balance_at.build_maps(accounts, bctx, periods)

            assert set(seam_maps.keys()) == set(expected_by_id.keys())
            # All five seeded accounts have anchors, so none is omitted.
            assert len(seam_maps) == 5
            for acct_id, expected_balances in expected_by_id.items():
                assert seam_maps[acct_id] == expected_balances

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
                schedule.schedule, as_of, schedule.current_balance,
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

        The FUTURE assertion is the load-bearing one, and the reason this test is not
        a tautology.  Every BEGUN period is answered by
        ``confirmed_loan_balance_map(account.id, ...)`` -- a per-account ledger read
        that is correct even if ``build_maps`` handed loan A the loan B
        ``DebtSchedule``.  Only the forward tail consumes that bundle, so a
        mis-assigned schedule shows up ONLY on a period after today.  Asserting just
        the begun periods (as this test did while the fixtures had no ledger, where
        the value came straight from the possibly-swapped bundle) would silently stop
        testing isolation at all.

        So all three regions are pinned: each loan's own ledger opening at a
        pre-anchor period ($240,000 / $180,000), its own trued-up balance at the
        anchor period ($200,000 / $50,000), and its own forward projection -- which
        must still straddle the two loans' wildly different balances after today.
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
                inv, bctx, periods, debt_schedule=None,
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
                inv_match, bctx, periods, debt_schedule=None,
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
                hysa, bctx, periods, debt_schedule=None,
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

    def test_kernel_producer_rejects_today_or_earlier(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The kernel's forward projection refuses a today-or-earlier date.

        ``forward_balance_at_date`` walks the schedule's UNCONFIRMED rows, and an
        OVERDUE payment (past due, still unpaid) is deliberately among them.  So
        at ``today`` the walk would report the balance net of a payment that was
        NEVER MADE -- understating the debt.  The producer therefore rejects the
        present as well as the past; the confirmed present is the resolver's
        ``current_balance``, which the seam supplies.
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

            with pytest.raises(ValueError, match="STRICTLY FORWARD"):
                net_worth_kernel.loan_owed_at_dates(
                    [acct], bctx, [date.today()],
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
                debt.current_balance - principal_retired
            )
            # And it agrees with the shared primitive every loan surface reads.
            assert owed[acct.id][1] == balance_from_schedule_at_date(
                forward_rows, far_out, debt.current_balance,
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
                forward_rows, today, debt.current_balance,
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
