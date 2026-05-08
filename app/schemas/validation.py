"""
Shekel Budget App -- Marshmallow Validation Schemas

Validates and deserializes incoming request data.  Used by routes
to keep controllers thin and push validation logic out of Flask.

Percent / decimal-rate convention
---------------------------------

Percentage rate fields (FICA, state flat rates, inflation, APY,
trend alert threshold, etc.) are stored as decimal fractions in the
database -- a 6.2% rate is persisted as ``Decimal("0.0620")`` in a
``Numeric(5, 4)`` or ``Numeric(7, 5)`` column with a database
CHECK constraint pinning the value to ``[0, 1]`` (and similar
ranges for the few "match-multiplier" cases).  The forms that
collect these values render the user-facing percent
(``0.0620 * 100 == 6.20``) and the routes divide by 100 (or call
``app.utils.formatting.pct_to_decimal``) before persistence.

The schemas in this module therefore validate the user-input
representation:

  - Schemas that run BEFORE the route's ``/100`` conversion accept
    the percent (e.g. ``Range(min=0, max=100)`` for a 0-100% rate).
  - Schemas that run AFTER the route's ``/100`` conversion (such as
    ``InvestmentParamsCreateSchema``, where the route's
    ``_convert_percentage_inputs`` helper rewrites the form payload
    in place before ``schema.load``) accept the decimal fraction
    (e.g. ``Range(min=0, max=1)`` for a 0-100% rate, or wider for
    multiplier semantics like 401(k) employer match).

The DB CHECK constraints added by commit C-24 of the 2026-04-15
security remediation plan are the storage-tier counterpart to these
validators; they are deliberately not redundant because they catch
raw-SQL bypasses of the route layer.  See
``migrations/versions/<C-24>_marshmallow_range_check_sweep.py`` for
the storage-tier bounds and keep both sides in sync when adding a
new column.

Monetary range validators
-------------------------

Pure monetary fields (deduction amount, SalaryProfile W-4 fields,
TaxBracketSet credits, etc.) get ``Range(min=...)`` validators per
commit C-24.  The minimum mirrors the database CHECK (``>= 0`` or
``> 0`` per column); the maximum is set well below the column's
storage limit but above any plausible real-world value, so a typo
that injects an extra digit is rejected with a clean field-level
400 instead of being silently committed.
"""

from decimal import Decimal

from marshmallow import Schema, fields, pre_load, validate, validates_schema, ValidationError, EXCLUDE


# ── Shared range validators (commit C-24) ─────────────────────────
#
# These constants centralise the percent-format and monetary range
# rules used across more than one schema below.  Validator instances
# are immutable for the parameter set they were constructed with, so
# a single shared instance per pattern is safe; if two fields need
# different bounds (e.g. raise percentage vs FICA rate), declare a
# second constant rather than mutating an existing one.

# Percent input that maps to a decimal fraction in storage: 0..100
# percent inclusive (e.g. user-entered "6.2" for a 6.2% rate, route
# divides by 100 before persistence).  Used by FICA, state flat-rate,
# loan interest, escrow inflation, default inflation, etc.
_PERCENT_INPUT_RANGE = validate.Range(
    min=Decimal("0"), max=Decimal("100"),
)

# Monetary range for W-4 / tax credit fields where the DB CHECK is
# ``>= 0``.  10,000,000 is generous: it accommodates very large W-4
# adjustments while still rejecting an obvious typo (extra digit) on
# a routine entry.  Columns are ``Numeric(12, 2)`` so the database
# can hold up to ~10B; this validator caps the schema layer well
# below that.
_NON_NEGATIVE_MONETARY = validate.Range(
    min=Decimal("0"), max=Decimal("10000000"),
)


class BaseSchema(Schema):
    """Base schema that strips CSRF tokens from form submissions."""

    class Meta:
        unknown = EXCLUDE


class TransactionUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the cell or popover was rendered.  The route handler
    compares the submitted value against ``Transaction.version_id``
    and short-circuits with 409 Conflict if they differ -- a stale-
    form check that catches the Tab-1/Tab-2 race even when the two
    requests are sequential rather than truly concurrent.  Optional
    so callers without a way to plumb the version through still
    pass validation; in that case only the SQLAlchemy
    ``version_id_col`` race detection applies, which catches the
    truly-concurrent case at flush time.  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

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
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    paid_at = fields.DateTime(allow_none=True, dump_only=True)
    version_id = fields.Integer(validate=validate.Range(min=1))


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
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)


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
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}


class TemplateCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction template.

    Includes a cross-field rule (``validate_envelope_only_on_expense``)
    that rejects ``is_envelope=True`` when ``transaction_type_id``
    refers to an income type.  Envelope rollover semantics (period-
    bounded amounts, leftover folds into the next period via
    ``Carry Fwd``) only apply to expense categories like groceries or
    spending money.  Income flows are settled via the
    ``Projected -> Received -> Settled`` workflow and the discrete
    carry-forward path; they have no rollover.
    """

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

    # Tracking & visibility flags.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    # Recurrence rule fields (optional -- omit for one-time / manual).
    # The value is the integer primary key of a ref.recurrence_patterns row,
    # submitted as a string via HTML form data.  Route-level code validates
    # existence via db.session.get().
    recurrence_pattern = fields.Integer(validate=validate.Range(min=1))
    interval_n = fields.Integer(validate=validate.Range(min=1))
    offset_periods = fields.Integer(validate=validate.Range(min=0))
    day_of_month = fields.Integer(validate=validate.Range(min=1, max=31))
    due_day_of_month = fields.Integer(
        validate=validate.Range(min=1, max=31), allow_none=True,
    )
    month_of_year = fields.Integer(validate=validate.Range(min=1, max=12))
    start_period_id = fields.Integer()
    end_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on income transaction templates.

        Envelope semantics (the source of truth for the carry-forward
        ``settle-and-roll`` branch -- see
        ``docs/carry-forward-aftermath-design.md``) only make sense for
        expense categories.  An income flow that arrives late is handled
        by the existing status workflow, not by rolling unspent funds
        into the next period.

        The validator runs only when both ``is_envelope`` and
        ``transaction_type_id`` are present in the deserialized payload.
        ``TemplateUpdateSchema`` partial updates that omit
        ``transaction_type_id`` skip the schema-level check; the route
        layer falls back to the existing template's stored
        ``transaction_type_id`` (see ``_is_tracking_on_non_expense`` in
        ``app/routes/templates.py``) so the rule is enforced end-to-end.

        The error is attached to the ``is_envelope`` field for
        consistency with other cross-field validators in this module
        (e.g. ``validate_goal_mode_fields``); the route layer surfaces
        the message to the user via ``flash``.

        Raises:
            ValidationError: If ``is_envelope`` is True and
                ``transaction_type_id`` resolves to the Income type.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel

        if not data.get("is_envelope"):
            return
        txn_type_id = data.get("transaction_type_id")
        if txn_type_id is None:
            return
        if ref_cache.transaction_type_is_income(txn_type_id):
            raise ValidationError(
                "Purchase tracking is only available for expense templates.",
                field_name="is_envelope",
            )


class TemplateUpdateSchema(TemplateCreateSchema):
    """Validates PUT data for updating a template.

    All fields optional (partial update), plus an effective date for
    recurrence regeneration.  Inherits the
    ``validate_envelope_only_on_expense`` cross-field rule from
    ``TemplateCreateSchema``; on partial updates that omit one of the
    two relevant fields, the validator returns early and the route
    layer applies the rule against the existing template's stored
    values.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    category_id = fields.Integer()
    transaction_type_id = fields.Integer()
    account_id = fields.Integer()

    # Date from which regeneration takes effect.
    effective_from = fields.Date()

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))


class AnchorUpdateSchema(BaseSchema):
    """Validates PATCH data for updating the account anchor balance.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the form was rendered.  The route handler compares
    the submitted value against ``Account.version_id`` and returns
    409 Conflict if they differ -- a stale-form check that catches
    the Tab-1/Tab-2 race even when the two requests are sequential
    rather than truly concurrent.  Optional so callers that have
    no way to plumb the version through (e.g. a future programmatic
    client) still pass validation; in that case only the
    SQLAlchemy ``version_id_col`` race detection applies, which
    catches the truly-concurrent case at flush time.
    """

    anchor_balance = fields.Decimal(required=True, places=2, as_string=True)
    version_id = fields.Integer(validate=validate.Range(min=1))


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


class CategoryEditSchema(BaseSchema):
    """Validates POST data for editing a category (rename / re-parent)."""

    group_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    item_name = fields.String(required=True, validate=validate.Length(min=1, max=100))


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
        load_default=0, validate=validate.Range(min=0, max=99),
    )
    other_dependents = fields.Integer(
        load_default=0, validate=validate.Range(min=0, max=99),
    )
    # F-074 / C-24: Added explicit Range(>= 0) to backstop the DB
    # CHECK (``additional_income >= 0``); the column is
    # ``Numeric(12, 2)`` and the upper bound is a generous form-
    # layer ceiling (see ``_NON_NEGATIVE_MONETARY``).
    additional_income = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # F-074 / C-24: Same as additional_income.  DB CHECK
    # ``additional_deductions >= 0``.
    additional_deductions = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # F-074 / C-24: Per-period extra withholding.  DB CHECK
    # ``extra_withholding >= 0``.
    extra_withholding = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )


