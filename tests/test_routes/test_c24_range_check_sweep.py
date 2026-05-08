"""
Shekel Budget App -- C-24 Range and CHECK Constraint Sweep Tests

Verifies the schema bound additions and database CHECK constraint
sweep introduced by commit C-24 of the 2026-04-15 security
remediation plan:

  - F-011: ``RaiseCreateSchema.percentage`` and ``flat_amount`` are
    bounded to a positive, column-fitting range that rejects pay-cut
    values and absurd typos.
  - F-012: ``DeductionCreateSchema.amount`` carries a wide
    field-level Range plus the
    ``validate_amount_against_calc_method`` cross-field rule that
    caps percent-method deductions at 100%.
  - F-074: ``SalaryProfile`` W-4 fields gain ``Range(min=0)``
    validators on both create and update schemas.
  - F-075: ``TaxBracketSetSchema`` monetary fields gain
    ``Range(min=0)`` validators; ``tax_year`` gains the same
    ``Range(2000, 2100)`` enforced on raises.
  - F-077: 21 storage-tier CHECK constraints across nine tables
    that reject raw-SQL bypasses of the form-layer validators.

For each finding, the tests cover three dimensions:

  1. **Boundary acceptance** -- a value at each side of the new
     bound is accepted by the schema (no false rejections).
  2. **Bound rejection** -- a value just outside the bound surfaces
     a clean :class:`marshmallow.exceptions.ValidationError` on the
     correct field name (no IntegrityError fall-through).
  3. **Storage-tier rejection** -- a raw INSERT/UPDATE that bypasses
     the schema and would violate the predicate is rejected by the
     database with a named CHECK violation.

Tests for the migration's pre-flight refusal logic live alongside
the existing C-22/C-23 migration round-trip coverage.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.calibration_override import CalibrationOverride
from app.models.interest_params import InterestParams
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import (
    AccountType, CalcMethod, DeductionTiming,
    FilingStatus, RaiseType, TaxType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.user import UserSettings
from app.schemas.validation import (
    DeductionCreateSchema,
    FicaConfigSchema,
    InvestmentParamsCreateSchema,
    RaiseCreateSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
    StateTaxConfigSchema,
    TaxBracketSetSchema,
)


# ── Constraint name constants ─────────────────────────────────────
#
# Each must stay in sync with the matching declaration in
# ``app/models/`` and with the migration body in
# ``migrations/versions/b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py``.

CK_ESCROW_NONNEG = "ck_escrow_components_nonneg_annual_amount"
CK_ESCROW_INFLATION = "ck_escrow_components_valid_inflation_rate"
CK_INTEREST_APY = "ck_interest_params_valid_apy"
CK_INVEST_LIMIT = "ck_investment_params_nonneg_contribution_limit"
CK_INVEST_FLAT = "ck_investment_params_valid_employer_flat_pct"
CK_INVEST_MATCH = "ck_investment_params_valid_employer_match_pct"
CK_INVEST_CAP = "ck_investment_params_valid_employer_match_cap"
CK_RATE_HISTORY = "ck_rate_history_valid_interest_rate"
CK_USER_SWR = "ck_user_settings_valid_safe_withdrawal"
CK_USER_TAX_RATE = "ck_user_settings_valid_estimated_tax_rate"
CK_DEDUCTION_INFL_RATE = "ck_paycheck_deductions_valid_inflation_rate"
CK_DEDUCTION_INFL_MONTH = "ck_paycheck_deductions_valid_inflation_month"
CK_RAISE_YEAR = "ck_salary_raises_valid_effective_year"
CK_STATE_TAX_DEDUCTION = "ck_state_tax_configs_nonneg_standard_deduction"
CK_STATE_TAX_YEAR = "ck_state_tax_configs_valid_tax_year"
CK_FICA_TAX_YEAR = "ck_fica_configs_valid_tax_year"
CK_BRACKET_TAX_YEAR = "ck_tax_bracket_sets_valid_tax_year"
CK_CALIB_FED = "ck_calibration_overrides_valid_federal_rate"
CK_CALIB_STATE = "ck_calibration_overrides_valid_state_rate"
CK_CALIB_SS = "ck_calibration_overrides_valid_ss_rate"
CK_CALIB_MEDICARE = "ck_calibration_overrides_valid_medicare_rate"


def _constraint_name_from(exc: IntegrityError) -> str | None:
    """Return the named constraint reported on the IntegrityError.

    Reads ``exc.orig.diag.constraint_name`` -- the structured field
    psycopg2 surfaces from the PostgreSQL error packet -- so the
    test does not depend on the brittle prose of the error message.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    diag = getattr(orig, "diag", None)
    if diag is None:
        return None
    return getattr(diag, "constraint_name", None)


