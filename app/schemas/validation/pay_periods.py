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
    ValidationError,
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app import ref_cache
from app.config import BaseConfig
from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError as AppValidationError
from app.models.pay_schedule import CADENCE_DAYS_MAX, CADENCE_DAYS_MIN
from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _RefEnumField,
    _normalize_empty_inputs,
)
from app.services import pay_schedule_service
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


class BusinessDayShiftField(_RefEnumField):
    """A submitted ``ref.business_day_shifts`` id, as its enum member.

    What payroll does when a payday lands on a day no money moves on (plan
    step **pay_calendar:C14-b**, ruling **R-PC56**).  The vocabulary is the
    one ``budget.recurrence_rules.shift_id`` already keys to, seeded at
    ``recurrence:R2``, so a bill's cash date and a payday ask one question of
    one table (**R-PC47**).

    Returning the MEMBER rather than the id is
    :class:`~app.schemas.validation._helpers._RefEnumField`'s standing reason
    plus one this axis has of its own:
    :func:`~app.utils.business_days.shift_to_business_day` REFUSES anything
    that is not a member rather than defaulting to a direction, and every
    other reference comparison in this application is an integer id -- so an
    id travelling under the name ``shift`` is the natural mistake, and it is
    one that would move a money date.  The conversion happens once here and
    once in
    :func:`~app.services.pay_schedule_service.upsert_schedule`, at the two
    edges of the wire.

    Whether the cadence beside it can CARRY the chosen convention is a
    property of the pair rather than of this field, so it is refused by
    :func:`~app.services.pay_schedule_service.reject_shift_on_short_cadence`
    at the write door -- where the pair is known and where the floor can be
    re-derived from the holiday calendar, which a schema-level bound could
    only freeze.
    """

    _invalid_message = "Invalid payday adjustment."

    def _member_for(self, row_id):
        """Return the :class:`~app.enums.BusinessDayShiftEnum` member, or ``None``.

        Args:
            row_id: A validated ``ref.business_day_shifts`` id.

        Returns:
            The member, or ``None`` when unmodelled.
        """
        return ref_cache.business_day_shift_member(row_id)


def shift_field(**kwargs) -> BusinessDayShiftField:
    """Return a payday-adjustment field for a door that asks for a cadence.

    Declared beside :func:`cadence_days_field` because **R-PC56** pairs them:
    the convention is asked wherever a cadence is, which is all four doors
    that state a rhythm.  A factory rather than a bare field for the reason
    that one has -- the per-schema half of the declaration differs by door --
    and so that a fifth door inherits the type rather than choosing one.

    Args:
        **kwargs: Forwarded to :class:`BusinessDayShiftField`.  Generate and
            registration default it to ``none`` exactly as they default the
            cadence beside it; regenerate and reset require it, exactly as
            they require the cadence.  The pairing is deliberate: a door where
            a missing cadence would silently restate the rhythm is a door
            where a missing convention would too, so the two fields answer a
            missing input the same way rather than differently.

    Returns:
        The field.
    """
    return BusinessDayShiftField(**kwargs)


def validate_derivable_rhythm(data):
    """Refuse a cadence and convention no pay calendar can derive from.

    The cross-field half of every schedule form's validation, shared by the
    four doors that state a rhythm so the rule is worded once -- the same
    placement, and the same reason, as
    :func:`~app.schemas.validation._recurrence.validate_authorable_cadence`.
    Whether a convention can be carried is a property of the PAIR and of
    neither field alone: a two-day cadence is ordinary, ``prior`` is ordinary,
    and together they displace two paydays onto one day that
    ``pay_calendar.derive_periods`` refuses outright.

    **The import is top-level, and a first draft deferred it on a rationale
    that measured false.**  That draft said deferring kept "the service layer"
    out of this module's import, which the auth schema pulls in at startup.
    It was already there: line 44 imports ``pay_period_write``, and that module
    imports ``pay_schedule_service`` itself, so
    ``app.services.pay_schedule_service`` is in ``sys.modules`` the moment this
    module finishes importing -- measured, not argued.  A ``pylint`` disable
    whose stated reason is false is worse than none, because
    ``shekel-disable-rationale`` then certifies a sentence nobody re-checked.

    **It does not restate the rule, it ASKS it.**  The floor is derived from
    the federal holiday calendar and the refusal belongs to the column's write
    door, so this calls that door's own predicate and converts its refusal into
    a field error.  Two spellings of a bound are two chances to disagree, which
    is what the cadence range's own history in this module records.

    **The field it names is the point of the function.**  Without it the
    refusal reaches ``routes/pay_periods.py``'s ``except ValidationError``
    handler, which renders every message it catches under ``start_date`` -- a
    cadence-and-convention complaint appearing beneath "First Payday".  That
    handler's comment predicted this exact failure in advance ("Widen either
    field and this line starts rendering a cadence message under the date
    box"); answering here is what keeps its attribution provable.

    Args:
        data: The deserialized form payload.  A door that omits either key
            (extend, truncate) has no pair to judge and is skipped.

    Raises:
        ValidationError: Marshmallow's, attributed to ``shift`` -- the control
            the owner would have to change, since the cadence is usually the
            fact and the convention usually the choice.
    """
    cadence_days = data.get("cadence_days")
    shift = data.get("shift")
    if cadence_days is None or shift is None:
        return
    try:
        pay_schedule_service.reject_shift_on_short_cadence(
            pay_schedule_service.Rhythm(cadence_days=cadence_days, shift=shift),
        )
    except AppValidationError as exc:
        raise ValidationError(str(exc), "shift") from exc


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
    shift = shift_field(load_default=BusinessDayShiftEnum.NONE)

    @validates_schema
    def validate_rhythm(self, data, **kwargs):
        """Refuse a pair no calendar can derive (**R-PC54**)."""
        validate_derivable_rhythm(data)


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
    and ``shift`` are required because regenerate establishes (and persists)
    the new rhythm, and a door that would silently restate one half on a
    missing input must not.
    """

    new_start_date = fields.Date(required=True)
    num_periods = num_periods_field(required=True)
    cadence_days = cadence_days_field(required=True)
    shift = shift_field(required=True)
    confirm_discard = fields.Boolean(load_default=False)

    @validates_schema
    def validate_rhythm(self, data, **kwargs):
        """Refuse a pair no calendar can derive (**R-PC54**)."""
        validate_derivable_rhythm(data)


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
    shift = shift_field(required=True)
    confirm = fields.Boolean(load_default=False)

    @validates_schema
    def validate_rhythm(self, data, **kwargs):
        """Refuse a pair no calendar can derive (**R-PC54**)."""
        validate_derivable_rhythm(data)


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