class SalaryProfileUpdateSchema(BaseSchema):
    """Validates POST data for updating a salary profile.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

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
    qualifying_children = fields.Integer(
        validate=validate.Range(min=0, max=99),
    )
    other_dependents = fields.Integer(
        validate=validate.Range(min=0, max=99),
    )
    # F-074 / C-24: See :class:`SalaryProfileCreateSchema` for the
    # bound rationale; the same Range applies on update.
    additional_income = fields.Decimal(
        places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    additional_deductions = fields.Decimal(
        places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    extra_withholding = fields.Decimal(
        places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))


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
    # F-011 / C-24: Tightened from Range(-100, 1000) to a positive,
    # column-fitting bound.  The user enters percent (e.g. "3" for a
    # 3% raise); the route divides by 100 before persistence into
    # ``salary.salary_raises.percentage`` (``Numeric(5, 4)`` -- max
    # storable 9.9999 == 999.99% raise).  A zero or negative raise is
    # not a raise at all (the audit's "no pay cuts" policy -- pay
    # cuts are not modelled today; revisit if that changes).  200% is
    # the realistic upper: a single-event 3x salary jump is already
    # extraordinary, and any larger entry is a typo we want rejected
    # at the form rather than silently amplified by recurring-raise
    # compounding.  The DB CHECK ``percentage > 0`` is the storage-
    # tier counterpart.
    percentage = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(
            min=Decimal("0.01"), max=Decimal("200"),
        ),
    )
    # F-011 / C-24: Tightened from Range(-1e7, 1e7) to a positive,
    # column-fitting bound on a flat-dollar raise per period.
    # ``salary.salary_raises.flat_amount`` is ``Numeric(12, 2)``; the
    # DB CHECK ``flat_amount > 0`` rejects zero/negative.  $10M is
    # the schema-layer ceiling -- well above any realistic raise but
    # below the column limit, so an order-of-magnitude typo is
    # rejected with a clean 400 rather than being committed.
    flat_amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(
            min=Decimal("0.01"), max=Decimal("10000000"),
        ),
    )
    is_recurring = fields.Boolean(load_default=False)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    @validates_schema
    def validate_one_method(self, data, **kwargs):
        has_pct = data.get("percentage") is not None
        has_flat = data.get("flat_amount") is not None
        if has_pct == has_flat:
            raise ValidationError(
                "Specify exactly one of percentage or flat_amount."
            )


class RaiseUpdateSchema(RaiseCreateSchema):
    """Validates POST data for updating an existing salary raise.

    Inherits all required-field and cross-field rules from
    :class:`RaiseCreateSchema` (the salary edit form submits the
    full record on every save), and adds the optimistic-locking
    ``version_id`` pin; see :class:`TransactionUpdateSchema` for the
    contract.  Commit C-18 of the 2026-04-15 security remediation
    plan.
    """

    version_id = fields.Integer(validate=validate.Range(min=1))


class DeductionCreateSchema(BaseSchema):
    """Validates POST data for adding a paycheck deduction.

    The ``amount`` field carries dual semantics keyed off
    ``calc_method_id``:

      - ``CalcMethodEnum.FLAT`` -- the user enters a per-paycheck
        dollar amount (e.g. "500.00") that is persisted as-is in
        ``salary.paycheck_deductions.amount`` (``Numeric(12, 4)``).
      - ``CalcMethodEnum.PERCENTAGE`` -- the user enters a percent
        of gross pay (e.g. "6" for 6%); the route divides by 100
        before persistence so the storage value is the decimal
        fraction.

    The wide field-level ``Range`` accommodates the dollar case;
    the cross-field validator ``validate_amount_against_calc_method``
    additionally rejects implausibly large percent inputs (a 500%
    deduction is a typo, not a deduction) per F-012 / C-24.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    deduction_timing_id = fields.Integer(required=True)
    calc_method_id = fields.Integer(required=True)
    # F-012 / C-24: Added explicit positive Range to backstop the DB
    # CHECK (``amount > 0``).  Column is ``Numeric(12, 4)``; min
    # 0.0001 is the smallest representable positive value.  $1M cap
    # is generous (a single deduction line of $1M per paycheck is
    # already nonsense) but rejects the obvious "extra digit" typo
    # at the form rather than letting an IntegrityError come back as
    # a 500.  The percent-input ceiling (calc_method = PERCENTAGE)
    # is enforced separately by
    # ``validate_amount_against_calc_method`` because the
    # field-level Range cannot see ``calc_method_id``.
    amount = fields.Decimal(
        required=True, places=4, as_string=True,
        validate=validate.Range(
            min=Decimal("0.0001"), max=Decimal("1000000"),
        ),
    )
    deductions_per_year = fields.Integer(
        load_default=26, validate=validate.OneOf([12, 24, 26])
    )
    # F-012 / C-24: ``annual_cap`` is nullable in the model (NULL =
    # uncapped); when present, must be positive (DB CHECK
    # ``annual_cap IS NULL OR annual_cap > 0``).  Column is
    # ``Numeric(12, 2)``; $100M cap is far above the largest
    # realistic annual cap (HSA family limit ~$8K, 401(k) elective
    # ~$23K, 401(k) including employer ~$70K).
    annual_cap = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0.01"), max=Decimal("100000000"),
        ),
    )
    inflation_enabled = fields.Boolean(load_default=False)
    # F-077 / C-24: Schema validates the user-input percent (e.g.
    # ``3`` for a 3% annual escalation); the route divides by 100
    # before persistence into ``Numeric(5, 4)``.  DB CHECK pins
    # storage to ``[0, 1]``.
    inflation_rate = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=_PERCENT_INPUT_RANGE,
    )
    inflation_effective_month = fields.Integer(
        validate=validate.Range(min=1, max=12), allow_none=True
    )
    target_account_id = fields.Integer(allow_none=True)

    @validates_schema
    def validate_amount_against_calc_method(self, data, **kwargs):
        """Cap ``amount`` to a sane percent range when the calc method is PERCENTAGE.

        The field-level Range is wide enough to admit any plausible
        flat-dollar deduction; without this cross-field rule the
        same wide bound silently accepts a "500" entered against
        ``calc_method = PERCENTAGE`` (read as "500% of gross") and
        produces a deduction that drains every paycheck to zero.
        Cap percent inputs at 100% -- the realistic ceiling for a
        single deduction line -- and let
        ``CalcMethodEnum.FLAT`` keep the wider field-level bound.
        F-012 / C-24 of the 2026-04-15 security remediation plan.

        Raises:
            ValidationError: When ``calc_method_id`` resolves to
                ``PERCENTAGE`` and ``amount`` is greater than 100.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import CalcMethodEnum  # pylint: disable=import-outside-toplevel

        calc_method_id = data.get("calc_method_id")
        amount = data.get("amount")
        if calc_method_id is None or amount is None:
            return
        try:
            percentage_id = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
        except KeyError:
            # ref_cache not initialised -- the route layer will
            # surface the missing reference data; no extra check we
            # can do here.
            return
        if calc_method_id != percentage_id:
            return
        if amount > Decimal("100"):
            raise ValidationError(
                "Percentage deductions must be at most 100%.",
                field_name="amount",
            )


class DeductionUpdateSchema(DeductionCreateSchema):
    """Validates POST data for updating an existing paycheck deduction.

    Inherits the required-field rules and the
    ``validate_amount_against_calc_method`` cross-field rule from
    :class:`DeductionCreateSchema` (the salary edit form submits the
    full record on every save), and adds the optimistic-locking
    ``version_id`` pin; see :class:`TransactionUpdateSchema` for the
    contract.  Commit C-18 of the 2026-04-15 security remediation
    plan.
    """

    version_id = fields.Integer(validate=validate.Range(min=1))


class TaxBracketSetSchema(BaseSchema):
    """Validates POST data for updating a tax bracket set.

    F-075 / C-24: monetary fields gain ``Range(min=0)`` validators
    so the schema layer rejects negative entries before the DB
    CHECK (``standard_deduction >= 0`` etc.) raises an opaque
    IntegrityError.  ``tax_year`` is bounded to ``[2000, 2100]`` to
    match the storage CHECK introduced by C-24's migration.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    filing_status_id = fields.Integer(required=True)
    tax_year = fields.Integer(
        required=True, validate=validate.Range(min=2000, max=2100),
    )
    # F-075 / C-24: Added explicit ``Range(>= 0)`` to backstop DB
    # CHECK ``standard_deduction >= 0``.  The 2026 federal standard
    # deduction tops out around $32,200 (married jointly); $10M is
    # a wildly generous form-layer ceiling that still rejects an
    # extra-zero typo.
    standard_deduction = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # F-075 / C-24: DB CHECK ``child_credit_amount >= 0``.  The CTC
    # is $2,000 per child today; cap matches the form-layer
    # ceiling.
    child_credit_amount = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # F-075 / C-24: DB CHECK ``other_dependent_credit_amount >= 0``.
    other_dependent_credit_amount = fields.Decimal(
        load_default="0", places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )


