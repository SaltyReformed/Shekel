"""
Shekel Budget App -- Schema Validation Tests

Tests each Marshmallow schema's load() method directly for:
  - Required field enforcement (missing → ValidationError)
  - Type coercion (string amounts → Decimal)
  - Range validation (amount >= 0, month 1-12, etc.)
  - @pre_load empty-string stripping
  - @validates_schema cross-field rules
"""

from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.schemas.validation import (
    AccountCreateSchema,
    CategoryCreateSchema,
    DeductionCreateSchema,
    FicaConfigSchema,
    InlineTransactionCreateSchema,
    PayPeriodGenerateSchema,
    RaiseCreateSchema,
    SalaryProfileCreateSchema,
    SavingsGoalCreateSchema,
    SavingsGoalUpdateSchema,
    TemplateCreateSchema,
    TemplateUpdateSchema,
    TransactionCreateSchema,
    TransactionUpdateSchema,
    TransferCreateSchema,
    TransferTemplateCreateSchema,
    TransferUpdateSchema,
)


# ── TransactionCreateSchema ──────────────────────────────────────────


class TestTransactionCreateSchema:
    """Tests for TransactionCreateSchema."""

    def test_valid_data(self):
        """Valid data loads successfully with Decimal coercion."""
        data = TransactionCreateSchema().load({
            "name": "Groceries",
            "estimated_amount": "85.50",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert data["name"] == "Groceries"
        assert data["estimated_amount"] == Decimal("85.50")

    def test_missing_required_field(self):
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TransactionCreateSchema().load({
                "estimated_amount": "100.00",
                # Missing name, pay_period_id, scenario_id, etc.
            })
        assert "name" in exc.value.messages

    def test_negative_estimated_amount(self):
        """Negative estimated_amount fails Range validation."""
        with pytest.raises(ValidationError) as exc:
            TransactionCreateSchema().load({
                "name": "Bad",
                "estimated_amount": "-10.00",
                "pay_period_id": "1",
                "scenario_id": "1",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
            })
        assert "estimated_amount" in exc.value.messages


# ── TransactionUpdateSchema ──────────────────────────────────────────


class TestTransactionUpdateSchema:
    """Tests for TransactionUpdateSchema."""

    def test_empty_strings_stripped(self):
        """@pre_load strips empty strings -- empty update is valid."""
        data = TransactionUpdateSchema().load({
            "name": "",
            "estimated_amount": "",
        })
        # Both fields stripped; result is empty dict.
        assert "name" not in data
        assert "estimated_amount" not in data

    def test_valid_partial_update(self):
        """Partial update with valid fields loads correctly."""
        data = TransactionUpdateSchema().load({
            "estimated_amount": "200.00",
        })
        assert data["estimated_amount"] == Decimal("200.00")

    def test_invalid_amount_rejected(self):
        """Non-numeric estimated_amount is rejected."""
        with pytest.raises(ValidationError) as exc:
            TransactionUpdateSchema().load({
                "estimated_amount": "abc",
            })
        assert "estimated_amount" in exc.value.messages


# ── InlineTransactionCreateSchema ────────────────────────────────────


class TestInlineTransactionCreateSchema:
    """Tests for InlineTransactionCreateSchema."""

    def test_valid_data(self):
        """Valid inline data loads without name field."""
        data = InlineTransactionCreateSchema().load({
            "estimated_amount": "50.00",
            "category_id": "1",
            "pay_period_id": "1",
            "transaction_type_id": "1",
            "scenario_id": "1",
            "account_id": "1",
        })
        assert data["estimated_amount"] == Decimal("50.00")
        assert "name" not in data  # Name not required for inline.

    def test_missing_required_field(self):
        """Missing category_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            InlineTransactionCreateSchema().load({
                "estimated_amount": "50.00",
                # Missing category_id, pay_period_id, etc.
            })
        assert "category_id" in exc.value.messages


# ── TemplateCreateSchema ─────────────────────────────────────────────


class TestTemplateCreateSchema:
    """Tests for TemplateCreateSchema."""

    def test_valid_data(self):
        """Valid template data loads with all required fields."""
        data = TemplateCreateSchema().load({
            "name": "Monthly Rent",
            "default_amount": "1200.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert data["name"] == "Monthly Rent"
        assert data["default_amount"] == Decimal("1200.00")

    def test_missing_required_field(self):
        """Missing name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "default_amount": "100.00",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
            })
        assert "name" in exc.value.messages

    def test_invalid_recurrence_pattern(self):
        """Non-integer recurrence_pattern fails Integer field validation."""
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "name": "Test",
                "default_amount": "100.00",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
                "recurrence_pattern": "daily",  # Not a valid integer.
            })
        assert "recurrence_pattern" in exc.value.messages

    def test_day_of_month_range(self):
        """day_of_month outside 1-31 fails Range validation."""
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "name": "Test",
                "default_amount": "100.00",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
                "day_of_month": "0",
            })
        assert "day_of_month" in exc.value.messages

    def test_empty_strings_stripped(self):
        """@pre_load strips empty recurrence fields from HTML forms."""
        data = TemplateCreateSchema().load({
            "name": "Test",
            "default_amount": "100.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            "recurrence_pattern": "",
            "interval_n": "",
            "day_of_month": "",
        })
        # Empty strings stripped -- optional fields absent.
        assert "recurrence_pattern" not in data
        assert "interval_n" not in data


