"""
Shekel Budget App -- Marshmallow Validation Schemas

Validates and deserializes incoming request data.  Used by routes
to keep controllers thin and push validation logic out of Flask.
"""

from marshmallow import Schema, fields, pre_load, validate, validates_schema, ValidationError, EXCLUDE


class BaseSchema(Schema):
    """Base schema that strips CSRF tokens from form submissions."""

    class Meta:
        unknown = EXCLUDE


class TransactionUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))
    status_id = fields.Integer()
    pay_period_id = fields.Integer()
    category_id = fields.Integer()
    notes = fields.String(allow_none=True)


class TransactionCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transaction."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))
    account_id = fields.Integer(required=True)
    pay_period_id = fields.Integer(required=True)
    scenario_id = fields.Integer(required=True)
    category_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    status_id = fields.Integer()
    notes = fields.String(allow_none=True)


class InlineTransactionCreateSchema(BaseSchema):
    """Validates POST data for inline transaction creation from the grid.

    Unlike TransactionCreateSchema, the name field is auto-derived from
    the category so it is not required from the user.
    """

    estimated_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))
    account_id = fields.Integer(required=True)
    category_id = fields.Integer(required=True)
    pay_period_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    scenario_id = fields.Integer(required=True)
    status_id = fields.Integer()
    notes = fields.String(allow_none=True)

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}


class TemplateCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction template."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so Marshmallow treats them as missing.

        HTML forms always submit every <input> element, even hidden ones,
        as empty strings.  Without this hook, those empty strings fail
        OneOf / Integer validation on optional fields.
        """
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    category_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    account_id = fields.Integer(required=True)

    # Recurrence rule fields (optional -- omit for one-time / manual).
    # The value is the integer primary key of a ref.recurrence_patterns row,
    # submitted as a string via HTML form data.  Route-level code validates
    # existence via db.session.get().
    recurrence_pattern = fields.Integer(validate=validate.Range(min=1))
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = fields.Integer()
    end_date = fields.Date(allow_none=True)


class TemplateUpdateSchema(TemplateCreateSchema):
    """Validates PUT data for updating a template.

    All fields optional (partial update), plus an effective date for
    recurrence regeneration.
    """

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    category_id = fields.Integer()
    transaction_type_id = fields.Integer()
    account_id = fields.Integer()

    # Date from which regeneration takes effect.
    effective_from = fields.Date()


class AnchorUpdateSchema(BaseSchema):
    """Validates PATCH data for updating the account anchor balance."""

    anchor_balance = fields.Decimal(required=True, places=2, as_string=True)


class PayPeriodGenerateSchema(BaseSchema):
    """Validates POST data for generating pay periods."""

    start_date = fields.Date(required=True)
    num_periods = fields.Integer(
        load_default=52, validate=validate.Range(min=1, max=260)
    )
    cadence_days = fields.Integer(
        load_default=14, validate=validate.Range(min=1, max=365)
    )


class CategoryCreateSchema(BaseSchema):
    """Validates POST data for creating a category."""

    group_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    item_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    sort_order = fields.Integer(load_default=0)


# ── Salary / Paycheck Schemas (Phase 2) ───────────────────────────


class SalaryProfileCreateSchema(BaseSchema):
    """Validates POST data for creating a salary profile."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    annual_salary = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    filing_status_id = fields.Integer(required=True)
    state_code = fields.String(
        required=True, validate=validate.Length(min=2, max=2)
    )
    pay_periods_per_year = fields.Integer(
        load_default=26, validate=validate.OneOf([12, 24, 26, 52])
    )

    # W-4 fields (IRS Pub 15-T)
    qualifying_children = fields.Integer(
        load_default=0, validate=validate.Range(min=0)
    )
    other_dependents = fields.Integer(
        load_default=0, validate=validate.Range(min=0)
    )
    additional_income = fields.Decimal(
        load_default="0", places=2, as_string=True
    )
    additional_deductions = fields.Decimal(
        load_default="0", places=2, as_string=True
    )
    extra_withholding = fields.Decimal(
        load_default="0", places=2, as_string=True
    )