class FicaConfigSchema(BaseSchema):
    """Validates POST data for updating FICA configuration.

    F-076 / C-24: ``tax_year`` bounded to ``[2000, 2100]`` to match
    the same-named bound on
    :class:`StateTaxConfigSchema`/:class:`TaxBracketSetSchema`; the
    rate fields keep their percent-input ``Range`` (the route
    divides by 100 before persistence into ``Numeric(5, 4)`` columns
    with DB CHECK ``rate >= 0 AND rate <= 1``).
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    tax_year = fields.Integer(
        required=True, validate=validate.Range(min=2000, max=2100),
    )
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


class StateTaxConfigSchema(BaseSchema):
    """Validates POST data for updating state tax configuration."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    state_code = fields.String(
        required=True, validate=validate.Length(min=2, max=2),
    )
    flat_rate = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    # F-077 / C-24: Backstop new DB CHECK
    # ``standard_deduction IS NULL OR standard_deduction >= 0``.
    standard_deduction = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    tax_year = fields.Integer(
        required=True, validate=validate.Range(min=2000, max=2100),
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
    category_id = fields.Integer(required=True)

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
    """Validates PUT data for updating a transfer template.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

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

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))


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
    category_id = fields.Integer(required=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        if data.get("from_account_id") and data.get("to_account_id"):
            if data["from_account_id"] == data["to_account_id"]:
                raise ValidationError("From and To accounts must be different.")


class TransferUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transfer (inline edit).

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    status_id = fields.Integer()
    name = fields.String(validate=validate.Length(max=200))
    category_id = fields.Integer(allow_none=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))


# ── Savings Goal Schemas (Phase 4) ────────────────────────────────


class SavingsGoalCreateSchema(BaseSchema):
    """Validates POST data for creating a savings goal.

    Supports two goal modes:

        Fixed (default): target_amount is required; income fields
        must be absent.

        Income-Relative: income_unit_id and income_multiplier are
        required; target_amount is optional (calculated on read).

    Cross-field rules are enforced in validate_goal_mode_fields().
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Remove empty-string form values so Marshmallow sees missing fields."""
        return {k: v for k, v in data.items() if v != ""}

    account_id = fields.Integer(required=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    target_amount = fields.Decimal(
        load_default=None, allow_none=True,
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    target_date = fields.Date()
    contribution_per_period = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    goal_mode_id = fields.Integer(load_default=1)
    income_unit_id = fields.Integer(load_default=None, allow_none=True)
    income_multiplier = fields.Decimal(
        load_default=None, allow_none=True,
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )

    @validates_schema
    def validate_goal_mode_fields(self, data, **kwargs):
        """Enforce cross-field constraints between goal mode and income fields."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        goal_mode_id = data.get("goal_mode_id", 1)
        income_unit_id = data.get("income_unit_id")
        income_multiplier = data.get("income_multiplier")
        target_amount = data.get("target_amount")

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        income_relative_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

        # Validate goal_mode_id is a known mode.
        if goal_mode_id not in (fixed_id, income_relative_id):
            raise ValidationError(
                "Invalid goal mode.", field_name="goal_mode_id",
            )

        if goal_mode_id == fixed_id:
            # Fixed mode: income fields must be absent.
            if income_unit_id is not None:
                raise ValidationError(
                    "Income unit must be empty for fixed-amount goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is not None:
                raise ValidationError(
                    "Income multiplier must be empty for fixed-amount goals.",
                    field_name="income_multiplier",
                )
            if target_amount is None:
                raise ValidationError(
                    "Target amount is required for fixed-amount goals.",
                    field_name="target_amount",
                )

        elif goal_mode_id == income_relative_id:
            # Income-relative mode: income fields are required.
            if income_unit_id is None:
                raise ValidationError(
                    "Income unit is required for income-relative goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is None:
                raise ValidationError(
                    "Income multiplier is required for income-relative goals.",
                    field_name="income_multiplier",
                )
            # Validate income_unit_id is a known unit.
            known_units = (
                ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS),
                ref_cache.income_unit_id(IncomeUnitEnum.MONTHS),
            )
            if income_unit_id not in known_units:
                raise ValidationError(
                    "Invalid income unit.", field_name="income_unit_id",
                )


class SavingsGoalUpdateSchema(BaseSchema):
    """Validates PUT data for updating a savings goal.

    Same cross-field rules as SavingsGoalCreateSchema.  The goal_mode_id
    defaults to None (not provided) for updates -- the cross-field
    validator only fires when goal_mode_id is explicitly included in
    the update payload.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Remove empty-string form values so Marshmallow sees missing fields."""
        return {k: v for k, v in data.items() if v != ""}

    account_id = fields.Integer()
    name = fields.String(validate=validate.Length(min=1, max=100))
    target_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    target_date = fields.Date(allow_none=True)
    contribution_per_period = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0),
    )
    is_active = fields.Boolean()
    goal_mode_id = fields.Integer()
    income_unit_id = fields.Integer(allow_none=True)
    income_multiplier = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))

    @validates_schema
    def validate_goal_mode_fields(self, data, **kwargs):
        """Enforce cross-field constraints between goal mode and income fields.

        Only validates when goal_mode_id is present in the update payload.
        Partial updates that omit goal_mode_id skip cross-field checks.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        goal_mode_id = data.get("goal_mode_id")
        if goal_mode_id is None:
            return

        income_unit_id = data.get("income_unit_id")
        income_multiplier = data.get("income_multiplier")
        target_amount = data.get("target_amount")

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        income_relative_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

        if goal_mode_id not in (fixed_id, income_relative_id):
            raise ValidationError(
                "Invalid goal mode.", field_name="goal_mode_id",
            )

        if goal_mode_id == fixed_id:
            if income_unit_id is not None:
                raise ValidationError(
                    "Income unit must be empty for fixed-amount goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is not None:
                raise ValidationError(
                    "Income multiplier must be empty for fixed-amount goals.",
                    field_name="income_multiplier",
                )
            if target_amount is None:
                raise ValidationError(
                    "Target amount is required for fixed-amount goals.",
                    field_name="target_amount",
                )

        elif goal_mode_id == income_relative_id:
            if income_unit_id is None:
                raise ValidationError(
                    "Income unit is required for income-relative goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is None:
                raise ValidationError(
                    "Income multiplier is required for income-relative goals.",
                    field_name="income_multiplier",
                )
            known_units = (
                ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS),
                ref_cache.income_unit_id(IncomeUnitEnum.MONTHS),
            )
            if income_unit_id not in known_units:
                raise ValidationError(
                    "Invalid income unit.", field_name="income_unit_id",
                )


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
    """Validates POST data for updating an account.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the edit form was rendered.  The handler compares
    the submitted value against the current ``Account.version_id``
    and short-circuits with 409 Conflict on mismatch; see the
    matching docstring on :class:`AnchorUpdateSchema`.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    name = fields.String(validate=validate.Length(min=1, max=100))
    account_type_id = fields.Integer()
    is_active = fields.Boolean()
    anchor_balance = fields.Decimal(places=2, as_string=True)
    version_id = fields.Integer(validate=validate.Range(min=1))