# ── TemplateUpdateSchema ─────────────────────────────────────────────


class TestTemplateUpdateSchema:
    """Tests for TemplateUpdateSchema."""

    def test_all_optional(self):
        """Empty update (after stripping) is valid."""
        data = TemplateUpdateSchema().load({
            "name": "",
            "default_amount": "",
        })
        assert "name" not in data
        assert "default_amount" not in data

    def test_effective_from_date_parsing(self):
        """effective_from parses a valid date string."""
        data = TemplateUpdateSchema().load({
            "effective_from": "2026-03-01",
        })
        from datetime import date
        assert data["effective_from"] == date(2026, 3, 1)

    def test_effective_from_invalid_date(self):
        """effective_from with invalid date raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TemplateUpdateSchema().load({
                "effective_from": "not-a-date",
            })
        assert "effective_from" in exc.value.messages


# ── TransferTemplateCreateSchema ─────────────────────────────────────


class TestTransferTemplateCreateSchema:
    """Tests for TransferTemplateCreateSchema."""

    def test_valid_data(self):
        """Valid transfer template data loads successfully."""
        data = TransferTemplateCreateSchema().load({
            "name": "Savings Transfer",
            "default_amount": "500.00",
            "from_account_id": "1",
            "to_account_id": "2",
            "category_id": "1",
        })
        assert data["default_amount"] == Decimal("500.00")

    def test_same_accounts_rejected(self):
        """from_account_id == to_account_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TransferTemplateCreateSchema().load({
                "name": "Bad Transfer",
                "default_amount": "100.00",
                "from_account_id": "1",
                "to_account_id": "1",
                "category_id": "1",
            })
        assert "_schema" in exc.value.messages

    def test_zero_amount_rejected(self):
        """default_amount=0 fails Range(min=0, min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            TransferTemplateCreateSchema().load({
                "name": "Zero Transfer",
                "default_amount": "0",
                "from_account_id": "1",
                "to_account_id": "2",
            })
        assert "default_amount" in exc.value.messages


# ── TransferCreateSchema ─────────────────────────────────────────────


class TestTransferCreateSchema:
    """Tests for TransferCreateSchema."""

    def test_valid_data(self):
        """Valid ad-hoc transfer data loads successfully."""
        data = TransferCreateSchema().load({
            "from_account_id": "1",
            "to_account_id": "2",
            "amount": "300.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
        })
        assert data["amount"] == Decimal("300.00")

    def test_same_accounts_rejected(self):
        """from_account_id == to_account_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load({
                "from_account_id": "1",
                "to_account_id": "1",
                "amount": "100.00",
                "pay_period_id": "1",
                "scenario_id": "1",
                "category_id": "1",
            })
        assert "_schema" in exc.value.messages


