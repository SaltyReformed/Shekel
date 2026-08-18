"""
Shekel Budget App -- Transaction Service: what SETTLING a row means

Ruling **R-FA**'s verb and everything it is made of: the branch predicate that
says whether a row's amount is DERIVED from its entries, the two preconditions
no settle door may skip, the valuation the panel displays and the verb books,
the predicate that tells a human's correction from the panel's own prefill, and
the envelope primitive underneath it all.

**Two settle entry points, and the difference is deliberate.**
:func:`settle_transaction` is what settling a row MEANS -- amount and status and
ledger together.  :func:`settle_from_entries` is the envelope PRIMITIVE
underneath it, and it stays public for ``carry_forward_service``, which settles
a BATCH and must reconcile the ledger after its ``no_autoflush`` block, so it
owns that act itself.

**The rule this module holds is that a door states an INTENT and the service
decides what it costs.**  Every public function here is one of those decisions,
and none of them is reachable from a template or a form field.

Architecture:
  - No Flask imports.  Receives ORM objects, mutates them, and
    raises domain exceptions on precondition violation.
  - All monetary arithmetic uses ``Decimal``.  The entry sum a settle books
    is ``row_valuation.purchases_total`` -- it was
    ``entry_service.compute_actual_from_entries`` until plan step X-au-c3
    deleted that helper along with the column it was named for.
  - Does NOT flush or commit -- the caller owns the transaction
    boundary so the helper can safely participate in larger atomic
    operations (e.g. the carry-forward batch in Phase 4).  The ledger
    reconcile :func:`settle_transaction` runs FLUSHES; it still does not
    commit.
"""

import logging
from datetime import date
from decimal import Decimal

from app.enums import SettlementBasisEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.services import posting_service
from app.services.cash_ledger import (
    amount_basis,
    contribution_of,
    live_override,
)
from app.services.row_valuation import purchases_total
from app.services.status_seam import (
    Settlement,
    apply_status_change,
    honoured_correction,
    recorded_settlement,
)
from app.services.transaction_service._status_rules import settled_status_id
from app.utils.balance_predicates import is_identity_move, settled_status_ids
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSACTION_SETTLED_FROM_ENTRIES,
    log_event,
)

logger = logging.getLogger(__name__)


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
    values at ``Decimal("0")`` through the valuation gate, so settling one
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


def fixed_settle_amount(txn: Transaction) -> "Decimal | None":
    """Return what a settle books from the row's OWN RECORDS, or ``None``.

    **The pure prefix of :func:`settle_amount`**, and the sibling in shape of
    :func:`app.services.row_valuation.fixed_contribution`: the arms that answer
    before any producer is consulted, so a caller that only wants those arms
    does not run the paycheck engine to find out there was nothing to run it
    for.  ``None`` means neither arm applies and the row's amount must be
    RESOLVED, which is what :func:`settle_amount` goes on to do.

    Two arms, in this order:

      * a row whose figure comes from its PURCHASES books their sum
        (:func:`settles_from_entries`, ruling **R-FF**) -- and it takes
        precedence, because such a row honours no correction at all;
      * a row still holding a ``corrected`` record books THAT
        (:func:`~app.services.status_seam.honoured_correction`, plan step
        X-au-c3).  A revert keeps what moved, so a row the user reverted in
        order to edit re-books the figure they read off their statement.

    **It is published because a SCREEN needs the second arm** (developer,
    2026-08-17).  A reverted row is worth its PLAN -- that is what the balance
    counts and what the grid shows -- while a re-settle books its retained
    correction, so the two numbers genuinely differ and the second one was
    visible on no surface but the reconcile panel.  The developer's rule is that
    whatever will be booked against the account is shown wherever the row is
    shown; :func:`retained_settle_amounts_by_id` is that map, and it is built
    from THIS function rather than from a second reading of the same columns, so
    what a screen promises and what a tick books cannot drift.

    Pure and cheap: a status test, a relationship read for an entries row, and
    one ``ref_cache`` lookup.  No producer runs, so a whole grid costs no
    paycheck engine.

    Args:
        txn: The row being priced.

    Returns:
        The figure the row's own records answer, or ``None`` when they do not.
    """
    if settles_from_entries(txn):
        return purchases_total(txn.entries)
    return honoured_correction(txn)


