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

    Every field is optional, so the assumptions panel's per-field saves
    (P3a: one field per POST) and a multi-field submit validate through
    the same schema.  ``merit_raise_horizon_years`` is a plain year
    count (no percent conversion) whose 0-50 ``Range`` mirrors the DB
    CHECK ``ck_user_settings_valid_merit_horizon``; it is NOT
    ``allow_none`` -- the column is NOT NULL, so an empty submit means
    "not provided" (the key is dropped), never a NULL write.  There is
    deliberately NO field for an assumed annual return: its save
    semantics are an open developer question, so the panel's return row
    stays what-if-only.
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
    merit_raise_horizon_years = fields.Integer(
        validate=validate.Range(min=0, max=50),
    )


class RetirementReadinessQuerySchema(BaseSchema):
    """Validates the /retirement/readiness HTMX what-if query string (P3a).

    The assumption what-ifs: ``swr`` rejects values outside ``[0, 1]``
    (F-13: a URL-edited negative rate must 422, never silently zero the
    required-savings figure -- the bound the retired gap fragment's
    RetirementGapQuerySchema carried, folded in here when that fragment
    retired in P3c); ``return_rate`` mirrors
    ``investment_params.assumed_annual_return``'s ``(-1, 1]`` storage
    bound (F-17: the schema owns the percent-to-fraction conversion via
    ``@pre_load``, so the route does no money math);
    ``merit_raise_horizon_years`` is 0-50, mirroring the DB CHECK and the
    settings schema.  The two lever stepper values carry the same bounds
    as :class:`RetirementLeverQuerySchema` (``months`` capped at the P2b
    +180 search bound; ``contribution`` a money amount, so it is
    deliberately NOT in ``_PERCENT_FIELDS``).  All optional: an absent
    parameter means "stored value" (no what-if on that row).
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
    merit_raise_horizon_years = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=0, max=50),
    )
    months = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=0, max=180),
    )
    contribution = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("100000"),
        ),
    )


class RetirementLeverQuerySchema(BaseSchema):
    """Validates the /retirement/levers HTMX stepper query string (P2c).

    Both parameters are optional stepper values -- an absent parameter
    displays the solver's own default.  ``contribution`` is a money
    amount (dollars per pay period, NOT a percent, so no ``@pre_load``
    percent conversion applies): two decimal places, bounded to
    ``[0, 100000]`` so a URL-edited negative or absurd amount is a 422
    rather than a silently nonsensical outcome line.  ``months`` is the
    retire-later offset in whole months, bounded to ``[0, 180]`` --
    the same +180 cap the P2b binary search honors
    (:data:`app.services.retirement_levers._MAX_DELAY_MONTHS`).
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs (no percent fields on this schema)."""
        return _normalize_empty_inputs(self, data)

    contribution = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("100000"),
        ),
    )
    months = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=0, max=180),
    )
