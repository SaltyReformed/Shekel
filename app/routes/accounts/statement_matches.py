"""
Shekel Budget App -- The statement review screen and its two write doors

"Which of my rows is this bank line?" -- the page that proposes matches, the
POST that applies a whole reviewed pass, and the POST that releases one match.
Plan steps **bank_import:X-f6a-2** and **X-f6a-3c-2**, rulings **R-FS**,
**R-FP** and **R-FV**.

**It MOVES MONEY, and it is the only screen in the app where the BANK gets the
last word on a date.**  Accepting a match writes the bank's posted day onto
every row the match names -- settling one still Projected and correcting one
whose recorded day was wrong.  Recording a line adds a movement the app did not
have at all.  Measured on the developer's own 2026-08-16 export against a
production clone: 124 proposals over 231 in-schedule lines, of which 46 correct
a day and 51 settle a row the app had never marked as having happened, plus 91
outflows no proposal can ever explain.

**ONE POST applies the whole reviewed pass, and that is plan step
X-f6a-3c-2.**  There were two write routes taking one act each, so working a
statement was 215 round trips at 3.67 s apiece -- **13.2 minutes**, which is
finding **N-306** and is why the corrections do not get made.  (The 12.88
minutes the service modules cite is the narrower figure: 215 derivations at
3.593 s, without the write each round trip also paid for.)  Measured on a
production clone through this route: the GET renders in **3.88 s** and the POST
applies the whole statement in **13.37 s**, 650 form fields, 11% of gunicorn's
120 s request timeout.  The account is derived TWICE in that request
(:class:`~app.services.statement_match.ReviewScope` -- once for the pass and
once for the answer, which must show the state the pass left) rather than
215 times.

**Nothing is applied that the owner did not accept** (ruling **R-FP**).  A
proposal is applied only if its checkbox was ticked; a bank line is recorded
only if its destination select names somewhere for it to go, and that select's
default is "leave this line alone".  The POST records exactly the ids the form
submitted, and the service re-derives every figure from them rather than
trusting the page.

**The POST answers with the SCREEN, through htmx, rather than a redirect.**
The ruled failure policy is that a refused item leaves nothing behind and the
rest still land, each refusal quoted with its own sentence -- and flash
messages ride in the signed session cookie, where the longest sentence a batch
item can carry measures **497 bytes** (``entry_service._doors``' settled-parent
refusal) against the 4 KB a browser will store.  **Nine of those overflow it**,
and an overflowed cookie is dropped, which logs the owner out.  The outcome is
therefore part of the re-rendered surface, which also keeps a refresh from
re-posting the pass.

**Why it is its own module beside ``statements``.**  That one owns what the
BANK SAID -- recording a file, idempotently, moving no figure.  This owns what
the app DOES about it, which is a write door onto ``settled_on`` on three row
kinds.  The boundary is the one ``reconcile`` and ``anchor`` cut along: a read
of an outside record against the door that acts on it.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, form
parsing, fragment rendering, flashes and redirects -- and delegates every read
and write to :mod:`app.services.statement_match`.
"""

import logging

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.exceptions import ValidationError
from app.extensions import db
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    refusal_sentence,
    run_statement_door,
)
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.schemas.validation import form_payload
from app.schemas.validation.statements import (
    NEVER,
    NEW_ENVELOPE,
    NOT_SAID,
    MerchantPolicyBatchSchema,
    StatementBatchSchema,
    StatementMatchReleaseSchema,
    batch_payload,
    policy_payload,
)
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    HandTotals,
    MatchSubmission,
    NewEnvelope,
    PolicyAnswer,
    PolicyStatement,
    PurchaseCreation,
    ReviewedBatch,
    ReviewScope,
    apply_reviewed,
    preview_hand_build,
    release_match,
    review_set,
    state_policies,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_BATCH_APPLIED,
    log_event,
)

_logger = logging.getLogger(__name__)

#: One schema instance each, constructed at import like every sibling's.
_batch_schema = StatementBatchSchema()
_release_schema = StatementMatchReleaseSchema()
_policy_schema = MerchantPolicyBatchSchema()

#: The partial both the page and the batch POST render.  Extracted at plan step
#: X-f6a-3c-2 so the answer to "apply this pass" is the SCREEN carrying its own
#: receipt: ONE template, so what a batch swaps in cannot drift from what a
#: reload shows.
_BODY = "accounts/_statement_review_body.html"
_HAND_TOTALS = "accounts/_statement_hand_totals.html"

