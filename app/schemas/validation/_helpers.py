"""Shared validation primitives.

The base schema (CSRF-stripping ``unknown = EXCLUDE`` policy), the
shared range validators, the percent-to-fraction ``@pre_load`` helper
(E-28 / HIGH-06), the :class:`RowId` field, and the cross-schema
envelope-on-income rule.  Every domain module in this package imports its
base and helpers from here so the percent-conversion, monetary-range and
submitted-id rules have a single home."""


from datetime import date
from decimal import Decimal, InvalidOperation

from marshmallow import (
    Schema,
    fields,
    validate,
    ValidationError,
    EXCLUDE,
)

from app import ref_cache
from app.utils.digit_strings import MIN_ROW_ID, parse_row_id


# ── Shared range validators (commit C-24) ─────────────────────────
#
# These constants centralise the percent-format and monetary range
# rules used across more than one schema below.  Validator instances
# are immutable for the parameter set they were constructed with, so
# a single shared instance per pattern is safe; if two fields need
# different bounds (e.g. raise percentage vs FICA rate), declare a
# second constant rather than mutating an existing one.

# Percent input that maps to a decimal fraction in storage: 0..100
# percent inclusive (e.g. user-entered "6.2" for a 6.2% rate, route
# divides by 100 before persistence).  Used by FICA, state flat-rate,
# loan interest, escrow inflation, default inflation, etc.
_PERCENT_INPUT_RANGE = validate.Range(
    min=Decimal("0"), max=Decimal("100"),
)

# The app's range for a non-negative money INPUT, where the DB CHECK is
# ``>= 0``.  10,000,000 is generous: it accommodates very large W-4
# adjustments while still rejecting an obvious typo (extra digit) on
# a routine entry.  Columns are ``Numeric(12, 2)`` so the database
# can hold up to ~10B; this validator caps the schema layer well
# below that.
#
# **The upper bound is not decoration, and plan step X-f2-c3 measured what
# omitting it costs.**  ``MarkDoneSchema`` carried the ``>= 0`` half alone, so a
# figure at or above ``10 ** 10`` passed validation, reached the settle verb and
# died at the DATABASE (``psycopg2.errors.NumericValueOutOfRange``) -- an
# unhandled 500 on a door an ordinary crafted POST reaches.  On the reconcile
# panel that door commits a whole statement walk at once, so one unstorable box
# discarded every other tick submitted with it.  A schema-tier bound BELOW the
# column's domain is what keeps an unstorable figure a designed 400.
#
# **50 of this package's 104 ``fields.Decimal`` declarations still state a lower
# bound and no upper one** (AST census 2026-08-12, counting the shared
# constants above as bounds): 14 in ``salary``, 11 in ``loans``, 6 each in
# ``savings`` and ``transactions``, 4 in ``transfers``, and 7 across five more.
# That class is ledger finding **N-256**; it is recorded rather than swept here,
# because a sweep across loans, templates, entries, settings and salary is a
# large unrelated diff and this step's own pull request moves money (ruling
# **R-EY**).
_NON_NEGATIVE_MONETARY = validate.Range(
    min=Decimal("0"), max=Decimal("10000000"),
)

# The window a recurring definition's amount may be stated to take effect in
# (plan step X-au-a).  An HTML date input accepts a four-digit-year typo, and
# ``budget.template_amount_versions`` STORES the date -- a stray ``0202`` becomes
# the series' earliest version, which anchors every date before the series and
# which the withdrawal door refuses to remove.  The bounds are the ones
# ``routes/salary/tax_config.py`` already uses for a tax YEAR, so the app states
# one opinion about how far its calendar reaches, and
# ``ck_template_amount_versions_effective_date_range`` mirrors them in the
# database for writers that never see a schema.
EFFECTIVE_DATE_MIN: date = date(2000, 1, 1)
EFFECTIVE_DATE_MAX: date = date(2100, 12, 31)


