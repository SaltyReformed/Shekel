"""Salary, paycheck-deduction, tax-config, and calibration schemas."""


from datetime import datetime, timezone
from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates,
    validates_schema,
    ValidationError,
)

from app import ref_cache
from app.enums import CalcMethodEnum
from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _NON_NEGATIVE_MONETARY,
    _PERCENT_INPUT_RANGE,
    _normalize_empty_inputs,
)
from app.utils.dates import to_display_date


class SalaryProfileCreateSchema(BaseSchema):
    """Validates POST data for creating a salary profile."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    annual_salary = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    filing_status_id = RowId(required=True)
    state_code = fields.String(
        required=True, validate=validate.Length(min=2, max=2)
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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(validate=validate.Length(min=1, max=200))
    annual_salary = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    filing_status_id = RowId()
    state_code = fields.String(validate=validate.Length(min=2, max=2))
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
    version_id = RowId(validate=validate.Range(min=1))


class RaiseCreateSchema(BaseSchema):
    """Validates POST data for adding a salary raise."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    raise_type_id = RowId(required=True)
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
        """Require exactly one of ``percentage`` or ``flat_amount``.

        A raise is either a percentage bump or a flat per-period dollar
        amount, never both and never neither; the two columns are
        mutually exclusive in ``salary.salary_raises``.

        Raises:
            ValidationError: If both fields are set, or neither is.
        """
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

    version_id = RowId(validate=validate.Range(min=1))


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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    deduction_timing_id = RowId(required=True)
    calc_method_id = RowId(required=True)
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
            min=Decimal("0"), min_inclusive=False,
            max=Decimal("100000000"),
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
    target_account_id = RowId(allow_none=True)

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

    version_id = RowId(validate=validate.Range(min=1))


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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    filing_status_id = RowId(required=True)
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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

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
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

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


class CalibrationSchema(BaseSchema):
    """Validates POST data for paycheck calibration from a real pay stub."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

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

    HIGH-03 / Q-25 / E-20 (audit 2026-05-19): the FICA cross-check below
    enforces that the posted ``effective_ss_rate`` and
    ``effective_medicare_rate`` pair are arithmetically consistent with
    the posted ``actual_social_security`` / ``actual_medicare`` /
    ``actual_gross_pay`` triple within a one-cent equivalent tolerance.
    The federal and state divisor is the profile-derived taxable base
    (gross minus current pre-tax deductions), which is not available at
    the schema layer; the route performs the equivalent federal/state
    cross-check after computing taxable so the four-rate pair is fully
    pinned end-to-end.  The cross-check rejects tampered or stale
    two-step submissions whose stored rate would otherwise multiply
    against future per-period gross to produce silently wrong
    withholding -- the failure mode the audit documented under HIGH-03.
    """

    FICA_TOLERANCE = Decimal("0.01")

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

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

    @validates_schema
    def validate_fica_rate_consistency(self, data, **kwargs):
        """Cross-check posted FICA rate pair against posted actual_* pair.

        For each FICA line (Social Security, Medicare), the calibrated
        effective rate must satisfy ``rate * actual_gross_pay ==
        actual_<line>`` to within a one-cent absolute tolerance.  This
        is the schema-layer half of the E-20 immutable-snapshot
        invariant; the route layer performs the federal/state half once
        the profile-derived taxable base is available.

        The tolerance is one cent of expected withholding, which covers
        the worst-case rounding drift from storing the rate to
        ``Numeric(12, 10)`` (rate precision 1e-10 multiplied by a
        realistic biweekly gross of <= $10^4 is bounded by $10^{-6},
        three orders of magnitude under one cent).

        Raises:
            ValidationError: If either FICA rate is inconsistent with
                the corresponding actual_* pair.  The error is attached
                to the offending ``effective_*_rate`` key so the route
                layer's field-level handler surfaces the right input.
        """
        gross = data.get("actual_gross_pay")
        if gross is None:
            return

        errors: dict[str, list[str]] = {}

        ss_actual = data.get("actual_social_security")
        ss_rate = data.get("effective_ss_rate")
        if ss_actual is not None and ss_rate is not None:
            derived = ss_rate * gross
            if abs(derived - ss_actual) > self.FICA_TOLERANCE:
                errors["effective_ss_rate"] = [
                    f"effective_ss_rate {ss_rate} is inconsistent with "
                    f"actual_social_security {ss_actual} on gross "
                    f"{gross} (expected ~= {ss_actual / gross}, derived "
                    f"{derived})."
                ]

        medicare_actual = data.get("actual_medicare")
        medicare_rate = data.get("effective_medicare_rate")
        if medicare_actual is not None and medicare_rate is not None:
            derived = medicare_rate * gross
            if abs(derived - medicare_actual) > self.FICA_TOLERANCE:
                errors["effective_medicare_rate"] = [
                    f"effective_medicare_rate {medicare_rate} is "
                    f"inconsistent with actual_medicare {medicare_actual} "
                    f"on gross {gross} (expected ~= "
                    f"{medicare_actual / gross}, derived {derived})."
                ]

        if errors:
            raise ValidationError(errors)


