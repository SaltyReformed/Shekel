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

# The uniform user-facing message for a foreign-key ``IntegrityError`` --
# one definition shared by the transaction, entries, and transfer
# mutation handlers whose designed fragments surface it.
INVALID_REFERENCE_MSG = (
    "Invalid reference. Check that all referenced records exist."
)


def designed_error(body: str, status: int) -> ResponseReturnValue:
    """Wrap a rendered error fragment so htmx swaps it despite the status.

    Args:
        body: The rendered partial, built for the request's own
            ``hx-target`` (the same surface a success response would
            replace -- cell, card, entry list, form panel).
        status: The HTTP error status (422 validation, 400 domain
            rejection, 500 handled failure).

    Returns:
        Flask response tuple ``(body, status, headers)`` carrying the
        designed-fragment marker header.
    """
    return body, status, {DESIGNED_FRAGMENT_HEADER: "1"}


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