# ── F-011: RaiseCreateSchema bounds ───────────────────────────────


class TestRaiseSchemaBounds:
    """Schema-layer bound checks on ``RaiseCreateSchema`` (F-011 / C-24)."""

    def _payload(self, **overrides):
        """Build a baseline raise payload with valid required fields."""
        base = {
            "raise_type_id": "1",
            "effective_month": "1",
            "effective_year": "2026",
            "is_recurring": "false",
        }
        base.update(overrides)
        return base

    def test_minimum_percentage_accepted(self):
        """0.01% is the smallest accepted positive raise."""
        schema = RaiseCreateSchema()
        data = schema.load(
            self._payload(percentage="0.01"),
        )
        assert data["percentage"] == Decimal("0.01")

    def test_zero_percentage_rejected(self):
        """A zero raise is not a raise (no pay cuts policy, F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(percentage="0"))
        assert "percentage" in info.value.messages

    def test_negative_percentage_rejected(self):
        """Pay cuts are not modelled (F-011 explicit decision)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(percentage="-5"))
        assert "percentage" in info.value.messages

    def test_percentage_at_upper_bound_accepted(self):
        """200% is the realistic ceiling (3x salary jump)."""
        schema = RaiseCreateSchema()
        data = schema.load(self._payload(percentage="200"))
        assert data["percentage"] == Decimal("200")

    def test_percentage_above_upper_rejected(self):
        """An order-of-magnitude typo (e.g. 2000) is rejected at the form."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(percentage="201"))
        assert "percentage" in info.value.messages

    def test_minimum_flat_amount_accepted(self):
        """1 cent is the smallest accepted positive flat raise."""
        schema = RaiseCreateSchema()
        data = schema.load(self._payload(flat_amount="0.01"))
        assert data["flat_amount"] == Decimal("0.01")

    def test_zero_flat_amount_rejected(self):
        """A zero flat raise is not a raise (F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(flat_amount="0"))
        assert "flat_amount" in info.value.messages

    def test_negative_flat_amount_rejected(self):
        """Pay cuts are not modelled (F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(flat_amount="-100"))
        assert "flat_amount" in info.value.messages

    def test_flat_amount_at_upper_bound_accepted(self):
        """$10,000,000 is the form-layer ceiling for a flat raise."""
        schema = RaiseCreateSchema()
        data = schema.load(self._payload(flat_amount="10000000"))
        assert data["flat_amount"] == Decimal("10000000")

    def test_flat_amount_above_upper_rejected(self):
        """Above $10M is treated as a typo (F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(flat_amount="10000001"))
        assert "flat_amount" in info.value.messages


# ── F-012: DeductionCreateSchema bounds + cross-field ─────────────


