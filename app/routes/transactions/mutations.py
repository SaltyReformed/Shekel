"""
Shekel Budget App -- Transaction route package: mutation handlers.

Every state-changing transaction route on a single row: the PATCH inline
edit save, the DELETE soft/hard delete, and the status workflow
(mark-done, mark/unmark credit, cancel).  Shadow transactions
(``transfer_id IS NOT NULL``) route through the transfer service so both
shadows and the parent transfer stay in sync (design doc invariants 3-5);
those three branches live in :mod:`._shadow_mutations`, which carries why
they were split out and why they moved TOGETHER.

The edit and status concerns share this one module deliberately: their
REGULAR (non-shadow) paths are near-identical parallel code (the apply +
posting reconcile + commit body, the ``StaleDataError`` / ``IntegrityError``
tails, the ``_RenderTarget`` response handling).  Splitting those across
modules would re-surface the intra-file duplication the monolith hid (R0801
is cross-file only); co-locating them keeps that intentional parallel code in
one file.
"""

import logging

from flask import request
from flask_login import current_user, login_required
from marshmallow import ValidationError as MarshmallowValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.services import (
    credit_workflow,
    posting_service,
    status_seam,
    transaction_service,
)
from app.services.settle_day import recorded_settle_day
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import get_accessible_transaction, require_owner
from app.utils.balance_predicates import is_credit
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._gates import (
    _reject_tracking_on_income,
    _reject_typed_payback_figure,
    _resolve_status_change,
)
from app.routes._render_helpers import render_transaction_cell
from app.routes.transactions._helpers import (
    _credit_payback_idempotent_response,
    _error_transaction_response,
    _finalised_edit_response,
    _get_owned_transaction,
    _INVALID_REFERENCE_MSG,
    _mark_done_schema,
    _mark_done_success_response,
    _RenderTarget,
    _stale_transaction_response,
    _update_schema,
    _verify_owned_fks_in_update,
)
from app.routes.transactions._shadow_mutations import (
    _apply_shadow_update,
    _cancel_shadow,
    _mark_done_shadow,
)
from app.utils.error_fragments import flatten_schema_errors

logger = logging.getLogger(__name__)

# The PATCH fields whose change can alter a transaction's posted double-entry
# ledger effect, so a change to any triggers a posting reconcile (Build-Order
# Step 3, the transaction analog of ``transfer_service._POSTING_RELEVANT_FIELDS``).
# ``status_id`` flips the settled/unsettled target; ``estimated_amount`` is the
# row's PLAN and ``settled_amount`` what it RECORDED, and the status decides
# which one is the magnitude posted (``row_valuation.settled_figure``, plan step
# X-au-c3 -- it was ``COALESCE(actual, estimated)``); ``category_id``
# moves which counter (category) ledger account the expense/income leg books
# into.
#
# ``settled_on`` IS here, and its inclusion is ruling **R-ED**'s second half:
# since plan step E1a a settle's civil day IS the ``entry_date`` its postings
# are filed under, so moving the day moves the day every posting counts from.
# ``sync_transaction_postings`` reconciles per ``(period, entry_date)``, so the
# entry at the OLD day reverses at that day and the effect re-posts at the new
# one (finding **N-13**), and its tail re-derives the account's anchor
# corrections that the moved delta staled.  Without it the rendered balance
# would move while the posted ledger kept the stale date -- the books silently
# out of step with the screen, on the one surface this arc exists to keep in
# step.
#
#
# ``pay_period_id`` IS here since plan step X-f3b (ruling **R-FM**), and the
# reason it was not is the reason it must be: the omission was justified by "a
# settled row's period cannot move anyway (the finalised-edit lock blocks
# editing it unless the same PATCH reverts to Projected)", which was sound
# while only a SETTLED row held postings.  A PROJECTED envelope now holds one
# per purchase whose bank posting day is recorded, and a posting carries the
# BUDGET column its source row is attributed to -- so moving such a row without
# a reconcile leaves its purchases' legs filed under the period it left, which
# is the R2 attribution rule broken on the one row type that can now reach it.
# The reconcile keys by ``(period, entry date)``, so the move reverses the old
# key and posts the new one in one balanced pair, exactly as a revert-and-move
# already does for a settled row.
#
# The other PATCH fields (``notes`` / ``name`` / ``due_date`` /
# ``is_envelope`` / the visibility flags) move none of
# those, so a metadata-only edit raises no reconcile.  The reconcile is
# idempotent, so listing a field that did not move the effect is a harmless
# no-op; this set is the cheap pre-filter that avoids a ledger round-trip on a
# pure metadata edit.
_POSTING_RELEVANT_FIELDS = frozenset({
    "status_id", "estimated_amount", "settled_amount", "category_id",
    "settled_on", "pay_period_id",
})

