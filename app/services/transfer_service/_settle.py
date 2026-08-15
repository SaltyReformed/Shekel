"""
Shekel Budget App -- Transfer Service: what SETTLING a transfer means

The whole act, for all three rows at once: an auto-derived loan payment stops
deriving and OWNS what it is live worth, the status and the settle day land in
ONE seam pass, and a figure a human typed is told apart from the panel's own
prefill echoed back.

**The rule lives here rather than at each door, and that is ruling R-FA applied
to the transfer table.**  It lived in ONE route branch --
``routes/transactions/_shadow_mutations``'s ``_mark_done_shadow``, which called
``loan_payment_service.live_loan_payment_amount`` and handed the answer to
``update_transfer`` as an ``actual_amount`` -- and FOUR doors could move a
transfer into the settled band before this step added a fifth (the reconcile
panel's tick):

* the grid's shadow "Mark Paid" (``_mark_done_shadow``), which froze;
* the transfers page's "Mark Done" (``routes/transfers/mutations.mark_done``),
  which did not;
* the transfer full-edit Status dropdown (``_execute_transfer_update``), which
  did not;
* a transaction PATCH landing on a shadow (``_apply_shadow_update``), which did
  not.

That is finding **N-219**'s shape one table over -- a ROUTE holding a money
rule, so one control books a different figure from another for the same payment.

**WHICH COLUMN the freeze writes is deliberately NOT changed here, and the
reason is measured rather than conservative.**  The figure is the APP's
derivation, so ruling **R-FH** says it belongs in the row's OWN amount and
``actual_amount`` should hold a human's figure alone; finding **N-241** records
that it does not.  This leaf BUILT that move and then withdrew it, because two
adversarial reviews measured it unsafe on today's schema:

* ``loan_payment_service._manual_shadow_amount`` derives a manual payment's cash
  as ``estimated_amount + extra`` and documents that column as *"always the
  generated base"*.  Writing the freeze there makes the derivation read its own
  output, so a settle / revert / settle cycle COMPOUNDS the standing extra --
  measured ``$1,599.10 -> $1,699.10 -> $1,799.10``;
* what a row is worth prefers a human's ``actual_amount`` over its own amount,
  and nothing clears a leftover ``actual_amount`` when a transfer is reverted
  (finding **N-257**), so a freeze written to ``estimated_amount`` is silently
  OUTRANKED by it -- the panel offers the frozen figure and the settle books
  the stale one, ``$99.10`` apart on the reviewed measurement.

Both hazards are the same shape: the write is neither idempotent nor
authoritative while the schema has no way to say whether a row's amount is its
OWN or DERIVED.  Ruling **R-FI**'s ``amount_source`` column is exactly that
statement, and plan step **X-au-c** adds it -- so the column move is that step's,
and the developer ruled it there (2026-08-12).  What THIS leaf does instead is
put the write in ONE place, so moving it is a one-line change rather than a hunt
across four modules.

**What it costs on production today is `$0.00`, and the reason is worth
stating**: ``budget.loan_payment_settings`` holds ZERO rows, so
``loan_payment_config`` answers ``(False, 0.00)`` for every transfer template
and :func:`frozen_amount` returns ``None`` everywhere.  All 342 shadows carry
``actual_amount = NULL`` (re-measured 2026-08-12), which is that fact in the
data.  The split opens the
first time a loan payment transfer is created through
``routes/loan/payment_transfer.py``, which is a live route.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Reads and mutates ORM rows; no flush, no commit.
  - All monetary arithmetic uses :class:`~decimal.Decimal`.
"""

import logging
from datetime import date
from decimal import Decimal

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import loan_payment_service
from app.services.cash_ledger import amount_basis, resolve_transaction_amount
from app.services.row_valuation import fixed_contribution
from app.services.transfer_service._status import apply_status_to_all_three
from app.services.transfer_service._validation import TransferRows
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_AMOUNT_FROZEN,
    log_event,
)

logger = logging.getLogger(__name__)