# E-28 / HIGH-06 (Commit 24): the percent-to-fraction divisor used by
# schemas whose form input is a percent (e.g. "4.5" for 4.5%) but whose
# storage column is the equivalent decimal fraction (Decimal("0.045")).
# Defined once here so a future tweak to the convention (no realistic
# scenario) lands in one place.
_PERCENT_DIVISOR = Decimal("100")


def _normalize_empty_inputs(schema, data):
    """Drop empty-string inputs; map them to ``None`` for nullable fields.

    HTML forms submit every rendered control, so an untouched optional
    input arrives as ``""``.  For a field that is not ``allow_none``
    that means "not provided": the key is dropped so ``load_default``
    and partial-update semantics apply.  For an ``allow_none`` field
    the form's empty value IS the null state (a "-- None --" select, a
    cleared date or number input), so the key is kept with an explicit
    ``None``.  Dropping those too made every nullable field unclearable
    from the UI: update routes apply only the keys present in the
    loaded payload, so the user's clear was a silent no-op (the
    deep-hunt pension salary-unlink follow-up; same class at the
    transfer category, deduction target-account, and date/notes
    clears).

    **It carried an ``and not field.dump_only`` arm until plan step X-f1c**
    (finding **N-184**).  That arm existed for one field,
    ``TransactionUpdateSchema.paid_at``, which ruling R-EC deleted with the
    column it named; no schema in this package declares a ``dump_only`` field
    now, and the settle-day door X-f1c added LOADS.  Worse than merely
    instance-less, the branch was UNFALSIFIABLE downstream of ``load()``:
    marshmallow discards a ``dump_only`` key either way, so a case routed
    through ``load()`` passed identically with the arm deleted -- which is how
    it survived a repair pass and a mutation test.  A guard whose only possible
    test cannot fail is not a guard.

    Args:
        schema: the schema instance (``self`` inside a ``@pre_load``
            hook); nullability is read from ``schema.fields``.
        data: the incoming ``@pre_load`` payload (a mapping).

    Returns:
        A new dict with each ``""`` value dropped or mapped to ``None``
        as above; non-empty values pass through unchanged.  Keys not
        declared on the schema (e.g. ``csrf_token``) are dropped when
        empty, exactly as before.
    """
    cleaned = {}
    for key, value in data.items():
        if value != "":
            cleaned[key] = value
            continue
        field = schema.fields.get(key)
        if field is not None and field.allow_none:
            cleaned[key] = None
    return cleaned


def _normalize_percent_fields(data, field_names):
    """Divide each named percent field in ``data`` by 100 in place.

    Used inside a schema's ``@pre_load`` to bridge the form input
    (user-facing percent like ``"4.5"``) and the storage representation
    (decimal fraction like ``"0.045"``) so the schema's ``Range``
    validator operates in the same domain as the DB ``CHECK``
    constraint (E-28).

    Args:
        data: the incoming ``@pre_load`` payload (a mapping).  Empty
            strings should already have been stripped by the caller;
            this helper assumes any present key has a non-empty value.
        field_names: tuple of percent-field names declared by the
            schema's ``_PERCENT_FIELDS`` attribute.

    Returns:
        ``data`` with each named field replaced by its decimal-fraction
        string equivalent.  Fields whose value cannot be parsed as a
        ``Decimal`` are left untouched so the field-level validator
        can surface the "Not a valid number." error rather than this
        helper masking it with a ``decimal.InvalidOperation``.  Fields
        not present in ``data`` are skipped.

    Side effects:
        mutates ``data`` in place; the returned reference is the
        same object passed in for caller convenience.
    """
    for name in field_names:
        if name not in data:
            continue
        raw = data[name]
        if raw is None:
            continue
        try:
            data[name] = str(Decimal(str(raw)) / _PERCENT_DIVISOR)
        except InvalidOperation:
            # Leave the raw value in place so the field validator
            # rejects it with its native "Not a valid number."
            # message.  Mirrors :func:`app.routes.investment._convert_percentage_inputs`
            # for narrow-catch parity.
            pass
    return data


