"""
Shekel Budget App -- The statement import page, its write door and its undo

"What did my bank actually say?" -- the page that records a statement, the POST
that performs one, and the POST that takes one back.  Plan steps
**bank_import:X-f6a-1** and **X-f6a-4**, ruling **R-FP**.

**The undo is here because a refusal owes one** (finding **N-302**).  Recording
what a bank said is append-only by design -- an observation quietly rewritten is
what ruling **R-FL** exists to prevent -- and that left every refusal terminal:
a restated line, or a first import that named the wrong Shekel account, ended
that account's ability to import at all, while the message told the owner it
"needs a human before anything overwrites it", a promise nothing in ``app/``
could keep.  ``delete_statement_import`` is the human's hands.  It destroys what
the BANK said and moves no money: a settle day an accepted match wrote is the
app's own record and stays.

**It records and does not reconcile, and the page says so.**  Nothing here
touches a transaction, a purchase, a status or a balance: an import writes
``budget.bank_statement_lines`` and stops.  Matching a recorded line to the
app's own rows, and correcting a ``settled_on`` from it, is the next leaf
(``X-f6a-2``).  Telling the user that plainly is part of the design rather than
a caveat -- an import screen that looked like it reconciled would be read as
having reconciled.

**Why it lives in the accounts package.**  A statement is a fact about ONE
account, the account is how ownership is checked, and the mapping from a bank's
account to a Shekel account is per-account state.  The subject boundary against
its siblings is the same one ``reconcile`` and ``difference`` cut along:
``anchor`` owns the balance ASSERTION's write door, ``reconcile`` owns what is
still OUTSTANDING against one, and this owns what the BANK said, which none of
them has ever had.

**It gates on the account KIND as well as on ownership**
(:func:`~app.routes.accounts._cash_page.load_cash_account_or_404`), because a
loan, a property or a 401(k) has no bank statement to import and its own detail
page 404s -- so without that gate this page renders for them with a back link
that dead-ends, and records bank lines against an account with no cash ledger.
The gate exists because the project has already paid twice for its absence.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, the
upload, form parsing, flashes and redirects -- and delegates every read and
write to :mod:`app.services.statement_import`.
"""

import logging

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.enums import StatementSourceEnum
from app.exceptions import StatementImportError, ValidationError
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    refusal_sentence,
    run_statement_door,
)
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.schemas.validation import form_payload
from app.schemas.validation.statements import (
    StatementImportDeleteSchema,
    StatementUploadSchema,
)
from app.services.statement_import import (
    available_sources,
    delete_import,
    import_history,
    recent_lines,
    record_statement,
    recorded_span,
)
from app.utils.auth_helpers import require_owner
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_IMPORT_DELETED,
    EVT_STATEMENT_IMPORTED,
    log_event,
)

_logger = logging.getLogger(__name__)

#: The form field the file arrives in.
_FILE_FIELD = "statement_file"

#: One schema instance each, constructed at import like every sibling's.
_upload_schema = StatementUploadSchema()
_delete_schema = StatementImportDeleteSchema()


