"""The recurrence controls both template forms submit, and the rules on them.

Everything a form states about HOW OFTEN a definition repeats: the two cadence
axis fields, the cross-field rules over them, the closing bound's
three-controls-into-one-value composition, and the
:class:`RecurrenceFormFieldsMixin` the two template create schemas inherit.

Split out of :mod:`._helpers` at plan step R7c-b, when that module met the
1,000-line cap.  The seam is the one the module list already implied: `_helpers`
holds the primitives EVERY domain schema in this package needs -- the base
schema, the shared range validators, the percent-to-fraction hook, the
:class:`~app.schemas.validation._helpers.RowId` field and the
:class:`~app.schemas.validation._helpers._RefEnumField` base -- while this holds
one DOMAIN's shared form.  Nothing outside the two template schemas imports it.

**Two of the rules here are one half of a pair**, and each says so in its own
docstring, because the layer that holds the other half cannot be reached from a
schema: a schema never sees the template being edited, so it cannot know whether
an update AUTHORS a rule (``RECURRENCE_NEEDS_A_START``) or what the STORED half
of a validity window is (:func:`end_bound_before_start_message`).  Both name the
route helper that completes them.
"""


from datetime import date

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    EFFECTIVE_DATE_MAX,
    EFFECTIVE_DATE_MIN,
    _RefEnumField,
)


#: The field error for a chosen cadence with no first occurrence beside it.
#:
#: Named here because TWO layers raise it and they must say the same thing: the
#: schema, for a CREATE (see
#: :meth:`RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`), and
#: ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` for the one
#: UPDATE branch that also authors a rule -- adding a cadence to a template
#: that had none.  The schema cannot tell those apart, because it never sees
#: the template.
RECURRENCE_NEEDS_A_START: dict[str, list[str]] = {
    "starts_on": [
        "Choose the date this first happens.  It is what the recurrence "
        "repeats from, so nothing is generated before it.",
    ],
}

# The largest occurrence count ``budget.recurrence_rules.max_occurrences`` can
# hold: it is a Postgres ``integer``, so a larger value dies at the DATABASE
# with ``psycopg2.errors.NumericValueOutOfRange`` -- an unhandled 500 on a door
# an ordinary crafted POST reaches, which is the ``MarkDoneSchema`` defect the
# monetary bound above records.  A schema-tier bound AT the column's domain is
# what keeps an unstorable count a designed 400.
#
# There is deliberately NO lower bound beside it (plan step R7b-3).  "A count
# names at least one occurrence" is
# :class:`~app.services.recurrence.EndsAfterOccurrences`'s own invariant, held
# at construction where no path can miss it; stating it here as well would put
# one rule in two places AND take the refusal away from the shape, whose
# message names the control and says what to type.
_MAX_OCCURRENCE_COUNT = 2147483647


class RecurrenceUnitField(_RefEnumField):
    """A submitted ``ref.recurrence_units`` id, as its enum member.

    The first of the two axes a recurrence form authors since plan step R7b-2:
    what ``interval_n`` counts.  It replaced ``RecurrencePatternField``, which
    validated the closed pattern set the form used to post -- that set is now
    an ENCODING the write door chooses, never a thing a user picks.

    Whether the cadence this unit belongs to can be stored at all is a property
    of the ``(interval, unit, placement)`` triple rather than of any one field,
    so it is checked by :func:`validate_authorable_cadence` and not here.
    """

    _invalid_message = "Invalid repeat unit."

    def _member_for(self, row_id):
        """Return the :class:`~app.enums.RecurrenceUnitEnum` member, or ``None``.

        Args:
            row_id: A validated ``ref.recurrence_units`` id.

        Returns:
            The member, or ``None`` when unmodelled.
        """
        # Pylint: ``import-outside-toplevel`` -- deferred so this shared schema
        # helper, which every domain module imports, does not pull the
        # recurrence service package in at import time for the two schemas that
        # need it.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            modelled_unit,
        )

        return modelled_unit(row_id)


