"""
Shekel Budget App -- Entry service: what a PURCHASE WRITE may not do.

**Split out of :mod:`._doors` at plan step X-au-j**, whose N-323 predicate took
that module past ``max-module-lines`` -- it sat at exactly 1000 of 1000, which
this project's own rule calls a cap reached rather than headroom.  The cut is by
what each function DECIDES rather than by size, the same seam
:mod:`app.services.status_seam._refusals` was cut on: *"they are gathered here
because they are one subject"*.

The subject here is what a purchase write may not do.  Nothing in this module
writes, flushes, or resolves a figure: each function reads the state a door is
about to change and RAISES, or returns.  A door composes them ahead of any
mutation, so a refused call leaves the session untouched.

:data:`_COST_BEARING_FIELDS` lives here because it is the same statement in
data: it names the fields that change what a row COST, which is exactly what a
settled parent refuses.

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows and plain data in, a
raise or ``None`` out; no Flask import, no writes.
"""

from datetime import date

from app import ref_cache
from app.enums import SettledDayBasisEnum, SettlementBasisEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.utils.balance_predicates import is_archived
from app.utils.dates import display_today

#: The purchase facts that change what its PARENT ROW COST, named by what
#: actually reads them.
#:
#: **It was ``_UPDATABLE_FIELDS - {"settled_on"}`` until plan step
#: ``bank_import:X-f6a-3b``, and defining a set by what it EXCLUDES is how it
#: came to claim two fields that change no figure.**  A census of every reader
#: in ``app/`` settles each one:
#:
#: * ``amount`` -- :func:`app.services.row_valuation.purchases_total`,
#:   ``cash_ledger._entry_checking_impact``, ``posted_purchase_sum``,
#:   ``credit_entry_sum``.  COST-BEARING;
#: * ``is_credit`` -- those same four plus ``sync_entry_payback``, which moves
#:   the purchase between this account's outflow and its CC Payback sibling.
#:   COST-BEARING;
#: * ``purchased_on`` -- ``Transaction.entries``' ordering, the out-of-period
#:   WARNING (:func:`._sums.check_purchase_date_in_period`), the reconcile
#:   panel's sort and its two OFFER predicates
#:   (``reconcile_service._rows.lands_on_or_before``,
#:   ``_purchases._outstanding_scope``), the matcher's ``expected_on``, three
#:   template fields.  **No valuation reads it.**  ``_outstanding_scope`` does
#:   admit a SETTLED parent, so this day gates which purchases the reconcile
#:   panel offers and a tick there writes ``settled_on`` -- admitted knowingly:
#:   ruling **R-FW** makes correcting the day a purchase was MADE the point, and
#:   it only ever moves the day EARLIER, which makes a row more offerable.
#:   Named by adversarial financial review 2026-08-19;
#: * ``description`` -- templates only.
#:
#: **What the miscount cost is measured.**  Ruling **R-FW**'s purchase-day
#: correction submits ``purchased_on`` beside ``settled_on`` in ONE call
#: (``statement_match._accept._apply_day``), so on the developer's own
#: statement 13 of the 15 corrections the review screen offered were refused by
#: a guard about what a row COST, for a field that changes no cost -- and the
#: 13 include the whole 2026-04-29 bookkeeping session ruling R-FW exists for.
#: The screen rendered an Accept button that could never succeed.
#:
#: ``settled_on`` was already outside the set and its reason is unchanged: it
#: is the day the BANK took this purchase, an OBSERVATION rather than a
#: restatement of what was spent.  Recording it moves that purchase's cash out
#: of its envelope's close and onto its own day
#: (``cash_ledger.settled_cash_leg``'s third term, ruling **R-FM**), and the two
#: always sum to the same total.  That is the SAME split plan step X-au-c3 is
#: built on -- ``settled_on`` / ``reconciled_by_id`` are the ASSERTION and
#: ``settled_amount`` / ``settled_basis_id`` are WHAT MOVED -- read one level
#: down, on the purchase instead of on the row.
_COST_BEARING_FIELDS = frozenset({"amount", "is_credit"})


