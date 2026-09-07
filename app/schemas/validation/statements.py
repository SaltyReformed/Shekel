"""
Shekel Budget App -- Statement Import Validation Schema

The upload form's one schema.  ``docs/coding-standards.md``: *"Marshmallow
schema for every state-changing route... No manual ``request.form.get()`` with
inline try/except."*  A file upload is no exemption -- the source field is a
closed enumeration, and validating it here rather than in the route is what
keeps the route's job to ownership, the unit of work and the flash.

**The offer set is the ADAPTER REGISTRY, resolved at validation time.**  It is
not a literal list here and not a second copy of the enum: a source whose
parser has not been written must be unofferable AND unsubmittable from the same
fact, or the two drift and a tampered form reaches a parser that does not exist.
"""

from marshmallow import (
    RAISE,
    Schema,
    ValidationError,
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
)
from app.services.statement_import import supported_sources
from app.services.statement_match import (
    NEW_ENVELOPE,
    ReviewedRow,
    parse_figure,
)
from app.utils.digit_strings import parse_row_id


def _source_values() -> list[str]:
    """Return the source values a file may actually be imported as.

    Read through the registry rather than the enum so a member without a
    parser is refused here exactly as it is hidden on the form.

    Returns:
        The submittable ``StatementSourceEnum`` values.
    """
    return [member.value for member in supported_sources()]


class StatementUploadSchema(BaseSchema):
    """Validate the statement upload form's non-file field.

    The FILE itself is not a Marshmallow field: ``request.files`` is a
    different mapping from ``request.form``, and Marshmallow validates data
    rather than streams.  The route checks the file's presence and hands the
    bytes to the adapter, which is the only thing that can say whether they are
    a statement.

    Inherits :class:`~app.schemas.validation._helpers.BaseSchema`, whose
    ``unknown = EXCLUDE`` is what drops the form's ``csrf_token`` -- the same
    rule every other schema here takes, rather than a second spelling of it.
    """

    source = fields.String(
        required=True,
        validate=validate.OneOf(_source_values()),
        error_messages={
            "required": "Choose which kind of export this file is.",
        },
    )


#: The most ids one match may name on a side.  A bound rather than a limit
#: anyone will meet: ruling **R-FS**'s largest measured shape is a payroll
#: deposit against three rows, and the hand-build form posts a checkbox per
#: row it renders -- so without a ceiling a crafted submission could ask the
#: accept door to re-derive and settle an account's whole history in one
#: request.  Generous enough that a real statement's biggest group is nowhere
#: near it.
_MAX_MATCH_MEMBERS: int = 100

#: The most acts ONE reviewed pass may ask for (plan step
#: ``bank_import:X-f6a-3c-2``).  **Measured, not chosen**: applying the
#: developer's own statement -- 124 proposals and 91 recordable lines -- takes
#: 5.80 s in the door on a production clone, of which 3.65 s is the one shared
#: derivation, so an item costs about 10 ms; the whole REQUEST, which derives a
#: second time to render its answer, measured 13.37 s.  Gunicorn's request
#: timeout is 120 s and Nginx's ``proxy_read_timeout`` is the same, so 500
#: items is about 9 s of door plus the response's own re-derivation -- well
#: inside the budget, at 2.3 times the acts the developer's own statement
#: offers.
#:
#: **43 ms is an AVERAGE over acts naming one to four rows, not a bound on
#: one.**  :data:`_MAX_MATCH_MEMBERS` lets a crafted item name 100 lines and
#: 100 rows, each running its own settle door, so a hostile pass is bounded by
#: ``MAX_CONTENT_LENGTH`` (512 KB, ``app/config.py``) rather than by this --
#: measured, a body that size carries about 44,600 ticks and is refused, in
#: 0.36 s, before any of them runs.  This ceiling is what keeps an ORDINARY
#: pass inside the budget; that one is what keeps a crafted one out.
#:
#: **A bound that fires is REFUSED and said, never silently truncated.**  An
#: import may carry ``_secu_csv.MAX_LINES`` = 20,000 lines, so an account can
#: in principle offer more acts than this; the owner is told to apply the pass
#: in two rather than having half of what they ticked dropped without a word.
MAX_BATCH_ITEMS: int = 500

