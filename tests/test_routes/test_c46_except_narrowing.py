"""
Shekel Budget App -- C-46 / F-145 Exception Narrowing Tests

Verifies that the post-C-46 narrowing of broad ``except Exception:``
blocks in salary, retirement, and investment routes correctly:

  1. catches SQLAlchemyError-family exceptions (DataError, FK
     violations, etc.) so the user-facing flash + redirect and the
     ``db.session.rollback()`` still run for every previously-
     swallowed DB-tier failure,
  2. allows non-SQLAlchemy exceptions (RuntimeError, TypeError,
     decimal arithmetic) to propagate to the Flask 500 handler
     instead of being silently swallowed as a generic flash.

For each previously-broad ``except Exception:`` site, one test
patches the relevant DB write to raise a ``DataError`` and asserts
the narrowed handler runs.  A representative test patches a write
to raise a ``RuntimeError`` and asserts the exception propagates
through the test client (proving the narrow catch does not swallow
programming bugs).

The retirement and investment routes use ``except InvalidOperation``
to narrow a ``Decimal(str) / Decimal("100")`` pre-validation step;
those handlers are covered by the existing
``test_update_settings_invalid_swr`` family in ``test_retirement.py``
plus a new test for ``investment._convert_percentage_inputs``.

Cross-references: F-145 (Low), C-46.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.exc import DataError

from app.extensions import db
from app.models.calibration_override import CalibrationOverride
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming, FilingStatus, RaiseType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from tests._test_helpers import freeze_today
from tests.test_routes.test_salary import _create_profile


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today inside the seeded period range.

    Matches the same freeze used by ``test_salary.py`` so the
    ``_create_profile`` helper and ``seed_periods`` fixture cooperate
    deterministically regardless of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _make_data_error():
    """Construct a representative ``DataError`` for use as side_effect.

    The instance's class hierarchy matters (must be a
    ``SQLAlchemyError`` subclass to land in the narrowed catch);
    the statement, params, and orig fields are not inspected by the
    route handlers under test.
    """
    return DataError(
        statement="(C-46 narrow-catch regression test)",
        params=None,
        orig=Exception("simulated NUMERIC overflow"),
    )


# ── salary.py: routes wrapping db.session.commit() ─────────────────


class TestSalaryNarrowCatch:
    """Verify each ``except SQLAlchemyError`` block in salary.py.

    Each test patches ``db.session.commit`` to raise a ``DataError``
    so the narrowed catch fires.  The previously-broad
    ``except Exception:`` would have caught this too; the regression
    here is that the post-narrow handler still rolls back the
    session, flashes the user-facing message, and redirects to a
    safe page (and -- crucially -- that non-SQLAlchemy exceptions
    are no longer swept under the same generic flash).
    """

    def test_create_profile_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``create_profile`` commit triggers narrow catch."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    "/salary",
                    data={
                        "name": "New Profile",
                        "annual_salary": "75000.00",
                        "filing_status_id": filing_status.id,
                        "state_code": "NC",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert "/salary/new" in resp.headers["Location"], (
                "create_profile must redirect to /salary/new on DB-tier "
                "failure so the user can retry with the same form."
            )

            # Rollback verified: no profile persisted under the
            # would-be name.
            db.session.expire_all()
            persisted = db.session.query(SalaryProfile).filter_by(
                user_id=seed_user["user"].id, name="New Profile",
            ).all()
            assert persisted == []

    def test_update_profile_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``update_profile`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            original_salary = profile.annual_salary
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile.id}",
                    data={
                        "name": "Day Job",
                        "annual_salary": "90000.00",
                        "filing_status_id": filing_status.id,
                        "state_code": "NC",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: annual_salary unchanged.
            db.session.expire_all()
            refreshed = db.session.get(SalaryProfile, profile.id)
            assert refreshed.annual_salary == original_salary

    def test_add_raise_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``add_raise`` commit triggers narrow catch.

        The ``except IntegrityError`` branch above the narrowed
        ``except SQLAlchemyError`` only handles ``IntegrityError``;
        ``DataError`` is a sibling subclass and must fall through to
        the narrow catch.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(
                name="merit",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile.id}/raises",
                    data={
                        "raise_type_id": raise_type.id,
                        "effective_month": "7",
                        "effective_year": "2026",
                        "percentage": "3",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: no raise persisted.
            db.session.expire_all()
            persisted = db.session.query(SalaryRaise).filter_by(
                salary_profile_id=profile.id,
            ).all()
            assert persisted == []

    def test_delete_raise_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``delete_raise`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(
                name="merit",
            ).one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_month=6,
                effective_year=2026,
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()
            raise_id = salary_raise.id

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/raises/{raise_id}/delete",
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: raise still present.
            db.session.expire_all()
            still_there = db.session.get(SalaryRaise, raise_id)
            assert still_there is not None

    def test_update_raise_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``update_raise`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(
                name="merit",
            ).one()
            cola_type = db.session.query(RaiseType).filter_by(
                name="cola",
            ).one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_month=6,
                effective_year=2026,
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()
            raise_id = salary_raise.id
            original_pct = salary_raise.percentage

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/raises/{raise_id}/edit",
                    data={
                        "raise_type_id": cola_type.id,
                        "effective_month": "7",
                        "effective_year": "2026",
                        "percentage": "5",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: raise unchanged.
            db.session.expire_all()
            refreshed = db.session.get(SalaryRaise, raise_id)
            assert refreshed.raise_type_id == raise_type.id
            assert refreshed.percentage == original_pct

    def test_add_deduction_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``add_deduction`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(
                name="pre_tax",
            ).one()
            flat_method = db.session.query(CalcMethod).filter_by(
                name="flat",
            ).one()

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile.id}/deductions",
                    data={
                        "name": "401k",
                        "deduction_timing_id": pre_tax.id,
                        "calc_method_id": flat_method.id,
                        "amount": "200.00",
                        "deductions_per_year": "26",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: no deduction persisted.
            db.session.expire_all()
            persisted = db.session.query(PaycheckDeduction).filter_by(
                salary_profile_id=profile.id,
            ).all()
            assert persisted == []

    def test_delete_deduction_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``delete_deduction`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(
                name="pre_tax",
            ).one()
            flat_method = db.session.query(CalcMethod).filter_by(
                name="flat",
            ).one()

            deduction = PaycheckDeduction(
                salary_profile_id=profile.id,
                deduction_timing_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="401k",
                amount=Decimal("200.0000"),
            )
            db.session.add(deduction)
            db.session.commit()
            ded_id = deduction.id

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/deductions/{ded_id}/delete",
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: deduction still present.
            db.session.expire_all()
            still_there = db.session.get(PaycheckDeduction, ded_id)
            assert still_there is not None

    def test_update_deduction_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` (non-Integrity) on ``update_deduction`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(DeductionTiming).filter_by(
                name="pre_tax",
            ).one()
            flat_method = db.session.query(CalcMethod).filter_by(
                name="flat",
            ).one()

            deduction = PaycheckDeduction(
                salary_profile_id=profile.id,
                deduction_timing_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="401k",
                amount=Decimal("200.0000"),
            )
            db.session.add(deduction)
            db.session.commit()
            ded_id = deduction.id
            original_amount = deduction.amount

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/deductions/{ded_id}/edit",
                    data={
                        "name": "401k",
                        "deduction_timing_id": pre_tax.id,
                        "calc_method_id": flat_method.id,
                        "amount": "500.00",
                        "deductions_per_year": "26",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Rollback verified: amount unchanged.
            db.session.expire_all()
            refreshed = db.session.get(PaycheckDeduction, ded_id)
            assert refreshed.amount == original_amount

    def test_calibrate_confirm_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``calibrate_confirm`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            profile_id = profile.id

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile_id}/calibrate/confirm",
                    data={
                        "actual_gross_pay": "2884.62",
                        "actual_federal_tax": "200.00",
                        "actual_state_tax": "100.00",
                        "actual_social_security": "178.85",
                        "actual_medicare": "41.83",
                        "effective_federal_rate": "0.0700",
                        "effective_state_rate": "0.0350",
                        "effective_ss_rate": "0.0620",
                        "effective_medicare_rate": "0.0145",
                        "pay_stub_date": "2026-03-14",
                    },
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile_id}/calibrate" in resp.headers["Location"]

            # Rollback verified: no calibration row persisted.
            db.session.expire_all()
            persisted = db.session.query(CalibrationOverride).filter_by(
                salary_profile_id=profile_id,
            ).all()
            assert persisted == []

    def test_calibrate_delete_data_error_handled(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``DataError`` on ``calibrate_delete`` commit triggers narrow catch."""
        with app.app_context():
            profile = _create_profile(seed_user)
            profile_id = profile.id

            # Seed a calibration so the delete branch executes.
            cal = CalibrationOverride(
                salary_profile_id=profile_id,
                actual_gross_pay=Decimal("2884.62"),
                actual_federal_tax=Decimal("200.00"),
                actual_state_tax=Decimal("100.00"),
                actual_social_security=Decimal("178.85"),
                actual_medicare=Decimal("41.83"),
                effective_federal_rate=Decimal("0.07000"),
                effective_state_rate=Decimal("0.03500"),
                effective_ss_rate=Decimal("0.06200"),
                effective_medicare_rate=Decimal("0.01450"),
                pay_stub_date=date(2026, 3, 14),
                is_active=True,
            )
            db.session.add(cal)
            db.session.commit()
            cal_id = cal.id

            with patch.object(
                db.session, "commit", side_effect=_make_data_error(),
            ):
                resp = auth_client.post(
                    f"/salary/{profile_id}/calibrate/delete",
                    follow_redirects=False,
                )

            assert resp.status_code == 302
            assert f"/salary/{profile_id}/edit" in resp.headers["Location"]

            # Rollback verified: calibration row still present.
            db.session.expire_all()
            still_there = db.session.get(CalibrationOverride, cal_id)
            assert still_there is not None