def retained_settle_amounts_by_id(rows) -> "dict[int, Decimal | None]":
    """Return ``{transaction_id: the figure a re-settle would RE-BOOK}``.

    The display map for the one figure a screen could not otherwise show (plan
    step X-au-c3, developer 2026-08-17).  A row that has been reverted out of
    the settled band keeps what it recorded, and a re-settle honours it -- so
    the grid showing a ``$500.00`` plan while marking it paid books ``$245.32``
    is two answers to one money question, which is the defect the reconcile
    panel's own prefill was fixed for and which the grid and the full-edit
    popover still had.

    ``None`` for every row where no such gap exists: a row already SETTLED
    (:func:`app.services.row_valuation.settled_amounts_by_id` is that row's
    map, and the screen shows the recorded figure already), a row that settles
    from its purchases (its entries are on screen and are the figure), and a row
    holding no ``corrected`` record (its plan IS what a settle would book).  So
    a non-``None`` value means exactly "this row will book something other than
    what you can see", which is the only case worth drawing.

    **It asks :func:`~app.services.status_seam.honoured_correction` rather than
    :func:`fixed_settle_amount`, and the difference is a false caption on a
    large class of rows.**  That function's FIRST arm is the purchases sum, so
    delegating to it answered a figure for every unsettled ENVELOPE carrying a
    purchase -- a `$400` envelope with one `$25` entry came back `$25.00`, and
    every grid surface drew it under the words "the figure recorded before this
    row was set back to Projected".  That row has never settled and records
    nothing; the caption was false, and the cell already showed `25 / 400` two
    lines up.  A retained CORRECTION is the one thing this map is about, and
    ``honoured_correction`` is the arm that answers it (found by adversarial
    review, 2026-08-18; the paragraph above already said this and the code did
    not do it).

    Args:
        rows: The rows a surface is about to render.

    Returns:
        ``{transaction_id: Decimal | None}`` covering every row.
    """
    settled = settled_status_ids()
    return {
        row.id: (
            None if row.status_id in settled else honoured_correction(row)
        )
        for row in rows
    }


def settle_amount(txn: Transaction) -> Decimal:
    """Return what settling *txn* would BOOK, absent a caller-supplied actual.

    **The ONE valuation act 1 of :func:`settle_transaction` uses**, published
    because the reconcile panel must show the figure a tick will book.  A panel
    that renders the row's own figure beside a verb that books something else is
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
        one exists and the row's own contribution otherwise.

    Raises:
        ValidationError: On a row no door may settle
            (:func:`reject_unsettleable`).  A shadow's value is the transfer
            service's and a deleted row's is nothing, and answering for either
            here would publish a figure :func:`settle_transaction` refuses to
            book -- which is exactly what plan step X-f2-c3 would otherwise walk
            into.
    """
    reject_unsettleable(txn)
    fixed = fixed_settle_amount(txn)
    if fixed is not None:
        return fixed
    return _manual_branch_figures(
        txn, amount_basis(txn.account.user_id, txn.scenario_id),
    )[0]


def _manual_branch_figures(
    txn: Transaction, basis,
) -> "tuple[Decimal, Decimal | None]":
    """Return the MANUAL branch's figure AND the live one behind it.

    **Split out of :func:`settle_amount` so ONE settle resolves ONE basis**
    (developer ruling, 2026-08-17).  The public accessor builds a basis per
    call, which is right for the reconcile panel's per-row offer and wrong
    inside the verb: :func:`settle_transaction` needs the same figure and then
    needs the same basis again for :func:`_reconcile_cached_amount`, so calling
    the public form there ran the profile lookup, the loan resolve and the
    paycheck engine twice over one row -- and a third time when a caller
    supplied a figure and the echo rule asked again.  That is the shape plan
    step X-au-c2b closed WITHIN a call (findings **N-268** / **N-269**), and it
    had grown back one tier up.

    **It answers BOTH figures because one resolution produces both**, and the
    alternative was resolving :func:`_freshest_amount` twice per settle -- once
    for what the settle books and once for the cache refresh that writes the
    same answer.  Returning the pair keeps ONE statement of the branch's rule,
    which is what :func:`settle_amount` exists to guarantee: the figure the
    reconcile panel OFFERS and the figure the verb BOOKS cannot drift, because
    they come from this function or from nowhere.

    Args:
        txn: The row being priced, still in its pre-settle status.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`.

    Returns:
        ``(booked, live)``: what a settle would book -- the projection's live
        figure when one exists, else the row's own contribution -- and the live
        figure itself, or ``None`` when nothing supersedes the row's cache.
    """
    live = _freshest_amount(txn, basis)
    booked = contribution_of(txn, basis) if live is None else live
    return booked, live


