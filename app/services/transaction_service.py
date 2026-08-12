"""
Shekel Budget App -- Transaction Service

Cross-cutting transaction state-change helpers used by multiple
routes and services.  Each function mutates a Transaction in place
and leaves the session/commit lifecycle to the caller, matching the
pattern in ``app/services/entry_service.py``.

**Two settle entry points, and the difference is deliberate.**
:func:`settle_transaction` is ruling **R-FA**'s verb -- what settling a row
MEANS, amount and status and ledger together.  :func:`settle_from_entries` is
the envelope PRIMITIVE underneath it, and it stays public for
``carry_forward_service``, which settles a BATCH and must reconcile the ledger
after its ``no_autoflush`` block, so it owns that act itself.

**Beside them sits :func:`apply_requested_status`, which is not a settle entry
point but the route layer's ONE status entry point.**  A door states the status
the user asked for; that function decides what applying it means and dispatches
a settle to the verb.  It exists because a route was making that decision, and
making it wrong -- see the paragraph below.

**THREE doors settle a transaction and all three are now on this verb** (plan
step X-ap, finding **N-219**).  The grid's Mark Paid calls it, the reconcile
panel's tick calls it since plan step X-f2-c2, and the Status dropdown on the
full-edit popover reaches it through :func:`apply_requested_status` -- which it
did not, until X-ap.  That third door flipped the status through the status
seam and reconciled the ledger, but never consulted the entries, so an
envelope-tracked row with a `$25` purchase against a `$400` estimate booked
`$25` through Mark Paid and **`$400`** through the dropdown, from two controls
in the same card.  Ruling **R-FA** named "two route branches" and there were
three; the census is now closed and stated at :func:`apply_requested_status`.

**The rule this module holds is that a door states an INTENT and the service
decides what it costs.**  Every public function here is one of those decisions,
and none of them is reachable from a template or a form field.

Architecture:
  - No Flask imports.  Receives ORM objects, mutates them, and
    raises domain exceptions on precondition violation.
  - All monetary arithmetic uses ``Decimal`` (the helpers delegate
    to ``app.services.entry_service.compute_actual_from_entries``).
  - Does NOT flush or commit -- the caller owns the transaction
    boundary so the helper can safely participate in larger atomic
    operations (e.g. the carry-forward batch in Phase 4).  The ledger
    reconcile :func:`settle_transaction` runs FLUSHES; it still does not
    commit.
"""

import logging
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import posting_service
from app.services.cash_ledger import live_amount_overrides
from app.services.entry_service import compute_actual_from_entries
from app.services.state_machine import allowed_transitions
from app.services.status_seam import apply_status_change
from app.utils.balance_predicates import settled_status_ids
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
    log_event,
)

logger = logging.getLogger(__name__)


def settled_status_member(txn: Transaction) -> StatusEnum:
    """Return the settled STATUS a row of this TYPE takes, as the enum member.

    The type rule, stated ONCE.  :func:`settled_status_id` resolves it to a
    ``ref.statuses.id`` for the writers and
    :func:`reject_mismatched_settled_status` reads its ``value`` for the human
    message -- which used to be a third spelling, ``"Received" if txn.is_income
    else "Paid"``, sitting two lines below the resolution it was restating.  A
    message derived from a second copy of a rule tells the user something the
    code may have stopped doing.

    Args:
        txn: The transaction about to settle.  Read for ``is_income`` only.

    Returns:
        ``StatusEnum.RECEIVED`` for income, ``StatusEnum.DONE`` otherwise.  The
        member's ``value`` is the DISPLAY name ("Received" / "Paid"), which is
        what makes one expression answer both needs.
    """
    return StatusEnum.RECEIVED if txn.is_income else StatusEnum.DONE


def settled_status_id(txn: Transaction) -> int:
    """Return the settled status a row of this TYPE takes.

    Income is Received and everything else is Paid.  It is a display
    convention rather than a balance one -- every reader of the settled band
    consumes ``settled_status_ids()`` as a SET and cannot tell the members
    apart -- but it is a convention with TWO former spellings, which is one
    too many for a rule that decides a stored column.

    **Both spellings were live and they agreed by reading.**
    ``app/routes/transactions/mutations.py:mark_done`` picked the id and
    handed it down, and :func:`settle_from_entries` re-derived the same id
    from the same predicate, its own comment naming the route as the thing it
    "mirrors" -- Section 8's "two spellings that agree by reading are two
    answers until one is deleted", on a money-adjacent rule.  Ruling **R-FA**
    forced the question by giving the reconcile tick the same settle as the
    grid's Mark Paid: a third door would have made it three.

    A transfer SHADOW never reaches here.  Its settle goes through
    ``transfer_service.update_transfer``, which sets Paid on both legs
    because the income/expense split is meaningless for a pair whose whole
    point is that one leg is each.

    Args:
        txn: The transaction about to settle.  Read for ``is_income`` only.

    Returns:
        The ``ref.statuses.id`` for Received or Paid.
    """
    return ref_cache.status_id(settled_status_member(txn))


def enters_settled_band(txn: Transaction, new_status_id: int) -> bool:
    """Return whether moving *txn* to *new_status_id* SETTLES it.

    **The predicate that tells a status ASSIGNMENT from a SETTLE**, published
    because :func:`apply_requested_status` dispatches on it and a door must not
    restate it.  Settling is entering the band from outside it: Projected ->
    Paid or Projected -> Received, the only two the state machine admits inward
    (Credit and Cancelled reach the band through Projected, never directly).

    **Staying inside the band is NOT a settle**, and that half is load-bearing.
    ``Paid -> Settled`` is an ARCHIVE of a row whose money already moved and
    whose amount is already a fact, and ``Paid -> Paid`` is an idempotent
    re-submit; routing either to :func:`settle_transaction` would ask an
    immutable row to re-price itself, which its envelope branch refuses by
    precondition and its manual branch would answer by re-reading a projection
    the row left months ago.

    Args:
        txn: The row, read for its CURRENT ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when the move crosses INTO the settled band from outside it.
    """
    settled = settled_status_ids()
    return txn.status_id not in settled and new_status_id in settled


