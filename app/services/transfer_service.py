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
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import ValidationError
from app.services import account_posting_service
from app.services import posting_service
from app.services._transfer_loan_posting import (
    _reject_installment_move_before_loan,
    _reject_payment_before_origination,
    _reject_transfer_out_of_loan,
    _resync_loan_postings_after_delete,
    _reverse_loan_payment_before_delete,
    _sync_loan_postings_if_loan,
)
from app.services._transfer_ownership import (
    _get_owned_account,
    _get_owned_category,
    _get_owned_period,
    _get_owned_scenario,
    _get_owned_transfer_template,
)
from app.services._transfer_validation import (
    _get_shadow_transactions,
    _get_transfer_or_raise,
    _validate_positive_amount,
    assert_restorable,
)
from app.services import status_seam
from app.services.state_machine import verify_transition
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_CREATED,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_RESTORED,
    EVT_TRANSFER_SOFT_DELETED,
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
# ``_run_posting_reconciles`` runs the full reconcile for that case explicitly
# (the per-(period, date) reconcile re-dates the entries, finding N-13) plus the
# two endpoint accounts' anchor-correction resync (F1).  The reconcile is
# idempotent, so listing a field that did not move the effect is a harmless
# no-op; this set is the cheap pre-filter that avoids a ledger round-trip on a
# pure metadata edit.
_POSTING_RELEVANT_FIELDS = frozenset(
    {"status_id", "amount", "actual_amount", "pay_period_id", "due_date"}
)


