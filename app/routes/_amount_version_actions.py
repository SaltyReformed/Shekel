"""
Shekel Budget App -- Amount-history route actions shared by both template kinds

One act, two forms.  The recurring-transaction edit page and the
recurring-transfer edit page each render the price history
:mod:`app.services.template_amount_service` writes, and each needs a control
that WITHDRAWS one entry -- the correction path for a price stamped against the
wrong date, which restating the amount cannot fix (a restatement writes a
version at the date it names and leaves the mis-dated one standing).

The two routes differ only in which model they look the template up on and where
they redirect afterwards, so the act itself lives here rather than twice: the
refusal wording, the stale-flush handling and the success flash are one
implementation, exactly as :mod:`app.routes._recurrence_form_helpers` holds the
steps those two update routes share.

Route-layer module (leading underscore = route-internal) rather than a service
because it consumes Flask ``flash`` / ``redirect`` / ``url_for``;
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
service owns the RULE (which entry may be withdrawn); this owns the response.
"""

from dataclasses import dataclass
from logging import Logger

from flask import Response, flash, redirect, url_for

from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
)
from app.routes._redirect_target import RedirectTarget
from app.services import template_amount_service


# Why a withdrawal is refused, stated as the repair rather than as the rule --
# and stated so that it is TRUE for all three reasons it fires, which an
# adversarial review found it was not: it named the earliest-entry rule even for
# an id that does not exist.  The service refuses the EARLIEST entry (every date
# before the series is priced from it), an entry whose removal would CHANGE the
# current amount (that is a price change, not a record correction), and an id
# this template does not own -- which is indistinguishable from a missing one, so
# the message leaks no existence either way.
_REFUSED_MESSAGE: str = (
    "That amount history entry could not be removed. The earliest entry stays, "
    "because every date before it is priced from it, and an entry that would "
    "change the current amount is changed from the Amount field instead. To "
    "re-date an entry, record the amount at the date you want first, then "
    "remove this one."
)


@dataclass(frozen=True)
class AmountVersionAction:
    """Where one kind's amount-history withdrawal reports back to.

    Bundled because the three fields are one cohesive concept -- the calling
    route's identity -- and passing them separately takes
    :func:`withdraw_amount_version` past pylint's argument threshold for no
    design gain.

    Attributes:
        logger: The calling route module's logger, so a stale-flush conflict is
            attributed to ``app.routes.templates`` or
            ``app.routes.transfers.templates`` exactly as its siblings are.
        edit_endpoint: The kind's edit-form endpoint, resolved with
            ``template_id`` for every redirect out of this action.
        noun: How the kind is named to the user in the stale-conflict flash
            ("recurring transaction" / "recurring transfer").
    """

    logger: Logger
    edit_endpoint: str
    noun: str


def withdraw_amount_version(
    template, version_id: int, action: AmountVersionAction,
) -> Response:
    """Withdraw one entry from *template*'s amount history and report back.

    Ownership is the CALLER's ``get_or_404`` on the template: the service looks
    the version up inside that template's own collection, so a ``version_id``
    belonging to another user's template is simply not found and its refusal is
    indistinguishable from "no such entry" (the security response rule -- 404
    for both not-found and not-yours, worn here as one refusal message for
    both).

    Args:
        template: The owned transaction or transfer template.
        version_id: The amount-version primary key to withdraw.
        action: The calling route's identity (:class:`AmountVersionAction`).

    Returns:
        A redirect back to the kind's edit form -- carrying the refusal, the
        stale-conflict warning, or the success flash.
    """
    template_id = template.id
    back = RedirectTarget(action.edit_endpoint, {"template_id": template_id})

    if not template_amount_service.delete_amount_version(template, version_id):
        flash(_REFUSED_MESSAGE, "warning")
        return back.to_response()

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=action.logger,
        log_label="withdraw_amount_version",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(noun=action.noun),
        redirect=back,
    ))
    if conflict is not None:
        return conflict

    flash("Amount history entry removed.", "success")
    return redirect(url_for(action.edit_endpoint, template_id=template_id))


__all__ = [
    "AmountVersionAction",
    "withdraw_amount_version",
]
