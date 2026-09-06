"""What this account has already DECIDED: the answers given, the acts accepted.

Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  The review
screen is the exception QUEUE -- what is still being decided -- and this is its
other half: a merchant answer the owner has already given, and a match they
have already accepted, are records of decisions made.  Neither is work, and
both were sitting between the owner and the two or three lines a routine import
actually asks about.

**Measured on the developer's own dev data 2026-08-27**, which is why the split
is a step rather than a preference: the review body rendered 578,523 bytes, of
which the merchant control was 225,472 (30 rows, 29 of them answers already
given) and the accepted-matches panel 216,637 (221 acts) -- **76% of the page
was two registers of decisions already made**, while the work a routine import
leaves was 136,414 bytes.

**This page MOVES MONEY through exactly one control**, and it is the undo:
releasing a match removes the rows that act created (ruling **R-GG**).  Stating
a merchant rule here moves none, exactly as it moves none on the queue -- a
rule is read to SUGGEST, and only a destination submitted for one specific line
records a purchase (ruling **R-FZ**).

**It costs no** :class:`~app.services.statement_match.ReviewScope`, and that is
not a saving but the point.  A merchant answer is one table read
(``merchant_rules`` joined to ``merchants``, plan step ``bank_import:X-gd-1``)
and an accepted act is another; neither needs the pay calendar, the candidate
derivation or the matcher.  Folding them into the review pass is what made the
queue pay for them.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, form
parsing, fragment rendering, flashes and redirects -- and delegates every read
and write to :mod:`app.services.statement_match`.
"""

import logging
from dataclasses import replace

from flask import render_template, request
from flask_login import current_user, login_required

from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._statement_release import release_and_return
from app.routes.accounts._statement_rules import record_submitted_rules
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    REGISTER_LIMIT,
    merchant_register,
    register_set,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error

_logger = logging.getLogger(__name__)

#: The partial both the page and the rule POST render.  ONE template, so what
#: a save swaps in cannot drift from what a reload shows -- the discipline the
#: review body already keeps for the same reason.
_BODY = "accounts/_statement_register_body.html"

def _derived(account, *, show_all):
    """Derive everything the register shows, once.

    Args:
        account: The owned, attached account.
        show_all: Whether the owner asked for the whole accepted record rather
            than the bounded one (:data:`~app.services.statement_match
            .REGISTER_LIMIT`).

    Returns:
        The :class:`~app.services.statement_match.StatementRegister`.
    """
    return register_set(
        current_user.id, account.id,
        None if show_all else REGISTER_LIMIT,
    )


def _register_context(
    account, register, *, show_all, rules=None, error=None,
) -> dict:
    """Assemble what the register body renders, for the page and for the POST.

    ONE builder, because the POST's answer IS the screen: a second assembly
    would let the surface a save swaps in disagree with the surface a reload
    shows.

    **The derivation is the CALLER's and is passed in**, which is the same rule
    the review screen keeps and for the sharper half of the same reason.  A
    refusal must render WITHOUT reading again: on the database arm the
    connection that produced the first error very likely produces a second,
    which escapes as an unhandled 500 that htmx will not swap -- so the owner
    presses Save and sees nothing at all.  So the door derives BEFORE it
    writes, hands that value to every refusal, and derives a FRESH one only on
    the path that WROTE -- where a stale one would show the answers the pass
    had just replaced.

    Args:
        account: The owned, attached account.
        register: What to render (:func:`_derived`).
        show_all: Whether the owner asked for the whole accepted record.
        rules: The :class:`~app.services.statement_match.StatedRules` a pass
            over the merchant control produced, or ``None`` on a plain render.
        error: A sentence explaining why nothing was saved at all, or ``None``.
            Distinct from a refused ITEM: this one means the submission never
            reached the door.

    Returns:
        The template context.
    """
    return {
        "account": account,
        "register": register,
        "show_all": show_all,
        # The picker the NEW-ENVELOPE answer needs.  The same ordering every
        # category picker in the app shares.
        "categories": list_active_categories(current_user.id),
        "rules": rules,
        "error": error,
    }


def _asked_for_everything() -> bool:
    """Return whether the request asked for the whole accepted record.

    A PRESENCE test and not a value one: the link either carries the flag or
    it does not, so there is no spelling of it to parse and no value to
    refuse.  What a crafted request can ask for is the page it would get by
    following the link the page already renders.

    **The contract is graded at every spelling** -- one literal was all any
    test exercised, and narrowing this to a typed value would have kept the
    suite green while ``?all=`` stopped lifting anything.  The case that grades
    it reaches in through the module, which is this project's allowance for a
    test of a private name; widening the surface for a test instead would be
    the API nobody asked for that rule 13 forbids.

    Returns:
        Whether the bound is lifted for this render.
    """
    return "all" in request.args