# ── Private helpers ────────────────────────────────────────────────


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
        settled_on: Optional settle DAY for a transfer created ALREADY
            settled (plan step E1a): mirrored to both shadows exactly as
            the update path's explicit ``settled_on`` is, with the same
            default -- a born-settled transfer without one settled TODAY
            (the F-048 / C-22 rule, on the user's clock).  Meaningless for
            an unsettled status, so :func:`create_transfer` rejects that
            combination loudly rather than recording a settle day for a
            payment that has not happened.
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
    settled_on: date | None = None


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
    _reject_transfer_out_of_loan(from_account)
    _get_owned_period(spec.pay_period_id, spec.user_id)
    # R-C: a loan cannot receive a payment before it originates -- the fold
    # would erase it while the cash side still debits the funding account.
    # Deliberately AFTER ``_get_owned_period``: this guard reads that period's
    # ``start_date`` (the installment fallback), so running it first would read
    # an unowned row and answer a cross-user id with a 400 carrying a date from
    # it, where the ownership rule requires an indistinguishable 404.
    _reject_payment_before_origination(
        to_account, spec.pay_period_id, spec.due_date,
    )
    _get_owned_scenario(spec.scenario_id, spec.user_id)
    _get_owned_category(spec.category_id, spec.user_id)
    _get_owned_transfer_template(spec.transfer_template_id, spec.user_id)
    created_status = db.session.get(Status, spec.status_id)
    if spec.settled_on is not None and not (
        created_status is not None and created_status.is_settled
    ):
        raise ValidationError(
            "settled_on is the settle day of a transfer created already "
            "settled; a transfer created with an unsettled status has not "
            "been paid, so it cannot carry one."
        )

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

    # ── Born-settled coherence (plan step E1a) ─────────────────────
    # A transfer BORN settled used to book NO cash entry and carry no settle
    # day -- a settled effect the ledger never saw, which the
    # checked-projection assert refuses the moment the loan syncs.  So the
    # create chokepoint applies update_transfer's two settle rules:
    # ``settled_on`` is the caller's explicit day or the user's today (the
    # F-048 / C-22 defense -- a transfer created settled settled at creation),
    # and the posting reconcile runs (the cash entry + the loan genesis
    # reconcile).  ``created_status`` was loaded in the validation block, which
    # also rejects a ``settled_on`` on an unsettled create before any row
    # exists.  ``display_today()`` rather than the server's day: this value IS
    # the ``entry_date`` the postings below are filed under (step C2's one
    # clock), and that day is the user's (ruling R-DH (b)).
    if created_status is not None and created_status.is_settled:
        settled_day = (
            spec.settled_on if spec.settled_on is not None else display_today()
        )
        expense_shadow.settled_on = settled_day
        income_shadow.settled_on = settled_day
        db.session.flush()
        posting_service.sync_transfer_postings(xfer, settled=True)
        _sync_loan_postings_if_loan(xfer)

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


def _apply_status_to_all_three(
    xfer: Transfer,
    expense_shadow: Transaction,
    income_shadow: Transaction,
    new_status_id: int,
) -> None:
    """Move a transfer and both shadows to one status, through the ONE seam.

    Replaces this module's own copy of the status seam (plan step X-aj1,
    ruling **R-DN**); see :func:`app.services.status_seam.apply_status_change`
    for the mechanics and for the three defects the duplicate carried.

    **Verified in FULL before anything is assigned.**  That preserves the
    atomicity the deleted version promised -- an illegal request leaves the
    transfer AND both shadows untouched (F-047 / commit C-21) -- and it is
    what makes ruling **R-DO** safe here: a shadow whose status has drifted
    somewhere the parent's status is not legally reachable from now REFUSES,
    so assigning as we went would strand the transfer ahead of its shadows.
    The seam re-verifies as it writes and stays the enforcement point; this
    pass is for atomicity, mirroring how the transaction PATCH handler's
    ``_resolve_status_change`` pre-check relates to the same seam.

    The shadow checks pass by construction for any transfer whose own
    transition was legal: a shadow's status equals the parent's pre-update
    status (Transfer Invariant 4), and every transfer-legal move is also
    transaction-legal (measured over both maps at X-aj1's trace, 0
    exceptions, reverse control firing).

    Args:
        xfer: The parent :class:`Transfer` being updated.
        expense_shadow: The expense-side shadow :class:`Transaction`.
        income_shadow: The income-side shadow :class:`Transaction`.
        new_status_id: The ``ref.statuses.id`` all three rows move to.

    Raises:
        ValidationError: If the transition is illegal for the transfer or
            for either shadow (propagated from the state machine).
    """
    rows = (xfer, expense_shadow, income_shadow)
    for row in rows:
        verify_transition(row, new_status_id)

    # ONE settle DAY for the PAIR (Transfer Invariant 3), resolved before
    # either shadow is written.  The seam's per-row rule is "preserve an
    # existing day, else stamp today", which is right for a lone transaction and
    # WRONG for a pair: two shadows whose days already differ would each keep
    # their own, and a pair where only one carries a day would have the other
    # stamped with today.  Both outcomes break the equality
    # ``posting_service._entry_date`` depends on -- it reads the INCOME shadow's
    # ``settled_on`` and its docstring records that the two are always equal
    # because the transfer service mirrors the day to both shadows.
    # Preferring an EXISTING day over today is what stops a repair from
    # inventing a settle day: the sibling already knows when the money moved,
    # and that day is the ``entry_date`` the postings are filed under.
    settled = new_status_id in settled_status_ids()
    pair_day = None
    if settled:
        pair_day = (
            expense_shadow.settled_on
            or income_shadow.settled_on
            or display_today()
        )
    for shadow in (expense_shadow, income_shadow):
        status_seam.apply_status_change(
            shadow, new_status_id, settled_on=pair_day,
        )
    # The parent carries no ``settled_on`` column, so it takes no day.
    status_seam.apply_status_change(xfer, new_status_id)


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
      ``_apply_status_to_all_three`` -- because ``actual_amount`` is applied AFTER
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
    # :func:`_apply_status_to_all_three` for the full audit rationale.  An
    # explicit ``settled_on`` in this same call is applied by its own branch
    # below and wins over whatever the seam derived here.
    if "status_id" in kwargs:
        _apply_status_to_all_three(
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
    # replayed settle (finding N-178, plan step X-f1b0).
    if "settled_on" in kwargs:
        new_settled_on = kwargs["settled_on"]
        expense_shadow.settled_on = new_settled_on
        income_shadow.settled_on = new_settled_on

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

    # ── Downstream re-reconcile (posting ledger) ───────────────────
    # After the payment is gone, re-reconcile the loan's genesis ledger: the
    # LATER payments whose running balance the deletion changed AND any true-up
    # whose owed_before it moved.  Idempotent and self-healing; skipped entirely
    # for a non-loan transfer.
    if is_loan_payment:
        _resync_loan_postings_after_delete(loan_account_id, scenario_id)
    return result


def restore_transfer(transfer_id, user_id):
    """Restore a soft-deleted transfer and its shadow transactions.

    This is the inverse of ``delete_transfer(soft=True)``.  Sets
    ``is_deleted=False`` on the transfer and both shadows, then
    re-syncs every field the service mirrors from the canonical parent
    onto both shadows (amount, status, period, category, due_date,
    is_override) in case any drifted via direct ORM mutation while the
    transfer was soft-deleted.  ``actual_amount`` stays excluded: the
    ``Transfer`` parent has no canonical column for it, so there is no value
    to re-sync against.

    **``settled_on`` IS now maintained, and it is not the parent that supplies
    it** (plan step X-aj1).  Repairing a status through the one seam brings
    the seam's dating rule with it, so a shadow repaired INTO a settled
    status must carry a day and one repaired out of it must not.  The day
    comes from the SIBLING shadow -- Transfer Invariant 3 says the pair is
    equal, and the sibling already records when the money moved.  Taking it
    from there rather than from today is what stops a repair from inventing a
    settle day: since plan step E1a that civil day is the ``entry_date`` the
    re-posted entry below is filed under, so a fabricated day would move money
    on what is supposed to be a repair.

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

    # Load ALL shadows without filtering by is_deleted -- they are
    # soft-deleted and that is exactly what we are undoing.  Same
    # query pattern as delete_transfer(soft=True).
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id)
        .all()
    )

    # ── Refuse before anything moves (X-aj1) ────────────────────────
    # Shadow count, type pairing, archived endpoints (F-164) and -- new at
    # ruling R-DO -- a status drift the state machine cannot legally repair.
    # Run BEFORE the un-delete, which is a change from the code this replaced:
    # that version flipped ``is_deleted`` first and then hand-restored it on
    # each failing branch, so the rollback was written out three times and the
    # fourth check would have had to remember it too.
    assert_restorable(xfer, shadows, user_id)

    xfer.is_deleted = False

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

        # Invariant 4 is repaired for the PAIR after this loop, not per shadow
        # -- see the call to :func:`_apply_status_to_all_three` below.

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

    # ── Invariant 4: the PAIR's status, through the one seam ────────
    # Repaired for both shadows together rather than one at a time, and that
    # is load-bearing rather than tidy.  The seam's per-row timestamp rule
    # ("preserve an instant, else stamp now()") would let a repair INVENT a
    # settle day for a shadow that has none -- and since plan step E1a that day
    # is the ``entry_date`` the re-posted entry below is filed under, so the
    # repair would move money.  Going through the pair-aware applier makes the
    # SIBLING's recorded instant the answer, which is what Transfer Invariant 3
    # says it is.  The transfer itself is already at this status, so its own
    # transition is the identity and legal by construction; the shadows'
    # transitions were proved repairable by ``assert_restorable`` above, so
    # neither verification can raise here.
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    expense_shadow = next(
        s for s in shadows if s.transaction_type_id == expense_type_id
    )
    income_shadow = next(s for s in shadows if s is not expense_shadow)
    if any(shadow.status_id != xfer.status_id for shadow in shadows):
        logger.warning(
            "Correcting shadow status drift on transfer %d: %s -> %s.",
            transfer_id,
            {shadow.id: shadow.status_id for shadow in shadows},
            xfer.status_id,
        )
    _apply_status_to_all_three(
        xfer, expense_shadow, income_shadow, xfer.status_id,
    )

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
    # Posting ledger: re-reconcile the loan's genesis ledger for a restored,
    # settled loan payment -- its split correction plus the opening / true-up
    # corrections (a no-op for a restored projected or non-loan transfer).
    _sync_loan_postings_if_loan(xfer)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_RESTORED, BUSINESS,
        "Transfer restored from soft-delete",
        user_id=user_id,
        transfer_id=transfer_id,
        shadow_count=len(shadows),
    )
    return xfer
