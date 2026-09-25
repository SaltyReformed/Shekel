"""The pay RHYTHM's form: how a rhythm crosses the wire, declared once.

**Plan step ``pay_calendar:C17-d-3`` (ruling R-PC84), and it left**
:mod:`app.schemas.validation.pay_periods` **at the 1,000-line cap** -- the
seam :mod:`app.schemas.validation._recurrence` already draws for the
recurrence form: ONE domain's shared form beside the doors that carry it.
What is here is everything the four doors that state a rhythm --
registration, generate, regenerate and reset -- share: the fields that
state one (the kind control, each arm's inputs, the payday convention),
the wire vocabulary those fields speak, the two inverses between the wire
and the :class:`~app.services.pay_rhythm.Rhythm` value, the cross-field
rule that judges a stated rhythm, and the mixins that put all of it on a
schema.  What stays in ``pay_periods`` is the pay-period DOORS: their own
bounds (the batch, the payday, the history window) and the schemas.

**The rhythm crosses the wire as a KIND and that kind's own inputs, and the
schema hands back the VALUE** (ruling **R-PC84**; the mechanism is
**R-PC80**'s "a form's kind control posts a plain wire value the schema maps
to a type").  Each door renders three radio arms -- every N days, monthly on
a day, twice a month on two days -- each holding its own number inputs, and
all of them submit; :func:`rhythm_from_wire` reads the chosen arm's inputs
and no other's, and :class:`RhythmFormMixin`'s ``@post_load`` puts the
:class:`~app.services.pay_rhythm.Rhythm` on the loaded payload under
``rhythm`` with the wire keys consumed, so no route spells a cadence's
constructor.  :func:`rhythm_to_wire` is the one inverse, for the two places
the application renders a rhythm BACK into a form: the regenerate door's
discard-confirm banner, which re-posts what was submitted, and the manage
card, which preselects the owner's stored rhythm.

The bounds are NAMED rather than repeated, on ``pay_periods``' own rule: the
cadence pair and the day-of-month pair are imported from the model carrying
the matching CHECK constraints
(:data:`~app.models.pay_era.CADENCE_DAYS_MIN` /
:data:`~app.models.pay_era.CADENCE_DAYS_MAX`,
:data:`~app.models.pay_era.DAY_OF_MONTH_MIN` /
:data:`~app.models.pay_era.DAY_OF_MONTH_MAX`), and what a PAIR of days may
be, the grid question and the collision floor are ASKED of the write door's
own predicates rather than restated.
"""


from dataclasses import dataclass
from typing import Callable

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    pre_load,
    validate,
    validates_schema,
)

from app import ref_cache
from app.config import BaseConfig
from app.enums import BusinessDayShiftEnum
from app.exceptions import ValidationError as AppValidationError
from app.models.pay_era import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    DAY_OF_MONTH_MAX,
    DAY_OF_MONTH_MIN,
)
from app.schemas.validation._helpers import (
    _RefEnumField,
    _clear_nullable_empties,
)
from app.services import pay_era_write, pay_schedule_service
from app.services.pay_rhythm import FixedDays, Monthly, Rhythm, SemiMonthly