class YtdTaxCheckpointSchema(BaseSchema):
    """Validates POST data for a YTD tax checkpoint (T-P2).

    The five year-to-date figures a user reads off a real pay stub plus
    the stub's ``as_of_date``.  Each figure is a non-negative
    ``Numeric(12, 2)`` (the schema-tier counterpart to the table's
    ``ck_ytd_tax_checkpoints_nonneg_*`` CHECKs), and the cross-field rule
    below rejects any withholding line larger than gross (the
    ``*_le_gross`` CHECKs' counterpart) -- a stub where federal exceeds
    gross is a data-entry typo, caught here as a clean field-level 400
    rather than an opaque IntegrityError.  ``as_of_date`` cannot be in the
    future (a stub for a date that has not happened yet is nonsense),
    bounded against the display-timezone civil day so the check follows
    the user's wall clock, not the server's UTC day (the timezone display
    policy).
    """

    # The four withholding lines, keyed by their form field name -> the
    # error surface for the ``<= gross`` cross-check below.
    _WITHHOLDING_FIELDS = (
        "ytd_federal",
        "ytd_state",
        "ytd_social_security",
        "ytd_medicare",
    )

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    as_of_date = fields.Date(required=True)
    ytd_gross = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_federal = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_state = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_social_security = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    ytd_medicare = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))

    @validates("as_of_date")
    def validate_not_future(self, value, **kwargs):
        """Reject an ``as_of_date`` after the display-timezone today.

        A pay stub can only measure withholding through a date that has
        already happened.  The bound is the user's wall-clock day
        (``America/New_York``), not the server's UTC day, so a late-evening
        Eastern entry near midnight is not spuriously rejected as "future"
        by a UTC clock that has already rolled over.

        Raises:
            ValidationError: When *value* is after the display-tz today.
        """
        today = to_display_date(datetime.now(timezone.utc))
        if value > today:
            raise ValidationError(
                f"as_of_date {value.isoformat()} is in the future "
                f"(after {today.isoformat()})."
            )

    @validates_schema
    def validate_components_le_gross(self, data, **kwargs):
        """Reject any withholding line that exceeds YTD gross.

        A federal / state / Social Security / Medicare figure larger than
        the gross it was withheld from is impossible -- a swapped or
        extra-digit typo.  The error is attached to the offending line so
        the route's field-level handler highlights the right input; the
        DB ``*_le_gross`` CHECKs are the storage-tier backstop for a
        raw-SQL bypass.

        Raises:
            ValidationError: When a withholding line exceeds ``ytd_gross``.
        """
        gross = data.get("ytd_gross")
        if gross is None:
            return
        errors: dict[str, list[str]] = {}
        for field_name in self._WITHHOLDING_FIELDS:
            value = data.get(field_name)
            if value is not None and value > gross:
                errors[field_name] = [
                    f"{field_name} {value} exceeds ytd_gross {gross}."
                ]
        if errors:
            raise ValidationError(errors)
