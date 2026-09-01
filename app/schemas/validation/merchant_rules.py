"""What the merchant-rule control submits, and what a rule answer may say.

Plan step ``bank_import:X-gf-1`` split this out of
:mod:`~app.schemas.validation.statements`.  **The seam is the SUBJECT**, and it
is the same one :mod:`app.services.statement_match._section` cuts one tier
down: that package's ``_reads`` answers *what does the review screen show about
this pass's LINES* and ``_section`` answers *what does it ask about this
account's MERCHANTS*.  This is those two questions on the WIRE -- the module
beside it grades a pass of money acts, and this grades a pass that MOVES NO
MONEY (ruling **R-GI**: stating where a merchant goes is a suggestion until a
line's own control is moved).

**Two graders, two ceilings, and that is why they are two modules.**
``statements`` bounds MONEY acts at 500, each of which runs a settle door and
costs about 10 ms; a rule statement writes at most one small row, so
:data:`_MAX_RULE_ITEMS` is 2,000 and is a different number for a different
reason.  One module holding both put a money bound and a no-money bound in one
place where a reader has to notice which is which.

**The split is a line cap made useful rather than worked around**, which is the
argument :mod:`app.services.statement_match._creations` and ``_section`` both
record: adding ruling **bank_import:R-GW**'s deposit arm took ``statements`` past this
project's 1,000-line module bound, and the two honest answers are to cut the
record or to cut the module.

What both halves share is :func:`~._helpers.order_token_key`, because both
submit ordering tokens keyed into field names -- one predicate over *which
submitted strings may be read as numbers*, in the module that already holds
this package's shared primitives.
"""

from dataclasses import dataclass

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
from app.services.statement_match import NEW_ENVELOPE, RuleAnswer
from app.utils.digit_strings import parse_row_id


@dataclass(frozen=True)
class SubmittedAnswer:
    """One answer the rule control submitted, with WHICH KIND it is.

    Plan step ``bank_import:X-gj-2a``.  **The discriminator is carried, not
    inferred**, and that is the whole reason this type exists.
    :class:`RuleAnswerField` returned a sentinel string or a BARE ``int``
    until this step, and the one reader --
    ``_statement_rules._rule_statements`` -- dispatched by naming the sentinels
    and FALLING THROUGH to *it must be a template id*.  That fall-through is
    safe with exactly one id-bearing answer and unsafe the moment there are
    two: ruling **R-HT(a)**'s income answer carries a category id, and reaching
    that dispatch as a bare int would have recorded it as a TEMPLATE rule --
    filing the merchant's SPENDING into a budget line the owner never named,
    from an answer they gave about DEPOSITS.

    **So the dispatch stops having a default arm.**  A reader asks
    :attr:`kind`, and a sixth answer added to the enum without an arm in the
    route is a value that matches nothing rather than one silently read as a
    template.

    Attributes:
        kind: Which answer this is (:class:`~app.services.statement_match
            .RuleAnswer`), for every answer that names something.
        row_id: The id it names -- a template for
            :attr:`~app.services.statement_match.RuleAnswer.TEMPLATE`, a
            category for
            :attr:`~app.services.statement_match.RuleAnswer.INCOME_CATEGORY`
            -- or ``None`` for the answers that name nothing.
    """

    kind: RuleAnswer
    row_id: "int | None" = None


