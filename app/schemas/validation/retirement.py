"""Pension-profile and retirement-settings validation schemas."""


from datetime import date
from decimal import Decimal

from marshmallow import (
    fields,
    post_load,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _RAISE_YEAR_RANGE,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)
from app.services.salary_raises import RAISE_END_MODES

#: What the assumptions rail names each recurring raise's end-year pair on the
#: wire (plan step salary:S3-f-2b): ``raise_end_mode_<raise id>`` and
#: ``raise_end_year_<raise id>``.  The id rides in the NAME rather than in a
#: value because every rail row submits on every readiness refresh
#: (``hx-include=".whatif-param, .lever-param"``), and a flat query string has
#: no other way to say which raise a mode belongs to.  Spelled here and in
#: ``retirement/_assumptions.html``, which renders the controls; the route test
#: posts what the template emits.
_RAISE_END_MODE_PARAM = "raise_end_mode_"
_RAISE_END_YEAR_PARAM = "raise_end_year_"
#: The same two, keyed by :class:`~app.services.salary_raises.EndYearError`'s
#: ``field`` so a refusal can name its control (:func:`raise_end_control`).
_RAISE_END_PARAMS = {"mode": _RAISE_END_MODE_PARAM, "year": _RAISE_END_YEAR_PARAM}


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

    salary_profile_id = RowId(allow_none=True)
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

    salary_profile_id = RowId(allow_none=True)
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


