"""
Shekel Budget App -- Transfer Service

The single point of enforcement for all transfer mutations.  Every code
path that creates, updates, or deletes a transfer MUST go through this
service.  Direct ORM manipulation of budget.transfers is forbidden
outside this module and the transfer recurrence engine (which delegates
to this service for the final insert step).

The service enforces the five core invariants (design doc section 4.5):

  1. Every transfer has exactly two linked shadow transactions
     (one expense, one income).
  2. Shadow transactions are never orphaned.
  3. Shadow amounts always equal the transfer amount.
  4. Shadow statuses always equal the transfer status.
  5. Shadow periods always equal the transfer period.

Architecture:
  - No Flask imports.  Receives plain data, returns ORM objects or
    raises exceptions.
  - All monetary arithmetic uses Decimal.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.account import Account
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError
from app.services import posting_service
from app.services._transfer_loan_posting import (
    _resync_loan_payment_postings_after_delete,
    _reverse_loan_payment_before_delete,
    _sync_loan_payment_postings_if_loan,
)
from app.services._transfer_ownership import (
    _get_owned_account,
    _get_owned_category,
    _get_owned_period,
    _get_owned_scenario,
    _get_owned_transfer_template,
)
from app.services.state_machine import verify_transition
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_CREATED,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT,
    EVT_TRANSFER_RESTORED,
    EVT_TRANSFER_SOFT_DELETED,
    EVT_TRANSFER_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)

# The ``update_transfer`` kwargs whose change can alter a transfer's posted
# double-entry ledger effect, so a change to any of them triggers a posting
# reconcile (Build-Order Step 2; see ``posting_service.sync_transfer_postings``).
# ``status_id`` flips the settled/unsettled target; ``amount`` (the estimated
# amount) and ``actual_amount`` together determine the settled shadow's
# ``effective_amount`` (``COALESCE(actual_amount, estimated_amount)``) -- the
# magnitude posted.  This set guards the posted MAGNITUDE and SETTLED-SENSE
# only.  The other kwargs (``pay_period_id`` / ``category_id`` / ``name`` /
# ``notes`` / ``due_date`` / ``is_override`` / ``paid_at``) move neither, so
# they raise no reconcile -- and a settled transfer's period / settle-date
# (which the journal entry denormalises) cannot move anyway: the
# finalised-edit lock blocks editing a settled transfer's period / due-date,
# and carry-forward and recurrence touch only Projected transfers.  (An
# amount-based reconcile could not re-stamp a stale period/date regardless --
# a future settled-period move would have to re-stamp the entry, not reconcile
# it.)  The reconcile is idempotent, so listing a field that did not move the
# effect is a harmless no-op; this set is the cheap pre-filter that avoids a
# ledger round-trip on a pure metadata edit.
_POSTING_RELEVANT_FIELDS = frozenset({"status_id", "amount", "actual_amount"})


# ── Private helpers ────────────────────────────────────────────────


def _validate_positive_amount(amount):
    """Ensure *amount* is a positive Decimal.

    Args:
        amount: The transfer amount (Decimal, int, float, or string).

    Returns:
        The validated amount as a Decimal.

    Raises:
        ValidationError: If amount is zero, negative, or not numeric.
    """
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid amount: {amount!r}.  Must be a positive number."
        ) from exc
    if amount <= 0:
        raise ValidationError(
            "Transfer amount must be positive."
        )
    return amount


def _get_transfer_or_raise(transfer_id, user_id, allow_deleted=False):
    """Load a Transfer and verify ownership and active status.

    Args:
        transfer_id:   The primary key.
        user_id:       The expected owner.
        allow_deleted: If False (default), soft-deleted transfers are
                       treated as non-existent and raise NotFoundError.
                       Set to True for operations that legitimately need
                       to act on deleted transfers (e.g. delete_transfer
                       for idempotent soft-delete, restore_transfer).

    Returns:
        The Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist, belongs to
            another user, or is soft-deleted (when allow_deleted is
            False).  The message is identical in all cases (security
            response rule -- do not reveal existence to wrong user).
    """
    xfer = db.session.get(Transfer, transfer_id)
    if xfer is None or xfer.user_id != user_id:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    # Soft-deleted transfers are invisible to normal operations.
    # Without this check, update_transfer on a deleted transfer would
    # cascade into a misleading "0 shadow transactions" error from
    # _get_shadow_transactions (the shadows are also deleted).
    if not allow_deleted and xfer.is_deleted:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    return xfer


def _get_shadow_transactions(transfer_id):
    """Load shadow transactions for a transfer and identify types.

    Returns:
        Tuple (expense_shadow, income_shadow).

    Raises:
        ValidationError: If the shadow count is not exactly 2 or if
            both shadows have the same transaction type (data
            integrity violation).
    """
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id, is_deleted=False)
        .all()
    )

    if len(shadows) != 2:
        # Differentiate between a soft-deleted transfer (expected state,
        # not corruption) and a genuinely corrupt transfer missing
        # shadows (unexpected state).  _get_transfer_or_raise blocks
        # soft-deleted transfers by default, so this path should only
        # fire for real corruption -- but defense-in-depth means we
        # check anyway to produce a helpful diagnostic.
        xfer = db.session.get(Transfer, transfer_id)
        is_soft_deleted = xfer is not None and xfer.is_deleted

        shadow_ids = [s.id for s in shadows]
        if is_soft_deleted and len(shadows) == 0:
            logger.warning(
                "Transfer %d is soft-deleted.  Its shadow transactions "
                "are also soft-deleted and excluded from active queries.  "
                "This is expected, not data corruption.",
                transfer_id,
            )
            raise ValidationError(
                f"Transfer {transfer_id} is soft-deleted and cannot be "
                f"modified.  Use restore_transfer to reactivate it first."
            )

        # Genuine data integrity violation: transfer is active but has
        # the wrong number of shadows.  Fail-fast.
        logger.error(
            "Transfer %d has %d active shadow transactions (expected 2).  "
            "Shadow IDs: %s.  This indicates data corruption.",
            transfer_id, len(shadows), shadow_ids,
        )
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions instead of the expected 2.  "
            f"Data integrity issue -- cannot proceed."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    expense_shadow = None
    income_shadow = None
    for s in shadows:
        if s.transaction_type_id == expense_type_id:
            expense_shadow = s
        elif s.transaction_type_id == income_type_id:
            income_shadow = s

    if expense_shadow is None or income_shadow is None:
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Data integrity issue."
        )

    return expense_shadow, income_shadow


def _build_shadow(
    xfer: Transfer, account_id: int, name: str, transaction_type_id: int
) -> Transaction:
    """Construct one shadow ``Transaction`` mirroring the parent transfer.

    Both shadows are transfer-generated (``template_id=None``,
    ``credit_payback_for_id=None``, no independent ``notes``) and inherit
    period / scenario / status / category / amount / due_date from the
    just-created ``xfer`` so the three rows stay equal (Transfer
    Invariants 1 and 3).  Only the per-side fields vary.

    Args:
        xfer: The parent :class:`Transfer`, already flushed so
            ``xfer.id`` is set (the shadow's ``transfer_id`` FK).
        account_id: The account this shadow lives in (``from_account``
            for the expense side, ``to_account`` for the income side).
        name: The shadow's display name.
        transaction_type_id: ``ref.transaction_types.id`` for the side
            (expense or income).

    Returns:
        An unsaved :class:`Transaction`; the caller adds it to the
        session.
    """
    return Transaction(
        account_id=account_id,
        template_id=None,       # Shadows are transfer-generated, not template-generated.
        transfer_id=xfer.id,
        pay_period_id=xfer.pay_period_id,
        scenario_id=xfer.scenario_id,
        status_id=xfer.status_id,
        name=name,
        category_id=xfer.category_id,
        transaction_type_id=transaction_type_id,
        estimated_amount=xfer.amount,
        actual_amount=None,
        is_override=False,
        is_deleted=False,
        credit_payback_for_id=None,
        notes=None,
        due_date=xfer.due_date,
    )


# ── Public API ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TransferSpec:  # pylint: disable=too-many-instance-attributes
    """The canonical inputs for creating a transfer.

    Bundles the twelve fields :func:`create_transfer` needs into one
    cohesive value object so the sole transfer-creation path takes a
    single argument rather than a twelve-field signature.  Every field
    is read by ``create_transfer`` and supplied together by every caller
    (the new-transfer route, the recurrence engine, the materialize path)
    -- this is one "transfer to create" request, mirroring the columns
    of the ``Transfer`` row it produces.

    Pylint: ``too-many-instance-attributes`` (12/7) -- these are the
    irreducible inputs of one creation request, read as a flat unit by
    the single consumer; there is NO cohesive sub-group to nest, so
    splitting would fragment one concept for no gain.  Mirrors the
    ``AmortizationRow`` / ``PayoffScenarios`` precedent.  Frozen so a
    constructed spec is an immutable record of one request.

    Attributes:
        user_id: Owner of the transfer.
        from_account_id: Account money leaves (expense side).
        to_account_id: Account money enters (income side).
        pay_period_id: Pay period for the transfer.
        scenario_id: Budget scenario.
        amount: Transfer amount (positive Decimal).
        status_id: Initial status (typically 'projected').
        category_id: Optional spending category mirrored to both
            shadows.  May be None.
        notes: Optional notes on the transfer (not mirrored to shadows).
        transfer_template_id: Optional link to the generating transfer
            template (for recurrence).
        name: Optional display name.  If None, generated from the
            account names.
        due_date: Optional due date stored on the transfer and mirrored
            to both shadow transactions.
    """

    user_id: int
    from_account_id: int
    to_account_id: int
    pay_period_id: int
    scenario_id: int
    amount: Decimal
    status_id: int
    category_id: int | None
    notes: str | None = None
    transfer_template_id: int | None = None
    name: str | None = None
    due_date: date | None = None


def create_transfer(spec: TransferSpec) -> Transfer:
    """Create a transfer and its two shadow transactions atomically.

    This is the ONLY code path that should create rows in
    budget.transfers.  It enforces invariants 1-5 from design doc
    section 4.5.

    Args:
        spec: The :class:`TransferSpec` carrying the owner, endpoints,
            placement (period/scenario), amount, status, category, and
            optional metadata (notes/name/template link/due date) for
            the transfer to create.

    Returns:
        The created Transfer object (shadows accessible via
        transfer.shadow_transactions backref).

    Raises:
        ValidationError: If amount is non-positive, accounts are the
            same, or any business rule is violated.
        NotFoundError: If any referenced entity does not exist or
            does not belong to user_id.
    """
    # ── Validate inputs ────────────────────────────────────────────
    amount = _validate_positive_amount(spec.amount)

    if spec.from_account_id == spec.to_account_id:
        raise ValidationError(
            "Source and destination accounts must be different."
        )

    from_account = _get_owned_account(
        spec.from_account_id, spec.user_id, label="Source account"
    )
    to_account = _get_owned_account(
        spec.to_account_id, spec.user_id, label="Destination account"
    )
    _get_owned_period(spec.pay_period_id, spec.user_id)
    _get_owned_scenario(spec.scenario_id, spec.user_id)
    _get_owned_category(spec.category_id, spec.user_id)
    _get_owned_transfer_template(spec.transfer_template_id, spec.user_id)

    # ── Ref data lookups ───────────────────────────────────────────
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    # ── Determine names ────────────────────────────────────────────
    transfer_name = spec.name or f"{from_account.name} to {to_account.name}"
    expense_shadow_name = f"Transfer to {to_account.name}"
    income_shadow_name = f"Transfer from {from_account.name}"

    # ── Create transfer record ─────────────────────────────────────
    xfer = Transfer(
        user_id=spec.user_id,
        from_account_id=spec.from_account_id,
        to_account_id=spec.to_account_id,
        pay_period_id=spec.pay_period_id,
        scenario_id=spec.scenario_id,
        status_id=spec.status_id,
        transfer_template_id=spec.transfer_template_id,
        name=transfer_name,
        amount=amount,
        category_id=spec.category_id,
        notes=spec.notes,
        due_date=spec.due_date,
        is_override=False,
        is_deleted=False,
    )
    db.session.add(xfer)
    # Flush to get transfer.id -- required before creating shadows
    # that reference it via transfer_id FK.
    db.session.flush()

    # ── Create the two shadows (expense from_account, income to_account) ──
    expense_shadow = _build_shadow(
        xfer, spec.from_account_id, expense_shadow_name, expense_type_id
    )
    db.session.add(expense_shadow)
    income_shadow = _build_shadow(
        xfer, spec.to_account_id, income_shadow_name, income_type_id
    )
    db.session.add(income_shadow)
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_TRANSFER_CREATED, BUSINESS,
        "Transfer created with shadow transactions",
        user_id=spec.user_id,
        transfer_id=xfer.id,
        from_account_id=spec.from_account_id,
        to_account_id=spec.to_account_id,
        pay_period_id=spec.pay_period_id,
        scenario_id=spec.scenario_id,
        amount=str(amount),
        status_id=spec.status_id,
        category_id=spec.category_id,
        transfer_template_id=spec.transfer_template_id,
        expense_shadow_id=expense_shadow.id,
        income_shadow_id=income_shadow.id,
    )
    return xfer


def _apply_status_change(
    xfer: Transfer,
    expense_shadow: Transaction,
    income_shadow: Transaction,
    updates: dict[str, object],
) -> None:
    """Apply a ``status_id`` update to the transfer and both shadows.

    Verify the transition BEFORE any propagation so an illegal request
    (for example settled -> projected) leaves both the parent transfer
    and the two shadow transactions untouched.  The state machine raises
    ``ValidationError`` -- the route layer surfaces it as a 400.  Audit
    reference: F-047 / commit C-21 of the 2026-04-15 security
    remediation plan.

    Defense-in-depth ``paid_at`` synchronization (F-048 / C-22): the
    route layer (``transfers.mark_done``, ``transactions.mark_done``
    shadow path) is expected to pass an explicit ``paid_at`` whenever it
    sets a settled status, but a future caller that forgets is still
    forced into a coherent state here.  Two cases:

    * Transitioning to a settled status (``is_settled = TRUE``) without
      an explicit ``paid_at`` -> default to ``now()`` so
      ``Transaction.days_paid_before_due`` and the dashboard's "paid on
      time" indicator work.
    * Transitioning to a non-settled status without an explicit
      ``paid_at`` -> clear the existing timestamp so a Paid transfer
      reverted to Projected does not retain a stale payment time.

    Both branches no-op when the caller passed ``paid_at`` explicitly
    (including ``paid_at=None``); the explicit downstream assignment in
    :func:`update_transfer` then takes effect.

    Args:
        xfer: The parent :class:`Transfer` being updated.
        expense_shadow: The expense-side shadow :class:`Transaction`.
        income_shadow: The income-side shadow :class:`Transaction`.
        updates: The :func:`update_transfer` kwargs; read for
            ``status_id`` and probed for an explicit ``paid_at``.
    """
    new_status_id = updates["status_id"]
    verify_transition(xfer.status_id, new_status_id, context="transfer")
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id

    if "paid_at" not in updates:
        new_status = db.session.get(Status, new_status_id)
        if new_status is not None:
            if new_status.is_settled:
                settled_ts = db.func.now()
                expense_shadow.paid_at = settled_ts
                income_shadow.paid_at = settled_ts
            else:
                expense_shadow.paid_at = None
                income_shadow.paid_at = None


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
        paid_at        -- Payment timestamp for both shadows
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

    # ── amount ─────────────────────────────────────────────────────
    if "amount" in kwargs:
        new_amount = _validate_positive_amount(kwargs["amount"])
        xfer.amount = new_amount
        expense_shadow.estimated_amount = new_amount
        income_shadow.estimated_amount = new_amount

    # ── status_id ──────────────────────────────────────────────────
    # Transition verified before any propagation, with the F-048
    # defense-in-depth ``paid_at`` synchronization; see
    # :func:`_apply_status_change` for the full audit rationale.
    if "status_id" in kwargs:
        _apply_status_change(xfer, expense_shadow, income_shadow, kwargs)

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

    # ── paid_at ───────────────────────────────────────────────────
    if "paid_at" in kwargs:
        new_paid_at = kwargs["paid_at"]
        expense_shadow.paid_at = new_paid_at
        income_shadow.paid_at = new_paid_at

    # ── is_override ────────────────────────────────────────────────
    if "is_override" in kwargs:
        flag = bool(kwargs["is_override"])
        xfer.is_override = flag
        expense_shadow.is_override = flag
        income_shadow.is_override = flag

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # After every kwarg is applied, bring the double-entry posting ledger
    # back in step with the transfer's now-current settled effect.  Placed
    # here -- NOT inside ``_apply_status_change`` -- because ``actual_amount``
    # is applied AFTER ``status_id`` above, and the grid shadow-edit path can
    # settle and set an actual amount in one call; the reconcile reads the
    # income shadow's ``effective_amount``, so it must run once everything is
    # in place or it would post the pre-edit estimate.  ``xfer.status_id`` is
    # the post-update status (``_apply_status_change`` already wrote it, or it
    # is unchanged), so its ``is_settled`` is the correct target sense.
    # Idempotent reconcile-to-target: a settle posts the effect, a revert /
    # cancel reverses to zero, and an unchanged effect writes nothing.
    if _POSTING_RELEVANT_FIELDS & kwargs.keys():
        current_status = db.session.get(Status, xfer.status_id)
        posting_service.sync_transfer_postings(
            xfer, settled=current_status.is_settled,
        )
        # Build-Order Step 4: a settle / revert / amount / actual edit of a
        # loan payment re-splits that loan's confirmed payments (the principal
        # / interest / escrow split couples on the running balance).  Runs LAST
        # -- after the Step-2 cash entry is in step -- and is a no-op for a
        # non-loan transfer.
        _sync_loan_payment_postings_if_loan(xfer)

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


def delete_transfer(transfer_id, user_id, soft=False):
    """Delete a transfer and its shadow transactions.

    Args:
        transfer_id: The primary key of the transfer to delete.
        user_id:     The expected owner (defense-in-depth).
        soft:        If True, set is_deleted=True on the transfer and
                     both shadows (preserves records).  If False,
                     physically remove the transfer; the ON DELETE
                     CASCADE FK on transactions.transfer_id removes
                     both shadows automatically.

    Returns:
        The soft-deleted Transfer if soft=True, or None if hard-deleted.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
    """
    # allow_deleted=True so that idempotent soft-delete and hard-delete
    # of already-soft-deleted transfers continue to work.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # Reverse any posted effect BEFORE the row is removed, so a settled
    # transfer's ledger entry nets to zero.  Runs first -- while xfer.id and
    # the shadows still exist -- so the reversal entry can link ``transfer_id``
    # and read the shadow settle date; a hard delete then SET-NULLs the link,
    # leaving the immutable net-zero pair as history.  Idempotent no-op for a
    # never-settled or already-reversed transfer (the account-delete and
    # recurrence-regeneration paths only ever reach those: Guard 4 in
    # ``accounts/crud.py`` archives any account with settled history).
    posting_service.sync_transfer_postings(xfer, settled=False)

    # ── Loan-payment split reversal (Build-Order Step 4) ───────────
    # Reverse this payment's split correction while the income shadow id still
    # exists -- load-bearing for a hard delete, whose CASCADE SET-NULLs the
    # correction's ``transaction_id`` link.  Capture the loan coordinates now,
    # before the row can be deleted, so the downstream payments (whose running
    # balance the deletion changes) can be re-split afterwards.  A no-op for a
    # non-loan transfer.
    is_loan_payment = _reverse_loan_payment_before_delete(xfer)
    loan_account_id = xfer.to_account_id
    scenario_id = xfer.scenario_id

    if soft:
        xfer.is_deleted = True
        # Soft-delete must explicitly mark both shadows.  The database
        # CASCADE only fires on physical deletes, not flag changes.
        shadows = (
            db.session.query(Transaction)
            .filter_by(transfer_id=transfer_id)
            .all()
        )
        for shadow in shadows:
            shadow.is_deleted = True
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_TRANSFER_SOFT_DELETED, BUSINESS,
            "Transfer and shadows soft-deleted",
            user_id=user_id,
            transfer_id=transfer_id,
            shadow_count=len(shadows),
        )
        result = xfer
    else:
        # Hard delete -- rely on ON DELETE CASCADE to remove shadows.
        db.session.delete(xfer)
        db.session.flush()

        # Verify CASCADE removed the shadows.  If they still exist,
        # the FK was misconfigured in Task 2.
        orphan_count = (
            db.session.query(Transaction)
            .filter_by(transfer_id=transfer_id)
            .count()
        )
        if orphan_count > 0:
            logger.error(
                "CASCADE delete failed: %d orphaned shadow transactions "
                "remain for deleted transfer %d.",
                orphan_count, transfer_id,
            )

        log_event(
            logger, logging.INFO, EVT_TRANSFER_HARD_DELETED, BUSINESS,
            "Transfer hard-deleted (CASCADE)",
            user_id=user_id,
            transfer_id=transfer_id,
            orphan_count=orphan_count,
        )
        result = None

    # ── Downstream re-split (Build-Order Step 4) ───────────────────
    # After the payment is gone, re-split the LATER payments whose running
    # balance the deletion changed.  Idempotent and self-healing; skipped
    # entirely for a non-loan transfer.
    if is_loan_payment:
        _resync_loan_payment_postings_after_delete(loan_account_id, scenario_id)
    return result


def restore_transfer(transfer_id, user_id):
    """Restore a soft-deleted transfer and its shadow transactions.

    This is the inverse of ``delete_transfer(soft=True)``.  Sets
    ``is_deleted=False`` on the transfer and both shadows, then
    re-syncs every field the service mirrors from the canonical parent
    onto both shadows (amount, status, period, category, due_date,
    is_override) in case any drifted via direct ORM mutation while the
    transfer was soft-deleted.  ``actual_amount`` and ``paid_at`` are
    deliberately excluded: the ``Transfer`` parent has no canonical
    column for them (they live on the shadow ``Transaction`` only), so
    there is no parent value to re-sync against.

    Idempotent: calling on an already-active transfer is a no-op.

    Args:
        transfer_id: The primary key of the transfer to restore.
        user_id:     The expected owner (defense-in-depth).

    Returns:
        The restored (or already-active) Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If shadow transactions are missing or have
            an invalid type pairing, indicating data corruption that
            cannot be automatically repaired; or if either the source
            or destination account has been archived
            (``is_active = False``) since the transfer was soft-deleted
            (F-164).  Reactivate the account before restoring.
    """
    # Must allow deleted transfers since that is the expected input.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # Idempotent: if the transfer is already active, return unchanged.
    # Matches the idempotent pattern of delete_transfer(soft=True).
    if not xfer.is_deleted:
        logger.debug(
            "restore_transfer called on active transfer %d; no-op.",
            transfer_id,
        )
        return xfer

    xfer.is_deleted = False

    # Load ALL shadows without filtering by is_deleted -- they are
    # soft-deleted and that is exactly what we are undoing.  Same
    # query pattern as delete_transfer(soft=True).
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .all()
    )

    # ── Validate shadow count ───────────────────────────────────────
    if len(shadows) != 2:
        logger.error(
            "Cannot restore transfer %d: expected 2 shadow transactions, "
            "found %d.  Shadow IDs: %s.  Data integrity issue.",
            transfer_id, len(shadows), [s.id for s in shadows],
        )
        # Roll back the is_deleted change on the transfer since we
        # cannot restore it in a consistent state.
        xfer.is_deleted = True
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions (expected 2).  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    # ── Validate shadow type pairing ────────────────────────────────
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    type_ids = {s.transaction_type_id for s in shadows}
    if type_ids != {expense_type_id, income_type_id}:
        logger.error(
            "Cannot restore transfer %d: shadow type pairing is invalid.  "
            "Expected one expense and one income, found type_ids=%s.",
            transfer_id, type_ids,
        )
        xfer.is_deleted = True
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    # ── Refuse restore onto archived accounts (F-164) ───────────────
    # Account FK is RESTRICT (see ``models/transfer.py``) so the rows
    # cannot be hard-deleted while the transfer references them; the
    # only way they go away semantically is via ``is_active = False``.
    # Reactivating a transfer pointed at an archived account would
    # silently resurrect entries against an account the user has
    # withdrawn from active projections, producing balance drift the
    # user has no UI affordance to investigate.  Hard-fail instead and
    # require the user to reactivate the account first.
    from_account = db.session.get(Account, xfer.from_account_id)
    to_account = db.session.get(Account, xfer.to_account_id)
    from_active = bool(from_account is not None and from_account.is_active)
    to_active = bool(to_account is not None and to_account.is_active)
    if not (from_active and to_active):
        log_event(
            logger, logging.WARNING,
            EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT, BUSINESS,
            "Refused to restore transfer with archived account",
            user_id=user_id,
            transfer_id=transfer_id,
            from_account_id=xfer.from_account_id,
            to_account_id=xfer.to_account_id,
            from_account_active=from_active,
            to_account_active=to_active,
        )
        # Roll back the is_deleted flip applied at the top of the
        # function so the transfer stays soft-deleted on the caller's
        # rollback path.  Matches the rollback pattern used in the
        # shadow-count and shadow-type validation branches above.
        xfer.is_deleted = True
        raise ValidationError(
            "Cannot restore transfer: source or destination account "
            "is archived.  Reactivate the account before restoring."
        )

    # ── Restore shadows and verify invariants ───────────────────────
    for shadow in shadows:
        shadow.is_deleted = False

        # Invariant 3: shadow amount must match transfer amount.
        if shadow.estimated_amount != xfer.amount:
            logger.warning(
                "Correcting shadow %d estimated_amount drift: %s -> %s "
                "(transfer %d amount).",
                shadow.id, shadow.estimated_amount, xfer.amount,
                transfer_id,
            )
            shadow.estimated_amount = xfer.amount

        # Invariant 4: shadow status must match transfer status.
        if shadow.status_id != xfer.status_id:
            logger.warning(
                "Correcting shadow %d status_id drift: %s -> %s "
                "(transfer %d status).",
                shadow.id, shadow.status_id, xfer.status_id,
                transfer_id,
            )
            shadow.status_id = xfer.status_id

        # Invariant 5: shadow period must match transfer period.
        if shadow.pay_period_id != xfer.pay_period_id:
            logger.warning(
                "Correcting shadow %d pay_period_id drift: %s -> %s "
                "(transfer %d period).",
                shadow.id, shadow.pay_period_id, xfer.pay_period_id,
                transfer_id,
            )
            shadow.pay_period_id = xfer.pay_period_id

        # Mirrored field: shadow category must match transfer category.
        # create_transfer/_build_shadow and update_transfer mirror the
        # parent category to both shadows so each account grid attributes
        # the entry to the same user-selected category; a drifted shadow
        # would surface under the wrong category in one grid.
        if shadow.category_id != xfer.category_id:
            logger.warning(
                "Correcting shadow %d category_id drift: %s -> %s "
                "(transfer %d category).",
                shadow.id, shadow.category_id, xfer.category_id,
                transfer_id,
            )
            shadow.category_id = xfer.category_id

        # Mirrored field: shadow due_date must match transfer due_date.
        # The parent is canonical (see ``models/transfer.py`` due_date
        # docstring, "Transfer Invariant 3"); the calendar, dashboard,
        # year-end and spending-trend consumers read the SHADOW due_date,
        # so a drifted shadow would mis-compute days-until-due / paid-on-
        # time while the parent still shows the correct date.
        if shadow.due_date != xfer.due_date:
            logger.warning(
                "Correcting shadow %d due_date drift: %s -> %s "
                "(transfer %d due_date).",
                shadow.id, shadow.due_date, xfer.due_date,
                transfer_id,
            )
            shadow.due_date = xfer.due_date

        # Mirrored field: shadow is_override must match transfer
        # is_override.  update_transfer mirrors the override flag to both
        # shadows so the carry-forward/dedupe state stays coherent across
        # the three rows; a drifted shadow would diverge from the parent's
        # override status.
        if shadow.is_override != xfer.is_override:
            logger.warning(
                "Correcting shadow %d is_override drift: %s -> %s "
                "(transfer %d is_override).",
                shadow.id, shadow.is_override, xfer.is_override,
                transfer_id,
            )
            shadow.is_override = xfer.is_override

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # Re-post the confirmed effect when the restored transfer is settled: a
    # settled transfer that was soft-deleted had its effect reversed by
    # ``delete_transfer``, so restoring re-syncs the ledger to its current
    # status.  Runs AFTER the shadows are un-deleted above, so the income
    # shadow's effective amount is readable.  A no-op for a restored projected
    # transfer (the common path -- nothing was posted to restore).
    restored_status = db.session.get(Status, xfer.status_id)
    posting_service.sync_transfer_postings(
        xfer, settled=restored_status.is_settled,
    )
    # Build-Order Step 4: re-post the split correction for a restored, settled
    # loan payment (a no-op for a restored projected or non-loan transfer).
    _sync_loan_payment_postings_if_loan(xfer)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_RESTORED, BUSINESS,
        "Transfer restored from soft-delete",
        user_id=user_id,
        transfer_id=transfer_id,
        shadow_count=len(shadows),
    )
    return xfer
