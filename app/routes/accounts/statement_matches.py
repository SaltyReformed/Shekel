"""
Shekel Budget App -- The statement review QUEUE and its write doors

"Which of my rows is this bank line?" -- the page that proposes matches, the
POST that applies a whole reviewed pass, and the POST that answers for a
merchant nobody has answered for yet.  Plan steps **bank_import:X-f6a-2** and
**X-f6a-3c-2**, rulings **R-FS**, **R-FP** and **R-FV**.

**It is the EXCEPTION QUEUE and nothing else**, and TWO steps made that true
rather than one.

* Plan step ``bank_import:X-gf-2`` (ruling **bank_import:R-GX**) took away the
  decisions already MADE: the matches already accepted and the merchants
  already answered for are :mod:`.statement_register`'s, and the undo that acts
  on one went with them.  Measured on the developer's own data before that
  split: they were 442,109 bytes of a 578,523-byte page, and the work a routine
  import leaves was 136,414.
* Plan step ``bank_import:X-gf-3b`` (ruling **bank_import:R-HC**) took away the
  TOOL: the hand-build match form is what three separate exceptions send the
  owner to and is not itself an exception, so it is
  :mod:`.statement_workbench`'s -- with its own write door, its own live-totals
  endpoint and no ordering token to collide with this one's.  Measured through
  this route on a clone of his data 2026-08-28: the review body rendered
  150,853 bytes of which that form's two unbounded pick lists were **89,247**
  (finding **bank_import:N-374**).

**It MOVES MONEY, and it is one of the TWO screens where the BANK gets the
last word on a date** -- :mod:`.statement_workbench` is the other since plan
step ``bank_import:X-gf-3b``, and this sentence said *the only* until then.
Accepting a match writes the bank's posted day onto
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
from functools import partial

from flask import render_template, request
from flask_login import current_user, login_required
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._statement_doors import (
    fragment_door,
    outcome_counts,
    refusal_sentence,
    run_statement_fragment_door,
)
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._statement_rules import record_submitted_rules
from app.schemas.validation.statements import (
    NEW_ENVELOPE,
    StatementBatchSchema,
    batch_payload,
)
from app.services.category_service import list_active_categories
from app.services.statement_match import (
    Consent,
    IncomeCreation,
    MatchSubmission,
    NewEnvelope,
    PurchaseCreation,
    ReviewedBatch,
    ReviewScope,
    apply_reviewed,
    review_set,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_BATCH_APPLIED,
    log_event,
)

_logger = logging.getLogger(__name__)

#: One schema instance, constructed at import like every sibling's.  The
#: merchant-rule form has its own, beside the door that reads it
#: (:mod:`._statement_rules`).
_batch_schema = StatementBatchSchema()

#: The partial both the page and the batch POST render.  Extracted at plan step
#: X-f6a-3c-2 so the answer to "apply this pass" is the SCREEN carrying its own
#: receipt: ONE template, so what a batch swaps in cannot drift from what a
#: reload shows.
_BODY = "accounts/_statement_review_body.html"

#: What a database failure tells the owner.  It names no table -- the traceback
#: goes to the log -- and it ends the way every refusal in this package does,
#: which is true here because the route owns the unit of work: a pass that
#: could not commit has written nothing.
_DB_ERROR_MESSAGE = (
    "Something went wrong applying that, and nothing was changed.  Here is "
    "where you were."
)


def _review_context(
    account, scope, outcome=None, error=None, rules=None,
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
        rules: The :class:`~app.services.statement_match.StatedRules` a
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
        # **No hand-build panel and no totals URL** since plan step
        # ``bank_import:X-gf-3b`` (ruling **bank_import:R-HC**): the form those
        # served is a surface of its own, with its own context builder, its own
        # live-totals endpoint and its own write door
        # (:mod:`.statement_workbench`).  What this queue keeps is a LINK to it
        # on every exception, carrying the line that exception is about.
        #
        # The picker the NEW-ENVELOPE arm needs (plan step X-f6a-3b).  Loaded
        # here rather than inside the review set because it is not a fact about
        # the statement: it is what any create form on this account offers, and
        # ``list_active_categories`` is the ordering every category picker in
        # the app already shares.
        "categories": list_active_categories(current_user.id),
        "outcome": outcome,
        "error": error,
        "rules": rules,
    }


def _render(account, scope, *, outcome=None, error=None, rules=None):
    """Return this door's own surface, carrying whatever there is to say.

    **ONE answer for all three outcomes** (plan step ``bank_import:X-gf-3b``):
    a plain render, a receipt after a pass, and a refusal are the same page
    with a different thing to say, and this module used to state "answer with
    the review body" in three places -- ``_refused`` beside two renders --
    which is three places for the answer to drift.

    **A refusal is a designed 400** and carries the marker htmx needs, because
    htmx leaves a 4xx non-swapping: a refusal that renders NOTHING reads as a
    broken button, which is worse than the error it reports.  It is rendered
    from whichever scope the caller passes, and on the money door that choice
    belongs to :func:`~._statement_doors.run_statement_fragment_door` -- the
    request's own on every refusal arm, because a refused pass wrote nothing
    and re-deriving on the ``SQLAlchemyError`` path would run the very read
    whose failure is being handled.

    Args:
        account: The owned, attached account.
        scope: The pass to render from.
        outcome: The :class:`~app.services.statement_match.BatchOutcome` to
            report, or ``None``.
        error: A sentence explaining why nothing was applied at all, or
            ``None``.
        rules: The :class:`~app.services.statement_match.StatedRules` a pass
            over the merchant section produced, or ``None``.

    Returns:
        The rendered body at 200, or the designed-fragment
        ``(body, 400, headers)`` triple when *error* is set.
    """
    body = render_template(
        _BODY,
        **_review_context(
            account, scope, outcome=outcome, error=error, rules=rules,
        ),
    )
    return body if error is None else designed_error(body, 400)


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
        # **A person read this screen and pressed Apply** (ruling **R-GH**).
        # Stated as a literal because it is a fact about the DOOR rather than
        # about the payload: no wire value reaches it, and the only other
        # consent belongs to an import filing under a standing rule.
        consent=Consent.TICKED,
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
        # **The lines of money COMING IN the owner ticked** (ruling **bank_import:R-GW**).
        # One id each and nothing to unpack: an income row is filed against no
        # container, so there is no arm to read out of the submission.
        incomes=tuple(
            IncomeCreation(line_id=item["line_id"])
            for item in submitted["incomes"]
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
        return _render(account, scope, error=refusal_sentence(errors))
    submitted = _batch_schema.load(payload)

    def _record(outcome):
        """Log what this pass did, AFTER the commit that made it true.

        It is the pass-level event beside the per-act ones, and the only place
        a REFUSED item is counted at all.

        Args:
            outcome: The :class:`~app.services.statement_match.BatchOutcome`
                the door applied.
        """
        log_event(
            _logger, logging.INFO, EVT_STATEMENT_BATCH_APPLIED, BUSINESS,
            "A reviewed statement pass was applied.",
            user_id=current_user.id,
            account_id=account_id,
            item_count=(
                len(submitted["matches"])
                + len(submitted["creations"])
                # **Every kind of act this pass carried** (ruling
                # **bank_import:R-GW**).  A count that named two of three kinds
                # would make the audit trail disagree with ``applied_count``
                # for any pass holding a deposit.
                + len(submitted["incomes"])
            ),
            # **The eleven money effects, stated ONCE for both doors that
            # apply a BatchOutcome** (:func:`~._statement_doors
            # .outcome_counts`).  They were spelled out here until plan step
            # ``bank_import:X-gf-3b`` gave the hand-built match a door of its
            # own, at which point the list existed twice -- and it is a list
            # this event has already been caught missing two entries from,
            # both of them effects that move MONEY rather than dates.
            **outcome_counts(outcome),
        )

    # ONE failure story AND one answer for every fragment-shaped statement door
    # (:func:`~._statement_doors.run_statement_fragment_door`), which owns the
    # commit and chooses the scope each arm renders from: a failure outside a
    # designed refusal writes nothing at all, and a pass is never reported
    # against the state it replaced.
    #
    # **Nothing inside the act raises a ``ValidationError`` today, and the arm
    # in that helper stands anyway.**  Every per-item refusal is caught by
    # ``_batch._run`` and reported on the outcome, which is the whole point of
    # the savepoints; ``_submitted_batch`` builds frozen values and cannot
    # refuse; and the commit raises ``SQLAlchemyError``.  Two earlier versions
    # of this comment each named a path that does not exist, which is worse
    # than naming none.  What justifies keeping it is the SURFACE, and it has a
    # firing control --
    # ``test_a_refusal_raised_OUTSIDE_an_item_still_answers_with_the_screen``.
    return run_statement_fragment_door(
        fragment_door(
            _logger, render=partial(_render, account), scope=scope,
            account_id=account_id, act="apply a statement review",
            db_error_message=_DB_ERROR_MESSAGE,
        ),
        lambda: apply_reviewed(_submitted_batch(submitted), scope),
        _record,
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/review/merchants",
    methods=["POST"],
)
@login_required
@require_owner
def state_merchant_rules(account_id):
    """Record where this owner says each merchant's spending goes.

    **It MOVES NO MONEY and can move none.**  A rule is read to SUGGEST a
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
    # ``_render``'s own docstring records what re-deriving costs: on the
    # database arm, the connection that produced the first error very likely
    # produces a second, which escapes as an unhandled 500 that htmx will not
    # swap -- so the owner presses Save and sees nothing at all.  **Before**,
    # because ``ReviewScope.build`` raises ``PayCalendarError`` loud by design,
    # and deriving after the commit would report failure for a write that had
    # already landed.
    #
    # **It stays valid for the answer**, which a scope built before a MONEY
    # pass would not: this door writes exactly one table,
    # ``budget.merchant_rules``, through the ORM and calls no service --
    # so nothing it can do touches the calendar, the candidates or their
    # prices, and ``review_set`` re-reads the rules themselves.  That is a
    # closed argument over one table rather than an enumeration over an open
    # set of writers, which is the shape adversarial review measured false at
    # X-f6a-3c-2.
    scope = ReviewScope.build(current_user.id, account_id)

    outcome = record_submitted_rules(
        request.form, current_user.id, account_id, _logger,
    )
    if outcome.refusal is not None:
        return _render(account, scope, error=outcome.refusal)
    return _render(account, scope, rules=outcome.recorded)
