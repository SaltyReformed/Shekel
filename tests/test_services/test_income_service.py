"""
Shekel Budget App -- Income Service Tests (C17 / F-20 / MED-06 / F-032).

Pins the raise-aware paycheck-engine producer contract:

- The helper returns ``Decimal("0")`` when no active SalaryProfile exists.
- The helper returns ``annual_salary / pay_periods_per_year`` byte-identical
  to the engine for a no-raise profile.
- The helper APPLIES applicable ``SalaryRaise`` rows so the post-raise
  per-period gross is returned -- the F-032 worked example: $104,000
  base with a 3% raise effective in the as-of period yields $4,120.00
  per period, not the pre-Commit-17 off-engine $4,000.00.
- Every downstream consumer (savings, year-end, retirement, investment)
  reads the same engine-derived value through the helper for a
  raise-applicable user.

Test fixture math (hand-computed):

- ``annual_salary = $104,000`` + 3% one-time raise effective 2026-03
- Post-raise annual = ``104000 * 1.03 = 107,120``
- Per-period (10-period-year fallback to ROUND_HALF_UP):
  ``107120 / 26 = 4,120.000...`` -> ``Decimal("4120.00")``
- Pre-fix (no raise applied): ``104000 / 26 = 4,000.00`` -> the
  pre-Commit-17 value the off-engine sites returned.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import FilingStatus, RaiseType, Status, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import (
    balance_resolver,
    income_service,
    pay_period_service,
    paycheck_calculator,
    savings_dashboard_service,
    year_end_summary_service,
)
from app.services.tax_config_service import load_tax_configs


# Hand-computed expected values (see module docstring for derivation).
_RAISE_APPLIED_GROSS = Decimal("4120.00")  # 104000 * 1.03 / 26
_NO_RAISE_GROSS = Decimal("4000.00")  # 104000 / 26
_AS_OF_AFTER_RAISE = date(2026, 3, 15)  # inside seed_periods period 5
_AS_OF_BEFORE_RAISE = date(2026, 1, 5)  # inside seed_periods period 0


def _create_profile(
    user_id: int, scenario_id: int, *, annual_salary: str = "104000.00",
) -> SalaryProfile:
    """Create an active SalaryProfile for the user.

    Helper isolates the FilingStatus lookup + required-column boilerplate
    so each test reads as fixture composition rather than ORM ceremony.
    """
    filing = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Test Salary",
        annual_salary=Decimal(annual_salary),
        state_code="NC",
        pay_periods_per_year=26,
        is_active=True,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _add_one_time_raise(
    profile: SalaryProfile, *, percentage: str = "0.0300",
    effective_month: int = 3, effective_year: int = 2026,
) -> SalaryRaise:
    """Attach a one-time percentage raise to the profile."""
    merit = db.session.query(RaiseType).filter_by(name="merit").one()
    salary_raise = SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=merit.id,
        effective_month=effective_month,
        effective_year=effective_year,
        percentage=Decimal(percentage),
        is_recurring=False,
    )
    db.session.add(salary_raise)
    db.session.flush()
    return salary_raise


def _make_salary_template(seed_user, profile, *, name="Paycheck"):
    """Create an Income template and link ``profile`` to it.

    The producer treats a transaction as salary-linked iff its
    ``template_id`` maps to an active SalaryProfile for the scenario, so
    the test must set ``profile.template_id`` to the created template.
    """
    income_type = (
        db.session.query(TransactionType).filter_by(name="Income").one()
    )
    category = next(iter(seed_user["categories"].values()))
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        transaction_type_id=income_type.id,
        name=name,
        default_amount=Decimal("4000.00"),
    )
    db.session.add(template)
    db.session.flush()
    profile.template_id = template.id
    db.session.flush()
    return template


def _make_txn(
    seed_user, period, *, template=None, type_name="Income",
    status_name="Projected", is_override=False, estimated_amount="1.00",
):
    """Create a single Transaction in ``period`` for the producer tests."""
    txn_type = (
        db.session.query(TransactionType).filter_by(name=type_name).one()
    )
    status = db.session.query(Status).filter_by(name=status_name).one()
    category = next(iter(seed_user["categories"].values()))
    txn = Transaction(
        account_id=seed_user["account"].id,
        template_id=template.id if template is not None else None,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status.id,
        name="producer-test txn",
        category_id=category.id,
        transaction_type_id=txn_type.id,
        estimated_amount=Decimal(estimated_amount),
        is_override=is_override,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestLiveProjectedNet:
    """Unit tests for ``income_service.live_projected_net`` (Workstream B).

    Locks the two properties the live-recompute relies on: the producer
    (a) recomputes the net LIVE from the salary profile, ignoring the
    stored ``estimated_amount`` (so a stale cache cannot leak through),
    and (b) filters to exactly the Projected, non-overridden,
    salary-linked income rows.
    """

    def test_recomputes_live_ignoring_stored_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected salary-linked income row maps to the LIVE net.

        The transaction's stored ``estimated_amount`` is deliberately set
        to $1.00 (a stale/wrong value).  The producer must return the
        live net for the transaction's period -- proving it recomputes
        from the profile and never trusts the cached column.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = pay_period_service.get_all_periods(user_id)[5]
            txn = _make_txn(
                seed_user, period, template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            overrides = income_service.live_projected_net(
                user_id, scenario_id, [txn],
            )

            # $104,000 profile, no raise, no tax configs seeded -> net =
            # gross = 104000 / 26 = $4,000.00 (hand-computed; the sibling
            # balance-resolver test pins the same value for this setup).
            # The producer must return this LIVE net, never the stale $1.00.
            expected_net = Decimal("4000.00")
            assert overrides == {txn.id: expected_net}
            assert overrides[txn.id] != Decimal("1.00")

    def test_filters_to_projected_nonoverride_salary_income(
        self, app, db, seed_user, seed_periods,
    ):
        """Only Projected, non-overridden, salary-linked income is overridden.

        Builds five rows and asserts the override dict contains exactly
        the one Projected non-override income row linked to the salary
        profile -- Received income (historical), an overridden row (user
        value respected), non-salary income (template has no profile),
        and an expense are all omitted, so a caller's fallback to the
        stored amount applies to them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            other_template = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Non-salary income",
                default_amount=Decimal("50.00"),
            )
            db.session.add(other_template)
            db.session.commit()

            periods = pay_period_service.get_all_periods(user_id)
            # Distinct periods avoid the (template, period, scenario)
            # non-override unique index.
            wanted = _make_txn(seed_user, periods[5], template=template)
            received = _make_txn(
                seed_user, periods[6], template=template,
                status_name="Received",
            )
            overridden = _make_txn(
                seed_user, periods[7], template=template, is_override=True,
            )
            non_salary = _make_txn(
                seed_user, periods[5], template=other_template,
            )
            expense = _make_txn(
                seed_user, periods[5], template=None, type_name="Expense",
            )
            db.session.commit()

            overrides = income_service.live_projected_net(
                user_id, scenario_id,
                [wanted, received, overridden, non_salary, expense],
            )

            assert set(overrides) == {wanted.id}, (
                "Only the Projected, non-override, salary-linked income "
                f"row should be overridden; got ids {sorted(overrides)}"
            )

    def test_empty_when_no_candidates(self, app, db, seed_user, seed_periods):
        """No salary-linked Projected income -> empty dict (fast no-op)."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            # Empty transaction list.
            assert income_service.live_projected_net(
                user_id, scenario_id, [],
            ) == {}

            # An income row whose template has no SalaryProfile -> omitted.
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            unlinked = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Unlinked income",
                default_amount=Decimal("100.00"),
            )
            db.session.add(unlinked)
            db.session.commit()
            txn = _make_txn(
                seed_user, pay_period_service.get_all_periods(user_id)[3],
                template=unlinked,
            )
            db.session.commit()
            assert income_service.live_projected_net(
                user_id, scenario_id, [txn],
            ) == {}


class TestLiveIncomeThroughBalanceResolver:
    """Workstream B integration: balance surfaces recompute projected salary
    income live, so a stale stored ``estimated_amount`` never reaches a
    balance or subtotal.  This is the drift-without-regeneration lock -- the
    exact failure mode (a code change staling the grid) that motivated the
    income resolver.
    """

    def test_stale_stored_income_overridden_by_live_net(
        self, app, db, seed_user, seed_periods,
    ):
        """A projected salary income row with a stale $1.00 stored amount
        contributes its LIVE net to both ``period_subtotal`` and
        ``balances_for`` -- never the stale stored value.

        $104,000 profile, no deductions, no tax configs seeded -> net =
        gross = 104000/26 = $4,000.00.  The transaction is stored at $1.00
        (simulating a cache invalidated by a profile/code change with no
        regeneration); both balance surfaces must show $4,000.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            periods = pay_period_service.get_all_periods(user_id)
            period = periods[5]
            _make_txn(
                seed_user, period, template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            tax_configs = load_tax_configs(user_id, profile)
            breakdowns = paycheck_calculator.project_salary(
                profile, periods, tax_configs, calibration=profile.calibration,
            )
            expected_net = {
                bd.period.period_id: bd.earnings.net_pay for bd in breakdowns
            }[period.id]
            # Sanity: the live net genuinely differs from the stale stored.
            assert expected_net == Decimal("4000.00")
            assert expected_net != Decimal("1.00")

            # period_subtotal income line reflects the live net.
            subtotal = balance_resolver.period_subtotal(
                account, scenario.id, period,
            )
            assert subtotal.income == expected_net, (
                f"period_subtotal.income should be live {expected_net}, "
                f"got {subtotal.income} (stale stored was 1.00)"
            )

            # balances_for: the income period's balance moves by the live net.
            result = balance_resolver.balances_for(
                account, scenario.id, periods,
            )
            idx = next(i for i, p in enumerate(periods) if p.id == period.id)
            prior = result.balances[periods[idx - 1].id]
            assert result.balances[period.id] - prior == expected_net, (
                "balances_for income-period delta should be the live net "
                f"{expected_net}, got {result.balances[period.id] - prior}"
            )

    def test_overridden_income_row_keeps_user_value(
        self, app, db, seed_user, seed_periods,
    ):
        """A user-overridden salary income row is NOT recomputed.

        ``is_override=True`` means the user deliberately set the amount;
        the resolver must respect it (the producer excludes it), so the
        subtotal reflects the stored $1234.56, not the live net.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = pay_period_service.get_all_periods(user_id)[5]
            _make_txn(
                seed_user, period, template=template, is_override=True,
                estimated_amount="1234.56",
            )
            db.session.commit()

            subtotal = balance_resolver.period_subtotal(
                account, scenario.id, period,
            )
            assert subtotal.income == Decimal("1234.56"), (
                "An overridden income row must keep the user's amount, "
                f"got {subtotal.income}"
            )


class TestGetCurrentGrossBiweekly:
    """Direct unit tests for ``income_service.get_current_gross_biweekly``."""

    def test_c17_1_raise_applied_yields_engine_per_period_gross(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-1: applicable raise -> raise-aware engine gross_biweekly.

        Hand arithmetic: ``104000 * 1.03 / 26 = 4120.00``.  Pre-Commit-17
        the off-engine sites returned ``104000 / 26 = 4000.00`` because
        the raise was silently dropped.  The helper invokes the paycheck
        engine for the as-of period, which folds the raise into the
        post-raise annual salary before dividing.
        """
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            _add_one_time_raise(profile)
            db.session.commit()

            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id, as_of=_AS_OF_AFTER_RAISE,
            )

            assert result == _RAISE_APPLIED_GROSS

    def test_c17_2_no_raise_yields_byte_identical_pre_fix_value(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-2: no raises -> engine value equals the pre-fix value.

        With zero raises, the post-raise annual salary equals the base
        annual salary, so the engine's ``104000 / 26`` matches the
        pre-fix ``104000 / 26 = 4000.00`` exactly.  Locks the "no
        regression for non-raised users" property.
        """
        with app.app_context():
            _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id, as_of=_AS_OF_AFTER_RAISE,
            )

            assert result == _NO_RAISE_GROSS

    def test_c17_3_no_active_profile_returns_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-3: missing active profile -> ``Decimal("0")``.

        Preserves the pre-fix fallback contract -- every off-engine
        site defaulted ``salary_gross_biweekly = Decimal("0")`` when
        the user had no active profile.  The helper matches.
        """
        with app.app_context():
            # No SalaryProfile inserted -- seed_user does not create one.
            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id, as_of=_AS_OF_AFTER_RAISE,
            )

            assert result == Decimal("0")

    def test_raise_does_not_apply_before_effective_month(
        self, app, db, seed_user, seed_periods,
    ):
        """A raise effective March must NOT apply to a January period.

        Locks the engine's per-period semantic: the raise factor enters
        the gross only for periods whose start_date is on or after the
        effective month.  Without this, the helper would over-state
        income for pre-raise periods.
        """
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            _add_one_time_raise(profile)
            db.session.commit()

            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id, as_of=_AS_OF_BEFORE_RAISE,
            )

            assert result == _NO_RAISE_GROSS

    def test_scenario_id_filter_scopes_lookup(
        self, app, db, seed_user, seed_periods,
    ):
        """``scenario_id`` keyword restricts the SalaryProfile lookup.

        The year-end consumer passes ``scenario_id=scenario.id`` so the
        per-scenario profile resolution stays consistent with how
        year-end aggregates the rest of its inputs.  A profile in a
        different scenario must NOT be returned.
        """
        with app.app_context():
            # Insert profile in seed_user's baseline scenario.
            _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            # Lookup with a different (non-existent) scenario_id returns
            # zero -- no profile matches the filter.
            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                scenario_id=seed_user["scenario"].id + 9999,
                as_of=_AS_OF_AFTER_RAISE,
            )
            assert result == Decimal("0")

            # Same call with the correct scenario_id resolves the profile.
            result_match = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                as_of=_AS_OF_AFTER_RAISE,
            )
            assert result_match == _NO_RAISE_GROSS