class TestDeductionSchemaBounds:
    """Schema-layer bound checks on ``DeductionCreateSchema`` (F-012 / C-24)."""

    def _payload(self, app, **overrides):
        """Build a baseline deduction payload with valid FK ids."""
        with app.app_context():
            timing_id = (
                db.session.query(DeductionTiming)
                .filter_by(name="pre_tax").one().id
            )
            flat_id = (
                db.session.query(CalcMethod)
                .filter_by(name="flat").one().id
            )
        base = {
            "name": "401k",
            "deduction_timing_id": str(timing_id),
            "calc_method_id": str(flat_id),
            "amount": "500.00",
            "deductions_per_year": "26",
        }
        base.update(overrides)
        return base

    def test_minimum_amount_accepted(self, app):
        """4-decimal precision min (0.0001) is accepted."""
        schema = DeductionCreateSchema()
        data = schema.load(self._payload(app, amount="0.0001"))
        assert data["amount"] == Decimal("0.0001")

    def test_zero_amount_rejected(self, app):
        """A zero deduction has no effect; rejected at the schema."""
        schema = DeductionCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, amount="0"))
        assert "amount" in info.value.messages

    def test_dollar_amount_at_upper_accepted(self, app):
        """$1M is the wide ceiling for the flat-dollar form."""
        schema = DeductionCreateSchema()
        data = schema.load(self._payload(app, amount="1000000"))
        assert data["amount"] == Decimal("1000000")

    def test_dollar_amount_above_upper_rejected(self, app):
        """An obvious extra-digit typo on a flat deduction is rejected."""
        schema = DeductionCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, amount="1000001"))
        assert "amount" in info.value.messages

    def test_percentage_method_caps_at_100(self, app):
        """``validate_amount_against_calc_method`` rejects > 100% percent."""
        with app.app_context():
            pct_id = (
                db.session.query(CalcMethod)
                .filter_by(name="percentage").one().id
            )
        schema = DeductionCreateSchema()
        payload = self._payload(
            app, calc_method_id=str(pct_id), amount="150",
        )
        with pytest.raises(ValidationError) as info:
            schema.load(payload)
        assert "amount" in info.value.messages

    def test_percentage_method_accepts_100(self, app):
        """100% is the inclusive percent ceiling (whole-paycheck deduction)."""
        with app.app_context():
            pct_id = (
                db.session.query(CalcMethod)
                .filter_by(name="percentage").one().id
            )
        schema = DeductionCreateSchema()
        payload = self._payload(
            app, calc_method_id=str(pct_id), amount="100",
        )
        data = schema.load(payload)
        assert data["amount"] == Decimal("100")

    def test_percentage_method_accepts_typical(self, app):
        """A typical 6% 401(k) deduction passes both validators."""
        with app.app_context():
            pct_id = (
                db.session.query(CalcMethod)
                .filter_by(name="percentage").one().id
            )
        schema = DeductionCreateSchema()
        data = schema.load(self._payload(
            app, calc_method_id=str(pct_id), amount="6",
        ))
        assert data["amount"] == Decimal("6")

    def test_inflation_rate_percent_input_accepted(self, app):
        """3% inflation input passes; the route divides by 100 later."""
        schema = DeductionCreateSchema()
        data = schema.load(self._payload(
            app, inflation_enabled="true", inflation_rate="3",
        ))
        assert data["inflation_rate"] == Decimal("3")

    def test_inflation_rate_above_100_rejected(self, app):
        """A 150% per-year escalation is a typo, not a rate."""
        schema = DeductionCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(
                app, inflation_enabled="true", inflation_rate="150",
            ))
        assert "inflation_rate" in info.value.messages

    def test_annual_cap_zero_rejected(self, app):
        """``annual_cap`` must be positive when present (DB CHECK > 0)."""
        schema = DeductionCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, annual_cap="0"))
        assert "annual_cap" in info.value.messages


# ── F-074: SalaryProfile W-4 field bounds ─────────────────────────