@accounts_bp.route("/accounts/<int:account_id>/statements")
@login_required
@require_owner
def statements(account_id):
    """Render what the bank has said about this account, and the upload form.

    Args:
        account_id: The account whose statements to show.

    Returns:
        The rendered page, or a 404 when the account is not the caller's or is
        a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)

    return render_template(
        "accounts/statements.html",
        account=account,
        sources=available_sources(),
        span=recorded_span(account_id),
        imports=import_history(account_id),
        lines=recent_lines(account_id),
    )


def _import_flash(outcome):
    """Return the flash text and category for a successful import.

    **A file the importer could not cross-check gets a WARNING, not a green
    tick**, and that is an honest report rather than a nicety.  The
    running-balance column is an export OPTION, so a statement can arrive with
    no per-line chain to verify -- and the page asks for that option precisely
    because "without it a missing line cannot be detected".  Saying "Recorded
    361 new lines" in green over a statement nothing could check would tell the
    user the opposite of what happened.  ``opening_balance is None`` is exactly
    that signal, because it is derived from the chain.

    Args:
        outcome: The :class:`~app.services.statement_import.ImportOutcome`.

    Returns:
        ``(message, category)``.
    """
    if not outcome.recorded_count:
        return (
            f"Nothing new: all {outcome.line_count} line(s) in this file were "
            f"already recorded.",
            "info",
        )
    unchecked = (
        "  This file carried no running balance, so it could not be checked "
        "against itself -- a missing line would not have been detected."
        if outcome.opening_balance is None else ""
    )
    known = (
        f", and {outcome.already_known} were already known."
        if outcome.already_known else "."
    )
    return (
        f"Recorded {outcome.recorded_count} new line(s) from "
        f"{outcome.period_start} to {outcome.period_end}{known}{unchecked}",
        "warning" if unchecked else "success",
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements", methods=["POST"],
)
@login_required
@require_owner
def import_statement(account_id):
    """Record an uploaded statement against this account.

    Every refusal the service can raise is a :class:`StatementImportError`
    subclass carrying a sentence written for the person who uploaded the file,
    so this handler has ONE except arm for the domain and renders the message
    rather than inventing one per failure.  The unit of work is the request:
    the service stages and flushes, this commits, and any refusal rolls the
    whole thing back -- which is what makes "nothing was imported", the phrase
    every one of those messages ends with, true rather than reassuring.

    **The success event is emitted AFTER the commit**, not by the service: a
    business event asserting "a bank statement was recorded" must not sit in
    the log when the transaction that would have recorded it failed.

    Args:
        account_id: The account to import into.

    Returns:
        A redirect back to the statements page, with a flash saying what
        happened.
    """
    account = load_cash_account_or_404(account_id)
    target = url_for("accounts.statements", account_id=account_id)

    errors = _upload_schema.validate(request.form)
    if errors:
        flash(refusal_sentence(errors), "warning")
        return redirect(target)
    source = StatementSourceEnum(_upload_schema.load(request.form)["source"])

    upload = request.files.get(_FILE_FIELD)
    if upload is None or not upload.filename:
        flash("Choose a file to import.", "warning")
        return redirect(target)

    # Read the whole upload into memory deliberately: the parser needs the
    # entire file to check it against its own summary and its running-balance
    # chain, so a streaming read would buy nothing.  The BYTE size is bounded
    # by ``MAX_CONTENT_LENGTH`` before this handler runs and the LINE count by
    # the adapter's own ceiling -- both, because a CSV parse expands its input
    # many times over in list overhead.
    payload = upload.read()

    def _report(outcome):
        """Log the business event and return the flash, AFTER the commit.

        The event asserting "a bank statement was recorded" must not sit in the
        log when the transaction that would have recorded it failed, which is
        why it is here rather than in the service.

        Args:
            outcome: The :class:`~app.services.statement_import.ImportOutcome`.

        Returns:
            ``(message, category)``.
        """
        log_event(
            _logger, logging.INFO, EVT_STATEMENT_IMPORTED, BUSINESS,
            "Recorded a bank statement.",
            account_id=account.id, source=source.value,
            import_id=outcome.import_id,
            line_count=outcome.line_count,
            recorded_count=outcome.recorded_count,
            period_start=outcome.period_start.isoformat(),
            period_end=outcome.period_end.isoformat(),
        )
        return _import_flash(outcome)

    return run_statement_door(
        StatementDoorContext(
            logger=_logger,
            refusal=StatementImportError,
            log_message=(
                "user_id=%d failed to import a statement for account %d"
            ),
            log_args=(current_user.id, account_id),
            flash_message=(
                "Something went wrong saving this statement.  Nothing was "
                "imported."
            ),
            target=target,
        ),
        lambda: record_statement(
            account_id=account.id,
            user_id=current_user.id,
            source=source,
            file_name=upload.filename,
            payload=payload,
        ),
        _report,
    )


def _removal_flash(account_id: int, removal) -> tuple:
    """Log the business event and return the flash, AFTER the commit.

    **It reports what was actually removed rather than "done".**  A destructive
    act whose report is a single word leaves the owner unable to tell a no-op
    from a much larger removal than they meant -- and this one can reach past
    the import itself, releasing matches and clearing the source-account
    pairing.  Every figure here was counted as the act ran, because afterwards
    the rows are gone.

    **The event is emitted HERE rather than in the service**, because a
    business event asserting that an import was destroyed must not sit in the
    log when the transaction that would have destroyed it failed.  This is the
    ``_report`` shape ``import_statement`` uses, on the door where a false
    entry costs most.

    Args:
        account_id: The account the import belonged to.
        removal: The :class:`~app.services.statement_import.ImportRemoval`.

    Returns:
        ``(message, category)``.
    """
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_IMPORT_DELETED, BUSINESS,
        "A recorded statement import was deleted.",
        user_id=current_user.id, account_id=account_id,
        import_id=removal.import_id, file_name=removal.file_name,
        period_start=removal.period_start.isoformat(),
        period_end=removal.period_end.isoformat(),
        lines_removed=removal.lines_removed,
        matches_released=removal.matches_released,
        identity_forgotten=removal.identity_forgotten,
    )
    released = (
        f"  {removal.matches_released} accepted match(es) were undone, so "
        f"their rows can be matched again -- the days those matches corrected "
        f"are unchanged."
        if removal.matches_released else ""
    )
    forgotten = (
        "  This was the last import for this account from that source, so the "
        "app no longer records which bank account it is; the next import will "
        "learn that again."
        if removal.identity_forgotten else ""
    )
    return (
        f"Deleted the import of '{removal.file_name}' covering "
        f"{removal.period_start} to {removal.period_end}, and the "
        f"{removal.lines_removed} bank line(s) it had recorded."
        f"{released}{forgotten}",
        "info",
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/delete", methods=["POST"],
)
@login_required
@require_owner
def delete_statement_import(account_id):
    """Undo one recorded import, so a refusal stops being terminal.

    **The repair door finding N-302 says a refusal owes** (plan step
    ``bank_import:X-f6a-4``).  A restated line, or a first import that named
    the wrong Shekel account, used to end this account's ability to import at
    all: nothing in ``app/`` deleted an import, a line or a recorded pairing,
    and the refusal's own message promised a human repair the app could not
    perform.

    **It moves NO money.**  What it destroys is what the BANK said; a settle
    day an accepted match wrote is the app's own record and stays, which is the
    rule ``release_match`` already states for the same reason.

    **It is a plain POST-redirect-GET**, like ``release_statement_match`` and
    unlike the review screen's batch: it names ONE act and either does it or
    refuses it, so a flash carries the whole answer.

    Args:
        account_id: The account whose import to delete.

    Returns:
        A redirect back to the statements page, with a flash saying exactly
        what was removed.
    """
    account = load_cash_account_or_404(account_id)
    target = url_for("accounts.statements", account_id=account_id)

    payload = form_payload(request.form, _delete_schema)
    errors = _delete_schema.validate(payload)
    if errors:
        flash(refusal_sentence(errors), "warning")
        return redirect(target)

    import_id = _delete_schema.load(payload)["import_id"]
    return run_statement_door(
        StatementDoorContext(
            logger=_logger,
            refusal=ValidationError,
            log_message="user_id=%d failed to delete an import on account %d",
            log_args=(current_user.id, account_id),
            flash_message=(
                "Something went wrong deleting that import.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda: delete_import(import_id, current_user.id, account.id),
        lambda removal: _removal_flash(account.id, removal),
    )