#: What a database failure tells the owner.  It names no table -- the traceback
#: goes to the log -- and it ends the way every refusal in this package does,
#: which is true here because the route owns the unit of work: a pass that
#: could not commit has written nothing.
_DB_ERROR_MESSAGE = (
    "Something went wrong applying that, and nothing was changed.  Here is "
    "where you were."
)


def _review_context(
    account, scope, outcome=None, error=None, policies=None,
) -> dict:
    """Assemble what the review body renders, for the page and for the POST.

    ONE builder, because the POST's answer IS the screen: a second assembly
    would let the surface a batch swaps in disagree with the surface a reload
    shows.

    Args:
        account: The owned, attached account.
        scope: The pass to render FROM
            (:class:`~app.services.statement_match.ReviewScope`).  **The caller
            supplies it, and a caller that has just WRITTEN must supply a fresh
            one**: a scope holds the rows and prices as they stood before the
            pass, which is exactly what must not be shown after it.
        outcome: The :class:`~app.services.statement_match.BatchOutcome` to
            report, or ``None`` on a plain render.
        error: A sentence explaining why nothing was applied at all, or
            ``None``.  Distinct from a refused ITEM: this one means the
            submission never reached the door.
        policies: The :class:`~app.services.statement_match.StatedPolicies` a
            pass over the merchant section produced, or ``None``.  A SEPARATE
            receipt from *outcome* because it reports a separate act: stating
            where a merchant goes moves no money, and folding the two would put
            "3 recorded" beside "2 merchants answered for" under one heading
            that could only be true of one of them.

    Returns:
        The template context.
    """
    return {
        "account": account,
        "review": review_set(scope),
        # The hand-build panel, drawn EMPTY on every full render and re-drawn
        # by its own endpoint as the owner ticks (plan step
        # ``bank_import:X-f6d-4``).  Empty rather than derived, because a full
        # render answers a request that ticked nothing -- and after a pass the
        # form's checkboxes come back unticked, so a panel showing a total
        # would be describing a selection that no longer exists.
        "totals": HandTotals.untouched(),
        "total_url": url_for(
            "accounts.statement_review_totals", account_id=account.id,
        ),
        # The picker the NEW-ENVELOPE arm needs (plan step X-f6a-3b).  Loaded
        # here rather than inside the review set because it is not a fact about
        # the statement: it is what any create form on this account offers, and
        # ``list_active_categories`` is the ordering every category picker in
        # the app already shares.
        "categories": list_active_categories(current_user.id),
        "outcome": outcome,
        "error": error,
        "policies": policies,
    }


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
        **_review_context(
            account, ReviewScope.build(current_user.id, account_id),
        ),
    )


