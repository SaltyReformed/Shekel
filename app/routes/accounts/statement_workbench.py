"""
Shekel Budget App -- The hand-build match WORKBENCH and its two doors

"Some of my rows are what this bank line paid for" -- the surface where the
owner asserts a correspondence the matcher would not guess, the POST that
records it, and the POST that prices it live as they tick.

**THE WORKBENCH IS NOT THE QUEUE** (ruling **bank_import:R-HC**, plan step
``bank_import:X-gf-3b``).  It is the same argument ruling **bank_import:R-GX**
made about the register, applied to the other thing on the review screen that
is not a decision: this form is the TOOL that three separate exceptions send
the owner to -- a parked card payment meeting its payback rows, a line a tier
declined, a deposit the books may already hold -- and it is not itself an
exception.  Measured through the real route on the developer's own data
2026-08-27, and reproduced 2026-08-28 on a clone of it: the review body
rendered 150,853 bytes of which this form's two pick lists were **89,247** --
22,830 for 27 bank lines and 66,417 for 67 rows -- against 1 unanswered
merchant, 2 creatable lines, 16 deposits and 9 parked payments of actual work
(finding **bank_import:N-374**).

**A CAP IS REFUSED AS THE REMEDY, and this surface is deliberately unbounded.**
The row that explains a line may be number 51, and a scored shortlist is what
ruling **R-GD**'s third amendment already withdrew on this screen after
measuring 0 of 18 inspected correct.  What N-374 closes on is the QUEUE being
bounded by the tool leaving it, not on this list being cut.

**IT MOVES MONEY.**  Recording a group settles every row it names onto the
bank's posted day, corrects one whose recorded day was wrong, re-prices one
whose figure the bank contradicts, and -- where the owner consents to a
difference -- records a row the app did not hold at all (ruling **R-FN**).

**Why it is its own door rather than a third arm of the reviewed pass.**  The
two are different acts with different shapes: a reviewed pass carries N
proposals, N creations and N incomes and is batched because N was 215 round
trips at 3.67 s apiece (finding **N-306**); a hand-built group is always
exactly one match with one consent figure.  While they shared a page the group
rode the batch door under the reserved ordering index ``"hand"``, and that
index was the only thing keeping its ticks out of proposal ``0``'s submission
-- the two forms being separate ``<form>`` elements, **a property of the
document**.  An ``hx-include``, or one merge of the two controls, would have
unioned proposal 0's hidden row ids with this group's ticks into ONE act naming
rows the owner never grouped.  Two surfaces posting to two doors share no
namespace, so that hazard is not relocated here, it is unrepresentable.

**What is NOT duplicated is the money.**  Both doors call
:func:`~app.services.statement_match.apply_reviewed`, which runs each item in
its own SAVEPOINT and reports per-item outcomes; the receipt both surfaces
render is one partial (``accounts/_statement_pass_receipt.html``).  A second
applier, or a second receipt, would be two statements of one act.

**The page is the header and the body is the answer**, which is the split
:mod:`.statement_matches` and :mod:`.statement_register` both keep and for the
same reason: a per-item receipt overflows the 4 KB a browser will store for the
signed session cookie a flash rides in.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, form
parsing, fragment rendering, URLs -- and delegates every read and write to
:mod:`app.services.statement_match`.
"""

import logging
from functools import partial