class PeriodPlacementField(_RefEnumField):
    """A submitted ``ref.period_placements`` id, as its enum member.

    The second axis a recurrence form authors: which pay period funds an
    occurrence.  See :class:`RecurrenceUnitField` for why the storable-set
    question is not asked here.
    """

    _invalid_message = "Invalid funding choice."

    def _member_for(self, row_id):
        """Return the :class:`~app.enums.PeriodPlacementEnum` member, or ``None``.

        Args:
            row_id: A validated ``ref.period_placements`` id.

        Returns:
            The member, or ``None`` when unmodelled.
        """
        # Pylint: ``import-outside-toplevel`` -- see
        # :meth:`RecurrenceUnitField._member_for`.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            modelled_placement,
        )

        return modelled_placement(row_id)


def validate_authorable_cadence(data):
    """Refuse a submitted cadence the application cannot STORE.

    The cross-field half of the recurrence form's validation, shared by the
    transaction-template and transfer-template schemas so the rule is stated
    once.  Whether a reading can be written is a property of the whole
    ``(interval, unit, placement)`` triple and of no single field: until plan
    step R7c the cadence is stored as a closed-set pattern id, and that set
    covers every N pay periods but only 1, 3 or 6 months, and pairs the
    first-paycheck placement with a ONE-month interval only.

    **This is the door's copy of a rule the FORM already makes unreachable.**
    Plan step R7b-2 serves the picker's options from
    :func:`~app.services.recurrence.cadence_options`, derived from the same
    table, so no combination a user can assemble by clicking arrives here
    refused.  It is what a hand-assembled POST meets, and it is why the write
    door's :class:`~app.services.recurrence.RecurrenceResolutionError` stays a
    broken-invariant 500 rather than becoming a user-facing path.

    Skipped when NO unit is named: "does not repeat" is the absence of a
    cadence, and a partial update that omits the recurrence keys leaves the
    stored one alone.

    **A named unit with no placement is REFUSED rather than skipped**, and two
    independent adversarial reviews of plan step R7b-2 found the same 500 in
    the version that skipped it.  The two axes are halves of one value, not two
    optional refinements: both write doors pop the placement with no default,
    so half a cadence reached ``build_recurrence_rule_from_form`` as an
    unhandled ``KeyError``.  Its EMPTY spelling was worse -- the field is
    ``allow_none``, so :func:`_normalize_empty_inputs` keeps the key with a
    present ``None``, marshmallow never deserializes it, and the route built a
    spec with ``placement=None`` that RESOLVED (the ``PERIOD`` unit's anchor
    does not read the placement) and then died inside ``encode_cadence``.  That
    is precisely the refusal this function exists to turn into a field error,
    arriving as a 500 instead.

    It is defect **D13**'s shape one field over, and the transfer route's own
    docstring records that one: ``recurrence_pattern`` was ``allow_none`` while
    a comment claimed it required, so an omitted key 500'd there too.  Refusing
    the pair HERE rather than defaulting the placement in the route is what
    stops a rule being authored from a cadence the user only half stated -- a
    default would pick which paycheck pays a bill on the user's behalf.

    Args:
        data: The deserialized schema payload.

    Raises:
        ValidationError: A unit is named with no placement, or the submitted
            triple has no closed-set pattern to be stored as.
    """
    # Pylint: ``import-outside-toplevel`` -- see
    # :meth:`RecurrenceUnitField._member_for`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        is_authorable,
    )

    unit = data.get("recurrence_unit")
    if unit is None:
        return
    placement = data.get("recurrence_placement")
    if placement is None:
        raise ValidationError(
            "Choose which paycheck funds each occurrence.",
            field_name="recurrence_placement",
        )
    if not is_authorable(data.get("interval_n", 1), unit, placement):
        raise ValidationError(
            "That repeat schedule cannot be saved yet. Pick a different "
            "interval or a different funding choice.",
            field_name="recurrence_unit",
        )