class SalaryProfileUpdateSchema(BaseSchema):
    """Validates POST data for updating a salary profile."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(validate=validate.Length(min=1, max=200))
    annual_salary = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    filing_status_id = fields.Integer()
    state_code = fields.String(validate=validate.Length(min=2, max=2))
    pay_periods_per_year = fields.Integer(
        validate=validate.OneOf([12, 24, 26, 52])
    )

    # W-4 fields (IRS Pub 15-T)
    qualifying_children = fields.Integer(validate=validate.Range(min=0))
    other_dependents = fields.Integer(validate=validate.Range(min=0))
    additional_income = fields.Decimal(places=2, as_string=True)
    additional_deductions = fields.Decimal(places=2, as_string=True)
    extra_withholding = fields.Decimal(places=2, as_string=True)


class RaiseCreateSchema(BaseSchema):
    """Validates POST data for adding a salary raise."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    raise_type_id = fields.Integer(required=True)
    effective_month = fields.Integer(
        required=True, validate=validate.Range(min=1, max=12)
    )
    effective_year = fields.Integer(
        required=True, validate=validate.Range(min=2000, max=2100),
    )
    percentage = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=-100, max=1000),
    )
    flat_amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=-10000000, max=10000000),
    )
    is_recurring = fields.Boolean(load_default=False)
    notes = fields.String(allow_none=True)

    @validates_schema
    def validate_one_method(self, data, **kwargs):
        has_pct = data.get("percentage") is not None
        has_flat = data.get("flat_amount") is not None
        if has_pct == has_flat:
            raise ValidationError(
                "Specify exactly one of percentage or flat_amount."
            )


class DeductionCreateSchema(BaseSchema):
    """Validates POST data for adding a paycheck deduction."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    deduction_timing_id = fields.Integer(required=True)
    calc_method_id = fields.Integer(required=True)
    amount = fields.Decimal(required=True, places=4, as_string=True)
    deductions_per_year = fields.Integer(
        load_default=26, validate=validate.OneOf([12, 24, 26])
    )
    annual_cap = fields.Decimal(places=2, as_string=True, allow_none=True)
    inflation_enabled = fields.Boolean(load_default=False)
    inflation_rate = fields.Decimal(places=4, as_string=True, allow_none=True)
    inflation_effective_month = fields.Integer(
        validate=validate.Range(min=1, max=12), allow_none=True
    )
    target_account_id = fields.Integer(allow_none=True)


class TaxBracketSetSchema(BaseSchema):
    """Validates POST data for updating a tax bracket set."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    filing_status_id = fields.Integer(required=True)
    tax_year = fields.Integer(required=True)
    standard_deduction = fields.Decimal(required=True, places=2, as_string=True)
    child_credit_amount = fields.Decimal(
        load_default="0", places=2, as_string=True
    )
    other_dependent_credit_amount = fields.Decimal(
        load_default="0", places=2, as_string=True
    )


class FicaConfigSchema(BaseSchema):
    """Validates POST data for updating FICA configuration."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    tax_year = fields.Integer(required=True)
    ss_rate = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    ss_wage_base = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    medicare_rate = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    medicare_surtax_rate = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    medicare_surtax_threshold = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )


# ── Transfer Schemas (Phase 4) ────────────────────────────────────


class TransferTemplateCreateSchema(BaseSchema):
    """Validates POST data for creating a transfer template."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        required=True, places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    from_account_id = fields.Integer(required=True)
    to_account_id = fields.Integer(required=True)
    category_id = fields.Integer(load_default=None, allow_none=True)

    # Recurrence rule fields (optional -- omit for one-time / manual).
    # The value is the integer primary key of a ref.recurrence_patterns row,
    # submitted as a string via HTML form data.  Route-level code validates
    # existence via db.session.get().
    recurrence_pattern = fields.Integer(validate=validate.Range(min=1))
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = fields.Integer()
    end_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        if data.get("from_account_id") and data.get("to_account_id"):
            if data["from_account_id"] == data["to_account_id"]:
                raise ValidationError("From and To accounts must be different.")


class TransferTemplateUpdateSchema(TransferTemplateCreateSchema):
    """Validates PUT data for updating a transfer template."""

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    from_account_id = fields.Integer()
    to_account_id = fields.Integer()
    category_id = fields.Integer(allow_none=True)

    # Date from which regeneration takes effect.
    effective_from = fields.Date()


class TransferCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transfer."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    from_account_id = fields.Integer(required=True)
    to_account_id = fields.Integer(required=True)
    amount = fields.Decimal(
        required=True, places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    pay_period_id = fields.Integer(required=True)
    scenario_id = fields.Integer(required=True)
    name = fields.String(validate=validate.Length(max=200))
    category_id = fields.Integer(load_default=None, allow_none=True)
    notes = fields.String(allow_none=True)

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        if data.get("from_account_id") and data.get("to_account_id"):
            if data["from_account_id"] == data["to_account_id"]:
                raise ValidationError("From and To accounts must be different.")


class TransferUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transfer (inline edit)."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    status_id = fields.Integer()
    name = fields.String(validate=validate.Length(max=200))
    category_id = fields.Integer(allow_none=True)
    notes = fields.String(allow_none=True)


# ── Savings Goal Schemas (Phase 4) ────────────────────────────────


class SavingsGoalCreateSchema(BaseSchema):
    """Validates POST data for creating a savings goal."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    account_id = fields.Integer(required=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    target_amount = fields.Decimal(
        required=True, places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    target_date = fields.Date()
    contribution_per_period = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0),
    )


class SavingsGoalUpdateSchema(BaseSchema):
    """Validates PUT data for updating a savings goal."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    account_id = fields.Integer()
    name = fields.String(validate=validate.Length(min=1, max=100))
    target_amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    target_date = fields.Date(allow_none=True)
    contribution_per_period = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    is_active = fields.Boolean()


# ── Account Schemas ────────────────────────────────────────────────


class AccountCreateSchema(BaseSchema):
    """Validates POST data for creating an account."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    account_type_id = fields.Integer(required=True)
    anchor_balance = fields.Decimal(places=2, as_string=True)


class AccountUpdateSchema(BaseSchema):
    """Validates POST data for updating an account."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(validate=validate.Length(min=1, max=100))
    account_type_id = fields.Integer()
    is_active = fields.Boolean()
    anchor_balance = fields.Decimal(places=2, as_string=True)


class AccountTypeCreateSchema(BaseSchema):
    """Validates POST data for creating an account type."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=30))


class AccountTypeUpdateSchema(BaseSchema):
    """Validates POST data for updating an account type."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=30))


# ── HYSA Schemas ──────────────────────────────────────────────────


class HysaParamsCreateSchema(BaseSchema):
    """Validates POST data for creating/updating HYSA parameters."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    apy = fields.Decimal(
        required=True, places=3, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    compounding_frequency = fields.String(
        required=True,
        validate=validate.OneOf(["daily", "monthly", "quarterly"]),
    )


class HysaParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating HYSA parameters."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    apy = fields.Decimal(
        places=3, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    compounding_frequency = fields.String(
        validate=validate.OneOf(["daily", "monthly", "quarterly"]),
    )


# ── Loan Params Schemas (unified for all installment loan types) ──


class LoanParamsCreateSchema(BaseSchema):
    """Validates POST data for creating loan parameters.

    Universal max of 600 for term_months; type-specific limits are
    enforced by the route using ref.account_types.max_term_months.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    original_principal = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    current_principal = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    interest_rate = fields.Decimal(required=True, places=5, as_string=True, validate=validate.Range(min=0, max=100))
    term_months = fields.Integer(required=True, validate=validate.Range(min=1, max=600))
    origination_date = fields.Date(required=True)
    payment_day = fields.Integer(required=True, validate=validate.Range(min=1, max=31))
    is_arm = fields.Boolean(load_default=False)
    arm_first_adjustment_months = fields.Integer(allow_none=True)
    arm_adjustment_interval_months = fields.Integer(allow_none=True)


class LoanParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating loan parameters.

    All fields optional (partial update).  original_principal and
    origination_date are omitted -- not updatable after initial setup.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    current_principal = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    interest_rate = fields.Decimal(places=5, as_string=True, validate=validate.Range(min=0, max=100))
    term_months = fields.Integer(validate=validate.Range(min=1, max=600))
    payment_day = fields.Integer(validate=validate.Range(min=1, max=31))
    is_arm = fields.Boolean()
    arm_first_adjustment_months = fields.Integer(allow_none=True)
    arm_adjustment_interval_months = fields.Integer(allow_none=True)


class RateChangeSchema(BaseSchema):
    """Validates POST data for recording a variable-rate change."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    effective_date = fields.Date(required=True)
    interest_rate = fields.Decimal(required=True, places=5, as_string=True, validate=validate.Range(min=0, max=100))
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))


class EscrowComponentSchema(BaseSchema):
    """Validates POST data for creating/updating an escrow component."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    annual_amount = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
    inflation_rate = fields.Decimal(places=4, as_string=True, allow_none=True, validate=validate.Range(min=0, max=100))



class PayoffCalculatorSchema(BaseSchema):
    """Validates POST data for payoff scenario analysis."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    mode = fields.String(required=True, validate=validate.OneOf(["extra_payment", "target_date"]))
    extra_monthly = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    target_date = fields.Date()


# ── Investment Schemas (Phase 5) ────────────────────────────────


class InvestmentParamsCreateSchema(BaseSchema):
    """Validates POST data for creating investment parameters."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    assumed_annual_return = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=-1, max=1),
    )
    annual_contribution_limit = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    contribution_limit_year = fields.Integer(allow_none=True)
    employer_contribution_type = fields.String(
        load_default="none",
        validate=validate.OneOf(["none", "flat_percentage", "match"]),
    )
    employer_flat_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )
    employer_match_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=10),
    )
    employer_match_cap_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )


class InvestmentParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating investment parameters."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    assumed_annual_return = fields.Decimal(
        places=5, as_string=True,
        validate=validate.Range(min=-1, max=1),
    )
    annual_contribution_limit = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    contribution_limit_year = fields.Integer(allow_none=True)
    employer_contribution_type = fields.String(
        validate=validate.OneOf(["none", "flat_percentage", "match"]),
    )
    employer_flat_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )
    employer_match_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=10),
    )
    employer_match_cap_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )


# ── Pension Schemas (Phase 5) ──────────────────────────────────


class PensionProfileCreateSchema(BaseSchema):
    """Validates POST data for creating a pension profile."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    salary_profile_id = fields.Integer(allow_none=True)
    name = fields.String(
        required=True, validate=validate.Length(min=1, max=100)
    )
    benefit_multiplier = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    consecutive_high_years = fields.Integer(
        load_default=4, validate=validate.Range(min=1, max=10),
    )
    hire_date = fields.Date(required=True)
    earliest_retirement_date = fields.Date(allow_none=True)
    planned_retirement_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_pension_dates(self, data, **kwargs):
        """Cross-field date validation for pension profiles."""
        from datetime import date as date_type  # pylint: disable=import-outside-toplevel

        hire = data.get("hire_date")
        earliest = data.get("earliest_retirement_date")
        planned = data.get("planned_retirement_date")

        if earliest and hire and earliest <= hire:
            raise ValidationError(
                "Earliest retirement date must be after hire date.",
                field_name="earliest_retirement_date",
            )
        if planned and hire and planned <= hire:
            raise ValidationError(
                "Planned retirement date must be after hire date.",
                field_name="planned_retirement_date",
            )
        if planned and planned <= date_type.today():
            raise ValidationError(
                "Planned retirement date must be in the future.",
                field_name="planned_retirement_date",
            )
        if planned and earliest and planned < earliest:
            raise ValidationError(
                "Planned retirement date must be on or after "
                "earliest retirement date.",
                field_name="planned_retirement_date",
            )


class PensionProfileUpdateSchema(BaseSchema):
    """Validates POST data for updating a pension profile."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    salary_profile_id = fields.Integer(allow_none=True)
    name = fields.String(validate=validate.Length(min=1, max=100))
    benefit_multiplier = fields.Decimal(
        places=5, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    consecutive_high_years = fields.Integer(
        validate=validate.Range(min=1, max=10),
    )
    hire_date = fields.Date()
    earliest_retirement_date = fields.Date(allow_none=True)
    planned_retirement_date = fields.Date(allow_none=True)


# ── Retirement Settings Schema (Phase 5) ──────────────────────


class RetirementSettingsSchema(BaseSchema):
    """Validates POST data for updating retirement planning settings."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    safe_withdrawal_rate = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    planned_retirement_date = fields.Date(allow_none=True)
    estimated_retirement_tax_rate = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )


# ── Calibration Schema (Phase 3.10) ──────────────────────────────


class CalibrationSchema(BaseSchema):
    """Validates POST data for paycheck calibration from a real pay stub."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    actual_gross_pay = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    actual_federal_tax = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_state_tax = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_social_security = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_medicare = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    pay_stub_date = fields.Date(required=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))


class CalibrationConfirmSchema(BaseSchema):
    """Validates POST data for the calibration confirm step.

    Includes the original pay stub fields plus the derived effective
    rates passed via hidden form fields from the preview page.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    actual_gross_pay = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    actual_federal_tax = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_state_tax = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_social_security = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    actual_medicare = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    effective_federal_rate = fields.Decimal(
        required=True, places=10, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    effective_state_rate = fields.Decimal(
        required=True, places=10, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    effective_ss_rate = fields.Decimal(
        required=True, places=10, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    effective_medicare_rate = fields.Decimal(
        required=True, places=10, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    pay_stub_date = fields.Date(required=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