def _reject_unsettleable(shadow: Transaction) -> None:
    """Refuse a row this module may not settle or price -- both rules, once.

    **The twin of ``transaction_service.reject_unsettleable``, and it exists
    for the reason that one does** (finding **N-233**): a verb owns its own
    preconditions, and the two public surfaces here would otherwise state them
    twice or -- as the first build of this module did -- not at all.

    **A row that is not a transfer shadow** has no parent to move with it, so
    pricing one here would answer for a row this module cannot settle.  It is
    the exact complement of the transaction service's own first rule, which
    refuses a shadow and names this module as the place a shadow goes.

    **A soft-deleted shadow** must not be resurrected by a settle.  It values
    at ``Decimal("0")`` (the valuation's own gate), so settling one books
    nothing while stamping both legs Paid and dated: a pair that reads settled
    and is worth nothing.  ``_get_transfer_or_raise`` already refuses a deleted
    PARENT, and the reconcile arm's own scope excludes both -- but
    :func:`settle_amount` is a public pure read with neither in front of it,
    and a figure this module publishes for a row it refuses to book is the
    shape plan step X-f2-c3 removed one table over.

    Args:
        shadow: The row to check.  Reads ``transfer_id`` and ``is_deleted``;
            neither triggers a lazy load.

    Raises:
        ValidationError: When *shadow* is not a transfer shadow, or is
            soft-deleted.
    """
    if shadow.transfer_id is None:
        raise ValidationError(
            f"Transaction {shadow.id} is not a transfer shadow; a regular row "
            "settles via transaction_service.settle_transaction.",
        )
    if shadow.is_deleted:
        raise ValidationError(
            f"Transaction {shadow.id} is soft-deleted; a settle cannot "
            "resurrect a deleted row.",
        )


def frozen_amount(shadow: Transaction) -> Decimal | None:
    """Return the live payment-date figure a settle FREEZES, or ``None``.

    The capture-on-settle rule: when the operator settles an auto-derived loan
    payment with one click, the figure recorded is the LIVE payment-date amount
    (P&I + escrow-as-of + extra), not the creation-time estimate the shadow was
    generated with.  Because the frozen cash and the genesis split read the same
    ``escrow_monthly_as_of`` on the shadow's own DUE date, ``cash == split``
    holds by construction rather than by luck.

    **Why the stored figure is stale in the first place**, since the freeze
    reads as arbitrary without it: ``routes/loan/payment_transfer.py`` writes
    one P&I-plus-escrow SNAPSHOT into ``transfer_templates.default_amount`` when
    the payment is set up, and ``transfer_recurrence`` copies that same scalar
    into every transfer it generates, for every period, forever.  The live
    figure re-resolves the escrow version in effect on each row's own due date.
    So the stored amount is not stale by accident -- it is a copy of a
    derivation, taken once and never invalidated, which is finding **N-224**'s
    shape and what ruling **R-FI** deletes outright.

    ``None`` -- meaning "this row derives nothing; leave its amount alone" --
    for every shadow that needs no capture: an operator ``is_override`` (the
    operator owns that amount), an already-settled shadow, a transfer that is
    not a loan payment (no settings row), a MANUAL payment with no standing
    extra (its stored estimate already IS the cash), or a loan that cannot
    resolve.  ``loan_payment_service.live_loan_payment_amount`` owns that list;
    this is a named seam over it, not a second copy.

    **The ``is_projected`` guard inside makes the freeze ONE-SHOT**, and
    :func:`settle` is placed to keep it that way: it resolves this BEFORE the
    status is applied, so a genuine first settle still sees a Projected shadow.
    A re-settle cannot reach it at all -- :func:`settle` runs only on the way
    INTO the settled band (``enters_settled_band``), so the ``done -> done``
    identity a stale tab can submit never re-freezes.

    Args:
        shadow: Either leg of the transfer -- both share the transfer id, the
            pay period and the due date, so either resolves the same figure and
            Transfer Invariant 3 is preserved whichever is passed.

    Returns:
        The ``Decimal`` to freeze, or ``None``.
    """
    return loan_payment_service.live_loan_payment_amount(
        shadow, shadow.scenario_id,
    )


def settle_amount(shadow: Transaction) -> Decimal:
    """Return what settling this transfer would BOOK on *shadow*'s account.

    **The transfer twin of ``transaction_service.settle_amount``, and it exists
    for the same reason**: the reconcile panel must show the figure a tick will
    book, and a panel rendering the row's own amount beside a verb that books
    the freeze is two answers to one money question, one screen apart.
    :func:`settle` resolves its own figure through the same two functions, so
    the displayed figure and the booked one cannot drift.

    It is a PURE read: nothing here mutates, so the panel calls it per offered
    row and the verb resolves again at the settle.

    Args:
        shadow: The leg being offered, still Projected.

    Returns:
        The frozen live figure when there is one, else what the row
        CONTRIBUTES (:func:`_unfrozen_amount`).

    Raises:
        ValidationError: On a row this module may not settle
            (:func:`_reject_unsettleable`) -- publishing a figure for one would
            be publishing a figure :func:`settle` refuses to book.
        AmountUnresolvable: From the amount model, for a row whose rule cannot
            price it.  A refusal is never a fallback.
    """
    _reject_unsettleable(shadow)
    frozen = frozen_amount(shadow)
    return _unfrozen_amount(shadow) if frozen is None else frozen


