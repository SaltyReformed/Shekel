"""
Shekel Budget App -- Anchor Balance Edit Routes

The grid and Net Worth Cockpit anchor-balance true-up endpoints,
split out of the historical monolithic ``app/routes/accounts.py`` in
Commit 21 of the financial-calculation audit follow-up (F-1).  The
retired ``/accounts`` table's inline balance editor also lived here
until the Net Worth Cockpit replaced that table; the cockpit reuses
the grid editor below, so only that family remains.

``true_up`` routes the actual mutation, history-row append,
conditional entries reconcile, and commit through
:func:`app.services.anchor_service.apply_anchor_true_up`, so the duplicate rule
(ruling **R-EQ**: an assertion is refused only when it changes nothing) and the
concurrency contract live in exactly one place.  This module is
therefore deliberately thin: it owns the HTTP-shaped concerns (form
validation, HTMX-fragment rendering, HX-Trigger header composition)
and delegates the database mutation to the shared service.

**The C-17 / F-009 optimistic lock is no longer part of that contract**
(ruling R-EN, plan step X-f1c3c): a true-up UPDATEs no column on
``accounts``, so ``version_id`` cannot fire, and this module no longer
carries a pre-flush version check or a 409.  What serialises two
concurrent true-ups is the per-owner write lock the reconcile itself
takes (:mod:`app.services.user_write_lock`) -- the reconcile is the
read-modify-write, so the lock belongs to it rather than to any one of
its callers.

The editor opens from five surfaces -- the grid cell, the dashboard
balance card, the cockpit per-card cell, the investment / retirement
detail page's balance hero, and the cash detail page's balance hero --
each threaded through as a normalized ``revert`` token so Cancel /
Escape re-render the correct opener (see
:func:`_normalize_revert_context`).
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from flask import render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts.reconcile import prompt_fragment
from app.services import (
    anchor_service,
    cash_ledger,
    pay_period_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.account_validation import _anchor_schema
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.utils.error_fragments import designed_error, flatten_schema_errors

logger = logging.getLogger(__name__)


# The kind-refusal body shared by the PATCH gate and the editor-form GET
# gate (ruling D4 / step A1): an amortizing loan's balance is
# ledger-derived and is asserted on the loan's own page
# (``apply_loan_anchor_true_up``), never as a cash anchor (B-15).
_LOAN_ANCHOR_REFUSAL = (
    "A loan's balance is not a cash anchor. Record a balance true-up "
    "on the loan's own page instead."
)

def _is_amortizing(account: Account) -> bool:
    """Return True when *account* is an amortizing loan.

    The route-layer twin of the loan package's ``_load_loan_account``
    kind test (``account_type.has_amortization``, a boolean column --
    never a type-name string).  Used to refuse the CASH anchor editor
    for a loan; the service-layer backstop is
    :class:`~app.services.anchor_service.AmortizingAccountAnchorError`.
    """
    acct_type = account.account_type
    return acct_type is not None and acct_type.has_amortization


# ── Anchor Balance True-up (Grid) ─────────────────────────────────


# The non-default surfaces the shared anchor editor can be opened from.
# The opener names its surface via the ``revert`` query token; only these
# canonical values are honored (see :func:`_normalize_revert_context`).
# Each maps to a revert endpoint in :func:`_anchor_revert_url`.  ``investment``
# is the investment / retirement detail page's balance hero (Loop B P1 C4);
# ``cash`` is the cash detail page's balance hero (the S8 / D14 port).
_REVERT_SURFACES = frozenset({"dashboard", "accounts", "investment", "cash"})


def _normalize_revert_context(raw_revert: str | None) -> str | None:
    """Allowlist-validate the raw ``revert`` token to a canonical value.

    The anchor editor is opened from more than one surface, and the
    opener names its surface via the ``revert`` query token.  Four
    non-default surfaces are recognized -- ``dashboard`` (the dashboard
    hero balance card), ``accounts`` (the Net Worth Cockpit's per-card
    balance cell), ``investment`` (the investment / retirement detail
    page's balance hero), and ``cash`` (the cash detail page's balance
    hero); every other value (unset, unknown, an attacker's probe)
    collapses to ``None`` so the grid's default revert target is used.
    Centralizing the allowlist here means the token is validated against
    :data:`_REVERT_SURFACES` in exactly one place -- :func:`_anchor_revert_url`
    (the Cancel / Escape target) and the edit form's ``hx-patch`` round-trip
    token both consume this normalized value rather than re-checking the raw
    string -- so the token is never interpolated unvalidated into a URL or
    template.  A third consumer, the 409 conflict cell's retry opener, left
    with ruling R-EN (plan step X-f1c3c).

    Args:
        raw_revert: The ``revert`` query token as received, or ``None``.

    Returns:
        The canonical surface name when the token names a recognized
        surface; otherwise ``None`` (the grid default).
    """
    return raw_revert if raw_revert in _REVERT_SURFACES else None


def _submission_is_the_coverage_boundary(
    boundary_day: date | None, submitted_day: date | None,
) -> bool:
    """Return True when this submission's assertion IS the account's boundary.

    **The reconcile prompt follows the COVERAGE BOUNDARY, not the click**
    (developer ruling 2026-08-04, plan step X-f1c4c).  Before this step every
    cash true-up stamped today, so "the day this submission asserts" and
    ``cash_ledger.reconciled_through`` -- ``MAX(observed_on)`` -- were the same
    value by construction and nothing had to say so.  A user-supplied day
    decouples them for the first time, and the prompt is keyed on the second.

    What that costs when nobody re-couples them, reproduced end to end: assert
    ``$2,500`` as of Jul 15 while the account's latest assertion is Aug 4, and
    the modal opens headed with the AUG 4 balance offering an Aug 2 purchase --
    which a Jul 15 statement cannot show.  Ticking it is a settlement the user
    has no evidence for, and it moves that debit out of its envelope's
    outstanding floor (``cash_ledger._amounts._entry_checking_impact``:
    ``max(500 - 0, 120) = 500`` becomes ``max(500 - 120, 0) = 380``), so the
    projected balance reads ``$120.00`` HIGH on money that never left the bank.

    **A ``None`` day is ALWAYS the boundary, and that is exact rather than a
    convenience.**  The service files ``display_today()`` for it, and every
    other assertion carries an ``observed_on`` at or before its own today
    (:func:`app.services.anchor_service.resolve_observation_day` refuses a
    future day), so today is ``>=`` every stored day and is therefore the new
    maximum.  For a supplied day *D* the new maximum is ``max(previous, D)``,
    so *D* is the boundary exactly when it equals it.  **The two branches are
    established differently and saying so matters**: the supplied-day branch
    COMPARES against the boundary, while the ``None`` branch is PROVED equal to
    it by the no-future-day rule and reads nothing.  An earlier version of this
    paragraph claimed both were read off one query, which is the over-stated
    shape this step corrected in three other docstrings.

    Asking here rather than sharpening the PANEL is the shape ruling R-EB and
    plan step X-f6 point at: the panel exists because nothing in the app records
    when money moved, and a bank import replaces the question rather than
    re-keying it.  Bounding the offer set by a historical statement day would
    manufacture hand-entered ``settled_on`` values for X-f6's matcher to
    arbitrate against the bank's own -- one question with two answers, which is
    what this arc exists to delete.

    Args:
        boundary_day: The account's coverage boundary AFTER the write --
            ``cash_ledger.reconciled_through(...).observed_day``.  Passed in
            rather than queried so this is a pure comparison the caller can
            resolve once, and so the rule can be graded without a database.
        submitted_day: The civil day the FORM submitted, or ``None`` when its
            date box was blank.  The submitted value, deliberately, not a
            re-resolved one: re-reading the clock here would be a second
            reading that a midnight tick could disagree with.

    Returns:
        ``True`` when the assertion this request filed is the account's
        coverage boundary, so the reconcile question is about IT.
    """
    if submitted_day is None:
        return True
    return submitted_day == boundary_day


@dataclass(frozen=True)
class _AnchorSubmission:
    """One validated balance assertion, as the true-up form submitted it.

    Two values that are ONE fact -- "this account held $B on day D" -- so the
    gate hands them back together rather than as a widening tuple, and so the
    success response can acknowledge BOTH halves of what was recorded rather
    than being handed the day alone.  Frozen: a submission is a record of what
    arrived, not a working value.

    Attributes:
        balance: The validated :class:`Decimal` balance being asserted.
        observed_on: The civil day the form submitted, or ``None`` when its date
            box was left blank -- which the write door reads as the user's today
            (:func:`app.services.anchor_service.resolve_observation_day`).  It is
            NOT defaulted here: a route that invented the day would be a second
            answer to "when is an assertion dated", and both anchor write doors
            already share one.
    """

    balance: Decimal
    observed_on: date | None


def _true_up_success_response(
    account: Account, revert_context: str | None,
    submission: _AnchorSubmission,
) -> tuple[str, int, dict[str, str]]:
    """Compose the anchor true-up success response.

    Shared by ``true_up``'s COMMITTED and UNCHANGED outcomes (both render the
    updated display cell and fire ``balanceChanged`` so other surfaces
    recompute).  Three fragments can ride along, and each is mounted where it
    can actually survive -- which is the whole of plan step X-f1e3:

    * **the updated display cell**, the response's primary target on all five
      surfaces;
    * **exactly ONE of the reconcile prompt or the back-dated
      acknowledgement**, both out-of-band into a ``base.html`` mount that no
      refresh region owns, so both reach all five surfaces by construction;
    * **the ``#anchor-as-of`` caption, for the GRID alone**, because it is the
      only surface whose caption nothing else redraws (see
      ``grid/_anchor_as_of_oob.html`` for the per-surface measurement).

    **The prompt and the acknowledgement are ONE if/else, deliberately.**  They
    are the opposite branches of :func:`_submission_is_the_coverage_boundary`:
    a submission that IS the account's coverage boundary can be reconciled
    against, and one that is not has nothing new to reconcile.  Writing them as
    two independent conditionals -- which is what stood here -- states one
    branch twice and lets a later edit produce both or neither.

    **A BACK-DATED submission is acknowledged rather than rendered**, and the
    reason is that without it this response is indistinguishable from doing
    nothing.  The cell re-renders from ``resolve_anchor`` -- the assertion that
    governs NOW -- which a back-dated correction by definition does not change,
    so a user who recorded an older statement saw their editor collapse back to
    the same figure with no sign the write landed.  That is the defect
    :func:`_anchor_editor_error` exists to prevent on the failure side, and it
    was still live on the success side.  It reached ONE of the five surfaces
    until plan step X-f1e3 gave it a mount of its own (finding **N-199**).

    Args:
        account: The post-commit account.  The "as of" snippet is dated from
            the ASSERTION this resolves for it (``observed_on``), never from
            the row's ``updated_at`` -- the two are different facts (ruling
            R-EP).
        revert_context: The normalized surface token, or ``None`` -- which is
            the grid, the one surface the "as of" snippet is emitted for.
        submission: What the form asserted.  Decides whether the reconcile
            prompt is asked (:func:`_submission_is_the_coverage_boundary`) and,
            on the other branch, supplies both figures the acknowledgement
            names.  **Required, with no default**: a defaulted submission here
            means "suppress the safety check", and with one caller a default
            that can only ever be wrong is a footgun rather than a convenience.

    Returns:
        The ``(body, status, headers)`` tuple Flask returns, carrying the
        ``HX-Trigger: balanceChanged`` header.
    """
    anchor = cash_ledger.resolve_anchor(account)
    html = render_template(
        "grid/_anchor_edit.html", account=account,
        anchor_balance=anchor.balance, editing=False,
    )
    is_boundary = _submission_is_the_coverage_boundary(
        cash_ledger.reconciled_through(account.id).observed_day,
        submission.observed_on,
    )
    if is_boundary:
        # The one question worth asking after a balance reading -- which of
        # these purchases has your bank taken?  Empty when nothing is
        # outstanding, so the one-click habit is not taxed by a prompt with
        # nothing in it.
        feedback = prompt_fragment(account)
    else:
        # ``submission.observed_on`` cannot be None on this branch: a blank
        # date box means "today", and today IS the coverage boundary
        # (:func:`_submission_is_the_coverage_boundary` proves it from the
        # no-future-day rule), so a blank submission always takes the branch
        # above.  The template dereferences it, so a future edit that broke
        # that guarantee fails LOUD rather than rendering a wrong day.
        feedback = render_template(
            "accounts/_anchor_recorded_toast.html",
            account=account,
            balance=submission.balance,
            observed_on=submission.observed_on,
        )
    # ``None`` is the grid, and only the grid: every named surface re-fetches
    # its own region on the ``balanceChanged`` fired below and redraws its own
    # caption, while three of them carry no ``#anchor-as-of`` element at all
    # (an out-of-band swap there would orphan-target, htmx:oobErrorNoTarget).
    as_of = (
        ""
        if revert_context is not None
        else render_template(
            "grid/_anchor_as_of_oob.html", observed_on=anchor.observed_on,
        )
    )
    return html + as_of + feedback, 200, {"HX-Trigger": "balanceChanged"}


def _anchor_day_bounds() -> dict[str, date]:
    """Return the editor's date-input bounds, keyed for the template.

    The browser refuses what the seam would refuse rather than round-tripping a
    rejection, and both bounds come from the same two PRIMITIVES
    :func:`app.services.anchor_service.resolve_observation_day` refuses by --
    never from a template literal.  **Stated precisely, because a first version
    of this docstring claimed the bounds come from that function**: it exposes
    neither as a value, so the floor here is genuinely one shared implementation
    (``pay_period_service.earliest_recordable_day``) while the ceiling is a
    SECOND reading of the same clock.  A midnight tick between this render and
    the submission therefore lets the browser offer a day the service then
    refuses -- which is exactly why that refusal is rendered
    (:func:`_anchor_editor_error`) rather than assumed unreachable.
    ``display_today()`` rather than ``date.today()``: the
    process clock is pinned to the display zone in the deployed container but
    not in CI or a script, and an input must not offer a day the service then
    rejects (ruling R-DH (b)).

    The layering is deliberate, not redundant: an input bound is captured at
    RENDER time, and the floor moves when pay periods are generated or
    truncated, so a form left open across such a change can still submit a day
    the seam refuses.  That is why the refusal is also rendered
    (:func:`_anchor_editor_error`) rather than assumed unreachable.

    Returns:
        The ``observed_on_min`` / ``observed_on_max`` pair, as dates.
    """
    return {
        "observed_on_min": pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
        "observed_on_max": display_today(),
    }


def _anchor_kind_refusal(account: Account) -> ResponseReturnValue:
    """Refuse a cash-anchor write on an AMORTIZING account, renderably.

    **The kind refusal answers a DISPLAY cell, not an editor** (plan step
    X-f1e3).  A loan's balance is ledger-derived and is asserted on the loan's
    own page (ruling D4 / step A1, finding B-15), so there is nothing here to
    resubmit -- re-rendering the editor would offer a Save button guaranteed to
    be refused again, which is the dead-end affordance this module's own
    ``anchor_form`` docstring says never to offer.
    :func:`_anchor_editor_error` is the right answer for the two INPUT-shaped
    rejections and the wrong one for this, so the two do not share a function.

    **It used to answer a raw string body**, which ``base.html`` leaves
    non-swapping, so the refusal rendered NOTHING and the form sat there.  That
    was justified on the claim that the arm is unreachable because
    ``anchor_form`` refuses to OPEN the editor for a loan -- and an account's
    kind is EDITABLE, so a form opened on a cash account can be submitted after
    that account has become a loan (finding **N-199**;
    ``test_a_cash_account_can_become_a_loan_under_an_open_editor`` walks the
    path).  The ordinary click that used to reach it is gone -- the shared
    partial now renders a loan's cell read-only -- so what remains is this
    race, and a raced write still deserves an answer its surface can render.

    Keeps the 422 rather than the sibling's 400: the payload is well formed and
    it is the ENTITY that cannot be processed.  A designed fragment swaps on
    any status, so naming the failure honestly costs a non-htmx client nothing.

    Args:
        account: The owned, attached amortizing :class:`Account`.

    Returns:
        The designed-fragment ``(body, 422, headers)`` triple.
    """
    return designed_error(
        render_template(
            "grid/_anchor_edit.html",
            account=account,
            anchor_balance=cash_ledger.resolve_anchor(account).balance,
            editing=False,
            error=_LOAN_ANCHOR_REFUSAL,
        ),
        422,
    )


def _anchor_editor_error(
    account: Account, revert_context: str | None, message: str,
) -> ResponseReturnValue:
    """Re-render the anchor editor in place, carrying *message*, as a 400.

    **The ONE rejection surface this door has** (plan step X-f1c4c).  Until that
    step its only rejection answered ``jsonify(errors=...)`` with no marker
    header -- and ``base.html``'s htmx config leaves 4xx non-swapping, so
    clearing the balance box and pressing Save produced a correct 400 that
    rendered NOTHING and left the form sitting there.  Adding a date box made a
    second rejection reachable by ordinary use (a day below the schedule, a form
    submitted after midnight), so the surface had to exist; converting the
    balance arm onto it too is what stops one form having a visible refusal and
    an invisible one.

    Echoes the SUBMITTED values rather than the stored ones: whichever field was
    wrong, the other is still what the user meant, and retyping it is not part of
    the fix.  Jinja escapes both into their attributes, and a value the browser
    cannot parse renders as an empty input -- the native affordance for "this
    needs re-entering".

    Args:
        account: The owned, attached :class:`Account` under edit.
        revert_context: The normalized surface token, or ``None``.  Threaded so
            Cancel / Escape from the error state still restore the surface that
            OPENED the editor rather than stranding a dashboard card on the grid
            cell.
        message: The user-facing reason, already flattened to one sentence.

    Returns:
        The designed-fragment ``(body, 400, headers)`` triple; the global
        ``htmx:beforeSwap`` listener in ``app.js`` swaps it despite the status.
    """
    return designed_error(
        render_template(
            "grid/_anchor_edit.html",
            account=account,
            anchor_balance=request.form.get("anchor_balance", ""),
            observed_on_value=request.form.get("observed_on", ""),
            editing=True,
            error=message,
            revert_url=_anchor_revert_url(account.id, revert_context),
            revert_context=revert_context,
            **_anchor_day_bounds(),
        ),
        400,
    )


def _true_up_request_gates(
    account: Account, revert_context: str | None,
) -> tuple[_AnchorSubmission | None, ResponseReturnValue | None]:
    """Run every pre-mutation gate for ``true_up`` in one place.

    The route grew a fifth early-return gate when the amortizing-kind
    refusal landed (ruling D4 / step A1), tripping Pylint's
    return-statement ceiling; consolidating the gates into a
    ``(values, failure)`` helper mirrors ``_validate_update_account``'s
    established shape.  Gate order: kind refusal first (a loan is
    rejected before its form is even validated -- the KIND of edit is
    wrong, not the payload), then schema validation.

    **Two gates left at plan step X-f1c3c and neither was weakened.**  The
    C-17 stale-form check went with ruling R-EN: an assertion history is
    APPEND-ONLY, so a second tab overwrites no ASSERTION and there is no
    conflict to REPORT -- two assertions are two facts and the later-observed
    one is current.  What that check was incidentally serialising, the posting
    reconcile, is serialised explicitly now
    (:mod:`app.services.user_write_lock`); a door-level gate was never the
    right home for it, since three other doors reach the same window.
    The "No current pay period found" 400 went with ruling R-EO:
    an assertion carries no pay period, so there is no period to resolve and
    nothing this door can fail to find.  That 400 was the true-up half of
    finding N-134's shape -- a balance the user typed, refused for want of a
    budgeting artifact that has nothing to do with what their bank holds.

    **BOTH arms answer a DESIGNED FRAGMENT.**  The schema arm converted at plan
    step X-f1c4c (it was ``jsonify(errors=...)``); the kind arm at X-f1e3, and
    the reason it had NOT converted was a measured-false claim this docstring
    used to make.  It said the editor is never OPENED for an amortizing account
    (``anchor_form`` refuses the same kind), so the arm answered a forged
    request and a designed fragment would be a rendering nobody could reach.
    **An account's kind is EDITABLE.**  ``_ACCOUNT_UPDATE_FIELDS`` includes
    ``account_type_id``, and ``_validate_account_type_change`` permits a
    boundary-crossing re-type while the account has no ledger postings -- which
    a ``$0.00`` opening leaves it with, because a zero correction emits no legs
    (``account_posting_service/_anchors.py``).  So: open the editor on such an
    account, re-type it to a mortgage in a second tab, press Save.  That is a
    real user in ordinary use, and ``base.html`` leaves 4xx non-swapping, so
    the raw body rendered NOTHING and the form simply sat there -- the exact
    defect X-f1c4c converted the other arm to prevent (finding **N-199**).
    It keeps the 422: the payload is well-formed and the ENTITY is what cannot
    be processed, and a designed fragment swaps on any status.

    It is the same open-then-change race ``_anchor_day_bounds`` already
    anticipates for the date floor, one field over: a form captured at render
    time can always be submitted after the state it was rendered against moved.

    Args:
        account: The owned, attached :class:`Account` under edit.
        revert_context: The normalized surface token, or ``None`` -- needed
            because a rejection RE-RENDERS the editor, and the re-rendered
            editor's Cancel must still return to the surface that opened it.

    Returns:
        ``(submission, failure)``.  On success ``failure`` is ``None`` and
        ``submission`` carries the validated balance and day; on rejection
        ``failure`` is the ready-to-return Flask response and ``submission``
        is ``None``.
    """
    if _is_amortizing(account):
        return None, _anchor_kind_refusal(account)

    errors = _anchor_schema.validate(request.form)
    if errors:
        return None, _anchor_editor_error(
            account, revert_context, flatten_schema_errors(errors),
        )

    data = _anchor_schema.load(request.form)
    return _AnchorSubmission(
        balance=Decimal(str(data["anchor_balance"])),
        observed_on=data.get("observed_on"),
    ), None


@accounts_bp.route("/accounts/<int:account_id>/true-up", methods=["PATCH"])
@login_required
@require_owner
def true_up(account_id):
    """Update the anchor balance for an account (inline edit from grid).

    Records the true-up in anchor_history for audit trail, then
    triggers a balance recalculation via HX-Trigger.

    Refuses an AMORTIZING account with 422 (ruling D4 / step A1,
    finding B-15): a loan's balance is ledger-derived and is asserted
    through the loan page's own true-up, never as a cash anchor.

    **It carries no optimistic lock, and that is ruling R-EN** (plan step
    X-f1c3c).  The form used to submit ``version_id`` and a mismatch answered
    409 with the editor in conflict mode; the service used to translate a
    flush-time ``StaleDataError`` into the same response.  Both are gone,
    because a true-up no longer writes the ``accounts`` row that
    ``version_id`` guards -- it appends an assertion.  **No ASSERTION is
    overwritten by a second tab**: two assertions of different balances are
    two facts, the later-observed one is current, and neither is lost.  Two
    tabs submitting the SAME balance for the same day are still idempotent --
    the write door compares against the governing assertion under the owner's
    lock and writes nothing (ruling R-EQ), and the route reports success.  The
    LEDGER those assertions reconcile into is a different question, answered a
    layer down by the per-owner write lock
    (:mod:`app.services.user_write_lock`) rather than here: a lock at this door
    would leave the settle self-heal, the direct anchor edit and the
    pay-period resync reaching the same window unguarded.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404

    # The opener (dashboard balance card, cockpit cell, detail hero) threads
    # its surface on the PATCH query so the success re-render matches the
    # surface that opened the editor.  Normalized against the allowlist so the
    # token is never interpolated unvalidated.
    revert_context = _normalize_revert_context(request.args.get("revert"))

    # Both pre-mutation gates (the D4/A1 amortizing-kind refusal and schema
    # validation) live in ``_true_up_request_gates``; a failure is returned
    # as-is.
    submission, failure = _true_up_request_gates(account, revert_context)
    if failure is not None:
        return failure

    # Canonical anchor true-up path: route the assertion append, the posting
    # re-base and the commit through the single authoritative helper
    # (``anchor_service.apply_anchor_true_up``) so ruling R-EQ's duplicate
    # rule cannot drift.  The route pre-gates
    # the amortizing kind, so the service's
    # ``AmortizingAccountAnchorError`` backstop is unreachable here (a
    # bypassing caller correctly surfaces it as a 500).  The
    # success-response composition (the updated cell, the optional OOB
    # "as-of" snippet, and the ``HX-Trigger: balanceChanged`` header)
    # lives in ``_true_up_success_response``.
    #
    # The DAY's bounds are the seam's, not this route's (ruling R-ER): a future
    # day and a day below the owner's schedule are refused by
    # ``anchor_service.resolve_observation_day``, shared with
    # ``account_service.create_account`` so an account's OPENING assertion and
    # every later one agree about which days are assertable.  (It was shared
    # with the account-edit door too, until plan step X-f1e deleted that door --
    # this is now the only place a balance is RE-asserted.)  Raised BEFORE
    # anything is staged and
    # before the owner's write lock is taken, so there is no transaction to roll
    # back here -- and it is a 400 rather than a 500 because the date box makes
    # it ordinary user input.
    try:
        outcome = anchor_service.apply_anchor_true_up(
            account=account,
            new_balance=submission.balance,
            observed_on=submission.observed_on,
        )
    except ValidationError as exc:
        return _anchor_editor_error(account, revert_context, str(exc))

    # UNCHANGED and COMMITTED share the success response (the
    # updated cell + an OOB "as of" snippet + the HX-Trigger that
    # recomputes other grid cells), so they converge on one return.
    if outcome is AnchorTrueUpOutcome.UNCHANGED:
        # Ruling R-EQ idempotent success: the submission asserts the balance
        # that already stands, so nothing was written and the session was
        # rolled back.  Expire the account so the partial re-reads the
        # assertion that governs rather than anything this request held.
        db.session.expire(account)
    else:
        db.session.refresh(account)
        # No log line here.  ``anchor_service.stage_anchor_true_up`` logs the
        # account, the balance AND the resolved day -- and it is the only layer
        # that knows the day, because a blank date box means "today" without
        # this one being told what that resolved to.  A route line naming the
        # same account and balance MINUS the day is a strict subset of the
        # writer's, and two INFO lines per true-up where one is contained in the
        # other is noise that reads as corroboration.

    return _true_up_success_response(account, revert_context, submission)