# ── TransferUpdateSchema ─────────────────────────────────────────────


class TestTransferUpdateSchema:
    """Tests for TransferUpdateSchema."""

    def test_valid_partial_update(self):
        """Partial update with amount loads correctly."""
        data = TransferUpdateSchema().load({
            "amount": "250.00",
        })
        assert data["amount"] == Decimal("250.00")

    def test_zero_amount_rejected(self):
        """amount=0 fails Range(min=0, min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            TransferUpdateSchema().load({
                "amount": "0",
            })
        assert "amount" in exc.value.messages


# ── SavingsGoalCreateSchema ──────────────────────────────────────────


class TestSavingsGoalCreateSchema:
    """Tests for SavingsGoalCreateSchema."""

    def test_valid_data(self):
        """Valid savings goal data loads successfully."""
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Emergency Fund",
            "target_amount": "10000.00",
        })
        assert data["target_amount"] == Decimal("10000.00")

    def test_zero_target_rejected(self):
        """target_amount=0 fails Range(min=0, min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Zero Goal",
                "target_amount": "0",
            })
        assert "target_amount" in exc.value.messages

    def test_missing_required_field(self):
        """Missing account_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "name": "No Account",
                "target_amount": "1000.00",
            })
        assert "account_id" in exc.value.messages


# ── SavingsGoalUpdateSchema ──────────────────────────────────────────


class TestSavingsGoalUpdateSchema:
    """Tests for SavingsGoalUpdateSchema."""

    def test_empty_strings_stripped(self):
        """@pre_load strips empty strings -- empty update is valid."""
        data = SavingsGoalUpdateSchema().load({
            "name": "",
            "target_amount": "",
        })
        assert "name" not in data
        assert "target_amount" not in data

    def test_valid_partial_update(self):
        """Partial update with is_active loads correctly."""
        data = SavingsGoalUpdateSchema().load({
            "is_active": "false",
        })
        assert data["is_active"] is False


# ── SalaryProfileCreateSchema ────────────────────────────────────────


class TestSalaryProfileCreateSchema:
    """Tests for SalaryProfileCreateSchema."""

    def test_valid_data(self):
        """Valid salary profile data loads with defaults."""
        data = SalaryProfileCreateSchema().load({
            "name": "My Salary",
            "annual_salary": "75000.00",
            "filing_status_id": "1",
            "state_code": "NC",
        })
        assert data["annual_salary"] == Decimal("75000.00")
        assert data["pay_periods_per_year"] == 26  # Default.

    def test_missing_required_field(self):
        """Missing annual_salary raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load({
                "name": "Bad Profile",
                "filing_status_id": "1",
                "state_code": "NC",
            })
        assert "annual_salary" in exc.value.messages

    def test_invalid_pay_periods_per_year(self):
        """pay_periods_per_year=10 fails OneOf validation."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load({
                "name": "Bad",
                "annual_salary": "75000.00",
                "filing_status_id": "1",
                "state_code": "NC",
                "pay_periods_per_year": "10",
            })
        assert "pay_periods_per_year" in exc.value.messages

    def test_state_code_length(self):
        """state_code must be exactly 2 characters."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load({
                "name": "Bad",
                "annual_salary": "75000.00",
                "filing_status_id": "1",
                "state_code": "NCC",  # 3 chars, max is 2.
            })
        assert "state_code" in exc.value.messages


# ── RaiseCreateSchema ────────────────────────────────────────────────


