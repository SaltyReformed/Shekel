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
from app.models.ref import FilingStatus, RaiseType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services import (
    income_service,
    investment_dashboard_service,
    savings_dashboard_service,
    year_end_summary_service,
)


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
            # ``_load_account_params``.  ``accounts`` is read but the
            # salary value is independent of any account.
            savings_params = savings_dashboard_service._load_account_params(
                user_id, accounts=[],
            )
            assert savings_params["salary_gross_biweekly"] == canonical

            # Year-end consumer: thin delegator over income_service.
            year_end_val = (
                year_end_summary_service._load_salary_gross_biweekly(
                    user_id, scenario,
                )
            )
            assert year_end_val == canonical

            # Investment consumer: ``_salary_gross_biweekly`` post-Commit-17
            # takes ``user_id`` and delegates to income_service.
            investment_val = (
                investment_dashboard_service._salary_gross_biweekly(user_id)
            )
            assert investment_val == canonical
