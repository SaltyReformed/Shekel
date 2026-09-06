"""
Shekel Budget App -- Transfer Service: WHICH TWO ACCOUNTS a transfer is on

:class:`_Endpoints`, its resolver and its applier -- the leaf that answers
"which account does this transfer's money leave, and which does it arrive at",
and that MOVES a transfer between two pairs of them.

**It exists because a recurring definition states two accounts the write door
could not** (plan step R10-b).  A generated transfer derives six columns from
its template; :func:`app.services.transfer_service.update_transfer` could write
four, so the recurrence engine applied a template's account change by DELETING
every row the definition had generated and re-creating it -- taking each row's
id, its ``notes`` and any settlement record it had retained through a revert
along with it.  Measured on a production clone, one edit of one live template:
51 transfers and 102 shadow rows destroyed and rebuilt to write values identical
to the ones already there, with 51 income legs re-pointed purely as a side
effect.  The same gap made a NON-repeating transfer refuse an account change
outright, so one edit meant two different things depending on whether the
transfer repeated.

Split out of :mod:`._update` at the same step, on the ground every earlier split
from this package used.  Written inline, that module measured 1,018 lines
against pylint's 1,000 cap (it was 805 before this step) -- and finding
**N-152** records that shaving prose is not the answer to that: a package, one
private leaf per responsibility, is.  The seam is the one the responsibility already
draws: :mod:`._update` owns applying an arbitrary field bag and reconciling the
ledger afterwards; which two accounts the pair sits on, and what a move between
them is refused for, is one question and it is this module's.

Flask-isolated like the rest of the package: plain data and ORM rows in,
mutations applied in place, no ``request`` / ``session`` imports, no flush, no
commit -- the caller owns the session boundary.
"""

from typing import NamedTuple

from app.exceptions import ValidationError
from app.models.account import Account
from app.services.transfer_service._create import shadow_names
from app.services.transfer_service._loan_posting import (
    _reject_transfer_out_of_loan,
)
from app.services.transfer_service._ownership import _get_owned_account
from app.services.transfer_service._validation import TransferRows


class _Endpoints(NamedTuple):
    """The two accounts an update LEAVES a transfer on, and the ones it vacates.

    **A transfer's endpoints move together or not at all**, because
    ``ck_transfers_different_accounts`` is a rule about the PAIR and because a
    move re-derives BOTH shadow names from BOTH accounts
    (:func:`app.services.transfer_service._create.shadow_names`).  Passing them
    as one value is what stops a call site pairing a resolved source with an
    unresolved destination.

    Attributes:
        from_account: The account the transfer's money leaves once this update
            lands -- the EXPENSE leg's account.  The transfer's current source
            when the update names none.
        to_account: The account it arrives at -- the INCOME leg's account.
        vacated_source_id: The account the money used to LEAVE, when this
            update moves it, else ``None``.
        vacated_destination_id: The account it used to ARRIVE at, when this
            update moves it, else ``None``.  Named apart from the source
            because only the DESTINATION can be a loan, and a loan payment's
            split correction has to be reversed while the pair is still on it
            (:func:`._loan_posting._reverse_loan_payment_before_it_leaves`).
    """

    from_account: Account
    to_account: Account
    vacated_source_id: "int | None"
    vacated_destination_id: "int | None"

    @property
    def vacated(self) -> "tuple[int, ...]":
        """Return the account IDs this update moves the transfer OFF.

        Empty when neither endpoint moves, which is every update that names no
        account.  It is what the ledger tail re-derives afterwards, and it is
        deliberately not a boolean: a reconcile has to NAME the accounts it
        walks, and "did something move" cannot.
        """
        return tuple(
            account_id
            for account_id in (
                self.vacated_source_id, self.vacated_destination_id,
            )
            if account_id is not None
        )