def _is_correction(
    txn: Transaction, submitted: "Decimal | None", booked: Decimal,
) -> bool:
    """Return whether *submitted* is a HUMAN's figure this settle would BOOK.

    **The verb's own act-1b decision, and it is finding N-231's fix.**  Three
    doors may hand a settle a figure and only some of them are corrections: a
    row whose amount is DERIVED from its entries ignores one outright
    (:func:`settles_from_entries`), and a figure equal to what the row would
    book anyway is the panel's own prefill coming back untouched.

    **It was PUBLIC until the developer's 2026-08-17 ruling**, so the reconcile
    writer could ask what the settle would count.  It asked BEFORE the settle
    and re-resolved the row's amount to make its comparison, which meant one
    ticked row paid for the derivation twice and the count was a second answer
    to a question the write had already decided.  :func:`settle_transaction`
    RETURNS that answer now -- the shape ``transfer_service._settle.settle``
    already had -- so the rule has one caller and needs no door.

    **What it replaced was a reading of the COLUMN, and that over-reported by
    construction.**  ``reconcile_service`` counted rows whose ``actual_amount``
    changed -- but an envelope's close always writes that column
    (:func:`settle_from_entries` sets it to ``sum(entries)``), so every envelope
    tick incremented a count whose whole purpose is to tell a human's figure
    from a machine's.  Measured before the fix: a probe settling one envelope
    with no correction submitted logged ``corrected_count: 1``.  Ruling
    **R-FB**'s production measurement ("11 of 93 settled bills carry a
    hand-typed correction") is made of exactly this signal.

    **It is asked BEFORE the settle and that is the only moment it has an
    answer**, because :func:`settle_transaction` mutates the very figures it
    compares.  Asking it there is exact rather than approximate: past
    :func:`_reconcile_cached_amount`, the row's contribution IS
    :func:`settle_amount`'s pre-settle answer, in all three shapes -- a row
    carrying its own ``actual_amount`` (the refresh is guarded off and both
    read that figure), a row whose live derivation supersedes its cache (the
    refresh writes exactly what this returned), and a row with nothing fresher
    (neither moves).  The historical defect this is NOT is comparing against
    the pre-refresh contribution, which is a cache the recompute has
    already superseded -- that made the echo rule inert for precisely the rows
    the refresh is about.

    Args:
        txn: The row about to settle, still in its pre-settle status.
        submitted: The figure a caller supplied, or ``None`` when nobody typed
            one.
        booked: What this settle would book absent a correction, resolved once
            by :func:`_manual_branch_figures` and threaded here rather than
            re-derived.

    Returns:
        True when the verb will RECORD *submitted* as a ``corrected``
        settlement.
    """
    return (
        submitted is not None
        and not settles_from_entries(txn)
        and submitted != booked
    )


