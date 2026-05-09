"""
Shekel Budget App -- C-23 Salary Raise / Deduction Uniqueness Tests

Verifies the composite unique constraints introduced in commit C-23
of the 2026-04-15 security remediation plan:

  - ``uq_salary_raises_profile_type_year_month`` on
    ``salary.salary_raises (salary_profile_id, raise_type_id,
    effective_year, effective_month)`` with
    ``NULLS NOT DISTINCT`` semantics (F-051).
  - ``uq_paycheck_deductions_profile_name`` on
    ``salary.paycheck_deductions (salary_profile_id, name)`` (F-052).

Coverage:
  1. Database-level enforcement (raw INSERT raises IntegrityError on
     the named constraint, including the NULL-year case for raises).
  2. Route-level idempotency (double-submit on add returns one row
     and a friendly flash, not a 500).
  3. Route-level update collision (renaming or re-keying to an
     existing row redirects with a warning, leaves DB unchanged).
  4. Permitted variations (different year, different month,
     different type, different name -- distinct rows allowed).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.category import Category
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    CalcMethod, DeductionTiming, FilingStatus,
    RaiseType, RecurrencePattern, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction_template import TransactionTemplate
from app.utils.db_errors import is_unique_violation

from tests._test_helpers import freeze_today


# ── Fixtures and helpers ────────────────────────────────────────


SALARY_RAISES_UNIQUE = "uq_salary_raises_profile_type_year_month"
PAYCHECK_DEDUCTIONS_UNIQUE = "uq_paycheck_deductions_profile_name"


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today inside the seeded period range.

    Mirrors the fixture in ``test_salary.py``.  Required because
    these tests exercise routes that call ``_regenerate_salary_transactions``,
    which in turn calls ``pay_period_service.get_current_period``;
    that lookup needs to find a seeded period at "today" or it
    skips regeneration silently and the tests cannot tell whether
    the constraint or the regeneration path failed.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _create_profile(seed_user):
    """Create a salary profile with linked template and recurrence."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    every_period = db.session.query(RecurrencePattern).filter_by(name="Every Period").one()

    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if cat is None:
        cat = Category(
            user_id=seed_user["user"].id,
            group_name="Income",
            item_name="Salary",
        )
        db.session.add(cat)
        db.session.flush()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id, pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        recurrence_rule_id=rule.id,
        transaction_type_id=income_type.id,
        name="Day Job",
        default_amount=Decimal("75000.00") / 26,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Day Job",
        annual_salary=Decimal("75000.00"),
        state_code="NC",
        pay_periods_per_year=26,
    )
    db.session.add(profile)
    db.session.commit()
    return profile


def _make_raise(profile, raise_type, *, year, month, percentage="0.03",
                is_recurring=False, flat_amount=None):
    """Insert a SalaryRaise directly (bypasses the route layer).

    Used to seed pre-existing rows for collision tests at the model
    layer.  Returns the persisted object so callers can read its id.
    """
    kwargs = dict(
        salary_profile_id=profile.id,
        raise_type_id=raise_type.id,
        effective_month=month,
        effective_year=year,
        is_recurring=is_recurring,
    )
    if flat_amount is not None:
        kwargs["flat_amount"] = Decimal(flat_amount)
    else:
        kwargs["percentage"] = Decimal(percentage)
    obj = SalaryRaise(**kwargs)
    db.session.add(obj)
    db.session.commit()
    return obj


def _make_deduction(profile, name="401k", amount="200.00",
                    timing_name="pre_tax", method_name="flat"):
    """Insert a PaycheckDeduction directly (bypasses the route layer)."""
    timing = db.session.query(DeductionTiming).filter_by(name=timing_name).one()
    method = db.session.query(CalcMethod).filter_by(name=method_name).one()
    obj = PaycheckDeduction(
        salary_profile_id=profile.id,
        deduction_timing_id=timing.id,
        calc_method_id=method.id,
        name=name,
        amount=Decimal(amount),
    )
    db.session.add(obj)
    db.session.commit()
    return obj


# ── Database-level enforcement ──────────────────────────────────


