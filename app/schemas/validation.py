"""
Shekel Budget App — Marshmallow Validation Schemas

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

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(places=2, as_string=True)
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True)
    status_id = fields.Integer()
    pay_period_id = fields.Integer()
    category_id = fields.Integer()
    notes = fields.String(allow_none=True)


class TransactionCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transaction."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(required=True, places=2, as_string=True)
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True)
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

    estimated_amount = fields.Decimal(required=True, places=2, as_string=True)
    actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True)
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
    default_amount = fields.Decimal(required=True, places=2, as_string=True)
    category_id = fields.Integer(required=True)
    transaction_type_id = fields.Integer(required=True)
    account_id = fields.Integer(required=True)

    # Recurrence rule fields (optional — omit for one-time / manual).
    recurrence_pattern = fields.String(
        validate=validate.OneOf([
            "every_period", "every_n_periods", "monthly",
            "monthly_first", "quarterly", "semi_annual",
            "annual", "once",
        ])
    )
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = fields.Integer()


class TemplateUpdateSchema(TemplateCreateSchema):
    """Validates PUT data for updating a template.

    All fields optional (partial update), plus an effective date for
    recurrence regeneration.
    """

    # Override — all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(places=2, as_string=True)
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
    annual_salary = fields.Decimal(required=True, places=2, as_string=True)
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
    annual_salary = fields.Decimal(places=2, as_string=True)
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
    effective_year = fields.Integer()
    percentage = fields.Decimal(places=4, as_string=True)
    flat_amount = fields.Decimal(places=2, as_string=True)
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
    ss_rate = fields.Decimal(required=True, places=4, as_string=True)
    ss_wage_base = fields.Decimal(required=True, places=2, as_string=True)
    medicare_rate = fields.Decimal(required=True, places=4, as_string=True)
    medicare_surtax_rate = fields.Decimal(required=True, places=4, as_string=True)
    medicare_surtax_threshold = fields.Decimal(
        required=True, places=2, as_string=True
    )
