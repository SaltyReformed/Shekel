"""
Shekel Budget App -- What the RECONCILE page's form submits

Plan step ``bank_import:X-gj-1b``.  One reader per act the page carries, and
the schemas that grade the one act :mod:`.statements` does not already have a
schema for.

**The FORM shape lives beside the schema that grades it**, which is the rule
:mod:`.statements` states for its own readers and the reason neither lives in
a route: a route that listed the field names itself is a route that can be
extended with a fourth kind of item and not updated.  This is a SIBLING of
that module rather than more of it, because the two are different surfaces --
and because one file holding both crossed pylint's 1,000-line ceiling, which
is finding **balance:N-365**: in a corpus where the docstrings ARE the design
record, meeting that ceiling by cutting prose is the harm that finding names.

**The money DOOR and its schemas are unchanged and shared.**  Everything here
produces payloads :class:`~.statements.StatementBatchSchema` and
:class:`~.statements.StatementMatchSchema` load, so the Reconcile page, the
review queue and the hand-build workbench are graded by ONE set of rules and
reach ONE applier.  A second schema would be free to grade ``residual`` less
strictly than the one beside it, which is what that field's own docstring
records having cost once.

**Nothing here validates.**  Every value moved is a raw submitted string, so a
forged id, an unparseable figure and a destination naming no row are all the
schemas' to refuse -- one grader, in one place, with one error structure the
route already knows how to render.
"""

from marshmallow import RAISE, Schema, fields, validate

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    order_token_key,
)
from app.schemas.validation.statements import (
    CATEGORY_PREFIX,
    DESTINATION_PREFIX,
    ENVELOPE_NAME_PREFIX,
    LEAVE_ALONE,
    MAX_BATCH_ITEMS,
)
from app.services.statement_match import RECORD_AS_INCOME, Verb


#: What a RECONCILE card's OK checkbox is named with, its value being the bank
#: line it consents to.  Plan step ``bank_import:X-gj-1b``, rulings **R-HS**
#: and **R-FP**.
#:
#: **Presence IS the consent, and that is what makes ruling R-HS structural.**
#: R-HS pre-fills a justified suggestion -- the destination a standing rule
#: names arrives selected -- and then says *an untouched card is not
#: submitted: OK per card and Apply are the consent*.  Those two cannot both
#: be true of :func:`batch_payload`'s form, where the destination select IS
#: the tick, so a pre-filled select on an untouched card would write a
#: purchase.  Here the select is only ever READ for a line whose OK checkbox
#: is in the body, and a browser submits a checkbox only when it is ticked.
_OK_FIELD = "ok"

#: What a card's four verb tabs are named with, keyed by its BANK LINE.  Plan
#: step ``bank_import:X-gj-1b``, rulings **R-HP** and **R-HW**.
#:
#: **The tab a card is left on IS the verb it is OK'd with**, which is the
#: locked direction's own "one primary button NAMED BY THE VERB".  They are a
#: RADIO GROUP rather than JavaScript state, so the panel needs no script to
#: switch tabs and the act the owner chose is a submitted fact rather than an
#: inference from which parameters happen to be present.
_VERB_PREFIX = "verb-"

#: What a card's MATCH tab ticks a candidate row with, keyed by its BANK LINE.
#: One token per row carrying its kind, id, reviewed figure and reviewed
#: revision (:class:`ReviewedRowField`), for the reason
#: :func:`_match_items` gives: two parallel lists are desynchronised by a body
#: submitting different lengths, and one token cannot be desynchronised from
#: itself.
_ROWS_PREFIX = "rows-"

#: What a card's MATCH tab carries the accepted difference in, keyed by its
#: BANK LINE.  The SERVER's own figure (:attr:`~app.services.statement_match
#: .HandTotals.consent`), which the door re-derives and refuses if the two
#: disagree.
_RESIDUAL_PREFIX = "residual-"