class TestSalaryRaiseUniqueConstraint:
    """Direct INSERT collisions surface IntegrityError on the C-23 constraint."""

    def test_duplicate_non_recurring_raise_rejected(self, app, seed_user, seed_periods):
        """Two non-recurring raises sharing (profile, type, year, month) collide."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            _make_raise(profile, merit, year=2026, month=7)

            duplicate = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                effective_month=7,
                effective_year=2026,
                percentage=Decimal("0.05"),
                is_recurring=False,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, SALARY_RAISES_UNIQUE,
            ), (
                "Expected IntegrityError on the C-23 constraint; got "
                f"constraint={getattr(getattr(excinfo.value.orig, 'diag', None), 'constraint_name', None)!r}"
            )

    def test_duplicate_recurring_raise_with_null_year_rejected(
        self, app, seed_user, seed_periods,
    ):
        """Two recurring raises with NULL year and same (profile, type, month) collide.

        This is the load-bearing assertion for the
        ``NULLS NOT DISTINCT`` modifier on the constraint: the
        SQL-standard default treats every NULL as a distinct value,
        which would let two recurring raises slip through and
        compound erroneously in the paycheck calculator.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            cola = db.session.query(RaiseType).filter_by(name="cola").one()
            _make_raise(
                profile, cola, year=None, month=4,
                percentage="0.025", is_recurring=True,
            )

            duplicate = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=cola.id,
                effective_month=4,
                effective_year=None,
                percentage=Decimal("0.025"),
                is_recurring=True,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, SALARY_RAISES_UNIQUE,
            )

    def test_distinct_year_or_month_or_type_allowed(
        self, app, seed_user, seed_periods,
    ):
        """Different year, month, or type creates distinct raises."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            cola = db.session.query(RaiseType).filter_by(name="cola").one()
            _make_raise(profile, merit, year=2026, month=7)
            # Different year.
            _make_raise(profile, merit, year=2027, month=7)
            # Different month.
            _make_raise(profile, merit, year=2026, month=8)
            # Different type.
            _make_raise(profile, cola, year=2026, month=7,
                        flat_amount="500.00")
            count = (
                db.session.query(SalaryRaise)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 4

    def test_same_shape_on_different_profile_allowed(
        self, app, seed_user, seed_periods,
    ):
        """The constraint scopes to a single salary profile, not user-wide."""
        with app.app_context():
            profile_a = _create_profile(seed_user)
            # Build a second profile under the same user; the
            # ``_create_profile`` helper insists on a unique
            # template, so we duplicate the bare minimum here.
            filing = (
                db.session.query(FilingStatus).filter_by(name="single").one()
            )
            profile_b = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                template_id=None,
                filing_status_id=filing.id,
                name="Side Gig",
                annual_salary=Decimal("20000.00"),
                state_code="NC",
                pay_periods_per_year=26,
            )
            db.session.add(profile_b)
            db.session.commit()

            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            _make_raise(profile_a, merit, year=2026, month=7)
            _make_raise(profile_b, merit, year=2026, month=7)
            count = (
                db.session.query(SalaryRaise)
                .filter(SalaryRaise.salary_profile_id.in_(
                    [profile_a.id, profile_b.id],
                ))
                .count()
            )
            assert count == 2


class TestPaycheckDeductionUniqueConstraint:
    """Direct INSERT collisions surface IntegrityError on the C-23 constraint."""

    def test_duplicate_name_on_same_profile_rejected(
        self, app, seed_user, seed_periods,
    ):
        """Two deductions sharing (profile, name) collide."""
        with app.app_context():
            profile = _create_profile(seed_user)
            _make_deduction(profile, name="401k", amount="200.00")

            timing = (
                db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            duplicate = PaycheckDeduction(
                salary_profile_id=profile.id,
                deduction_timing_id=timing.id,
                calc_method_id=method.id,
                name="401k",
                amount=Decimal("250.00"),
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, PAYCHECK_DEDUCTIONS_UNIQUE,
            )

    def test_duplicate_blocked_even_when_first_is_inactive(
        self, app, seed_user, seed_periods,
    ):
        """is_active toggle does not exempt rows from the unique key.

        The audit cited this as a deliberate tradeoff: a deactivated
        deduction must be reactivated rather than re-created, so
        the unique scope is the full table on the salary profile,
        not "active rows only."
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            existing = _make_deduction(profile, name="Health Insurance")
            existing.is_active = False
            db.session.commit()

            timing = (
                db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            duplicate = PaycheckDeduction(
                salary_profile_id=profile.id,
                deduction_timing_id=timing.id,
                calc_method_id=method.id,
                name="Health Insurance",
                amount=Decimal("199.00"),
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, PAYCHECK_DEDUCTIONS_UNIQUE,
            )

    def test_distinct_name_or_profile_allowed(
        self, app, seed_user, seed_periods,
    ):
        """Different name or profile creates distinct deductions."""
        with app.app_context():
            profile = _create_profile(seed_user)
            _make_deduction(profile, name="401k", amount="200.00")
            _make_deduction(profile, name="Roth IRA", amount="150.00")
            count = (
                db.session.query(PaycheckDeduction)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 2


# ── Route-level enforcement ─────────────────────────────────────


class TestAddRaiseRoute:
    """POST /salary/<id>/raises double-submit handling."""

    def test_double_submit_creates_one_raise(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Duplicate POST returns idempotent success and one row."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            data = {
                "raise_type_id": merit.id,
                "effective_month": "7",
                "effective_year": "2026",
                "percentage": "3",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/raises", data=data,
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert b"Raise added." in r1.data

            r2 = auth_client.post(
                f"/salary/{profile.id}/raises", data=data,
                follow_redirects=True,
            )
            assert r2.status_code == 200
            assert b"already exists" in r2.data
            # The "Raise added." flash from the second submit is
            # absent: we hit the IntegrityError branch.
            assert r2.data.count(b"Raise added.") == 0

            db.session.expire_all()
            count = (
                db.session.query(SalaryRaise)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 1

    def test_distinct_year_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Different effective_year is a different raise -- both succeed."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            base = {
                "raise_type_id": merit.id,
                "effective_month": "7",
                "percentage": "3",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={**base, "effective_year": "2026"},
                follow_redirects=True,
            )
            r2 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={**base, "effective_year": "2027"},
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert b"Raise added." in r1.data
            assert b"Raise added." in r2.data
            db.session.expire_all()
            count = (
                db.session.query(SalaryRaise)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 2


class TestUpdateRaiseRoute:
    """POST /salary/raises/<id>/edit collision handling."""

    def test_rename_to_existing_key_returns_warning(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Editing a raise to collide with a sibling redirects with a warning."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            existing = _make_raise(profile, merit, year=2026, month=7)
            target = _make_raise(profile, merit, year=2027, month=8)
            target_id = target.id
            target_version = target.version_id

            # Try to edit ``target`` to overlap ``existing``'s key.
            resp = auth_client.post(
                f"/salary/raises/{target_id}/edit",
                data={
                    "raise_type_id": str(merit.id),
                    "effective_month": "7",
                    "effective_year": "2026",
                    "percentage": "3",
                    "version_id": str(target_version),
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already covers" in resp.data

            # Target row's effective date must be unchanged.
            db.session.expire_all()
            refreshed = db.session.get(SalaryRaise, target_id)
            assert refreshed.effective_year == 2027
            assert refreshed.effective_month == 8
            # And ``existing`` must still exist (not overwritten).
            assert (
                db.session.query(SalaryRaise)
                .filter_by(id=existing.id)
                .one()
                .effective_year == 2026
            )


class TestAddDeductionRoute:
    """POST /salary/<id>/deductions double-submit handling."""

    def test_double_submit_creates_one_deduction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Duplicate POST returns idempotent success and one row."""
        with app.app_context():
            profile = _create_profile(seed_user)
            timing = (
                db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            data = {
                "name": "401k",
                "deduction_timing_id": timing.id,
                "calc_method_id": method.id,
                "amount": "250.00",
                "deductions_per_year": "26",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/deductions", data=data,
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert b"401k" in r1.data
            assert b"added" in r1.data

            r2 = auth_client.post(
                f"/salary/{profile.id}/deductions", data=data,
                follow_redirects=True,
            )
            assert r2.status_code == 200
            assert b"already exists" in r2.data

            db.session.expire_all()
            count = (
                db.session.query(PaycheckDeduction)
                .filter_by(salary_profile_id=profile.id, name="401k")
                .count()
            )
            assert count == 1

    def test_distinct_name_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Different name is a different deduction -- both succeed."""
        with app.app_context():
            profile = _create_profile(seed_user)
            timing = (
                db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            base = {
                "deduction_timing_id": timing.id,
                "calc_method_id": method.id,
                "amount": "200.00",
                "deductions_per_year": "26",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={**base, "name": "401k"},
                follow_redirects=True,
            )
            r2 = auth_client.post(
                f"/salary/{profile.id}/deductions",
                data={**base, "name": "HSA"},
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            db.session.expire_all()
            count = (
                db.session.query(PaycheckDeduction)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 2


class TestUpdateDeductionRoute:
    """POST /salary/deductions/<id>/edit collision handling."""

    def test_rename_to_existing_name_returns_warning(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Editing a deduction's name to collide with a sibling redirects with a warning."""
        with app.app_context():
            profile = _create_profile(seed_user)
            timing = (
                db.session.query(DeductionTiming).filter_by(name="pre_tax").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            existing = _make_deduction(profile, name="401k")
            target = _make_deduction(profile, name="HSA")
            target_id = target.id
            target_version = target.version_id

            resp = auth_client.post(
                f"/salary/deductions/{target_id}/edit",
                data={
                    "name": "401k",
                    "deduction_timing_id": str(timing.id),
                    "calc_method_id": str(method.id),
                    "amount": "200.00",
                    "deductions_per_year": "26",
                    "version_id": str(target_version),
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already uses that" in resp.data

            db.session.expire_all()
            refreshed = db.session.get(PaycheckDeduction, target_id)
            assert refreshed.name == "HSA"
            # ``existing`` is untouched.
            assert (
                db.session.query(PaycheckDeduction)
                .filter_by(id=existing.id)
                .one()
                .name == "401k"
            )