#: What the destination select submits when the owner picks "a new envelope".
#: A NAMED arm rather than an absent id, and that is plan step X-f6a-3c-2's
#: correction to the shape: the arm was "``transaction_id`` is missing" until
#: then, so the always-rendered, always-prefilled name box read as a
#: destination and made the existing-envelope arm unreachable from a browser
#: (three adversarial reviews, 2026-08-19).  One control now states which of
#: the three things the owner meant, so nothing has to be inferred from an
#: absence.
#:
#: **Re-exported from the SERVICE rather than declared here** (plan step
#: X-f6a-3d), because the service produces this value as well as reading it:
#: ``Placement.select_value`` answers what a line's control would be set to.
#: Two literals would be one wire value spelled twice, which is this package's
#: own root cause 1 -- and the import direction is the one this module already
#: takes for ``supported_sources``.

#: What that select submits when the owner has not picked anything, which is
#: its DEFAULT.  The line is left alone: it is not an act, so it never reaches
#: the schema --
#: :func:`~app.schemas.validation.statement_reconcile.reconcile_payload`
#: drops it.  **The default is the
#: do-nothing arm on purpose** (developer ruling, 2026-08-19): the select used
#: to default to the first envelope in the line's pay period, which on the
#: developer's own data has already CLOSED at a fixed figure on 78 of 91
#: lines, and the category select defaulted to the first active category
#: ("Auto: Property Tax").  One press per line hid that; one press for forty
#: would not.
LEAVE_ALONE: str = ""