def _reconcile_creation(form, key: str, destination: str) -> dict:
    """Return one OK'd ADD card as the creation item the schema loads.

    Args:
        form: The request's ``MultiDict``.
        key: The bank line's id, as submitted.
        destination: What that line's destination control named.

    Returns:
        The item.  Raw strings: the schema is what reads them.
    """
    return {
        "line_id": key,
        "destination": destination,
        "envelope_name": form.get(f"{ENVELOPE_NAME_PREFIX}{key}", ""),
        "category_id": form.get(f"{CATEGORY_PREFIX}{key}", ""),
    }


def reconcile_match_payload(form, key: str) -> dict:
    """Return one Reconcile card's MATCH tab as :class:`StatementMatchSchema`
    loads it.

    **Two callers, one reader**: :func:`reconcile_payload` reads it for a card
    the owner OK'd, and the panel's own live-difference fragment reads it for
    the card being ticked -- so the figure on screen and the figure the door
    compares against come from ONE reading of one body, which is the rule
    :func:`~app.services.statement_match.preview_hand_build` exists to keep.

    Args:
        form: The request's ``MultiDict``.
        key: The bank line's id, as submitted.

    Returns:
        The item, carrying ``"residual"`` only where the consent box did.
        **Omitted rather than sent as ``None``**, so the schema's own
        ``load_default`` is the one statement of what absence means -- and an
        EMPTY consent is untouched rather than malformed, which is
        :func:`_match_items`' founding principle.

    """
    item = {"line_ids": [key], "rows": form.getlist(f"{_ROWS_PREFIX}{key}")}
    residual = form.get(f"{_RESIDUAL_PREFIX}{key}")
    if residual:
        item["residual"] = residual
    return item


def reconcile_payload(form) -> "tuple[dict, tuple[str, ...]]":
    """Return one Reconcile pass as :class:`StatementBatchSchema` loads it.

    Plan step ``bank_import:X-gj-1b``.  **A second reader for one schema and
    one door**, which is what :func:`hand_match_payload` already is: the
    Reconcile page and the review queue apply the same acts through
    :func:`~app.services.statement_match.apply_reviewed`, and a second SCHEMA
    would be free to grade ``residual`` less strictly than the one beside it.
    What differs is the FORM, and the form shape lives here beside the schema
    that grades it.

    **It is keyed by BANK LINE and carries no rendered position at all**,
    where :func:`batch_payload` keys its ticks by the proposal's position on
    the page.  That is ruling **R-HC**'s own lesson taken one surface further:
    a position is a property of the DOCUMENT, and two acts that share a
    namespace are one ``hx-include`` away from being unioned into an act
    nobody assembled.  One card, one line id, everywhere.

    **The OK checkbox is the consent and the VERB radio is the act**, so
    nothing here infers an arm from an absence -- the defect that made the
    existing-envelope arm unreachable from a browser at plan step X-f6a-3b.
    A card OK'd on a verb this build has no door for (TRANSFER, SKIP), or on
    ADD with nothing chosen, produces no item and is REPORTED rather than
    dropped: see the second half of the return value.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        ``(payload, ok_with_no_act)``.  The payload is
        ``{"matches": [...], "creations": [...], "incomes": [...]}``, in
        ascending bank-line order through the same
        :func:`~app.schemas.validation._helpers.order_token_key` every other
        reader here uses, because the receipt this order becomes is meant to
        read down the page.  ``ok_with_no_act`` is every submitted OK that
        named no act at all, as the raw keys, so the screen can say which
        cards it did nothing for instead of leaving a press unanswered.

    """
    matches: list = []
    creations: list = []
    incomes: list = []
    silent: list = []
    for key in sorted(set(form.getlist(_OK_FIELD)), key=order_token_key):
        verb = form.get(f"{_VERB_PREFIX}{key}", "")
        if verb == Verb.MATCH.value:
            matches.append(reconcile_match_payload(form, key))
            continue
        destination = (
            form.get(f"{DESTINATION_PREFIX}{key}", LEAVE_ALONE)
            if verb == Verb.ADD.value else LEAVE_ALONE
        )
        if destination == RECORD_AS_INCOME:
            # **One id and nothing to unpack** (ruling **bank_import:R-GW**):
            # an income row is filed against no container, so the arm the
            # control names is the whole of what the wire has to say.
            incomes.append({"line_id": key})
        elif destination != LEAVE_ALONE:
            creations.append(_reconcile_creation(form, key, destination))
        else:
            silent.append(key)
    return (
        {"matches": matches, "creations": creations, "incomes": incomes},
        tuple(silent),
    )