def leaves_settled_band(txn: Transaction, new_status_id: int) -> bool:
    """Return whether moving *txn* to *new_status_id* UNSETTLES it.

    :func:`enters_settled_band`'s mirror, and it exists for the same reason:
    the two directions are different acts and a door must not decide which is
    which.  The only edges out of the band are ``Paid -> Projected`` and
    ``Received -> Projected`` -- the documented unlock path, where the user is
    saying the money did not move after all.

    Args:
        txn: The row, read for its CURRENT ``status_id``.
        new_status_id: The status a door is asking for.

    Returns:
        True when the move crosses OUT of the settled band.
    """
    settled = settled_status_ids()
    return txn.status_id in settled and new_status_id not in settled


def _release_derived_actual(txn: Transaction) -> None:
    """Drop an envelope's DERIVED actual when the row stops being settled.

    **A settle writes ``actual_amount`` for an envelope; a revert must take it
    back**, because what that column holds for such a row is not a fact the
    user authored -- it is ``sum(entries)`` at the moment of the settle, which
    :func:`settle_from_entries` wrote in the same statement as the status and
    the settle day.  The seam already clears ``settled_on`` on the way out, on
    exactly this reasoning; the derived amount is the same kind of value and was
    being left behind.

    **Measured, and it is why this exists rather than being argued.**
    Production row 2281 *Groceries* is Projected today carrying
    ``actual_amount = 533.08`` against a `$500.00` budget -- written by
    ``settle_from_entries`` (audit_log id 2978, one statement with ``status_id``
    and ``settled_on``) and left behind by a later revert.  ``effective_amount``
    is ``COALESCE(actual, estimated)``, so that row projects at its SPEND rather
    than its budget, and a purchase deleted while it is Projected does not move
    the figure: :func:`app.services.entry_service._resync_settled_envelope` is
    gated on the settled band, correctly, because a Projected row's actual is
    not yet a fact.  The result is a stored derived value with nothing that can
    re-derive it.

    **Only the DERIVED kind is released**, which is what makes this narrow
    enough to be right: a BILL's ``actual_amount`` is a figure a HUMAN read off
    a statement (ruling **R-FB**), and clearing that on a revert would delete
    the user's own correction.  :func:`settles_from_entries` is the same
    predicate the settle branches on and the same one the edit doors offer an
    amount box on (ruling **R-FF**), so a row's amount is derived, correctable
    and released by ONE rule rather than three.

    Mutates in place; does NOT flush or commit.

    Args:
        txn: The row leaving the settled band, still in its settled status.
            Read for ``tracks_purchases`` and ``entries``.
    """
    if settles_from_entries(txn):
        txn.actual_amount = None


def _mismatched_settled_status_ids(txn: Transaction) -> frozenset[int]:
    """Return the TYPE-specific settled statuses *txn* does NOT take.

    One expression for the two things that need it, the refusal
    (:func:`reject_mismatched_settled_status`) and the dropdown's pre-hint
    (:func:`offerable_status_ids`).  Stated once so the enforced rule and the
    displayed rule cannot drift, which is the same reason
    :func:`settles_from_entries` is published.

    **It is the TYPE PAIR minus this row's, NOT the settled band minus this
    row's, and the difference is a capability.**  The band has THREE members and
    the third is ``Settled`` -- the archive, which is not type-specific: an
    expense reaches it from Paid and an income row from Received.  A first draft
    subtracted the whole band, so ``Settled`` counted as "mismatched" for every
    row and :func:`offerable_status_ids` removed it from every dropdown --
    silently retiring the only control that offers the archive, while the state
    machine still called ``Paid -> Settled`` legal and the seam still preserved
    the settle day across it.  Caught by adversarial review, and two assertions
    written in the same step said the opposite ("the full-edit Status dropdown
    can still reach it"), which is what made it a mistake rather than a
    decision.

    A SET rather than an equality, and deliberately: an inline equality against
    a resolved status id is the D6-09 shape the project's own census test
    refuses outside ``balance_predicates``, and the set form is both honest
    about what is being asked and the one that composes with
    :func:`app.services.state_machine.allowed_transitions`.

    Args:
        txn: The row, read for ``is_income``.

    Returns:
        The ``ref.statuses.id`` values this row's TYPE forbids it settling
        into -- exactly one member, for either type.
    """
    type_specific = frozenset({
        ref_cache.status_id(StatusEnum.DONE),
        ref_cache.status_id(StatusEnum.RECEIVED),
    })
    return type_specific - {settled_status_id(txn)}