#: The key a form posts its closing-bound SHAPE under, and the key the
#: composed bound is handed to the route under.
#:
#: They are the same field: :func:`compose_end_bound` replaces the submitted
#: token with the value it names, the way
#: :class:`RecurrenceUnitField` replaces a submitted id with its enum member.
#: One key rather than two is what keeps "the form said nothing about the
#: bound" expressible -- an ABSENT key, which a loan payment's form and an
#: amount-only PATCH both produce, and which the update route reads as "leave
#: the stored bound alone".
#: The rule's FIRST OCCURRENCE, as the payload spells it, and the day a clamped
#: one MEANT.
#:
#: Named here for the reason :data:`RECURRENCE_END_BOUND_KEY` is: the route
#: helpers read these keys' PRESENCE, not just their values, and presence is
#: how a form says "not mine to state" -- so the string has to be the same one
#: the field is declared under or a locked control's silence reads as a stated
#: ``None``.  Three route modules and the schema spelled it four times between
#: them before plan step R7c-b named it, which is three chances for a rename to
#: half-land.
#:
#: The two are one constant apart rather than one value because the payload has
#: two keys; they RIDE TOGETHER on the start's presence, which
#: ``update_recurrence_rule_from_form`` is where that rule is written.
RECURRENCE_STARTS_ON_KEY: str = "starts_on"
RECURRENCE_NOMINAL_DAY_KEY: str = "nominal_day"


RECURRENCE_END_BOUND_KEY: str = "recurrence_end_mode"


def compose_end_bound(data):
    """Replace the submitted bound token with the :class:`EndBound` it names.

    The closing bound crosses the wire as THREE controls -- a mode select and
    the two inputs its shapes need -- and is ONE value everywhere above this
    line.  Composing it here rather than in the route is the placement
    :class:`_RefEnumField` records for the axis fields: the schema is the
    boundary between the wire format and the logic value, so a second form
    door cannot forget the conversion.

    **The two payload inputs are consumed, not left beside the result.**  A
    route that could still read ``end_date`` would be able to write a bound the
    mode did not name, which is the two-independent-fields reading
    :class:`~app.services.recurrence.EndBound` exists to remove.

    **An ABSENT mode leaves the payload untouched**, and the distinction is
    load-bearing rather than tidy.  "The form did not mention the bound" is a
    real request -- a loan payment's form, whose bound is DERIVED from the loan
    and whose controls are therefore disabled, and any partial update -- and it
    must not read as "ends never", which would silently clear a stop the user
    set.  It is the same present-``None``-versus-absent distinction
    ``recurrence_unit`` already turns on.

    Args:
        data: The deserialized schema payload, mutated in place.

    Returns:
        The same payload, so a ``@post_load`` hook can hand it straight back.

    Raises:
        ValidationError: The mode names no shape, or the shape it names needs
            an input the submission left blank.  Raised against the CONTROL at
            fault -- the empty date box, not the mode select the user answered
            correctly -- which is what
            :class:`~app.services.recurrence.EndBoundInputError` carries the
            field for.
    """
    # Pylint: ``import-outside-toplevel`` -- see
    # :meth:`RecurrenceUnitField._member_for`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        EndBoundInputError,
        end_bound_from_token,
    )

    if RECURRENCE_END_BOUND_KEY not in data:
        return data
    token = data.pop(RECURRENCE_END_BOUND_KEY)
    end_date = data.pop("end_date", None)
    max_occurrences = data.pop("max_occurrences", None)
    try:
        data[RECURRENCE_END_BOUND_KEY] = end_bound_from_token(
            token, end_date=end_date, max_occurrences=max_occurrences,
        )
    except EndBoundInputError as exc:
        raise ValidationError(exc.message, field_name=exc.field) from exc
    return data