class AccountTypeCreateSchema(BaseSchema):
    """Validates POST data for creating an account type.

    Includes all metadata fields that drive dispatch logic.  Cross-field
    validation ensures flag combinations are consistent with the chosen
    category (e.g. has_amortization requires Liability).
    """

    name = fields.String(required=True, validate=validate.Length(min=1, max=30))
    category_id = fields.Integer(required=True)
    has_parameters = fields.Boolean(load_default=False)
    has_amortization = fields.Boolean(load_default=False)
    has_interest = fields.Boolean(load_default=False)
    is_pretax = fields.Boolean(load_default=False)
    is_liquid = fields.Boolean(load_default=False)
    icon_class = fields.String(
        load_default="bi-bank",
        validate=validate.Length(max=30),
    )
    max_term_months = fields.Integer(
        load_default=None, allow_none=True,
        validate=validate.Range(min=1, max=600),
    )

    @validates_schema
    def validate_flag_combinations(self, data, **kwargs):
        """Enforce category-flag consistency rules."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import AcctCategoryEnum  # pylint: disable=import-outside-toplevel

        cat_id = data.get("category_id")
        liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
        asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
        retirement_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)

        if data.get("has_amortization") and cat_id != liability_id:
            raise ValidationError(
                "has_amortization requires Liability category.",
                field_name="has_amortization",
            )
        if data.get("has_interest") and cat_id != asset_id:
            raise ValidationError(
                "has_interest requires Asset category.",
                field_name="has_interest",
            )
        if data.get("is_pretax") and cat_id != retirement_id:
            raise ValidationError(
                "is_pretax requires Retirement category.",
                field_name="is_pretax",
            )
        if data.get("is_liquid") and cat_id != asset_id:
            raise ValidationError(
                "is_liquid requires Asset category.",
                field_name="is_liquid",
            )
        if data.get("has_amortization") and data.get("has_interest"):
            raise ValidationError(
                "has_amortization and has_interest are mutually exclusive.",
                field_name="has_amortization",
            )
        if data.get("max_term_months") and not data.get("has_amortization"):
            raise ValidationError(
                "max_term_months requires has_amortization.",
                field_name="max_term_months",
            )


class AccountTypeUpdateSchema(BaseSchema):
    """Validates POST data for updating an account type.

    All fields are optional for partial updates.  Cross-field
    validation mirrors AccountTypeCreateSchema but only fires when
    the relevant fields are present in the submitted data.
    """

    name = fields.String(validate=validate.Length(min=1, max=30))
    category_id = fields.Integer()
    has_parameters = fields.Boolean()
    has_amortization = fields.Boolean()
    has_interest = fields.Boolean()
    is_pretax = fields.Boolean()
    is_liquid = fields.Boolean()
    icon_class = fields.String(validate=validate.Length(max=30))
    max_term_months = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1, max=600),
    )

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation.

        When *data* is a Werkzeug MultiDict (HTML form submission), take
        the last value for each key so the hidden-input + checkbox pattern
        resolves correctly: checked -> 'true' (last value wins), unchecked
        -> 'false' (sole value from hidden input).
        """
        if hasattr(data, "getlist"):
            return {
                k: vs[-1]
                for k in data
                if (vs := data.getlist(k)) and vs[-1] != ""
            }
        return {k: v for k, v in data.items() if v != ""}

    @validates_schema
    def validate_flag_combinations(self, data, **kwargs):
        """Enforce category-flag consistency rules on partial updates.

        Category-flag checks only fire when both the flag and
        category_id are present, so partial updates that omit
        category_id do not falsely reject.  Mutual-exclusion and
        dependency checks fire whenever both relevant fields are
        present.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import AcctCategoryEnum  # pylint: disable=import-outside-toplevel

        cat_id = data.get("category_id")

        if cat_id is not None:
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            retirement_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)

            if data.get("has_amortization") and cat_id != liability_id:
                raise ValidationError(
                    "has_amortization requires Liability category.",
                    field_name="has_amortization",
                )
            if data.get("has_interest") and cat_id != asset_id:
                raise ValidationError(
                    "has_interest requires Asset category.",
                    field_name="has_interest",
                )
            if data.get("is_pretax") and cat_id != retirement_id:
                raise ValidationError(
                    "is_pretax requires Retirement category.",
                    field_name="is_pretax",
                )
            if data.get("is_liquid") and cat_id != asset_id:
                raise ValidationError(
                    "is_liquid requires Asset category.",
                    field_name="is_liquid",
                )

        if data.get("has_amortization") and data.get("has_interest"):
            raise ValidationError(
                "has_amortization and has_interest are mutually exclusive.",
                field_name="has_amortization",
            )
        if data.get("max_term_months") and not data.get("has_amortization"):
            raise ValidationError(
                "max_term_months requires has_amortization.",
                field_name="max_term_months",
            )


# ── HYSA Schemas ──────────────────────────────────────────────────


class InterestParamsCreateSchema(BaseSchema):
    """Validates POST data for creating interest parameters."""

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


class InterestParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating interest parameters."""

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