def reject_mismatched_settled_status(
    txn: Transaction, new_status_id: int,
) -> None:
    """Refuse a settled status that contradicts the row's TYPE.

    :func:`settled_status_id` is the rule -- income settles as Received, an
    expense as Paid -- and it is the verb's to apply, so a door that asks for
    the OTHER one is asking for something the domain does not have.  The
    alternative to refusing is silent substitution: the row lands in a status
    the user did not pick, with nothing said about it.

    **The state machine cannot make this judgement**, and that is why the rule
    is here.  Its transaction map admits Projected -> Paid AND Projected ->
    Received because it is keyed on the STATUS, not on the row: it never sees
    ``transaction_type_id``.  So the two facts have to meet somewhere, and this
    is the only place that holds both.

    **It refuses nothing that exists.**  Measured on production: 17 rows carry
    an income type in the Paid status and all 17 are transfer SHADOWS, whose
    settle goes through ``transfer_service`` and never reaches here (a transfer
    sets Paid on both legs deliberately -- the income/expense split is
    meaningless for a pair whose whole point is that one leg is each).  Every
    one of the 143 non-shadow settled rows agrees with its own type.

    The full-edit dropdown pre-hints the same rule
    (:func:`offerable_status_ids`), so reaching this refusal takes a crafted
    request or a stale form -- which is exactly the layering the route's own
    purchase-tracking guard uses, and the reason this is a designed 400 rather
    than a programming error.

    Args:
        txn: The row being settled, read for ``is_income``.
        new_status_id: The settled status the door asked for.

    Raises:
        ValidationError: When *new_status_id* is a settled status other than
            the one this row's type takes.
    """
    if new_status_id not in _mismatched_settled_status_ids(txn):
        return
    kind = "Income" if txn.is_income else "An expense"
    takes = settled_status_member(txn).value
    raise ValidationError(
        f"{kind} settles as {takes}.  Transaction {txn.id} was asked to "
        f"settle as status {new_status_id} instead."
    )


def offerable_status_ids(txn: Transaction) -> frozenset[int]:
    """Return the statuses a DOOR may offer for *txn* -- the dropdown's set.

    ``state_machine.allowed_transitions`` narrowed by this module's type rule.
    The state machine grades the STATUS and cannot see the row's type, so its
    answer for a Projected row contains both Paid and Received; exactly one of
    them is reachable for any given row, and offering the other is offering a
    control that 400s at :func:`reject_mismatched_settled_status`.

    It is the pre-hint half of one rule rather than a second rule, which is the
    shape the status dropdown was already built to (grid audit D2: "options the
    state machine would reject render disabled, so an illegal transition cannot
    be picked instead of failing as a 400 after Save").  The enforcement stays
    at the verb; this only decides what the user is shown.

    **There is deliberately NO exemption for a row already sitting in the
    mismatched status.**  A first draft carried one -- ``- {txn.status_id}``, so
    such a row could still re-submit its own status -- and it was UNREACHABLE:
    the only mismatched rows in existence are the 17 income-typed Paid transfer
    SHADOWS, and ``routes/transactions/forms`` branches a shadow to the transfer
    popover before this is called, while ``TransactionUpdateSchema`` carries no
    ``transaction_type_id`` for a PATCH to flip.  A guard whose only possible
    test cannot fail is not a guard (finding **N-184**), and this module's own
    seam deleted one for that reason at plan step X-f1c.

    Args:
        txn: The row the dropdown is being rendered for.

    Returns:
        The legal successor ids, minus the settled status this row's type does
        not take.
    """
    return allowed_transitions(txn) - _mismatched_settled_status_ids(txn)


def settles_from_entries(txn: Transaction) -> bool:
    """Return whether a settle DERIVES this row's amount from its entries.

    **The verb's own branch predicate, published** -- because three things ask
    it and two of them are not this module.  :func:`settle_transaction` picks
    its branch on it; :func:`settle_amount` values the row on it; and the
    reconcile panel decides whether to render an editable amount on it, which
    is ruling **R-FF**: a tick is correctable exactly when the verb takes its
    MANUAL branch.

    Writing the predicate at each of those three sites is the shape this arc
    exists to remove, and the failure it produces here is specific: a panel
    that offers a box the verb ignores takes a user's typed figure and drops
    it, silently, on the screen whose whole job is entering the true one.

    **Both halves are load-bearing.**  ``tracks_purchases`` alone would claim
    production's ``Kayla's Spending Money`` -- envelope-tracked, `$100.00`
    budgeted, ZERO entries -- derives its amount from entries that do not
    exist, settling it at `$0.00` and refusing the user the box that would have
    corrected it.

    Args:
        txn: The row.  Reads ``tracks_purchases`` (a template lookup for a
            template-linked row) and the ``entries`` relationship.

    Returns:
        True when a settle takes the ``sum(entries)`` branch.
    """
    return bool(txn.tracks_purchases and txn.entries)


def reject_unsettleable(txn: Transaction) -> None:
    """Refuse a row NO settle door may touch -- both rules, stated once.

    **Two refusals in one statement, because they are the same kind of rule and
    they had drifted apart** (finding **N-233**).  Every public settle surface
    in this module asks it: :func:`settle_transaction`, which would otherwise
    settle one leg of a transfer pair silently; :func:`settle_amount`, which
    would otherwise price one off the loan-payment seam and hand a caller a
    figure this module refuses to book; and :func:`settle_from_entries`, which
    asked BOTH questions in its own words and so gave the transfer rule a
    second, shorter spelling.  A verb owns its own preconditions; three verbs
    owning the same two own them once.

    **A transfer shadow** settles through ``transfer_service.update_transfer``
    so both legs and the parent move together (``CLAUDE.md`` transfer invariants
    3 and 4).

    **A soft-deleted row** must not be resurrected by a status change.  It
    values at ``Decimal("0")`` through ``effective_amount``, so settling one
    books nothing while stamping the row Paid and dated: a row that reads
    settled and is worth nothing.  The envelope branch refused this from the
    beginning and the MANUAL branch never did, and the gap was REACHABLE --
    ``get_accessible_transaction`` does not filter ``is_deleted``, so
    ``POST /transactions/<id>/mark-done`` on a soft-deleted non-envelope row
    flipped it into the settled band.  Measured on production: 102 soft-deleted
    rows, every one of them Projected, so the ledger cost is ``$0.00`` and the
    cost is to the data.

    Ordered shadow-then-deleted so a row that is both reports the rule that
    routes it somewhere else rather than the one that refuses it outright.  Both
    are column reads, so neither triggers the relationship lazy-load
    :func:`settle_from_entries`' cheap-first precondition ordering avoids.

    Args:
        txn: The row to check.  Reads ``transfer_id`` and ``is_deleted``.

    Raises:
        ValidationError: When *txn* is a transfer shadow or is soft-deleted.
    """
    if txn.transfer_id is not None:
        raise ValidationError(
            f"Transaction {txn.id} is a transfer shadow; "
            "transfers settle via transfer_service.update_transfer so both "
            "legs and the parent move together.",
        )
    if txn.is_deleted:
        raise ValidationError(
            f"Transaction {txn.id} is soft-deleted; a settle cannot "
            "resurrect a deleted row.",
        )