class RaiseProbeSchema(BaseSchema):
    """ONE rail row's end-year answer: the salary form's pair, off the wire.

    The same two controls :class:`~app.schemas.validation.salary
    .RaiseCreateSchema` reads (``raise_end_mode`` and ``terminal_year``) under
    the rail's shorter names, graded by the same vocabulary and the same year
    window.  What it does NOT grade is the cross-field rule -- a year cannot
    precede the raise's effective year -- because that needs the ROW, which
    only the service holding the rows has:
    :meth:`~app.services.retirement_plan.RetirementInputs.plan_with` applies
    :func:`~app.services.salary_raises.end_year_of` there.  A schema that
    graded half the rule here and left the other half to the service would be
    two homes for one rule; this one grades the FIELDS and hands the pair on.
    """

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Map an empty year box to ``None``; drop an empty mode."""
        return _normalize_empty_inputs(self, data)

    mode = fields.String(
        required=True, validate=validate.OneOf(RAISE_END_MODES),
    )
    year = fields.Integer(allow_none=True, validate=_RAISE_YEAR_RANGE)

    @post_load
    def as_pair(self, data, **kwargs):
        """Hand the service the ``(mode, year)`` pair ``end_year_of`` takes.

        Args:
            data: The deserialized ``{"mode": ..., "year": ...}``.
            **kwargs: Marshmallow's contract, unused.

        Returns:
            ``(mode, year_or_None)``.
        """
        return (data["mode"], data.get("year"))


def _gather_raise_probes(data) -> dict:
    """Collect the rail's per-raise pairs out of a flat query string.

    Read off the RAW payload, before :func:`_normalize_empty_inputs` runs:
    that helper drops an undeclared key whose value is ``""`` -- which is
    exactly what a rail row under *no end year* submits for its year box --
    and ``BaseSchema`` excludes whatever undeclared keys survive.  Gathering
    first is what lets :class:`RaiseProbeSchema` see the empty box and map it
    to ``None`` itself.

    Args:
        data: The ``@pre_load`` payload -- a ``MultiDict`` from the route or a
            plain mapping from a test.

    Returns:
        ``{raise id as submitted: {"mode": ..., "year": ...}}`` -- raw strings,
        one entry per raise id that appeared under either prefix, so a mode
        without its year or a year without its mode reaches the nested schema
        and is graded there rather than silently paired with nothing.
    """
    probes: dict = {}
    for key in data:
        for prefix, half in (
            (_RAISE_END_MODE_PARAM, "mode"), (_RAISE_END_YEAR_PARAM, "year"),
        ):
            if key.startswith(prefix):
                probes.setdefault(key[len(prefix):], {})[half] = data[key]
    return probes


def _normalize_with_raise_probes(schema, data, percent_fields) -> dict:
    """The ``@pre_load`` body both schemas that read the rail's pair share.

    Gather the per-raise pairs FIRST (see :func:`_gather_raise_probes` for why
    the order matters), then the two normalisations every retirement schema
    runs, then attach the pairs under ``raise_probes`` for the declared field
    to grade.  One body because the readiness GET and the settings POST carry
    the SAME controls -- the rail's rows are the what-if inputs and, since plan
    step salary:S3-f-3, the Save's -- and two spellings of how they are read
    off the wire would be the disagreement the pair's naming was designed to
    prevent.

    Args:
        schema: The schema instance, for :func:`_normalize_empty_inputs`.
        data: The raw payload.
        percent_fields: The schema's ``_PERCENT_FIELDS``.

    Returns:
        The normalised payload, with ``raise_probes`` present iff a pair was.
    """
    probes = _gather_raise_probes(data)
    data = _normalize_empty_inputs(schema, data)
    data = _normalize_percent_fields(data, percent_fields)
    if probes:
        data["raise_probes"] = probes
    return data


def raise_end_control(raise_id, field: str) -> str:
    """The rail control's NAME for one half of a raise's end-year pair.

    The one spelling of ``raise_end_mode_<id>`` / ``raise_end_year_<id>``
    outside the template: ``retirement.update_settings`` keys a refusal by the
    control it belongs to, exactly as the rail's other rows key theirs, so the
    fragment renders it on that control (plan step salary:S3-f-3).

    Args:
        raise_id: The raise's id, as submitted or as stored.
        field: :class:`~app.services.salary_raises.EndYearError`'s
            vocabulary -- ``"mode"`` or ``"year"``.

    Returns:
        The control name.
    """
    return f"{_RAISE_END_PARAMS[field]}{raise_id}"


def raise_probe_errors_by_control(field_errors: dict) -> dict[str, list[str]]:
    """Re-key the ``raise_probes`` field's refusals onto the rail's controls.

    marshmallow reports a ``Dict`` field per key: ``{"value": {half:
    [messages]}}`` when the nested pair failed, ``{"key": [messages]}`` when
    the id itself did.  The rail renders errors by control name, so each half
    lands on its control and a refused id -- which names no rendered row --
    lands on the mode control of the id as submitted.

    Args:
        field_errors: ``errors["raise_probes"]`` from ``schema.validate``.

    Returns:
        ``{control name: [messages]}``.
    """
    by_control: dict[str, list[str]] = {}
    for raise_id, halves in field_errors.items():
        for half, messages in halves.get("value", {}).items():
            by_control.setdefault(raise_end_control(raise_id, half), []).extend(messages)
        if "key" in halves:
            by_control.setdefault(raise_end_control(raise_id, "mode"), []).extend(halves["key"])
    return by_control


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
    the same schema.  There is deliberately NO field for an assumed annual
    return: its save semantics are an open developer question, so the
    panel's return row stays what-if-only.  There is no longer one for the
    merit-raise horizon either -- plan step salary:S3-c deleted that
    setting (ruling **R-SAL11**); **what replaced it is the per-raise
    pair** (plan step salary:S3-f-3, ruling **R-SAL22**): a rail row's Save
    posts the SAME ``raise_end_mode_<id>`` / ``raise_end_year_<id>`` controls
    its what-if sends, gathered and graded exactly as
    :class:`RetirementReadinessQuerySchema` gathers and grades them, into
    ``raise_probes`` -- ``{raise_id: (mode, year)}`` -- which the route
    resolves against the ROW through the ONE end-year rule and writes.  The
    salary page still writes the whole raise through
    :class:`~app.schemas.validation.salary.RaiseCreateSchema`.
    """

    _PERCENT_FIELDS = (
        "safe_withdrawal_rate", "estimated_retirement_tax_rate",
    )

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Gather the per-raise pairs, normalize empties, convert percents."""
        return _normalize_with_raise_probes(self, data, self._PERCENT_FIELDS)

    safe_withdrawal_rate = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=0, max=1),
    )
    planned_retirement_date = fields.Date(allow_none=True)
    estimated_retirement_tax_rate = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )
    # The rail's per-raise pair, declared as the readiness query declares it
    # (one spelling: the same field, the same nested schema, the same strict
    # id key); absent when no rail row posted.
    raise_probes = fields.Dict(
        keys=RowId(), values=fields.Nested(RaiseProbeSchema),
    )

    @validates_schema
    def validate_future_retirement_date(self, data, **kwargs):
        """Reject a planned retirement date that is not in the future.

        Mirrors the pension schemas' rule (M1): a past or today date
        collapses the projection horizon to zero periods, producing a
        contradictory page (a shortfall verdict beside a lever with no
        periods to solve over).  ``None`` (clearing the date) stays
        valid -- only a present-or-past DATE is rejected.
        """
        planned = data.get("planned_retirement_date")
        if planned and planned <= date.today():
            raise ValidationError(
                "Planned retirement date must be in the future.",
                field_name="planned_retirement_date",
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
    ``@pre_load``, so the route does no money math).  It carried a
    ``merit_raise_horizon_years`` what-if until plan step salary:S3-c
    deleted the setting behind it (ruling **R-SAL11**); a stale bookmark
    still passing one is DROPPED rather than rejected, because
    ``BaseSchema`` excludes unknown keys.  **The per-raise probe replaced it
    at plan step salary:S3-f-2b**: ``raise_end_mode_<id>`` /
    ``raise_end_year_<id>`` pairs, one per recurring raise the rail lists,
    gathered in ``@pre_load`` (the ids are in the NAMES, so no declared
    field could catch them before ``EXCLUDE`` drops them) into
    ``raise_probes``, ``{raise_id: (mode, year)}``, which the route hands to
    ``plan_with`` to resolve against the rows.  The two lever stepper values:
    ``months`` capped at
    the P2b +180 search bound
    (:data:`app.services.retirement_levers._MAX_DELAY_MONTHS`) and
    ``contribution`` a money amount bounded to ``[0, 100000]`` (so it is
    deliberately NOT in ``_PERCENT_FIELDS``; a URL-edited negative or
    absurd amount is a 422).  All optional: an absent parameter means
    "stored value" (no what-if on that row).  This is the ONE schema for
    every lever/what-if refresh -- the P2c-era RetirementLeverQuerySchema
    retired with its /retirement/levers route (M2: the page's steppers
    all refresh through /retirement/readiness).
    """

    _PERCENT_FIELDS = ("swr", "return_rate")

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Gather the per-raise pairs, normalize empties, convert percents."""
        return _normalize_with_raise_probes(self, data, self._PERCENT_FIELDS)

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
    # Keyed by the raise id as every other submitted row id is read
    # (:class:`RowId`), so ``raise_end_mode_007`` and ``raise_end_mode_`` are
    # refusals rather than coercions; absent when no rail row submitted.
    raise_probes = fields.Dict(
        keys=RowId(), values=fields.Nested(RaiseProbeSchema),
    )