#: What a Reconcile card's *always, for this merchant* checkbox is named with,
#: keyed by its BANK LINE, its value being the merchant the card is about.
#: Plan step ``bank_import:X-gj-1b``, ruling **bank_import:R-GI**.
#:
#: **The merchant is an ID and never the bank's own string** (plan step
#: ``bank_import:X-gd-1``): the rule form used to post the description back,
#: so the door's whole defence against a crafted merchant was a comparison in
#: Python.  It posts the merchant ROW now, which
#: ``fk_merchant_rules_merchant_account`` holds to this account and
#: :func:`~app.services.statement_match.state_rules` checks against this
#: account's own set.
_ALWAYS_PREFIX = "always-"


class ReconcileRuleSchema(BaseSchema):
    """Validate ONE *always, for this merchant* tick.

    **Two ids and nothing else.**  What the rule SAYS is not on the wire: it
    is the destination the same card submitted, read back through
    :func:`~app.services.statement_match.rule_naming`, so the rule and the
    purchase can never describe different budget lines.  A wire value naming
    the answer would be a second statement of one choice, free to disagree
    with the select beside it.

    **Both are :class:`~app.schemas.validation._helpers.RowId`, not
    ``fields.Integer``** (plan step X-ae, finding **N-141**): they name ROWS,
    and ``Integer`` reads ``'١٢'``, ``' 12 '``, ``'+12'``, ``'1_0'``,
    ``'007'``, ``'-5'`` and ``'0'`` as ids -- two of which name no row at all.
    """

    line_id = RowId(
        required=True,
        error_messages={"required": "Which line is that rule about?"},
    )
    merchant_id = RowId(
        required=True,
        error_messages={"required": "Which merchant is that rule about?"},
    )


class ReconcileRuleBatchSchema(Schema):
    """Validate every *always* tick one Reconcile pass carried.

    **A SECOND grader beside :class:`StatementBatchSchema`, and that is the
    shipped separation rather than a new one**: stating where a merchant's
    spending goes moves no money, and the developer's ruling of 2026-08-19
    gave it its own door for exactly that reason.  One schema carrying both
    would have to refuse a whole money pass over a preference.
    """

    class Meta:
        """Refuse anything the form did not declare."""

        unknown = RAISE

    rules = fields.List(
        fields.Nested(ReconcileRuleSchema),
        required=False, load_default=list,
        validate=validate.Length(max=MAX_BATCH_ITEMS),
    )


def reconcile_rules_payload(form) -> dict:
    """Return every *always* tick as :class:`ReconcileRuleBatchSchema` loads it.

    **Presence IS the tick**: a browser submits a checkbox only when it is
    ticked, so an item exists here exactly when the owner asked for a standing
    rule, and there is no do-nothing value to drop.

    **It reads every tick, not only the OK'd ones**, and the narrowing to what
    was actually recorded happens where the creations are known -- because a
    rule about a line the owner did not OK is a rule about a destination they
    did not confirm, and the caller is what holds both halves.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        ``{"rules": [{"line_id": ..., "merchant_id": ...}]}``, ascending by
        bank line through the same
        :func:`~app.schemas.validation._helpers.order_token_key` every other
        reader here uses.  Raw strings: the schema is what reads them.
    """
    keys = [
        field[len(_ALWAYS_PREFIX):] for field in form.keys()
        if field.startswith(_ALWAYS_PREFIX)
    ]
    return {
        "rules": [
            {"line_id": key, "merchant_id": form[f"{_ALWAYS_PREFIX}{key}"]}
            for key in sorted(keys, key=order_token_key)
        ],
    }
