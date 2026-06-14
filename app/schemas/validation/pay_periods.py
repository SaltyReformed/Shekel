"""Pay-period generation and lifecycle validation schemas."""


from marshmallow import (
    fields,
    validate,
)

from app.schemas.validation._helpers import BaseSchema


class PayPeriodGenerateSchema(BaseSchema):
    """Validates POST data for generating pay periods."""

    start_date = fields.Date(required=True)
    num_periods = fields.Integer(
        load_default=52, validate=validate.Range(min=1, max=260)
    )
    cadence_days = fields.Integer(
        load_default=14, validate=validate.Range(min=1, max=365)
    )


class PayPeriodExtendSchema(BaseSchema):
    """Validates POST data for extending the schedule forward.

    ``cadence_days`` is optional: when omitted the service resolves it
    from the stored schedule (else the last period's length), so the
    common case is a single "how many periods" field.
    """

    num_periods = fields.Integer(
        required=True, validate=validate.Range(min=1, max=260)
    )
    cadence_days = fields.Integer(
        required=False, allow_none=True, validate=validate.Range(min=1, max=365)
    )


class PayPeriodTruncateSchema(BaseSchema):
    """Validates POST data for truncating the schedule tail.

    ``keep_through_index`` is the highest ``period_index`` to keep; every
    higher index is deleted.  ``confirm_discard`` acknowledges the loss of
    hand-entered / changed rows the discard gate would otherwise block on.
    """

    keep_through_index = fields.Integer(
        required=True, validate=validate.Range(min=0, max=100000)
    )
    confirm_discard = fields.Boolean(load_default=False)


class PayPeriodRegenerateSchema(BaseSchema):
    """Validates POST data for regenerating the future tail.

    Mirrors the generate fields plus ``confirm_discard``; ``cadence_days``
    is required because regenerate establishes (and persists) the new
    cadence.
    """

    new_start_date = fields.Date(required=True)
    num_periods = fields.Integer(
        required=True, validate=validate.Range(min=1, max=260)
    )
    cadence_days = fields.Integer(
        required=True, validate=validate.Range(min=1, max=365)
    )
    confirm_discard = fields.Boolean(load_default=False)


class PayPeriodResetSchema(BaseSchema):
    """Validates POST data for a full schedule reset (first-time setup).

    Mirrors the generate fields plus a required ``confirm`` acknowledgement.
    Unlike regenerate there is no ``confirm_discard``: reset wipes the
    WHOLE schedule -- including past and anchor periods -- by design, so
    its safety is the service's zero-settled refusal plus this explicit
    confirmation (an unchecked box submits nothing, hence
    ``load_default=False``; the route refuses an unconfirmed reset).
    """

    new_start_date = fields.Date(required=True)
    num_periods = fields.Integer(
        required=True, validate=validate.Range(min=1, max=260)
    )
    cadence_days = fields.Integer(
        required=True, validate=validate.Range(min=1, max=365)
    )
    confirm = fields.Boolean(load_default=False)


class PayScheduleSchema(BaseSchema):
    """Validates POST data for the continuous-rolling-window settings.

    ``rolling_enabled`` toggles continuous top-up (an unchecked checkbox
    submits nothing, hence ``load_default=False``).  ``rolling_target_periods``
    is how many current-and-future periods to keep generated ahead -- the
    count INCLUDES the current period.  Its range mirrors the generate /
    extend ``num_periods`` cap (1..260) and the column's ``> 0`` CHECK.
    Cadence is NOT set here: it is owned by generate / regenerate.
    """

    rolling_enabled = fields.Boolean(load_default=False)
    rolling_target_periods = fields.Integer(
        required=True, validate=validate.Range(min=1, max=260)
    )