class TestRaiseCreateSchema:
    """Tests for RaiseCreateSchema."""

    def test_valid_with_percentage(self):
        """Valid raise with percentage loads successfully."""
        data = RaiseCreateSchema().load({
            "raise_type_id": "1",
            "effective_month": "3",
            "effective_year": "2026",
            "percentage": "3.50",
        })
        assert data["percentage"] == Decimal("3.50")

    def test_valid_with_flat_amount(self):
        """Valid raise with flat_amount loads successfully."""
        data = RaiseCreateSchema().load({
            "raise_type_id": "1",
            "effective_month": "1",
            "effective_year": "2026",
            "flat_amount": "5000.00",
        })
        assert data["flat_amount"] == Decimal("5000.00")

    def test_both_percentage_and_flat_rejected(self):
        """Both percentage and flat_amount raises cross-field error."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "3",
                "effective_year": "2026",
                "percentage": "3",
                "flat_amount": "5000.00",
            })
        assert "_schema" in exc.value.messages

    def test_neither_percentage_nor_flat_rejected(self):
        """Neither percentage nor flat_amount raises cross-field error."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "3",
                "effective_year": "2026",
            })
        assert "_schema" in exc.value.messages

    def test_missing_effective_year_rejected(self):
        """Raise without effective_year is rejected."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "3",
                "percentage": "3",
            })
        assert "effective_year" in exc.value.messages

    def test_month_out_of_range(self):
        """effective_month=13 fails Range(1-12) validation."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "13",
                "effective_year": "2026",
                "percentage": "3",
            })
        assert "effective_month" in exc.value.messages


# ── DeductionCreateSchema ────────────────────────────────────────────


class TestDeductionCreateSchema:
    """Tests for DeductionCreateSchema."""

    def test_valid_data(self):
        """Valid deduction data loads with defaults."""
        data = DeductionCreateSchema().load({
            "name": "401k",
            "deduction_timing_id": "1",
            "calc_method_id": "1",
            "amount": "250.0000",
        })
        assert data["amount"] == Decimal("250.0000")
        assert data["deductions_per_year"] == 26  # Default.

    def test_invalid_deductions_per_year(self):
        """deductions_per_year=52 fails OneOf validation."""
        with pytest.raises(ValidationError) as exc:
            DeductionCreateSchema().load({
                "name": "Bad",
                "deduction_timing_id": "1",
                "calc_method_id": "1",
                "amount": "100.0000",
                "deductions_per_year": "52",
            })
        assert "deductions_per_year" in exc.value.messages

    def test_missing_required_field(self):
        """Missing name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            DeductionCreateSchema().load({
                "deduction_timing_id": "1",
                "calc_method_id": "1",
                "amount": "100.0000",
            })
        assert "name" in exc.value.messages


# ── FicaConfigSchema ─────────────────────────────────────────────────


class TestFicaConfigSchema:
    """Tests for FicaConfigSchema."""

    def test_valid_data(self):
        """Valid FICA config data loads successfully."""
        data = FicaConfigSchema().load({
            "tax_year": "2026",
            "ss_rate": "6.20",
            "ss_wage_base": "176100.00",
            "medicare_rate": "1.45",
            "medicare_surtax_rate": "0.90",
            "medicare_surtax_threshold": "200000.00",
        })
        assert data["ss_rate"] == Decimal("6.20")
        assert data["tax_year"] == 2026

    def test_missing_required_field(self):
        """Missing ss_rate raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            FicaConfigSchema().load({
                "tax_year": "2026",
                # Missing all rate fields.
            })
        assert "ss_rate" in exc.value.messages


# ── AccountCreateSchema ──────────────────────────────────────────────


