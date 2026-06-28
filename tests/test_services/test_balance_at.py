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

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import (
    CompoundingFrequencyEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.loan_params import LoanParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, CalcMethod, DeductionTiming
from app.models.transaction import Transaction
from app.services import (
    account_service,
    balance_at,
    balance_calculator,
    balance_resolver,
    income_service,
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
from tests._test_helpers import (
    add_txn,
    insert_origination_event,
    insert_origination_rate,
    insert_trueup_event,
    make_appreciating_account,
    make_investment_account,
    make_salary_profile,
)


def _make_hysa(db, seed_user, anchor_period, balance):
    """Create an HYSA account (INTEREST) with InterestParams (5% APY daily)."""
    hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=hysa_type.id,
            name="HYSA",
            anchor_balance=balance,
            anchor_period_id=anchor_period.id,
        ),
    )
    db.session.add(acct)
    db.session.flush()
    db.session.add(InterestParams(
        account_id=acct.id,
        apy=Decimal("0.05000"),
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        ),
    ))
    db.session.commit()
    return acct


def _make_mortgage(
    db, seed_user, anchor_period, balance, origination_date, name="Mortgage",
):
    """Create a Mortgage (AMORTIZING) with LoanParams + origination event/rate.

    ``name`` is parameterised so a test can seed two mortgages in one user
    without colliding on the ``(user_id, name)`` unique constraint.  Returns
    ``(account, loan_params)`` so a caller can append a trueup event (e.g. to
    drive the loan to paid-off / empty-schedule, or to re-anchor it today).
    """
    mortgage_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=mortgage_type.id,
            name=name,
            anchor_balance=balance,
            anchor_period_id=anchor_period.id,
        ),
    )
    db.session.add(acct)
    db.session.flush()
    params = LoanParams(
        account_id=acct.id,
        original_principal=balance,
        current_principal=balance,
        term_months=360,
        origination_date=origination_date,
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    insert_origination_event(params)
    insert_origination_rate(params, Decimal("0.06500"))
    db.session.commit()
    return acct, params


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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(account, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                account, scenario, periods,
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
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(hysa, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                hysa, scenario, periods,
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
        """A loan map equals the kernel's, and pre-payment == current_balance.

        The seam internally generates the same debt schedule the kernel
        consumes, so the maps match.  A recent balance true-up re-anchors
        the resolver to today, so its schedule is today-forward: the earliest
        (pre-first-payment) periods report the resolver-derived
        current_balance ($200,000) held flat -- NEVER the $240,000 original
        principal (the recurring bug this seam fences).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
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
                [mortgage], scenario.id,
            )[mortgage.id]
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(mortgage, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                mortgage, scenario, periods,
                debt_schedule=schedule, investment_params=None,
                deductions=[], salary_gross_biweekly=gross,
            )

            assert seam is not None
            assert seam == expected

            # The first scheduled payment is today-forward; every period
            # ending before it sits at the resolver current_balance.
            first_payment = min(
                row.payment_date for row in schedule.schedule
            )
            pre_payment = [p for p in periods if p.end_date < first_payment]
            assert pre_payment, "expected a pre-first-payment period"
            assert seam[pre_payment[0].id] == schedule.current_balance
            # The pre-payment value is the trued-up current balance, never
            # the $240,000 original principal.
            assert schedule.current_balance == Decimal("200000.00")
            assert schedule.current_balance != Decimal("240000.00")

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
            periods = pay_period_service.get_all_periods(user_id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            schedule = net_worth_kernel.generate_debt_schedules(
                [loan], scenario.id,
            )[loan.id]
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(loan, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                loan, scenario, periods,
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

            seam = balance_at.balance_map(inv, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                inv, scenario, periods,
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
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            params = load_investment_params_for_accounts([inv]).get(inv.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv.id],
            ).get(inv.id, [])
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(inv, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                inv, scenario, periods,
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
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )

            seed = balance_at.investment_seed_map(inv, scenario, periods)
            # Delegation parity: the seam returns the kernel seed verbatim.
            assert seed == net_worth_kernel.investment_base_balance_map(
                inv, scenario, periods,
            )
            # Cash basis: anchor $10,000.00 carried flat (no contributions, no
            # modeled growth) at every post-anchor period.
            assert seed[periods[2].id] == Decimal("10000.00")
            assert seed[periods[-1].id] == Decimal("10000.00")
            # Strictly below the growth-modeled map -- the seed is pre-growth.
            modeled = balance_at.balance_map(inv, scenario, periods)
            assert modeled[periods[-1].id] > seed[periods[-1].id]


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
            periods = pay_period_service.get_all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(prop, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                prop, scenario, periods,
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

            # Assemble the inputs exactly as the orchestrator does.
            params = _load_account_params(user_id, accounts)
            loan_accounts = [
                a for a in accounts if a.id in params.loan_params_map
            ]
            debt_schedules = net_worth_kernel.generate_debt_schedules(
                loan_accounts, scenario.id,
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
                    account, scenario, periods,
                    debt_schedule=debt_schedules.get(account.id),
                    investment_params=params.investment_params_map.get(
                        account.id,
                    ),
                    deductions=params.deductions_by_account.get(
                        account.id, [],
                    ),
                    salary_gross_biweekly=params.salary_gross_biweekly,
                )
                if balances is not None:
                    expected_by_id[account.id] = balances

            seam_maps = balance_at.build_maps(accounts, scenario, periods)

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
            periods = pay_period_service.get_all_periods(user_id)
            checking = seed_user["account"]
            no_anchor = SimpleNamespace(
                id=-1, user_id=user_id, account_type=None,
                current_anchor_period_id=None,
            )

            seam_maps = balance_at.build_maps(
                [checking, no_anchor], scenario, periods,
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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date  # inside a known period

            seam = balance_at.balance_at(account, scenario, as_of)
            expected = balance_resolver.balance_as_of_date(
                account, scenario.id, as_of,
            )
            assert seam == expected

    def test_loan_equals_schedule_lookup(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a loan, balance_at == balance_from_schedule_at_date."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            mortgage, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            schedule = net_worth_kernel.generate_debt_schedules(
                [mortgage], scenario.id,
            )[mortgage.id]
            as_of = periods[7].end_date

            seam = balance_at.balance_at(mortgage, scenario, as_of)
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
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(inv, scenario, as_of)
            full_map = balance_at.balance_map(inv, scenario, periods)
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
            periods = pay_period_service.get_all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(prop, scenario, as_of)
            full_map = balance_at.balance_map(prop, scenario, periods)
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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            bonus = add_txn(
                db.session, seed_user, periods[5], "Bonus", "100.00",
                is_income=True,
            )
            db.session.commit()
            overrides = {bonus.id: Decimal("9999.00")}

            seam = balance_at.balance_map(
                account, scenario, periods, amount_overrides=overrides,
            )
            expected = balance_resolver.balances_for(
                account, scenario.id, periods, amount_overrides=overrides,
            ).balances
            assert seam == expected

            # The override changed the projection: $1,000 anchor + $9,999
            # bonus = $10,999 at period 5, vs $1,000 + $100 = $1,100 without.
            no_override = balance_at.balance_map(account, scenario, periods)
            assert no_override[periods[5].id] == Decimal("1100.00")
            assert seam[periods[5].id] == Decimal("10999.00")


class TestMultiLoanIsolation:
    """build_maps keeps each loan's schedule separate (no shared/positional bug)."""

    def test_two_loans_keep_distinct_current_balances(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two trued-up loans in one build_maps keep DISTINCT pre-payment balances.

        A shared or positional debt-schedule forward would collapse both
        loans onto one balance.  Loan A is trued up to $200,000 today and
        loan B to $50,000 today (both pre-first-payment at period 0), so the
        seam must report each loan's OWN current balance at period 0 -- the
        debt_schedules map is keyed by account id, not positional.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
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

            seam_maps = balance_at.build_maps([loan_a, loan_b], scenario, periods)

            # Pre-first-payment period 0 -> each loan's OWN current balance.
            assert seam_maps[loan_a.id][periods[0].id] == Decimal("200000.00")
            assert seam_maps[loan_b.id][periods[0].id] == Decimal("50000.00")


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
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            baseline = balance_at.balance_map(inv, scenario, periods)

            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            _add_flat_deduction(db, profile, inv, Decimal("200.0000"))
            db.session.commit()

            with_ded = balance_at.balance_map(inv, scenario, periods)
            # Post-anchor period reflects the consumed contribution.
            assert with_ded[periods[-1].id] > baseline[periods[-1].id]

            # seam == kernel with the SAME manually-loaded deduction.
            params = load_investment_params_for_accounts([inv]).get(inv.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv.id],
            ).get(inv.id, [])
            gross = income_service.get_current_gross_biweekly(user_id)
            expected = net_worth_kernel.build_account_balance_map(
                inv, scenario, periods, debt_schedule=None,
                investment_params=params, deductions=deductions,
                salary_gross_biweekly=gross,
            )
            assert with_ded == expected
            assert len(deductions) == 1  # the deduction was actually loaded

            # Scope: a non-investment account in the same batch is untouched.
            checking = seed_user["account"]
            maps = balance_at.build_maps([inv, checking], scenario, periods)
            assert maps[checking.id] == balance_at.balance_map(
                checking, scenario, periods,
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

            match_map = balance_at.balance_map(inv_match, scenario, periods)
            none_map = balance_at.balance_map(inv_none, scenario, periods)
            assert match_map[periods[-1].id] > none_map[periods[-1].id]

            # seam == kernel for the matched account.
            params = load_investment_params_for_accounts(
                [inv_match],
            ).get(inv_match.id)
            deductions = load_active_deductions_for_accounts(
                user_id, [inv_match.id],
            ).get(inv_match.id, [])
            expected = net_worth_kernel.build_account_balance_map(
                inv_match, scenario, periods, debt_schedule=None,
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
                balance_at.balance_map(account, None, periods)
            with pytest.raises(ValueError):
                balance_at.build_maps([account], None, periods)
            with pytest.raises(ValueError):
                balance_at.balance_at(account, None, as_of)


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

            seam = balance_at.balance_at(acct, scenario, as_of)
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
            periods = pay_period_service.get_all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            seam = balance_at.balance_at(inv, scenario, date(2000, 1, 1))
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
                    acct, scenario, periods, amount_overrides=overrides,
                ) == balance_at.balance_map(acct, scenario, periods)

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
                hysa, scenario, periods, amount_overrides=overrides,
            )
            without_ov = balance_at.balance_map(hysa, scenario, periods)
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
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[2], Decimal("5000.00"))
            gross = income_service.get_current_gross_biweekly(user_id)

            seam = balance_at.balance_map(hysa, scenario, periods)
            expected = net_worth_kernel.build_account_balance_map(
                hysa, scenario, periods, debt_schedule=None,
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
            periods = pay_period_service.get_all_periods(user_id)
            assert balance_at.build_maps([], scenario, periods) == {}

    def test_balance_map_empty_periods_is_empty_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An anchored account over no periods yields an empty (not None) map."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            account = seed_user["account"]
            result = balance_at.balance_map(account, scenario, [])
            assert result is not None
            assert len(result) == 0

    def test_balance_map_no_anchor_account_is_none(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A no-anchor account yields None directly from balance_map."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            periods = pay_period_service.get_all_periods(user_id)
            no_anchor = SimpleNamespace(
                id=-1, user_id=user_id, account_type=None,
                current_anchor_period_id=None,
            )
            assert balance_at.balance_map(no_anchor, scenario, periods) is None


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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]

            seam = balance_at.cash_balance_map(account, scenario, periods)
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
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))

            cash = balance_at.cash_balance_map(hysa, scenario, periods)
            kind_correct = balance_at.balance_map(hysa, scenario, periods)
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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            add_txn(
                db.session, seed_user, periods[3], "Deposit", "500.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            seam = balance_at.cash_balance_map(account, scenario, periods)
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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            bonus = add_txn(
                db.session, seed_user, periods[5], "Bonus", "100.00",
                is_income=True,
            )
            db.session.commit()
            overrides = {bonus.id: Decimal("9999.00")}

            seam = balance_at.cash_balance_map(
                account, scenario, periods, amount_overrides=overrides,
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
            periods = pay_period_service.get_all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            seam = balance_at.cash_balance_at(account, scenario, as_of)
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
            periods = pay_period_service.get_all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            as_of = periods[-1].end_date

            cash = balance_at.cash_balance_at(hysa, scenario, as_of)
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
                balance_at.cash_balance_map(account, None, periods)
            with pytest.raises(ValueError):
                balance_at.cash_balance_at(account, None, as_of)


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
            new_balances = balance_at.balance_map(hysa, scenario, periods)
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
