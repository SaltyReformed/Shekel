"""
Shekel Budget App -- Transfer Service: the UPDATE verb

The one path that changes an existing transfer, and with it both shadow
:class:`~app.models.transaction.Transaction` rows.  Transfer Invariants 3-5
are maintained here: shadow amounts, statuses and periods always equal the
parent's, because every field a caller may move is mirrored in this module and
nowhere else.

Its tail is the posting reconcile (:func:`_reconcile_postings_after_update`),
which brings the double-entry ledger back in step after the kwargs land -- once
the FINAL amount and status are in place rather than at the field that moved.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging
from decimal import Decimal, InvalidOperation

from app.exceptions import ValidationError
from app.extensions import db
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import account_posting_service
from app.services import posting_service
from app.services.transfer_service._loan_posting import (
    _reject_installment_move_before_loan,
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._ownership import (
    _get_owned_category,
    _get_owned_period,
)
from app.services.transfer_service._status import (
    apply_settle_day_correction,
    apply_status_to_all_three,
)
from app.services.transfer_service._validation import (
    _get_shadow_transactions,
    _get_transfer_or_raise,
    _validate_positive_amount,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)

# The ``update_transfer`` kwargs whose change can alter a transfer's posted
# double-entry ledger effect or its attribution, so a change to any of them
# triggers a posting reconcile (Build-Order Step 2; see
# ``posting_service.sync_transfer_postings``).  ``status_id`` flips the
# settled/unsettled target; ``amount`` (the estimated amount) and
# ``actual_amount`` together determine the settled shadow's ``effective_amount``
# (``COALESCE(actual_amount, estimated_amount)``) -- the magnitude posted;
# ``pay_period_id`` moves the entry's period, so a settled period move
# reconciles R2-correctly (the per-(account, period) reconcile reverses the old
# period and posts the new) AND fires the effect-time self-heal for the Step-5
# account-anchor corrections (F1).
#
# ``due_date`` IS here, and its inclusion is load-bearing: on a LOAN payment the
# due date is the installment the payment satisfies, which the genesis write walk
# (``loan_ledger.merge_anchor_and_payment_events``) orders
# payments by AND applies its strict ``anchor_date < due_date`` post-anchor
# boundary against -- so moving it changes which payments an anchor SUBSUMES, and
# therefore the POSTED balance.  Editing it without a reconcile would leave the
# posted ledger disagreeing with every live reader (the history rows, the payment
# table, the resolver's replay), silently, until an unrelated chokepoint happened
# to fire.  On a NON-loan transfer the cash reconcile is reconcile-to-target and
# writes nothing, so listing it costs one idempotent no-op round-trip.
#
# The remaining kwargs (``category_id`` / ``name`` / ``notes`` / ``is_override``)
# move none of these, so they raise no reconcile.  ``settled_on`` is deliberately
# NOT here: it moves no leg AMOUNT, and an unsettled transfer has no postings to
# re-date, so the set stays the cheap always-on pre-filter.  A SETTLED
# ``settled_on`` edit IS posting-relevant since step E1a -- it moves the day every
# posting counts from (the ``entry_date``, step C2's one clock) -- and
# ``_reconcile_postings_after_update`` runs the full reconcile for that case
# explicitly (the per-(period, date) reconcile re-dates the entries, finding
# N-13) plus the two endpoint accounts' anchor-correction resync (F1).  The
# reconcile is idempotent, so listing a field that did not move the effect is a
# harmless no-op; this set is the cheap pre-filter that avoids a ledger
# round-trip on a pure metadata edit.
_POSTING_RELEVANT_FIELDS = frozenset(
    {"status_id", "amount", "actual_amount", "pay_period_id", "due_date"}
)


def _apply_actual_amount(
    expense_shadow: Transaction, income_shadow: Transaction, raw: object
) -> None:
    """Mirror an ``actual_amount`` update onto both shadow transactions.

    The ``Transfer`` model has no ``actual_amount`` column, so this kwarg
    updates the two shadows directly.  ``None`` clears the settled
    amount; any other value is coerced to ``Decimal`` (a parse failure
    is a caller bug -> ``ValidationError``).

    Args:
        expense_shadow: The expense-side shadow :class:`Transaction`.
        income_shadow: The income-side shadow :class:`Transaction`.
        raw: The submitted actual amount (``None`` or Decimal-coercible).

    Raises:
        ValidationError: If *raw* is not None and cannot be parsed as a
            Decimal.
    """
    if raw is not None:
        try:
            actual = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError(
                f"Invalid actual_amount: {raw!r}."
            ) from exc
    else:
        actual = None
    expense_shadow.actual_amount = actual
    income_shadow.actual_amount = actual


def _reconcile_postings_after_update(xfer: Transfer, updates: dict[str, object]) -> None:
    """Bring the posting ledger back in step after an ``update_transfer`` edit.

    Extracted from :func:`update_transfer` (which was at its branch/statement
    budget) so the reconcile tail is one cohesive step.  Runs after every kwarg
    is applied and the session is flushed:

    * **Step-2 cash reconcile** when a magnitude / settled-sense / period field
      changed (``_POSTING_RELEVANT_FIELDS``).  Placed here -- NOT inside
      ``apply_status_to_all_three`` -- because ``actual_amount`` is applied AFTER
      ``status_id`` and the grid shadow-edit path can settle and set an actual
      in one call; the reconcile reads the income shadow's ``effective_amount``,
      so it must run once everything is in place or it would post the pre-edit
      estimate.  ``xfer.status_id`` is the post-update status, so its
      ``is_settled`` is the correct target sense.  Idempotent
      reconcile-to-target: a settle posts the effect, a revert / cancel reverses
      to zero, an unchanged effect writes nothing.
    * **Loan-payment genesis reconcile** last (a no-op for a non-loan transfer):
      a settle / revert / amount / actual / period edit of a loan payment
      re-reconciles that loan's confirmed-payment splits (coupled on the running
      balance) and its opening / true-up anchor corrections.
    * **Full reconcile on a settled ``settled_on`` edit too (E1a / N-13)**: a
      ``settled_on`` change moves the day every posting counts from (its
      ``entry_date``, step C2's one clock) without changing any leg amount, so
      the per-period reconcile used to write nothing and the entries kept
      their stale dates.  The reconcile is per-(period, DATE) now: the
      old-dated entries reverse at their own date, the effect re-posts at the
      new settle date, and the loan sync's checked-projection assert verifies
      the result -- so the fold and the ledger cannot disagree about WHEN.
    * **Step-5 account-anchor resync on a settled ``settled_on`` edit (F1)**:
      resync the two endpoint accounts' anchor corrections so a settled
      ``settled_on`` move cannot strand a stale anchor correction (their
      reconcile is anchor-walk-derived, not delta-keyed off this transfer).
      Only for a SETTLED transfer (a projected one posts nothing); a no-op for
      a loan endpoint (the account walk skips amortizing accounts).
      ``pay_period_id`` needs no such branch -- it is in
      ``_POSTING_RELEVANT_FIELDS``, so a period move reconciles R2-correctly
      and self-heals via the cash reconcile above.  Fires on ANY settled
      ``settled_on`` edit, not only a pure one: on the common settle path (status
      + ``settled_on`` together) the reconcile's tail self-heal already covers
      both endpoints, so these two idempotent walks are redundant there -- an
      accepted, safe cost.  It is deliberately NOT narrowed to
      ``not needs_reconcile``, because a future COMBINED edit (e.g. ``amount``
      + ``settled_on``) could move the attribution in a way the delta-keyed
      self-heal does not cover; an always-correct resync is the point of this
      seam.

    Args:
        xfer: The updated, flushed :class:`Transfer`.
        updates: The ``update_transfer`` kwargs that were applied.
    """
    needs_reconcile = bool(_POSTING_RELEVANT_FIELDS & updates.keys())
    settled_on_edited = "settled_on" in updates
    if not (needs_reconcile or settled_on_edited):
        return
    current_status = db.session.get(Status, xfer.status_id)
    # A settled ``settled_on`` edit moves the day the event counts from (step
    # C2's one clock), which since step E1a IS a posting-relevant change: the
    # per-(period, date) reconcile reverses the stale-dated entry and re-posts
    # at the new settle date (finding N-13), and the loan sync's
    # checked-projection assert then verifies the ledger against the walk.
    if needs_reconcile or (settled_on_edited and current_status.is_settled):
        posting_service.sync_transfer_postings(
            xfer, settled=current_status.is_settled,
        )
        _sync_loan_postings_if_loan(xfer)
    if settled_on_edited and current_status.is_settled:
        account_posting_service.sync_account_anchor_postings(
            xfer.from_account_id, xfer.scenario_id,
        )
        account_posting_service.sync_account_anchor_postings(
            xfer.to_account_id, xfer.scenario_id,
        )


def update_transfer(transfer_id, user_id, **kwargs):
    """Update a transfer and propagate changes to shadow transactions.

    Enforces invariants 3-5: shadow amounts, statuses, and periods
    always match the parent transfer.

    Accepted kwargs:
        amount         -- New transfer amount (positive Decimal).
        status_id      -- New status for transfer and both shadows.
        pay_period_id  -- New period for transfer and both shadows.
        category_id    -- New category (expense shadow only).
        name           -- New display name (transfer only, not shadows).
        notes          -- New notes (transfer only, not shadows).
        actual_amount  -- Actual settled amount (both shadows only;
                          the Transfer model has no actual_amount
                          column).
        due_date       -- Due date for the transfer and both shadows
                          (Date or None).
        settled_on     -- The civil day the money moved, for both shadows
                          (DateTime or None).
        is_override    -- Override flag (transfer and both shadows).

    Any other kwargs are silently ignored (consistent with the
    BaseSchema EXCLUDE pattern).

    Args:
        transfer_id: The primary key of the transfer to update.
        user_id:     The expected owner (defense-in-depth).
        **kwargs:    The fields to update; see "Accepted kwargs" above.
                     Any key not listed there is silently ignored.

    Returns:
        The updated Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If validation fails (non-positive amount,
            wrong period owner, data integrity issues).
    """
    xfer = _get_transfer_or_raise(transfer_id, user_id)
    expense_shadow, income_shadow = _get_shadow_transactions(transfer_id)

    # R-C: refuse an edit that would move a loan payment before its loan, before
    # any field is applied.  See :func:`_reject_installment_move_before_loan`.
    _reject_installment_move_before_loan(xfer, user_id, kwargs)

    # ── amount ─────────────────────────────────────────────────────
    if "amount" in kwargs:
        new_amount = _validate_positive_amount(kwargs["amount"])
        xfer.amount = new_amount
        expense_shadow.estimated_amount = new_amount
        income_shadow.estimated_amount = new_amount

    # ── status_id ──────────────────────────────────────────────────
    # All three transitions verified before any propagation, then applied
    # through the ONE status seam, which owns the F-048 defense-in-depth
    # ``settled_on`` synchronization and the ``status`` expire; see
    # :func:`app.services.transfer_service._status.apply_status_to_all_three`
    # for the full audit rationale.  An
    # explicit ``settled_on`` in this same call is applied by its own branch
    # below and wins over whatever the seam derived here.
    if "status_id" in kwargs:
        apply_status_to_all_three(
            xfer, expense_shadow, income_shadow, kwargs["status_id"],
        )

    # ── pay_period_id ──────────────────────────────────────────────
    if "pay_period_id" in kwargs:
        new_period_id = kwargs["pay_period_id"]
        _get_owned_period(new_period_id, user_id)
        xfer.pay_period_id = new_period_id
        expense_shadow.pay_period_id = new_period_id
        income_shadow.pay_period_id = new_period_id

    # ── category_id ────────────────────────────────────────────────
    # Category updates apply to both shadows so the transaction
    # appears under the user-selected category in both account grids.
    if "category_id" in kwargs:
        new_cat_id = kwargs["category_id"]
        if new_cat_id is not None:
            _get_owned_category(new_cat_id, user_id)
        xfer.category_id = new_cat_id
        expense_shadow.category_id = new_cat_id
        income_shadow.category_id = new_cat_id

    # ── name ───────────────────────────────────────────────────────
    # Name is display metadata on the transfer only.  Shadow names
    # are derived from account names and do not change here.
    if "name" in kwargs:
        xfer.name = kwargs["name"]

    # ── notes ──────────────────────────────────────────────────────
    # Notes live on the transfer only; shadow transactions do not
    # carry independent notes.
    if "notes" in kwargs:
        xfer.notes = kwargs["notes"]

    # ── actual_amount ──────────────────────────────────────────────
    if "actual_amount" in kwargs:
        _apply_actual_amount(
            expense_shadow, income_shadow, kwargs["actual_amount"]
        )

    # ── due_date ──────────────────────────────────────────────────
    # The parent transfer is canonical; mirror to both shadows so the
    # three rows stay equal (Transfer Invariant 3).
    if "due_date" in kwargs:
        new_due = kwargs["due_date"]
        xfer.due_date = new_due
        expense_shadow.due_date = new_due
        income_shadow.due_date = new_due

    # ── settled_on ────────────────────────────────────────────────
    # The ONE caller that legitimately supplies a day is the user CORRECTING
    # it (ruling R-ED).  Both mark-done routes used to pass one and did not
    # mean it: their value overrode the seam's preserve rule and re-dated a
    # replayed settle (finding N-178, plan step X-f1b0).  It assigned the
    # column on both shadows here until plan step X-f1b, which made that a
    # second write door for the column the seam owns (finding N-183).
    if "settled_on" in kwargs:
        apply_settle_day_correction(
            xfer, expense_shadow, income_shadow, kwargs["settled_on"],
        )

    # ── is_override ────────────────────────────────────────────────
    if "is_override" in kwargs:
        flag = bool(kwargs["is_override"])
        xfer.is_override = flag
        expense_shadow.is_override = flag
        income_shadow.is_override = flag

    db.session.flush()

    _reconcile_postings_after_update(xfer, kwargs)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_UPDATED, BUSINESS,
        "Transfer updated",
        user_id=user_id,
        transfer_id=transfer_id,
        # Sorting the field list keeps the structured log deterministic
        # so dashboards can group by ``fields_changed`` without spurious
        # cardinality from kwarg ordering.
        fields_changed=sorted(kwargs.keys()),
    )
    return xfer
