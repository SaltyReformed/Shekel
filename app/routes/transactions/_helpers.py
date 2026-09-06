"""
Shekel Budget App -- Transaction route package: shared helpers.

The Marshmallow schema singletons, the credit-payback unique-index name
constant, the :class:`_RenderTarget` response-surface bundle, and the
private render / ownership / FK helpers shared across the transaction
route sub-modules.  Schema instances are constructed once at import time
so every handler reuses the same instance (Marshmallow contract),
preserving the pre-split monolith's behaviour.
"""

import logging

from dataclasses import dataclass

from flask import render_template
from flask_login import current_user

from app.extensions import db
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status
from app.routes._render_helpers import (
    fragment_amounts,
    render_transaction_cell,
)
from app.schemas.validation import (
    MarkDoneSchema,
    TransactionUpdateSchema,
    TransactionCreateSchema,
    InlineTransactionCreateSchema,
)
from app.services import grid_view_service
from app.services.state_machine import finalised_edit_rejection
from app.services.entry_service import (
    build_entry_lists_dict,
    build_entry_sums_dict,
)
from app.services.pay_calendar import FiledRow, calendar_for
from app.utils.auth_helpers import (
    get_accessible_transaction,
    log_refused_lookup,
)
from app.utils.dates import display_today
from app.utils.db_errors import is_unique_violation
from app.utils.error_fragments import INVALID_REFERENCE_MSG, designed_error

# Name of the partial unique index that backstops commit C-19's
# duplicate CC Payback fix.  Mirrors the literal in
# ``migrations/versions/b3d8f4a01c92_*.py`` and
# ``app.models.transaction.Transaction.__table_args__``; renaming
# the index requires a coordinated edit across all three sites.
_CREDIT_PAYBACK_UNIQUE_INDEX = "uq_transactions_credit_payback_unique"

# Package-local alias of the shared foreign-key rejection message so the
# mutation handlers import it alongside the other private helpers.
_INVALID_REFERENCE_MSG = INVALID_REFERENCE_MSG

# Money / period / category / due-date fields that the finalised-row edit
# lock (#26) protects.  Names match :class:`TransactionUpdateSchema` (the
# loaded ``data`` dict for both the regular and the transfer-shadow PATCH
# paths).  Display fields (``notes``, ``name``) and the ad-hoc visibility
# flags stay editable on a finalised row; the ``status_id`` transition is
# guarded separately by ``state_machine.verify_transition``.
#
# ``settled_on`` is deliberately NOT here (ruling **R-ED**, plan step X-f1c).
# Every member below is a BUDGET DECISION the user made, locked so a paid
# movement cannot be retroactively rewritten; the settle day is an OBSERVED
# FACT about their bank, and an observed fact gets corrected when the statement
# says otherwise.  Locking it would make the correction UNREACHABLE -- reverting
# to Projected and re-settling stamps TODAY, so "this actually cleared last
# Tuesday" would be inexpressible.  It is the same line ``TransactionEntry``
# draws one table over (``purchased_on`` guarded, ``settled_on`` freely
# editable on the inline form).
#
# ``settled_amount`` is NOT here either, and it sits on exactly the same side
# of that line as ``settled_on`` (developer ruling, 2026-08-17).  A draft of
# plan step X-au-c3 locked it, reasoning that "re-stating what a settle recorded
# is not an edit" -- but the two facts a settle records are the FIGURE and the
# DAY, both observations about the bank, and locking one while exempting the
# other split a pair that belongs together.  The estimate and the actual are
# different facts and get different boxes: the estimate is the budget decision
# the lock protects, and the actual is what the statement says.  Locking it made
# the only correction path revert-and-re-settle, which silently re-booked a
# retained figure over a re-planned amount -- so the lock did not merely
# inconvenience, it produced a wrong number.
_LOCKED_EDIT_FIELDS = frozenset({
    "estimated_amount", "category_id", "pay_period_id", "due_date",
})

logger = logging.getLogger(__name__)

# Marshmallow schema instances.
_update_schema = TransactionUpdateSchema()
_create_schema = TransactionCreateSchema()
_inline_create_schema = InlineTransactionCreateSchema()