def settle_amount(txn: Transaction) -> Decimal:
    """Return what settling *txn* would BOOK, absent a caller-supplied actual.

    **The ONE valuation act 1 of :func:`settle_transaction` uses**, published
    because the reconcile panel must show the figure a tick will book.  A panel
    that renders ``effective_amount`` beside a verb that books something else is
    two answers to one money question, one screen apart -- and after plan step
    X-aq the verb genuinely books something else for a salary row.  The verb
    CALLS this rather than re-branching, so there is no shape in which the
    displayed figure and the booked one can drift.

    It is a PURE read: nothing here mutates, so the panel calls it per offered
    row and the verb calls it again at the settle.

    Args:
        txn: The row about to be offered or settled, still Projected.

    Returns:
        ``sum(entries)`` when :func:`settles_from_entries`, else the freshest
        derivation of the row's own amount -- the projection's live figure when
        one exists and the stored ``effective_amount`` otherwise.

    Raises:
        ValidationError: On a row no door may settle
            (:func:`reject_unsettleable`).  A shadow's value is the transfer
            service's and a deleted row's is nothing, and answering for either
            here would publish a figure :func:`settle_transaction` refuses to
            book -- which is exactly what plan step X-f2-c3 would otherwise walk
            into.
    """
    reject_unsettleable(txn)
    if settles_from_entries(txn):
        return compute_actual_from_entries(txn.entries)
    live = _freshest_amount(txn)
    return txn.effective_amount if live is None else live


def apply_requested_status(
    txn: Transaction,
    new_status_id: int,
    *,
    settled_on: date | None = None,
) -> None:
    """Apply the status a DOOR requested, and reconcile the ledger to it.

    **The route layer's ONE status entry point, and the reason it exists is
    that a route was deciding what a status change MEANS.**  The transaction
    PATCH handler and the cancel handler each called
    ``status_seam.apply_status_change`` directly -- the MECHANICS primitive,
    which verifies the transition, assigns the column and maintains the settle
    day, and deliberately does nothing else.  For "the user cancelled this row"
    the mechanics ARE the whole act.  For "the user marked this row Paid" they
    are not: settling also decides what the row is WORTH
    (:func:`settle_transaction`), and a door that flips the status without
    asking books whatever figure the row happened to be carrying.  That is
    finding **N-219** on the transaction PATCH door, and the shape of it is a
    ROUTE holding a money rule -- this arc's own root cause 1.

    So the routes stop choosing.  A door states the status the USER asked for
    and this decides what applying it means.  After plan step X-ap the only
    ``app/`` callers of the seam are this function, :func:`settle_transaction`,
    :func:`settle_from_entries`, ``credit_workflow`` (Credit and its revert,
    neither of them settled statuses) and ``transfer_service._status`` (a transfer and
    its two shadows, which settle through ``transfer_service`` by transfer
    invariants 3 and 4) -- so a FOURTH transaction settle door cannot be opened
    by reaching for the obvious primitive, which is how the third one was.

    **The ledger reconcile is here rather than at each door** for the reason
    :func:`settle_transaction` states for its own: every status change moves
    the row's posted effect (a settle posts it, a cancel reverses it), and a
    door that forgets posts nothing while reporting success.  It is reconciled
    LAST, after the caller's own field writes, so it reads the final amount and
    category rather than the pre-edit ones -- the discipline
    ``transfer_service.update_transfer`` documents.  A caller that edits
    posting-relevant fields WITHOUT changing the status still owes its own
    reconcile; there is no status change for this function to hang one on.

    Does NOT flush or commit -- the caller owns the session boundary.

    Args:
        txn: The transaction whose status the door is changing.  Must be a
            REGULAR row: a transfer shadow's status is its parent's
            (``transfer_service.update_transfer``), and the PATCH route branches
            a shadow away before it reaches here.
        new_status_id: The ``ref.statuses.id`` the door asked for -- the
            SUBMITTED status when the form carried one, else the row's own (an
            edit that changes only the settle day is an identity transition).
        settled_on: The civil day the money moved, when the door knows it, after
            the door's own :func:`app.services.status_seam.settle_day_for_status`
            reading of the submission.  ``None`` leaves the seam's rule in force.

    Raises:
        ValidationError: From an illegal transition or the seam's settle-day
            refusals.  A 400 at the route.
        PostingError: From the reconcile, on a broken ledger invariant.
            Deliberately NOT a sibling of ``ValidationError`` -- it must fail
            loud rather than render as a designed refusal.
    """
    # THE DISPATCH, and it is the whole of finding **N-219**'s fix.  Moving a
    # row INTO the settled band is a SETTLE, which decides an amount before it
    # decides a status; every other status change is the mechanics alone.  The
    # verb reconciles the ledger itself, so this arm returns rather than falling
    # through to a second reconcile of the same row.
    if enters_settled_band(txn, new_status_id):
        reject_mismatched_settled_status(txn, new_status_id)
        settle_transaction(txn, settled_on=settled_on)
        return
    # The other direction is the settle's own act undone: an envelope's
    # ``actual_amount`` was DERIVED from its entries by the settle, so leaving
    # the band takes it back.  Read BEFORE the seam and applied AFTER it, and
    # both halves of that matter -- the predicate is about the status the row
    # is LEAVING, and a refused transition must leave the row untouched (the
    # ordering ``apply_status_change`` uses for its own three refusals).  It
    # lands before the reconcile below, which reads ``effective_amount``.
    releases_derived_actual = leaves_settled_band(txn, new_status_id)
    apply_status_change(txn, new_status_id, settled_on=settled_on)
    if releases_derived_actual:
        _release_derived_actual(txn)
    posting_service.sync_transaction_postings(
        txn, settled=txn.status.is_settled,
    )


