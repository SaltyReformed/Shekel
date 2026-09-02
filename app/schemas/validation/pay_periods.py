"""Pay-period generation and lifecycle validation schemas.

The bounds every field here applies are NAMED rather than repeated, and plan
step **X-ad-a** is why: registration became a fifth door onto the same two
rules, so a literal copied once more would have been five statements of one
bound.  Each pair is imported from whoever OWNS the rule rather than restated
here -- the cadence pair from the model carrying the matching CHECK constraint
(:data:`~app.models.pay_schedule.CADENCE_DAYS_MIN` /
:data:`~app.models.pay_schedule.CADENCE_DAYS_MAX`), the batch pair from the
writer whose transaction does the work
(:data:`~app.services.pay_period_write.PERIOD_BATCH_MIN` /
:data:`~app.services.pay_period_write.PERIOD_BATCH_MAX`), and, since plan step
**balance:X-bh-2**, the pay-history window from :mod:`app.utils.dates` --
which is not a column's bound at all but how far this application's calendar
reaches, mirrored onto ``ck_pay_schedule_history_opens_range`` for the writers
that never see a schema.  A schema is a door, and a door does not get to
invent the rule it enforces.

*THREE pairs now, where this paragraph counted two until X-bh-2 added the
third.  The number is restated rather than left to decay, which is the same
discipline* :data:`app.utils.dates.CALENDAR_DATE_MIN`'s *own comment follows.*
"""


from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.config import BaseConfig
from app.models.pay_schedule import CADENCE_DAYS_MAX, CADENCE_DAYS_MIN
from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)
from app.services.pay_period_write import PERIOD_BATCH_MAX, PERIOD_BATCH_MIN
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN


#: The two shared validators, built once.  ``validate.Range`` instances are
#: immutable for the bounds they were constructed with, so sharing one across
#: fields is safe -- and it is what makes "the same bound" literally the same
#: object rather than an equal copy.
#:
#: **The cadence bound is the COLUMN's, and it is the only one there is** since
#: plan step ``pay_calendar:C4-c``.  This module carried a second name for the
#: floor -- ``CADENCE_DAYS_FORM_MIN``, one day tighter -- because the writer
#: STORED ``end_date`` and ``ck_pay_periods_date_order`` required
#: ``start_date < end_date``, so a cadence of 1 reached the CHECK as an
#: unhandled 500.  The column is gone, two paydays a day apart simply define a
#: one-day period, and the second name had no content left: one number with two
#: names is how ``EFFECTIVE_DATE_*`` and ``_STARTS_ON_*`` already came to exist.
#: So the browser, the schema and the column state one range, and the generate
#: route's remaining service refusal is PROVABLY the forward-only one.
_CADENCE_DAYS_RANGE = validate.Range(
    min=CADENCE_DAYS_MIN, max=CADENCE_DAYS_MAX,
)
_PERIOD_BATCH_RANGE = validate.Range(
    min=PERIOD_BATCH_MIN, max=PERIOD_BATCH_MAX,
)
#: The window a stated pay-history opening may fall in, from
#: :mod:`app.utils.dates`, which OWNS it.  Not the column's own rule, unlike
#: the two ranges above: it is how far this application's calendar reaches,
#: and ``ck_pay_schedule_history_opens_range`` mirrors the same pair onto the
#: table.  Reading the constant here rather than an alias of it is what keeps
#: one number from acquiring a fourth name.
_HISTORY_OPENS_RANGE = validate.Range(
    min=CALENDAR_DATE_MIN, max=CALENDAR_DATE_MAX,
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


def history_opens_on_field(**kwargs) -> fields.Date:
    """Return a when-did-these-paychecks-start field bounded by the column's CHECK.

    Plan step **balance:X-bh-2**.  Two doors ask the question -- registration
    and the pay-periods settings section -- so the bound is declared once here
    beside the cadence and batch fields, for the reason the module docstring
    gives.

    ``allow_none`` is fixed rather than forwarded, because a blank answer is
    the field's ORDINARY value and means something: the owner has not stated a
    history, so the engine counts only their recorded paydays (ruling
    **balance:R-IA** as amended 2026-08-31).  A door that wanted it required
    would be asking a different question.  Its callers pair
    it with :func:`~app.schemas.validation._helpers._normalize_empty_inputs`,
    which is what turns an untouched HTML date input's ``""`` into that
    ``None`` rather than into "not a valid date".

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Date` -- the
            per-schema half of the declaration; see :func:`cadence_days_field`.

    Returns:
        The field, carrying the shared range validator.
    """
    return fields.Date(
        validate=_HISTORY_OPENS_RANGE, allow_none=True, **kwargs
    )


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

    **One field, and the deleted one is finding P29's fix** (plan step C3-b).
    ``cadence_days`` was accepted here, optional, and forwarded into
    ``extend_pay_periods`` -- while the extend card renders NO control for it.
    So a direct POST generated paychecks at a spacing the app never recorded:
    ``budget.pay_schedule`` still said 14, and ``resolve_cadence``, the derived
    horizon and the next rolling top-up all continued at 14.  Extend CONTINUES
    an existing schedule, so the cadence is not a question this door asks; the
    field is gone rather than newly persisted, which is what finding **P30**
    asked for.  ``BaseSchema``'s ``unknown = EXCLUDE`` means an old client that
    still posts one is not refused -- the value is simply ignored, which is now
    what it means.
    """

    num_periods = num_periods_field(required=True)


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


class PayHistorySchema(BaseSchema):
    """Validates POST data for the pay-history opening.

    Plan step **balance:X-bh-2**.  One optional field, and it is its own form
    rather than a field on the rolling-window one beside it: that form
    configures a WRITE (how far ahead to keep generating) and this states a
    fact about the owner, so pressing Save on either must not restate the
    other's answer.

    ``load_default=None`` and ``allow_none`` together mean a submission with
    the box cleared -- or with the field absent -- stores ``NULL``, which is
    how an owner WITHDRAWS a statement and returns to being counted from the
    record.  Clearing it is a real user action rather than a missing input,
    which is exactly the distinction
    :func:`~app.schemas.validation._helpers._normalize_empty_inputs` draws.
    """

    history_opens_on = history_opens_on_field(load_default=None)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Map the cleared date input's ``""`` to an explicit ``None``."""
        return _normalize_empty_inputs(self, data)


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