def _submitted_batch(submitted) -> ReviewedBatch:
    """Return the loaded payload as the batch the service applies.

    **The DESTINATION is one field naming one of two arms**, so nothing here
    infers an arm from an absence -- which is the defect that made the
    existing-envelope arm unreachable from a browser at plan step X-f6a-3b: the
    name box is always rendered and always prefilled, so keying on "no
    ``transaction_id``" named BOTH destinations on every submission.

    **Nothing here names an owner or an account.**  Whose pass this is, is the
    scope's -- one statement, made where the route proved it -- so no item can
    be priced against one account and written against another.

    Args:
        submitted: What :class:`~app.schemas.validation.statements
            .StatementBatchSchema` loaded.

    Returns:
        The :class:`~app.services.statement_match.ReviewedBatch`.
    """
    return ReviewedBatch(
        matches=tuple(
            MatchSubmission(
                line_ids=frozenset(item["line_ids"]),
                rows=frozenset(item["rows"]),
                accepted_difference=item["residual"],
            )
            for item in submitted["matches"]
        ),
        creations=tuple(
            PurchaseCreation(
                line_id=item["line_id"],
                transaction_id=(
                    None if item["destination"] == NEW_ENVELOPE
                    else item["destination"]
                ),
                new_envelope=(
                    NewEnvelope(
                        name=item["envelope_name"],
                        category_id=item["category_id"],
                    )
                    if item["destination"] == NEW_ENVELOPE else None
                ),
            )
            for item in submitted["creations"]
        ),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review", methods=["POST"],
)
@login_required
@require_owner
def apply_statement_review(account_id):
    """Apply everything the owner ticked, and answer with the screen.

    The unit of work is the REQUEST: each item runs inside its own SAVEPOINT so
    a refused one leaves nothing behind while the rest still land, and this
    route commits once -- so a failure outside a designed refusal writes
    nothing at all, which is what makes "nothing was changed" true rather than
    reassuring.

    Args:
        account_id: The account being reviewed.

    Returns:
        The re-rendered review body carrying the outcome, at 200; or the same
        body carrying one refusal sentence at 400, marked as a designed
        fragment so htmx swaps it (:mod:`app.utils.error_fragments`).
    """
    account = load_cash_account_or_404(account_id)

    # THROUGH ``batch_payload``, not the raw ``MultiDict``: one submission
    # carries many acts, each keyed by its rendered position or by its own bank
    # line, and ``request.form["line_ids"]`` would collapse every one of them
    # into the first value of one key.  The regrouping is the schema module's
    # business rather than this route's, for the reason ``form_payload``'s own
    # docstring gives about a route that lists field names itself.
    # ONE derivation, built HERE.  Only a route builds a read pass -- the same
    # rule ``BalanceContext`` is held to -- and this one serves three purposes:
    # the door applies against it, and either failure arm renders from it.
    scope = ReviewScope.build(current_user.id, account_id)

    payload = batch_payload(request.form)
    errors = _batch_schema.validate(payload)
    if errors:
        return _refused(account, scope, refusal_sentence(errors))
    submitted = _batch_schema.load(payload)

    try:
        outcome = apply_reviewed(_submitted_batch(submitted), scope)
        db.session.commit()
    except ValidationError as exc:
        # **Nothing inside this ``try`` raises one today, and the arm stands
        # anyway.**  Every per-item refusal is caught by ``_batch._run`` and
        # reported on the outcome, which is the whole point of the savepoints;
        # ``_submitted_batch`` builds frozen values and cannot refuse; and the
        # commit raises ``SQLAlchemyError``.  Two earlier versions of this
        # comment each named a path that does not exist, which is worse than
        # naming none.
        #
        # What justifies keeping it is the SURFACE rather than a known caller:
        # a designed refusal escaping an htmx POST is answered by the app-wide
        # handler with a page htmx will not swap (no marker header), so the
        # owner presses Apply and sees nothing at all.  This arm is the only
        # thing that can answer with the screen.  It has a firing control --
        # ``test_a_refusal_raised_OUTSIDE_an_item_still_answers_with_the_screen``
        # -- so it is a guard something can observe rather than one nothing can.
        db.session.rollback()
        return _refused(account, scope, str(exc))
    except SQLAlchemyError:
        db.session.rollback()
        _logger.exception(
            "user_id=%d failed to apply a statement review on account %d",
            current_user.id, account_id,
        )
        return _refused(account, scope, _DB_ERROR_MESSAGE)

    # AFTER the commit, so an event asserting a pass landed cannot sit in the
    # log for a transaction that failed -- the discipline ``statements``' own
    # import event states for itself.  It is the pass-level event beside the
    # per-act ones, and the only place a REFUSED item is counted at all.
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_BATCH_APPLIED, BUSINESS,
        "A reviewed statement pass was applied.",
        user_id=current_user.id,
        account_id=account_id,
        item_count=len(submitted["matches"]) + len(submitted["creations"]),
        applied_count=outcome.applied_count,
        refused_count=outcome.refused_count,
        settled_count=outcome.settled_count,
        corrected_count=outcome.corrected_count,
        redated_count=outcome.redated_count,
        # **The two effects this event was silent about**, and they are the
        # ones that move money rather than dates: a repricing changes what a
        # payment cost, and a residual records a row the app did not hold at
        # all.  Named by adversarial financial review 2026-08-23; the first
        # had been missing since the pass event was written.
        repriced_count=outcome.repriced_count,
        residual_count=outcome.residual_count,
        residual_total=str(outcome.residual_total),
        recorded_count=outcome.recorded_count,
        envelopes_created=outcome.envelopes_created,
    )

    # A FRESH scope for the ANSWER, and only on the path that WROTE.  The pass
    # has just settled rows, moved days and created purchases, so the one it
    # was applied against describes a state that no longer exists -- and this
    # screen is where the owner checks what happened.  Two derivations for a
    # whole pass, against the 215 the single-act doors took.
    return render_template(
        _BODY,
        **_review_context(
            account, ReviewScope.build(current_user.id, account_id),
            outcome=outcome,
        ),
    )


