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
from dataclasses import dataclass

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
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
        imports=import_history(current_user.id, account_id),
        lines=recent_lines(account_id),
        evidence_copy=EVIDENCE_COPY,
    )


@dataclass(frozen=True)
class _EvidenceCopy:
    """What a screen says about one way a stated balance was pinned.

    Attributes:
        label: The short form, for a table cell.
        sentence: The full form, for the import receipt and the cell's title.
        badge: The Bootstrap class the label wears.  **It lives here rather
            than in a template conditional** so no surface has to ask which
            basis it is holding: a template testing
            ``basis == ASSUMED_LAST_DAY`` would be one more place to update
            when a fourth way to pin a day exists, and the display decision is
            the display layer's to make once.
    """

    label: str
    sentence: str
    badge: str


#: What the screens say about each way a file's stated balance was pinned.
#: **Keyed by the enum MEMBER, not by its ``name`` string**, so the
#: project-wide IDs-for-logic rule holds here exactly as it does in a query --
#: the template subscripts this map with the member the service handed it and
#: compares no string at all.  ONE map rather than one per surface: the receipt
#: and the imports table say the same thing about the same fact, and two
#: spellings would be two places for a fourth basis to be forgotten.
#: **PUBLIC because it has readers in two route modules** -- this page's
#: receipt and imports table, and the books-vs-bank page, which names the
#: same fact about the same anchor.  A message with two readers is part of
#: the interface, which is finding **N-33**'s shape stated rather than left
#: to a convention (:mod:`app.routes.accounts.difference` imports
#: ``LOAN_ANCHOR_REFUSAL`` from ``anchor`` for the same reason).
EVIDENCE_COPY = {
    StatementBalanceEvidenceEnum.FILE_CHAIN: _EvidenceCopy(
        label="proved by the file",
        sentence=(
            "Its stated balance is proved by the file's own per-line running "
            "balance, so nothing outside it was needed."
        ),
        badge="text-bg-secondary",
    ),
    StatementBalanceEvidenceEnum.CORROBORATED: _EvidenceCopy(
        label="corroborated",
        sentence=(
            "Its stated balance agrees with the statements already recorded "
            "for this account, so two of them say the same thing."
        ),
        badge="text-bg-secondary",
    ),
    StatementBalanceEvidenceEnum.UNCORROBORATED: _EvidenceCopy(
        label="uncorroborated",
        sentence=(
            "Nothing has confirmed its stated balance.  Export once with your "
            "bank's running-balance option ticked and every statement after "
            "it can be checked against that one."
        ),
        badge="text-bg-warning",
    ),
}


def _balance_sentence(outcome):
    """Return what the receipt says about the file's own stated balance.

    Args:
        outcome: The :class:`~app.services.statement_import.ImportOutcome`.

    Returns:
        One sentence, plus the PLACEMENT whenever the day the figure is placed
        at differs from the day the file names.

    **The gap between those two days is the number the owner has to judge**,
    and it is stated for every evidence level rather than only the weakest.  An
    ordinary export's header sits a day past its last line; the developer's
    2026-01-02..2026-03-31 export, pulled 2026-08-23, sits **145 days** past it
    and `$255.41` out.  A gap the owner can see is a gap the owner can judge.
    An earlier draft said *"that assumes nothing moved between"* those days,
    which stopped being true when the weakest-link rule made ``uncorroborated``
    cover solved days too -- a solved day assumes nothing about the span; it is
    the OPENING behind it that is unconfirmed.
    """
    if outcome.balance is None:
        return "It states no balance, so there was none to check."
    if not outcome.balance.is_anchored:
        return (
            f"It states {outcome.balance.stated} as of "
            f"{outcome.balance.stated_on}, which its own lines do not reach, "
            f"so nothing here could place that figure and no balance was "
            f"recorded from it."
        )
    copy = EVIDENCE_COPY[outcome.balance.evidence]
    if outcome.balance.effective_on == outcome.balance.stated_on:
        return copy.sentence
    return (
        f"{copy.sentence}  The figure is placed at "
        f"{outcome.balance.effective_on}, where the file states it as of "
        f"{outcome.balance.stated_on}."
    )


def _import_flash(outcome):
    """Return the flash text and category for a successful import.

    **A file whose stated balance nothing could CHECK gets a warning, not a
    green tick**, and that is an honest report rather than a nicety: an
    unchecked anchor propagates, because every later import reconciles against
    it and inherits its error.  Saying "Recorded 361 new lines" in green over
    one would tell the user the opposite of what happened.  All three unproven
    states share the warning -- no balance stated, a balance the file's own
    lines cannot reach, and one merely assumed -- because what they have in
    common is the thing the owner needs to know: nothing here confirmed it.

    **The signal used to be ``opening_balance is None`` and that was measured
    WRONG** (plan step ``bank_import:X-f6e-1``).  That column was derived from
    the per-line running-balance chain, which SECU stopped exporting between
    the developer's 2026-07-19 and 2026-08-16 pulls -- so the warning fired on
    every modern import, claiming "a missing line would not have been
    detected" while ``_secu_csv._verify_against_totals`` had already checked
    the line list against the file's own ``Totals:`` row and would have
    detected exactly that.  Both dev imports carry that NULL.  What is
    genuinely unchecked is the BALANCE, and the basis is what says so.

    Args:
        outcome: The :class:`~app.services.statement_import.ImportOutcome`.

    Returns:
        ``(message, category)``.
    """
    unproven = outcome.balance is None or (
        not outcome.balance.is_anchored
        or outcome.balance.evidence
        is StatementBalanceEvidenceEnum.UNCORROBORATED
    )
    balance = f"  {_balance_sentence(outcome)}"
    if not outcome.recorded_count:
        # **The balance sentence belongs on THIS branch too**, and leaving it
        # off was a defect only driving the real app exposed: re-importing the
        # developer's own 2026-08-16 export added 0 of its 361 lines and yet
        # RECORDED an anchor -- its stated `$4,747.63` placed at 2026-08-13 --
        # while the receipt said "Nothing new" and nothing else.  A line is not
        # the only thing an import can learn.
        return (
            f"Nothing new: all {outcome.line_count} line(s) in this file were "
            f"already recorded.{balance}",
            "warning" if unproven else "info",
        )
    known = (
        f", and {outcome.already_known} were already known."
        if outcome.already_known else "."
    )
    return (
        f"Recorded {outcome.recorded_count} new line(s) from "
        f"{outcome.period_start} to {outcome.period_end}{known}{balance}",
        "warning" if unproven else "success",
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
        rows_removed=removal.rows_removed,
        cash_removed=str(removal.cash_removed),
        identity_forgotten=removal.identity_forgotten,
    )
    released = (
        f"  {removal.matches_released} accepted match(es) were undone, so "
        f"their rows can be matched again -- the days those matches corrected "
        f"are unchanged."
        if removal.matches_released else ""
    )
    # **The one sentence on this receipt that reports MONEY** (plan step
    # ``bank_import:X-f6f``, ruling **R-GG**): a row the review created from
    # one of these lines goes with the line, so this act is no longer
    # balance-neutral and a report that said only "N matches undone" would
    # hide it.
    removed_rows = (
        f"  {removal.rows_removed} row(s) the review had created from those "
        f"lines were removed with them, worth {removal.cash_removed:+,.2f}."
        if removal.rows_removed else ""
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
        f"{released}{removed_rows}{forgotten}",
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