from flask import render_template, request, url_for
from flask_login import current_user, login_required
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._statement_doors import (
    fragment_door,
    log_pass_applied,
    refusal_sentence,
    run_statement_fragment_door,
    submitted_match,
)
from app.schemas.validation.statements import (
    StatementMatchSchema,
    hand_match_payload,
)
from app.services.statement_match import (
    Consent,
    HandTotals,
    MatchSubmission,
    ReviewedBatch,
    ReviewScope,
    apply_reviewed,
    preview_hand_build,
    review_set,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error

_logger = logging.getLogger(__name__)

#: One schema instance, constructed at import like every sibling's.  It is
#: :class:`~app.schemas.validation.statements.StatementMatchSchema` itself and
#: not a workbench-specific subclass: a hand-built group and a ticked proposal
#: are the SAME act reaching the same door, and a second schema would be free
#: to grade ``residual`` less strictly than the one beside it -- which is what
#: that field's own docstring records having cost once.
_match_schema = StatementMatchSchema()

#: The partial the page, the apply door and the refusal arm all render.  ONE
#: template, so what htmx swaps in after a POST cannot drift from what a reload
#: shows -- the discipline both sibling statement surfaces keep.
_BODY = "accounts/_statement_workbench_body.html"
_HAND_TOTALS = "accounts/_statement_hand_totals.html"

#: The query argument an exception on the review queue links with, so the line
#: it is about arrives already ticked (ruling **bank_import:R-HC**).
_PRESELECT_ARG = "line"

#: What a database failure tells the owner.  It names no table -- the traceback
#: goes to the log -- and it ends the way every refusal in this package does,
#: which is true here because the route owns the unit of work: a group that
#: could not commit has written nothing.
_DB_ERROR_MESSAGE = (
    "Something went wrong recording that match, and nothing was changed.  "
    "Here is where you were."
)


def _preselected(review) -> "frozenset[int]":
    """Return the bank lines that arrive already ticked, from ``?line=``.

    Ruling **bank_import:R-HC**: each exception on the queue links here "with
    its own line already ticked", so the owner lands on the tool with the thing
    they were looking at selected rather than having to find it in a list of 27.

    **The membership test IS the validation, and there is no branch for a bad
    id.**  A value is honoured only if it names a line THIS pass left
    unexplained on THIS account -- so a line belonging to someone else, one
    another match already claims, one before the pay calendar opens, and one
    that is not a number at all are all rejected by the same intersection.  It
    is a set intersection rather than a parse-then-authorise because an
    ownership check written here would be a second statement of the one
    ``review_set`` already applies, free to drift from it.

    **What the intersection actually protects is the TOTALS PANEL, not the
    checkbox**, and a first version of this docstring said the opposite.  The
    checkbox is safe either way: the form loops over ``review.unmatched``, so
    an id absent from that list has no control to tick and never had one --
    which means a control asserting *a foreign line does not arrive ticked*
    passes with this whole intersection DELETED.  It was written that way and
    the mutation survived.  What is not safe either way is
    :func:`_workbench_context` handing this set to ``preview_hand_build``:
    ``_resolve.load_lines`` REFUSES an id that names no line on the account or
    one already claimed, so without the narrowing an ordinary stale link --
    tick, match, press Back -- renders the workbench under *"A statement line
    you picked is already matched to something else.  Nothing was changed."*
    for an act nobody attempted.  Measured both ways on a clone of the
    developer's data 2026-08-28.  On a FOREIGN line the same path would answer
    with ``load_lines``' other refusal, which distinguishes a line that exists
    from one that does not.

    **It never refuses the request**, which is the other half: a stale link is
    an ordinary browser gesture, and answering one with a 400 would be worse
    than answering it with a correct page that ticks nothing.

    Args:
        review: This pass's :class:`~app.services.statement_match.ReviewSet`.

    Returns:
        The line ids to render ticked, empty when the request named none this
        pass can offer.
    """
    offerable = {line.line_id for line in review.unmatched}
    asked = set()
    for raw in request.args.getlist(_PRESELECT_ARG):
        # ``str.isdigit`` is the wrong predicate here and this project owns
        # that fact (:mod:`app.utils.digit_strings`, finding **N-136**): it is
        # true for 888 characters, 128 of which make ``int()`` raise.  The
        # intersection below is what authorises; this only has to not raise.
        try:
            asked.add(int(raw))
        except ValueError:
            continue
    return frozenset(asked & offerable)


def _workbench_context(
    account, scope, outcome=None, error=None,
) -> dict:
    """Assemble what the workbench body renders, for the page and the POST.

    ONE builder, because the POST's answer IS the screen: a second assembly
    would let the surface a press swaps in disagree with the surface a reload
    shows.

    Args:
        account: The owned, attached account.
        scope: The pass to render FROM
            (:class:`~app.services.statement_match.ReviewScope`).  **The caller
            supplies it, and a caller that has just WRITTEN must supply a fresh
            one**: a scope holds the rows and prices as they stood before the
            act, which is exactly what must not be shown after it.
        outcome: The :class:`~app.services.statement_match.BatchOutcome` to
            report, or ``None`` on a plain render.
        error: A sentence explaining why nothing was recorded at all, or
            ``None``.  Distinct from a refused ITEM: this one means the
            submission never reached the door.

    Returns:
        The template context.
    """
    review = review_set(scope)
    preselected = _preselected(review)
    return {
        "account": account,
        "review": review,
        "preselected": preselected,
        # **The panel describes the selection that is actually ticked**, which
        # on arrival from an exception's link is one line rather than nothing.
        # Rendering the empty panel beside a ticked box would be a screen
        # contradicting its own controls -- the shape plan step
        # ``bank_import:X-gc`` corrected on three surfaces, and the shape this
        # very panel had once already: a first version of ``X-f6d-4`` reported
        # `$0.00` for a `$2,573.43` line the owner had just ticked.
        #
        # **Through the door's own reader and NOT through a branch on
        # emptiness.**  ``preview_hand_build`` answers an empty submission with
        # the untouched panel as its own first statement, so the render after a
        # write -- where no ``?line=`` is in play and the form comes back
        # unticked -- reaches that answer by the same call rather than by a
        # condition here that could disagree with it.
        "totals": preview_hand_build(
            MatchSubmission(line_ids=preselected, rows=frozenset()), scope,
        ),
        "total_url": url_for(
            "accounts.statement_match_totals", account_id=account.id,
        ),
        "outcome": outcome,
        "error": error,
    }


def _render(account, scope, *, outcome=None, error=None):
    """Return this door's own surface, carrying whatever there is to say.

    **ONE answer for all three outcomes** (plan step ``bank_import:X-gf-3b``):
    a plain render, a receipt after a write, and a refusal are the same page
    with a different thing to say, and this door used to state "answer with the
    workbench body" in two places -- a ``_refused`` helper beside a success
    render -- which is two places for the answer to drift.

    **A refusal is a designed 400** and carries the marker htmx needs, because
    htmx leaves a 4xx non-swapping: a refusal that renders NOTHING reads as a
    broken button, which is worse than the error it reports.  Whether the scope
    is the request's own or a fresh one is decided by
    :func:`~._statement_doors.run_statement_fragment_door`, which is what makes
    "a pass is never reported against the state it replaced" structural rather
    than remembered.

    Args:
        account: The owned, attached account.
        scope: The pass to render from.
        outcome: The :class:`~app.services.statement_match.BatchOutcome` to
            report, or ``None``.
        error: A sentence explaining why nothing was recorded at all, or
            ``None``.

    Returns:
        The rendered body at 200, or the designed-fragment
        ``(body, 400, headers)`` triple when *error* is set.
    """
    body = render_template(
        _BODY,
        **_workbench_context(account, scope, outcome=outcome, error=error),
    )
    return body if error is None else designed_error(body, 400)


@accounts_bp.route("/accounts/<int:account_id>/statements/match")
@login_required
@require_owner
def statement_workbench(account_id):
    """Render the two pick lists a hand-built match is assembled from.

    Args:
        account_id: The account whose unexplained lines and rows to show.

    Returns:
        The rendered page, or a 404 when the account is not the caller's or is
        a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/statement_workbench.html",
        **_workbench_context(
            account, ReviewScope.build(current_user.id, account_id),
        ),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/match", methods=["POST"],
)
@login_required
@require_owner
def apply_hand_match(account_id):
    """Record the group the owner assembled, and answer with the screen.

    The unit of work is the REQUEST: the item runs inside its own SAVEPOINT so
    a refusal leaves nothing behind, and this route commits once -- so a
    failure outside a designed refusal writes nothing at all, which is what
    makes "nothing was changed" true rather than reassuring.

    **It applies through the same door a reviewed pass does**
    (:func:`~app.services.statement_match.apply_reviewed`), carrying one match
    and no creations or incomes.  A second applier for a one-item batch would
    be a second place for the savepoint discipline, the per-item refusal
    wording and the outcome counts to be got wrong.

    Args:
        account_id: The account being matched against.

    Returns:
        The re-rendered workbench body carrying the outcome, at 200; or the
        same body carrying one refusal sentence at 400, marked as a designed
        fragment so htmx swaps it (:mod:`app.utils.error_fragments`).
    """
    account = load_cash_account_or_404(account_id)

    # ONE derivation, built HERE.  Only a route builds a read pass -- the same
    # rule ``BalanceContext`` is held to -- and this one serves two purposes:
    # the door applies against it, and either failure arm renders from it.
    scope = ReviewScope.build(current_user.id, account_id)

    payload = hand_match_payload(request.form)
    errors = _match_schema.validate(payload)
    if errors:
        return _render(account, scope, error=refusal_sentence(errors))
    submitted = _match_schema.load(payload)

    def _record(outcome):
        """Log what this act did, AFTER the commit that made it true.

        **The SAME event as the reviewed pass**, and deliberately: this is one
        act of the kind that door applies many of, so a second event name would
        split one audit trail in two and make "how many matches were recorded
        on this account" a question needing a union.  The counts come from the
        one statement of them (:func:`~._statement_doors.outcome_counts`),
        including the three this door cannot produce -- an audit trail whose
        FIELDS depend on which door wrote the row cannot be queried across the
        two.

        Args:
            outcome: The :class:`~app.services.statement_match.BatchOutcome`
                the door applied.
        """
        log_pass_applied(
            _logger, "A hand-built statement match was recorded.",
            account_id=account_id, item_count=1, outcome=outcome,
        )

    return run_statement_fragment_door(
        fragment_door(
            _logger, render=partial(_render, account), scope=scope,
            account_id=account_id, act="record a hand-built match",
            db_error_message=_DB_ERROR_MESSAGE,
        ),
        lambda: apply_reviewed(
            ReviewedBatch(
                # **A person read this screen and pressed the button** (ruling
                # **R-GH**).  Stated as a literal because it is a fact about
                # the DOOR rather than about the payload: no wire value reaches
                # it, and the only other consent belongs to an import filing
                # under a standing rule.
                consent=Consent.TICKED,
                matches=(submitted_match(submitted),),
                creations=(),
                incomes=(),
            ),
            scope,
        ),
        _record,
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/match/totals", methods=["POST"],
)
@login_required
@require_owner
def statement_match_totals(account_id):
    """Answer what the workbench's ticked lines and rows come to.

    Plan step ``bank_import:X-f6d-4``, ruling **R-FN**; moved here from
    ``statement_matches.statement_review_totals`` at ``bank_import:X-gf-3b``
    with the form it prices.  A difference is a transaction the owner ACCEPTS,
    and the group they are assembling is one nothing has computed -- so this
    computes it, and the consent box the fragment renders carries the SERVER's
    own figure.

    **It MOVES NO MONEY and writes nothing**, which is why it may fire on every
    tick.  It runs the accept door's own reads and refusals through
    :func:`~app.services.statement_match.preview_hand_build` and renders the
    answer; the only door that writes is :func:`apply_hand_match`.  It is a
    POST rather than a GET because it carries a list of ids and a CSRF token,
    not because it changes anything.

    **It takes the body Apply would send**, read through the same
    :func:`~app.schemas.validation.statements.hand_match_payload`, so the panel
    is that act asked what it would do rather than a second opinion about it.
    A submission naming nothing -- which is what an untouched form sends --
    renders the empty panel.

    Args:
        account_id: The account being matched against.

    Returns:
        The re-rendered panel, at 200; or the same panel carrying one refusal
        sentence at 400, marked as a designed fragment so htmx swaps it.
    """
    # The ownership proof, for its refusal rather than for its value: this
    # answer names no account, and the project's rule is 404 for both "not
    # found" and "not yours".
    load_cash_account_or_404(account_id)
    scope = ReviewScope.build(current_user.id, account_id)

    payload = hand_match_payload(request.form)
    errors = _match_schema.validate(payload)
    if errors:
        return designed_error(
            render_template(
                _HAND_TOTALS,
                totals=HandTotals.refused(refusal_sentence(errors)),
            ),
            400,
        )
    submitted = _match_schema.load(payload)
    # NO event and NO commit.  Nothing was written, and a read pass that logged
    # would put a line in the audit trail for every checkbox on the page.
    return render_template(
        _HAND_TOTALS,
        totals=preview_hand_build(submitted_match(submitted), scope),
    )