def cost_fields_changing(valid_updates: dict) -> "frozenset[str]":
    """Return the fields :func:`_reject_settled_parent` must weigh for one call.

    **Ruling R-GE (2026-08-22): a bank statement's evidence justifies re-costing
    a settled purchase, and the EVIDENCE IS IN THE CALL.**
    :data:`_COST_BEARING_FIELDS` exists for a human's second thoughts -- a typed
    figure with nothing behind it -- and a statement is the opposite of one.  So
    ``amount`` leaves the refused set exactly when the same submission records a
    settle day whose basis is ``observed``.

    **What bounds the permission is the BASIS, not a flag a caller asserts**, and
    that is the whole design: ``observed`` means *the bank showed this money
    move*, and the statement matcher is its only writer -- the entry PATCH door
    writes ``entered`` and the reconcile panel ``asserted`` through its own bulk
    UPDATE.  A caller holding an ``observed`` day HAS the evidence, so the rule
    needs no second channel and no ordinary edit form can reach it.

    ``is_credit`` is NOT released with it: which side of the card a purchase sat
    on is not a figure a statement states.

    Args:
        valid_updates: The submission, already narrowed to updatable fields.

    Returns:
        The field names to weigh -- every changing field, less ``amount`` where
        the call carries the bank's own observation.
    """
    changing = frozenset(valid_updates)
    evidence = valid_updates.get("settle_day")
    if getattr(evidence, "basis", None) is SettledDayBasisEnum.OBSERVED:
        return changing - {"amount"}
    return changing


def _reject_settled_parent(
    txn: Transaction, changing: "frozenset[str]",
) -> None:
    """Refuse an entry mutation that RE-COSTS a row whose money has MOVED.

    **Finding N-229's door half, widened to the settled BAND at plan step
    X-au-c3** (developer ruling, 2026-08-17).  A settled envelope's purchases
    are closed: the user has said this money moved, and re-pricing one or
    removing one would move it again -- so both refuse, on Paid and Received as
    well as on the terminal ``Settled``.

    **ADDING one is a separate rule since plan step ``bank_import:X-f6a-3b``,
    and it lives in :func:`_reject_settled_addition`.**  The two are not the
    same question: removing a purchase shrinks a recorded cost with no evidence
    but the user's own second thoughts, where ADDING one on a row whose figure
    IS its purchases raises that cost by exactly the figure a bank statement
    just showed.  Keeping them in one function meant the optimistic direction
    and the evidenced one shared a refusal.

    **The reason it is the BAND and not the archive is carry-forward, and it is
    the argument that decides this.**  ``carry_forward_service`` rolls an
    envelope's UNSPENT remainder (``estimated - Sigma(entries)``) into the next
    period's row and then settles the source at what was spent.  So the moment
    an envelope closes, its leftover has already moved on and is sitting in a
    LATER row.  A purchase recorded against the closed source afterwards would
    raise its cost while that later row still holds the rolled-forward money --
    the same dollars counted twice, in two periods, with nothing to reconcile
    them.  A forgotten purchase belongs in the period that now holds the money.

    **What a user does instead**: put the row back to Projected, record the
    purchase, and close it again.  That is the same act the refusal names, and
    it is honest about what happened -- the close was premature, so the settle
    day it stamped and the statement it was reconciled against were premature
    too.  Leaving the settled band releases the ASSERTION -- the settle day and
    the statement link, in ``status_seam.apply_status_change`` -- and KEEPS what
    the row recorded, which is correct rather than a cost: the purchases are
    what the figure is made of, so a re-close restates it from them on the day
    the money really moved.

    **The rejected alternative, and why**: letting a settled envelope re-derive
    its figure from a late purchase is what the deleted
    ``_update_actual_if_paid`` did (``actual_amount = Sigma(entries)`` on any
    settled row with entries).  It moves money in the OPTIMISTIC direction
    without a human act -- one ``$50`` purchase back-filled into a ``$500``
    close crashes the recorded cost to ``$50`` and hands ``$450`` of
    already-spent money back to the projection.  That is precisely the failure
    :func:`~app.services.status_seam.reject_future_settle_day` exists to
    prevent, and the reason ``TransactionEntry.settled_on`` deliberately bounds
    only from below: where the app must guess, it keeps the balance LOW.

    **It is FIELD-AWARE, and what it admits is every field that changes no
    figure** (developer ruling, 2026-08-17; the set corrected at plan step
    ``bank_import:X-f6a-3b``).  Everything above is an argument about what the
    row COST, so :data:`_COST_BEARING_FIELDS` is now named by what reads a
    figure rather than by subtracting the one field somebody remembered --
    which is what let it refuse ``purchased_on`` and ``description``, neither
    of which any money rule reads.

    ``settled_on`` is the field that argument was written for.  Recording it
    changes no total -- it moves that purchase's cash out of the envelope's
    close and onto its own day, and ``settled_cash_leg`` subtracts exactly what
    the purchase's own leg books, so the two always sum to the row's whole debit
    total.  Refusing it would leave already-spent money dated on the day the
    envelope happened to be closed with no door to correct it: measured on the
    2026-08-17 production dump, 28 closed envelopes hold 61 debit purchases
    with no posting day recorded, totalling ``$4,360.07``.

    That split is this step's own three-lifetime model read one level down.  A
    purchase's amount is WHAT MOVED and its posting day is an ASSERTION about
    when -- the same two facts ``settled_amount`` and ``settled_on`` are on the
    parent, with the same answer: the assertion may be recorded, corrected and
    withdrawn long after the figure is final.

    ``Status.is_settled`` is the band -- Paid, Received AND the terminal
    ``Settled`` -- where :func:`~app.utils.balance_predicates.is_archived` is
    that last status alone.  The band is what this rule is about, so it reads
    the band; the archive keeps its own predicate because other readers mean it.

    Args:
        txn: The parent transaction the entry belongs (or would belong) to.
            Its ``status`` relationship is read (``lazy="joined"``).
        changing: The purchase facts this act writes.  The DELETE door passes
            :data:`_COST_BEARING_FIELDS` -- a purchase vanishing changes every
            one of them -- and the update door passes the fields it was
            actually given, which is what lets a posting-day or purchase-day
            edit through where a re-price is refused.  The CREATE door does not
            call this at all; :func:`_reject_settled_addition` is its rule.

    Raises:
        ValidationError: When *txn* is in a settled status and *changing*
            touches any cost-bearing field.
    """
    if txn.status is None or not txn.status.is_settled:
        return
    if not changing & _COST_BEARING_FIELDS:
        return
    raise ValidationError(
        f"Transaction {txn.id} has settled; its purchases are closed and "
        "cannot be removed or re-priced. Doing so would change what "
        "the row cost after its money moved -- and a carry-forward has "
        "already rolled its leftover into a later period, so the same dollars "
        "would be counted twice. Set the row back to Projected to change a "
        "purchase, then mark it paid again. Recording the day your bank took "
        "a purchase, or the day you made it, is still allowed: those say when "
        "this money moved, not how much of it did."
    )


