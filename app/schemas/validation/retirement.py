"""Pension-profile and retirement-settings validation schemas."""


from datetime import date
from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)


class PensionProfileCreateSchema(BaseSchema):
    """Validates POST data for creating a pension profile.

    E-28 / HIGH-06 / F-17 (Commit 12 of the follow-up plan):
    ``benefit_multiplier`` is persisted as a decimal fraction (e.g.
    ``Decimal("0.01850")`` for a 1.85% multiplier).  The ``@pre_load``
    converts the form's user-facing percent to its fraction
    equivalent so the schema's ``Range`` validator and the storage
    representation agree.
    """

    _PERCENT_FIELDS = ("benefit_multiplier",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

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
        if planned and planned <= date.today():
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
    """Validates POST data for updating a pension profile.

    Same fraction-domain convention as
    :class:`PensionProfileCreateSchema`.
    """

    _PERCENT_FIELDS = ("benefit_multiplier",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

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
    """Validates POST data for updating retirement planning settings.

    E-28 / HIGH-06 / F-17 (Commit 12 of the follow-up plan):
    ``safe_withdrawal_rate`` and ``estimated_retirement_tax_rate``
    are persisted as decimal fractions matching the
    ``user_settings`` DB CHECKs (``[0, 1]`` on both columns).  The
    ``@pre_load`` converts the form's user-facing percent (e.g.
    ``"4"`` for 4% SWR) to its fraction equivalent (``"0.04"``).
    """

    _PERCENT_FIELDS = (
        "safe_withdrawal_rate", "estimated_retirement_tax_rate",
    )

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    safe_withdrawal_rate = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    planned_retirement_date = fields.Date(allow_none=True)
    estimated_retirement_tax_rate = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )


class RetirementGapQuerySchema(BaseSchema):
    """Validates the /retirement/gap HTMX slider override query string.

    F-13: the slider's URL-editable ``swr`` parameter must reject
    negative values rather than letting the calculator silently
    collapse ``required_retirement_savings`` to zero.  Matches the
    Commit 24 / HIGH-06 convention: the schema owns the percent-to-
    fraction conversion via ``@pre_load`` so the route does no money
    math.  ``Range(min=0, max=1)`` on the stored fraction mirrors
    ``user_settings.safe_withdrawal_rate``'s CHECK constraint, so a
    URL-edited ``swr=-5`` is rejected at the schema with a 422 instead
    of silently zeroing the analysis.

    F-17 (Commit 12 of the follow-up plan): the ``return_rate``
    slider override is now routed through the same schema with the
    same percent-to-fraction conversion -- this collapses the last
    inline ``Decimal("100")`` division in the retirement route to
    complete the "schemas own percent conversion" convention.  The
    range mirrors ``investment_params.assumed_annual_return``'s
    ``(-1, 1]`` storage bound (returns may be negative in a loss
    scenario, but a -100% return is degenerate -- the growth engine's
    per-period rate would be -1; DH-#28 follow-up).
    """

    _PERCENT_FIELDS = ("swr", "return_rate")

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    swr = fields.Decimal(
        places=5, as_string=True, allow_none=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    return_rate = fields.Decimal(
        places=5, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("-1"), max=Decimal("1"), min_inclusive=False,
        ),
    )