class TestAccountCreateSchema:
    """Tests for AccountCreateSchema."""

    def test_valid_data(self):
        """Valid account data loads successfully."""
        data = AccountCreateSchema().load({
            "name": "Checking",
            "account_type_id": "1",
            "anchor_balance": "5000.00",
        })
        assert data["name"] == "Checking"
        assert data["anchor_balance"] == Decimal("5000.00")

    def test_missing_required_field(self):
        """Missing name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            AccountCreateSchema().load({
                "account_type_id": "1",
            })
        assert "name" in exc.value.messages

    def test_empty_strings_stripped(self):
        """@pre_load strips empty optional fields."""
        data = AccountCreateSchema().load({
            "name": "Test",
            "account_type_id": "1",
            "anchor_balance": "",
        })
        assert "anchor_balance" not in data


# ── PayPeriodGenerateSchema ──────────────────────────────────────────


class TestPayPeriodGenerateSchema:
    """Tests for PayPeriodGenerateSchema."""

    def test_valid_data_with_defaults(self):
        """Valid data uses defaults for num_periods and cadence_days."""
        data = PayPeriodGenerateSchema().load({
            "start_date": "2026-03-01",
        })
        from datetime import date
        assert data["start_date"] == date(2026, 3, 1)
        assert data["num_periods"] == 52   # Default.
        assert data["cadence_days"] == 14  # Default.

    def test_num_periods_out_of_range(self):
        """num_periods=0 fails Range(1-260) validation."""
        with pytest.raises(ValidationError) as exc:
            PayPeriodGenerateSchema().load({
                "start_date": "2026-03-01",
                "num_periods": "0",
            })
        assert "num_periods" in exc.value.messages

    def test_cadence_days_out_of_range(self):
        """cadence_days=0 fails Range(1-365) validation."""
        with pytest.raises(ValidationError) as exc:
            PayPeriodGenerateSchema().load({
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })
        assert "cadence_days" in exc.value.messages

    def test_missing_start_date(self):
        """Missing start_date raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            PayPeriodGenerateSchema().load({
                "num_periods": "10",
            })
        assert "start_date" in exc.value.messages


# ── CategoryCreateSchema ─────────────────────────────────────────────