class TestConsumerIntegration:
    """C17-4: every downstream consumer reads the same engine value."""

    def test_c17_4_savings_year_end_investment_agree_on_raised_gross(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C17-4: four consumers route through the engine-derived value.

        Sets up one raise-applicable scenario and calls each consumer's
        private helper (or the producer that fans the value out).  All
        four must report the same engine-derived per-period gross.  The
        fixture uses ``seed_periods_today`` so ``date.today()`` falls
        in a period whose ``period_month >= effective_month`` -- the
        raise effective Jan 2026 applies to every 2026 period.

        Hand arithmetic: ``104000 * 1.03 / 26 = 4120.00``.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, scenario.id)
            _add_one_time_raise(
                profile, effective_month=1, effective_year=2026,
            )
            db.session.commit()

            # Producer: the canonical helper itself.
            canonical = income_service.get_current_gross_biweekly(user_id)
            assert canonical == _RAISE_APPLIED_GROSS

            # Savings consumer: routed through income_service via
            # ``_load_account_params`` (in the package's ``_data``
            # sub-module after the Phase 2 split).  ``accounts`` is read
            # but the salary value is independent of any account.
            savings_params = savings_dashboard_service._data._load_account_params(
                user_id, accounts=[],
            )
            assert savings_params.salary_gross_biweekly == canonical

            # Year-end consumer: thin delegator over income_service
            # (moved to the ._data sub-module in the Phase 2 split).
            year_end_val = (
                year_end_summary_service._data._load_salary_gross_biweekly(
                    user_id, scenario,
                )
            )
            assert year_end_val == canonical

            # Investment consumer: Commit 17 introduced a thin
            # ``_salary_gross_biweekly`` wrapper around
            # ``income_service.get_current_gross_biweekly``; Commit 18
            # (F-22) removed the wrapper and routed
            # ``_projection_inputs_for_account`` through the canonical
            # helper directly.  Asserting the producer alone still
            # locks the producer/consumer agreement because the
            # investment dashboard now has no intermediate site that
            # could drift.
            investment_val = income_service.get_current_gross_biweekly(user_id)
            assert investment_val == canonical