class RuleAnswerField(fields.Field):
    """Which of the six things the rule control's one select can say.

    **One field because the owner makes one choice**, exactly as
    :class:`PurchaseDestination` is one field: the control is a single
    ``<select>`` whose options are each recurring envelope on the account, *a
    new envelope*, each category a DEPOSIT from this merchant could be income
    under (**R-HT(a)**), *ask me every time*, *never a purchase*, and -- on a
    merchant with no rule only -- *I have not said*.  Splitting it into an id
    plus an implied arm is what let a form name two destinations at once one
    leaf earlier.

    **Five of the six are ANSWERS and the last is the absence of one**
    (rulings **R-GI**, **R-HT(a)**).  This field grades the wire and does not
    know the difference; the route is where :data:`NOT_SAID` stops being a
    value and becomes an item that is simply not submitted to the door.

    **The id half is exactly as strict as :class:`RowId`**, through the same
    :func:`~app.utils.digit_strings.parse_row_id`: after either prefix,
    ``'٧'``, ``' 7 '``, ``'+7'``, ``'0_7'``, ``'007'``, ``'-7'`` and ``'0'``
    name no row here either.

    **What it RETURNS is a :class:`SubmittedAnswer` and no longer a bare id**,
    so the two id-bearing answers cannot be confused for one another.
    """

    default_error_messages = {
        "invalid": "That is not somewhere a merchant's money can go.",
    }

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the answer *value* names.

        Args:
            value: The submitted value.
            attr: The field name being loaded (marshmallow's contract).
            data: The whole payload being loaded (marshmallow's contract).
            **kwargs: Marshmallow's contract, unused.

        Returns:
            :data:`NOT_SAID` for the one option that is not an answer, else a
            :class:`SubmittedAnswer` naming which answer it is and the row it
            names.

        Raises:
            ValidationError: When *value* is none of those.
        """
        if value == NOT_SAID:
            return value
        named = _NAMES_NOTHING.get(value)
        if named is not None:
            return SubmittedAnswer(kind=named)
        if not isinstance(value, str):
            raise self.make_error("invalid")
        for prefix, kind in _PREFIXED_ANSWERS:
            if value.startswith(prefix):
                row_id = parse_row_id(value[len(prefix):])
                if row_id is None:
                    raise self.make_error("invalid")
                return SubmittedAnswer(kind=kind, row_id=row_id)
        raise self.make_error("invalid")


class MerchantRuleSchema(BaseSchema):
    """Validate ONE merchant's stated destination.

    **It states no owner and no account**, for the reason
    :class:`StatementPurchaseSchema` states none: whose account this is, is
    the route's one proved statement.

    **The merchant is an ID and is exactly as strict as every other id here**
    (plan step ``bank_import:X-gd-1``).  It was free text from a BANK, so the
    schema could say nothing about it at all and the whole defence was a scope
    comparison in the service.  A merchant is a row now:
    :class:`RowId` refuses ``'٧'``, ``' 7 '``, ``'+7'``, ``'007'``, ``'-7'``
    and ``'0'`` before the service is asked, and
    ``fk_merchant_rules_merchant_account`` refuses a well-formed id that
    is not this account's.

    **The name and the category are PARAMETERS OF ONE ANSWER**, like
    ``StatementPurchaseSchema``'s, and whether that answer is COMPLETE is the
    service's question rather than this schema's -- for the same reason, one
    door over: a ``@validates_schema`` rule refuses the WHOLE payload, and this
    payload carries every merchant on the account.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    merchant_id = RowId(
        required=True,
        error_messages={"required": "Which merchant is this about?"},
    )
    answer = RuleAnswerField(
        required=True,
        error_messages={"required": "Where should this merchant's spending go?"},
    )
    envelope_name = fields.String(
        required=False, load_default=None,
        validate=validate.Length(min=1, max=200),
    )
    category_id = RowId(required=False, load_default=None)


class MerchantRuleBatchSchema(Schema):
    """Validate ONE pass over the rule section: every merchant it renders.

    Plan step ``bank_import:X-f6a-3d``.  **A plain
    :class:`marshmallow.Schema`, not a
    :class:`~app.schemas.validation._helpers.BaseSchema`**, for the reason
    :class:`StatementBatchSchema` is: that base drops a FORM's ``csrf_token``
    with ``unknown = EXCLUDE``, and this schema never sees a form --
    :func:`rule_payload` has already turned one into a list.  Inheriting it
    would silently swallow a key this schema does not declare.
    """

    class Meta:
        """Refuse an unknown key rather than dropping it silently."""

        unknown = RAISE

    rules = fields.List(
        fields.Nested(MerchantRuleSchema), required=False,
        load_default=list,
    )

    @validates_schema
    def validate_pass_is_not_too_large(self, data, **kwargs):
        """Refuse a submission answering for more merchants than one may carry.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's context, unused.

        Raises:
            ValidationError: When it names more than :data:`_MAX_RULE_ITEMS`.
        """
        total = len(data.get("rules", ()))
        if total > _MAX_RULE_ITEMS:
            raise ValidationError(
                f"That is {total:,} merchants to answer for at once, and this "
                f"page records at most {_MAX_RULE_ITEMS:,}.  Nothing was "
                f"changed.",
                field_name="rules",
            )


#: What the rule control submits for a merchant the owner has not answered
#: for, which is that row's DEFAULT.  It means *state nothing about this
#: merchant*: the route drops such an item before the service sees it, so no
#: row is written and none is removed.
#:
#: **It is rendered ONLY on a merchant with no rule** (ruling **R-GS**, plan
#: step ``bank_import:X-gd-2``).  It used to be the WITHDRAWAL as well -- pick
#: it on an answered merchant and the row was deleted -- and there is no
#: withdrawal now: *ask me every time* (:data:`ALWAYS_ASK`) is the answer that
#: replaced it, and a rule row once made is only ever restated.  A crafted body
#: submitting this for an answered merchant therefore changes nothing rather
#: than un-stating an answer, which is the direction the absence of a delete
#: door has to fail in.
#:
#: **A NAMED arm rather than the empty string, and the first draft of this
#: constant WAS the empty string and was unreachable.**  ``BaseSchema``'s
#: ``@pre_load`` normalizer drops every ``""`` a form submits, because for an
#: ordinary optional control that means *untouched*; here it means something
#: the door has to be able to read, so the drop turned ``required=True`` into
#: "this answer is missing".  Caught by this package's own wire test.  It is
#: the same correction plan step X-f6a-3c-2 made to :data:`NEW_ENVELOPE` -- an
#: arm is STATED, never inferred from an absence.
NOT_SAID: str = "unset"

#: What it submits for *never a purchase*.  A NAMED arm rather than an absence,
#: for the reason :data:`NEW_ENVELOPE` is one: "the owner said never" and "the
#: owner has not said" are different answers and the screen shows them
#: differently, so the wire has to be able to tell them apart.
NEVER: str = "never"

#: What it submits for *ask me every time*, ruling **R-GS**'s fourth answer.
#:
#: **It is a different value from :data:`NOT_SAID` even though the two have the
#: same effect on money today**, and that is the whole reason it exists: one is
#: a question the owner still owes an answer to and the other is a question
#: they have answered.  Collapsing them onto one wire value would make the
#: answer unsayable, which is precisely the mistake :data:`NEVER` exists not to
#: repeat -- and the screen that asks for rules
#: (``bank_import:X-gf``'s exception queue) reads exactly this difference.
ALWAYS_ASK: str = "ask"

#: The prefix a submitted rule answer carries, keyed by the merchant's own
#: rendered POSITION rather than by the merchant itself.  It was keyed by
#: position because a merchant was free TEXT from a bank -- spaces, apostrophes
#: and parentheses -- and a field name built from it could not be split back
#: apart reliably.  A merchant is a ROW as of plan step ``bank_import:X-gd-1``,
#: so ``rule-<merchant_id>`` would now split as cleanly as
#: ``destination-<id>`` does; the position key is KEPT because nothing depends
#: on it being unforgeable -- the merchant id travels in
#: :data:`_RULE_MERCHANT_PREFIX` and is what the door acts on -- and changing
#: it would be churn on a control ``bank_import:X-gf`` rebuilds whole.
_RULE_PREFIX = "rule-"
_RULE_MERCHANT_PREFIX = "rule_merchant-"
_RULE_NAME_PREFIX = "rule_name-"
_RULE_CATEGORY_PREFIX = "rule_category-"

#: What a TEMPLATE answer's value is prefixed with, so one control can carry
#: several kinds of answer without a second field saying which kind it is.  The
#: id half is read through the same :func:`~app.utils.digit_strings.parse_row_id`
#: every other row id on this screen goes through.
_TEMPLATE_VALUE_PREFIX = "t:"

#: What an INCOME-CATEGORY answer's value is prefixed with (**R-HT(a)**, plan
#: step ``bank_import:X-gj-2a``): *a deposit from this signature is income under
#: that category*.
#:
#: **Its arrival is what forced :class:`RuleAnswerField` to stop returning a
#: BARE int**, and that is a money fix rather than a typing preference.  The
#: field returned a sentinel string or the template's id, and
#: ``_statement_rules._rule_statements`` read *anything that is not one of the
#: sentinels* as a template id -- a fall-through.  A second id-bearing answer
#: reaching that dispatch as a bare int would have been recorded as a TEMPLATE
#: rule, so a deposit answer would file the merchant's SPENDING into a budget
#: line the owner never named for it.  It is the same shape
#: ``_placement.placements_for`` was corrected into naming its answers
#: explicitly for -- *a fifth answer one edit away from being resolved as a
#: template with a NULL template id* -- caught there at ruling **R-GS** and
#: still live here.
_INCOME_VALUE_PREFIX = "i:"

#: The answers whose wire value NAMES no row, by that value.
#:
#: **A table rather than a chain of comparisons**, for the reason
#: ``_verbs._WORDS`` is one: it is one fact per answer, and a membership test
#: followed by a separate mapping is two places for the set to disagree with
#: itself.
_NAMES_NOTHING: "dict[str, RuleAnswer]" = {
    NEVER: RuleAnswer.NEVER,
    ALWAYS_ASK: RuleAnswer.ALWAYS_ASK,
    NEW_ENVELOPE: RuleAnswer.NEW_ENVELOPE,
}

#: The answers whose wire value carries a ROW ID, by their prefix.
#:
#: **The pair is what makes the field's reading TOTAL over the answer set**:
#: every member of :class:`~app.services.statement_match.RuleAnswer` appears in
#: exactly one of these two tables, which is graded as a round trip rather than
#: by two independent cases -- the same discipline ``RuleAnswer.of`` and
#: ``_stating._columns_of`` are held to, and for the same reason: an answer
#: added to the enum and to neither table is one this control can never submit.
_PREFIXED_ANSWERS: "tuple[tuple[str, RuleAnswer], ...]" = (
    (_TEMPLATE_VALUE_PREFIX, RuleAnswer.TEMPLATE),
    (_INCOME_VALUE_PREFIX, RuleAnswer.INCOME_CATEGORY),
)

#: The most merchants one submission of the rule section may answer for.
#: **A separate ceiling from :data:`_MAX_BATCH_ITEMS`, and the distinction is
#: the subject rather than a second copy of one rule.**  That one bounds MONEY
#: acts, each of which runs a settle door and costs about 10 ms; a rule
#: statement writes at most one small row and moves nothing, and the section
#: submits every merchant it renders -- so most of a real pass is no-ops.
#: Bounded by what an account can actually show: the developer's own 361-line
#: export names 59 distinct merchants, and ``_secu_csv.MAX_LINES`` caps an
#: import at 20,000 lines.  2,000 is far past any real statement and still
#: refuses a body inventing merchants.
_MAX_RULE_ITEMS: int = 2000


def rule_payload(form) -> dict:
    """Return one pass over the rule section as :class:`MerchantRuleBatchSchema` loads it.

    **The form shape lives HERE, beside the schema that grades it**, for the
    reason :func:`batch_payload` gives.

    **Every merchant the section rendered submits an item, including the ones
    the owner did not touch.**  There is no way to tell an untouched control
    from a deliberately-repeated answer on the wire, and inventing one -- a
    hidden "what it was" field -- would be a value the submitter could forge
    into a write nobody asked for.  The SERVICE compares each answer against
    what is stored and reports only what changed, which is the same question
    asked where the answer is actually known.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        ``{"rules": [...]}``.  Raw strings: the schema is what reads them.
    """
    keys = [
        field[len(_RULE_PREFIX):] for field in form.keys()
        if field.startswith(_RULE_PREFIX)
    ]
    items = []
    for key in sorted(keys, key=order_token_key):
        merchant = form.get(f"{_RULE_MERCHANT_PREFIX}{key}")
        if merchant is None:
            # A rule answer with no merchant beside it names nothing.  It is
            # dropped rather than refused because it is unreachable from this
            # screen -- the two fields are rendered together -- and because a
            # crafted body naming an answer for nobody has asked for nothing.
            continue
        items.append({
            "merchant_id": merchant,
            "answer": form[f"{_RULE_PREFIX}{key}"],
            "envelope_name": form.get(f"{_RULE_NAME_PREFIX}{key}", ""),
            "category_id": form.get(f"{_RULE_CATEGORY_PREFIX}{key}", ""),
        })
    return {"rules": items}