class TestCategoryCreateSchema:
    """Tests for CategoryCreateSchema."""

    def test_valid_data(self):
        """Valid category data loads with sort_order default."""
        data = CategoryCreateSchema().load({
            "group_name": "Auto",
            "item_name": "Car Payment",
        })
        assert data["group_name"] == "Auto"
        assert data["sort_order"] == 0  # Default.

    def test_missing_required_field(self):
        """Missing group_name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            CategoryCreateSchema().load({
                "item_name": "Rent",
            })
        assert "group_name" in exc.value.messages


# ── TransactionCreateSchemaBoundary ─────────────────────────────────


class TestTransactionCreateSchemaBoundary:
    """Boundary and edge-case tests for TransactionCreateSchema."""

    def test_excessive_decimal_places_coerced(self):
        """Decimal field with places=2 quantizes excessive decimal places.

        Marshmallow's Decimal field with places=2 rounds the input to 2
        decimal places on deserialization. A user typing '100.12345' must not
        store 3+ decimal places in a Numeric(12,2) column.
        """
        data = TransactionCreateSchema().load({
            "name": "Test",
            "estimated_amount": "100.12345",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        # 100.12345 rounded to 2 places → 100.12 (3rd decimal is 3 < 5, rounds down).
        assert data["estimated_amount"] == Decimal("100.12")

    def test_xss_in_name_field(self):
        """Schema accepts raw HTML in name field -- no sanitization at this layer.

        # Schema accepts raw HTML. XSS prevention relies on Jinja2 auto-escaping
        # in templates ({{ name }} auto-escapes by default). Verify template
        # escaping separately.
        """
        data = TransactionCreateSchema().load({
            "name": "<script>alert(1)</script>",
            "estimated_amount": "100.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert data["name"] == "<script>alert(1)</script>"

    def test_name_at_max_length(self):
        """A name of exactly 200 characters passes Length(max=200) validation."""
        long_name = "A" * 200
        data = TransactionCreateSchema().load({
            "name": long_name,
            "estimated_amount": "100.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert len(data["name"]) == 200

    def test_name_over_max_length(self):
        """A name of 201 characters fails Length(max=200) validation."""
        long_name = "A" * 201
        with pytest.raises(ValidationError) as exc:
            TransactionCreateSchema().load({
                "name": long_name,
                "estimated_amount": "100.00",
                "pay_period_id": "1",
                "scenario_id": "1",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
            })
        assert "name" in exc.value.messages


# ── TestTemplateCreateSchemaBoundary ────────────────────────────────


class TestTemplateCreateSchemaBoundary:
    """Boundary tests for TemplateCreateSchema recurrence fields."""

    def _valid_template_data(self, **overrides):
        """Return a valid template payload with optional overrides."""
        data = {
            "name": "Test Template",
            "default_amount": "100.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        }
        data.update(overrides)
        return data

    def test_day_of_month_zero_rejected(self):
        """day_of_month=0 fails Range(min=1, max=31) validation."""
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(day_of_month="0")
            )
        assert "day_of_month" in exc.value.messages

    def test_day_of_month_32_rejected(self):
        """day_of_month=32 fails Range(min=1, max=31) validation.

        No month has 32 days. The schema must reject this before it reaches
        the recurrence engine.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(day_of_month="32")
            )
        assert "day_of_month" in exc.value.messages

    def test_day_of_month_31_accepted(self):
        """day_of_month=31 is valid -- months with 31 days exist."""
        data = TemplateCreateSchema().load(
            self._valid_template_data(day_of_month="31")
        )
        assert data["day_of_month"] == 31

    def test_interval_n_zero_rejected(self):
        """interval_n=0 fails Range(min=1) validation.

        interval_n=0 would cause an infinite loop in the recurrence engine.
        The schema should catch it before the DB check constraint.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(interval_n="0")
            )
        assert "interval_n" in exc.value.messages


# ── TestTransferCreateSchemaBoundary ────────────────────────────────


class TestTransferCreateSchemaBoundary:
    """Boundary tests for TransferCreateSchema amount and account validation."""

    def _valid_transfer_data(self, **overrides):
        """Return a valid transfer payload with optional overrides."""
        data = {
            "from_account_id": "1",
            "to_account_id": "2",
            "amount": "100.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
        }
        data.update(overrides)
        return data

    def test_negative_amount_rejected(self):
        """Negative amount fails Range(min=0, min_inclusive=False) validation."""
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load(
                self._valid_transfer_data(amount="-100")
            )
        assert "amount" in exc.value.messages

    def test_zero_amount_rejected(self):
        """Zero amount fails Range(min=0, min_inclusive=False) -- must be > 0.

        Zero-amount transfers are meaningless and should be blocked at the
        schema level.
        """
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load(
                self._valid_transfer_data(amount="0")
            )
        assert "amount" in exc.value.messages

    def test_same_account_ids_rejected(self):
        """from_account_id == to_account_id raises @validates_schema error.

        The error message contains 'From and To accounts must be different.'
        """
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load(
                self._valid_transfer_data(from_account_id="5", to_account_id="5")
            )
        # Cross-field validation error goes to _schema key.
        assert "_schema" in exc.value.messages

    def test_very_small_positive_amount_accepted(self):
        """amount=0.01 (smallest valid transfer) passes validation.

        Boundary of the Range(min=0, min_inclusive=False) validator.
        """
        data = TransferCreateSchema().load(
            self._valid_transfer_data(amount="0.01")
        )
        assert data["amount"] == Decimal("0.01")


# ── TestSalaryProfileCreateSchemaBoundary ───────────────────────────


class TestSalaryProfileCreateSchemaBoundary:
    """Boundary tests for SalaryProfileCreateSchema state_code validation."""

    def _valid_salary_data(self, **overrides):
        """Return a valid salary profile payload with optional overrides."""
        data = {
            "name": "Test Profile",
            "annual_salary": "75000.00",
            "filing_status_id": "1",
            "state_code": "NC",
        }
        data.update(overrides)
        return data

    def test_empty_state_code_rejected(self):
        """Empty state_code is stripped by @pre_load, then fails as missing required field.

        An empty state code would break the tax calculator lookup.
        """
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(
                self._valid_salary_data(state_code="")
            )
        assert "state_code" in exc.value.messages

    def test_single_char_state_code_rejected(self):
        """Single-character state_code fails Length(min=2, max=2) validation."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(
                self._valid_salary_data(state_code="N")
            )
        assert "state_code" in exc.value.messages

    def test_lowercase_state_code_accepted(self):
        """Lowercase state_code is accepted -- no uppercase normalization in schema.

        # Schema accepts lowercase state codes. Normalization to uppercase
        # (if needed) must happen at the route or service level.
        """
        data = SalaryProfileCreateSchema().load(
            self._valid_salary_data(state_code="nc")
        )
        assert data["state_code"] == "nc"