def settle_transaction(
    txn: Transaction,
    *,
    actual_amount: Decimal | None = None,
    settled_on: date | None = None,
) -> None:
    """Settle one regular transaction -- what "the money moved" MEANS for a row.

    **Ruling R-FA's verb.**  The rule lived in a ROUTE branch
    (``mutations.py:_mark_done_regular``) until plan step X-f2-c2, which needs
    the reconcile panel's tick to settle a row the SAME way the grid's Mark
    Paid does.  The two alternatives R-FA rejected were the reconcile writer
    re-stating the amount rule -- this arc's own root cause 1, on a money rule
    -- and the tick calling the mark-done HTTP endpoint per row, which has no
    atomicity and no channel for a statement date.  So the rule left the route
    and both doors call it.

    Three acts, in this order and the order matters:

    1. **The amount, which is the FRESHEST derivation of what this row is
       worth** (ruling **R-FE**, plan step X-aq), and it is TWO writes to two
       columns that mean two different things.  An envelope-tracked row WITH
       entries settles at ``sum(entries)`` (:func:`settle_from_entries`),
       because its entries ARE the record of what it cost.  Everything else:
       :func:`_reconcile_cached_amount` first refreshes ``estimated_amount``
       from the projection's own live derivation, because that column is a
       CACHE and this is the one moment the arc reconciles it (finding
       **N-224**; plan step **X-ar** gives the same reconciler its other
       triggers and deletes the read-time thread).  Then a caller-supplied
       *actual_amount* -- a figure a HUMAN read off a statement -- lands in
       ``actual_amount``, and only if it differs from what the refreshed row
       would book anyway.
       **The ``and txn.entries`` half is load-bearing**, and production says
       so: ``Kayla's Spending Money`` carries no entries at all, so settling it
       from entries unconditionally would book ``$0.00`` against its
       ``$100.00`` estimate.  **Why the rule is HERE and not at each door**: it
       decides money, three doors settle a row, and a door that picks its own
       figure is how one row comes to book two amounts depending on which
       control the user pressed.  **And why the two writes are not one**: a
       machine's recompute and a human's correction are different facts, and
       three subsystems read ``actual_amount``'s NULL-ness as meaning the
       second -- see :func:`_reconcile_cached_amount` for the three and for the
       review that sent the single-column version back.
    2. **The status**, through the single seam, so the transition is verified
       and the settle day stamped by the one door that owns both.
    3. **The ledger**, reconciled LAST, so it reads the final amount rather
       than the estimate -- the discipline ``transfer_service.update_transfer``
       documents and the reason the reconcile is not at the status flip.

    **Why act 3 is inside this verb and not left to the caller.**  Every
    settle door must reconcile, and a door that forgets posts nothing while
    reporting success -- an argument a caller can get wrong is a defect, not a
    contract (Section 8).  ``carry_forward_service`` genuinely cannot use this
    verb: it settles a BATCH and must reconcile after its ``no_autoflush``
    block so ``_emit_balanced_entry``'s flush lands on the batch's index-safe
    final state, so it keeps calling :func:`settle_from_entries` directly and
    owns its own reconcile.  That is the layering: the primitive for a batch,
    this verb for a settle, and since plan step X-ap the batch is the ONLY
    non-caller -- the full-edit Status dropdown was the other one, and it was a
    defect rather than a layering choice.

    **It takes a settle DAY since plan step X-f2-c2's money commit**, which is
    the caller ruling **R-EC** was waiting for: the reconcile tick knows the day
    the statement covers, and stamping the user's today instead would date the
    money to when they got round to reconciling.  ``None`` leaves the seam's own
    rule in force -- the user's today on first entry to the settled band,
    preserved on re-entry -- which is what every other door means.

    Does NOT commit -- the caller owns the session boundary.

    **The shadow refusal is THIS function's, and the first draft borrowed it
    from a branch a shadow never reaches.**  That draft said
    :func:`settle_from_entries` refuses one by precondition -- true of that
    helper, and unreachable here: a shadow carries no ``template_id`` and no
    ``is_envelope``, so ``tracks_purchases`` is False and a shadow always takes
    the MANUAL branch, where nothing looked at ``transfer_id``.  An adversarial
    review ran it and settled one leg of a pair: expense shadow Paid, income
    shadow still Projected, parent transfer still Projected -- ``CLAUDE.md``
    transfer invariants **3** and **4** broken in one call, and silently,
    because ``sync_transaction_postings`` returns ``[]`` for a shadow so the
    ledger stays flat while the grid shows one leg settled.  No caller can
    reach it today (``mark_done`` routes a shadow to ``_mark_done_shadow``
    first), but this is a PUBLIC verb documented as what a door calls, and
    X-f2-c3 puts transfer shadows in the reconcile panel.  A verb owns its own
    preconditions.

    Args:
        txn: The transaction to settle.  Must be a REGULAR row -- a shadow is
            REFUSED here, because a transfer settles through
            ``transfer_service.update_transfer`` so both legs and the parent
            move together.
        actual_amount: What the row actually cost, when the CALLER knows --
            i.e. a figure a human supplied.  ``None`` does NOT mean "keep the
            stored amount": it means "nobody typed one", and act 1 then asks
            :func:`_freshest_amount` what the projection is holding.  **No form
            submits it today** -- measured: ``name="actual_amount"`` appears
            only on the full-edit and full-create templates, which PATCH and
            POST the transaction rather than posting mark-done, and no JS
            composes it -- so ``MarkDoneSchema``'s field is a channel the UI
            has never used.  Ruling **R-FB** is what gives it a first real
            caller: a BILL's tick may correct its amount, prefilled, and an
            envelope's close may not.
        settled_on: The civil day the money moved, when the CALLER knows it --
            the reconcile tick's statement date.  ``None`` derives it through
            the seam.  A day in the future or one supplied for a non-settled
            status is refused there (rulings **R-EJ** / finding **N-183**);
            the tick cannot reach either, because an assertion's own
            ``observed_on`` is already refused in the future by
            ``anchor_service``.

    Raises:
        ValidationError: On a transfer shadow or a soft-deleted row, from the
            envelope branch's remaining preconditions, from an illegal
            transition, or from the seam's settle-day refusals.  All are 400s at
            the route.
        PostingError: From act 3, on a broken ledger invariant.  Deliberately
            NOT a sibling of ``ValidationError`` -- it must fail loud rather
            than render as a designed refusal.
    """
    # Checked FIRST and before any mutation, so a refused call leaves the row
    # untouched -- the ordering ``status_seam.apply_status_change`` uses for
    # its own three refusals, and for the same reason.
    reject_unsettleable(txn)

    if settles_from_entries(txn):
        settle_from_entries(txn, settled_on=settled_on)
    else:
        # Act 1a: RECONCILE THE CACHE, before the seam.  It must be before:
        # the projection's own rule is Projected-only, so ``live_projected_net``
        # drops a row the moment its status leaves that band and asking after
        # the flip always answers "nothing fresher".
        _reconcile_cached_amount(txn)
        # Act 1b: the HUMAN's figure, and only a human's reaches this column.
        # The echo rule: a figure equal to what the row would book anyway is not
        # a correction, and writing it would populate a column that is NULL on
        # every uncorrected row -- destroying the only signal that says a human
        # typed one, which is what ruling R-FB's own production measurement is
        # made of ("11 of 93 settled bills carry a hand-typed correction").
        # That half is load-bearing rather than tidy: the reconcile panel
        # PREFILLS its amount box, so an untouched tick submits the figure the
        # row would have booked anyway.
        #
        # **The panel is the ONLY caller that reaches it**, and saying so
        # replaces a claim this comment used to make that plan step X-ap turned
        # out NOT to be true.  It predicted the full-edit door would thread its
        # submitted ``actual_amount`` into this parameter; X-ap instead lets the
        # PATCH handler's own ``setattr`` loop write that column and calls this
        # verb with no figure, because two writers of one column in one request
        # is the shape this arc removes.  A justification naming a caller that
        # does not exist is the defect ruling R-EC deleted a whole parameter
        # for; it is corrected here rather than left to read as coverage.
        #
        # **It is compared against the REFRESHED ``effective_amount``**, which
        # is why act 1a runs first: comparing a prefill taken from
        # :func:`settle_amount` against a cache that recompute had already
        # superseded made the rule inert for exactly the rows act 1a is about.
        correction = (
            actual_amount
            if actual_amount is not None
            and actual_amount != txn.effective_amount
            else None
        )
        apply_status_change(
            txn, settled_status_id(txn), settled_on=settled_on,
        )
        # Applied AFTER the seam so act 3 below reads the final actual amount
        # rather than the pre-settle estimate (the 2.8b HIGH, forward
        # direction).
        if correction is not None:
            txn.actual_amount = correction

    posting_service.sync_transaction_postings(
        txn, settled=txn.status.is_settled,
    )