def _unfrozen_amount(shadow: Transaction) -> Decimal:
    """Return what *shadow* contributes when no freeze answers for it.

    The transfer twin's replacement for ``shadow.effective_amount`` (plan step
    X-au-c2), and the ONE statement of it, so the figure the panel OFFERS and
    the figure :func:`settle` BOOKS cannot come to be computed two ways -- which
    is the drift :func:`settle_amount` exists to prevent, and it would have been
    reintroduced by inlining this at both sites.

    **The status / entered-actual gate is asked BEFORE the basis is built**,
    and an adversarial review is why: Python evaluates arguments before the
    call, so passing ``amount_basis(...)`` into the valuation ran the producers
    unconditionally -- including for the two shapes that never read the result.
    A Cancelled or Credit shadow answers ``$0.00`` from the gate, and a shadow
    carrying a leftover ``actual_amount`` answers that; finding **N-257** is
    that the second is REACHABLE, because nothing clears an actual when a
    transfer is reverted, so every re-offered reverted shadow paid a full
    producer run whose answer was discarded.

    **It is reached only when :func:`frozen_amount` answered ``None``.**  A
    derive-mode loan payment is priced by the freeze, so this basis is built
    exactly for the rows the loan resolver had no answer for.  What it still
    costs on the rows that DO reach the resolver is one repeat of the transfer
    /template lookup ``frozen_amount`` just made -- recorded rather than fixed,
    because removing it means threading a resolved config into ``amount_basis``,
    which is plan step X-au-f's redesign of this door.

    A basis over ONE row is correct here rather than a batch: both public
    surfaces price a single shadow -- the panel offers row by row and the settle
    books one transfer -- so there is no row set to amortise a producer call
    over.

    Args:
        shadow: The leg being priced.  Its ``scenario_id`` is a column and its
            ``account`` relationship is ``lazy="joined"``, so neither key costs
            a query.

    Returns:
        ``0`` for a row that contributes nothing, the entered ``actual_amount``
        when there is one, else the row's resolved amount.

    Raises:
        AmountUnresolvable: When the rule that prices this row cannot answer.
    """
    fixed = fixed_contribution(shadow)
    if fixed is not None:
        return fixed
    return resolve_transaction_amount(
        shadow,
        amount_basis(shadow.account.user_id, shadow.scenario_id, [shadow]),
    )