def _reject_settled_addition(
    txn: Transaction, settled_on: "date | None",
) -> None:
    """Refuse a NEW purchase against a settled row that cannot record one.

    **The rule is about the row's FIGURE, not its status** (plan step
    ``bank_import:X-f6a-3b``, on measurement).  A settled row records what
    moved in one of two ways
    (:class:`~app.enums.SettlementBasisEnum`), and a new purchase means opposite
    things to them:

    * a ``purchases`` settlement stores NO figure -- the row's cost IS
      ``Sigma(entries)`` (:func:`app.services.row_valuation.settled_figure`), so
      a new POSTED purchase raises that cost by its own amount while
      ``settled_cash_leg`` subtracts the same amount, leaving the envelope's own
      leg unchanged and the purchase booking its own cash on its own day.
      Measured 2026-08-18: adding `$18.64` to the 2026-05-21 Groceries close
      shrank that day's anchor true-up by exactly `$18.64`;
    * a ``derived`` or ``corrected`` settlement STORES its figure, fixed before
      the purchase existed, so the gross cannot rise and ``settled_cash_leg``'s
      third term subtracts money it never held.  Measured: adding `$367.62` to a
      `$163.95` close moved that leg to **`+203.67`** -- an EXPENSE row
      publishing an inflow -- while the true-up moved `$0.00`.  Both legs still
      net to `-163.95`, which is why the balance instrument is BLIND to it.

    **Nothing else keeps that state unrepresentable** (finding **N-318**):
    :func:`app.services.cash_ledger.cash_leg_of` is total in every other
    direction, states no precondition and cannot see one, so THIS function is
    the guarantee.  Production holds no instance -- measured 2026-08-19, 137
    settled rows carry a stored-figure settlement (8 of them envelopes on the
    developer's checking account) and every one holds ZERO purchases -- and a
    row that HAS purchases always settles on the ``purchases`` basis, because
    ``settles_from_entries`` is ``tracks_purchases and txn.entries`` and
    ``carry_forward``'s direct call writes that basis unconditionally.

    **The purchase must state the day the BANK TOOK IT** (developer ruling,
    2026-08-19), because the paragraph above holds only for a POSTED one: an
    undated purchase is not in ``posted_purchase_sum``, so the gross rises with
    nothing subtracting it and the row's own leg moves by the purchase amount
    **on the row's original settle day** -- measured, `-50.00` to `-80.00`, on
    a past day with no external evidence.  So the rule admits the case its
    argument supports and no more.  The importer always supplies both days; the
    add-purchase form has no posting-day field and keeps refusing exactly as it
    does today.  Found by adversarial financial review 2026-08-19.

    **It does NOT answer the carry-forward double count, and it does not have
    to** (finding **N-249**, owner ``balance:X-ax``).  Every carried-forward
    source settles on this very basis, so a purchase added afterwards leaves
    the target period holding a leftover computed from the old sum.  The
    developer RULED that remedy on 2026-08-12 -- reconcile the rollover --
    and explicitly REJECTED refusing late purchases on a rolled-forward
    source, which is the reason :func:`_reject_settled_parent`'s message still
    gives for a refusal this rule lifts.

    **The ARCHIVE is refused whatever its basis** (finding **N-229**): an
    archived row's purchases are history, and
    ``statement_match._candidates._purchase_candidates`` already declines to
    offer one, so admitting a new purchase here would be the only door
    disagreeing with that.

    **What a user does when this refuses**: put the row back to Projected,
    record the purchase, and close it again -- which restates the figure from
    the records, exactly as :func:`_reject_settled_parent` describes.

    Args:
        txn: The parent transaction the new entry would belong to.  Its
            ``status`` relationship is read (``lazy="joined"``) and then its
            settlement record.
        settled_on: The day the bank took the new purchase, or ``None`` when
            the caller does not know it.

    Raises:
        ValidationError: When *txn* has settled and its recorded figure is not
            its purchases, when it is archived, or when the purchase states no
            posting day.
    """
    if txn.status is None or not txn.status.is_settled:
        return
    purchases_basis = ref_cache.settlement_basis_id(
        SettlementBasisEnum.PURCHASES,
    )
    if not is_archived(txn) and txn.settled_basis_id == purchases_basis:
        if settled_on is not None:
            return
        raise ValidationError(
            f"Transaction {txn.id} has settled, so a purchase added to it has "
            "to say when your bank took the money -- without that day its "
            "amount comes out of this row on the day the row closed, which is "
            "a day you may already have checked against a statement. Record "
            "the purchase from your bank statement, or set the row back to "
            "Projected, add it, and mark it paid again."
        )
    raise ValidationError(
        f"Transaction {txn.id} has settled and records a fixed figure, so a "
        "new purchase cannot be added to it: the row's cost would not grow by "
        "the purchase, and the purchase's own cash would be subtracted from a "
        "total that never contained it. Set the row back to Projected, add "
        "the purchase, and mark it paid again -- that restates what it cost "
        "from the purchases themselves."
    )