class RefinanceSchema(BaseSchema):
    """Validates POST data for refinance what-if calculator input.

    The new_rate field accepts a percentage (e.g. 5.0 for 5%);
    the route converts to decimal (0.05) before passing to the engine.
    The new_principal field is optional -- when omitted, the route
    auto-calculates as current_real_principal + closing_costs.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields fall back to defaults."""
        return {k: v for k, v in data.items() if v != ""}

    new_rate = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    new_term_months = fields.Integer(
        required=True, validate=validate.Range(min=1, max=600),
    )
    closing_costs = fields.Decimal(
        load_default=Decimal("0.00"), places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    new_principal = fields.Decimal(
        load_default=None, allow_none=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )


class LoanPaymentTransferSchema(BaseSchema):
    """Validates POST data for creating a recurring loan payment transfer.

    The source_account_id is required (the account money comes from).
    The amount is optional -- if omitted, the route uses the computed
    monthly payment (P&I + escrow).  If provided, must be positive.
    """

    source_account_id = fields.Integer(required=True, validate=validate.Range(min=1))
    amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False))


class InvestmentContributionTransferSchema(BaseSchema):
    """Validates POST data for creating a recurring investment contribution transfer.

    The source_account_id is required (the account money comes from).
    The amount is optional -- if omitted, the route computes a suggested
    amount from the annual contribution limit and remaining pay periods.
    If provided, must be positive.
    """

    source_account_id = fields.Integer(
        required=True, validate=validate.Range(min=1),
    )
    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )


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
    # F-077 / C-24: Backstop the new DB CHECK
    # ``annual_contribution_limit IS NULL OR annual_contribution_limit >= 0``
    # with a wide form-layer ceiling.  Real limits top out at the
    # employer-plus-employee 401(k) cap (~$70K in 2026); $100M is a
    # generous typo guard.
    annual_contribution_limit = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("100000000"),
        ),
    )
    contribution_limit_year = fields.Integer(
        allow_none=True, validate=validate.Range(min=2000, max=2100),
    )
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
    # F-077 / C-24: see :class:`InvestmentParamsCreateSchema` for
    # the bound rationale; the same Range applies on update.
    annual_contribution_limit = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("100000000"),
        ),
    )
    contribution_limit_year = fields.Integer(
        allow_none=True, validate=validate.Range(min=2000, max=2100),
    )
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