# ── salary.py: private helper ──────────────────────────────────────


class TestRegenerateHelperNarrowCatch:
    """Verify ``_regenerate_salary_transactions``' narrowed catch.

    The helper logs ``SQLAlchemyError`` raised by
    ``recurrence_engine.regenerate_for_template`` (so operators see
    profile-id context) and re-raises so the calling route catches
    it.  Non-SQLAlchemy exceptions propagate without the helper's
    log entry but the route handler still records them.
    """

    def test_helper_logs_sqlalchemy_error_with_profile_id(
        self, app, auth_client, seed_user, seed_periods, caplog,
    ):
        """Helper logs the profile-id when regenerate raises SQLAlchemyError.

        Patches ``recurrence_engine.regenerate_for_template`` (the
        only SQLAlchemy-emitting call inside the helper's try block)
        to raise a ``DataError``, then drives the helper through
        ``update_profile``.  The helper's ``logger.exception`` must
        emit a record whose message includes the profile id, and the
        outer route's ``except SQLAlchemyError`` block then converts
        the re-raised error into the standard flash + redirect.
        """
        import logging
        with app.app_context():
            profile = _create_profile(seed_user)
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with caplog.at_level(logging.ERROR, logger="app.routes.salary"):
                with patch(
                    "app.routes.salary.recurrence_engine."
                    "regenerate_for_template",
                    side_effect=_make_data_error(),
                ):
                    resp = auth_client.post(
                        f"/salary/{profile.id}",
                        data={
                            "name": "Day Job",
                            "annual_salary": "80000.00",
                            "filing_status_id": filing_status.id,
                            "state_code": "NC",
                        },
                        follow_redirects=False,
                    )

            assert resp.status_code == 302
            assert f"/salary/{profile.id}/edit" in resp.headers["Location"]

            # Helper logged with profile id.
            helper_logs = [
                rec for rec in caplog.records
                if "Failed to regenerate salary transactions" in rec.getMessage()
                and str(profile.id) in rec.getMessage()
            ]
            assert len(helper_logs) >= 1, (
                "The helper's ``except SQLAlchemyError`` block must emit "
                "a ``logger.exception`` record naming the profile id so "
                "operators can correlate regeneration failures with the "
                "salary profile under maintenance."
            )