def _resolve_endpoints(
    rows: TransferRows, user_id: int, updates: "dict[str, object]",
) -> _Endpoints:
    """Resolve and REFUSE the endpoints this update leaves the transfer with.

    **Plan step R10-b, and it exists because the definition states two accounts
    the write door could not.**  A generated transfer derives six columns from
    its template and :func:`update_transfer` could write four; the recurrence
    engine applied the other two -- a template's source and destination -- by
    DELETING every row the definition had generated and building replacements,
    which took each row's id, its notes and any settlement record it had
    retained through a revert with it.  Measured on a production clone: a single
    edit of one live template destroyed 51 transfers and 102 shadows to write
    values identical to the ones already there, and moved 51 income legs from
    one account to another purely as a side effect of the rebuild.

    Everything a MOVE must be refused for is asked here, and this function
    WRITES NOTHING -- so a move refused for its own reason leaves all three rows
    exactly as it found them.  **That is not the same as saying the whole update
    is atomic, and an adversarial review of R10-b is why the difference is
    spelled out**: :func:`._update.update_transfer` applies the caller-stated
    facts (the override flag, the endpoints, the amount) and only then dispatches
    the SETTLE, which can still raise -- on an illegal transition, an
    unresolvable figure, or one of the seam's settle-day refusals.  So "no write
    precedes a refusal" holds for every gate, and a raise from the settle still
    leaves a partially-applied pair in the session for the caller's rollback to
    discard.  The amount's own refusal was hoisted into that gate block at this
    step for exactly this reason; the settle's cannot be, because they are
    answers the settle computes.  Nothing is re-asked when neither
    endpoint moves, which is the same discipline
    :func:`_reject_installment_move_before_loan` states for the installment
    fields: a pre-existing arrangement stays editable in every OTHER respect
    rather than being re-graded by an unrelated edit.

    The PARENT's name is not touched here; see
    :func:`app.services.transfer_service._create.shadow_names` for why a
    derived shadow name is re-derived and a stated transfer name is not.
    **For an AD-HOC transfer created without a name that leaves a stale label**
    -- ``create_transfer`` defaults the parent's name from the endpoints, and
    nothing afterwards distinguishes that default from a name the owner typed.
    It is latent rather than live: an ad-hoc transfer reaches this arm from no
    route, and both callers that do reach it are template-linked and state the
    definition's name alongside the accounts.

    Args:
        rows: The transfer and both shadows, at their pre-update endpoints.
        user_id: The expected owner -- both resulting accounts are re-checked
            against it, so a caller that skips the route cannot re-point a
            transfer at an account across an ownership line.
        updates: The update kwargs as submitted.

    Returns:
        The :class:`_Endpoints` this update leaves the transfer with.

    Raises:
        NotFoundError: If a submitted account is not *user_id*'s.  The security
            response rule collapses "not found" and "not yours" to one answer.
        ValidationError: If the move would leave the two endpoints equal, or
            if it MOVES the source onto an amortizing loan -- a disbursement,
            which the loan engine does not model
            (:func:`_reject_transfer_out_of_loan`).  A move that leaves the
            source where it is does not re-ask, for the reason the comment at
            that call states.
    """
    xfer = rows.transfer
    from_id = updates.get("from_account_id", xfer.from_account_id)
    to_id = updates.get("to_account_id", xfer.to_account_id)
    vacated_source = (
        xfer.from_account_id if from_id != xfer.from_account_id else None
    )
    vacated_destination = (
        xfer.to_account_id if to_id != xfer.to_account_id else None
    )
    if vacated_source is None and vacated_destination is None:
        return _Endpoints(xfer.from_account, xfer.to_account, None, None)
    if from_id == to_id:
        raise ValidationError(
            "Source and destination accounts must be different."
        )
    from_account = _get_owned_account(from_id, user_id, label="Source account")
    to_account = _get_owned_account(
        to_id, user_id, label="Destination account",
    )
    # **Asked only when the SOURCE moves**, which an adversarial review of this
    # step corrected.  Asked on any endpoint move, it re-graded an arrangement
    # the edit does not touch: a legacy transfer whose source is an amortizing
    # loan -- written before ``create_transfer`` guarded it -- could not move
    # its DESTINATION either, which is the freezing
    # :func:`._loan_posting._reject_installment_move_before_loan` states the
    # discipline against three paragraphs down its own docstring.  It also made
    # the vacated-source arm of :func:`._loan_posting._resync_vacated_loan`
    # unreachable, so that function's stated reason for taking both endpoints
    # was false.  Narrowing it restores both.
    if vacated_source is not None:
        _reject_transfer_out_of_loan(from_account)
    return _Endpoints(
        from_account, to_account, vacated_source, vacated_destination,
    )