class RowId(fields.Integer):
    """A submitted database row id, in its one canonical spelling.

    The schema layer's share of "what does this submitted digit string
    mean" (plan step X-ae, finding N-141), consuming the same
    :func:`~app.utils.digit_strings.parse_row_id` as the form doors and the
    URL converter.  Declared on every field in this package that names a ROW
    -- 77 of them, which is the 73 called ``*_id`` PLUS the four
    ``recurrence_unit`` / ``recurrence_placement`` fields, whose names do not
    say so and which a completeness gate matching on an ``_id`` suffix could
    not see.  (It was the two ``recurrence_pattern`` fields those replaced at
    plan step R7b-2 -- same blind spot, one vocabulary later.)  An id means the
    same thing whether it arrives in a path or a form body.

    **What it refuses that ``fields.Integer`` accepts.**  Marshmallow's
    ``Integer`` is crash-safe -- it catches the ``ValueError`` -- but it is
    as lax as ``int()`` about what it will read, which was measured on this
    project's own declarations::

        fields.Integer().deserialize("١٢")    -> 12
        fields.Integer().deserialize(" 12 ")  -> 12
        fields.Integer().deserialize("+12")   -> 12
        fields.Integer().deserialize("1_0")   -> 10
        fields.Integer().deserialize("007")   ->  7
        fields.Integer().deserialize("-5")    -> -5
        fields.Integer().deserialize("0")     ->  0

    Seven spellings of "the row I mean", two of which name no row at all.
    Each is rejected here as a validation error the form reports, not as a
    silent coercion.

    **A non-string payload must still name a row EXACTLY.**  A JSON body or a
    programmatic caller submits a number rather than a spelling of one, so
    there is nothing to normalise -- but ``Integer`` would TRUNCATE it, and
    ``1.9`` naming row 1 is the same defect as ``"007"`` naming row 7.  A
    non-integral value is refused rather than rounded, and the
    :data:`~app.utils.digit_strings.MIN_ROW_ID` floor applies on both paths.

    **The strictness is on LOAD only, deliberately.**  It overrides
    ``_deserialize`` rather than ``_format_num`` because marshmallow calls
    ``_format_num`` from ``_serialize`` OUTSIDE the ``_validated``
    try/except -- so a rule expressed there escapes as a raw ``ValueError``
    when a schema dumps, which an adversarial review demonstrated on
    ``dump(0)``, ``dump(-5)`` and ``dump("007")``.  Dumping is the
    application rendering its OWN rows, not reading a submission, and it has
    no submitted spelling to police.
    """

    default_error_messages = {"invalid": "Not a valid id."}

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the row id *value* names.

        Args:
            value: The submitted value -- a ``str`` from a form or query, or
                an already-typed number from a programmatic payload.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Forwarded to :class:`marshmallow.fields.Integer`.

        Returns:
            The row id as an ``int``.

        Raises:
            ValidationError: *value* names no row -- a non-canonical
                spelling, a non-integral number, or a value below
                :data:`~app.utils.digit_strings.MIN_ROW_ID`.
        """
        if isinstance(value, str):
            row_id = parse_row_id(value)
            if row_id is None:
                raise self.make_error("invalid", input=value)
            return row_id
        row_id = super()._deserialize(value, attr, data, **kwargs)
        # ``Integer`` has already truncated at this point, so the round-trip
        # is what detects that it did: ``1.9`` arrives here as ``1``.
        if row_id != value or row_id < MIN_ROW_ID:
            raise self.make_error("invalid", input=value)
        return row_id


class _RefEnumField(RowId):
    """A submitted ``ref`` row id, deserialized to the ENUM member it names.

    :class:`RowId` answers "does this name a row"; this answers the narrower
    question a domain surface actually needs -- "does this name a value the
    application MODELS" -- and hands back that value rather than the integer
    that spelled it.

    **Returning the member rather than the id is what stops the lookup
    happening twice.**  A submitted id is the WIRE format and the enum member
    is the logic value, so the schema -- the boundary between them -- is where
    the conversion belongs.  Every consumer downstream (the write door's
    :class:`~app.services.recurrence.RecurrenceSpec`, the cross-field cadence
    validator, the preview reader) takes members, so a field returning ids
    would make each of them repeat a scan that can only ever give one answer.
    It remains IDs-for-logic in the sense the project means: nothing compares a
    ``name`` string, and the integer is what crosses the wire and what the
    column holds.

    **Declared as a FIELD TYPE rather than a ``validate=`` argument on
    purpose.**  The rule then travels with the value: a future schema that
    declares one of these axes gets the refusal without its author
    remembering, which is the failure this placement exists to remove.  Before
    plan step R2e-2 the check lived in the two route-layer form readers -- so
    it was the same rule written twice, and any third caller would have had
    neither copy.

    The last-resort invariant stays where it was: the write door still RAISES
    for a value it cannot encode, so one that reaches it some other way is
    refused loudly rather than persisted.  This field is what turns that 500
    into a field error the form can flash.

    No guard for an uninitialised ``ref_cache``: the lookup raises there, but a
    form cannot render in that window either (``cadence_options`` and
    ``register_ref_id_globals`` both need the same cache), so a guard would be
    dead code that only made the failure quieter.

    Subclasses supply :meth:`_member_for` and ``_invalid_message``.
    """

    #: What a refusal says.  Per subclass because the axis is what the user
    #: chose, and "Invalid recurrence value" names no control on the form.
    _invalid_message: str = "Invalid value."

    def _member_for(self, row_id):
        """Return the enum member *row_id* names, or ``None``.

        Args:
            row_id: A validated ``ref`` row id.

        Returns:
            The enum member, or ``None`` when the application models no value
            for that id.
        """
        raise NotImplementedError

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the enum member, refusing an id the application does not model.

        Args:
            value: The submitted value.
            attr: Field name (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Forwarded to :class:`RowId`.

        Returns:
            The enum member *value* names.

        Raises:
            ValidationError: *value* names no row (via :class:`RowId`), or
                names a row no member of this axis's enum does.
        """
        row_id = super()._deserialize(value, attr, data, **kwargs)
        member = self._member_for(row_id)
        if member is None:
            raise ValidationError(self._invalid_message)
        return member


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