def _reconcile_cached_amount(txn: Transaction) -> None:
    """Refresh *txn*'s cached amount from its own live derivation.

    **This is plan step X-ar's reconciler, with ONE trigger.**  Finding
    **N-224** is that ``transactions.estimated_amount`` is a CACHE of a
    derivation with nothing that ever writes it back:
    :func:`app.services.income_service.live_projected_net` recomputes a
    salary-linked paycheck at READ time and discards the answer, so every
    balance surface shows the live figure while the stored column keeps a value
    its own inputs have moved past.  X-ar deletes the read-time thread outright
    and keeps the stored amount true by reconciling it on input change and at
    deploy; this reconciles it at the one moment the arc has reached, the
    settle, and writes the SAME column X-ar's reconciler will write.

    **Why the cache and not ``actual_amount``**, which is what a first version
    of ruling R-FE wrote and what an adversarial review sent back.  Three
    subsystems read that column's NULL-ness as meaning *a human entered a
    fact*, and a machine write is indistinguishable from theirs afterwards:
    ``income_service`` says a settled income row's actual is "a historical
    fact, never a recomputable projection"; ``spending_analysis`` says only "a
    settled row with an explicitly entered, different actual" can produce a
    surprise, so a refresh manufactures one; and the grid strikes through
    ``estimated_amount`` beside ``actual_amount`` exactly when they differ,
    rendering a `$2,100` the user never saw against the `$2,105` every screen
    had already shown them.  The write is also permanent -- the row leaves
    ``live_projected_net``'s Projected-only candidate set at the settle, so the
    stale estimate could never be repaired afterwards and X-ar's own reconciler
    could not tell this write from a real correction.

    ``is_override`` is deliberately NOT set: the flag means a human chose this
    figure, and the recurrence engine's own ``resolve_conflicts`` sets it False
    while rewriting ``estimated_amount`` for the same reason.  Nothing else
    moves -- the row's template, period and scenario are untouched, so the
    partial UNIQUE index over those three cannot be disturbed, and
    ``ck_transactions_estimated_amount`` (``>= 0``) is satisfied by a figure the
    paycheck engine has already rounded.

    Mutates in place and does NOT flush or commit.

    Args:
        txn: The row about to settle, still in its pre-settle status.  Must be
            asked BEFORE the status flip: the projection's rule is
            Projected-only, so after it there is never anything fresher.
    """
    live = _freshest_amount(txn)
    if live is not None:
        txn.estimated_amount = live