def _reject_future_purchase_date(purchased_on: date) -> None:
    """Refuse a purchase dated after the user's today (ruling R-M).

    The ONE statement of "a purchase entry records a purchase that HAPPENED",
    shared by both write doors (:func:`create_entry` and :func:`update_entry`)
    so the boundary cannot hold on one and not the other -- the same
    both-doors-one-derivation shape ruling R-C's origination guard uses.

    **Why the source and not the reader** (ruling R-M, whose work shipped at
    plan step S1-c, so it is recorded in
    ``docs/audits/balance_architecture/archive/phase_x_as_built_2026-08-04.md``
    Section 3 rather than in the live README).  A future
    purchase is not merely odd data: it moves a rendered balance.  The
    projection holds back
    ``max(estimated - settled_debit - credit, outstanding_debit)`` for a
    still-projected envelope, so an entry dated ahead changes today's balance in
    EITHER direction depending only on its credit flag -- measured on the live
    Groceries envelope (``$780.00`` budgeted, ``$60.55`` held back): a
    ``$150.00`` future debit takes the reservation to ``$150.00`` through the
    ``max()`` floor (``-$89.45`` on the balance), while the same amount ticked
    CC takes it to ``$0.00`` (``+$60.55``).  Refusing it here is what lets the
    reservation's ``as_of`` window -- the parameter the calendar passed and the
    grid did not, which was the divergence itself -- stay DELETED at plan step
    X-c2 rather than ruled.

    **This guard survived the 2026-08-01 re-ruling of R-M, and it survived by
    moving onto the column it was always about.**  R-M and ruling R-DH (e) had
    been defining ONE column two ways -- "the day the purchase happened, never
    in the future" and "the day the money hit the account, one to two days
    later for a debit card".  The column split rather than the guard bending:
    ``purchased_on`` keeps this boundary intact and ``settled_on`` carries the
    posting day, so the forward case the developer needs to express has its own
    field and this one no longer has to admit a forecast.  Widening THIS bound
    was rejected: the column would then mean "purchase day" on some rows and
    "posting day" on others, with nothing in the schema recording which, and
    the remaining-budget figure and the out-of-period warning both read it as
    the purchase day.

    Backdating stays fully allowed, and is used: a purchase logged days after it
    happened, or one dated into the previous pay period, is ordinary (the real
    2026-05-21 Groceries row carries entries from 05-18).  A purchase you have
    not made yet is the envelope's remaining BUDGET, which the row already
    models.

    The comparison is against :func:`~app.utils.dates.display_today` -- the
    user's wall-clock date, not the server's UTC one -- because
    ``purchased_on`` is a civil date the user types on their own clock.
    Judging it in UTC would refuse a legitimate same-day entry for the hours
    the two frames disagree.

    Args:
        purchased_on: The civil date the caller wants the entry to carry.

    Raises:
        ValidationError: When *purchased_on* is after the user's today.  The
            message carries both dates so the surface can show what was
            rejected and what the boundary was.
    """
    today = display_today()
    if purchased_on > today:
        raise ValidationError(
            f"A purchase entry records a purchase that has already happened, "
            f"so its date cannot be in the future: {purchased_on.isoformat()} "
            f"is after today ({today.isoformat()}).  Log the purchase when it "
            f"happens; money you have not spent yet is already held back by "
            f"this row's remaining budget.  If the purchase is made but has "
            f"not reached your bank yet, that is what the posting date is for."
        )