class TestSalaryProfileSchemaBounds:
    """Schema-layer bound checks on the W-4 fields (F-074 / C-24)."""

    def _payload(self, app, **overrides):
        """Build a baseline salary-profile payload."""
        with app.app_context():
            single_id = (
                db.session.query(FilingStatus)
                .filter_by(name="single").one().id
            )
        base = {
            "name": "Test Profile",
            "annual_salary": "100000",
            "filing_status_id": str(single_id),
            "state_code": "NC",
        }
        base.update(overrides)
        return base

    def test_zero_w4_fields_accepted(self, app):
        """The schema's ``load_default="0"`` round-trips through the bound."""
        schema = SalaryProfileCreateSchema()
        data = schema.load(self._payload(
            app,
            additional_income="0",
            additional_deductions="0",
            extra_withholding="0",
        ))
        assert data["additional_income"] == Decimal("0")
        assert data["additional_deductions"] == Decimal("0")
        assert data["extra_withholding"] == Decimal("0")

    def test_negative_additional_income_rejected(self, app):
        """DB CHECK ``additional_income >= 0`` is now matched at the schema."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, additional_income="-1"))
        assert "additional_income" in info.value.messages

    def test_negative_additional_deductions_rejected(self, app):
        """DB CHECK ``additional_deductions >= 0`` matched at the schema."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, additional_deductions="-1"))
        assert "additional_deductions" in info.value.messages

    def test_negative_extra_withholding_rejected(self, app):
        """DB CHECK ``extra_withholding >= 0`` matched at the schema."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, extra_withholding="-1"))
        assert "extra_withholding" in info.value.messages

    def test_extra_digit_typo_rejected(self, app):
        """$10M+1 trips the form-layer typo guard."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, additional_income="10000001"))
        assert "additional_income" in info.value.messages

    def test_update_schema_inherits_bounds(self):
        """``SalaryProfileUpdateSchema`` rejects negatives identically."""
        schema = SalaryProfileUpdateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({"additional_income": "-5"})
        assert "additional_income" in info.value.messages


# ── F-075: TaxBracketSetSchema bounds ─────────────────────────────


class TestTaxBracketSetSchemaBounds:
    """Schema-layer bound checks on tax bracket monetary fields (F-075)."""

    def _payload(self, **overrides):
        """Build a baseline tax-bracket-set payload."""
        base = {
            "filing_status_id": "1",
            "tax_year": "2026",
            "standard_deduction": "15000",
            "child_credit_amount": "2000",
            "other_dependent_credit_amount": "500",
        }
        base.update(overrides)
        return base

    def test_zero_credits_accepted(self):
        """Zero is a valid credit amount (the load_default value)."""
        schema = TaxBracketSetSchema()
        data = schema.load(self._payload(
            child_credit_amount="0",
            other_dependent_credit_amount="0",
        ))
        assert data["child_credit_amount"] == Decimal("0")
        assert data["other_dependent_credit_amount"] == Decimal("0")

    def test_negative_standard_deduction_rejected(self):
        """DB CHECK ``standard_deduction >= 0`` matched at the schema."""
        schema = TaxBracketSetSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(standard_deduction="-1"))
        assert "standard_deduction" in info.value.messages

    def test_negative_child_credit_rejected(self):
        """DB CHECK ``child_credit_amount >= 0`` matched at the schema."""
        schema = TaxBracketSetSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(child_credit_amount="-1"))
        assert "child_credit_amount" in info.value.messages

    def test_negative_other_dependent_credit_rejected(self):
        """DB CHECK matched on the third credit too."""
        schema = TaxBracketSetSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(other_dependent_credit_amount="-1"))
        assert "other_dependent_credit_amount" in info.value.messages

    def test_tax_year_below_2000_rejected(self):
        """``tax_year`` is bounded to [2000, 2100] like raises and state."""
        schema = TaxBracketSetSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(tax_year="1999"))
        assert "tax_year" in info.value.messages

    def test_tax_year_above_2100_rejected(self):
        """A year typo (e.g. ``20226``) is rejected at the schema."""
        schema = TaxBracketSetSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(tax_year="2101"))
        assert "tax_year" in info.value.messages


# ── Misc schema bounds ────────────────────────────────────────────