# ── salary.py: propagation guarantee for non-SQLAlchemy errors ─────


class TestNonSqlAlchemyErrorPropagates:
    """The narrowed catch must NOT swallow programming bugs.

    Pre-C-46 the broad ``except Exception:`` would have caught any
    runtime error and presented the same generic flash.  Post-C-46
    the narrow ``except SQLAlchemyError:`` lets non-SQLAlchemy
    exceptions propagate so they surface as 500s (test client raises
    in TESTING mode) instead of hiding behind a "Failed to ...
    Please try again." flash that an operator would never see in the
    logs as anything other than a routine user error.
    """

    def test_create_profile_runtime_error_propagates(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """``RuntimeError`` from commit is no longer swallowed."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()

            with patch.object(
                db.session, "commit",
                side_effect=RuntimeError("simulated programming bug"),
            ):
                with pytest.raises(RuntimeError, match="programming bug"):
                    auth_client.post(
                        "/salary",
                        data={
                            "name": "New Profile",
                            "annual_salary": "75000.00",
                            "filing_status_id": filing_status.id,
                            "state_code": "NC",
                        },
                        follow_redirects=False,
                    )


# ── investment.py: ``except InvalidOperation`` pre-processing ──────


class TestInvestmentInvalidOperationCatch:
    """Verify ``_convert_percentage_inputs`` narrow catch.

    The helper converts percentage form inputs (e.g. ``"7"`` -> ``"0.07"``)
    before Marshmallow validation runs.  Non-numeric input raises
    ``decimal.InvalidOperation``; the narrow catch leaves the raw
    value in place so Marshmallow's "Not a valid number." message
    re-renders the form rather than silently swallowing the error.
    """

    def test_update_params_non_numeric_return_passes_to_validation(
        self, app, auth_client, seed_user,
    ):
        """Non-numeric ``assumed_annual_return`` reaches Marshmallow as-is."""
        with app.app_context():
            retirement_type = db.session.query(AccountType).filter_by(
                name="401(k)",
            ).first()
            assert retirement_type is not None, (
                "Reference data must seed a 401(k) account type for "
                "investment routes to exercise their narrow catch."
            )

            from app.models.account import Account
            account = Account(
                user_id=seed_user["user"].id,
                account_type_id=retirement_type.id,
                name="My 401k",
                current_anchor_balance=Decimal("0.00"),
            )
            db.session.add(account)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/{account.id}/investment/params",
                data={
                    "assumed_annual_return": "abc",
                    "annual_contribution_limit": "23000",
                    "contribution_limit_year": "2026",
                    "employer_contribution_type": "none",
                },
                follow_redirects=False,
            )

            # Marshmallow rejects "abc" with the validation error
            # flash.  The route redirects to the dashboard.
            assert resp.status_code == 302
            assert f"/accounts/{account.id}/investment" in resp.headers["Location"]

            # No InvestmentParams row persisted.
            db.session.expire_all()
            persisted = db.session.query(InvestmentParams).filter_by(
                account_id=account.id,
            ).all()
            assert persisted == []