class BaseSchema(Schema):
    """Base schema that strips CSRF tokens from form submissions."""

    class Meta:
        """Marshmallow options: silently drop unknown fields (e.g. the CSRF token)."""

        unknown = EXCLUDE


def _reject_envelope_on_income(data, message):
    """Raise ValidationError when ``is_envelope`` is set on an income payload.

    Shared cross-field rule for the template and transaction create
    schemas (DRY -- one implementation of the check).  Envelope /
    purchase-tracking semantics only apply to expenses: an income flow
    has no per-period budget to track individual purchases against, and
    the carry-forward ``settle-and-roll`` branch that envelope tracking
    feeds is expense-only.

    Runs only when both ``is_envelope`` and ``transaction_type_id`` are
    present in the deserialized payload.  Partial updates that omit the
    type skip the schema check and rely on a route-layer fallback
    against the stored type.

    Args:
        data: The deserialized schema payload.
        message: The error message to raise.  Passed in so each caller
            can phrase it for its own entity (template vs ad-hoc
            transaction) without forking the check logic.

    Raises:
        ValidationError: If ``is_envelope`` is True and
            ``transaction_type_id`` resolves to the Income type.  The
            error is attached to the ``is_envelope`` field for
            consistency with the other cross-field validators here.
    """

    if not data.get("is_envelope"):
        return
    txn_type_id = data.get("transaction_type_id")
    if txn_type_id is None:
        return
    if ref_cache.transaction_type_is_income(txn_type_id):
        raise ValidationError(message, field_name="is_envelope")