def _reject_future_posting_day(settled_on: "date | None") -> None:
    """Refuse a bank posting day after the user's today -- ruling **R-FM**.

    The purchase twin of
    :func:`app.services.status_seam.reject_future_settle_day`, and it arrived at
    plan step X-f3b because that step INVERTED the reason this column had no
    upper bound: a forward day was conservative while a purchase was not a cash
    movement, and now RELEASES the reservation today while booking the cash
    later.  The argument is stated once, at the rule it is about
    (``cash_ledger._amounts._entry_aware_amount``), rather than a second time
    here.  A purchase the bank has not taken yet leaves ``settled_on`` NULL,
    which is what that state has always meant, so nothing expressible is lost;
    measured before the bound was added, ZERO of 91 production purchases carried
    a forward day.

    The clock is the USER's (:func:`~app.utils.dates.display_today`), for the
    reason :func:`_reject_future_purchase_date` beside it states.

    Args:
        settled_on: The posting day the caller wants the entry to carry, or
            ``None`` to clear it (always allowed -- it is the outstanding
            state).

    Raises:
        ValidationError: When *settled_on* is after the user's today.
    """
    if settled_on is None:
        return
    today = display_today()
    if settled_on > today:
        raise ValidationError(
            f"A posting date records the day your bank TOOK the money, so it "
            f"cannot be in the future: {settled_on.isoformat()} is after today "
            f"({today.isoformat()}).  Leave the posting date empty until you "
            f"see the purchase on a statement -- an unposted purchase already "
            f"holds its whole budget back."
        )


def _reject_settled_before_purchase(
    purchased_on: date, settled_on: date | None,
) -> None:
    """Refuse a posting day earlier than the purchase it belongs to.

    Money cannot leave the account before it was spent.  The database carries
    the same rule as ``ck_transaction_entries_settled_not_before_purchase`` and
    that constraint is the backstop; this is the door, so the user gets a
    message naming both dates instead of a 500 from an ``IntegrityError``.

    It is checked against the RESULTING pair rather than the submitted one,
    because either side can move: editing a purchase's date backwards past a
    posting day already recorded breaks the invariant just as surely as
    entering an early posting day does.

    The UPPER bound is :func:`_reject_future_posting_day`'s and it arrived at
    plan step X-f3b (ruling **R-FM**), which inverted the reason there was
    none: a recorded posting day is now the moment the money leaves the book,
    so a forward one takes already-spent money out of today's projection instead
    of holding it conservatively.  The two bounds are separate functions because
    they are separate rules -- this one is about a PAIR of the row's own dates
    and that one is about the clock -- and both are checked on the RESULT of an
    update rather than on its submission.

    Args:
        purchased_on: The day the purchase was made, after any pending update.
        settled_on: The day the bank took it, after any pending update, or
            ``None`` when it has not been observed.

    Raises:
        ValidationError: When *settled_on* precedes *purchased_on*.
    """
    if settled_on is not None and settled_on < purchased_on:
        raise ValidationError(
            f"A purchase cannot reach your bank before you make it: "
            f"{settled_on.isoformat()} is earlier than the purchase date "
            f"({purchased_on.isoformat()}).  Correct whichever of the two is "
            f"wrong."
        )