# Schema for the optional ``settled_amount`` form field on
# ``mark_done`` (the field was ``actual_amount`` until plan step X-au-c3
# replaced that column with a settlement record).  Single instance per
# process (Marshmallow contract); replaces the per-branch raw
# ``Decimal(request.form.get(...))``
# parse the route used before commit C-27 / F-042 / F-162 of the
# 2026-04-15 security remediation plan.
_mark_done_schema = MarkDoneSchema()


@dataclass(frozen=True)
class _RenderTarget:
    """The response surface a mark_done request renders into.

    Bundles the three render-routing fields the mobile / companion card
    action bar posts (``render=mobile_card`` plus the per-tab
    ``card_prefix`` and the ``can_edit`` flag) so :func:`mark_done` and
    its helpers thread one value instead of three parallel arguments.
    The desktop grid and full-edit popover omit these, so the default
    (empty ``render_mode``) resolves to the cell + ``balanceChanged``
    targeted-swap path.
    """

    render_mode: str
    card_prefix: str
    can_edit: bool


def _render_mobile_card(txn, *, card_prefix, can_edit, error=None):
    """Render a single mobile transaction card for an HTMX swap.

    The mobile / companion Mark Paid form targets the card wrapper
    (``hx-target="#card-<prefix-><id>"``, ``hx-swap="outerHTML"``), so
    this returns exactly that one card re-rendered in its post-action
    state -- the settled badge shows and the Mark Paid button drops --
    without the full-page reload the desktop ``gridRefresh`` path uses.

    Reuses the canonical producers so the swapped-in card is identical
    to the page-load card: :func:`grid_view_service.build_row_keys` for
    the row label, :func:`build_entry_sums_dict` for the progress
    aggregate, and :func:`build_entry_lists_dict` for the inline
    envelope entries.  Ownership scoping uses ``txn.user_id`` (the data
    owner) so the companion path resolves the linked owner's categories, not
    the companion's own (empty) set.  It read that value off the paycheck
    (``txn.pay_period.user_id``) until plan step ``pay_calendar:C13-b``; the
    walk hydrated a whole ``budget.pay_periods`` row to learn one integer the
    transaction carries.

    **It derives that owner's pay calendar since pay-calendar plan step
    C4-a-3**, which is one indexed read of ``budget.pay_periods`` and one of
    ``budget.pay_schedule`` more than this fragment used to cost.  The entry
    list's out-of-period warning needs the paycheck's SPAN, and the span is a
    derivation over the owner's paydays rather than the ``end_date`` column
    plan step **C4-c** dropped -- so a single-row surface that holds no window
    has to derive rather than read.  Deriving is the only honest option left:
    a targeted "what is the next payday after this one" query here would be a
    second implementation of the rule this arc exists to state once.

    Args:
        txn: The Transaction just settled, with ``entries`` and
            ``template`` accessible.
        card_prefix: The per-tab id namespace the requesting card used
            (``"tp"`` for This Period and the companion view; ``""`` for
            prefix-less direct renders).  Drives the wrapper id so the
            outerHTML swap resolves.
        can_edit: ``True`` for the owner card, ``False`` for companion.
        error: Optional rejection message.  When set, the card renders
            a danger banner naming why the mutation was refused (the
            designed error-fragment path; see
            :func:`_error_transaction_response`).

    Returns:
        Rendered HTML string for one ``grid/_mobile_card_single.html``.
    """
    owner_id = txn.user_id
    categories = (
        db.session.query(Category)
        .filter_by(user_id=owner_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    row_keys = grid_view_service.build_row_keys(
        [txn], categories, is_income_section=txn.is_income,
    )
    # A just-settled transaction is neither cancelled nor deleted, so
    # the SUCCESS path always yields a row key; the guard degrades to
    # the desktop cell rather than raising IndexError.  The ERROR path
    # can genuinely land here: the card lists filter cancelled rows
    # out, so a stale card's rejected action (e.g. Mark Paid after
    # another device cancelled) has no card to re-render -- swap in a
    # banner-only wrapper that keeps the requesting card's id and says
    # why the action was refused.
    if not row_keys:
        if error is not None:
            return render_template(
                "grid/_mobile_card_error.html",
                txn=txn, id_prefix=card_prefix, error=error,
            )
        return render_transaction_cell(txn)
    amounts = fragment_amounts(txn)
    budgets = amounts.budgets
    # The paycheck this row is FILED in, DERIVED (pay-calendar plan step
    # C4-a-3): the entry-list builder judged its out-of-period warning against
    # ``txn.pay_period.end_date`` until then, a stored column plan step C4-c
    # drops.  The owner is the one resolved above -- the DATA owner, so the
    # companion path reads the linked owner's schedule -- and ``require_period``
    # rather than ``period_by_id`` because the id comes off a stored row with a
    # NOT NULL, ``ON DELETE CASCADE`` foreign key, so a ``None`` here would be
    # an inconsistent picture rather than an answer.
    #
    # **Only a row that TRACKS PURCHASES needs it**, on the same predicate
    # ``build_entry_lists_dict`` already filters by: a plain bill draws no
    # purchase list, so deriving its owner's whole calendar would be two
    # queries for a value nothing reads.  Found by adversarial review,
    # 2026-08-31.
    #
    # **The READ ORDER, stated here because ``require_period`` requires every
    # caller to state its own**: the ROW is read first (the ownership door
    # above), the paydays second.  So this is exposed to a concurrent
    # DESTRUCTIVE pay-period door -- reset, regenerate or truncate -- landing
    # between the two under ``READ COMMITTED``: the identity-mapped
    # ``txn.pay_period`` still answers while the fresh payday read no longer
    # holds the id.  That is balance finding **N-358**, and this is a
    # render-after-commit path, which is the half of it that has no snapshot.
    # `balance:X-i5` is the remedy; `C4-a-2`'s -- scope the query by the
    # calendar's own ids -- is unavailable here, because the row arrives from
    # ``get_accessible_transaction`` and reordering that door is not this
    # leaf's to do.
    period = (
        calendar_for(owner_id).require_period(FiledRow.for_row(txn))
        if txn.tracks_purchases
        else None
    )
    return render_template(
        "grid/_mobile_card_single.html",
        rk=row_keys[0],
        txn=txn,
        budgets=budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        entry_sums=build_entry_sums_dict([txn], budgets),
        # The span map is EMPTY for a row that draws no purchase list, and
        # the builder's own ``tracks_purchases`` filter runs before it
        # indexes -- so the two agree by reading one predicate rather than
        # by a comment saying they do.
        entry_lists=build_entry_lists_dict(
            [txn], budgets,
            {} if period is None else {period.period_id: period},
        ),
        can_edit=can_edit,
        id_prefix=card_prefix,
        # The USER's civil day, never the process's.  This reaches
        # ``grid/_transaction_entries.html``'s add-purchase form as the
        # ``purchased_on`` default and both pickers' ``max``, and
        # ``entry_service._reject_future_purchase_date`` judges that field
        # against ``display_today()`` (ruling R-M).  With ``date.today()`` here
        # the two clocks disagree on any process not pinned to
        # ``America/New_York`` -- CI runs ``TZ=Pacific/Kiritimati`` precisely to
        # catch this -- and the app's own form would default to a date its own
        # server rejects.  Prod pins the zone, so this was latent rather than
        # live; it is the same two-clock shape as finding N-133 / R2.
        today=display_today(),
        error=error,
    )


def _mark_done_success_response(txn, target):
    """Build the success response tuple for a mark_done request.

    Forks on the rendering surface the request came from:

      * ``target.render_mode == "mobile_card"``: return the single
        re-rendered mobile card + ``HX-Trigger: mobileCardSettled``.  The
        card swaps in place (no reload); the owner This Period summary
        blocks listen for ``mobileCardSettled`` and self-refresh, while
        the companion page has no summary blocks so only the card
        updates.
      * otherwise (desktop grid / full-edit popover): the desktop cell +
        ``HX-Trigger: balanceChanged`` -- a targeted swap, no reload.
        The freshly settled cell swaps in place (``hx-target`` is the
        cell), and ``balanceChanged from:body`` drives the self-refresh
        on the sticky ``<tfoot>`` balance row (grid/_balance_row.html)
        and the two summary subtotal ``<tbody>`` sections
        (grid/_subtotal_rows.html), so the daily desktop mark-paid feels
        instant.  This is the REGULAR (non-transfer) mark_done path only:
        the helper is reached solely from :func:`_mark_done_regular`.
        The transfer-shadow path (:func:`_mark_done_shadow`) deliberately
        keeps ``gridRefresh`` because the sibling shadow cell on the
        other leg also changes and only a full reload re-renders it
        today; ``mark_credit`` / ``cancel_transaction`` / ``unmark_credit``
        likewise keep ``gridRefresh`` because they add or remove grid
        rows, which an in-place cell swap cannot express.

    Args:
        txn: The settled Transaction.
        target: The :class:`_RenderTarget` describing the response
            surface.  ``render_mode`` selects the mobile-card vs desktop
            path; ``card_prefix`` / ``can_edit`` are forwarded to the
            card render and are only meaningful on the mobile_card path.

    Returns:
        A Flask ``(html, status, headers)`` response tuple.
    """
    if target.render_mode == "mobile_card":
        return (
            _render_mobile_card(
                txn, card_prefix=target.card_prefix, can_edit=target.can_edit,
            ),
            200,
            {"HX-Trigger": "mobileCardSettled"},
        )
    return render_transaction_cell(txn), 200, {"HX-Trigger": "balanceChanged"}


def _credit_payback_idempotent_response(exc, txn_id):
    """Translate a credit-payback unique-index violation into a 200.

    Backstop for commit C-19 (audit finding F-008): if a future
    caller bypasses ``credit_workflow.mark_as_credit``'s row lock
    and a duplicate payback INSERT reaches PostgreSQL,
    ``uq_transactions_credit_payback_unique`` rejects it and this
    helper rolls back, re-fetches the source row, and renders the
    cell at HTTP 200 -- matching what a serialised request would
    have produced.  Other ``IntegrityError`` constraint hits return
    the standard 400 so unrelated FK / check failures stay visible.
    """
    db.session.rollback()
    if not is_unique_violation(exc, _CREDIT_PAYBACK_UNIQUE_INDEX):
        return _error_transaction_response(txn_id, _INVALID_REFERENCE_MSG)
    logger.info(
        "Duplicate CC payback prevented on mark_credit id=%d "
        "(idempotent success)", txn_id,
    )
    refreshed = _get_owned_transaction(txn_id)
    if refreshed is None:
        return "Not found", 404
    return (
        render_transaction_cell(refreshed),
        200,
        {"HX-Trigger": "gridRefresh"},
    )


def _stale_transaction_response(txn_id, target=None):
    """Roll back the session and render the cell in conflict mode + 409.

    Used by every PATCH/POST/DELETE handler that can race a
    concurrent commit against the version-pinned UPDATE.  Re-fetches
    the transaction from the database so the user sees the winner's
    state -- never the loser's stale in-memory copy -- and tags the
    cell with ``conflict=True`` so the template surfaces a warning
    indicator.  Returns a 404 if the row was hard-deleted by the
    winning request.

    The mobile/companion Mark Paid path passes a :class:`_RenderTarget`
    with ``render_mode == "mobile_card"`` so the 409 body is the
    re-rendered mobile card (latest state) rather than the desktop cell;
    the card's ``hx-target`` is the card wrapper, so a desktop-cell body
    would not swap.  That path re-fetches through
    :func:`get_accessible_transaction` so a companion's
    conflict resolves against the linked owner's row (the desktop path
    uses :func:`_get_owned_transaction`, which is owner-only).

    Args:
        txn_id: Primary key of the transaction the route was trying
            to mutate.  Used to re-fetch under ownership checks so
            the conflict UI renders the correct row.
        target: The :class:`_RenderTarget` for the mobile/companion Mark
            Paid path, or ``None`` (the default) for the desktop-cell
            conflict every non-mobile caller wants.  When present and
            ``render_mode == "mobile_card"`` the 409 body is the mobile
            card; ``card_prefix`` / ``can_edit`` drive its wrapper id and
            the owner-vs-companion edit affordance.

    Returns:
        Flask response tuple ``(html, 409)`` or ``("Not found", 404)``
        when the row vanished entirely.
    """
    db.session.rollback()
    db.session.expire_all()
    if target is not None and target.render_mode == "mobile_card":
        txn = get_accessible_transaction(txn_id)
        if txn is None:
            return "Not found", 404
        return (
            _render_mobile_card(
                txn, card_prefix=target.card_prefix, can_edit=target.can_edit,
            ),
            409,
        )
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_transaction_cell(txn, conflict=True), 409


def _error_transaction_response(txn_id, message, target=None, status=400):
    """Roll back and render the request's surface as a designed error.

    The rejected-mutation twin of :func:`_stale_transaction_response`
    (the marker-header convention, closeout plan session 4): every
    transaction-mutation 400/422 that a user can reach re-renders the
    surface the request targeted -- the desktop cell, or the mobile /
    companion card when the request carried ``render=mobile_card`` --
    with CURRENT data plus the rejection message, and stamps the
    designed-fragment header so the body swaps instead of being
    silently dropped by the app-wide htmx config.

    Rolls back unconditionally: some callers reject before any mutation
    (a no-op rollback), others after the service staged changes; one
    rollback here keeps every caller safe and the re-fetch below then
    reads committed state.

    Args:
        txn_id: Primary key of the transaction the route was trying to
            mutate.  Re-fetched under the same ownership rules as the
            stale-conflict helper (accessible for the mobile-card path,
            owner-only for the desktop cell).
        message: The user-facing rejection message (shown in the cell's
            title/aria hint or the card's danger banner).
        target: The :class:`_RenderTarget` for the mobile/companion
            path, or ``None`` for the desktop cell.
        status: The HTTP error status (400 domain rejection, 422
            validation failure).

    Returns:
        A designed-fragment Flask response tuple, or
        ``("Not found", 404)`` when the row vanished.
    """
    db.session.rollback()
    db.session.expire_all()
    if target is not None and target.render_mode == "mobile_card":
        txn = get_accessible_transaction(txn_id)
        if txn is None:
            return "Not found", 404
        return designed_error(
            _render_mobile_card(
                txn, card_prefix=target.card_prefix,
                can_edit=target.can_edit, error=message,
            ),
            status,
        )
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return designed_error(render_transaction_cell(txn, error=message), status)


def _finalised_edit_response(txn, data):
    """Reject locked-field edits on a finalised (is_immutable) transaction.

    Looks up the current and (if the PATCH transitions status) the new
    :class:`Status` BEFORE the caller's ``setattr`` loop dirties the
    session -- matching ``_resolve_status_change``'s autoflush-safe
    ordering -- and defers the policy decision to
    :func:`finalised_edit_rejection`.  Applies to the regular edit path
    (``mutations._apply_regular_update``) and the transfer-shadow path
    (``_shadow_mutations._apply_shadow_update`` -- the shadow's status
    mirrors its parent transfer's, Invariant 3), the two user edit entry
    points; the system mutation paths (recurrence, carry-forward,
    mark-done, cancel) deliberately bypass this lock.

    It lives HERE rather than beside either caller because those two now
    sit in different modules (plan step X-f1c's split), and a route helper
    both mutation modules need is exactly this module's purpose -- the
    alternative is one module importing the other, which closes a cycle.

    Args:
        txn: The Transaction (or transfer shadow) being edited.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A designed 400 error-fragment response when a locked field is
        edited on a finalised row not being reverted to a mutable
        status, or ``None`` when the edit may proceed.
    """
    if not _LOCKED_EDIT_FIELDS & data.keys():
        return None
    current_status = db.session.get(Status, txn.status_id)
    new_status = (
        db.session.get(Status, data["status_id"])
        if "status_id" in data else None
    )
    message = finalised_edit_rejection(
        current_status, new_status, context="transaction",
    )
    if message is None:
        return None
    return _error_transaction_response(txn.id, message)


def _get_owned_transaction(txn_id):
    """Fetch a transaction and verify it belongs to the current user.

    Ownership is the row's OWN ``user_id`` column since plan step
    ``pay_calendar:C13-b``.  This read was ``txn.pay_period.user_id``, and the
    docstring here said "since transactions don't have a direct user_id
    column" -- a premise plan step ``C13-a`` retired.  The two values cannot
    disagree: ``fk_transactions_owner_period`` holds the row's owner equal to
    its paycheck's on every write.

    **Dropping the walk drops a HYDRATION the callers used to inherit**, and
    :func:`app.routes._render_helpers.render_transaction_cell` is the one that
    names it -- see its own docstring, which this step re-measured.

    Returns:
        Transaction if found and owned by current_user, else None.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    if txn.user_id != current_user.id:
        return None
    return txn


def _resolve_owned_period(period_id, not_found_msg="Pay period not found"):
    """Answer ``current_user``'s calendar for *period_id*, or refuse.

    **The ONE way this blueprint asks whether a submitted paycheck is the
    requester's** (plan step ``pay_calendar:C13-b``).  Three doors -- both
    create routes and the PATCH door's :func:`_verify_owned_fks_in_update` --
    passed a ``PayPeriod`` spec to :func:`_resolve_owned_fks` instead: fetch
    ``budget.pay_periods`` by primary key, compare ``row.user_id`` against the
    requester.  Those are three of the EIGHT primary-key refetches finding
    **P75** counts.

    **Why the calendar and not the composite key.**  ``C13-a`` made a
    transaction filed in a stranger's paycheck UNSTORABLE
    (``fk_transactions_owner_period``), so deleting these checks outright
    would still refuse the write -- but as an ``IntegrityError`` the create
    routes translate into ``400 "Invalid reference..."``, where the security
    response rule asks for the 404 that "not found" gets.  The key answers
    STORAGE; this answers INPUT, and they are two different questions.  A
    calendar holds ONE owner's whole schedule, so a foreign id is ABSENT
    rather than present-and-rejected: the two answers the rule wants
    indistinguishable are one answer, with no comparison left for a later edit
    to drop.  It is the shape plan step C2-f3e ruled for the READ doors, and
    the developer ruled this step to extend it to the write doors (2026-09-03).

    **It LOGS where ``_resolve_owned_fks`` is silent**, and that is
    :func:`~app.utils.auth_helpers.log_refused_lookup`'s stated rule rather
    than an addition: an owner-scoped lookup cannot tell "no such row" from
    "not yours", which is the stronger security property and exactly why it
    must not also mean no trail.

    Args:
        period_id: A submitted ``budget.pay_periods.id``, or ``None``.
        not_found_msg: The 404 body.  Defaults to the write doors' wording;
            the grid-cell fragments pass their own uniform ``"Not found"`` so
            no fragment's body says which of its four ids was wrong.

    Returns:
        ``(period, None)`` with the resolved
        :class:`~app.services.pay_calendar.DerivedPeriod`, or
        ``(None, (not_found_msg, 404))`` -- a Flask response tuple the caller
        returns directly to HTMX.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar (see
            :func:`~app.services.pay_calendar.calendar_for`).  Uncaught, as at
            every other route that derives one -- but note it CHANGES what a
            schedule-less owner meets at these three write doors: the
            application handler (**R-PC42**) renders the recovery card at
            **200** where the primary-key compare answered
            ``("Pay period not found", 404)``.  Nothing leaks -- that card
            names no submitted id -- and the state is unreachable for an owner
            registration has served since plan step ``balance:X-ad-a``.
    """
    period = calendar_for(current_user.id).period_by_id(period_id)
    if period is None:
        log_refused_lookup("PayPeriod", period_id)
        return None, (not_found_msg, 404)
    return period, None


def _resolve_owned_fks(specs):
    """Fetch and ownership-check a sequence of user-scoped FK ids.

    Centralizes the IDOR probe shared by the transaction create routes
    (:func:`create_inline`, :func:`create_transaction`) and the grid
    form-partial routes (:func:`get_quick_create`,
    :func:`get_full_create`, :func:`get_empty_cell`, which reach it through
    their one shared ``_resolve_grid_cell``) and the PATCH door's own
    :func:`_verify_owned_fks_in_update`.  **No ``PayPeriod`` spec arrives here
    any more**: plan step C2-f3e answered the period from the owner's derived
    pay calendar on the three form-partial routes, and plan step
    ``pay_calendar:C13-b`` moved the last three -- the two create doors and
    the PATCH door -- onto :func:`_resolve_owned_period`, which is that same
    lookup given a name.  What is left here fetches by primary key and
    compares, because no derivation owns ``budget.accounts``,
    ``budget.categories`` or ``budget.scenarios``.  For each
    ``(model, obj_id, not_found_msg)`` spec the row is fetched by
    primary key and confirmed to belong to ``current_user``; a missing
    row and a cross-user row return the identical 404 so an attacker
    cannot distinguish the two (security response rule: "404 for both
    not found and not yours").  Specs are checked in order and the
    first failure short-circuits, so callers list them so the most
    specific message surfaces first -- mirroring the sequential per-FK
    checks these routes used before the extraction.

    A ``None`` ``obj_id`` resolves to the 404 without issuing a
    NULL-primary-key query (which SQLAlchemy warns cannot load a row);
    the create routes never pass ``None`` (schema-required fields), but
    the form-partial routes read ids straight off the query string.

    Args:
        specs: ordered ``(model, obj_id, not_found_msg)`` tuples.  Use a
            distinct model class per spec: the returned map is keyed by
            model, so two specs sharing one model collapse to the last
            row fetched.  That precondition holds for every caller today;
            note it touches only the convenience map -- every spec is
            still ownership-checked, so a collision could never weaken
            the 404 gate.

    Returns:
        ``(resolved, None)`` on success, where *resolved* maps each
        spec's model class to its fetched row, or ``(None, (msg, 404))``
        on the first ownership failure -- a Flask response tuple the
        caller returns directly to HTMX.
    """
    resolved = {}
    for model, obj_id, not_found_msg in specs:
        obj = db.session.get(model, obj_id) if obj_id is not None else None
        if obj is None or obj.user_id != current_user.id:
            return None, (not_found_msg, 404)
        resolved[model] = obj
    return resolved, None


def _verify_owned_fks_in_update(data):
    """Verify cross-user FK ownership for the PATCH update payload.

    Used by :func:`update_transaction` to reject ``pay_period_id``
    and ``category_id`` values that belong to another user before
    any state-changing work runs.  The period goes through
    :func:`_resolve_owned_period` since plan step ``pay_calendar:C13-b`` --
    the owner's calendar, asked FIRST so the message order this door's 404
    strings depend on is unchanged -- and the category through
    :func:`_resolve_owned_fks`.  Without this probe an
    authenticated owner could submit a victim's ``pay_period_id``
    or ``category_id`` and the unfiltered ``setattr`` loop in
    :func:`update_transaction` would silently re-parent the
    transaction into the victim's namespace -- the FK constraint
    passes because the row exists, just under another user, and
    PostgreSQL never raises ``IntegrityError``.

    Audit reference: F-029 / commit C-29 of the 2026-04-15
    security remediation plan.  Mirrors the route-boundary FK
    probes already in :func:`create_inline` and
    :func:`create_transaction`; ``status_id`` is a reference table
    FK (not user-scoped) and so does not need an ownership check.
    The 404 strings deliberately match the messages used by the
    create routes so the client cannot tell whether the row does
    not exist or belongs to another user (security response rule:
    "404 for both not found and not yours").

    Args:
        data: The schema-loaded PATCH payload.  ``pay_period_id``
            and ``category_id`` are the only user-scoped FK keys
            inspected; absent keys are skipped.

    Returns:
        ``None`` on success.  On failure, a Flask response tuple
        ``(body, 404)`` the caller returns directly to HTMX.
    """
    if "pay_period_id" in data:
        _, error = _resolve_owned_period(data["pay_period_id"])
        if error is not None:
            return error
    specs = []
    if "category_id" in data:
        specs.append((Category, data["category_id"], "Category not found"))
    _, error = _resolve_owned_fks(specs)
    return error