def _submitted_policies(submitted) -> "tuple[PolicyStatement, ...]":
    """Return the loaded payload as the statements the service records.

    **The wire's four values become the service's three answers plus a
    withdrawal**, and the mapping happens HERE because it is a fact about the
    FORM rather than about the domain: the service's
    :class:`~app.services.statement_match.PolicyAnswer` has no member for "not
    said", since not having said something is the absence of a row.

    Args:
        submitted: What :class:`~app.schemas.validation.statements
            .MerchantPolicyBatchSchema` loaded.

    Returns:
        One :class:`~app.services.statement_match.PolicyStatement` per merchant
        the section rendered, in the order it rendered them.
    """
    statements = []
    for item in submitted["policies"]:
        answer = item["answer"]
        if answer == NOT_SAID:
            statements.append(PolicyStatement(
                merchant_id=item["merchant_id"], answer=None,
            ))
        elif answer == NEVER:
            statements.append(PolicyStatement(
                merchant_id=item["merchant_id"], answer=PolicyAnswer.NEVER,
            ))
        elif answer == NEW_ENVELOPE:
            statements.append(PolicyStatement(
                merchant_id=item["merchant_id"],
                answer=PolicyAnswer.NEW_ENVELOPE,
                envelope_name=item["envelope_name"],
                category_id=item["category_id"],
            ))
        else:
            statements.append(PolicyStatement(
                merchant_id=item["merchant_id"],
                answer=PolicyAnswer.TEMPLATE,
                template_id=answer,
            ))
    return tuple(statements)


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review/merchants",
    methods=["POST"],
)
@login_required
@require_owner
def state_merchant_destinations(account_id):
    """Record where this owner says each merchant's spending goes.

    **It MOVES NO MONEY and can move none.**  A policy is read to SUGGEST a
    destination on the review screen below; the only thing that records a
    purchase is an explicit destination submitted for one specific line, which
    is what keeps ruling **R-FZ**'s *the destination select IS the tick* whole.
    That is also why this is its own door rather than a third item kind inside
    the batch: two acts with two consequences, one of which is money and one of
    which is not.

    **It derives the scope ONCE, before the write**, and reuses it for the
    answer and for every refusal -- see the comment at the derivation for why
    both the count and the moment matter.

    Args:
        account_id: The account being reviewed.

    Returns:
        The re-rendered review body carrying what was recorded, at 200; or the
        same body carrying one refusal sentence at 400, marked as a designed
        fragment so htmx swaps it.
    """
    account = load_cash_account_or_404(account_id)

    # ONE derivation, built BEFORE the write, and both halves of that are the
    # point.  **Once**, because every arm below renders the screen and
    # ``_refused``'s own docstring records what re-deriving costs: on the
    # database arm, the connection that produced the first error very likely
    # produces a second, which escapes as an unhandled 500 that htmx will not
    # swap -- so the owner presses Save and sees nothing at all.  **Before**,
    # because ``ReviewScope.build`` raises ``PayCalendarError`` loud by design,
    # and deriving after the commit would report failure for a write that had
    # already landed.
    #
    # **It stays valid for the answer**, which a scope built before a MONEY
    # pass would not: this door writes exactly one table,
    # ``budget.merchant_destinations``, through the ORM and calls no service --
    # so nothing it can do touches the calendar, the candidates or their
    # prices, and ``review_set`` re-reads the policies themselves.  That is a
    # closed argument over one table rather than an enumeration over an open
    # set of writers, which is the shape adversarial review measured false at
    # X-f6a-3c-2.
    scope = ReviewScope.build(current_user.id, account_id)

    payload = policy_payload(request.form)
    errors = _policy_schema.validate(payload)
    if errors:
        return _refused(account, scope, refusal_sentence(errors))
    statements = _submitted_policies(_policy_schema.load(payload))

    try:
        recorded = state_policies(statements, current_user.id, account_id)
        db.session.commit()
    except ValidationError as exc:
        db.session.rollback()
        return _refused(account, scope, str(exc))
    except SQLAlchemyError:
        db.session.rollback()
        _logger.exception(
            "user_id=%d failed to record merchant destinations on account %d",
            current_user.id, account_id,
        )
        return _refused(account, scope, _DB_ERROR_MESSAGE)

    return render_template(
        _BODY, **_review_context(account, scope, policies=recorded),
    )