def _anchor_revert_url(account_id, revert_context):
    """Resolve the URL the anchor editor reverts to on Cancel / Escape.

    The anchor editor (``grid/_anchor_edit.html``) is opened from more
    than one surface, and Cancel / Escape must restore whichever surface
    opened it -- not always the grid display cell.  This maps the
    normalized surface token (from :func:`_normalize_revert_context`) to
    the GET endpoint that re-renders the opener.  ``None`` falls back to
    the grid's ``anchor_display`` so the grid path is byte-for-byte
    unchanged (it passes no ``revert``).

    Locked contexts:

    * ``dashboard`` -- the dashboard balance card re-renders via
      ``dashboard.balance_section`` (restores the account name, caption,
      and runway the grid display cell lacks; the audit's cancel-path
      stranding fix).
    * ``accounts`` -- the Net Worth Cockpit's per-card balance cell
      re-renders via ``savings.cockpit_balance`` (restores that one card's
      resolver balance; the cockpit is multi-card, so the revert is
      account-scoped rather than the dashboard's single hero).
    * ``investment`` -- the investment / retirement detail page's balance
      hero re-renders via ``investment.balance_hero`` (restores the
      model-from-anchor balance the detail headline shows; Loop B P1 C4).
    * ``cash`` -- the cash detail page's balance hero re-renders via
      ``accounts.cash_balance_hero`` (restores the resolver
      current-period balance the detail headline shows; S8 / D14 port).
    * default / grid -- ``accounts.anchor_display`` (the grid cell).

    Args:
        account_id: The account whose editor is being reverted.
        revert_context: The normalized surface token, or ``None``.

    Returns:
        The revert URL string.
    """
    if revert_context == "dashboard":
        return url_for("dashboard.balance_section")
    if revert_context == "accounts":
        return url_for("savings.cockpit_balance", account_id=account_id)
    if revert_context == "investment":
        return url_for("investment.balance_hero", account_id=account_id)
    if revert_context == "cash":
        return url_for("accounts.cash_balance_hero", account_id=account_id)
    return url_for("accounts.anchor_display", account_id=account_id)