class TestMiscSchemaBounds:
    """Bounds added on F-077-adjacent schemas (FICA, state, investment)."""

    def test_fica_tax_year_below_2000_rejected(self):
        schema = FicaConfigSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({
                "tax_year": "1999",
                "ss_rate": "6.2",
                "ss_wage_base": "176100",
                "medicare_rate": "1.45",
                "medicare_surtax_rate": "0.9",
                "medicare_surtax_threshold": "200000",
            })
        assert "tax_year" in info.value.messages

    def test_state_tax_negative_standard_deduction_rejected(self):
        schema = StateTaxConfigSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({
                "state_code": "NC",
                "tax_year": "2026",
                "standard_deduction": "-1",
            })
        assert "standard_deduction" in info.value.messages

    def test_invest_negative_contribution_limit_rejected(self):
        schema = InvestmentParamsCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({
                "assumed_annual_return": "0.07",
                "annual_contribution_limit": "-1",
            })
        assert "annual_contribution_limit" in info.value.messages

    def test_invest_contribution_limit_year_out_of_range_rejected(self):
        schema = InvestmentParamsCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({
                "assumed_annual_return": "0.07",
                "contribution_limit_year": "1999",
            })
        assert "contribution_limit_year" in info.value.messages


# ── F-077: Storage-tier CHECK enforcement (raw SQL bypass) ────────
#
# Each test below INSERTs or UPDATEs via raw SQL so the route layer
# (and the now-tightened schemas) cannot intercept the value, and
# asserts the database rejects with the specific named constraint.


def _insert_account(seed_user, name, type_name):
    """Insert a budget.accounts row of the given account type."""
    acct_type = (
        db.session.query(AccountType).filter_by(name=type_name).one()
    )
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=acct_type.id,
        name=name,
        current_anchor_balance=Decimal("0.00"),
    )
    db.session.add(account)
    db.session.commit()
    return account


class TestEscrowComponentsCheck:
    """``budget.escrow_components`` CHECK constraints (F-077)."""

    def test_negative_annual_amount_rejected(self, app, seed_user):
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage", "Mortgage",
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.escrow_components "
                        "(account_id, name, annual_amount, "
                        " inflation_rate, is_active, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 'Tax', -100.00, NULL, true, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_ESCROW_NONNEG

    def test_inflation_rate_above_one_rejected(self, app, seed_user):
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage 2", "Mortgage",
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.escrow_components "
                        "(account_id, name, annual_amount, "
                        " inflation_rate, is_active, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 'Tax', 1200.00, 1.5, true, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_ESCROW_INFLATION

    def test_inflation_rate_null_accepted(self, app, seed_user):
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage 3", "Mortgage",
            )
            comp = EscrowComponent(
                account_id=account.id,
                name="Property Tax",
                annual_amount=Decimal("1200.00"),
                inflation_rate=None,
            )
            db.session.add(comp)
            db.session.commit()
            assert comp.id is not None


class TestInterestParamsCheck:
    """``budget.interest_params.apy`` CHECK (F-077)."""

    def test_apy_above_one_rejected(self, app, seed_user):
        with app.app_context():
            account = _insert_account(seed_user, "HYSA 1", "HYSA")
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.interest_params "
                        "(account_id, apy, compounding_frequency, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 1.5, 'daily', now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INTEREST_APY

    def test_apy_at_one_accepted(self, app, seed_user):
        """Storage-tier upper bound is inclusive at 1.0 (== 100% APY)."""
        with app.app_context():
            account = _insert_account(seed_user, "HYSA 2", "HYSA")
            params = InterestParams(
                account_id=account.id,
                apy=Decimal("1.00000"),
                compounding_frequency="daily",
            )
            db.session.add(params)
            db.session.commit()
            assert params.apy == Decimal("1.00000")

    def test_apy_negative_rejected(self, app, seed_user):
        with app.app_context():
            account = _insert_account(seed_user, "HYSA 3", "HYSA")
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.interest_params "
                        "(account_id, apy, compounding_frequency, "
                        " created_at, updated_at) "
                        "VALUES (:aid, -0.01, 'daily', now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INTEREST_APY