def settle_transaction(
    txn: Transaction,
    *,
    submitted: Decimal | None = None,
    settled_on: date | None = None,
) -> bool:
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
       worth** (ruling **R-FE**, plan step X-aq).  An envelope-tracked row WITH
       entries settles at ``sum(entries)`` (:func:`settle_from_entries`),
       because its entries ARE the record of what it cost -- and its record
       stores NO figure at all, for that reason.  Everything else resolves its
       figure ONCE (:func:`_manual_branch_figures`) and does two things with the
       one answer: :func:`_reconcile_cached_amount` refreshes
       ``estimated_amount`` where the projection's live derivation supersedes
       it, because that column is a CACHE and this is the one moment the arc
       reconciles it (finding **N-224**; plan step **X-ar** gives the same
       reconciler its other triggers and deletes the read-time thread), and the
       settle RECORDS what it booked.  A caller-supplied *submitted* figure -- a
       figure a HUMAN read off a statement -- is what the record states instead,
       and only if it differs from what the row would book anyway.
       **The ``and txn.entries`` half is load-bearing**, and production says
       so: ``Kayla's Spending Money`` carries no entries at all, so settling it
       from entries unconditionally would book ``$0.00`` against its
       ``$100.00`` estimate.  **Why the rule is HERE and not at each door**: it
       decides money, three doors settle a row, and a door that picks its own
       figure is how one row comes to book two amounts depending on which
       control the user pressed.  **And why the plan and the record are two
       columns**: a machine's recompute and a human's correction are different
       facts, and until plan step X-au-c3 three subsystems read one column's
       NULL-ness to tell them apart -- see :func:`_reconcile_cached_amount` for
       the three, and ``settled_basis_id`` for what says it now.
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
        submitted: What the row actually cost, when the CALLER knows -- i.e. a
            figure a human supplied.  ``None`` does NOT mean "keep the stored
            amount": it means "nobody typed one", and the settle then RECORDS
            what it resolved, on the ``derived`` basis.  Ruling **R-FB** is what
            gives the parameter its real callers: a BILL's tick may correct its
            amount, prefilled, and an envelope's close may not -- the envelope
            branch ignores a submitted figure outright, and the full-edit door
            refuses one on such a row rather than dropping it silently.
            **It is named for what it IS rather than for a column** since plan
            step X-au-c3: it was ``actual_amount``, and the column of that name
            is gone -- a settled row records what moved in ``settled_amount``
            beside a ``settled_basis_id`` that says whether this figure is why.
        settled_on: The civil day the money moved, when the CALLER knows it --
            the reconcile tick's statement date.  ``None`` derives it through
            the seam.  A day in the future or one supplied for a non-settled
            status is refused there (rulings **R-EJ** / finding **N-183**);
            the tick cannot reach either, because an assertion's own
            ``observed_on`` is already refused in the future by
            ``anchor_service``.

    Returns:
        Whether this settle booked a HUMAN's figure -- what the reconcile
        writer counts (finding **N-231**).  **Answered by the act itself since
        the developer's 2026-08-17 ruling**, which is the shape
        ``transfer_service._settle.settle`` already had one table over: the
        caller used to ask :func:`_is_correction` separately, which re-resolved
        the row's amount to make its comparison, so one ticked row paid for the
        paycheck engine twice and the count could in principle disagree with
        the write.  Asked by the verb, the two cannot differ and the figure is
        resolved once.  ``False`` for the envelope branch, which books its own
        entries and honours no submitted figure at all.

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

    # **A row ALREADY in the status this settle would move it to is an
    # idempotent no-op**, which is the rule
    # ``transfer_service._update.settle_transfer`` has always stated one table
    # over and this verb did not.  Without it a replayed mark-done -- a double
    # click, a stale tab, a re-POST of an empty body -- re-derived the row and
    # OVERWROTE its record: measured, a settle booking a human's ``$90.00``
    # against a ``$500.00`` plan came back ``$90.00`` on the ``derived`` basis,
    # so the figure survived and the stored answer to "did a human correct this"
    # was destroyed.  That answer is the entire reason ``settled_basis_id``
    # exists (finding **N-241**, ruling **R-FH**), and the reconcile writer's
    # correction count is read from this verb's return, so the replay also
    # un-counted a correction that had really been made.
    #
    # **``Settlement.from_settle`` closed that measurement independently** by
    # honouring a RETAINED ``corrected`` record, so the manual branch would now
    # survive the replay without this line.  What it still uniquely protects is
    # the ENVELOPE replay: ``settle_from_entries`` refuses an immutable row by
    # precondition, so a double-clicked Mark Paid on an already-Paid envelope
    # would 400 where nothing is wrong and nothing needs doing.  That is the
    # case ``TestAReplayedSettleIsANoOp`` pins, and it is pinned because this
    # return had no firing control anywhere in the suite until then -- all
    # 9,611 tests passed with it disabled, measured 2026-08-17.
    #
    # **It tests the row's own settled status, NOT the settled BAND**, and the
    # difference is a refusal: a first version asked ``status_id in
    # settled_status_ids()`` and thereby swallowed ``Settled -> Paid``, an
    # ILLEGAL transition ``state_machine.verify_transition`` exists to reject,
    # turning a designed 400 into a silent 200.  Only the identity move is
    # nothing to do; every other settled-to-settled move still owes the state
    # machine its answer.
    #
    # Settling again is not how a settled row is changed: the figure is
    # corrected by reverting and re-settling (the record survives the revert,
    # ``status_seam.apply_status_change``), and the DAY by
    # ``status_seam.settle_day_for_status`` (ruling **R-ED**).
    if is_identity_move(txn, settled_status_id(txn)):
        return False

    correction = None
    if settles_from_entries(txn):
        settle_from_entries(txn, settled_on=settled_on)
    else:
        # Act 1b's DECISION, taken before act 1a moves anything.  The echo rule
        # -- a figure equal to what the row would book anyway is not a
        # correction -- is :func:`_is_correction`'s, asked here and by the
        # reconcile writer's telemetry so one rule has one statement (finding
        # **N-231**).  Writing an echoed figure would populate a column that is
        # NULL on every uncorrected row, destroying the only signal that says a
        # human typed one, which is what ruling R-FB's own production
        # measurement is made of ("11 of 93 settled bills carry a hand-typed
        # correction").  That half is load-bearing rather than tidy: the
        # reconcile panel PREFILLS its amount box, so an untouched tick submits
        # the figure the row would have booked anyway.
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
        # **Asked BEFORE act 1a, and that is exact rather than approximate.**
        # The comparison it makes -- against :func:`settle_amount`, the same
        # expression the panel prefills from -- equals the post-refresh
        # the row's contribution in every shape (:func:`_is_correction` states the
        # three).  What it is NOT is a comparison against the pre-refresh
        # that contribution: it is a cache the recompute has already
        # superseded, and using it made the rule inert for exactly the rows act
        # 1a is about.
        # What this settle BOOKS, read once from the same published rule the
        # reconcile panel prefills from, so the figure offered and the figure
        # recorded cannot differ.  Read BEFORE act 1a below, which is the only
        # moment it has an answer: the live producers are Projected-only.
        # ONE basis for the whole act (developer ruling, 2026-08-17): the
        # figure this settle books, the echo rule's comparison and act 1a's
        # cache refresh are three questions about one row against one
        # derivation, and building a basis for each ran the paycheck engine
        # up to three times per settle.
        basis = amount_basis(txn.account.user_id, txn.scenario_id)
        resolved, live = _manual_branch_figures(txn, basis)
        # What this settle BOOKS: a RETAINED correction if the row still holds
        # one, else what the branch resolves.  Read through the same published
        # rule :func:`settle_amount` answers with, so the figure the panel
        # OFFERS and the figure this books are one expression and cannot drift.
        held = honoured_correction(txn)
        booked = resolved if held is None else held
        correction = submitted if _is_correction(txn, submitted, booked) else None
        # Act 1a: RECONCILE THE CACHE, before the seam.  It must be before:
        # the projection's own rule is Projected-only, so ``live_projected_net``
        # drops a row the moment its status leaves that band and asking after
        # the flip always answers "nothing fresher".
        _reconcile_cached_amount(txn, live)
        # Acts 1b and 2 in ONE call: what moved, how it is known, and the day.
        # The record is written by the seam rather than here so ``settled_amount``
        # keeps the ONE writer ``settled_on`` has (finding **N-185**'s rule
        # applied to the column beside it) -- two writers of one money column in
        # one request is the shape this arc removes.
        #
        # ``recorded_settlement`` is what the row STILL carries from a settle it
        # has since been reverted out of: a revert releases the assertion and
        # keeps what moved, so a user who reverted in order to edit has not lost
        # the figure they read off their statement.  ``from_settle`` honours a
        # retained ``corrected`` one, which is what makes that round trip
        # lossless rather than merely slower to destroy the figure.
        apply_status_change(
            txn, settled_status_id(txn), settled_on=settled_on,
            settlement=Settlement.from_settle(
                booked, correction, recorded_settlement(txn),
            ),
        )

    posting_service.sync_transaction_postings(
        txn, settled=txn.status.is_settled,
    )
    return correction is not None