class UserSettingsSchema(BaseSchema):
    """Validates POST data for updating user settings."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        cleaned = {}
        for k, v in data.items():
            if k == "default_grid_account_id" and v == "":
                cleaned[k] = None  # Empty string means "clear".
            elif v != "":
                cleaned[k] = v
        return cleaned

    grid_default_periods = fields.Integer(
        validate=validate.Range(min=1, max=52),
    )
    default_inflation_rate = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0, max=100),
    )
    low_balance_threshold = fields.Integer(
        validate=validate.Range(min=0),
    )
    large_transaction_threshold = fields.Integer(
        validate=validate.Range(min=0),
    )
    trend_alert_threshold = fields.Integer(
        validate=validate.Range(min=1, max=100),
    )
    anchor_staleness_days = fields.Integer(
        validate=validate.Range(min=1),
    )
    default_grid_account_id = fields.Integer(allow_none=True)


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


# ── Transaction Entry Schemas (Section 9) ────────────────────────


class EntryCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction entry.

    Requires amount (>= 0.01), description (1--200 chars), and
    entry_date.  is_credit defaults to False.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(
        required=True, validate=validate.Length(min=1, max=200),
    )
    entry_date = fields.Date(required=True)
    is_credit = fields.Boolean(load_default=False)


class EntryUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction entry.

    All fields optional for partial updates.  When present, the same
    validation rules as EntryCreateSchema apply.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty-string values so optional fields don't fail validation."""
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(validate=validate.Length(min=1, max=200))
    entry_date = fields.Date()
    is_credit = fields.Boolean()

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))