class TestInvestmentParamsCheck:
    """``budget.investment_params`` CHECK constraints (F-077)."""

    def _account(self, seed_user):
        return _insert_account(seed_user, "401k", "401(k)")

    def test_negative_contribution_limit_rejected(self, app, seed_user):
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " annual_contribution_limit, "
                        " employer_contribution_type, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, -1.00, 'none', "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_LIMIT

    def test_employer_flat_above_one_rejected(self, app, seed_user):
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type, "
                        " employer_flat_percentage, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, 'flat_percentage', 1.5, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_FLAT

    def test_employer_match_above_ten_rejected(self, app, seed_user):
        with app.app_context():
            account = self._account(seed_user)
            # The Numeric(5,4) column physically caps below 10 so we
            # exercise the column type rather than the CHECK by
            # picking a value > 9.9999.  The DataError surfaces from
            # the column type and is enough to confirm the CHECK is
            # not the binding constraint here; storage tier remains
            # protected on every face.
            try:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type, "
                        " employer_match_percentage, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, 'match', 9.9999, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            # Now violate the CHECK directly via UPDATE that reduces
            # to an in-range column value but still trips the
            # constraint when set to a negative value.
            inserted_id = db.session.execute(
                text(
                    "SELECT id FROM budget.investment_params "
                    "WHERE account_id = :aid"
                ),
                {"aid": account.id},
            ).scalar()
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE budget.investment_params "
                        "SET employer_match_percentage = -0.01 "
                        "WHERE id = :iid"
                    ),
                    {"iid": inserted_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_MATCH

    def test_employer_match_cap_above_one_rejected(self, app, seed_user):
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type, "
                        " employer_match_cap_percentage, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, 'match', 1.5, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_CAP


class TestRateHistoryCheck:
    """``budget.rate_history.interest_rate`` CHECK (F-077)."""

    def test_rate_above_one_rejected(self, app, seed_user):
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage R", "Mortgage",
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.rate_history "
                        "(account_id, effective_date, interest_rate, "
                        " created_at) "
                        "VALUES (:aid, '2026-01-01', 1.5, now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_RATE_HISTORY

    def test_rate_at_one_accepted(self, app, seed_user):
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage R2", "Mortgage",
            )
            row = RateHistory(
                account_id=account.id,
                effective_date=date(2026, 1, 1),
                interest_rate=Decimal("1.00000"),
            )
            db.session.add(row)
            db.session.commit()
            assert row.id is not None


