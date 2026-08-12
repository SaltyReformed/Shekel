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

**THREE doors settle a transaction, not two, and only two of them are on this
verb.**  Saying so here rather than letting the next leaf discover it: the
grid's Mark Paid calls the verb, the reconcile panel's tick calls it since plan
step X-f2-c2, and ``routes/transactions/mutations._apply_regular_update`` -- the
Status dropdown on the full-edit popover -- does NOT.  That third door flips
the status through the seam and reconciles, but never consults the entries, so
an envelope-tracked row with a `$25` purchase against a `$400` estimate books
`$25` through Mark Paid and **`$400`** through the dropdown, from two controls
in the same card.  Measured on both this tree and the merge-base, so it is
PRE-EXISTING and neither caused nor worsened here; ruling **R-FA** named "two
route branches" and there were three.  It is a live money defect with its own
ledger row and its own step, because routing it onto this verb CHANGES what a
full-edit Save books and so cannot ride inside a zero-money commit.

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
from app.services.status_seam import apply_status_change
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
    log_event,
)

logger = logging.getLogger(__name__)


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
    if txn.is_income:
        return ref_cache.status_id(StatusEnum.RECEIVED)
    return ref_cache.status_id(StatusEnum.DONE)


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


def reject_transfer_shadow(txn: Transaction) -> None:
    """Refuse a transfer shadow -- the rule, stated once for both doors.

    A transfer settles through ``transfer_service.update_transfer`` so both legs
    and the parent move together (``CLAUDE.md`` transfer invariants 3 and 4).
    Two PUBLIC functions here have to know that -- :func:`settle_transaction`,
    which would otherwise settle one leg silently, and :func:`settle_amount`,
    which would otherwise price one off the loan-payment seam and hand a caller
    a figure this module refuses to book.  A verb owns its own preconditions;
    two verbs owning the same one own it once.

    Args:
        txn: The row to check.

    Raises:
        ValidationError: When *txn* is a transfer shadow.
    """
    if txn.transfer_id is not None:
        raise ValidationError(
            f"Transaction {txn.id} is a transfer shadow; "
            "transfers settle via transfer_service.update_transfer so both "
            "legs and the parent move together.",
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
        ValidationError: On a transfer shadow
            (:func:`reject_transfer_shadow`).  A shadow's value is the transfer
            service's, and answering here would publish a figure
            :func:`settle_transaction` refuses to book -- which is exactly what
            plan step X-f2-c3 would otherwise walk into.
    """
    reject_transfer_shadow(txn)
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
    neither of them settled statuses) and ``_transfer_status`` (a transfer and
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
    apply_status_change(txn, new_status_id, settled_on=settled_on)
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
    this verb for a settle.  It is not the ONLY non-caller -- the full-edit
    Status dropdown is a third settle door that is on neither, which is a
    defect rather than a layering choice; see this module's docstring.

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
        ValidationError: On a transfer shadow, from the envelope branch's
            preconditions, from an illegal transition, or from the seam's
            settle-day refusals.  All are 400s at the route.
        PostingError: From act 3, on a broken ledger invariant.  Deliberately
            NOT a sibling of ``ValidationError`` -- it must fail loud rather
            than render as a designed refusal.
    """
    # Checked FIRST and before any mutation, so a refused call leaves the row
    # untouched -- the ordering ``status_seam.apply_status_change`` uses for
    # its own three refusals, and for the same reason.
    reject_transfer_shadow(txn)

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
        # row would have booked anyway, and plan step X-ap routes a full-edit
        # form here that submits ``actual_amount`` on EVERY save.
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

    Effect on *txn* (in place):
      - ``actual_amount`` is set to ``sum(e.amount for e in txn.entries)``,
        which is ``Decimal("0")`` when ``txn.entries`` is empty.  Empty
        entries on an envelope row settle at zero spend (the carry-forward
        branch then folds the full estimated amount into the next
        period's canonical row).
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

      1. ``txn.is_deleted`` is False.  Soft-deleted rows must not be
         resurrected via a status change.
      2. ``txn.tracks_purchases`` is True -- the row is purchase-tracked,
         either via its template's ``is_envelope`` flag or, for an ad-hoc
         row, its own ``is_envelope`` column.  Envelope semantics are the
         contract this helper relies on; calling on a non-tracked row is
         a programming error and surfaces as a ``ValidationError``.
      3. ``txn.transfer_id`` is None.  Transfer shadows must settle
         through ``app.services.transfer_service.update_transfer`` so
         both shadow legs and the parent transfer stay in sync (see
         transfer invariants in CLAUDE.md).
      4. ``txn.status`` is mutable (``status.is_immutable`` is False).
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
    if txn.is_deleted:
        raise ValidationError(
            f"Transaction {txn.id} is soft-deleted; "
            "settle_from_entries cannot resurrect deleted rows.",
        )
    if txn.transfer_id is not None:
        raise ValidationError(
            f"Transaction {txn.id} is a transfer shadow; "
            "transfers settle via transfer_service.update_transfer.",
        )
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