def settle(
    rows: TransferRows,
    new_status_id: int,
    *,
    submitted: Decimal | None,
    settled_on: date | None,
) -> bool:
    """Settle a transfer -- both legs and the parent -- and say whose figure it booked.

    **The whole act, in one function, and that is what the four doors buy from
    it.**  A settle is not a status change with extras: it decides what the row
    is worth, records that the money moved, and dates it, and a door that does
    two of those three books a figure the third contradicts.

    Three acts, in this order, and the order is the rule:

    1. **The amount, decided but not yet written.**  :func:`frozen_amount` is
       resolved ONCE, before anything moves -- it is guarded on ``is_projected``,
       so asking after the status flip would always answer ``None``.  A figure a
       HUMAN supplied is compared against what the row would book anyway and is
       a CORRECTION only if it differs; a correction beats the derivation,
       because a figure somebody read off a statement is a fact and the freeze
       is an inference.
    2. **The status and the settle day, in ONE seam pass.**  The day is the
       caller's when it has one -- the reconcile tick's statement date -- so
       the pair is dated once rather than stamped with today and corrected
       afterwards.  That second write was this module's own defect: the settle
       went through :func:`~app.services.transfer_service._status.apply_settle_day_correction`,
       the door ruling **R-ED** built for a user CORRECTING a day, so every
       tick wrote ``settled_on`` twice and the intermediate value was a day the
       money did not move.
    3. **The figure**, written to BOTH legs after the seam so the ledger
       reconcile in ``update_transfer``'s tail reads the final amount rather
       than the pre-settle estimate -- and after it, so a refused transition
       leaves the money columns untouched.

    **An ECHO is not written, and act 3 exists for that.**  The reconcile panel
    PREFILLS its amount box, so an untouched tick posts the figure the row would
    book anyway; recording it would populate a column that is NULL on every
    uncorrected row and destroy the only signal that says a human read a number
    off a statement (ruling **R-FB**'s production measurement, "11 of 93 settled
    bills carry a hand-typed correction", is made of exactly that signal).

    **A settle WRITES the column or leaves it, and never CLEARS it.**  Clearing
    is its own act: a caller that means it says so with an explicit
    ``actual_amount=None``, which
    :func:`~app.services.transfer_service._update._fields_the_settle_left`
    routes past this function to the door that performs it.
    ``settle_transaction`` follows the same rule one table over, so the two
    settles cannot come to disagree about what a ``None`` means.

    **Writing the freeze HERE rather than into the row's own amount is finding
    N-241 left open deliberately**; the module docstring above states the two
    measured hazards that withdrew the move and names the step that owns it.
    Because the freeze lands in ``actual_amount``, it OVERWRITES a figure left
    behind by a reverted settle rather than being outranked by one -- so the
    booked figure is what :func:`settle_amount` offered, on every row shape.
    That is the property the column move would have broken, and it is why the
    move waits for ``amount_source`` rather than shipping beside a workaround.

    Mutates in place.  Does NOT flush, commit, or reconcile the posted ledger
    -- ``update_transfer``'s tail owns all three, so a settle and an ordinary
    edit reconcile through one statement.

    Args:
        rows: The transfer and both shadows, at their pre-settle status.  The
            freeze is resolved from the EXPENSE leg; either would answer the
            same (Transfer Invariant 3 -- both share the transfer id, the
            period and the due date), and naming one means the choice is not
            made twice.
        new_status_id: The settled status all three rows move to, as the DOOR
            asked for it.  Verified by
            :func:`~app.services.transfer_service._status.apply_status_to_all_three`.
        submitted: The figure a caller supplied, or ``None`` when nobody typed
            one.
        settled_on: The civil day the money moved, when the caller knows it.
            ``None`` leaves the pair-day rule in force.

    Returns:
        Whether this settle booked a HUMAN's figure -- what the reconcile
        writer counts (finding **N-231**).  Answered by the act itself rather
        than by a predicate the caller asks separately, so the count and the
        write cannot disagree and the freeze is resolved once per settle
        instead of once per asker.

    Raises:
        ValidationError: On a row this module may not settle
            (:func:`_reject_unsettleable`), or from the status seam's own
            transition and settle-day refusals.
    """
    _reject_unsettleable(rows.expense)

    # Resolved ONCE, and everything below reads this answer rather than asking
    # again.  It was asked up to three times per ticked row before plan step
    # X-f2-c3 -- by the arm's correction predicate, by the dispatch's own
    # predicate, and by its fallback -- and each asking is a ``Transfer`` query
    # plus, for a derive-mode payment, a loan-basis resolve and an escrow load.
    frozen = frozen_amount(rows.expense)
    booked = _unfrozen_amount(rows.expense) if frozen is None else frozen
    correction = (
        submitted if submitted is not None and submitted != booked else None
    )
    # A human's figure beats the derivation; absent one, the freeze books what
    # the payment is live worth; absent both, the row keeps what it carries.
    resolved = frozen if correction is None else correction

    apply_status_to_all_three(rows, new_status_id, settled_on=settled_on)

    if resolved is not None:
        for shadow in rows.shadows:
            shadow.actual_amount = resolved
    if frozen is not None and correction is None:
        log_event(
            logger, logging.INFO, EVT_TRANSFER_AMOUNT_FROZEN, BUSINESS,
            "A derived loan payment froze its live payment-date figure",
            user_id=rows.transfer.user_id,
            transfer_id=rows.transfer.id,
            frozen_amount=str(frozen),
        )
    return correction is not None


def record_clearing(shadow: Transaction, anchor_id: int) -> None:
    """Record WHICH statement showed one leg of a transfer (ruling **R-FL**).

    **The one column of a shadow that is deliberately NOT mirrored**, and the
    reason a shadow mutation lives here rather than at the reconcile panel that
    calls it.  ``CLAUDE.md``'s transfer invariant 4 says no code path mutates a
    shadow directly; invariant 3 says the amounts, statuses and periods of the
    three rows always match.  Clearing is neither: a transfer LEAVES one bank
    and ARRIVES at another, so the asserted account's statement showed its own
    leg and the other account's statement is a document nobody read in that
    act.  Mirroring it would record an observation nobody made, on an account
    whose balance the user has not looked at.

    So the fact is per-leg and the DOOR is still the transfer service -- which
    is what keeps invariant 4 true as written, and what gives the asymmetry one
    place to be explained rather than a comment at a caller.

    **It writes the link and nothing else.**  The settle itself -- the status,
    the pair's day, the loan freeze, the correction rule -- is
    :func:`settle`'s, and the caller runs that first; a clearing recorded
    against a leg that has not settled would violate
    ``ck_transactions_cleared_needs_settle_day``, which pairs this column with
    the settle day the seam writes.

    Flushes nothing and commits nothing -- the caller owns the session boundary.

    Args:
        shadow: The leg on the account whose statement was read.  The caller
            resolved it through that account's own offer scope
            (:func:`app.services.reconcile_service._rows.outstanding_rows`), so
            it is this owner's and on this account by construction.
        anchor_id: The ``budget.account_anchor_history`` row the statement is.
    """
    shadow.reconciled_by_id = anchor_id