# ── TestFicaConfigSchemaBoundary ────────────────────────────────────


class TestFicaConfigSchemaBoundary:
    """Boundary tests for FicaConfigSchema rate validation gaps."""

    def _valid_fica_data(self, **overrides):
        """Return a valid FICA config payload with optional overrides."""
        data = {
            "tax_year": "2026",
            "ss_rate": "6.20",
            "ss_wage_base": "176100.00",
            "medicare_rate": "1.45",
            "medicare_surtax_rate": "0.90",
            "medicare_surtax_threshold": "200000.00",
        }
        data.update(overrides)
        return data

    def test_fica_rate_over_100_rejected(self):
        """ss_rate=200 (200%) is rejected by Range(min=0, max=100) validator."""
        with pytest.raises(ValidationError) as exc:
            FicaConfigSchema().load(
                self._valid_fica_data(ss_rate="200")
            )
        assert "ss_rate" in exc.value.messages

    def test_negative_fica_rate_rejected(self):
        """Negative ss_rate is rejected by Range(min=0, max=100) validator."""
        with pytest.raises(ValidationError) as exc:
            FicaConfigSchema().load(
                self._valid_fica_data(ss_rate="-5")
            )
        assert "ss_rate" in exc.value.messages

    def test_zero_wage_base_rejected(self):
        """ss_wage_base=0 is rejected by Range(min=0, min_inclusive=False).

        Wage base must be positive. Matches the database CHECK constraint
        ``ss_wage_base > 0``.
        """
        with pytest.raises(ValidationError) as exc:
            FicaConfigSchema().load(
                self._valid_fica_data(ss_wage_base="0")
            )
        assert "ss_wage_base" in exc.value.messages

    def test_rate_at_zero_accepted(self):
        """ss_rate=0 is accepted -- inclusive lower bound of Range(min=0, max=100)."""
        data = FicaConfigSchema().load(
            self._valid_fica_data(ss_rate="0.00")
        )
        assert data["ss_rate"] == Decimal("0.00")

    def test_rate_at_100_accepted(self):
        """ss_rate=100 is accepted -- inclusive upper bound of Range(min=0, max=100)."""
        data = FicaConfigSchema().load(
            self._valid_fica_data(ss_rate="100.00")
        )
        assert data["ss_rate"] == Decimal("100.00")

    def test_wage_base_minimum_accepted(self):
        """ss_wage_base=0.01 is accepted -- smallest valid value (> 0)."""
        data = FicaConfigSchema().load(
            self._valid_fica_data(ss_wage_base="0.01")
        )
        assert data["ss_wage_base"] == Decimal("0.01")

    def test_threshold_minimum_accepted(self):
        """medicare_surtax_threshold=0.01 is accepted -- smallest valid value (> 0)."""
        data = FicaConfigSchema().load(
            self._valid_fica_data(medicare_surtax_threshold="0.01")
        )
        assert data["medicare_surtax_threshold"] == Decimal("0.01")