class PurchaseDestination(fields.Field):
    """Where one bank line is to be recorded: an envelope, or a new one.

    **One field because the owner makes one choice.**  The review screen's
    destination control is a single ``<select>`` whose options are the pay
    period's envelopes plus :data:`NEW_ENVELOPE`, so the submission carries one
    value and this reads it into one of two things: an ``int`` naming an
    envelope, or the :data:`NEW_ENVELOPE` string.  Splitting it into an id plus
    an implied arm is what let a form name both destinations at once.

    **The id half is exactly as strict as :class:`RowId`**, through the same
    :func:`~app.utils.digit_strings.parse_row_id`: ``'٧'``, ``' 7 '``, ``'+7'``,
    ``'0_7'``, ``'007'``, ``'-7'`` and ``'0'`` name no envelope here either.  A
    second, laxer reading of a row id on a money door is exactly what plan step
    X-ae removed.
    """

    default_error_messages = {
        "invalid": "That is not a place a purchase can go.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the destination *value* names.

        Args:
            value: The submitted value.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            :data:`NEW_ENVELOPE`, or the ``int`` id of an existing envelope.

        Raises:
            ValidationError: When *value* is neither.
        """
        if value == NEW_ENVELOPE:
            return NEW_ENVELOPE
        row_id = parse_row_id(value) if isinstance(value, str) else None
        if row_id is None:
            raise self.make_error("invalid")
        return row_id


class ReviewedRowField(fields.Field):
    """One app row a match names, AS THE SCREEN SHOWED IT.

    **The format is the service's, read through the service's own reader**
    (:meth:`~app.services.statement_match.ReviewedRow.from_token`).  The review
    template WRITES this token and this field READS it, and a second parse
    living here would be two spellings of one format with nothing in the tree
    failing when they diverged -- which is this arc's own root cause 1, on the
    one pair where the halves are a template and a validator.

    **Everything strict about it is strict in there**, so this field is the
    thin adapter it looks like: the row id and the version counter go through
    the same :func:`~app.utils.digit_strings.parse_row_id` every other id on
    this screen does, and the figure is matched against an explicit pattern
    before it reaches ``Decimal`` -- because a bare ``Decimal(raw)`` accepts
    ``"NaN"``, and a ``NaN`` figure compares unequal to every row, which would
    turn the staleness guard into a no-op that always passes.

    What arrives is a value object, not a mapping: the door's parameter type
    is :class:`~app.services.statement_match.ReviewedRow`, and loading straight
    into it is what keeps the route from assembling one field by field.
    """

    default_error_messages = {
        "invalid": "That is not a row this page could have shown you.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the reviewed row *value* names.

        Args:
            value: The submitted token.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            The :class:`~app.services.statement_match.ReviewedRow`.

        Raises:
            ValidationError: When *value* is not a token this app emitted.
        """
        try:
            return ReviewedRow.from_token(value)
        except ValueError as exc:
            raise self.make_error("invalid") from exc


class ReviewedFigureField(fields.Field):
    """One money figure a submission carries, in the format this screen emits.

    :class:`ReviewedRowField`'s sibling and, since plan step
    ``bank_import:X-f6d-4``, its co-reader: both go through
    :func:`~app.services.statement_match._submission.parse_figure`, so the two
    money strings this one form submits are strict in exactly the same way.
    See :attr:`StatementMatchSchema.residual` for what having two strictnesses
    measurably cost.

    ``None`` passes through untouched, because absence is a state the schema
    names (``load_default``) rather than a spelling this reads.
    """

    default_error_messages = {
        "invalid": "That is not a figure this page could have shown you.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the figure *value* names.

        Args:
            value: The submitted string.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            Its :class:`~decimal.Decimal`.

        Raises:
            ValidationError: When *value* is not a figure this app emitted.
        """
        if not isinstance(value, str):
            raise self.make_error("invalid")
        try:
            return parse_figure(value)
        except ValueError as exc:
            raise self.make_error("invalid") from exc


class StatementMatchSchema(BaseSchema):
    """Validate ONE accepted match: its bank lines, and its reviewed rows.

    **The door still re-derives everything it WRITES**, inside the same
    transaction, so a stale page cannot commit a number the database no longer
    holds.  What :class:`ReviewedRowField` carries is a PRECONDITION -- the
    figure and the revision the owner was looking at -- and the door refuses an
    item whose row has moved since (finding **N-336**, plan step
    ``bank_import:X-f6d-3``).  The distinction is the whole reason this schema
    may accept an amount at all: nothing here is a value to write, and a field
    that fed one to a settle verb would still be the channel the old docstring
    refused.

    **``rows`` REPLACED ``transaction_ids`` and ``entry_ids``.**  Those were one
    fact discriminated by table, and carrying the reviewed state beside them
    would have meant a second list joined back on the row id -- a parallel
    array, whose halves a crafted body desynchronises by submitting different
    lengths.  One token per row cannot be desynchronised from itself.

    Both lists are ``required=False`` with an empty default: which is populated
    depends on R-FS's shape, and the door's own ``_reject_empty_side`` is what
    refuses a submission naming nothing.  A schema arm refusing it too would be
    a second statement of one rule -- and the wrong one, because "at least one
    line AND at least one row" is a relation between two fields rather than a
    fact about either.

    **``line_ids`` members are :class:`~app.schemas.validation._helpers.RowId`,
    not ``fields.Integer``** (plan step X-ae, finding **N-141**): ``Integer``
    reads ``'١٢'``, ``' 12 '``, ``'+12'``, ``'1_0'``, ``'007'``, ``'-5'`` and
    ``'0'`` as ids -- two of which name no row at all.  The completeness gate
    in ``tests/test_schemas`` is what caught the first draft of this schema
    declaring them the lax way, and :class:`ReviewedRowField` reaches the same
    reader for the two counters inside its token.

    **It is NESTED inside :class:`StatementBatchSchema` since plan step
    X-f6a-3c-2**, because one submission now carries many of these: the
    proposals the owner ticked, and the group they built by hand, are the same
    act and reach the same door.
    """

    line_ids = fields.List(
        RowId(), required=False, load_default=list,
        validate=validate.Length(max=_MAX_MATCH_MEMBERS),
    )
    rows = fields.List(
        ReviewedRowField(), required=False, load_default=list,
        validate=validate.Length(max=_MAX_MATCH_MEMBERS),
    )
    #: The DIFFERENCE this match states it was REVIEWED against (plan step
    #: ``bank_import:X-f6d-4``, ruling **R-FN**).
    #:
    #: **It used to be absent on every proposal the app itself offers**, and
    #: plan step ``bank_import:X-gj-1b`` inverted that: the accept door
    #: exempts no shape now, so a proposal card renders this as a HIDDEN input
    #: carrying :func:`app.jinja_filters.stated_difference` and every ticked
    #: item submits one.  ``load_default=None`` remains, because absence is
    #: still a real thing a body can say and the DOOR is where it is answered
    #: -- refused where a difference would be written, permitted where none
    #: would be
    #: (``app.services.statement_match._variance._reject_unaccepted_difference``).
    #: A ``required=True`` here would turn a scriptless owner's balanced
    #: hand-built group into a 400 over the whole pass.
    #:
    #: **Read through the service's own strict reader, exactly as
    #: :class:`ReviewedRowField` beside it is.**  A first version declared it
    #: ``fields.Decimal(places=2)``, and an adversarial review measured what
    #: that cost on 2026-08-23: marshmallow quantizes with the default context
    #: rounding, which is ``ROUND_HALF_EVEN`` -- the mode
    #: :mod:`app.utils.money` says must never be reached implicitly through a
    #: bare ``.quantize`` -- so ``"0.054"`` was silently REPAIRED into
    #: agreement with a true difference of ``0.05``, on the one field the
    #: design says must be exact.  It also took ``"1_0"``, ``"+0.05"`` and
    #: ``" 0.05 "``, which the row token on the same form deliberately refuses:
    #: two strictnesses for two money strings on one door.
    #:
    #: **No ``Range`` bound, and the reason is NOT the one a first version
    #: gave.**  That version said a bound was unnecessary because the
    #: difference is "bounded by the ``Numeric(12, 2)`` columns it is summed
    #: from", which is arithmetically false -- a match may name up to
    #: :data:`_MAX_MATCH_MEMBERS` of them per side.  The bound lives at the
    #: DOOR instead
    #: (``app.services.statement_match._variance._reject_unstorable``), where
    #: the sum it must bound actually exists; a bound here could only refuse a
    #: figure the door was going to refuse anyway, since nothing is written
    #: unless this equals the door's own derivation.
    residual = ReviewedFigureField(required=False, load_default=None)
    #: WHICH member of this match the difference belongs to (plan step
    #: ``bank_import:X-gj-3a``), as that row's own reviewed token.
    #:
    #: **The same field type as a member of ``rows`` above, deliberately.**
    #: The control's value IS one of the row tokens the same body carries, so
    #: reading it through a second, looser field would let a body attribute a
    #: difference to something the row list could not have contained -- and
    #: ``resolve_rows`` compares the two as whole values, which only holds if
    #: both were read by one reader.
    #:
    #: ``load_default=None`` is what every surface but the Reconcile card's
    #: MATCH pane relies on: a match naming ONE row needs no attribution
    #: (ruling **R-GD(a)**), and a match naming several with none named is the
    #: shape that mints **R-FN**'s ordinary row, which is what this screen did
    #: for every group before this step.  A ``required=True`` here would 400
    #: the whole pass for a hand-built group nobody had to attribute.
    difference_on = ReviewedRowField(required=False, load_default=None)


class StatementMatchReleaseSchema(BaseSchema):
    """Validate the id of the match being released.

    Separate from :class:`StatementMatchSchema` because it is a different act
    on a different object: one names a correspondence to create, the other
    names an act to delete, and a single schema carrying both would have every
    field optional and refuse nothing.
    """

    match_id = RowId(
        required=True,
        error_messages={"required": "Which match do you want to undo?"},
    )


class StatementSkipReleaseSchema(BaseSchema):
    """Validate the id of the skip being undone (plan step X-gj-4c-2).

    Its own schema for the reason :class:`StatementMatchReleaseSchema` is
    separate from :class:`StatementMatchSchema`, applied one act over: a match
    and a skip are different objects in different tables, and one schema
    carrying both ids would have each optional and refuse a submission naming
    neither.

    **The id is a :class:`~app.schemas.validation._helpers.RowId`, not
    ``fields.Integer``** (plan step X-ae, finding **N-141**): it names a ROW,
    and ``Integer`` reads ``'١٢'``, ``' 12 '``, ``'+12'``, ``'1_0'``,
    ``'007'``, ``'-5'`` and ``'0'`` as ids -- two of which name no row at all.
    """

    skip_id = RowId(
        required=True,
        error_messages={"required": "Which skip do you want to undo?"},
    )


class AgreementDaySchema(BaseSchema):
    """The one day the books-vs-bank drill-down is asked about.

    A GET fragment's only argument, declared as a schema rather than parsed
    inline so the day it reads is a ``fields.Date`` under the project's one set
    of rules -- and so a second reader of this URL cannot invent a second
    spelling of what a day is.
    """

    day = fields.Date(required=True)


class StatementImportDeleteSchema(BaseSchema):
    """Validate the id of the import being deleted (plan step X-f6a-4).

    Its own schema for the reason :class:`StatementMatchReleaseSchema` is
    separate from :class:`StatementMatchSchema`: these are different acts on
    different objects, and one schema carrying both would have every field
    optional and refuse nothing.

    **The id is a :class:`~app.schemas.validation._helpers.RowId`, not
    ``fields.Integer``** (plan step X-ae, finding **N-141**).  It names a ROW,
    and ``Integer`` reads ``'١٢'``, ``' 12 '``, ``'+12'``, ``'1_0'``, ``'007'``,
    ``'-5'`` and ``'0'`` as ids -- two of which name no row at all.
    """

    import_id = RowId(
        required=True,
        error_messages={"required": "Which import do you want to delete?"},
    )


class StatementPurchaseSchema(BaseSchema):
    """Validate ONE bank line becoming a purchase (plan step X-f6a-3b).

    **One line and one destination, and no figure at all.**  The amount and
    both days come from the recorded LINE inside the same transaction
    (:mod:`app.services.statement_match._create`), so a stale page cannot
    commit a number the bank did not state -- the same reason
    :class:`StatementMatchSchema` beside it carries ids only.

    **The destination is ONE field naming one of two arms**
    (:class:`PurchaseDestination`), which is plan step X-f6a-3c-2's correction.
    It was a nullable ``transaction_id`` whose ABSENCE meant "make a new
    envelope", so the always-rendered name box read as a destination of its own
    and the existing-envelope arm was unreachable from a browser.  A control
    that says which arm was chosen cannot be misread; an absence can.

    The name and the category are PARAMETERS OF ONE ARM rather than a
    destination, and **whether that arm is COMPLETE is the door's question,
    not this schema's** (plan step X-f6a-3c-2).  It was a
    ``@validates_schema`` rule here, which was right while one POST was one
    act and wrong the moment a POST became a whole pass: a nested error refuses
    the ENTIRE payload, so an owner who picked "a new envelope" on one line and
    left its category untouched -- the form's own default, and the ordinary
    slip -- lost 124 proposals and 90 good creations to it.  That contradicts
    the ruled failure policy in the one case the form itself produces.
    ``_create._reject_incomplete_new_envelope`` owns it now, so it is one
    item's refusal like every other.

    **A MALFORMED payload is still a pass-level refusal, and that is the right
    asymmetry**: an id that names no row, or a body naming more acts than one
    request may carry, is a fact about the SUBMISSION rather than about an act
    the owner reviewed -- no browser of ours produces one, so there is no pass
    to salvage.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you recording?"},
    )
    destination = PurchaseDestination(
        required=True,
        error_messages={"required": "Where should this purchase go?"},
    )
    #: Defaulted from what the BANK called the merchant and editable, because
    #: the bank's own words are the only description of this spending that
    #: exists.  The 200 matches ``transactions.name``.
    envelope_name = fields.String(
        required=False, load_default=None,
        validate=validate.Length(min=1, max=200),
    )
    category_id = RowId(required=False, load_default=None)


class StatementIncomeSchema(BaseSchema):
    """Validate ONE bank line becoming an income row (ruling **bank_import:R-GW**).

    **One line id and nothing else, and the emptiness is the design.**  Its
    sibling :class:`StatementPurchaseSchema` needs a destination because a
    purchase is filed against a container the owner chooses between; an income
    row is filed against nothing, so there is no arm to name and no name or
    category to state.  The figure and the day come from the recorded LINE
    inside the same transaction (:mod:`app.services.statement_match._income`),
    so a stale page cannot commit a number the bank did not state.

    **A one-field schema still earns its place** rather than the list holding
    bare ids: it is what refuses a forged ``line_id`` -- a negative, a
    thousand-digit string, a word -- through the same :class:`RowId` every
    other id on this pass is graded by, and it is the shape a second fact
    about a deposit would be added to.

    **Which lines may be recorded this way is the DOOR's question, not this
    schema's**: that the line is on this account, that nothing already claims
    it, and that it is money ARRIVING are all facts about the database rather
    than about the submission, and a nested error here would refuse the ENTIRE
    pass -- the failure mode :class:`StatementPurchaseSchema` records having
    cost 124 proposals and 90 creations once.
    """

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you recording?"},
    )