def _freshest_amount(txn: Transaction) -> Decimal | None:
    """Return the amount a settle should book, or ``None`` to leave the column.

    **Ruling R-FE's rule, and it exists because the app holds TWO answers to
    what a projected row is worth** (finding **N-224**).
    ``transactions.estimated_amount`` is a CACHE of a derivation:
    :func:`app.services.income_service.live_projected_net` recomputes a
    salary-linked paycheck at READ time and writes nothing back, so every
    balance surface shows the live figure while every settle door used to book
    the stored one.  A settle for a figure the projection was not holding moves
    the projected end balance by the difference -- which is exactly the
    invariant ruling R-DH (c) states and plan step X-f3 is ship-gated on.

    So this asks the projection's OWN override map
    (:func:`app.services.cash_ledger.live_amount_overrides`) rather than
    restating which rows have a live value.  It is the same expression
    :func:`app.services.cash_ledger.income_amount` evaluates one tier down --
    "the override when present, else ``effective_amount``" -- asked for one row
    instead of reduced over a period, and plan step **X-ar** deletes both by
    making the stored amount authoritative.

    **It costs nothing on the rows it does not apply to.**  Both halves of the
    override map filter their candidates in Python first and return an empty
    dict with NO query: the loan half wants ``transfer_id IS NOT NULL``, which
    :func:`settle_transaction` has already refused, and the salary half wants a
    Projected, non-overridden, template-linked income row.  An expense, an
    ad-hoc row, an already-settled row and a manually-overridden paycheck each
    leave here after two list comprehensions.

    **A row carrying an ``actual_amount`` is NOT a candidate, and that guard is
    a money fix rather than a shortcut.**  What this refreshes is a CACHE, and
    the cache is ``estimated_amount``; ``actual_amount`` is a fact a human
    entered.  Without the guard, a Projected salary row whose actual the owner
    typed by hand is compared against the recompute and OVERWRITTEN at settle --
    the app deleting the user's own figure and substituting its estimate.  It
    was reachable: ``is_override`` is the only other thing excluding such a row
    from the override map, and ``routes/transactions/mutations`` sets that flag
    only when ``estimated_amount`` or ``pay_period_id`` was submitted, so
    editing the actual ALONE leaves it False.  An invariant of this module
    cannot rest on a bookkeeping flag another module sets for another reason.

    **It compares against ``estimated_amount``, not ``effective_amount``**, for
    the same reason: past the guard above they are equal, and naming the column
    that IS the cache says what the comparison means.

    Args:
        txn: The row about to settle, still in its pre-settle status.  Read for
            its account, scenario, and the fields the override map's candidate
            filters test; not mutated.

    Returns:
        The live amount when one exists and disagrees with the cache, else
        ``None`` -- meaning "nothing fresher than what the row already says".
    """
    if txn.actual_amount is not None:
        return None
    live = live_amount_overrides(
        txn.account, txn.scenario_id, [txn],
    ).get(txn.id)
    if live is None or live == txn.estimated_amount:
        return None
    return live