# The PATCH fields the status seam writes, so the generic ``setattr`` loop in
# :func:`_apply_regular_update` must NOT.  They are one fact -- a row is settled
# if and only if it carries the day its money moved -- and
# ``status_seam.apply_status_change`` writes them in a single statement after
# verifying the transition and expiring the ``status`` relationship.  A bare
# ``setattr`` would skip all of that, and for ``settled_on`` it would make the
# loop a SECOND writer of the column the seam is the single door to: exactly
# finding **N-183** (which ``update_transfer`` shipped and X-f1b closed), about
# to be re-run on the transaction side by this step's own edit door (finding
# **N-185**).  Naming the pair here is what keeps the loop from growing a third
# member silently.
#: The fields the STATUS SEAM owns, excluded from the field-application loop
#: so it cannot become a second writer of any of them.  ``settled_amount``
#: joined at plan step X-au-c3: it states WHAT MOVED, so a bare ``setattr``
#: could book money on a row that never settled and could do it beside a basis
#: saying something else.  It reaches the record only through
#: ``apply_requested_status``, which hands it to the settle verb or refuses it
#: with a designed 400 when no settle is happening.
_SEAM_OWNED_FIELDS = frozenset(
    {"status_id", "settled_on", "settled_amount"}
)


def _apply_field_updates(txn, data):
    """Write the submitted fields onto *txn*, refusing what may not be written.

    The FIELD half of :func:`_apply_regular_update`, extracted so the handler
    keeps one exit per concern rather than growing a branch per rule.  FOUR
    acts, and the order is load-bearing.  **This enumeration said "three" and
    listed three until plan step X-au-j**, by which time the body already
    performed four: an adversarial review found the count stale in the one
    helper whose whole discipline is that its order matters.  The one it had
    never named is marked NEW below.

    1. the ``setattr`` loop.  ``status_id`` and ``settled_on`` are BOTH excluded
       and routed through the status verb instead: a bare ``setattr`` would
       assign the column but skip the transition check, the settle-day
       stamp/clear, and the status-relationship expire that
       ``status_seam.apply_status_change`` owns -- and for ``settled_on`` it
       would make this loop a SECOND writer of a column the seam is the single
       door to (finding **N-185**, the re-run of N-183 on the transaction side).
    2. ``is_override``, which sits with the field writes and ABOVE both the
       refusal below and the status work.  Two separate reasons, and both are
       load-bearing.  The settle asks the projection for a fresher amount and
       SKIPS a row the user has overridden
       (``income_service.live_projected_net``), so setting the flag afterwards
       would let a salary row's recompute overwrite the estimate the same form
       just submitted.  And act 3 FLUSHES (see below), so a flag written after
       it is written after the UPDATE it belongs in -- which for a period move
       leaves the row inside the generation index's partial predicate
       (``is_override = FALSE``) while its period is already the new one.
       **Plan step R17 narrowed what that can collide with but did not remove
       it**: the dated index is keyed
       ``(template, scenario, occurs_on)``, and a move does not touch
       ``occurs_on``, so a DATED row no longer collides with whatever the
       engine put in the target period.  An UNDATED template-linked row
       (``occurs_on IS NULL``) is still keyed on its paycheck by
       ``idx_transactions_template_scenario_undated``, so for that row the
       ordering is exactly as load-bearing as it was.
    2b. **(NEW to this list, shipped at X-au-c2b)** ``amount_source_id = None``
       whenever a figure is typed: a hand-priced row OWNS its figure, and
       ``ck_transactions_amount_ownership`` pairs the two one-to-one, so
       writing the column while a relation still claimed the row is an
       ``IntegrityError``.
    3. the derived-amount refusal, asked AFTER the loop so ``tracks_purchases``
       reads the RESULTING row: unchecking "Track individual purchases" in the
       same save legitimately gives the row its own amount back.  **It reads a
       LAZY relationship and therefore autoflushes**, which is why this whole
       helper is called from inside the caller's exception net rather than
       above it.

    **The refusal is ruling R-FF's**, the same sentence the reconcile panel
    already obeys ("a tick is correctable exactly when the settle verb takes its
    MANUAL branch"), applied to the second door that offers an amount box.  An
    envelope carrying purchases settles at ``sum(entries)``, so a figure typed
    beside it is not a correction the app can honour: it would be written here,
    overwritten by the settle, and never mentioned.  The popover no longer
    renders the input for such a row (``amount_correctable``), so this is the
    crafted-request and stale-form backstop.  Only a REAL figure is refused: an
    empty box loads as an explicit ``None`` (the field is ``allow_none``), which
    states no amount at all.

    A refused call has already staged the ``setattr`` loop's mutations;
    ``_error_transaction_response`` rolls them back, exactly as the settle-day
    refusal in the caller already relies on.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when every field was
        written.
    """
    for field, value in data.items():
        if field in _SEAM_OWNED_FIELDS:
            continue
        setattr(txn, field, value)

    if "estimated_amount" in data:
        # A typed figure makes the row's amount its OWN, so the relation that
        # priced it is CLEARED in the same act (plan step X-au-c2b).
        # ``ck_transactions_amount_ownership`` pairs the two -- a row states
        # either a figure or the relation that prices it, never both -- so
        # writing the column while a relation still claimed the row is an
        # ``IntegrityError``.  It is a no-op on today's data, because nothing
        # is declared derived yet, and it is written now because the amount
        # model's own dispatch already ASSERTS it: "a row a human RE-PRICED
        # owns its figure because the write door CLEARS its source".  That was
        # true at one write door of three when an adversarial review counted
        # them.
        txn.amount_source_id = None

    if txn.template_id and ("estimated_amount" in data or "pay_period_id" in data):
        txn.is_override = True

    if (
        data.get("settled_amount") is not None
        and transaction_service.settles_from_entries(txn)
    ):
        return _error_transaction_response(
            txn.id,
            "This row's actual comes from the purchases recorded against it, "
            "so an amount typed here would be discarded. Record the purchase "
            "instead, or correct one that is already there.",
        )
    return None


