"""The UNDO's route half, stated once for the three surfaces that render it.

Plan step ``bank_import:X-gf-2``.  Releasing a match is ONE act with one
refusal story, and it is offered from three places: the RECONCILE page's two
settled tabs, where every applied act is listed (plan step
``bank_import:X-gj-1c``, ruling **R-HU**); the register, which that step
retires and ``X-gi`` deletes; and the bank-statements page, where the acts a
standing rule filed at import are receipted (ruling **R-GH**).  What differs
between them is only WHERE THE OWNER WAS -- so the target is the parameter and
the door is written once.

**A door returns the owner to the page they pressed it on**, which is why the
target is a parameter at all rather than a constant.  Both surfaces used to
post to one route that redirected to the review screen, so undoing an act from
the import receipt threw the reader onto a different page mid-receipt -- and
after this step that page does not list accepted matches at all, which would
have made the redirect land nowhere near the act.

**The URL is what says which surface**, and nothing on the wire does.  A hidden
field naming a destination would be a client-chosen redirect and an allowlist
to keep; three routes over one function make the routing table the closed set.
What a caller may add is which VIEW of its own page to come back to
(``**target_args``), and it hands those over already graded -- the Reconcile
page's tab is a :class:`~app.services.statement_match.Tab` member or a 404,
never the string it arrived as.

Route-layer module (leading underscore = route-internal), beside
:mod:`._statement_doors` and for the same reason: it consumes ``flash`` and
``url_for`` and hands the service plain ids.
"""

import logging

from flask import url_for
from flask_login import current_user

from app.exceptions import ValidationError
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    run_one_id_door,
)
from app.schemas.validation.statements import StatementMatchReleaseSchema
from app.services.statement_match import release_match

_logger = logging.getLogger(__name__)

#: One schema instance, constructed at import like every sibling's.
_release_schema = StatementMatchReleaseSchema()


def _release_report(released) -> "tuple[str, str]":
    """Return the flash for one released match: what came back, and what went.

    **The removal half is not an aside**, which is why it names a figure and
    not only a count (plan step ``bank_import:X-f6f``): this act destroys the
    app's record of money that moved, and a receipt saying "1 row" over a
    `$213.49` swipe is the *"Nothing moved."* sentence ruling **R-GD** has
    already had to correct once, one door over.

    Args:
        released: The :class:`~app.services.statement_match.ReleasedMatch`.

    Returns:
        ``(message, category)``.
    """
    removed = (
        f"  It also removed the {released.removed_rows} row(s) that match had "
        f"created, worth {released.removed_cash:+,.2f}."
        if released.removed_rows else ""
    )
    kept = (
        f"  {released.kept_containers} budget line(s) it created were kept: "
        f"something is still filed under them, or you have edited them since."
        if released.kept_containers else ""
    )
    return (
        "Match undone.  Those statement lines are unexplained again; the days "
        f"they corrected are unchanged.{removed}{kept}",
        "info",
    )


def release_and_return(account, target_endpoint: str, **target_args):
    """Undo the submitted match and send the owner back to *target_endpoint*.

    **It does NOT put the settle days back**, and both surfaces say so: the
    bank is still the best evidence the app has about when that money moved, so
    reverting a correction in order to tidy a relation would throw away the
    fact and keep the bookkeeping.  What comes back is the QUESTION.

    **It DOES remove the rows the act created** (plan step
    ``bank_import:X-f6f``, ruling **R-GG**), which is why the control carries a
    ``data-confirm`` naming them and their figure.

    **It stays a plain POST-redirect-GET** where the rule door became an htmx
    swap, and the difference is the subject rather than an inconsistency: this
    names ONE act and either does it or refuses it, so a flash carries the
    whole answer.  The rule door reports per-item outcomes no flash can hold.

    Args:
        account: The owned, attached account, proved by the calling route.
        target_endpoint: The endpoint to return to -- the page the control was
            pressed on.  Takes ``account_id``, which every statement surface
            does.
        **target_args: What else names the VIEW the owner was looking at, for
            a target that has more than one (plan step ``bank_import:X-gj-1c``).
            The Reconcile page passes which tab is open and whether the bound
            on settled acts is lifted; the two older surfaces pass nothing and
            get the URL they always got.  **Route-supplied and never read off
            the wire here**: the caller has already graded whatever it took
            from the request, so nothing a submitter can spell reaches
            ``url_for`` through this -- which is the property the module
            docstring's "no client-chosen redirect" rests on, kept rather than
            widened.

    Returns:
        A redirect to that page, carrying the receipt or the refusal.
    """
    target = url_for(target_endpoint, account_id=account.id, **target_args)
    # **The one-id door's shared VALIDATE half** (:func:`~._statement_doors
    # .run_one_id_door`, plan step ``bank_import:X-gj-4c-2``), which carries
    # WHY it exists and what the gate did not see.  Stated THERE and not here:
    # a measurement in two homes is what this very extraction is about, and
    # this copy stood with two numbers its twin had already recorded as
    # measured FALSE -- the correction was applied to one and not the other.
    # Found by an unprimed adversarial review 2026-09-04.
    return run_one_id_door(
        _release_schema, "match_id",
        StatementDoorContext(
            logger=_logger,
            refusal=ValidationError,
            log_message="user_id=%d failed to release a match on account %d",
            log_args=(current_user.id, account.id),
            flash_message=(
                "Something went wrong undoing that match.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda match_id: release_match(
            match_id, current_user.id, account.id,
        ),
        _release_report,
    )