def end_bound_before_start_message(end_date: date, starts_on: date) -> str:
    """Return the refusal for a closing bound that precedes the first occurrence.

    **Named because TWO doors raise it and they must say the same thing**, the
    same reason :data:`RECURRENCE_NEEDS_A_START` is named: this schema, for a
    submission that states both values, and
    ``_recurrence_form_refusals.refuse_inverted_window`` for an UPDATE, where
    either value may be the STORED one and no schema can see the pair.

    There is no ``ck_recurrence_rules_valid_window`` behind them.  Plan step
    R7c-b held that CHECK back on a developer ruling: the columns carry
    user-authored windows AND derived loan-payment ones, and an empty DERIVED
    window is a correct answer that a CHECK cannot tell from a user's mistake.
    So these two doors are the whole of the rule, which is why the update one
    exists at all rather than being left to a backstop.

    Args:
        end_date: The stated closing date.
        starts_on: The rule's first occurrence.

    Returns:
        The refusal sentence, naming both dates.
    """
    return (
        f"This stops repeating on {end_date:%b %-d, %Y}, before it first "
        f"happens on {starts_on:%b %-d, %Y}.  A recurrence that ends before it "
        f"starts names no occurrence at all."
    )


def require_end_bound_after_start(data):
    """Reject a closing bound that falls before the rule's first occurrence.

    **The CREATE door's half of the rule**, where the submission states both
    values and the pair is a two-field comparison a schema can refuse without a
    calendar.  ``end_date`` is live and user-authored, so an unrefused
    violation would reach the flush -- the user could not stop a recurring bill
    and the projection would keep charging it.
    ``_recurrence_form_refusals.refuse_inverted_window`` is the UPDATE door's
    half, and it is not redundant: on an update either value may be the stored
    one, which this cannot see.

    **Read off the COMPOSED bound, not off the ``end_date`` control**, and the
    difference is a false refusal.  The three "Ends" controls are one value with
    three shapes (plan step R7b-3): a submission whose mode is "Never" carries
    no date whatever the date input holds, so comparing the raw key would refuse
    a stale value the rule will never store.  Asking the value keeps "which
    input does this shape read" stated once, in
    :class:`~app.services.recurrence.EndBound`.

    Runs only when the payload states BOTH.  A form that stated no bound leaves
    the stored one alone (a loan payment's disabled control, an amount-only
    PATCH), and a stored pair was checked when it was written.

    Args:
        data: The payload, AFTER :func:`compose_end_bound`.

    Returns:
        None.

    Raises:
        ValidationError: The stated end date precedes the first occurrence.
    """
    starts_on = data.get("starts_on")
    bound = data.get(RECURRENCE_END_BOUND_KEY)
    if starts_on is None or bound is None:
        return
    end_date = bound.columns().end_date
    if end_date is None or end_date >= starts_on:
        return
    raise ValidationError(
        end_bound_before_start_message(end_date, starts_on),
        field_name="end_date",
    )


