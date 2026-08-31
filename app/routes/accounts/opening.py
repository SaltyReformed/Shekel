"""
Shekel Budget App -- Books-opening restatement route

The door an owner corrects an account's OPENING EQUITY through (plan step
**X-f3c-2b-2a**): the card the shared account edit page renders, and the POST
that appends the restatement.

**Why it is not in :mod:`app.routes.accounts.crud`.**  That module owns account
CRUD -- a name, a type, a sort order, an active flag, all plain columns on one
row -- and this writes an append-only MONEY fact through a service that takes
the owner's write lock and re-bases the posted ledger.  It is a third subject
beside it exactly as :mod:`app.routes.accounts.history` is a third subject
beside ``detail``'s page and ``anchor``'s write door, and it is split for the
same reason: a door with its own service, its own schema and its own refusals
does not belong inside a route module that has none of them.  ``crud`` also
stood at 761 of pylint's 1000-line ceiling before this step.

**The card is rendered by the EDIT page and linked to by the balance-history
card, which is one door with two entrances rather than two doors** (developer
ruling, 2026-08-31).  The opening is DISPLAYED in exactly one place -- the
``<tfoot>`` of ``accounts/_balance_history.html``, on the cash detail page --
and that page serves three of the owner's nine accounts.  Four of the other six
(the two IRAs, the 401(k) and the Property) carry a ``migration_derived``
opening the balance fold READS, so a door reachable only from the cash card
would leave the accounts most likely to hold a wrong figure with no way to
correct it.  The shared edit form is the one surface every account kind reaches
(the cockpit card's kebab -> Edit), so the form lives there and the card points
at it.

**A full page rather than an inline editor, and that is a design decision
rather than a shortcut.**  Asserting a balance is the one-click habit this app
is built around -- five surfaces open that editor and it defaults its own date
box to today.  Restating what the books opened with is the opposite: rare,
consequential, and the one act that moves the level every balance the account
has ever rendered is stacked on.  ``app.opening_infrastructure`` calls a
restatement rare in as many words.  A deliberate page with the standing figures
pre-filled is the honest affordance for it, and it also keeps the
balance-history card what its own module says it is -- a RECORD, not a write
door.

Services boundary: this module owns the HTTP-shaped concerns (the ownership
check, the kind gate, schema validation, the flash and the redirect) and every
refusal that is about money or dates belongs to
:mod:`app.services.opening_service`.  No arithmetic happens here or in the
template.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from flask import flash, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from app.enums import AccountOpeningSourceEnum
from app.exceptions import ValidationError
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.services import cash_ledger, opening_service
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.opening_service import OpeningRestatementOutcome
from app.utils.account_validation import _opening_schema
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.utils.error_fragments import flatten_schema_errors
from app import ref_cache

logger = logging.getLogger(__name__)


#: The refusal an amortizing account's restatement gets, shared by the card
#: (which is not rendered at all) and the POST (which answers it).  A loan's
#: opening is ``LoanParams.original_principal`` and nothing reads its
#: ``budget.account_openings`` row while the loan is configured, so a door here
#: would report success and move no figure -- the twin of
#: ``anchor.LOAN_ANCHOR_REFUSAL`` one fact over.
LOAN_OPENING_REFUSAL = (
    "A loan's opening is its original principal, recorded with the loan's "
    "terms. Correct it on the loan's own page."
)


def _latest_legal_opening_day(
    earliest_movement: "date | None", earliest_assertion: "date | None",
) -> date:
    """Return the latest day this account's books may legally open on.

    The date input's ``max``, so the browser refuses what
    :mod:`app.services.opening_service` would refuse rather than round-tripping
    a rejection.  Both bounds come from the same two PRIMITIVES the service
    refuses by, never from a template literal: the owner's today
    (``display_today()``, ruling **R-DH (b)** -- the process clock is pinned to
    the display zone in the deployed container but not in CI or a script) and
    the account's earliest recorded movement, minus one day because the service
    refuses a day ON the movement as well as after it.

    **The layering is deliberate, not redundant**, and it is the argument
    ``anchor._anchor_day_bounds`` makes for its own pair: an input bound is
    captured at RENDER time, and both of these move -- the clock at midnight,
    the movement floor whenever a row is settled or re-dated -- so a form left
    open across such a change can still submit a day the service refuses.  That
    is why the refusal is also rendered rather than assumed unreachable.

    Args:
        earliest_movement: The account's earliest recorded movement day, or
            ``None`` where it records none.  **Taken rather than looked up**,
            so one render asks the question once and the ceiling and the
            sentence explaining it cannot come from two reads.
        earliest_assertion: The earliest day the owner has asserted a balance
            for, or ``None``.  The bound a code review added on 2026-08-31,
            after the ceiling offered TODAY on every account with no settled
            movement -- which is every investment, retirement and property
            account the developer holds.

    Returns:
        The latest legal ``opened_on``: the EARLIEST of the owner's today, the
        day before the account's first recorded movement, and the day of its
        first asserted balance.
    """
    candidates = [display_today()]
    if earliest_movement is not None:
        candidates.append(earliest_movement - timedelta(days=1))
    if earliest_assertion is not None:
        # NOT minus a day: an opening EQUAL to the earliest assertion is what
        # every account is created holding, so the last legal day is that day
        # itself.
        candidates.append(earliest_assertion)
    return min(candidates)


def books_opening_context(account: Account) -> "dict | None":
    """Assemble the books-opening card's context, or ``None`` to render nothing.

    Called by :func:`app.routes.accounts.crud.edit_account` so the card is part
    of the page it lives on rather than a fragment the page fetches: nothing
    about an opening changes without a full round trip through the POST below,
    so there is no event for a fragment to refresh on.

    Args:
        account: The owned, attached :class:`~app.models.account.Account`.
            Caller owns the ownership check.

    Returns:
        The card's context -- ``opened_on``, ``equity``, ``declared`` (whether
        a human stated the standing figure or the X-f3c-2a migration derived
        it), ``opened_on_max`` and ``earliest_movement`` -- or ``None`` for an
        AMORTIZING account, whose opening is its loan's original principal.
        ``None`` rather than a flag the template branches on: a card that must
        not be offered is absent, not disabled, which is the dead-end
        affordance rule ``anchor.anchor_form`` states.
    """
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return None
    # **The non-raising reader, so a broken invariant costs the card and not
    # the PAGE** (adversarial review, 2026-08-31).  ``account_opening_fact``
    # raises for an account carrying no opening row, and this builder is called
    # unconditionally by ``crud.edit_account`` -- which is the only surface
    # offering rename, archive and hard-delete.  Raising here would 500 that
    # page, so the one account that most needs repairing would be the one
    # account no door could reach.
    opening = cash_ledger.governing_account_opening(account.id)
    if opening is None:
        return None
    # ONE read of the account's earliest movement for this render, threaded to
    # both consumers: the date input's ceiling and the sentence that tells the
    # owner why it stops there.  Asking twice is this project's DRY violation
    # rather than a cost.
    earliest = cash_ledger.earliest_recorded_movement_day(account.id)
    first_assertion = cash_ledger.earliest_assertion_day(account.id)
    return {
        "opened_on": opening.opened_on,
        "equity": opening.opening_equity,
        # Whether a HUMAN stated the standing figure.  The card says so,
        # because a ``migration_derived`` opening is the pre-X-f3c-2a inference
        # frozen and may be WRONG -- findings **N-275** and **N-379** measure
        # two of the seven production figures wrong against the owner's own
        # bank -- and this is the door for exactly that case.
        "declared": opening.source_id == ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
        "opened_on_max": _latest_legal_opening_day(earliest, first_assertion),
        # The assertion bound in WORDS, beside the movement one, for the same
        # reason: an owner refused by a ceiling they cannot see reads it as the
        # form being broken.
        "earliest_assertion": first_assertion,
        # The bound in WORDS for the help text, so the owner is told why the
        # date box stops where it does instead of finding out by being
        # refused.  ``None`` when the account records no movement at all.
        "earliest_movement": earliest,
    }


def _restatement_failure(account_id: int, message: str) -> ResponseReturnValue:
    """Flash *message* and return to the edit page carrying the card.

    The ONE rejection path this door has, and it is the flash-and-redirect
    shape every other write on that page uses (``crud.update_account``,
    ``crud.archive_account``, ``crud.hard_delete_account``) rather than a
    designed HTMX fragment: this is a full-page form POST, so the page it
    returns to re-renders the card from the database with the standing figures
    -- which is the correct thing for a refused submission to show, because
    nothing was written.

    Args:
        account_id: The account whose edit page to return to.
        message: The user-facing reason, already flattened to one sentence.

    Returns:
        The redirect response.
    """
    flash(message, "danger")
    return redirect(url_for("accounts.edit_account", account_id=account_id))


@accounts_bp.route("/accounts/<int:account_id>/opening", methods=["POST"])
@login_required
@require_owner
def restate_opening(account_id):
    """Restate what an account's books opened with.

    Appends one ``budget.account_openings`` row through
    :func:`app.services.opening_service.apply_opening_restatement`, which takes
    the owner's write lock, declines a submission that changes nothing (ruling
    **R-EQ**), re-bases the account's posted anchor corrections onto the new
    day and figure, and commits.

    **Every money and date refusal belongs to the service, and this route adds
    none of its own.**  The day is bounded there -- not in the future, and
    strictly before every movement the account records (ruling **R-HG**) -- so
    the card's date-input ``max`` is a convenience the browser applies first
    and never a second answer to the same question.  The kind gate is asked
    here as well, and that is not a duplicate for the reason
    ``anchor._true_up_request_gates`` gives: an account's KIND is EDITABLE, so
    a card rendered while the account was cash can be submitted after it became
    a loan, and the service's own refusal is a ``ValueError`` that would reach
    the owner as a 500.

    **It tells the owner what a restatement does NOT do**, and that sentence is
    measured rather than cautious: the account's later assertions still say
    what they said, so the gap the restatement opens is booked as a correction
    against them.  On the developer's own archived Fidelity account, taking a
    ``$4,863.56`` opening to ``$0.00`` leaves the 2026-04-06 assertion booking a
    ``$5,363.56`` true-up and the asset returns in full.  An owner who is not
    told that reads the unchanged balance as the door having failed.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404

    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return _restatement_failure(account_id, LOAN_OPENING_REFUSAL)

    errors = _opening_schema.validate(request.form)
    if errors:
        return _restatement_failure(
            account_id, flatten_schema_errors(errors),
        )
    data = _opening_schema.load(request.form)

    try:
        outcome = opening_service.apply_opening_restatement(
            account=account,
            opening=opening_service.BooksOpening(
                opened_on=data["opened_on"],
                # ``Decimal(str(...))`` rather than the schema's own object,
                # the construction ``anchor.true_up`` uses for the same reason:
                # the project builds money from a STRING so nothing can hand a
                # binary float into a monetary path.
                equity=Decimal(str(data["opening_equity"])),
            ),
        )
    except ValidationError as exc:
        return _restatement_failure(account_id, str(exc))

    if outcome is OpeningRestatementOutcome.UNCHANGED:
        # Ruling R-EQ idempotent success: the submission states the opening
        # that already stands, so nothing was written and the service rolled
        # back.  Reported as success rather than as an error -- the state the
        # owner asked for is the state that stands -- but not as "recorded",
        # which would be false.
        flash(
            "These books already open on "
            f"{data['opened_on'].isoformat()} at ${data['opening_equity']}. "
            "Nothing was changed.",
            "info",
        )
    else:
        flash(
            "Books restated: this account now opens on "
            f"{data['opened_on'].isoformat()} holding "
            f"${data['opening_equity']}. Balances you recorded afterwards are "
            "unchanged, so the difference shows up as a correction against "
            "them -- reported as a gain or as interest on a savings, "
            "investment or property account. Your balance stays right; "
            "restating those later balances is what clears it.",
            "success",
        )
    # No log line here.  ``opening_service.stage_account_opening`` logs the
    # account, the day, the figure AND the provenance, and a route line naming
    # a strict subset of that is noise that reads as corroboration.
    return redirect(url_for("accounts.edit_account", account_id=account_id))
