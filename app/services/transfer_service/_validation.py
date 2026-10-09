"""
Shekel Budget App -- Transfer Service validate-and-load guards

The input-validation and entity-loading guards for
:mod:`app.services.transfer_service`: validate a submitted amount, refuse a
transfer OUT of a source no engine models one from (the composed loan-or-card
set both doors call), load the owned/active
:class:`~app.models.transfer.Transfer` with the live twin
:class:`~app.models.transaction.Transaction` rows its mirrors write, and
refuse a restore whose twins are not a pair.
Each is a precondition check the mutation entry points run before they touch
any row, raising the project's domain exceptions
(:class:`~app.exceptions.ValidationError` for a bad amount or a restore's
broken pair, :class:`~app.exceptions.NotFoundError` for a missing or
not-yours transfer -- with an identical message for both the "missing" and
the "not yours" case, the project security-response rule -- no existence
oracle).

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the Build-Order Step 2-4 posting-ledger wiring lands -- the
same split that moved the ownership loaders into ``_ownership`` and
the loan-posting glue into ``_loan_posting``.  :func:`assert_restorable`
joined them at plan step X-aj1 (ruling **R-DR**), bringing ``restore_transfer``'s
preconditions to the module whose single responsibility they already were.

These helpers plus :class:`TransferRows` and its one loader are a cohesive,
transfer-service-private cluster (single responsibility: validate inputs and
load the rows a mutation operates on).  They write no ``status_id``
and construct no ``Transaction`` -- so they stay clear of the W9907 status fence
that keeps the status-mirroring appliers in the parent module -- and they
compute no balance.
Flask-isolated like the parent service: plain data in, ORM objects out, no
``request`` / ``session`` imports.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cached_property

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError
from app.services.account_projection import is_revolving
from app.services.transfer_legs import TransferLeg, transfer_side_leg
from app.services.transfer_service._loan_posting import (
    _reject_transfer_out_of_loan,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT,
    log_event,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferRows:
    """A transfer and its live twin rows: what a mutation writes.

    Introduced at plan step X-f2-c3 as Transfer Invariant 1 as a TYPE -- a
    parent and exactly two shadow transactions, whose statuses and periods
    were kept equal -- because threading the three through the settle's
    signature put it over pylint's argument ceiling, and a call site that
    could pair one transfer with another's shadows could break the invariant
    by typo.  The same reasoning
    :class:`app.services.reconcile_service._rows.Statement` is built on.

    **Since plan step ``balance:X-bi-6-4d-2`` the twins are MIRRORS and
    nothing more**: a transfer's status is one column on the transfer, each
    side's day and figure are its payment record's (hung off the transfer by
    a side link), and no reader asks a twin for either.  What a twin still
    carries -- its pay period, category, due date, override flag, amount
    ownership, account and name -- is written by the arms of
    ``_update`` and ``_endpoints`` over :attr:`shadows`, until plan step
    ``X-bi-6-4d-3`` deletes the rows.  So the shape no longer insists on two:
    a transfer whose twin was deleted behind the app's back (no door does
    it) settles like any other, which the developer ruled (**R-BAL235**,
    2026-10-08, "Offer and settle it": the state stops existing when the
    twins are deleted in the same release), and the mirrors write the twins
    there are.

    Attributes:
        transfer: The parent :class:`~app.models.transfer.Transfer`.
        shadows: Its live twin :class:`~app.models.transaction.Transaction`
            rows, in id order -- two on every transfer a door built.
    """

    transfer: Transfer
    shadows: "tuple[Transaction, ...]"

    @cached_property
    def expense_leg(self) -> TransferLeg:
        """Return the from-side LEG with its record: what the pair already records.

        The settle and the update read what a pair RECORDS off its expense
        side -- the retained correction a re-settle honours, the record it
        carries forward, the figure an echoed Actual box is compared against
        -- and since leaf ``balance:X-bi-6-4d-1`` they read it here, through
        :func:`app.services.transfer_legs.transfer_side_leg`, rather than off
        the expense shadow's ``entries``: ``transfer_legs`` holds the one join
        ``X-bi-6-4d``'s re-parent moves, so this read moves with it.  The
        leg's status is its TRANSFER's.  It is read by SIDE, never by the transfer's
        current account, because an endpoint move earlier in the same act
        leaves ``from_account_id`` stale until a flush (see the producer).

        **Read ONCE per act and kept**, so the grade, the settle and the
        correction of one update ask one query between them: a producer asked
        twice in one request is two resolution points, which can come to
        disagree about WHEN they resolved.
        That is correct because every reader asks BEFORE the act's seam pass
        writes the record, and nothing earlier in the act writes a movement's
        figure or source -- an endpoint move carries the record to the new
        endpoint and may re-date it ``borrowed`` (ruling **R-BAL168**), and
        every reader of this value asks only for the record's figure, its
        source and its retained correction, none of which a move writes.  A
        reader AFTER the seam pass would not see the act's own write
        consistently (a first settle's new movement is absent from the cached
        value; a kept one is the same object, re-priced in place), so none
        may be added there.  **Its ``account_id`` is frozen at the read**, too,
        and an act may move an endpoint around that read: when a figure is
        graded the read precedes the move (``_update._grade_submitted_figure``
        runs ahead of ``_endpoints._apply_endpoint_move``) and names the OLD
        source after it; otherwise the first read is the settle's, after the
        move has flushed, and names the NEW source.  Either way nothing may
        read this leg's account: every
        reader takes its ``record``, and one that needs the side's account
        reads the transfer's.

        Returns:
            The :class:`~app.services.transfer_legs.TransferLeg` on the
            transfer's source account, its ``record`` the covering movement
            the expense side holds (dated, or kept un-dated across a revert),
            ``None`` when it holds none.
        """
        return transfer_side_leg(self.transfer, is_income=False)


def load_transfer_rows(transfer_id, user_id) -> TransferRows:
    """Load an owned, live transfer and the live twin rows its mirrors write.

    The ONE loader a mutation entry point calls.  It verified the twins as a
    pair -- exactly one live expense and one live income -- and refused a
    transfer whose pair was broken, until plan step ``balance:X-bi-6-4d-2``:
    nothing reads a twin's status, day or figure any more, so a broken pair
    is no reason to refuse a settle, and the developer ruled that it is not
    (**R-BAL235**, :class:`TransferRows`).  A restore still counts the twins
    (:func:`assert_restorable`), because it un-deletes them.

    Args:
        transfer_id: The primary key of the transfer to load.
        user_id: The expected owner (defense-in-depth).

    Returns:
        The :class:`TransferRows`.

    Raises:
        NotFoundError: If the transfer does not exist, belongs to another
            user, or is soft-deleted (:func:`_get_transfer_or_raise`).
    """
    xfer = _get_transfer_or_raise(transfer_id, user_id)
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id, is_deleted=False)
        .order_by(Transaction.id)
        .all()
    )
    return TransferRows(xfer, tuple(shadows))


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


def _reject_transfer_out_of_revolving(from_account: Account) -> None:
    """Reject a transfer whose SOURCE is a revolving credit line (a credit card).

    A transfer OUT of a card is a cash advance (into a cash account) or a
    balance transfer (into another card), and the card refuses both rather
    than modelling them (design ``docs/design/credit_card_from_scratch.md``
    3.8, plan step ``credit_card:CC-10``).  The card's whole model rests on
    money leaving it ONLY as a purchase -- a movement on the card (3.2) -- and
    a transfer touching it only as its PAYMENT, into it (3.5): the statement's
    grace test is graded on the credits INTO the card
    (:func:`app.services.card_statement.grace_kept`), and its finance charge
    prices one APR per date (``budget.rate_history`` through
    :func:`app.services.card_apr.apr_in_effect`) over the balance path (3.6).
    A cash advance carries its own APR, a fee and no grace, which one APR row
    per date and one grace rule cannot express, so admitting it would price
    every later statement of that card wrong -- the same reason a disbursement
    out of a loan is refused (:func:`._loan_posting._reject_transfer_out_of_loan`).

    **Asked at BOTH doors that can put a transfer on a source account**, which
    is the loan refusal's discipline: :func:`app.services.transfer_service.create_transfer`,
    and :func:`._endpoints._resolve_endpoints`, where an update MOVES a
    source.  Refusing at only the first would leave the second a way straight
    past it -- and neither door names this function.  Both call
    :func:`_reject_unmodeled_source`, the ONE set of source refusals, so a
    door cannot hold the loan's refusal and miss the card's.

    **It lives here, not beside the loan refusal in** :mod:`._loan_posting`,
    because that module's responsibility is the loan's genesis-ledger glue and
    a card has none -- it classifies PLAIN and rides the cash fold -- while
    this module's is exactly this: a precondition a mutation asks before it
    touches a row.  It reads :func:`app.services.account_projection.is_revolving`,
    the ONE predicate every card feature gates on (plan step CC-1, ruling
    ``credit_card:R-CC14``).

    What stays ALLOWED, and is pinned beside the refusals: a transfer INTO the
    card (its payment), a move of a transfer's DESTINATION onto a card, and
    direct income on the card at the transaction-create doors (a refund, a
    redemption), which refuse an amortizing loan only
    (:func:`app.routes.transactions.create._reject_transaction_on_loan`).

    Args:
        from_account: The transfer's source account (already ownership-checked,
            with its ``account_type`` loaded).

    Raises:
        ValidationError: When *from_account* is a credit card.
    """
    if is_revolving(from_account):
        raise ValidationError(
            f"Cannot transfer money out of a credit card: source account "
            f"'{from_account.name}' is a credit card.  A card is paid by a "
            f"transfer INTO it; money leaves it only as a purchase on the card."
        )


def _reject_unmodeled_source(from_account: Account) -> None:
    """Refuse a SOURCE no engine models a transfer out of: a loan or a card.

    **The ONE set of source refusals, and the only name either door calls.**
    A transfer's source is refused at two doors --
    :func:`app.services.transfer_service.create_transfer` and
    :func:`._endpoints._resolve_endpoints`, where an update moves a source --
    and the loan refusal's own docstring records why refusing at one door
    leaves the other a way straight past it.  Two refusals spelled at two
    doors is the same defect one level up: a third refusal added at one door
    and forgotten at the other, with nothing but a test per door to notice.
    Composing the set here gives it ONE home: a refusal joins this body and
    reaches both doors (plan step ``credit_card:CC-10``, CLAUDE.md rule 14).
    It does not make a one-door refusal unrepresentable -- a call added
    directly at either door still is one -- which is why neither door names
    an arm of this set, and why the arms have no other caller.

    The order is immaterial: ``ck_account_types_revolving_is_plain`` makes a
    type that is both amortizing and revolving unrepresentable, so at most one
    arm can fire for any real row.

    Args:
        from_account: The transfer's source account (already ownership-checked,
            with its ``account_type`` loaded).

    Raises:
        ValidationError: When *from_account* is an amortizing loan
            (:func:`._loan_posting._reject_transfer_out_of_loan`) or a credit
            card (:func:`_reject_transfer_out_of_revolving`).
    """
    _reject_transfer_out_of_loan(from_account)
    _reject_transfer_out_of_revolving(from_account)


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
    # Soft-deleted transfers are invisible to normal operations: a deleted
    # transfer takes no money (ruling R-CC89), and an edit would write the
    # mirrors of twins that are hidden with it.
    if not allow_deleted and xfer.is_deleted:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    return xfer


def assert_restorable(xfer, shadows, user_id):
    """Refuse a restore whose preconditions do not hold, before anything moves.

    The three checks ``restore_transfer`` runs before it un-deletes a thing.
    Extracted here at plan step X-aj1 (ruling **R-DO**) because they are
    precondition checks on the rows a mutation operates on, which is this
    module's single responsibility, and because gathering them made the caller's
    own defect visible: it used to set ``is_deleted = False`` FIRST and then
    hand-restore the flag on each failing branch -- a rollback written out three
    times, with three chances for the next branch to forget it.  Validating
    before mutating makes that class of miss structurally impossible, and the
    three hand-rollbacks are deleted rather than extended to a fourth.

    The checks, in order, each refusing rather than repairing:

    1. **Shadow count.** Exactly two, or the pair is corrupt (Invariant 1).
    2. **Type pairing.** One expense and one income, or the pair is corrupt.
    3. **Archived endpoints (F-164).** The account FK is RESTRICT, so the rows
       cannot be hard-deleted while the transfer references them; the only way
       an endpoint goes away semantically is ``is_active = False``.  Restoring
       onto one would resurrect entries against an account the user has
       withdrawn from active projections, producing balance drift they have no
       UI affordance to investigate.

    A fourth, **unrepairable status drift** (ruling **R-DO**: a shadow whose
    status could not legally reach the parent's), went at plan step
    ``balance:X-bi-6-4d-2``: a shadow's status is no longer kept or read, so
    the restore no longer repairs one.

    Args:
        xfer: The soft-deleted :class:`~app.models.transfer.Transfer` being
            restored.  NOT mutated here.
        shadows: Every :class:`~app.models.transaction.Transaction` linked to
            it, loaded without an ``is_deleted`` filter.
        user_id: The owner, for the archived-endpoint refusal's structured log.

    Raises:
        ValidationError: On any of the three, with a message naming what a human
            has to fix.
    """
    transfer_id = xfer.id
    if len(shadows) != 2:
        logger.error(
            "Cannot restore transfer %d: expected 2 shadow transactions, "
            "found %d.  Shadow IDs: %s.  Data integrity issue.",
            transfer_id, len(shadows), [s.id for s in shadows],
        )
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions (expected 2).  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    type_ids = {s.transaction_type_id for s in shadows}
    if type_ids != {expense_type_id, income_type_id}:
        logger.error(
            "Cannot restore transfer %d: shadow type pairing is invalid.  "
            "Expected one expense and one income, found type_ids=%s.",
            transfer_id, type_ids,
        )
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

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
        raise ValidationError(
            "Cannot restore transfer: source or destination account "
            "is archived.  Reactivate the account before restoring."
        )