#: The shared validators, built once.  ``validate.Range`` instances are
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
#: A day of the month a monthly or semi-monthly rhythm pays on (plan step
#: ``pay_calendar:C17-d-3``), the model's own 1..31: 29-31 mean "or the last
#: day of a shorter month" (ruling **R-PC79**), so the bound is the calendar's
#: and not the shortest month's.  What a PAIR of them may be -- distinct, the
#: lower at most 27 -- is a rule over two controls, asked of the write door's
#: own predicate by :func:`validate_derivable_rhythm` rather than restated.
_DAY_OF_MONTH_RANGE = validate.Range(
    min=DAY_OF_MONTH_MIN, max=DAY_OF_MONTH_MAX,
)
def cadence_days_field(**kwargs) -> fields.Integer:
    """Return a days-between-paydays field bounded by the column's CHECK.

    **Nullable, since plan step ``pay_calendar:C17-d-3``**, as every
    kind-parameter input is: the every-N-days radio arm's box is rendered
    and submitted whichever arm the owner chose, so a blank one arrives as
    ``""`` -- ``None`` after :class:`RhythmFormMixin`'s ``@pre_load`` -- and
    means "not this arm" when another kind was chosen, and "enter it" when
    this one was (:func:`rhythm_from_wire`).  Whether the arm is REQUIRED is
    therefore the builder's question, keyed on the chosen kind, and not this
    field's.

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Integer` -- the
            per-schema half of the declaration, which differs by door:
            generate and registration default it beside a defaulted kind,
            regenerate and reset carry no default because they require the
            kind.

    Returns:
        The field, carrying the shared range validator.
    """
    return fields.Integer(
        validate=_CADENCE_DAYS_RANGE, allow_none=True, **kwargs
    )


def day_of_month_field(**kwargs) -> fields.Integer:
    """Return a which-day-of-the-month field bounded by the model's 1..31.

    Plan step ``pay_calendar:C17-d-3``.  Three per door -- the monthly arm's
    day and the semi-monthly arm's two -- declared through one factory so the
    four doors bound them identically, for the reason
    :func:`cadence_days_field` exists.  Every door renders every arm, so the
    box is present on all of them and blank on the ones the owner did not
    choose, which is why it is nullable rather than required (see
    :func:`cadence_days_field`); an absent key and a blank box both reach
    :func:`rhythm_from_wire` as "nothing stated", since it reads with
    ``get``.

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Integer`, as the
            sibling factories forward theirs; no door passes any today.

    Returns:
        The field, carrying the shared day-of-month range validator.
    """
    return fields.Integer(
        validate=_DAY_OF_MONTH_RANGE, allow_none=True, **kwargs
    )