def _apply_endpoint_move(rows: TransferRows, endpoints: _Endpoints) -> None:
    """Move a transfer and both legs onto *endpoints*, re-deriving the names.

    Transfer Invariant 1 read as a write: the parent names the pair of accounts
    and each shadow LIVES on one of them, so all three move in one act or the
    pair stops describing the transfer.  The expense leg takes the source and
    the income leg the destination, which is the pairing
    :func:`app.services.transfer_service._create.create_transfer` establishes
    and :class:`~app.services.transfer_service._validation.TransferRows`
    resolves by transaction TYPE rather than by position.

    **Applied with the caller-stated facts, ahead of the settle dispatch**, for
    the reason the ``is_override`` arm states: everything between those two
    points is a fact the CALLER stated, and the derivation comes after all of
    them.  The settle builds its own
    :class:`~app.services.cash_ledger.AmountBasis` from the database
    (``._settle.settle``), and that basis resolves an auto-derived loan
    payment's destination loan -- so an update that re-points a payment and
    settles it in one call must have stated the destination first.

    **That is the placement's PRINCIPLE, not a measured defect, and the
    difference is stated rather than blurred.**  Moving the apply after the
    dispatch was tried and the suite stayed green: the only reader that would
    answer differently is ``cash_ledger.LoanPricing.live_cash``, and
    reaching it needs a TEMPLATE-linked derive-mode loan payment re-pointed
    between two loans while settling in the same call -- which no route sends
    and the recurrence engine's maintain pass never does, because it settles
    nothing.  The order is kept because a caller-stated fact belongs before the
    derivation that reads it, and this paragraph is what stops the next reader
    concluding it was measured.

    **It assigns the RELATIONSHIP, not the foreign key, and the difference is a
    measured defect.**  SQLAlchemy does not refresh ``Transfer.to_account`` when
    only ``to_account_id`` moves -- not even across the flush that follows -- so
    every reader that asks the ACCOUNT rather than its id saw the account the
    transfer had before the move.  Measured: after re-pointing a Mortgage
    payment at savings, ``xfer.to_account.name`` still answered ``Mortgage``.
    Two readers depend on it and each fails in a different direction:
    ``_loan_posting._sync_loan_postings_if_loan`` re-derives the genesis of
    whichever loan ``to_account`` names, so a move ONTO a loan would have left
    the NEW loan's ledger unreconciled while a move OFF one reconciled the old
    one by accident; and ``._settle.settle``'s auto-derived loan freeze asks the
    same question, so a combined move-and-settle would have priced the payment
    against the wrong loan.  Assigning the relationship writes both halves at
    once and leaves nothing to remember.

    Args:
        rows: The transfer and both shadows.
        endpoints: The resolved :class:`_Endpoints`; a no-op when its *vacated*
            is empty, which is every update that names no account.
    """
    if not endpoints.vacated:
        return
    expense_name, income_name = shadow_names(
        endpoints.from_account, endpoints.to_account,
    )
    rows.transfer.from_account = endpoints.from_account
    rows.transfer.to_account = endpoints.to_account
    rows.expense.account = endpoints.from_account
    rows.expense.name = expense_name
    rows.income.account = endpoints.to_account
    rows.income.name = income_name