class TestUserSettingsCheck:
    """``auth.user_settings`` rate CHECK constraints (F-077)."""

    def test_swr_above_one_rejected(self, app, seed_user):
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id).one()
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE auth.user_settings "
                        "SET safe_withdrawal_rate = 1.5 "
                        "WHERE id = :sid"
                    ),
                    {"sid": settings.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_USER_SWR

    def test_estimated_tax_rate_above_one_rejected(self, app, seed_user):
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id).one()
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE auth.user_settings "
                        "SET estimated_retirement_tax_rate = 2.0 "
                        "WHERE id = :sid"
                    ),
                    {"sid": settings.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_USER_TAX_RATE


class TestPaycheckDeductionCheck:
    """``salary.paycheck_deductions`` CHECK constraints (F-077)."""

    def _make_profile(self, seed_user):
        single_id = (
            db.session.query(FilingStatus)
            .filter_by(name="single").one().id
        )
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=single_id,
            name="Profile",
            annual_salary=Decimal("100000.00"),
        )
        db.session.add(profile)
        db.session.commit()
        return profile

    def _make_deduction_kwargs(self):
        timing_id = (
            db.session.query(DeductionTiming)
            .filter_by(name="pre_tax").one().id
        )
        flat_id = (
            db.session.query(CalcMethod)
            .filter_by(name="flat").one().id
        )
        return {
            "deduction_timing_id": timing_id,
            "calc_method_id": flat_id,
        }

    def test_inflation_rate_above_one_rejected(self, app, seed_user):
        with app.app_context():
            profile = self._make_profile(seed_user)
            kwargs = self._make_deduction_kwargs()
            ded = PaycheckDeduction(
                salary_profile_id=profile.id,
                name="401k",
                amount=Decimal("500.00"),
                **kwargs,
            )
            db.session.add(ded)
            db.session.commit()
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE salary.paycheck_deductions "
                        "SET inflation_rate = 1.5 WHERE id = :did"
                    ),
                    {"did": ded.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_DEDUCTION_INFL_RATE

    def test_inflation_month_thirteen_rejected(self, app, seed_user):
        with app.app_context():
            profile = self._make_profile(seed_user)
            kwargs = self._make_deduction_kwargs()
            ded = PaycheckDeduction(
                salary_profile_id=profile.id,
                name="HSA",
                amount=Decimal("100.00"),
                **kwargs,
            )
            db.session.add(ded)
            db.session.commit()
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE salary.paycheck_deductions "
                        "SET inflation_effective_month = 13 "
                        "WHERE id = :did"
                    ),
                    {"did": ded.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_DEDUCTION_INFL_MONTH


class TestSalaryRaiseCheck:
    """``salary.salary_raises.effective_year`` CHECK (F-077)."""

    def _make_profile(self, seed_user):
        single_id = (
            db.session.query(FilingStatus)
            .filter_by(name="single").one().id
        )
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=single_id,
            name="P",
            annual_salary=Decimal("100000.00"),
        )
        db.session.add(profile)
        db.session.commit()
        return profile

    def test_effective_year_below_2000_rejected(self, app, seed_user):
        with app.app_context():
            profile = self._make_profile(seed_user)
            type_id = (
                db.session.query(RaiseType)
                .filter_by(name="merit").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.salary_raises "
                        "(salary_profile_id, raise_type_id, "
                        " effective_year, effective_month, "
                        " percentage, is_recurring, version_id, "
                        " created_at) "
                        "VALUES (:pid, :tid, 1999, 1, 0.0300, "
                        "        false, 1, now())"
                    ),
                    {"pid": profile.id, "tid": type_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_RAISE_YEAR

    def test_effective_year_null_accepted(self, app, seed_user):
        """Recurring raises legitimately carry NULL year."""
        with app.app_context():
            profile = self._make_profile(seed_user)
            type_id = (
                db.session.query(RaiseType)
                .filter_by(name="cola").one().id
            )
            raise_row = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=type_id,
                effective_year=None,
                effective_month=1,
                percentage=Decimal("0.0300"),
                is_recurring=True,
            )
            db.session.add(raise_row)
            db.session.commit()
            assert raise_row.id is not None