def _apply_regular_update(txn, txn_id, data):
    """Apply a PATCH update to a regular (non-shadow) transaction.

    Runs the three pre-mutation gates, writes the submitted fields
    (:func:`_apply_field_updates`), applies the requested status through
    ``transaction_service.apply_requested_status``, deletes the auto-generated
    payback when the change reverts a Credit row (mirroring ``unmark_credit``
    via the shared ``credit_workflow.delete_payback_on_credit_revert``), and
    commits under the optimistic lock.  A ``pay_period_id`` change relocates the
    row across the grid, so it triggers a full ``gridRefresh`` instead of the
    in-place ``balanceChanged`` swap.

    **This handler does not know what a status change means, and that is plan
    step X-ap.**  It used to call the status SEAM -- the mechanics primitive --
    so picking Paid in the popover's Status dropdown flipped an envelope-tracked
    row into the settled band without ever consulting its purchases: a `$25`
    purchase against a `$400` estimate booked `$400` here and `$25` through the
    grid's own Mark Paid button, from two controls in the same card (finding
    **N-219**).

    Args:
        txn: The Transaction being edited.
        txn_id: The transaction's id, used for stale-conflict logging.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A Flask response tuple: the updated cell + ``gridRefresh`` (on a
        period move) or ``balanceChanged`` on success, a 409 conflict
        cell on a concurrent commit, or a 400 on a rejected status
        change, a locked-field edit of a finalised row (#26), the income
        purchase-tracking guard, an amount the settle would discard, or a bad
        FK.
    """
    # The three pre-mutation gates share ONE error exit, the shape
    # ``transfers.update_transfer`` already uses, so each refusal this arc adds
    # does not push the handler past pylint's too-many-returns threshold.
    # ``or`` preserves the precedence the guards depend on: an illegal status
    # change reports its own message BEFORE the finalised-field lock speaks, and
    # each returns ``None`` when it passes.
    #
    # Gate 1, ``_resolve_status_change``: the state-machine transition check,
    # run before any column is mutated so a rejection leaves the row untouched.
    # Gate 2, the finalised-row edit lock (#26): a Paid/Received/
    # Credit/Cancelled row's money/period/category/due-date fields cannot be
    # rewritten unless this same request reverts it to Projected.
    # Gate 3, purchase tracking is expense-only.
    gate_error = (
        _resolve_status_change(txn, data)
        or _finalised_edit_response(txn, data)
        or _reject_tracking_on_income(txn, data)
        # A payback's figure is not its own to state (N-252); see the gate.
        or _reject_typed_payback_figure(txn, data)
    )
    if gate_error is not None:
        return gate_error

    # Detect a period move before the setattr loop mutates the row.  A
    # move relocates the row to a different period in the grid, which an
    # in-place cell swap (hx-target="#txn-cell-<id>") cannot express --
    # the cell would re-render in its old position.  When the period
    # actually changes the response triggers a full grid refresh (see
    # the ``HX-Trigger`` selection at the end of the handler), matching
    # the ``gridRefresh`` pattern carry-forward uses for cross-period
    # moves.
    period_changed = (
        "pay_period_id" in data and data["pay_period_id"] != txn.pay_period_id
    )

    # Detect a Credit reversion before the setattr loop rewrites
    # status_id.  A Credit row leaving Credit status (the state machine
    # only admits Credit -> Projected besides identity) must delete its
    # auto-generated payback exactly like unmark_credit -- otherwise the
    # PATCH path orphans the payback and inflates the next period's
    # projected expenses.  An identity re-submit (Credit -> Credit)
    # keeps the payback.
    reverts_credit = (
        "status_id" in data
        and is_credit(txn)
        and data["status_id"] != ref_cache.status_id(StatusEnum.CREDIT)
    )

    # Apply the status and the settle day through the ONE status verb, in ONE
    # call, because they are ONE fact: a row is settled if and only if it
    # carries the day its money moved, and the seam underneath writes both in
    # the same statement.  The status is the SUBMITTED one when the PATCH
    # carried one, else the row's own -- a day-only edit is an identity
    # transition, exactly as the transfer side's
    # :func:`transfer_service._status.apply_settle_day_to_pair` does it.
    # ``settle_day_for_status`` drops a day submitted alongside a revert out of
    # the settled band (ruling **R-EG**), so the documented unlock path (set
    # Status to Projected to edit the amounts) is not broken by the form
    # re-submitting the day the row already carried.
    #
    # **This route does NOT decide what applying a status MEANS** --
    # ``transaction_service.apply_requested_status`` does, and it reconciles the
    # ledger as part of the act.  Calling the seam here instead is finding
    # **N-219**: the seam is the MECHANICS primitive, so a door that reaches for
    # it settles a row without ever asking what the row is worth.
    #
    # ``_resolve_status_change`` already pre-verified a SUBMITTED transition for
    # error precedence, and the identity transition a day-only edit performs is
    # always legal (``state_machine``'s module docstring) -- so the TRANSITION
    # arm never raises here for a recognised status.  The DAY arm can: ruling
    # **R-EJ** refuses a settle day that has not happened yet, and that is
    # ordinary user input from the correction box.  A day-only PATCH also
    # reaches the seam with NO prior ``verify_transition`` (that gate returns
    # early when no status was submitted), so a corrupt ``status_id`` surfaces
    # here too.  ``_error_transaction_response`` rolls the session back, which
    # discards the ``setattr`` loop's already-staged mutations above.
    #
    # ``settle_day_for_status`` itself can refuse too (ruling **R-EL** bounds a
    # forwarded day below at the schedule's start), so it shares the same
    # ``except``: both are ordinary input from the correction box, and both must
    # render as a designed 400 with the staged ``setattr`` mutations rolled back.
    new_status_id = data.get("status_id", txn.status_id)

    # **The three excepts cover the WHOLE tail, and they were split by the order
    # the phases were written in rather than by a decision** -- the same
    # unification :func:`_mark_done_regular` records, forced here by the same
    # cause: the status verb now reconciles the ledger, so one call raises both
    # the designed ``ValidationError`` (a 400) and the concurrency
    # ``StaleDataError`` (a 409) and cannot be split across two nets.  Nothing
    # moves the other way: ``PostingError`` is a SIBLING of ``ValidationError``
    # under ``ShekelError`` rather than a subclass, so a broken ledger invariant
    # still fails loud instead of rendering as a designed refusal, and
    # ``credit_workflow.delete_payback_on_credit_revert`` raises neither (its
    # three ``ValidationError`` siblings are in ``mark_as_credit`` /
    # ``unmark_credit``, which this path does not call).
    try:
        # Write the submitted fields, flag a template row as overridden, and
        # refuse an amount the settle would discard -- three acts whose ORDER is
        # load-bearing and is documented at the helper.  Extracted so this
        # handler keeps one exit per concern rather than one per rule.
        #
        # **INSIDE the net, because it FLUSHES.**  Its derived-amount guard asks
        # ``settles_from_entries``, which lazy-loads ``template`` (or
        # ``entries``) and so autoflushes the ``setattr`` loop's staged
        # mutations as the version-pinned UPDATE.  Left above the ``try`` that
        # was the request's FIRST flush sitting outside its own exception net: a
        # concurrent commit surfaced as a 500 instead of the designed 409, and a
        # period move whose ``is_override`` had not yet been written tripped
        # the generation index (then keyed on the paycheck, now
        # ``idx_transactions_template_scenario_undated`` for an undated row)
        # as an uncaught ``IntegrityError``.  Found by adversarial review; the comment below
        # claimed the three excepts covered the whole tail, and they covered the
        # tail while the first flush had moved above it.
        field_error = _apply_field_updates(txn, data)
        if field_error is not None:
            return field_error
        # ``recorded`` is what makes the reading ECHO-AWARE (plan step X-az):
        # this form prefills the settle-day box, so an untouched Save re-submits
        # the day the row already carries -- and without the stored pair the
        # rule would restamp that day's BASIS as the owner's own typing, which
        # is finding **N-332**'s own laundering arriving through the edit door.
        settle_day = status_seam.settle_day_for_status(
            current_user.id, new_status_id, data.get("settled_on"),
            recorded_settle_day(txn),
        )
        # **A submitted FIGURE is a third reason to enter the status arm**, and
        # without it the door never saw one (found by two independent
        # adversarial reviews, 2026-08-17).  ``apply_requested_status`` decides
        # what a figure MEANS -- a correction on a row already settled, a
        # dropped echo on the way out of the band, a refusal for a figure the
        # user CHANGED beside a revert -- but this dispatch reached it only when
        # a STATUS or a DAY arrived too, so a PATCH carrying ``settled_amount``
        # alone answered 200 having discarded it.  ``new_status_id`` defaults to
        # the row's CURRENT status (above), so such a request is an identity
        # move.  **The route no longer grades the figure itself** (2026-08-18):
        # it called ``settled_amount_for_status``, which read the STATUS alone
        # and so could not tell an untouched prefill from a number the user had
        # just retyped.  That rule needs the row, and the door has it.
        submitted_figure = data.get("settled_amount")
        if (
            "status_id" in data
            or settle_day is not None
            or submitted_figure is not None
        ):
            transaction_service.apply_requested_status(
                txn, new_status_id, settle_day=settle_day,
                submitted=submitted_figure,
            )
        elif _POSTING_RELEVANT_FIELDS & data.keys():
            # Posting ledger reconcile (Build-Order Step 3) for the edit that
            # moves a posted effect WITHOUT touching the status: a re-category,
            # a corrected amount.  The status arm above owns the reconcile for
            # every other case, which is what keeps this request to exactly ONE
            # ledger round-trip -- ``status_id`` and ``settled_on`` are both in
            # ``_POSTING_RELEVANT_FIELDS``, so an ungated call here would be a
            # second reconcile of the same row on every status change.  Placed
            # LAST -- NOT at the field write -- so it reads the FINAL amount and
            # category, the exact discipline
            # ``transfer_service.update_transfer`` documents (the 2.8b HIGH: a
            # settle-and-recategorize PATCH applies category_id after status_id,
            # so posting at the flip would book the stale category).  The
            # reconcile reads the OLD category's posted legs back from the
            # ledger by transaction_id, so a revert-and-recategorize reverses
            # the old category cleanly even though ``txn.category_id`` already
            # points at the new one (the 2.8 CRITICAL).  Gated so a notes-only
            # edit posts nothing.  Inside the StaleDataError net for the same
            # reason as the payback delete below: the reconcile's flush
            # autoflushes the version-pinned row, so a concurrent commit
            # surfaces here as a 409, not a 500.
            posting_service.sync_transaction_postings(
                txn, settled=txn.status.is_settled,
            )
        if reverts_credit:
            # Inside the StaleDataError net deliberately: the payback
            # lookup autoflushes the already-dirtied row (the
            # version-pinned UPDATE), so a concurrent commit surfaces
            # here as StaleDataError and must yield the 409 conflict
            # cell, not a 500.  The helper does not commit -- the
            # deletion joins this request's commit so the status flip
            # and the payback removal land atomically.
            credit_workflow.delete_payback_on_credit_revert(
                txn, current_user.id,
            )
        db.session.commit()
    except ValidationError as exc:
        return _error_transaction_response(txn_id, str(exc))
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError:
        return _error_transaction_response(txn_id, _INVALID_REFERENCE_MSG)
    logger.info("user_id=%d updated transaction %d", current_user.id, txn_id)

    # A period move needs a full grid refresh so the row appears under
    # its new period; an in-place edit only needs the balance rows
    # recomputed.  ``gridRefresh`` reloads the page (app.js); the
    # returned cell still swaps first, which is harmless before reload.
    response = render_transaction_cell(txn)
    return response, 200, {
        "HX-Trigger": "gridRefresh" if period_changed else "balanceChanged",
    }


@transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
@login_required
@require_owner
def update_transaction(txn_id):
    """Update a transaction's fields (inline edit save).

    Shadow transactions (transfer_id IS NOT NULL) are routed through
    the transfer service so both shadows and the parent transfer stay
    in sync (design doc invariants 3-5).

    Returns the updated cell fragment.  Sends an HX-Trigger header
    to refresh the balance row.

    Optimistic locking (commit C-18 / F-010) operates in two layers:

      1. Stale-form check: the cell ships ``version_id`` as a hidden
         input set to ``Transaction.version_id`` at render time.
         When the submitted value differs from the row's current
         counter, the handler short-circuits with a 409 + conflict
         cell partial and records nothing.  This catches the
         sequential Tab-1/Tab-2 race documented in C-17.

      2. SQLAlchemy ``version_id_col``: any concurrent flush that
         races past the stale-form check is still narrowed by
         ``WHERE version_id = ?`` at the database tier; the loser
         raises ``StaleDataError`` which the handler converts into
         the same 409 + conflict cell.  The two layers together
         close every interleaving the optimistic-lock contract is
         meant to cover.

    Route-boundary FK ownership (commit C-29 / F-029 of the
    2026-04-15 security remediation plan): when the schema accepts
    a user-scoped FK -- ``pay_period_id`` or ``category_id`` -- the
    submitted id is verified against ``current_user.id`` here,
    before any state-changing work runs.  Without this probe an
    authenticated owner could submit another user's
    ``pay_period_id`` or ``category_id`` and the unfiltered
    ``setattr`` loop would silently re-parent the transaction into
    the victim's namespace (the FK row exists, the FK constraint
    passes, and PostgreSQL never raises ``IntegrityError``).
    ``status_id`` is a reference table FK (not user-scoped) and so
    does not need an ownership check.  The probe runs before the
    transfer-shadow branch so a malicious request that targets a
    transfer shadow with a cross-user FK is rejected even though
    the transfer-shadow path drops ``pay_period_id`` silently --
    matching the layered defense ``transfers.update_transfer``
    received in commit C-27.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Parse and validate input.
    errors = _update_schema.validate(request.form)
    if errors:
        # Designed fragment (marker-header convention): the cell
        # re-rendered with the flattened field errors in its hint,
        # so a rejected card Save is visible instead of silently
        # dropped by the app-wide htmx config.
        return _error_transaction_response(
            txn.id, flatten_schema_errors(errors), status=422,
        )

    data = _update_schema.load(request.form)

    # Route-boundary FK ownership (commit C-29 / F-029).  Reject
    # cross-user ``pay_period_id`` / ``category_id`` before the
    # stale-form check or the transfer-shadow branch so the
    # security response (404) takes precedence over the UX
    # response (409 conflict cell) when the same request triggers
    # both.  See :func:`_verify_owned_fks_in_update` for the
    # threat-model details.
    fk_error = _verify_owned_fks_in_update(data)
    if fk_error is not None:
        return fk_error

    # Stale-form check.  Performed before any mutation so audit-log
    # triggers record only successful edits.  Conditional on the
    # form having submitted a version (clients that omit it fall
    # through to the SQLAlchemy-tier check at flush time).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != txn.version_id:
        logger.info(
            "Stale-form conflict on update_transaction id=%d "
            "(submitted=%d, current=%d)",
            txn_id, submitted_version, txn.version_id,
        )
        return render_transaction_cell(txn, conflict=True), 409

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _apply_shadow_update(txn, txn_id, data)
    # --- End guard ---

    return _apply_regular_update(txn, txn_id, data)


@transactions_bp.route("/transactions/<int:txn_id>", methods=["DELETE"])
@login_required
@require_owner
def delete_transaction(txn_id):
    """Remove a transaction from the books, soft or hard.

    **The HTTP shape only.**  What a delete MEANS -- which matches it
    withdraws, which payback it takes down, which postings it reverses and
    whether the row leaves the table at all -- is
    :func:`transaction_service.delete_transaction`, the verb this route and
    ``statement_match``'s undo now share (plan step ``bank_import:X-gb``).
    The sequence was spelled here and again there, and each of its four steps
    has an ORDER that is load-bearing.

    **This door has a SURFACE since X-gb** -- the delete control on the
    full-edit action card -- and finding **N-344** is that it did not: the
    route existed with full teardown logic and a census of every template and
    script found zero callers, so the 46 envelope shells one YTD statement pass
    minted could not be removed from the app at all.

    Responds with ``gridRefresh`` rather than ``balanceChanged`` because the
    row LEAVES the grid, which an in-place cell swap cannot express -- the same
    reason ``cancel_transaction`` and ``unmark_credit`` keep it.  The body is
    empty: there is no cell left to render.

    Refusals (``deletion_refusal``) come back as the designed error fragment
    the card's other controls use, so a crafted request or a stale card is told
    why rather than swapping a bare string.

    Optimistic locking (commit C-18 / F-010): the soft-delete UPDATE and the
    hard-delete DELETE are both version-pinned by SQLAlchemy.  A concurrent
    commit that bumps the row's version raises ``StaleDataError``, which the
    handler converts to a 409 + conflict cell so the user can retry against
    fresh state.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    try:
        outcome = transaction_service.delete_transaction(txn, current_user.id)
        db.session.commit()
    except ValidationError as exc:
        return _error_transaction_response(txn_id, str(exc))
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info(
        "user_id=%d deleted transaction %d (soft=%s, matches_withdrawn=%d)",
        current_user.id, txn_id, outcome.soft, outcome.withdrawn.matches,
    )
    return "", 200, {"HX-Trigger": "gridRefresh"}