@accounts_bp.route("/accounts/<int:account_id>/statements/register")
@login_required
@require_owner
def statement_register(account_id):
    """Render what this account has already decided.

    Args:
        account_id: The account whose decisions to show.

    Returns:
        The rendered page, or a 404 when the account is not the caller's or is
        a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    show_all = _asked_for_everything()
    return render_template(
        "accounts/statement_register.html",
        **_register_context(
            account, _derived(account, show_all=show_all), show_all=show_all,
        ),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/register/merchants",
    methods=["POST"],
)
@login_required
@require_owner
def restate_merchant_rules(account_id):
    """Change what this owner has already said about a merchant.

    **It MOVES NO MONEY and can move none** -- the same ruling of 2026-08-19
    the queue's own rule door is held to.  A rule is read to SUGGEST a
    destination on the review screen; the only thing that records a purchase is
    an explicit destination submitted for one specific line.

    **It is its own door rather than the queue's** because the surface is the
    answer: this one re-renders the register, and the queue's re-renders the
    queue, where a new answer also changes which lines are parked and what each
    remaining line is placed against.  One door serving both would have to be
    told which surface it was posted from, and the URL says it already.

    **An answer here is never WITHDRAWN, only restated** (ruling **R-GS**), so
    this door cannot empty the register: every merchant it renders keeps its
    row whatever is submitted.

    Args:
        account_id: The account being reviewed.

    Returns:
        The re-rendered register body carrying what was recorded, at 200; or
        the same body carrying one refusal sentence at 400, marked as a
        designed fragment so htmx swaps it.
    """
    account = load_cash_account_or_404(account_id)
    show_all = _asked_for_everything()
    # BEFORE the write, and reused by every refusal -- see
    # :func:`_register_context` for what re-deriving on the database arm costs.
    before = _derived(account, show_all=show_all)

    outcome = record_submitted_rules(
        request.form, current_user.id, account_id, _logger,
    )
    if outcome.refusal is not None:
        return _refused(account, before, outcome.refusal, show_all=show_all)
    # A FRESH derivation of the ANSWERS, and only of them.  They are exactly
    # what this pass replaced -- and the accepted acts are exactly what it
    # CANNOT have touched, ``state_rules`` writing one table and no act, so
    # re-deriving the whole register here would re-fold every act on the
    # account to show a changed sentence.  That fold is what this step took
    # off the review screen (adversarial review 2026-08-27).
    return render_template(
        _BODY,
        **_register_context(
            account,
            replace(
                before,
                merchants=merchant_register(current_user.id, account_id),
            ),
            show_all=show_all, rules=outcome.recorded,
        ),
    )


def _refused(account, register, message: str, *, show_all: bool):
    """Re-render the register body carrying *message*, as a designed 400.

    **The re-render happens AFTER the rollback**, so the surface describes the
    state that survives rather than one about to be discarded.  It carries the
    designed-fragment marker because htmx leaves a 4xx non-swapping, and a
    refusal that renders NOTHING reads as a broken button.

    **It reuses the request's OWN derivation rather than taking a second one**,
    and that is a correctness fix rather than a saving -- the discipline
    ``statement_matches._refused`` states for the same shape one screen over.
    A refused pass wrote nothing, so what was derived before it still describes
    the state that survives; and re-deriving would run the very read whose
    failure this arm is handling.

    Args:
        account: The owned, attached account.
        register: The request's derived view, still valid because nothing was
            written.
        message: The user-facing reason, one sentence.
        show_all: Whether this render is the unbounded one.

    Returns:
        The designed-fragment ``(body, 400, headers)`` triple.
    """
    return designed_error(
        render_template(
            _BODY,
            **_register_context(
                account, register, show_all=show_all, error=message,
            ),
        ),
        400,
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/register/release",
    methods=["POST"],
)
@login_required
@require_owner
def release_from_register(account_id):
    """Undo one accepted match and come back to the register.

    The act, its refusals and its receipt are
    :func:`~._statement_release.release_and_return`'s; what this route owns is
    the ownership proof and the page to return to.

    Args:
        account_id: The account being reviewed.

    Returns:
        A redirect back to the register.
    """
    account = load_cash_account_or_404(account_id)
    return release_and_return(account, "accounts.statement_register")
