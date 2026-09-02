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

from app.schemas.validation._helpers import order_token_key
from app.schemas.validation.statements import (
    CATEGORY_PREFIX,
    DESTINATION_PREFIX,
    ENVELOPE_NAME_PREFIX,
    LEAVE_ALONE,
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

#: What a card's MATCH tab names the member that CARRIES that difference in,
#: keyed by its BANK LINE (plan step ``bank_import:X-gj-3a``).  Its value is
#: one of the very ``rows-<line>`` tokens the same body sends, and the empty
#: string is the select's first option -- *a new row with no category* --
#: which is the shape every group had before this step.
#:
#: **Sent as an EMPTY string rather than omitted when nothing is chosen**,
#: because a ``<select>`` always submits: the reader below is what turns that
#: empty value back into an absence, exactly as it does for an untouched
#: consent box, so the schema's ``load_default`` stays the one statement of
#: what *nothing was said* means.
_DIFFERENCE_ON_PREFIX = "difference_on-"


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
        The item, carrying ``"residual"`` only where the consent box did and
        ``"difference_on"`` only where the attribution select named a member.
        **Omitted rather than sent as ``None``**, so the schema's own
        ``load_default`` is the one statement of what absence means -- and an
        EMPTY consent, or an unchosen member, is untouched rather than
        malformed, which is :func:`_match_items`' founding principle.

    """
    item = {"line_ids": [key], "rows": form.getlist(f"{_ROWS_PREFIX}{key}")}
    residual = form.get(f"{_RESIDUAL_PREFIX}{key}")
    if residual:
        item["residual"] = residual
    attributed = form.get(f"{_DIFFERENCE_ON_PREFIX}{key}")
    if attributed:
        item["difference_on"] = attributed
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


# **NOTHING HERE READS AN "always, for this merchant" TICK**, and the absence
# is ruling **bank_import:R-IB** (developer, 2026-08-30).  This module carried
# ``_ALWAYS_PREFIX``, ``ReconcileRuleSchema``, ``ReconcileRuleBatchSchema`` and
# ``reconcile_rules_payload`` until then: a per-LINE checkbox posting a
# ``merchant_id``, graded by a second schema beside the money one.
#
# All four are gone rather than fixed, because the grain was the defect.  A
# standing rule is ONE fact per merchant and the card is one line, so the page
# asked one question 86 times on the developer's own pass; the tick had to be
# read BEFORE the money door ran, so a per-item refusal rolled back in its
# savepoint while the rule was written anyway; and the wire carried a
# ``merchant_id`` the server can derive from the line, which nothing checked
# the line agreed with.
#
# The offer is on the RECEIPT now, once per merchant, about what the door
# APPLIED -- and it posts to the door the review queue and the register
# already use, at the field names
# :func:`~app.schemas.validation.merchant_rules.rule_payload` reads.  So there
# is no reconcile-specific rule schema to write: three surfaces, one grader.