class CadenceKindField(fields.String):
    """A submitted cadence KIND token, as the value type it names.

    The kind control's wire value (ruling **R-PC80**: "a plain wire value the
    schema maps to a type"; the control itself is **R-PC84**'s radio arms),
    deserialized to the :mod:`app.services.pay_rhythm` class the token
    names -- :class:`~app.services.pay_rhythm.FixedDays`,
    :class:`~app.services.pay_rhythm.Monthly` or
    :class:`~app.services.pay_rhythm.SemiMonthly` -- for the reason
    :class:`~app.schemas.validation._helpers._RefEnumField` gives for handing
    back a member rather than an id: the schema is the boundary between the
    wire spelling and the logic value, and every consumer downstream
    (:func:`rhythm_from_wire`, the kind's own dispatch tables) takes the
    type.  There is no ``ref`` table behind it, by that ruling: the kind is
    which parameters an era carries, so the token is spelled once in
    :data:`_WIRE_OF_KIND` and read from there by the templates through
    :func:`cadence_kind_token`.
    """

    default_error_messages = {"invalid": "Choose how often you are paid."}

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the kind *value* names.

        Args:
            value: The submitted token.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Forwarded to :class:`marshmallow.fields.String`.

        Returns:
            The cadence class.

        Raises:
            ValidationError: *value* is not a string, or names no kind.
        """
        token = super()._deserialize(value, attr, data, **kwargs)
        kind = _KIND_OF_TOKEN.get(token)
        if kind is None:
            raise self.make_error("invalid")
        return kind


def cadence_kind_field(**kwargs) -> CadenceKindField:
    """Return the kind control's field for a door that asks for a rhythm.

    A factory beside :func:`cadence_days_field` and :func:`shift_field` for
    the reason those two are: the per-schema half differs by door (generate
    and registration default it to every-N-days beside the defaulted day
    count; regenerate and reset require it, as they require the convention),
    and a fifth door inherits the type rather than choosing one.

    Args:
        **kwargs: Forwarded to :class:`CadenceKindField`.

    Returns:
        The field.
    """
    return CadenceKindField(**kwargs)


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
    :func:`~app.services.pay_era_write.mint_era`, at the two edges of
    the wire.

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


#: The wire key of each radio arm's input, spelled once for the builders,
#: the renderers and :data:`_WIRE_OF_KIND` below.  The templates spell the
#: same names as ``name=`` attributes, which is the wire contract itself.
_CADENCE_DAYS = "cadence_days"
_DAY_OF_MONTH = "day_of_month"
_FIRST_DAY_OF_MONTH = "first_day_of_month"
_SECOND_DAY_OF_MONTH = "second_day_of_month"


def _required_input(data, control: str, prompt: str) -> int:
    """Return the chosen arm's input *control*, refusing a blank one.

    Args:
        data: The deserialized payload.
        control: The wire key of the input.
        prompt: What the refusal says.

    Returns:
        The submitted integer.

    Raises:
        ValidationError: Marshmallow's, attributed to *control*: the box is
            blank (``None`` after the mixin's ``@pre_load``) or absent.
    """
    value = data.get(control)
    if value is None:
        raise ValidationError(prompt, control)
    return value


def _fixed_days_from_wire(data) -> FixedDays:
    """Return the every-N-days arm's value.

    Args:
        data: The deserialized payload.

    Returns:
        The :class:`~app.services.pay_rhythm.FixedDays`.

    Raises:
        ValidationError: The day count is blank.
    """
    return FixedDays(_required_input(
        data, _CADENCE_DAYS, "Enter the days between paydays.",
    ))


def _monthly_from_wire(data) -> Monthly:
    """Return the monthly arm's value.

    Args:
        data: The deserialized payload.

    Returns:
        The :class:`~app.services.pay_rhythm.Monthly`.

    Raises:
        ValidationError: The day is blank.
    """
    return Monthly(_required_input(
        data, _DAY_OF_MONTH, "Enter the day of the month you are paid on.",
    ))


def _semi_monthly_from_wire(data) -> SemiMonthly:
    """Return the twice-a-month arm's value.

    Args:
        data: The deserialized payload.

    Returns:
        The :class:`~app.services.pay_rhythm.SemiMonthly`, which sorts the
        pair itself.

    Raises:
        ValidationError: Either day is blank; the first blank one is named.
    """
    return SemiMonthly((
        _required_input(
            data, _FIRST_DAY_OF_MONTH, "Enter the first of your two paydays.",
        ),
        _required_input(
            data, _SECOND_DAY_OF_MONTH, "Enter the second of your two paydays.",
        ),
    ))


def _fixed_days_to_wire(cadence: FixedDays) -> dict:
    """Return the every-N-days arm's inputs for *cadence*.

    Args:
        cadence: The value.

    Returns:
        ``{"cadence_days": days}``.
    """
    return {_CADENCE_DAYS: cadence.days}


def _monthly_to_wire(cadence: Monthly) -> dict:
    """Return the monthly arm's input for *cadence*.

    Args:
        cadence: The value.

    Returns:
        ``{"day_of_month": day}``.
    """
    return {_DAY_OF_MONTH: cadence.day}


def _semi_monthly_to_wire(cadence: SemiMonthly) -> dict:
    """Return the twice-a-month arm's inputs for *cadence*, lower day first.

    Args:
        cadence: The value.

    Returns:
        ``{"first_day_of_month": lower, "second_day_of_month": upper}``.
    """
    lower, upper = cadence.days
    return {_FIRST_DAY_OF_MONTH: lower, _SECOND_DAY_OF_MONTH: upper}


@dataclass(frozen=True)
class _KindWire:
    """How one cadence kind is spelled on the wire.

    Attributes:
        token: What the kind control posts for it.
        controls: The wire keys of the arm's inputs, in the order the arm
            renders them.
        from_wire: Builds the value from the arm's inputs, refusing a blank
            one against its control.
        to_wire: Renders the value back as the arm's inputs.
    """

    token: str
    controls: tuple[str, ...]
    from_wire: Callable[[dict], "FixedDays | Monthly | SemiMonthly"]
    to_wire: Callable[["FixedDays | Monthly | SemiMonthly"], dict]

    @property
    def bound_control(self) -> str:
        """Return the control a refusal of the value's BOUNDS is attributed to.

        The arm's last input, the one that completes the kind's parameters.
        Reached today only by the semi-monthly pair rule (the two days equal,
        or the lower past 27): a single input's range is the field's own
        validator, and marshmallow skips every cross-field rule while a
        field error stands.

        Returns:
            The wire key.
        """
        return self.controls[-1]


#: The wire spelling of each cadence KIND, keyed by the value's class (plan
#: step ``pay_calendar:C17-d-3``, ruling **R-PC84**): the ONE place a kind's
#: token and its arm's inputs are named, as ``pay_era_write._COLUMNS_OF`` is
#: the one place a value becomes its columns.  The kind field reads the
#: tokens from here, the templates read them through
#: :func:`cadence_kind_token`, and :func:`rhythm_from_wire` /
#: :func:`rhythm_to_wire` dispatch on it in each direction.  A kind absent
#: here is refused by the lookup rather than read as another.
_WIRE_OF_KIND = {
    FixedDays: _KindWire(
        "fixed_days", (_CADENCE_DAYS,),
        _fixed_days_from_wire, _fixed_days_to_wire,
    ),
    Monthly: _KindWire(
        "monthly", (_DAY_OF_MONTH,),
        _monthly_from_wire, _monthly_to_wire,
    ),
    SemiMonthly: _KindWire(
        "semi_monthly", (_FIRST_DAY_OF_MONTH, _SECOND_DAY_OF_MONTH),
        _semi_monthly_from_wire, _semi_monthly_to_wire,
    ),
}

#: The token -> kind reading of :data:`_WIRE_OF_KIND`, for the kind field.
_KIND_OF_TOKEN = {wire.token: kind for kind, wire in _WIRE_OF_KIND.items()}

#: Every key a rhythm occupies on the wire: the kind control, each arm's
#: inputs, and the convention.  What :class:`RhythmFormMixin`'s
#: ``@post_load`` consumes when it puts the value on the payload.
RHYTHM_WIRE_KEYS = frozenset({"cadence_kind", "shift"}).union(
    *(wire.controls for wire in _WIRE_OF_KIND.values())
)


def cadence_kind_token(kind) -> str:
    """Return what the kind control posts for *kind*.

    The templates' one reading of the wire vocabulary, registered as Jinja
    globals by ``jinja_globals.register_pay_calendar_bound_globals`` so a
    radio arm's ``value=`` and the token the schema maps are one spelling.

    Args:
        kind: A cadence class.

    Returns:
        The token.
    """
    return _WIRE_OF_KIND[kind].token


def rhythm_from_wire(data) -> Rhythm:
    """Return the :class:`~app.services.pay_rhythm.Rhythm` a payload states.

    **The ONE place the wire becomes the value**, called by
    :func:`validate_derivable_rhythm` to judge it and by the mixin's
    ``@post_load`` to hand it back; the four routes never spell a cadence's
    constructor.  Reads the chosen kind's arm and no other's: every arm's
    inputs are rendered and submitted, so the ones the owner did not choose
    carry a blank, a stale number or the stored rhythm's value, and none of
    it reaches the value.  Each box's own RANGE still applies whichever arm
    is chosen -- it is the field's validator, asked before any of this runs
    -- as the browser's ``min`` / ``max`` apply to every rendered box.

    Args:
        data: The deserialized payload, carrying ``cadence_kind`` (already
            a class) and ``shift`` (already a member); the callers check
            both are present.

    Returns:
        The rhythm.

    Raises:
        ValidationError: Marshmallow's, attributed to the chosen arm's blank
            input.
    """
    cadence = _WIRE_OF_KIND[data["cadence_kind"]].from_wire(data)
    return Rhythm(cadence=cadence, shift=data["shift"])


def rhythm_to_wire(rhythm: Rhythm) -> dict:
    """Return *rhythm* as the wire keys the four doors' forms post.

    The inverse of :func:`rhythm_from_wire`, for the two places a rhythm is
    rendered BACK into a form: the regenerate door's discard-confirm banner
    re-posts what was submitted as hidden inputs, and the manage card
    preselects the owner's stored rhythm so a regenerate run for an
    unrelated reason cannot silently restate it (the argument the
    convention select already makes).  The convention goes back to its
    ``ref.business_day_shifts`` id, because that is what the select posts
    and what :class:`BusinessDayShiftField` maps.

    Args:
        rhythm: The value.

    Returns:
        ``{"cadence_kind": token, <the kind's arm's inputs>, "shift": id}``.
    """
    wire = _WIRE_OF_KIND[type(rhythm.cadence)]
    return {
        "cadence_kind": wire.token,
        **wire.to_wire(rhythm.cadence),
        "shift": ref_cache.business_day_shift_id(rhythm.shift),
    }


def validate_derivable_rhythm(data, payday_control: str):
    """Refuse a rhythm and first payday no pay calendar can derive from.

    The cross-field half of every schedule form's validation, shared by the
    four doors that state a rhythm so the rule is worded once -- the same
    placement, and the same reason, as
    :func:`~app.schemas.validation._recurrence.validate_authorable_cadence`.
    Three rules, none a property of one field alone: whether a semi-monthly
    PAIR of days is legal, whether the stated first payday lies on the
    stated rhythm's grid, and whether the convention can be carried -- a
    two-day cadence is ordinary, ``prior`` is ordinary, and together they
    displace two paydays onto one day that ``pay_calendar.derive_periods``
    refuses outright.

    **The two service imports are top-level.**  This module imports
    ``pay_schedule_service`` and ``pay_era_write`` directly (the header's
    import block), neither imports the schema package, and importing this
    module alone raises no cycle -- measured at the split, 2026-09-14.  *The
    paragraph that stood here until the split argued the same conclusion
    from ``pay_periods``' import of the batch pair, which this module does
    not make; an adversarial review of ``C17-d-3`` caught the carried-over
    premise.*  The rule it restated still holds: a ``pylint`` disable whose
    stated reason is false is worse than none, because
    ``shekel-disable-rationale`` then certifies a sentence nobody re-checked.

    **It does not restate the rules, it ASKS them.**  The floor is derived
    from the federal holiday calendar, the grid question is the grid's own
    round trip, and each refusal belongs to the write door, so this calls
    that door's three predicates in the order ``registration_service`` and
    ``pay_period_write`` ask them and converts each refusal into a field
    error.  Two spellings of a bound are two chances to disagree, which is
    what the cadence range's own history in this module records.

    **The field each names is the point of the function.**  Without it a
    refusal reaches ``routes/pay_periods.py``'s ``except ValidationError``
    handler, which renders every message it catches under ``start_date`` -- a
    cadence-and-convention complaint appearing beneath "First Payday".  That
    handler's comment predicted this exact failure in advance ("Widen either
    field and this line starts rendering a cadence message under the date
    box"); answering here is what keeps its attribution provable.  Plan step
    ``pay_calendar:C17-d-3`` added the grid question, whose control IS the
    payday box on every door -- named per door, since the four spell it
    ``start_date``, ``new_start_date`` and ``last_payday``.

    Args:
        data: The deserialized form payload of a door carrying
            :class:`RhythmFormMixin`'s fields, so ``cadence_kind`` and
            ``shift`` are present: defaulted or required on every such door,
            and a field error on either skips this rule before it runs.
        payday_control: The wire key of this door's first-payday input.

    Raises:
        ValidationError: Marshmallow's, attributed to the control the owner
            would have to change: the chosen arm's blank input; the arm's
            last input for an illegal pair; the payday box for a first
            payday the rhythm does not pay on; ``shift`` for a convention
            the rhythm cannot carry, since the cadence is usually the fact
            and the convention usually the choice.
    """
    rhythm = rhythm_from_wire(data)
    try:
        pay_schedule_service.reject_out_of_range_cadence(rhythm.cadence)
    except AppValidationError as exc:
        raise ValidationError(
            str(exc), _WIRE_OF_KIND[type(rhythm.cadence)].bound_control,
        ) from exc
    payday = data.get(payday_control)
    if payday is not None:
        try:
            pay_era_write.reject_phase_off_grid(payday, rhythm.cadence)
        except AppValidationError as exc:
            raise ValidationError(str(exc), payday_control) from exc
    try:
        pay_schedule_service.reject_shift_on_short_cadence(rhythm)
    except AppValidationError as exc:
        raise ValidationError(str(exc), "shift") from exc


class RhythmFormMixin:
    """The three hooks every door that states a rhythm runs, declared once.

    Plan step ``pay_calendar:C17-d-3``.  Four schemas -- generate,
    regenerate, reset and registration -- carry the rhythm's wire keys and
    the same three hooks over them; copied four times, a fifth door would
    have none of them, which is the reason
    :class:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin`
    records.  The FIELDS are the two subclasses' below, because their
    per-door half differs: :class:`DefaultedRhythmFields` for the doors that
    ESTABLISH a rhythm and may fall back to the app's premise,
    :class:`RequiredRhythmFields` for the doors that CORRECT one and must
    not.

    Marshmallow collects fields and hooks across the whole MRO, so a plain
    mixin beside :class:`~app.schemas.validation._helpers.BaseSchema` is all
    this needs; it deliberately does NOT subclass ``Schema``.

    Attributes:
        payday_control: The wire key of the door's first-payday input, which
            the grid question is attributed to; each schema names its own.
    """

    payday_control: str

    @pre_load
    def clear_blank_rhythm_inputs(self, data, **kwargs):
        """Map each blank nullable input to an explicit ``None``.

        An HTML form submits every control it renders, so the arms the owner
        did not choose arrive as ``""`` -- and so does an untouched optional
        date such as registration's ``history_opens_on``.
        :func:`~app.schemas.validation._helpers._clear_nullable_empties` is
        what turns those into the ``None`` the fields allow, instead of a
        "Not a valid integer." refusal on a box the owner never used.
        **The NULLABLE half only, never ``_normalize_empty_inputs``**: that
        one also DROPS an empty non-nullable input, which on registration
        turns "Display name is required." into "Missing data for required
        field." (measured, on two suites, at plan step balance:X-bh-2).
        Registration's own ``@pre_load`` strips the credential fields; the
        two commute, so their unstated order is safe.

        Args:
            data: The incoming payload.
            **kwargs: Marshmallow's hook contract.

        Returns:
            The payload with blank nullable inputs set to ``None``.
        """
        return _clear_nullable_empties(self, data)

    @validates_schema
    def validate_rhythm(self, data, **kwargs):
        """Refuse a rhythm no calendar can derive (**R-PC54**).

        On the three pay-period doors this buys field attribution.
        Registration renders no field errors -- ``routes/auth/credentials``
        flashes ``_first_validation_message`` -- so what it buys THERE is
        that all four doors refuse the same rhythms through one predicate
        rather than three through the schema and the fourth through a
        service the schema does not reach (adversarial review, 2026-09-05).

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.
        """
        validate_derivable_rhythm(data, self.payday_control)

    @post_load
    def build_rhythm(self, data, **kwargs):
        """Replace the rhythm's wire keys with the value they state.

        The payload's ``rhythm`` is a :class:`~app.services.pay_rhythm.Rhythm`
        after this, and the kind control, every arm's inputs and the
        convention are CONSUMED rather than left beside it: a route that
        could still read ``cadence_days`` off a monthly submission would
        read ``None`` where it expected a count, which is the two-readings
        state the value exists to remove
        (:func:`~app.schemas.validation._recurrence.compose_end_bound`'s
        precedent).  Runs only after every validator passed, so the build
        cannot refuse here; under ``Schema.validate`` it does not run at
        all, which is why the refusals live in :meth:`validate_rhythm`.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Returns:
            The payload, with ``rhythm`` in place of the wire keys.
        """
        rhythm = rhythm_from_wire(data)
        for key in RHYTHM_WIRE_KEYS:
            data.pop(key, None)
        data["rhythm"] = rhythm
        return data


def _premise_kind() -> type:
    """Return the kind a door that establishes a rhythm assumes when none is posted.

    Every ``DEFAULT_PAY_CADENCE_DAYS`` days, the premise the whole app is
    organised around and what the old single box meant; the day count
    beside it defaults on the same doors.

    Returns:
        :class:`~app.services.pay_rhythm.FixedDays`.
    """
    return FixedDays


class DefaultedRhythmFields(RhythmFormMixin):
    """The rhythm's wire keys for a door that ESTABLISHES a rhythm.

    Generate and registration: an owner who states nothing is read as the
    app's premise -- every ``DEFAULT_PAY_CADENCE_DAYS`` days on the
    scheduled day -- exactly as the old single box defaulted, so an old
    client posting no kind and no day count means what it always meant.
    The three day-of-month inputs carry no default because the arms they
    belong to are not the defaulted one.  Declared once for the two doors
    rather than copied, which is
    :class:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin`'s
    reason: a copy is what a third such door would have neither of, and
    ``duplicate-code`` said so on the first draft.

    **Marshmallow orders a mixin's fields BEFORE the subclass's own**, so on
    registration these six precede the credential fields.  The
    credential-first rule that schema states holds for every error the FORM
    can produce but one: the six are defaulted or nullable, their ranges are
    the browser's ``min`` / ``max``, the radio and select values are fixed
    -- and a number box does accept exponent notation (``1e2`` is inside
    ``min=1 max=365`` to the browser and "Not a valid integer." to
    marshmallow), so an owner who types that beside a bad email is told
    about the number first.  Measured (an adversarial review of
    ``C17-d-3``); accepted, because marshmallow 4 orders inherited fields
    first whatever the base order and has no field-order option left.
    """

    # A CALLABLE, because marshmallow calls a callable ``load_default`` to
    # produce the value and a class is callable: ``load_default=FixedDays``
    # would construct one with no day count.
    cadence_kind = cadence_kind_field(load_default=_premise_kind)
    cadence_days = cadence_days_field(
        load_default=BaseConfig.DEFAULT_PAY_CADENCE_DAYS,
    )
    day_of_month = day_of_month_field()
    first_day_of_month = day_of_month_field()
    second_day_of_month = day_of_month_field()
    shift = shift_field(load_default=BusinessDayShiftEnum.NONE)


class RequiredRhythmFields(RhythmFormMixin):
    """The rhythm's wire keys for a door that CORRECTS a rhythm.

    Regenerate and reset: ``cadence_kind`` and ``shift`` are required
    because these doors persist the new rhythm, and a door that would
    silently restate one half on a missing input must not.  The chosen
    kind's inputs are required by the builder, keyed on the kind
    (:func:`rhythm_from_wire`), since every arm's box is submitted and only
    the chosen arm's may be demanded; ``cadence_days`` therefore carries no
    default here where :class:`DefaultedRhythmFields` gives it the app's.
    """

    cadence_kind = cadence_kind_field(required=True)
    cadence_days = cadence_days_field()
    day_of_month = day_of_month_field()
    first_day_of_month = day_of_month_field()
    second_day_of_month = day_of_month_field()
    shift = shift_field(required=True)
