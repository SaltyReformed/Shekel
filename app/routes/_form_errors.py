"""
Shekel Budget App -- Which Marshmallow error a form flashes

One rule for "the schema refused this payload; what do we tell the user",
shared by the transaction-template and transfer-template CRUD routes.

It was one rule with two behaviours.  ``templates.py`` picked actionable
messages out of the errors dict; ``transfers/templates.py`` flashed a fixed
"Please correct the highlighted errors and try again." for everything -- so the
SAME schema refusal explained itself on one form and not on the other.  Plan
step R2e-2 made that asymmetry visible by moving the recurrence-pattern check
into the schema: the message the transfer routes had always flashed from the
route layer would otherwise have become the generic prompt.

**Why an allowlist rather than "flash the first error".**  Most field errors
are Marshmallow's own ("Not a valid integer."), and the widget that produced
them is already visible beside the input, so surfacing them adds noise without
adding information.  The listed keys are the ones whose message states a
BUSINESS rule the user cannot see from the form -- a cross-field validator, or
a value that is well-formed but names something this application does not
model.  Those are worth saying out loud.
"""
from typing import Any

from flask import Response, flash, request

from app.routes._redirect_target import RedirectTarget

#: Marshmallow error keys whose messages are flashed VERBATIM.
#:
#: * ``is_envelope`` -- ``validate_envelope_only_on_expense``, a cross-field
#:   rule ("Purchase tracking is only available for expense templates.").
#: * ``recurrence_pattern`` -- ``RecurrencePatternField``, which refuses a
#:   well-formed row id that names no cadence the application models.  Field
#:   errors on these keys from the stock validators are rare in practice (an
#:   HTML ``<select>`` submits only what it rendered) and remain acceptable
#:   feedback when they do appear.
ACTIONABLE_FLASH_FIELDS: tuple[str, ...] = (
    "is_envelope",
    "recurrence_pattern",
)

GENERIC_VALIDATION_FLASH: str = (
    "Please correct the highlighted errors and try again."
)
"""What a form says when no error carries a message worth repeating."""


def flash_message_for_errors(errors: dict[str, Any]) -> str:
    """Pick the user-facing flash message from a Marshmallow errors dict.

    Args:
        errors: The dict returned by ``schema.validate(request.form)``.

    Returns:
        The first :data:`ACTIONABLE_FLASH_FIELDS` message present, else
        :data:`GENERIC_VALIDATION_FLASH`.  Always non-empty.
    """
    for field in ACTIONABLE_FLASH_FIELDS:
        messages = errors.get(field)
        if isinstance(messages, list) and messages:
            return str(messages[0])
    return GENERIC_VALIDATION_FLASH


def validate_form_or_redirect(
    schema: Any, redirect: RedirectTarget,
) -> Response | None:
    """Validate ``request.form`` against *schema*, flashing and redirecting on failure.

    The whole "refuse a bad payload" step of a CRUD route, in one place.  All
    four template-CRUD routes ran it inline; once plan step R2e-2 gave the two
    transfer routes the same message-picking as the two transaction routes, the
    four copies became textually identical and ``duplicate-code`` said so.
    Extracting is the honest answer to that, not a one-sided disable: the step
    genuinely is one rule, and having one door means a future route cannot
    validate a payload and then flash something else.

    Args:
        schema: The Marshmallow schema to validate ``request.form`` against.
        redirect: Where to send the user when the payload is refused --
            typically back to the form they submitted.

    Returns:
        A Flask redirect :class:`Response` when the payload is invalid (the
        caller returns it directly), or ``None`` when it is valid and the
        caller should proceed to ``schema.load``.
    """
    errors = schema.validate(request.form)
    if not errors:
        return None
    flash(flash_message_for_errors(errors), "danger")
    return redirect.to_response()


__all__ = [
    "ACTIONABLE_FLASH_FIELDS",
    "GENERIC_VALIDATION_FLASH",
    "flash_message_for_errors",
    "validate_form_or_redirect",
]