def _mark_done_regular(txn, txn_id, submitted, target):
    """Settle a regular (non-shadow) transaction.

    **The rule this used to hold is now a SERVICE verb** --
    :func:`transaction_service.settle_transaction`, ruling **R-FA** -- because
    plan step X-f2-c2 gives the reconcile panel's tick the same settle, and
    two doors restating one money rule is this arc's own root cause 1.  What
    is left here is the HTTP shape: which surface renders, and which of the
    three designed responses a failure takes.

    **The three excepts now cover the WHOLE settle, and what that changes was
    MEASURED rather than argued.**  This handler used to run the
    amount-and-status phase under an ``except ValidationError`` and the
    reconcile-and-commit phase under ``except StaleDataError / IntegrityError``
    -- a split that came from the order the phases were written in, not from a
    decision, and one no caller of a single verb could be expected to
    reproduce.  It is unified here because an argument a caller can get wrong
    is a defect rather than a contract, and X-f2-c2's reconcile writer calls
    the same verb in a loop.

    **It fixes no live defect, and the first draft of this paragraph claimed it
    did.**  Reconstructing the pre-change route from git and running the new
    control against it shows the same 409 both ways.  The reason is not visible
    by reading the phases: the FIRST flush of a settle is
    ``txn.status.is_settled``, the argument to the posting reconcile, because
    ``apply_status_change`` both dirties the row and expires ``status``, so
    reading it back refreshes and autoflushes.  That expression is inside the
    net under either topology, and everything before it reads columns or
    already-loaded relationships.  Recorded at
    ``TestTransactionStaleFormPrevention.test_mark_done_catches_a_stale_settle_as_409``.

    Nothing moves the other way either: the reconcile raises ``PostingError``,
    a SIBLING of ``ValidationError`` under ``ShekelError`` rather than a
    subclass, so a broken ledger invariant still fails loud instead of
    rendering as a designed refusal -- and no module in the reconcile's import
    closure raises ``ValidationError`` at all (measured across
    ``posting_service``, ``posting_reads``, ``_posting_write``,
    ``ledger_account_service`` and ``user_write_lock``).

    Args:
        txn: The Transaction being settled.
        txn_id: The transaction's id, for stale-conflict logging.
        submitted: The figure a human typed for what moved.  ``None`` does NOT
            mean "leave the record alone": it means nobody typed one, and the
            settle then RECORDS what it resolved on the ``derived`` basis.  The
            verb ignores it for an envelope-tracked row with entries, whose
            entries ARE the record of what it cost.
        target: The :class:`_RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the success surface on commit, a 409
        conflict surface on a concurrent commit, or a 400 on a bad FK or
        a rejected transition.
    """
    try:
        transaction_service.settle_transaction(
            txn, submitted=submitted,
        )
        db.session.commit()
    except ValidationError as exc:
        # The envelope branch's preconditions, and the illegal-transition case
        # a stale surface can still reach (e.g. a Mark Paid tap on a card
        # another device just cancelled) -- the designed fragment shows
        # current state plus the reason (grid audit D2, ruled 2026-07-11).
        # Audit reference: F-047 / F-161 follow-up to commit C-21.
        return _error_transaction_response(txn_id, str(exc), target)
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id, target)
    except IntegrityError:
        return _error_transaction_response(
            txn_id, _INVALID_REFERENCE_MSG, target,
        )
    # The status the row LANDED in, read off the row rather than from a value
    # the caller computed: the verb owns the income/expense pick now
    # (``transaction_service.settled_status_id``), and logging a requested id
    # beside a row that took a different one is how a log stops being
    # evidence.
    logger.info(
        "user_id=%d marked transaction %d status_id=%d",
        current_user.id, txn_id, txn.status_id,
    )

    return _mark_done_success_response(txn, target)


