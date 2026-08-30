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
    order_token_key,
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

#: The prefix a submitted match item carries.  Its INDEX is the rendered
#: position of the proposal it came from, so the ids of the item the owner
#: ticked travel with the tick rather than being re-derived server-side -- the
#: POST records exactly the ids the form submitted (ruling **R-FP**), and a
#: proposal re-derived after the fact could differ from the one that was
#: reviewed.
_MATCH_PREFIX = "match-"


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
#: the schema -- :func:`batch_payload` drops it.  **The default is the
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


class StatementBatchSchema(Schema):
    """Validate ONE reviewed pass: every match ticked, every line named.

    Plan step ``bank_import:X-f6a-3c-2``.  The review screen posts what the
    owner reviewed in one request rather than 215, so the payload is a LIST of
    acts and each act is validated by the schema that already owned it -- there
    is no second statement here of what a match or a purchase submission is.

    **It is a plain :class:`marshmallow.Schema`, not a
    :class:`~app.schemas.validation._helpers.BaseSchema`.**  That base exists to
    drop a FORM's ``csrf_token`` with ``unknown = EXCLUDE``; this schema never
    sees a form, because :func:`batch_payload` has already turned one into
    these two lists.  Inheriting it would silently swallow a key this schema
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
            # a list rather than per field**: a third list with its own
            # ceiling would admit half again as much work as the bound says.
            + len(data.get("incomes", ()))
        )
        if total > MAX_BATCH_ITEMS:
            raise ValidationError(
                f"That is {total:,} things to apply at once, and this page "
                f"applies at most {MAX_BATCH_ITEMS:,} in one pass.  Untick "
                f"some and apply them in two goes -- nothing was changed.",
                field_name="matches",
            )


#: The prefix a submitted destination carries, keyed by its BANK LINE's id
#: rather than by a position.  ``reconcile.py``'s ``settled_amount-<id>`` boxes
#: are keyed the same way and for the same reason its comment gives: paired
#: arrays would depend on the browser submitting several lists in the same
#: order, which is a property of the document rather than of the form.
#: What a line's RECORD-AS-INCOME checkbox is named with (ruling **bank_import:R-GW**,
#: plan step ``bank_import:X-gf-1``).  A checkbox rather than a select, because
#: there is nothing to choose between: an income row is filed against no
#: container, so the only question is whether to record it.  **A browser
#: submits a checkbox only when it is TICKED**, so an untouched control does
#: not appear in the form at all -- which is what makes R-FP's "nothing is
#: applied that the owner did not accept" structural here rather than a
#: default that has to be got right, the way :data:`LEAVE_ALONE` is for the
#: select beside it.
_INCOME_PREFIX = "record_income-"

#: What a submitted destination carries, keyed by its BANK LINE's id.  **Read
#: by TWO surfaces' readers** -- the review queue's here and the Reconcile
#: page's in :mod:`.statement_reconcile` -- so it is stated once rather than
#: spelled twice, which is this package's own root cause 1.  Keyed
#: that way rather than by a rendered position because a destination names one
#: LINE, and paired arrays would depend on the browser submitting several lists
#: of equal length -- which a crafted body need not do.
DESTINATION_PREFIX = "destination-"
ENVELOPE_NAME_PREFIX = "envelope_name-"
CATEGORY_PREFIX = "category_id-"


def _match_items(form) -> list:
    """Return the match items the form ticked, in rendered order.

    A match item is applied only when its index appears in ``apply``, which is
    what a ticked checkbox submits.

    **Every index it reads is now a rendered POSITION**, and until plan step
    ``bank_import:X-gf-3b`` one was not: the hand-build form shared this
    screen and submitted the reserved index ``"hand"``.  That form is a surface
    of its own now with a door of its own
    (:func:`hand_match_payload`), so this function reads the reviewed pass and
    nothing else.  :func:`~app.schemas.validation._helpers.order_token_key`
    still tolerates a non-numeric token, and must: that is what stops a crafted
    ``apply=%C2%B2`` raising inside the sort.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        One ``{"line_ids": [...], "rows": [...]}`` per ticked index,
        ascending, plus ``"residual"`` where that item submitted one.  Raw
        strings: the schema is what reads them, and a ``rows`` member is one
        token carrying a row's kind, id, reviewed figure and reviewed revision
        together (plan step ``bank_import:X-f6d-3``) rather than two lists a
        body could submit at different lengths.

        **``residual`` is OMITTED rather than sent as ``None``** when the item
        did not carry one, so the schema's own ``load_default`` decides what
        absence means -- one statement of that default, in the schema, rather
        than one here and one there.  It is read with ``get`` and not
        ``getlist`` because it is ONE consent per item: a body sending two
        keeps the first, and whichever it keeps still has to equal the
        difference the door derives, so no repeated key can choose what gets
        written.

        **An EMPTY consent is untouched, not malformed**, which is this
        module's own founding principle: a browser submits every control it
        renders, so an untouched one must be recognisable as untouched.  A
        body carrying one would otherwise 400 the WHOLE pass over a field
        nobody filled in.  Found by adversarial security review 2026-08-23.

        **The surfaces no longer render this field one way.**  Until plan step
        ``bank_import:X-gj-1b`` every one of them rendered the box ``value=""``
        and ``disabled`` in lockstep, so a browser could not send an empty
        one.  That is now true only of the arm where nothing is ticked: a
        proposal card carries the figure as a HIDDEN input, and a panel whose
        sides agree carries ``"0.00"`` the same way.  The emptiness rule holds
        for the same reason it always did -- it is about what a body may say,
        not about which control said it.
    """
    getlist = form.getlist
    items = []
    for raw in sorted(set(getlist("apply")), key=order_token_key):
        item = {
            "line_ids": getlist(f"{_MATCH_PREFIX}{raw}-line_ids"),
            "rows": getlist(f"{_MATCH_PREFIX}{raw}-rows"),
        }
        residual = form.get(f"{_MATCH_PREFIX}{raw}-residual")
        if residual:
            item["residual"] = residual
        items.append(item)
    return items


def _creation_items(form) -> list:
    """Return the destinations the form named, in bank-line order.

    A line is an act only when its destination select names one: its default
    is :data:`LEAVE_ALONE`, which is dropped here rather than reaching the
    schema.  That is what makes "the select IS the tick" true -- there is no
    state in which a line the owner did not choose a place for is recorded.

    **The order is the LINE's, not the field name's.**  Sorting the raw field
    names put line 100 between 10 and 2, because ``destination-100`` sorts
    lexically -- and the receipt this order becomes is meant to read down the
    page, which the screen renders in bank-line order.  So the KEY is what is
    sorted, through the same
    :func:`~app.schemas.validation._helpers.order_token_key` the ticks use.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        One ``{"line_id": ..., "destination": ..., "envelope_name": ...,
        "category_id": ...}`` per named line, ascending by its line key.  Raw
        strings: the schema is what reads them.
    """
    keys = [
        field[len(DESTINATION_PREFIX):] for field in form.keys()
        if field.startswith(DESTINATION_PREFIX)
    ]
    items = []
    for key in sorted(keys, key=order_token_key):
        destination = form[f"{DESTINATION_PREFIX}{key}"]
        if destination == LEAVE_ALONE:
            continue
        items.append({
            "line_id": key,
            "destination": destination,
            "envelope_name": form.get(f"{ENVELOPE_NAME_PREFIX}{key}", ""),
            "category_id": form.get(f"{CATEGORY_PREFIX}{key}", ""),
        })
    return items


def _income_items(form) -> list:
    """Return the inflow lines the form ticked, in bank-line order.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  **Presence IS the
    tick**: a browser submits a checkbox only when it is ticked, so an item
    here exists exactly when the owner ticked one, and there is no do-nothing
    value to drop the way :data:`LEAVE_ALONE` is dropped from the select
    beside it.

    **The VALUE is never read**, and that is deliberate rather than lax: the
    only fact the wire carries is which line was ticked, which is in the field
    NAME, so a submitted value has nothing to say and reading one would invent
    a second thing a forged body could vary.

    **The order is the LINE's**, through the same
    :func:`~app.schemas.validation._helpers.order_token_key` the ticks and
    the destinations use, because the receipt this order becomes is meant to
    read down the page.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        One ``{"line_id": ...}`` per ticked line, ascending by its line key.
        A raw string: the schema is what reads it.
    """
    keys = [
        field[len(_INCOME_PREFIX):] for field in form.keys()
        if field.startswith(_INCOME_PREFIX)
    ]
    return [{"line_id": key} for key in sorted(keys, key=order_token_key)]


def hand_match_payload(form) -> dict:
    """Return one hand-built group as :class:`StatementMatchSchema` loads it.

    Plan step ``bank_import:X-gf-3b``, ruling **bank_import:R-HC**.  The
    workbench posts ONE group -- its whole submission IS the match -- so this
    reads three flat keys and there is no ordering token to sort, no prefix to
    strip and no list of items to build.

    **THE ABSENT INDEX IS THE POINT, and it deletes a money-correctness hazard
    rather than moving it.**  This group rode :func:`_match_items` while the
    hand-build form shared a page with the reviewed pass, under the reserved
    index ``"hand"``.  That index was never a label: proposal 0 emits
    ``apply=0`` and ``match-0-line_ids``, this form emitted the same shape, and
    the only thing keeping one submission's ticks out of the other's was that
    they were separate ``<form>`` elements -- **a property of the document**.
    Adding an ``hx-include``, or merging the two controls, would have unioned
    proposal 0's hidden row ids with this group's ticks into ONE act naming
    rows the owner never grouped.  Two surfaces posting to two doors share no
    namespace, so there is nothing left for that hazard to be expressed in.

    **It reuses :class:`StatementMatchSchema` rather than declaring a schema of
    its own.**  A hand-built group and a ticked proposal are the same ACT -- a
    correspondence between bank lines and rows, with an accepted difference --
    and they reach the same door (``_accept.record_match``).  A second schema
    would be a second statement of what a match submission is, free to grade
    ``residual`` less strictly than the one beside it, which is exactly what
    :attr:`StatementMatchSchema.residual` records having cost once.

    **It carries no validation of its own**, for the reason
    :func:`batch_payload` states: every value it moves is a raw submitted
    string, so a forged id and an unparseable figure are the schema's to refuse.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        ``{"line_ids": [...], "rows": [...]}``, plus ``"residual"`` where the
        consent box carried one.  **Omitted rather than sent as ``None``** when
        it did not, so the schema's own ``load_default`` is the one statement
        of what absence means -- and an EMPTY consent is untouched rather than
        malformed, which is :func:`_match_items`' own founding principle: a
        body carrying an empty one must not 400 the act over a field nobody
        filled in.  This surface still renders the box ``value=""`` and
        ``disabled`` in lockstep when there is nothing ticked; since plan step
        ``bank_import:X-gj-1b`` it renders a HIDDEN ``"0.00"`` where the two
        sides agree, because the door checks the figure a match says it was
        reviewed against whether or not there is anything to permit.
    """
    item = {
        "line_ids": form.getlist("line_ids"),
        "rows": form.getlist("rows"),
    }
    residual = form.get("residual")
    if residual:
        item["residual"] = residual
    return item


def batch_payload(form) -> dict:
    """Return one reviewed pass as the payload :class:`StatementBatchSchema` loads.

    **The form shape lives HERE, beside the schema that grades it**, for the
    reason :func:`~app.schemas.validation._helpers.form_payload` gives: a route
    that listed the field names itself is a route that can be extended with a
    fourth kind of item and not updated.

    **It carries no validation of its own.**  Every value it moves is a raw
    submitted string, so a forged index, a forged id and an unparseable
    destination are all the schema's to refuse -- one grader, in one place,
    with one error structure the route already knows how to render.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        ``{"matches": [...], "creations": [...], "incomes": [...]}``.
    """
    return {
        "matches": _match_items(form),
        "creations": _creation_items(form),
        "incomes": _income_items(form),
    }