def settle_from_entries(
    txn: Transaction, *, settled_on: date | None = None,
) -> None:
    """Settle a tracked-envelope transaction at sum(entries).

    The envelope PRIMITIVE: the three writes an envelope's close needs --
    ``status_id``, the settle day, and ``actual_amount`` -- as a single source
    of truth.  It is reached two ways, and the split is
    :func:`settle_transaction`'s docstring: every DOOR settles through that
    verb, which chooses this branch when the row is envelope-tracked and has
    entries, while ``carry_forward_service._execute`` calls this directly
    because it settles a batch and owes its ledger reconcile a different
    moment (see ``docs/carry-forward-aftermath-design.md`` Option F).

    **An EMPTY entry list settles at ``Decimal("0")`` here and at the row's own
    estimate through :func:`settle_transaction`, and that difference is RULED
    rather than incidental** (ruling **R-FJ**, finding **N-230**).  The two are
    answering different questions.  This primitive's caller,
    ``carry_forward_service``, has ALREADY relocated the unspent money -- it
    rolls ``estimated - sum(entries)`` into the next period's row and then
    settles the source at what was spent -- so booking the estimate here would
    count the same dollars twice.  A DOOR has relocated nothing: pressing Paid
    on an envelope says the budget is finished, and booking ``$0.00`` would
    record that no money left the account while marking the row Paid.  The
    discriminator is therefore the CALLER's act, not the row, which is why the
    rule cannot live in a shared branch and why :func:`settle_transaction` gates
    its entries branch on ``and txn.entries``.

    Production carries both signatures, which is how the difference was found:
    of 9 settled entry-less envelopes, 8 were booked at their estimate
    (`$794.79`) through a door and 1 -- ``Kayla's Spending Money``, `$157.60` --
    at `$0.00` through carry-forward.

    Effect on *txn* (in place):
      - ``actual_amount`` is set to ``sum(e.amount for e in txn.entries)``,
        which is ``Decimal("0")`` when ``txn.entries`` is empty -- see the
        ruling above.
      - ``status_id`` is set to ``DONE`` for expense transactions and
        ``RECEIVED`` for income transactions, matching the display
        convention used by ``app/routes/transactions.py:mark_done``.
      - ``settled_on`` is *settled_on* when the caller supplied one, else the
        status seam's own rule: the user's today on the first entry into a
        settled status, preserved on a re-settle.

    **The day parameter is BACK, and this time it has a caller** -- the
    reconcile panel's envelope close, which knows the statement's date and must
    not date the money to the day the user got round to reconciling.  Ruling
    **R-EC** deleted the previous one (``paid_at``) because NO call site passed
    it and its docstring named a caller that never had; the test rule 13 sets is
    a real caller, not a plausible one, and :func:`settle_transaction` now
    threads this from ``reconcile_service``.  ``carry_forward_service`` still
    passes nothing, which is correct: a carried-forward envelope settles today,
    not on some statement's day.

    **It took an explicit ``paid_at`` until plan step X-f1, and that parameter
    was DEAD** (ruling R-EC, rule 13).  Its docstring named the carry-forward
    envelope branch as the caller that supplied it;
    ``carry_forward_service._execute`` calls this with no such argument and
    always has.  Measured across ``app/``: zero call sites passed it.  A knob
    with no caller and a false justification is exactly the speculative
    flexibility the coding standards forbid, so it went rather than being
    renamed -- the seam owns the day, and a caller that genuinely means "this
    settled on a different day" corrects it on the row afterwards (ruling
    R-ED).

    The function does NOT flush or commit -- the caller owns the
    session boundary so the settlement can participate in a larger
    atomic operation (e.g. the carry-forward batch).

    Preconditions (defensively validated, not assumed):

      1. Neither a transfer shadow nor a soft-deleted row
         (:func:`reject_unsettleable`).  Both rules are shared with the two
         other public settle surfaces rather than restated here -- this
         helper's own wording of the transfer rule was a SECOND spelling of
         it (finding **N-233**).
      2. ``txn.tracks_purchases`` is True -- the row is purchase-tracked,
         either via its template's ``is_envelope`` flag or, for an ad-hoc
         row, its own ``is_envelope`` column.  Envelope semantics are the
         contract this helper relies on; calling on a non-tracked row is
         a programming error and surfaces as a ``ValidationError``.
      3. ``txn.status`` is mutable (``status.is_immutable`` is False).
         The only mutable status in the current schema is ``Projected``;
         settling a row that is already Paid, Received, Cancelled,
         Credit, or Settled is meaningless and indicates a caller bug.

    Args:
        txn: The Transaction to settle.  Must be attached to the
            current SQLAlchemy session so the entries relationship
            resolves correctly.
        settled_on: The civil day the money moved, when the caller knows it.
            ``None`` leaves the seam to derive it.  Passed straight through --
            every refusal that guards it (a future day, a day beside a
            non-settled status, a ``datetime``) is the seam's, so this helper
            adds no second opinion about a value it does not own.

    Raises:
        ValidationError: If any precondition is violated.  The error
            message names the txn ID and the specific violated rule
            so the route layer can surface an actionable message.

    Returns:
        None.  Mutations are applied in place; the caller is
        responsible for committing the surrounding transaction.
    """
    # Preconditions are checked cheap-first so an early bailout never
    # triggers an autoflush of pending mutations on the txn.  Column
    # checks come before relationship accesses (which lazy-load and
    # therefore autoflush) to keep the failure path side-effect-free.
    # The shared pair reads two columns, so it belongs at the front.
    reject_unsettleable(txn)
    # Resolved purchase-tracking check: covers template-generated rows
    # (template.is_envelope) and ad-hoc rows (own is_envelope flag).  For
    # an ad-hoc row tracks_purchases reads a column only -- no relationship
    # access -- so the cheap-first / autoflush-safe ordering holds; for a
    # template row it accesses the template exactly as the prior guard did.
    if not txn.tracks_purchases:
        raise ValidationError(
            f"Transaction {txn.id} is not envelope-tracked; "
            "settle_from_entries requires individual purchase tracking "
            "(template.is_envelope, or is_envelope on an ad-hoc row).",
        )
    # Guard against settling an already-finalised row.  ``status`` may
    # be unloaded if the caller passed a detached or freshly-constructed
    # transaction; treat the missing relationship as a precondition
    # violation rather than silently coercing it to mutable.
    if txn.status is None or txn.status.is_immutable:
        status_label = (
            txn.status.name if txn.status is not None else "<unset>"
        )
        raise ValidationError(
            f"Transaction {txn.id} has an immutable status "
            f"({status_label!r}); settle_from_entries requires a "
            "mutable status (Projected).",
        )

    new_status_id = settled_status_id(txn)

    # Route the status mechanics (transition check, status_id, settled_on,
    # status expire) through the single seam so this helper cannot drift from
    # the manual mark-done / PATCH / credit paths.  The day is the caller's
    # when it has one -- the reconcile tick's statement date -- and otherwise
    # None, which is the seam's "stamp the user's today on entering the settled
    # status and preserve an existing one".
    apply_status_change(txn, new_status_id, settled_on=settled_on)
    # ``compute_actual_from_entries`` returns Decimal("0") on an empty
    # list, which is the carry-forward "no spend, full rollover" case.
    txn.actual_amount = compute_actual_from_entries(txn.entries)

    log_event(
        logger, logging.INFO,
        EVT_TRANSACTION_SETTLED_FROM_ENTRIES, BUSINESS,
        "Envelope transaction settled at sum(entries)",
        # PayPeriod owner is the canonical user_id source for Transaction
        # rows (Transaction has no direct user_id column).  pay_period
        # is already loaded by the caller so this read does not trigger
        # an autoflush.
        user_id=txn.pay_period.user_id,
        transaction_id=txn.id,
        new_status_id=new_status_id,
        actual_amount=str(txn.actual_amount),
        settled_on=txn.settled_on.isoformat(),
    )