class StatementSkipSchema(BaseSchema):
    """Validate ONE bank line being recorded as explained by nothing.

    Ruling **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``.

    **One line id and nothing else, and the emptiness goes further than its
    sibling's.**  :class:`StatementIncomeSchema` has one field because an
    income row is filed against no container; a SKIP is filed against nothing
    at all -- no container, no row, no figure and no reason, because the
    decision IS *explained by nothing*.  There is no second fact about a skip
    for this schema to grow.

    **A one-field schema still earns its place**, for its sibling's reason: it
    is what refuses a forged ``line_id`` -- a negative, a thousand-digit
    string, a word -- through the same :class:`RowId` every other id on this
    pass is graded by, before any of it reaches a door.

    **Which lines may be skipped is the DOOR's question, not this schema's**:
    that the line is on this account, that no accepted match already answers
    it, and that its merchant is not one a source files as paying an account
    the owner holds (ruling **bank_import:R-JI**) are all facts about the
    database rather than about the submission -- and a nested error here would
    refuse the ENTIRE pass, which is the failure mode
    :class:`StatementPurchaseSchema` records having cost 124 proposals and 90
    creations once.
    """

    line_id = RowId(
        required=True,
        error_messages={"required": "Which statement line are you skipping?"},
    )


class StatementBatchSchema(Schema):
    """Validate ONE reviewed pass: every match ticked, every line named.

    Plan step ``bank_import:X-f6a-3c-2``.  The review screen posts what the
    owner reviewed in one request rather than 215, so the payload is a LIST of
    acts and each act is validated by the schema that already owned it -- there
    is no second statement here of what a match or a purchase submission is.

    **It is a plain :class:`marshmallow.Schema`, not a
    :class:`~app.schemas.validation._helpers.BaseSchema`.**  That base exists to
    drop a FORM's ``csrf_token`` with ``unknown = EXCLUDE``; this schema never
    sees a form, because
    :func:`~app.schemas.validation.statement_reconcile.reconcile_payload` has
    already turned one into these lists.  Inheriting it would silently swallow a key this schema
    does not declare, on the one payload that carries every act in a pass.

    **The ceiling is on the SUM and is stated ONCE**
    (:data:`MAX_BATCH_ITEMS`), because the screen's own offer set is bounded
    by what an import may carry and a crafted submission is bounded by nothing.
    A per-list ``Length`` beside it was a second statement of one rule and the
    WRONG one: two lists with their own ceilings admit twice the work either
    allows, and marshmallow's generic "Longer than maximum length 500" fired
    first and named no remedy -- on a bound whose whole point is to tell the
    owner to apply the pass in two goes.
    """

    class Meta:
        """Refuse an unknown key rather than dropping it silently."""

        unknown = RAISE

    matches = fields.List(
        fields.Nested(StatementMatchSchema), required=False, load_default=list,
    )
    creations = fields.List(
        fields.Nested(StatementPurchaseSchema), required=False,
        load_default=list,
    )
    #: The lines of money COMING IN the owner ticked (ruling **bank_import:R-GW**).  **Its
    #: own list rather than a third arm of ``creations``**: that schema
    #: REQUIRES a destination, and an income row has none, so folding them
    #: would mean a required field that is meaningless for half its members --
    #: and the two reach different doors writing different shapes.
    incomes = fields.List(
        fields.Nested(StatementIncomeSchema), required=False,
        load_default=list,
    )
    #: The lines the owner decided are explained by NOTHING (ruling
    #: **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``).  **Its own
    #: list beside the three above** for the reason ``incomes`` is its own:
    #: it reaches a different door, which writes a different TABLE
    #: (``budget.statement_line_skips``) and no money at all.  Folding it into
    #: any of them would mean a required field that is meaningless for its
    #: members -- a skip names no destination and no row.
    skips = fields.List(
        fields.Nested(StatementSkipSchema), required=False,
        load_default=list,
    )

    @validates_schema
    def validate_pass_is_not_too_large(self, data, **kwargs):
        """Refuse a pass asking for more acts than one request may carry.

        The ONE bound, over both kinds, because what a request's time budget
        cares about is how many acts run rather than which sort they are.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's context, unused.

        Raises:
            ValidationError: When the pass names more than
                :data:`MAX_BATCH_ITEMS` acts in total.
        """
        total = (
            len(data.get("matches", ()))
            + len(data.get("creations", ()))
            # **Every kind of act, and the sum is why this arm is stated over
            # a list rather than per field**: four lists each carrying
            # their own ceiling would admit FOUR TIMES what the bound says.
            # *This comment said "a third list" until plan step
            # ``bank_import:X-gj-4b`` added the fourth*, which is a claim about
            # the lines below it rather than a turn of phrase.
            + len(data.get("incomes", ()))
            + len(data.get("skips", ()))
        )
        if total > MAX_BATCH_ITEMS:
            raise ValidationError(
                f"That is {total:,} things to apply at once, and this page "
                f"applies at most {MAX_BATCH_ITEMS:,} in one pass.  Untick "
                f"some and apply them in two goes -- nothing was changed.",
                field_name="matches",
            )


#: What a submitted destination carries, keyed by its BANK LINE's id.  Keyed
#: that way rather than by a rendered position because a destination names one
#: LINE, and paired arrays would depend on the browser submitting several lists
#: of equal length -- which a crafted body need not do.
#:
#: **It had TWO readers and now has ONE**, and the placement argument that put
#: it here is spent: it was stated here rather than twice because the review
#: queue's ``_creation_items`` read it beside the Reconcile page's
#: :func:`~.statement_reconcile.reconcile_payload`, and plan step
#: ``bank_import:X-gi-3`` deleted the first.  These three constants now have
#: exactly one consumer and it is the OTHER module, so rule 14's *move the
#: leaf* says they belong beside it.  **Not moved here**, because a constant
#: move is a change to two modules' surfaces and this step is a deletion --
#: adversarial review 2026-09-06 named it, and it is recorded rather than
#: silently left.
DESTINATION_PREFIX = "destination-"
ENVELOPE_NAME_PREFIX = "envelope_name-"
CATEGORY_PREFIX = "category_id-"