def _reconcile_cached_amount(txn: Transaction, live: "Decimal | None") -> None:
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
        live: The projection's live figure for this row, or ``None`` when
            nothing supersedes its cache.  Resolved ONCE by
            :func:`_manual_branch_figures` alongside the figure the settle
            books, and threaded here rather than re-derived -- the same
            build-once-and-thread discipline the fold uses over a whole plan,
            applied to one settle.  It is the caller's job to ask before the
            status flip, for the reason the *txn* argument states.
    """
    if live is not None:
        txn.estimated_amount = live


def _freshest_amount(txn: Transaction, basis) -> Decimal | None:
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

    So this asks the projection's OWN live-override seam
    (:func:`app.services.cash_ledger.live_override`) rather than restating which
    rows have a live value.  It is the same expression
    :func:`app.services.cash_ledger.income_amount` evaluates one tier down --
    "the override when present, else the row's own contribution" -- asked for
    one row instead of reduced over a period, and plan step **X-au-d** deletes
    both by making the row's amount DERIVED rather than cached.

    **It costs nothing on the rows it does not apply to.**  Both halves of the
    basis filter their candidates in Python first and return an empty
    dict with NO query: the loan half wants ``transfer_id IS NOT NULL``, which
    :func:`settle_transaction` has already refused, and the salary half wants a
    Projected, non-overridden, template-linked income row.  An expense, an
    ad-hoc row, an already-settled row and a manually-overridden paycheck each
    leave here after two list comprehensions.

    **It carried a fourth guard until plan step X-au-c3 -- "a row carrying an
    ``actual_amount`` is NOT a candidate" -- and that guard is DELETED because
    its state became unconstructible.**  It protected a Projected salary row
    whose actual the owner had typed by hand from having its estimate
    overwritten at settle.  That row can no longer exist: a figure now records a
    SETTLE, and ``ck_transactions_settled_amount_needs_basis`` keeps one off a
    row that has not settled, so a pre-settle row carries none by construction.
    The five production rows that were in that state were promoted into their
    PLAN by migration ``e4b8a71c0f36``, where the valuation was already reading
    them.  Translating the guard into ``settled_basis_id is not None`` would have
    kept the shape and lost the point: every caller here is pre-settle, both live
    producers are Projected-only, so no single-line mutation of the translated
    guard could fail a test -- and a guard whose only possible test cannot fail
    is not a guard (the same rule ``status_seam.settle_day_for_status``'s closing
    note states, and finding **N-184**'s).

    **It compares against ``estimated_amount``, not the row's contribution**:
    the two are equal for every row that reaches here, and naming the column that
    IS the cache says what the comparison means.

    Args:
        txn: The row about to settle, still in its pre-settle status.  Read for
            the fields the live producers' candidate filters test; not mutated.
        basis: The :class:`~app.services.cash_ledger.AmountBasis` built over
            this one row.  Taken as an argument rather than built here so the
            caller that also needs the row's contribution
            (:func:`settle_amount`) pays for ONE basis rather than two -- the
            same build-once-and-thread discipline the fold uses over a whole
            plan.  It is pinned to the row's OWNER and SCENARIO since plan step
            X-au-c2b, not built over this one row.

    Returns:
        The live amount when one exists and disagrees with the cache, else
        ``None`` -- meaning "nothing fresher than what the row already says".
    """
    live = live_override(txn, basis)
    if live is None or live == txn.estimated_amount:
        return None
    return live


