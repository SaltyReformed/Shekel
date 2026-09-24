"""
Shekel Budget App -- Designed error-fragment response helpers.

The marker-header convention (closeout plan session 4, ruled 2026-07-11):
htmx's app-wide ``responseHandling`` config (``base.html``) leaves 4xx/5xx
bodies non-swapping because most of them are raw strings or JSON.  A route
that deliberately renders a DESIGNED error state -- the same partial the
request targeted, re-rendered in place with field errors or a danger
banner -- opts back in by stamping :data:`DESIGNED_FRAGMENT_HEADER` on the
response.  ONE global ``htmx:beforeSwap`` listener in ``app.js`` swaps any
response carrying the marker, replacing the per-surface target-id shims
this convention retired (``tax_checkpoint.js``, the swap block in
``retirement_controls.js``).

The marker is what lets the client distinguish a handled-error fragment
from an unhandled error document: a crash page never carries the header,
so it stays non-swapping (raw 4xx strings likewise).  409 conflict bodies
are all designed partials already and keep their unconditional swap in the
htmx config; they do not need the marker.
"""

from flask.typing import ResponseReturnValue

# Response header marking a 4xx/5xx body as a designed fragment built for
# the request's own hx-target.  Read by the global htmx:beforeSwap
# listener in app/static/js/app.js -- the two names must stay in sync.
DESIGNED_FRAGMENT_HEADER = "Shekel-Designed-Fragment"

# htmx's own response header naming the element a response swaps into
# INSTEAD of the request's hx-target (a CSS selector; htmx reads it before
# it raises htmx:beforeSwap, so the marker listener above sees the retargeted
# swap).  A designed fragment built for a region the request did not target
# carries it -- the ``/retirement/readiness`` what-if's refusal is the
# assumptions RAIL, and that request targets the readiness card.
RETARGET_HEADER = "HX-Retarget"

# The uniform user-facing message for a foreign-key ``IntegrityError`` --
# one definition shared by the transaction, entries, and transfer
# mutation handlers whose designed fragments surface it.
INVALID_REFERENCE_MSG = (
    "Invalid reference. Check that all referenced records exist."
)

# What a stale page reads when the row it acted on is not one it may name:
# a one-off row whose delete removed it from the table, a row that never
# existed, and another user's row -- one sentence for all three, so the
# uniform 404 still says nothing about which it was (plan step
# ``credit_card:CC-5-4a-4``, ruling **R-CC104**, developer 2026-09-23: *"A
# one-off row is erased, so its name is gone, and the app can't tell it apart
# from a row that was never yours. Those show 'This transaction no longer
# exists.  Reload the page.' Another user's row gets the same words, so
# nothing leaks."*).  Shared by the transaction and entries routes, at the
# three doors that ruling names.
ROW_NO_LONGER_EXISTS_MSG = "This transaction no longer exists.  Reload the page."


def refusal_for_a_gone_row(answer, refusal) -> str:
    """Return what a stale page is told about a row its door no longer serves.

    The one choice between the two answers ruling **R-CC104** gives, for the
    three doors it names: a hidden row the requester may reach is named, in
    the door's own words for the act it refused, saying "was archived" where
    its recurring item is archived and "was deleted" otherwise (ruling
    **R-CC107**); anything else gets :data:`ROW_NO_LONGER_EXISTS_MSG`, the
    same words whichever it was.

    Args:
        answer: What ``auth_helpers.get_accessible_transaction_or_deleted``
            answered when it was not a live row: a
            :class:`~app.utils.hidden_row.HiddenRow`, or ``None``.
        refusal: The door's sentence for its act, taking the
            :class:`~app.utils.hidden_row.HiddenRow` --
            ``deleted_row_payment_refusal``,
            ``deleted_row_purchase_refusal`` or the Save door's.

    Returns:
        The sentence to show.
    """
    return ROW_NO_LONGER_EXISTS_MSG if answer is None else refusal(answer)


def designed_error(
    body: str, status: int, *, retarget: str | None = None,
) -> ResponseReturnValue:
    """Wrap a rendered error fragment so htmx swaps it despite the status.

    Args:
        body: The rendered partial, built for the request's own
            ``hx-target`` (the same surface a success response would
            replace -- cell, card, entry list, form panel) -- or, with
            *retarget*, for the region that selector names.
        status: The HTTP error status (422 validation, 400 domain
            rejection, 500 handled failure).
        retarget: A CSS selector for the element the body swaps into when
            it is NOT the request's own target (:data:`RETARGET_HEADER`);
            the request's own swap style still applies.  ``None`` for a
            body built for the request's target, which is every caller but
            the readiness what-if's refusal (plan step salary:S3-f-4,
            ruling **R-SAL33**).

    Returns:
        Flask response tuple ``(body, status, headers)`` carrying the
        designed-fragment marker header, and the retarget header when one
        was asked for.
    """
    headers = {DESIGNED_FRAGMENT_HEADER: "1"}
    if retarget is not None:
        headers[RETARGET_HEADER] = retarget
    return body, status, headers


def flatten_schema_errors(errors: dict[str, list[str]]) -> str:
    """Render a Marshmallow field-error dict as one banner-ready sentence.

    The grid's cell / card / entry-list error surfaces carry a single
    message string (an icon-plus-title treatment or a one-line banner),
    not a per-field form re-render, so the validator's dict shape is
    flattened to ``"field: message"`` pairs.  The ``_schema`` pseudo-field
    (schema-level errors) has no field name worth showing and contributes
    its messages bare.

    Args:
        errors: Marshmallow ``schema.validate()`` / ``exc.messages``
            output -- field name to list of message strings.

    Returns:
        A single semicolon-joined message string.
    """
    parts = []
    for field, messages in errors.items():
        joined = " ".join(messages)
        parts.append(joined if field == "_schema" else f"{field}: {joined}")
    return "; ".join(parts)