@accounts_bp.route("/accounts/<int:account_id>/anchor-form", methods=["GET"])
@login_required
@require_owner
def anchor_form(account_id):
    """HTMX partial: return the inline edit form for the anchor balance.

    Accepts an optional ``revert`` query parameter naming the surface
    that opened the editor (e.g. ``dashboard``), so Cancel and Escape
    restore that surface rather than always swapping in the grid display
    cell.  See :func:`_anchor_revert_url` for the mapping; an unset value
    keeps the grid's default revert target.

    The normalized token is also passed to the template as
    ``revert_context`` so the edit form's ``hx-patch`` carries the surface
    through the mutation round-trip, keeping the success re-render on the
    surface that opened the editor rather than stranding the dashboard card
    on the grid display cell.  It carried a second job -- letting a 409
    conflict response re-render the conflict cell with the same retry-reopen
    target -- until ruling R-EN deleted that response (plan step X-f1c3c).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    # Ruling D4 / step A1: never OPEN the cash anchor editor for an
    # amortizing loan -- the PATCH would be refused (B-15), so offering
    # the form would be a dead-end affordance.
    #
    # **The affordance itself is gone since plan step X-f1e3**: the shared
    # partial renders a loan's balance read-only on every surface, the rule the
    # cockpit's loan cards already followed and the other four did not.  So
    # this arm no longer answers an ordinary click; what can still reach it is
    # a RACE -- the cell was rendered while the account was cash and clicked
    # after it became a loan (an account's kind is editable).  It answers a
    # designed fragment rather than a raw body for that case, because a raw
    # 4xx is left non-swapping by ``base.html`` and the click would otherwise
    # do nothing visible at all -- a dead click with no form to explain it,
    # which is finding N-199's defect in its worst form.
    if _is_amortizing(account):
        return _anchor_kind_refusal(account)

    revert_context = _normalize_revert_context(request.args.get("revert"))
    revert_url = _anchor_revert_url(account_id, revert_context)
    bounds = _anchor_day_bounds()
    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        anchor_balance=cash_ledger.resolve_anchor(account).balance,
        editing=True,
        # The statement day defaults to TODAY, not to the governing assertion's
        # own day (rulings **R-EE** / **R-EI**, plan step X-f1c4c).  A true-up is
        # the user reading their bank NOW in the overwhelming case, and R-EE
        # keeps that one click plus Enter; prefilling the last assertion's day
        # would make the ordinary path silently RE-assert an old day, which is
        # the one thing this field exists to stop being a guess.  Back-dating is
        # then a deliberate edit of a box that already shows the right answer.
        observed_on_value=bounds["observed_on_max"].isoformat(),
        revert_url=revert_url,
        revert_context=revert_context,
        **bounds,
    )


@accounts_bp.route("/accounts/<int:account_id>/anchor-display", methods=["GET"])
@login_required
@require_owner
def anchor_display(account_id):
    """HTMX partial: return the anchor balance display (non-editing)."""
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        anchor_balance=cash_ledger.resolve_anchor(account).balance,
        editing=False,
    )