def _refused(account, scope, message: str):
    """Re-render the review body carrying *message*, as a designed 400.

    **The re-render happens AFTER the rollback**, so the surface describes the
    state that survives rather than one about to be discarded -- the discipline
    ``reconcile._refusal`` states for the same shape.  It carries the
    designed-fragment marker because htmx leaves a 4xx non-swapping, and a
    refusal that renders NOTHING reads as a broken button, which is worse than
    the error it reports.

    **It reuses the request's OWN scope rather than deriving a second one**,
    and that is a correctness fix rather than a saving.  A refused pass wrote
    nothing, so the scope it was applied against still describes the state that
    survives -- and re-deriving would run the very read whose failure this arm
    is handling: on the ``SQLAlchemyError`` path, the connection or timeout
    that produced the first error very likely produces a second, which escapes
    as an unhandled 500.  htmx does not swap a 500 (it carries no marker
    header), so the owner would see nothing at all.  Found by adversarial
    security review 2026-08-19.

    Args:
        account: The owned, attached account.
        scope: The request's derived offer set, still valid because nothing
            was written.
        message: The user-facing reason, one sentence.

    Returns:
        The designed-fragment ``(body, 400, headers)`` triple.
    """
    return designed_error(
        render_template(
            _BODY,
            **_review_context(account, scope, error=message),
        ),
        400,
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review/totals", methods=["POST"],
)
@login_required
@require_owner
def statement_review_totals(account_id):
    """Answer what the hand-build form's ticked lines and rows come to.

    Plan step ``bank_import:X-f6d-4``, ruling **R-FN**.  A difference is a
    transaction the owner ACCEPTS, and the group they are assembling is one
    nothing has computed -- so this computes it, and the consent box the
    fragment renders carries the SERVER's own figure.

    **It MOVES NO MONEY and writes nothing**, which is why it may fire on
    every tick.  It runs the accept door's reads and refusals through
    :func:`~app.services.statement_match.preview_hand_build` and renders the
    answer; the only door that writes is still
    ``apply_statement_review``.  It is a POST rather than a GET because it
    carries a list of ids and a CSRF token, not because it changes anything --
    the same reason the policy section posts.

    **It takes the body Apply would send**, read through the same
    ``batch_payload`` regrouping, so the panel is that act asked what it would
    do rather than a second opinion about it.  A submission naming no hand
    item -- which is what an untouched form sends -- renders the empty panel.

    Args:
        account_id: The account being reviewed.

    Returns:
        The re-rendered panel, at 200; or the same panel carrying one refusal
        sentence at 400, marked as a designed fragment so htmx swaps it.
    """
    # The ownership proof, for its refusal rather than for its value: this
    # answer names no account, and the project's rule is 404 for both "not
    # found" and "not yours".
    load_cash_account_or_404(account_id)
    scope = ReviewScope.build(current_user.id, account_id)
    context = {
        "total_url": url_for(
            "accounts.statement_review_totals", account_id=account_id,
        ),
    }

    payload = batch_payload(request.form)
    errors = _batch_schema.validate(payload)
    if errors:
        return designed_error(
            render_template(
                _HAND_TOTALS,
                totals=HandTotals.refused(refusal_sentence(errors)),
                **context,
            ),
            400,
        )
    submitted = _batch_schema.load(payload)
    matches = _submitted_batch(submitted).matches
    totals = (
        preview_hand_build(matches[0], scope) if matches
        else HandTotals.untouched()
    )
    # NO event and NO commit.  Nothing was written, and a read pass that logged
    # would put a line in the audit trail for every checkbox on the page.
    return render_template(_HAND_TOTALS, totals=totals)


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


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review/release", methods=["POST"],
)
@login_required
@require_owner
def release_statement_match(account_id):
    """Undo one match: put its bank lines back, and take back what it created.

    **It does NOT put the settle days back**, and the page says so: the bank is
    still the best evidence the app has about when that money moved, so
    reverting a correction in order to tidy a relation would throw away the
    fact and keep the bookkeeping.  What comes back is the QUESTION.

    **It DOES remove the rows the act created** (plan step
    ``bank_import:X-f6f``, ruling **R-GG**), which is why the button carries a
    ``data-confirm`` naming them and their figure: a purchase a bank line
    became is money the app records only because this act recorded it, and the
    control that withdraws it is the one place the owner can see how much.

    **It stays a plain POST-redirect-GET** where its sibling became an htmx
    swap, and the difference is the subject rather than an inconsistency: this
    names ONE act and either does it or refuses it, so a flash carries the
    whole answer.  Its sibling reports per-item outcomes no flash can hold.

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
        flash(refusal_sentence(errors), "warning")
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
        _release_report,
    )