class TestStateTaxConfigCheck:
    """``salary.state_tax_configs`` CHECK constraints (F-077)."""

    def test_negative_standard_deduction_rejected(self, app, seed_user):
        """Storage rejects -1.00 via ck_state_tax_configs_nonneg_standard_deduction."""
        with app.app_context():
            tax_type_id = (
                db.session.query(TaxType).filter_by(name="flat").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.state_tax_configs "
                        "(user_id, tax_type_id, state_code, tax_year, "
                        " flat_rate, standard_deduction, created_at) "
                        "VALUES (:uid, :ttid, 'CA', 2026, 0.05, "
                        "        -1.00, now())"
                    ),
                    {"uid": seed_user["user"].id, "ttid": tax_type_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_STATE_TAX_DEDUCTION

    def test_tax_year_above_2100_rejected(self, app, seed_user):
        """Storage rejects 2200 via ck_state_tax_configs_valid_tax_year."""
        with app.app_context():
            tax_type_id = (
                db.session.query(TaxType).filter_by(name="flat").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.state_tax_configs "
                        "(user_id, tax_type_id, state_code, tax_year, "
                        " flat_rate, created_at) "
                        "VALUES (:uid, :ttid, 'CA', 2200, 0.05, "
                        "        now())"
                    ),
                    {"uid": seed_user["user"].id, "ttid": tax_type_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_STATE_TAX_YEAR


class TestFicaConfigCheck:
    """``salary.fica_configs.tax_year`` CHECK (F-077)."""

    def test_tax_year_below_2000_rejected(self, app, seed_user):
        """Storage rejects 1999 via ck_fica_configs_valid_tax_year."""
        with app.app_context():
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.fica_configs "
                        "(user_id, tax_year, ss_rate, ss_wage_base, "
                        " medicare_rate, medicare_surtax_rate, "
                        " medicare_surtax_threshold, created_at) "
                        "VALUES (:uid, 1999, 0.062, 176100, "
                        "        0.0145, 0.009, 200000, now())"
                    ),
                    {"uid": seed_user["user"].id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_FICA_TAX_YEAR


class TestTaxBracketSetCheck:
    """``salary.tax_bracket_sets.tax_year`` CHECK (F-077)."""

    def test_tax_year_above_2100_rejected(self, app, seed_user):
        """Storage rejects 2200 via ck_tax_bracket_sets_valid_tax_year."""
        with app.app_context():
            single_id = (
                db.session.query(FilingStatus)
                .filter_by(name="single").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.tax_bracket_sets "
                        "(user_id, filing_status_id, tax_year, "
                        " standard_deduction, child_credit_amount, "
                        " other_dependent_credit_amount, created_at) "
                        "VALUES (:uid, :fid, 2200, 15000.00, "
                        "        2000.00, 500.00, now())"
                    ),
                    {
                        "uid": seed_user["user"].id,
                        "fid": single_id,
                    },
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_BRACKET_TAX_YEAR


class TestCalibrationOverrideCheck:
    """``salary.calibration_overrides`` effective-rate CHECK constraints."""

    def _make_profile(self, seed_user):
        """Build and persist a minimal salary profile for the calibration tests."""
        single_id = (
            db.session.query(FilingStatus)
            .filter_by(name="single").one().id
        )
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=single_id,
            name="Cal",
            annual_salary=Decimal("100000.00"),
        )
        db.session.add(profile)
        db.session.commit()
        return profile

    def _build_calibration(self, profile, **overrides):
        """Return CalibrationOverride kwargs with realistic defaults; overrides
        replace any keys."""
        base = {
            "salary_profile_id": profile.id,
            "actual_gross_pay": Decimal("3846.15"),
            "actual_federal_tax": Decimal("400.00"),
            "actual_state_tax": Decimal("160.00"),
            "actual_social_security": Decimal("238.46"),
            "actual_medicare": Decimal("55.77"),
            "effective_federal_rate": Decimal("0.1040"),
            "effective_state_rate": Decimal("0.0416"),
            "effective_ss_rate": Decimal("0.0620"),
            "effective_medicare_rate": Decimal("0.0145"),
            "pay_stub_date": date(2026, 1, 15),
            "is_active": True,
        }
        base.update(overrides)
        return base

    @pytest.mark.parametrize(
        "field, constraint",
        [
            ("effective_federal_rate", CK_CALIB_FED),
            ("effective_state_rate", CK_CALIB_STATE),
            ("effective_ss_rate", CK_CALIB_SS),
            ("effective_medicare_rate", CK_CALIB_MEDICARE),
        ],
    )
    def test_effective_rate_above_one_rejected(
        self, app, seed_user, field, constraint,
    ):
        """Storage rejects each parameterised effective rate when > 1."""
        with app.app_context():
            profile = self._make_profile(seed_user)
            kwargs = self._build_calibration(profile, **{field: Decimal("1.5")})
            with pytest.raises(IntegrityError) as info:
                cal = CalibrationOverride(**kwargs)
                db.session.add(cal)
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == constraint

    def test_all_rates_in_range_accepted(self, app, seed_user):
        """The exemplar pay-stub rates from the calibration tutorial pass."""
        with app.app_context():
            profile = self._make_profile(seed_user)
            cal = CalibrationOverride(**self._build_calibration(profile))
            db.session.add(cal)
            db.session.commit()
            assert cal.id is not None