@transactions_bp.route("/transactions/<int:txn_id>/mark-done", methods=["POST"])
@login_required
def mark_done(txn_id):
    """Set a transaction's status to 'done' (expenses) or 'received' (income).

    Shadow transactions route through the transfer service so both
    shadows and the parent transfer are updated atomically.

    Automatically picks the correct status based on transaction type.
    For entry-capable transactions with entries, the settle records the
    ``purchases`` basis and the entries state the figure.  For all others, it
    accepts an optional ``settled_amount`` from the form -- parsed via
    :class:`MarkDoneSchema` so a malformed numeric value returns a
    clean 422 with the Marshmallow per-field message instead of the
    legacy ``"Invalid actual amount"`` translation, and a negative
    value is rejected at the schema tier (commit C-27 / F-042 /
    F-162 of the 2026-04-15 security remediation plan).  422 (not 400)
    is the validation-error status the entry routes and
    ``coding-standards.md`` mandate (DH-#81).

    Optimistic locking (commit C-18 / F-010): the button-click path
    has no form-side ``version_id`` to compare, so the optimistic
    lock relies on SQLAlchemy's ``version_id_col`` race detection
    at flush time.  ``StaleDataError`` is converted to a 409 +
    conflict cell so the user retries against fresh state instead
    of seeing a 500.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # Rendering surface for the response.  The mobile / companion card
    # action bar posts ``render=mobile_card`` plus the per-tab
    # ``card_prefix`` and the ``can_edit`` flag so the response is a
    # single re-rendered card (in-place swap, no reload); the desktop
    # grid and full-edit popover omit these, so the response defaults
    # to the cell + gridRefresh reload.  Read off ``request.form``
    # directly -- these are render-routing fields, not part of the
    # money-only ``MarkDoneSchema``.  Resolved BEFORE schema validation
    # so the 422 below can render the correct surface too.
    render_mode = request.form.get("render", "")
    card_prefix = request.form.get("card_prefix", "")
    card_can_edit = request.form.get("can_edit") == "1"
    target = _RenderTarget(render_mode, card_prefix, card_can_edit)

    # Validate the optional ``settled_amount`` form field once,
    # before branching on transfer detection, so both code paths
    # apply identical validation.  ``MarkDoneSchema`` strips empty
    # strings via its pre_load hook so the missing-field UX (a
    # button click with no body) yields it absent from the loaded
    # dict -- which means "nobody typed a figure", and the settle
    # then records what it resolved rather than leaving the row
    # unrecorded (plan step X-au-c3).
    try:
        mark_done_data = _mark_done_schema.load(request.form)
    except MarshmallowValidationError as exc:
        # Designed fragment: the requesting surface (cell or mobile
        # card) re-rendered with the per-field message in its hint /
        # banner instead of a JSON body the client silently drops.
        return _error_transaction_response(
            txn.id, flatten_schema_errors(exc.messages), target, status=422,
        )
    submitted = mark_done_data.get("settled_amount")

    # The income/expense status pick is NOT made here.  It used to be, and
    # ``transaction_service.settle_from_entries`` re-derived the same id from
    # the same predicate with a comment saying it "mirrors" this line -- two
    # spellings of one rule that agreed by reading.  It is now
    # ``transaction_service.settled_status_id``, inside the verb, and the
    # shadow branch below never wanted it: ``transfer_service`` sets Paid on
    # both legs, because the split is meaningless for a pair whose whole point
    # is that one leg is each.

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _mark_done_shadow(txn, txn_id, submitted, target)
    # --- End guard ---

    return _mark_done_regular(txn, txn_id, submitted, target)


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@login_required
@require_owner
def mark_credit(txn_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense.

    Optimistic locking (commit C-18 / F-010):
    ``StaleDataError`` -> 409 conflict cell.

    TOCTOU duplicate-payback prevention (commit C-19 / F-008):
    ``credit_workflow.mark_as_credit`` acquires
    ``SELECT ... FOR NO KEY UPDATE`` on the source row to serialise
    concurrent requests; the partial unique index
    ``uq_transactions_credit_payback_unique`` backstops any future
    caller that bypasses the lock, and the IntegrityError catch
    below converts the violation into idempotent success.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot mark a transfer shadow as credit.", 400

    try:
        credit_workflow.mark_as_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(exc, txn_id)
    except (NotFoundError, ValidationError) as exc:
        return _error_transaction_response(txn_id, str(exc))
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@login_required
@require_owner
def unmark_credit(txn_id):
    """Revert credit status and delete the auto-generated payback.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection guard: credit is not applicable to transfers ---
    if txn.transfer_id is not None:
        return "Cannot unmark credit on a transfer shadow.", 400

    try:
        credit_workflow.unmark_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on unmark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Raised when the bespoke source-state guard or the
        # state-machine verification in
        # ``credit_workflow.unmark_credit`` rejects the request --
        # e.g. attempting to unmark a Paid row.  The fragment names
        # the offending status so the user understands why.
        return _error_transaction_response(txn_id, str(exc))
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/cancel", methods=["POST"])
@login_required
@require_owner
def cancel_transaction(txn_id):
    """Set a transaction's status to 'cancelled'.

    Shadow transactions route through the transfer service to cancel
    the parent transfer and both shadows atomically.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

    # --- Transfer detection guard ---
    if txn.transfer_id is not None:
        return _cancel_shadow(txn, txn_id, cancelled_id)
    # --- End guard ---

    # Route the cancel through the ONE status verb (state-machine check +
    # status_id + settled_on + the ledger reconcile).  Cancelled is reachable
    # only from Projected (or the Cancelled identity edge for idempotent
    # re-submits); a direct done -> cancelled or settled -> cancelled would
    # erase the paid/archived audit trail and raises ValidationError -> 400.
    # Cancelled is non-settled, so the seam leaves settled_on clear.  Audit
    # reference: F-047 / F-161 follow-up to commit C-21.
    #
    # The reconcile inside the verb is what this handler used to run itself:
    # reconcile to the new status's settled sense as the final step, mirroring
    # the transfer pattern (reconcile on every status change).  Cancelled is
    # non-settled and is reachable only from Projected, so it is an idempotent
    # no-op today (a Projected source has no postings to reverse), but it keeps
    # the "every status change reconciles last" invariant complete and
    # self-heals if the state machine ever admits cancelling a settled row.
    # Inside the StaleDataError net: the reconcile's flush autoflushes the
    # version-pinned row, so a concurrent commit surfaces here as a 409, not a
    # 500.
    try:
        transaction_service.apply_requested_status(txn, cancelled_id)
        db.session.commit()
    except ValidationError as exc:
        return _error_transaction_response(txn_id, str(exc))
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info("user_id=%d cancelled transaction %d", current_user.id, txn_id)

    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}
