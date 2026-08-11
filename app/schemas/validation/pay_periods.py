"""Pay-period generation and lifecycle validation schemas.

The bounds every field here applies are NAMED rather than repeated, and plan
step **X-ad-a** is why: registration became a fifth door onto the same two
rules, so a literal copied once more would have been five statements of one
bound.  Each pair is imported from whoever OWNS the rule rather than restated
here -- the cadence pair from the model carrying the matching CHECK constraint
(:data:`~app.models.pay_schedule.CADENCE_DAYS_MIN` /
:data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`), the batch pair from the
writer whose transaction does the work
(:data:`~app.services.pay_period_service.PERIOD_BATCH_MIN` /
:data:`~app.services.pay_period_service.PERIOD_BATCH_MAX`).  A schema is a
door, and a door does not get to invent the rule it enforces.
"""


from marshmallow import (
    fields,
    validate,
)

from app.config import BaseConfig
from app.models.pay_period import MIN_MATERIALISABLE_CADENCE_DAYS
from app.models.pay_schedule import CADENCE_DAYS_MAX, CADENCE_DAYS_MIN
from app.schemas.validation._helpers import BaseSchema, RowId
from app.services.pay_period_service import PERIOD_BATCH_MAX, PERIOD_BATCH_MIN


#: The two shared validators, built once.  ``validate.Range`` instances are
#: immutable for the bounds they were constructed with, so sharing one across
#: fields is safe -- and it is what makes "the same bound" literally the same
#: object rather than an equal copy.
#:
#: **The cadence FLOOR here is the WRITER's, not the column's**, and the
#: difference is one day.  ``budget.pay_schedule.cadence_days`` accepts 1
#: (:data:`~app.models.pay_schedule.CADENCE_DAYS_MIN`) and a derived calendar
#: handles a one-day cycle correctly, but every form that carries a cadence
#: submits it to :func:`~app.services.pay_period_service.generate_pay_periods`,
#: which AUTHORS ``end_date`` and so cannot express a period shorter than two
#: days (:data:`~app.models.pay_period.MIN_MATERIALISABLE_CADENCE_DAYS`).
#: Bounding at the column's floor let a 1 through to the CHECK as an unhandled
#: 500.  Taking the tighter of the two here means the browser, the schema and
#: the service refuse the same set -- and it is what makes the generate
#: route's remaining service refusal PROVABLY the forward-only one.  C4 drops
#: the authored column and this floor returns to the column's.
CADENCE_DAYS_FORM_MIN = max(CADENCE_DAYS_MIN, MIN_MATERIALISABLE_CADENCE_DAYS)

_CADENCE_DAYS_RANGE = validate.Range(
    min=CADENCE_DAYS_FORM_MIN, max=CADENCE_DAYS_MAX,
)
_PERIOD_BATCH_RANGE = validate.Range(
    min=PERIOD_BATCH_MIN, max=PERIOD_BATCH_MAX,
)


def cadence_days_field(**kwargs) -> fields.Integer:
    """Return a days-between-paydays field bounded by the column's CHECK.

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Integer` -- the
            per-schema half of the declaration (``required``, ``load_default``,
            ``allow_none``), which differs by door: generate defaults it,
            regenerate and reset require it, extend allows ``None`` to mean
            "continue at the stored cadence".

    Returns:
        The field, carrying the shared range validator.
    """
    return fields.Integer(validate=_CADENCE_DAYS_RANGE, **kwargs)


def num_periods_field(**kwargs) -> fields.Integer:
    """Return a how-many-periods field bounded by the batch policy.

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Integer`; see
            :func:`cadence_days_field` for why the rest of the declaration is
            the caller's.

    Returns:
        The field, carrying the shared range validator.
    """
    return fields.Integer(validate=_PERIOD_BATCH_RANGE, **kwargs)


class PayPeriodGenerateSchema(BaseSchema):
    """Validates POST data for generating pay periods."""

    start_date = fields.Date(required=True)
    num_periods = num_periods_field(
        load_default=BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
    )
    cadence_days = cadence_days_field(
        load_default=BaseConfig.DEFAULT_PAY_CADENCE_DAYS,
    )


class PayPeriodExtendSchema(BaseSchema):
    """Validates POST data for extending the schedule forward.

    ``cadence_days`` is optional: when omitted the service resolves it
    from the stored schedule (else the last period's length), so the
    common case is a single "how many periods" field.
    """

    num_periods = num_periods_field(required=True)
    cadence_days = cadence_days_field(required=False, allow_none=True)


class PayPeriodTruncateSchema(BaseSchema):
    """Validates POST data for truncating the schedule tail.

    ``keep_through_period_id`` names the last pay period to KEEP; every period
    opening after it is deleted.  ``confirm_discard`` acknowledges the loss of
    hand-entered / changed rows the discard gate would otherwise block on.

    **It names the period by ``id``, and plan step C3-a is why** (finding
    **P13**).  This field was ``keep_through_index``, a plain
    ``fields.Integer`` carrying the ORDINAL ``budget.pay_periods.period_index``
    -- so a user-supplied position selected which periods a CASCADE destroyed,
    and it survived a round trip through the browser in the discard-confirm
    422's hidden payload.  That was safe only while nothing renumbered, which
    is true today and which plan steps C3-b and C6 change; identity is ``id``,
    so the wire key is ``id``.

    A :class:`~app.schemas.validation._helpers.RowId` rather than an
    ``Integer``: it names a ROW, and the strict spelling rules that go with
    that (no ``"007"``, no ``" 12 "``, no ``1.9``) travel with the type.  The
    service still resolves the id against the submitter's OWN periods, because
    a well-formed id is not an owned one.
    """

    keep_through_period_id = RowId(required=True)
    confirm_discard = fields.Boolean(load_default=False)


class PayPeriodRegenerateSchema(BaseSchema):
    """Validates POST data for regenerating the future tail.

    Mirrors the generate fields plus ``confirm_discard``; ``cadence_days``
    is required because regenerate establishes (and persists) the new
    cadence.
    """

    new_start_date = fields.Date(required=True)
    num_periods = num_periods_field(required=True)
    cadence_days = cadence_days_field(required=True)
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
    num_periods = num_periods_field(required=True)
    cadence_days = cadence_days_field(required=True)
    confirm = fields.Boolean(load_default=False)


class PayScheduleSchema(BaseSchema):
    """Validates POST data for the continuous-rolling-window settings.

    ``rolling_enabled`` toggles continuous top-up (an unchecked checkbox
    submits nothing, hence ``load_default=False``).  ``rolling_target_periods``
    is how many current-and-future periods to keep generated ahead -- the
    count INCLUDES the current period.  It takes the generate / extend batch
    bound, which is the same question asked of a target rather than of a
    submission, and it sits inside the column's ``> 0`` CHECK.
    Cadence is NOT set here: it is owned by generate / regenerate.
    """

    rolling_enabled = fields.Boolean(load_default=False)
    rolling_target_periods = num_periods_field(required=True)