# ── TestCategoryCreateSchemaBoundary ────────────────────────────────


class TestCategoryCreateSchemaBoundary:
    """Boundary tests for CategoryCreateSchema field length limits."""

    def test_group_name_at_max_length(self):
        """group_name of exactly 100 characters passes Length(max=100) validation."""
        long_name = "G" * 100
        data = CategoryCreateSchema().load({
            "group_name": long_name,
            "item_name": "Test",
        })
        assert len(data["group_name"]) == 100

    def test_group_name_over_max_length(self):
        """group_name of 101 characters fails Length(max=100) validation."""
        long_name = "G" * 101
        with pytest.raises(ValidationError) as exc:
            CategoryCreateSchema().load({
                "group_name": long_name,
                "item_name": "Test",
            })
        assert "group_name" in exc.value.messages

    def test_xss_in_group_name(self):
        """Schema accepts raw HTML in group_name -- no sanitization at this layer.

        # Schema accepts raw HTML. XSS prevention relies on Jinja2 auto-escaping
        # in templates ({{ name }} auto-escapes by default). Verify template
        # escaping separately.
        """
        data = CategoryCreateSchema().load({
            "group_name": "<img src=x onerror=alert(1)>",
            "item_name": "Test",
        })
        assert data["group_name"] == "<img src=x onerror=alert(1)>"


# ── Audit Remediation Range Validation Tests (H-06, M-14) ──────────


class TestAnnualSalaryRange:
    """SalaryProfileCreateSchema rejects zero and negative annual_salary (H-06)."""

    def _base(self, **overrides):
        data = {
            "name": "Test", "annual_salary": "75000.00",
            "filing_status_id": "1", "state_code": "NC",
        }
        data.update(overrides)
        return data

    def test_zero_salary_rejected(self):
        """annual_salary=0 is rejected (min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(self._base(annual_salary="0"))
        assert "annual_salary" in exc.value.messages

    def test_negative_salary_rejected(self):
        """Negative annual_salary is rejected."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(self._base(annual_salary="-50000"))
        assert "annual_salary" in exc.value.messages

    def test_positive_salary_accepted(self):
        """Valid positive annual_salary passes."""
        data = SalaryProfileCreateSchema().load(self._base(annual_salary="1.00"))
        assert data["annual_salary"] == Decimal("1.00")


class TestRaiseRangeValidation:
    """RaiseCreateSchema rejects out-of-range years and amounts (M-14)."""

    def _base(self, **overrides):
        data = {
            "raise_type_id": "1", "effective_month": "7",
            "effective_year": "2026", "percentage": "3.00",
        }
        data.update(overrides)
        return data

    def test_year_zero_rejected(self):
        """effective_year=0 is rejected (min=2000)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(effective_year="0"))
        assert "effective_year" in exc.value.messages

    def test_year_far_future_rejected(self):
        """effective_year=999999 is rejected (max=2100)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(effective_year="999999"))
        assert "effective_year" in exc.value.messages

    def test_valid_year_accepted(self):
        """effective_year=2026 passes."""
        data = RaiseCreateSchema().load(self._base())
        assert data["effective_year"] == 2026


class TestContributionPerPeriodRange:
    """SavingsGoalCreateSchema rejects negative contributions (M-14)."""

    def _base(self, **overrides):
        data = {
            "account_id": "1", "name": "Emergency Fund",
            "target_amount": "10000.00",
        }
        data.update(overrides)
        return data

    def test_negative_contribution_rejected(self):
        """contribution_per_period=-100 is rejected (min=0)."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load(
                self._base(contribution_per_period="-100.00")
            )
        assert "contribution_per_period" in exc.value.messages

    def test_zero_contribution_accepted(self):
        """contribution_per_period=0 is accepted."""
        data = SavingsGoalCreateSchema().load(
            self._base(contribution_per_period="0.00")
        )
        assert data["contribution_per_period"] == Decimal("0.00")