def settle_from_entries(
    txn: Transaction, *, settled_on: date | None = None,
) -> None:
    """Settle a tracked-envelope transaction at sum(entries).

    The envelope PRIMITIVE: the three writes an envelope's close needs --
    ``status_id``, the settle day, and the settlement RECORD -- as a single
    source of truth.  It is reached two ways, and the split is
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
      - **NO figure is stored at all** (plan step X-au-c3).  The record's basis
        is ``purchases``, and a ``purchases`` settlement leaves
        ``settled_amount`` NULL because the row's own entries ARE the record:
        ``row_valuation.settled_figure`` sums them on demand.  It USED to set
        ``actual_amount = sum(entries)``, and dropping that copy is what deleted
        ``entry_service``'s re-derivation of a settled envelope's figure -- with
        one copy there is nothing for a reconciler to keep in step, and a
        purchase corrected later moves the close by exactly its own difference.
        What the close BOOKS is still ``sum(e.amount for e in txn.entries)``,
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

    # Route the status mechanics (transition check, status_id, the settlement
    # record, status expire) through the single seam so this helper cannot drift
    # from the manual mark-done / PATCH / credit paths.  The day is the caller's
    # when it has one -- the reconcile tick's statement date -- and otherwise
    # None, which is the seam's "stamp the user's today on entering the settled
    # status and preserve an existing one".
    #
    # The record STORES NO FIGURE (plan step X-au-c3): its basis is
    # ``purchases``, and the row's own entries are what state the amount.  That
    # is what deleted ``entry_service``'s re-derivation of a settled envelope's
    # figure -- with one copy there is nothing for a reconciler to keep in step,
    # and a purchase corrected next week moves the close by exactly its own
    # difference with no second write.
    apply_status_change(
        txn, new_status_id, settled_on=settled_on,
        settlement=Settlement(
            amount=None, basis=SettlementBasisEnum.PURCHASES,
        ),
    )

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
        # What the close BOOKS, computed for the log rather than read back off
        # the row: a ``purchases`` settlement stores nothing to read.
        # ``purchases_total`` answers ``Decimal("0")`` for an empty entry list,
        # which is the carry-forward "no spend, full rollover" case.
        settled_amount=str(purchases_total(txn.entries)),
        settled_on=txn.settled_on.isoformat(),
    )