# --- Companion user management -------------------------------------------
#
# Email regex matches auth_service.register_user so the two code paths
# accept the same set of addresses.  The password byte limit matches
# bcrypt's hard 72-byte ceiling enforced by auth_service.hash_password.
# The minimum length matches change_password and register_user.

_COMPANION_EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_COMPANION_PASSWORD_MIN_LENGTH = 12
_COMPANION_PASSWORD_MAX_BYTES = 72


def _normalize_companion_form(data):
    """Strip whitespace and lowercase email for a companion form payload.

    Works with both Werkzeug ImmutableMultiDict (from request.form) and
    plain dicts.  Leaves password fields untouched because leading or
    trailing spaces may be intentional.  Missing keys are left missing
    so required-field validation produces the correct error.
    """
    cleaned = dict(data)
    if "email" in cleaned and isinstance(cleaned["email"], str):
        cleaned["email"] = cleaned["email"].strip().lower()
    if "display_name" in cleaned and isinstance(cleaned["display_name"], str):
        cleaned["display_name"] = cleaned["display_name"].strip()
    return cleaned


class CompanionCreateSchema(BaseSchema):
    """Validates POST data for creating a new companion user account.

    Required fields: email, display_name, password, password_confirm.
    Email is lowercased, stripped, and matched against a simple format
    regex.  Passwords must be at least 12 characters and at most 72
    UTF-8 bytes (bcrypt's ceiling), and password_confirm must match.

    Uniqueness of the email address is enforced by the calling route,
    not by the schema -- it needs a live database session.
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip whitespace and lowercase the email before field validation."""
        return _normalize_companion_form(data)

    email = fields.String(
        required=True,
        validate=[
            validate.Length(min=1, max=255),
            validate.Regexp(
                _COMPANION_EMAIL_REGEX, error="Invalid email format.",
            ),
        ],
    )
    display_name = fields.String(
        required=True,
        validate=validate.Length(min=1, max=100),
    )
    password = fields.String(
        required=True,
        validate=validate.Length(min=_COMPANION_PASSWORD_MIN_LENGTH),
    )
    password_confirm = fields.String(required=True)

    @validates_schema
    def validate_password_bytes(self, data, **kwargs):
        """Reject passwords longer than bcrypt's 72-byte UTF-8 limit."""
        password = data.get("password") or ""
        if len(password.encode("utf-8")) > _COMPANION_PASSWORD_MAX_BYTES:
            raise ValidationError(
                "Password must be at most "
                f"{_COMPANION_PASSWORD_MAX_BYTES} bytes.",
                "password",
            )

    @validates_schema
    def validate_password_match(self, data, **kwargs):
        """Require password_confirm to equal password."""
        if data.get("password") != data.get("password_confirm"):
            raise ValidationError(
                "Passwords do not match.", "password_confirm",
            )


class CompanionEditSchema(BaseSchema):
    """Validates POST data for editing an existing companion account.

    Email and display_name are required (same rules as the create
    schema).  Password fields are optional: blank means "keep the
    current password unchanged."  When a new password is supplied the
    same 12-character / 72-byte rules apply and password_confirm must
    match.  Email uniqueness is enforced by the calling route.
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip whitespace and lowercase the email before field validation."""
        return _normalize_companion_form(data)

    email = fields.String(
        required=True,
        validate=[
            validate.Length(min=1, max=255),
            validate.Regexp(
                _COMPANION_EMAIL_REGEX, error="Invalid email format.",
            ),
        ],
    )
    display_name = fields.String(
        required=True,
        validate=validate.Length(min=1, max=100),
    )
    password = fields.String(load_default="")
    password_confirm = fields.String(load_default="")

    @validates_schema
    def validate_password_change(self, data, **kwargs):
        """Validate the password fields only when a new password is given.

        Blank password fields mean "no change" and pass silently.  Any
        non-blank password must satisfy the same length rules as the
        create schema and match its confirmation.
        """
        password = data.get("password") or ""
        confirm = data.get("password_confirm") or ""
        if not password and not confirm:
            return
        if len(password) < _COMPANION_PASSWORD_MIN_LENGTH:
            raise ValidationError(
                "Password must be at least "
                f"{_COMPANION_PASSWORD_MIN_LENGTH} characters.",
                "password",
            )
        if len(password.encode("utf-8")) > _COMPANION_PASSWORD_MAX_BYTES:
            raise ValidationError(
                "Password must be at most "
                f"{_COMPANION_PASSWORD_MAX_BYTES} bytes.",
                "password",
            )
        if password != confirm:
            raise ValidationError(
                "Passwords do not match.", "password_confirm",
            )
