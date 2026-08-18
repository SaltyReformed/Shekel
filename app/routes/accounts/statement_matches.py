"""
Shekel Budget App -- The statement review screen and its two write doors

"Which of my rows is this bank line?" -- the page that proposes matches, and
the POSTs that accept and release one.  Plan step **bank_import:X-f6a-2**,
rulings **R-FS**, **R-FP** and **R-FV**.

**It MOVES MONEY, and it is the only screen in the app where the BANK gets the
last word on a date.**  Accepting a match writes the bank's posted day onto
every row the match names -- settling one still Projected and correcting one
whose recorded day was wrong.  Measured on the developer's own 2026-08-16
export against a production clone: 124 proposals over 231 in-schedule lines, of
which 46 correct a day and 51 settle a row the app had never marked as having
happened.

**Nothing is applied that the owner did not accept** (ruling **R-FP**).  The
GET proposes; the POST records exactly the ids the form submitted, and the
service re-derives every figure from them rather than trusting the page.

**Why it is its own module beside ``statements``.**  That one owns what the
BANK SAID -- recording a file, idempotently, moving no figure.  This owns what
the app DOES about it, which is a write door onto ``settled_on`` on three row
kinds.  The boundary is the one ``reconcile`` and ``anchor`` cut along: a read
of an outside record against the door that acts on it.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, form
parsing, flashes and redirects -- and delegates every read and write to
:mod:`app.services.statement_match`.
"""

import logging

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.exceptions import ValidationError
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    run_statement_door,
)
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.schemas.validation import form_payload
from app.schemas.validation.statements import (
    StatementMatchReleaseSchema,
    StatementMatchSchema,
)
from app.services.statement_match import (
    MatchSubmission,
    accept_match,
    release_match,
    review_set,
)
from app.utils.auth_helpers import require_owner

_logger = logging.getLogger(__name__)

#: One schema instance each, constructed at import like every sibling's.
_match_schema = StatementMatchSchema()
_release_schema = StatementMatchReleaseSchema()


def _messages(errors):
    """Yield every message in a Marshmallow error structure, however nested.

    **A LIST field's errors are keyed by INDEX, not flat**, so
    ``{"transaction_ids": {0: ["Not a valid integer."]}}`` is the ordinary
    shape here rather than an exotic one -- and a flattener assuming
    ``{field: [str]}`` raises ``TypeError`` inside the handler that exists to
    render a refusal.  Reached by submitting one bad id, which is what a stale
    page does; caught by the route test that submits ``'007'``.

    Args:
        errors: A Marshmallow error value -- a mapping, a list, or a message.

    Yields:
        Each leaf message, in the order marshmallow reports them.
    """
    if isinstance(errors, dict):
        for value in errors.values():
            yield from _messages(value)
    elif isinstance(errors, (list, tuple)):
        for value in errors:
            yield from _messages(value)
    else:
        yield str(errors)


def _flash_errors(errors) -> None:
    """Flash a schema's messages as one warning.

    Args:
        errors: Marshmallow's error structure, at any nesting.
    """
    flash("; ".join(_messages(errors)), "warning")


@accounts_bp.route("/accounts/<int:account_id>/statements/review")
@login_required
@require_owner
def review_statements(account_id):
    """Render what the bank says, matched against what this account holds.

    Args:
        account_id: The account whose statement lines to review.

    Returns:
        The rendered page, or a 404 when the account is not the caller's or is
        a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/statement_review.html",
        account=account,
        review=review_set(current_user.id, account_id),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review", methods=["POST"],
)
@login_required
@require_owner
def accept_statement_match(account_id):
    """Record that the submitted bank lines ARE the submitted rows.

    The unit of work is the request: the service stages and flushes, this
    commits, and any refusal rolls the whole thing back -- which is what makes
    "nothing was changed", the phrase every refusal message ends with, true
    rather than reassuring.

    Args:
        account_id: The account being reviewed.

    Returns:
        A redirect back to the review page, with a flash saying what moved.
    """
    account = load_cash_account_or_404(account_id)
    target = url_for("accounts.review_statements", account_id=account_id)

    # THROUGH ``form_payload``, not the raw ``MultiDict``: a repeated form key
    # is what a GROUP match posts, and ``request.form["transaction_ids"]``
    # returns only the first of them.  The helper's own docstring carries why
    # the expansion is the schema's business rather than this route's.
    payload = form_payload(request.form, _match_schema)
    errors = _match_schema.validate(payload)
    if errors:
        _flash_errors(errors)
        return redirect(target)
    submitted = _match_schema.load(payload)

    return run_statement_door(
        StatementDoorContext(
            logger=_logger,
            refusal=ValidationError,
            log_message="user_id=%d failed to accept a match on account %d",
            log_args=(current_user.id, account_id),
            flash_message=(
                "Something went wrong recording that match.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda: accept_match(MatchSubmission(
            owner_id=current_user.id,
            account_id=account.id,
            line_ids=frozenset(submitted["line_ids"]),
            transaction_ids=frozenset(submitted["transaction_ids"]),
            entry_ids=frozenset(submitted["entry_ids"]),
        )),
        lambda accepted: (_accepted_message(accepted), "success"),
    )


def _accepted_message(accepted) -> str:
    """Return the sentence describing what an accepted match did.

    **It names the two effects separately**, because they are different acts
    with different consequences: settling a row books money the projection was
    still holding forward, and correcting a day moves money already booked from
    one day to another.  A single "2 rows updated" would hide which happened.

    Args:
        accepted: The :class:`~app.services.statement_match.AcceptedMatch`.

    Returns:
        The flash text.
    """
    did = []
    if accepted.settled_count:
        did.append(
            f"marked {accepted.settled_count} row(s) as having happened"
        )
    if accepted.corrected_count:
        did.append(
            f"moved {accepted.corrected_count} row(s) onto the bank's day"
        )
    what = " and ".join(did) if did else "confirmed what you already had"
    return (
        f"Matched {accepted.line_count} statement line(s) worth "
        f"{accepted.amount:+,.2f} on {accepted.posts_on}: {what}."
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review/release", methods=["POST"],
)
@login_required
@require_owner
def release_statement_match(account_id):
    """Undo one match, putting its bank lines back among the unexplained.

    **It does NOT put the settle days back**, and the page says so: the bank is
    still the best evidence the app has about when that money moved, so
    reverting a correction in order to tidy a relation would throw away the
    fact and keep the bookkeeping.  What comes back is the QUESTION.

    Args:
        account_id: The account being reviewed.

    Returns:
        A redirect back to the review page.
    """
    account = load_cash_account_or_404(account_id)
    target = url_for("accounts.review_statements", account_id=account_id)

    payload = form_payload(request.form, _release_schema)
    errors = _release_schema.validate(payload)
    if errors:
        _flash_errors(errors)
        return redirect(target)

    match_id = _release_schema.load(payload)["match_id"]
    return run_statement_door(
        StatementDoorContext(
            logger=_logger,
            refusal=ValidationError,
            log_message="user_id=%d failed to release a match on account %d",
            log_args=(current_user.id, account_id),
            flash_message=(
                "Something went wrong undoing that match.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda: release_match(match_id, current_user.id, account.id),
        lambda _released: (
            "Match undone.  Those statement lines are unexplained again; the "
            "days they corrected are unchanged.",
            "info",
        ),
    )