class RecurrenceFormFieldsMixin:
    """The recurrence controls both template forms submit, declared once.

    A form authors a recurrence the same way whichever kind of definition it
    belongs to -- the cadence's two axes, the calendar coordinates, the opening
    bound and the closing one -- so the two template schemas
    (``TemplateCreateSchema``, ``TransferTemplateCreateSchema``) carried SEVEN
    identical field declarations and an identical cross-field cadence rule.
    Plan step R7b-3 would have made it nine and added an identical
    ``@post_load`` to each, which is when ``duplicate-code`` said so and the
    copy stopped being worth keeping.  They differ in exactly ONE field:
    ``due_day_of_month``, which only a transaction template carries, and which
    that schema declares for itself.

    **Declared here rather than copied because a copy is what a THIRD form
    would have neither of**, which is the same reasoning
    :class:`_RefEnumField` records for putting the modelled-value check in the
    field type: a rule that travels with the declaration cannot be forgotten.
    Plan step R7b-3 is what made the duplication worth removing -- it added the
    closing bound's three controls and its hook to both, and ``duplicate-code``
    caught the pair.

    Marshmallow collects fields and hooks across the whole MRO, so a plain
    mixin beside :class:`BaseSchema` is all this needs; it deliberately does
    NOT subclass ``Schema``, which would make it a schema in its own right and
    invite it to be loaded.
    """

    # The two AUTHORED cadence axes since plan step R7b-2.  Each value is the
    # integer primary key of a ref row (recurrence_units, period_placements),
    # submitted as a string via HTML form data and deserialized to the ENUM
    # member it names.  The typed fields rather than bare ``RowId``s: they also
    # refuse an id no enum member names -- what the application MODELS is
    # narrower than what a table HOLDS, and the gap is a 500 (plan step R2e-2).
    #
    # ``RowId`` underneath rather than ``Integer`` because these ARE row ids
    # despite their names (plan step X-ae): an adversarial review found
    # ``Integer`` reading '١', ' 2 ', '+3', '007' and '1_0' as ids, and the
    # completeness gate could not see it while that gate matched on a ``_id``
    # SUFFIX.
    #
    # They replaced ``recurrence_pattern``: the closed pattern set is now the
    # STORAGE encoding the write door chooses, not a name a user picks.
    #
    # ``allow_none`` so the form's "Does not repeat" option survives the
    # pre_load hook as an explicit ``None`` rather than a dropped key (plan step
    # R2e-1).  The two are different requests and the update route acts on them
    # differently -- a present ``None`` CLEARS the recurrence, an absent key
    # leaves it alone -- so collapsing them would make an amount-only PATCH
    # silently delete a template's cadence.
    #
    # ``offset_periods`` is GONE (defect D8).  It was a vestigial field no
    # template ever rendered an input for, so every submission carried the
    # schema default -- which the update path then wrote over the rule's real
    # phase.  ``resolve`` derives the phase from the rule's opening bound.
    recurrence_unit = RecurrenceUnitField(allow_none=True)
    recurrence_placement = PeriodPlacementField(allow_none=True)
    interval_n = fields.Integer(validate=validate.Range(min=1))

    # The rule's FIRST OCCURRENCE (plan step R7c-b, ruling **R-R16**).  It
    # replaced THREE fields: ``day_of_month`` (the cycle's day),
    # ``month_of_year`` (its residue class) and ``start_date`` (the opening
    # bound) -- because the first occurrence carries all three, being the
    # earliest thing the cadence produces and the member of the cycle its
    # position defines.
    #
    # **The key is ``starts_on`` and not the ``start_date`` it replaced, and
    # renaming it is the point.**  The two mean different things -- a bound
    # against an occurrence -- so a page cached from before this deploy posts a
    # key nothing reads and the submission fails the required-together rule
    # below rather than silently authoring a rule whose start means something
    # the user did not say.
    #
    # NOT ``required``: a submission that names no cadence
    # (``recurrence_unit`` empty, the form's "Does not repeat") authors no rule
    # at all, and a partial update that omits every recurrence key must stay a
    # partial update.  What makes it required WHEN A CADENCE IS CHOSEN is
    # :meth:`validate_recurrence_states_a_start` below, which is the only
    # layer that can see both fields.
    #
    # Bounded for the reason ``effective_from`` is bounded on both template
    # schemas: an ``<input type="date">`` accepts a four- or five-digit-year
    # typo, and here the consequence is worse than a bad version date -- a
    # first occurrence past the horizon generates NOTHING, silently.
    starts_on = fields.Date(
        validate=validate.Range(
            min=EFFECTIVE_DATE_MIN, max=EFFECTIVE_DATE_MAX,
        ),
    )

    # The day the rule MEANS when ``starts_on``'s own month was too short to
    # hold it, and ``None`` -- which is every ordinary rule -- when the date
    # holds the day (ruling R-R3).  The form renders the control that posts it
    # ONLY where the chosen date leaves the question open: a date that is its
    # month's last day in a month shorter than 31 days could be "the 28th" or
    # "the last day of the month", and those are different cadences from the
    # following month on.
    #
    # 29-31, matching ``ck_recurrence_rules_nominal_day`` exactly: every month
    # holds its first 28 days, so a smaller value would be a second statement
    # of the day ``starts_on`` already carries.  The rest of that CHECK -- that
    # the date's own day IS the clamp of this value -- is a two-field rule the
    # column cannot express alone and
    # :class:`~app.services.recurrence.RecurrenceSpec` refuses at construction.
    nominal_day = fields.Integer(
        allow_none=True, validate=validate.Range(min=29, max=31),
    )

    # The CLOSING BOUND, as three controls that compose into ONE value (plan
    # step R7b-3).  ``recurrence_end_mode`` names which of the bound's three
    # shapes the user chose and :meth:`build_end_bound` replaces it with the
    # :class:`~app.services.recurrence.EndBound` that shape builds, consuming
    # the two inputs beside it -- the same wire-format-to-logic-value
    # conversion :class:`RecurrenceUnitField` performs one field up.
    #
    # ``max_occurrences`` carries only an UPPER bound here, and the asymmetry
    # is the point.  "At least one occurrence" is the SHAPE's invariant
    # (``EndsAfterOccurrences.__post_init__``), held where no path can miss it
    # and refused with a message that names the control; repeating it as a
    # ``min=`` would put one rule in two places and hand the user marshmallow's
    # generic wording instead.  What the shape has no opinion about is how
    # large a count the COLUMN can hold, so that bound is here -- see
    # :data:`_MAX_OCCURRENCE_COUNT`.
    #
    # No ``allow_none`` on the mode, deliberately.  An empty select value is
    # dropped by :func:`_normalize_empty_inputs`, so it arrives ABSENT -- and
    # an absent mode is what "this form said nothing about the bound" means (a
    # loan payment, whose bound is derived; an amount-only PATCH).  Keeping it
    # as a present ``None`` would make those requests indistinguishable from
    # "ends never", which silently clears a stop the user set.
    #
    # ``end_date`` stays ``allow_none`` because clearing a date input has to
    # reach the schema as a stated empty rather than a dropped key.
    recurrence_end_mode = fields.String()
    end_date = fields.Date(allow_none=True)
    max_occurrences = fields.Integer(
        validate=validate.Range(max=_MAX_OCCURRENCE_COUNT),
    )

    @validates_schema
    def validate_cadence_is_storable(self, data, **kwargs):
        """Reject a submitted cadence the closed pattern set cannot store.

        See :func:`validate_authorable_cadence` for the reasoning: the
        storable set is a property of the whole ``(interval, unit,
        placement)`` triple, and the form already makes an unstorable one
        unofferable, so this is what a hand-assembled POST meets.

        The update schemas inherit it; a partial update that omits the
        recurrence keys returns early there for the same reason the envelope
        rule does.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: The triple has no closed-set pattern to be stored
                as.
        """
        validate_authorable_cadence(data)

    #: Whether a chosen cadence on THIS schema must come with a first
    #: occurrence.
    #:
    #: ``True`` on the create schemas, where a submission always authors a new
    #: rule; ``False`` on the update schemas, where it usually re-points an
    #: existing one and where an omitted key means "leave the stored value
    #: alone".  See :meth:`validate_recurrence_states_a_start`.
    recurrence_start_is_required: bool = True

    @validates_schema
    def validate_nominal_day_fits_the_start(self, data, **kwargs):
        """Reject a nominal day the submitted first occurrence leaves no room for.

        **The submission's half of ``ck_recurrence_rules_nominal_day``**, and
        the reason the field's own ``Range(29, 31)`` is not enough: the CHECK
        has three conjuncts and the domain is only one of them.  The others are
        a two-field rule -- the day must EXCEED the date's own, and the date's
        own day must be exactly what clamping the day into that month produces
        -- so no single field can hold them and a schema that mirrors the
        domain alone lets the pair through.

        ``starts_on=2026-04-15&nominal_day=30`` is the shape: 30 is in the
        domain, April 15 was never clamped by anything, and the pair reached
        :class:`~app.services.recurrence.RecurrenceSpec`, which refuses it as a
        BROKEN INVARIANT -- an unhandled 500 rather than a field error naming
        the control.  Asked through
        :func:`~app.services.recurrence.is_offerable_nominal_day`, which exists
        for exactly this and had no production caller until now, so the door
        and the write door cannot disagree about which pairs are admissible.

        Skipped unless the submission states all three.  A form that names no
        cadence authors no rule; one that omits ``starts_on`` leaves the stored
        date alone, and the update door then ignores a submitted nominal day
        with it (both ride on ONE presence key, because they are one statement
        of when the rule fires).

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: The pair contradicts itself.
        """
        # Pylint: ``import-outside-toplevel`` -- see
        # :meth:`RecurrenceUnitField._member_for`.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            is_offerable_nominal_day,
            offerable_nominal_days,
        )

        unit = data.get("recurrence_unit")
        starts_on = data.get("starts_on")
        nominal_day = data.get("nominal_day")
        if unit is None or starts_on is None or nominal_day is None:
            return
        if is_offerable_nominal_day(unit, starts_on, nominal_day):
            return
        offerable = offerable_nominal_days(unit, starts_on)
        raise ValidationError(
            f"{starts_on:%b %-d, %Y} cannot mean day {nominal_day}. "
            + (
                f"Starting on that date, this can only repeat on "
                f"{' or '.join(str(day) for day in offerable)}."
                if offerable else
                "That date already says which day this repeats on, so there "
                "is nothing else for it to mean."
            ),
            field_name="nominal_day",
        )

    @validates_schema
    def validate_recurrence_states_a_start(self, data, **kwargs):
        """Reject a chosen cadence with no first occurrence beside it.

        ``budget.recurrence_rules.starts_on`` is ``NOT NULL`` from plan step
        R7c-b, so a rule cannot be authored without one -- and the value is not
        merely a required field, it is the whole of what the rule says about
        when it begins.  Without this the omission would reach the write door
        as a ``RecurrenceResolutionError``, which routes turn into a flash
        rather than a field error.

        **A money decision, not a typing one.**  An empty opening bound used to
        mean "start with the schedule", and the create routes generate with no
        lower window bound -- so a ``$2,000.00`` rent template created today
        wrote five backdated rows into pay periods that had already closed
        (measured at plan step R7b-4).  A required first occurrence makes that
        state unreachable rather than defended against.

        **It applies to a CREATE and not to an UPDATE** (developer ruling
        2026-08-15), which is the split :attr:`recurrence_start_is_required`
        carries.  The harm above is a create's: an update edits a rule that
        already HAS a start, so an unstated one leaves that value alone and
        backdates nothing.  Requiring it on both is what made a loan payment's
        locked control unsubmittable -- the app derives that bound, so its form
        may not state one, and the only ways to satisfy a required field there
        were to post the derived value back (making "not mine to state"
        indistinguishable from a deliberate choice) or to lock the user out of
        renaming their own loan payment.

        An update that adds a cadence to a template which had none DOES author
        a rule, and the schema cannot see that -- it has no template.  The
        route can, and
        ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` refuses
        it there with the same message.

        Both fields are read from the payload rather than from the schema's
        ``required``, because the rule is CONDITIONAL: a submission naming no
        cadence authors no rule, and a partial update that omits every
        recurrence key must stay a partial update.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: A cadence was chosen and no ``starts_on`` given.
        """
        if not self.recurrence_start_is_required:
            return
        if data.get("recurrence_unit") is None:
            return
        if data.get("starts_on") is not None:
            return
        raise ValidationError(RECURRENCE_NEEDS_A_START)

    @post_load
    def build_end_bound(self, data, **kwargs):
        """Replace the submitted bound token with the value it names.

        See :func:`compose_end_bound`, which carries the reasoning, and
        :func:`require_end_bound_after_start` for the window rule checked once
        the bound is a value rather than three controls.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Returns:
            The payload, with the three bound controls collapsed into one
            ``recurrence_end_mode`` entry holding an
            :class:`~app.services.recurrence.EndBound`.

        Raises:
            ValidationError: See :func:`require_end_bound_after_start`.
        """
        composed = compose_end_bound(data)
        require_end_bound_after_start(composed)
        return composed
