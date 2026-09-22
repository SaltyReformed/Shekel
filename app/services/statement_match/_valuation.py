"""What ONE candidate is WORTH, and when the app believes its money moved.

The VALUATION half of the offer set, split out of :mod:`._candidates` at plan
step ``credit_card:CC-5-4a-1`` when that module crossed the 1,000-line bound
(ruling **balance:R-IR**: the session that breaks a module is the one that
splits it, by SUBJECT).  The seam is the one :func:`repriced`'s own docstring
has drawn since plan step ``bank_import:X-f6a-3c-2``: *the scope answers WHICH
rows an act may reach; this answers what one of them is WORTH, and the two
must be asked at different moments.*  :mod:`._candidates` keeps the scope --
the three arms that decide which rows exist and may be offered, and the claims
that say which are already spoken for -- and calls the constructors here once
per row; the write doors call :func:`repriced` once per act.

**ONE construction per kind, because two callers build each and one of them
writes money with it** (plan step ``bank_import:X-f6a-3c-2``): the offer set
builds a candidate for every row the account holds, and the accept door
re-builds the one to four rows an act names -- through the same function, so
what a row is worth and when the app believes it moved come from one read on
both sides of the money gate.  Three constructors for the three subjects of
:class:`~._subjects.RowKind`: a PURCHASE (:func:`purchase_candidate`), a
Projected TRANSACTION (:func:`transaction_candidate`), and a SETTLEMENT --
a settled row's covering movement, the subject of every settled match since
ruling **R-CC43** (:func:`settlement_candidate`).

**Pricing is the cash ledger's, never restated here.**  A purchase and a
DATED covering movement are worth ``cash_ledger.movement_cash_leg`` (ruling
**R-BAL35**), the one valuation the fold and the ledger book for a movement;
a Projected row -- and an UN-DATED covering movement, a reverted row's kept
record that a match re-settles through the row's door -- is worth
``cash_ledger.cash_leg_of`` over what its own settle verb says it would book
(:func:`transaction_price`).  A matcher that computed its own figure could offer a line
against a number no door would book.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.enums import SettledDayBasisEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    cash_ledger,
    transaction_service,
    transfer_service,
)
from app.services.settle_day import recorded_settle_day
from app.utils.balance_predicates import is_projected

from ._subjects import CandidateRow, RowKind

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCalendar


def transaction_price(
    txn: Transaction, basis: "cash_ledger.AmountBasis",
) -> "Decimal | None":
    """Return what settling the PROJECTED *txn* would book, signed, or ``None``.

    The one branch in this module, and it is the verb's own partition rather
    than a money rule of its own:

    * a row that settles from its purchases
      (``transaction_service.settles_from_entries``, the verb's own
      predicate) is worth ``0`` to the offer: the bank line it could be is one
      of its purchases, each a candidate of its own (ruling **R-BAL78**);
    * any other row is worth what settling it would book, which is its own
      arm's ``settle_amount`` -- the transfer service's for a shadow leg and
      the transaction service's for its complement -- signed by
      :func:`~app.services.cash_ledger.cash_leg_of`.  A retained STATED
      record (a reverted row's kept figure, ruling **R-BAL61**) is honoured
      there, so a kept movement's candidate is priced at what its re-settle
      books and not at a stale column.

    **It prices a PROJECTED row only** (plan step ``credit_card:CC-5-4a-1``,
    ruling **R-CC43**).  Through ``CC-5-3`` it carried a settled arm --
    ``status_seam.covered_cash_leg`` on the screen's account (rulings
    **R-BAL81**, **R-CC40**) -- because a settled row was the candidate and
    its movement the price.  The movement is the candidate now
    (:func:`settlement_candidate` reads it directly), so a settled row has no
    price as a row and this function is not asked for one:
    :func:`~._candidates._transaction_candidates` scopes on ``Projected`` and
    :func:`repriced` declines a row that has since settled.  Two callers, one
    for the offer and one for the re-price, and the un-dated arm of
    :func:`settlement_candidate` is the third -- a reverted row's kept
    movement re-settles through the row's door, so it is worth what that door
    would book.

    **Neither ``settle_amount`` can refuse a row this module's scope admits**,
    and that is why there is no guard against one here.  Both refuse exactly a
    soft-deleted row and a row on the wrong side of the shadow partition;
    :func:`~._candidates._transaction_candidates` excludes the first through
    ``balance_contributing_clause`` and the dispatch above IS the second.  A
    ``try`` around them would be a guard nothing could ever observe, which this
    project has twice measured as worse than none.

    **``AmountUnresolvable`` is a different thing and is REPORTED rather than
    swallowed or raised.**  It means the amount model had no rule for the row
    -- latent today, because every production row still owns its figure, and
    live from the first per-kind cutover (plan steps ``balance:X-au-d`` on).  A
    matcher cannot offer a row it cannot price without guessing, so the row
    leaves the candidate set; but a reader that dropped it silently would hide
    a broken row, and one that raised would make the whole review screen
    unreachable for one bad row with no in-app repair -- which is finding
    **N-302**'s shape.  :class:`~._subjects.Candidates` carries the count instead
    and the screen says so.

    Args:
        txn: The Projected row to price, with ``entries`` loaded.
        basis: The PASS's
            :class:`~app.services.cash_ledger.AmountBasis`, built once by
            :meth:`~._scope.ReviewScope.build` and threaded (plan step
            X-au-j).  Every offered row built its own until then, which finding
            **N-309** measured at **609 salary-pricing and 609 loan-pricing
            constructions** over 825 candidates and `4.7 s` to render -- and
            ``amount_basis``'s own docstring had already named calling the
            derivations per row as finding **N-228**.  The same reason the
            calendar is a parameter one tier up, and the same shape a balance
            pass threads its ``BalanceContext`` for.

    Returns:
        Its signed cash effect on the account its settle books on, or ``None``
        when the amount model cannot answer for it.
    """
    settle_amount = (
        transfer_service.settle_amount if txn.transfer_id is not None
        else transaction_service.settle_amount
    )
    if transaction_service.settles_from_entries(txn):
        # Its purchases ARE the figure (ruling **R-BAL78**) and each is a
        # candidate of its own, so the row is worth nothing to the offer.
        return Decimal("0")
    try:
        return cash_ledger.cash_leg_of(txn, settle_amount(txn, basis))
    except AmountUnresolvable:
        return None


def _label(txn: Transaction) -> str:
    """Return what to call *txn* on the review screen.

    The row's own name, with the parent transfer named where the row is a
    SHADOW: two accounts hold a leg each and both are called "Transfer to
    Mortgage", so a reviewer reading a checking statement has to be told which
    side they are being offered.

    Args:
        txn: The row being offered.

    Returns:
        Its display label.
    """
    if txn.transfer_id is None:
        return txn.name
    return f"{txn.name} (transfer leg)"


def _day_basis(row) -> SettledDayBasisEnum | None:
    """Return WHICH KIND of settle day *row* records, or ``None`` for none.

    Plan step **X-az**.  ONE reading for both candidate constructors, because a
    transaction and a purchase carry the same pair of columns and answering the
    question twice is two chances to answer it differently -- which is exactly
    what the two ``reconciled_by_id`` tests this replaced were.

    **It reads the stored basis and derives nothing.**  The basis is what the
    row's own settle door recorded: ``observed`` for a day the bank posted,
    ``asserted`` for the day a balance was asserted FOR (an upper bound), and
    ``entered`` for the owner's own.  Nothing here re-classifies, because a
    re-classification is the defect finding **N-332** names.

    Its parameter is the two models' shared
    :class:`~app.models.mixins.SettleDatedMixin`, which is where the pair is
    declared once for both -- so this reads a column set the schema guarantees
    rather than one it happens to share.

    Args:
        row: A :class:`~app.models.transaction.Transaction` or a
            :class:`~app.models.transaction_entry.TransactionEntry`.

    Returns:
        Its :class:`~app.enums.SettledDayBasisEnum` member, or ``None`` when the
        row carries no settle day at all.

    Raises:
        ValueError: When the row carries a day and no basis, or a basis and no
            day (propagated from
            :func:`app.services.settle_day.recorded_settle_day`).  Each table's
            ``ck_*_settle_day_basis_pairing`` makes both unstorable, so reaching
            either means something wrote around every door.
    """
    recorded = recorded_settle_day(row)
    return None if recorded is None else recorded.basis


def purchase_candidate(
    entry: TransactionEntry, calendar: "PayCalendar",
) -> CandidateRow:
    """Return one purchase as the candidate value every consumer here shares.

    **ONE construction, because two callers build it and one of them writes
    money with it** (plan step ``bank_import:X-f6a-3c-2``):
    :func:`~._candidates._purchase_candidates` builds it for every purchase this account
    holds, and :func:`~._create.create_purchase_from_line` builds it for the
    ONE purchase that door has just created -- a row no offer set derived
    before it can contain.  Two constructions would be two answers to what a
    purchase is worth and when the app believes it moved, on the two sides of a
    single match.

    A purchase's cash is :func:`app.services.cash_ledger.movement_cash_leg`
    -- its stored figure in its PARENT's direction, the one valuation every
    reader of a movement shares since plan step ``balance:X-bi-3b`` (ruling
    **R-BAL35**).  This spelled ``-entry.amount`` for itself before that
    step, total over both signs of the figure (it read *"always money
    LEAVING"* until plan step ``bank_import:X-gj-2b-3``; ruling
    **bank_import:R-II** ended that, and a stored refund of ``-28.29`` is a
    ``+28.29`` cash candidate here, which is why :mod:`._already_held`'s
    positive-cash set need not be income) but wrong in direction for a
    movement under an income row.  Every purchase offered here is a DEBIT
    under a CONTRIBUTING expense row (:func:`~._candidates._purchase_candidates`'s filter;
    ``create_entry`` refuses an income parent), so on that set the two agree
    to the cent; the producer is also total where this was not.

    **It takes the calendar since plan step ``bank_import:X-gz``**, for the
    reason its twin always has: the row states the paycheck it is budgeted in
    (ruling **R-BI9**), and a purchase's is its ENVELOPE's --
    ``entry.transaction.pay_period_id``, which every caller already has loaded
    beside the name this label reads.  Unlike the twin it never declines a row
    whose period the calendar lacks: a purchase is dated by its own day, and
    the offer set's period filter is what keeps such a row out of it
    (``TestTheCalendarIsTheOwnershipSCOPE``), so the placement is ``None``
    there and the row is otherwise what it was.

    Args:
        entry: The purchase, with its parent transaction loaded.
        calendar: The pass's
            :class:`~app.services.pay_calendar.PayCalendar`, which the
            envelope's period is read from.

    Returns:
        Its :class:`~._subjects.CandidateRow`.
    """
    return CandidateRow(
        kind=RowKind.PURCHASE,
        row_id=entry.id,
        label=f"{entry.transaction.name}: {entry.description}",
        cash_amount=cash_ledger.movement_cash_leg(entry.transaction, entry),
        settled_on=entry.settled_on,
        is_settled=entry.settled_on is not None,
        # **A purchase always states its own figure.**  The two shapes whose
        # amount is a fact about another row are both TRANSACTIONS -- an
        # envelope worth its purchases and a payback worth the spend it repays
        # -- and a purchase is what those rows are made OF.  Ruling **R-GE** is
        # what lets a match correct one even under a settled parent, and it
        # bounds that permission by the DOOR rather than by the row, so nothing
        # here narrows it further.  See
        # :attr:`~._subjects.CandidateRow.figure_is_correctable`.
        states_own_figure=True,
        parent_id=entry.transaction_id,
        # WHERE it is budgeted: its envelope's paycheck, read off the parent
        # this label already reads (plan step ``bank_import:X-gz``).
        period=calendar.period_by_id(entry.transaction.pay_period_id),
        # A purchase's budget clock is ONE day, so both ends of its window are
        # that day: it is not undated, it is dated on a clock the cash column
        # does not hold (ruling **R-FW**).  ``expected_on`` and
        # ``expected_through`` derive from it.
        purchased_on=entry.purchased_on,
        # WHICH KIND of day ``settled_on`` is, READ rather than inferred (plan
        # step **X-az**, finding **N-332**).  It tested ``reconciled_by_id`` --
        # a different question, WHICH statement was seen to show this money --
        # and that inference was exact over the panel's bound and the bank's
        # observation and blind to the owner's own typed day, which carries no
        # link and so read as an observation.  ``CandidateRow.expected_window``
        # is the single reader and states the measurement.
        # WHICH REVISION the screen is about to show (plan step
        # ``bank_import:X-f6d-3``, finding **N-336**).  Read here rather than
        # by the reader that emits it, for the reason every fact beside it is:
        # the OFFER SET and the ACCEPT DOOR both build a candidate through this
        # one constructor, so the state a review is checked against and the
        # state it was taken against come from the same read.
        version_id=entry.version_id,
        settle_day_basis=_day_basis(entry),
    )


def _states_own_figure(txn: Transaction) -> bool:
    """Return whether *txn*'s figure is its own to state.

    **The not-its-own-figure census, stated ONCE and here, because both
    members are load-bearing and one of them was missed** (plan step
    ``bank_import:X-f6d-1``) -- and read by two constructors since plan step
    ``credit_card:CC-5-4a-1``, a row's own candidate and its payment's, so
    it is a function rather than an expression each spells.
    ``transaction_service`` publishes exactly two predicates for *this figure
    is not this row's to state* and they are siblings by that module's own
    docstring: an ENVELOPE derives its figure from the purchases recorded
    against it, and a CC PAYBACK from the card spend of the row it names.
    Correcting either writes a number the next sibling write silently
    reverts (finding **N-252**), and the transaction door's own backstop
    (``_correction_for_status``) refuses only the FIRST -- a payback is
    refused at the PATCH route instead -- so a door reaching it from here
    would have written a ``corrected`` record onto a figure that is a fact
    about another row.  Measured by the batch suite's own stale-price case,
    which booked `-60.00` against a payback re-derived to `50.00`.

    A transfer SHADOW is the third member of that class and is NOT folded
    in: ``transfer_id`` beside it already states it, and what the owner must
    do about one is different (change the transfer, not a purchase), which
    is why the accept door gives it its own sentence.
    :attr:`~._subjects.CandidateRow.figure_is_correctable` is where the two
    facts are read together.

    Neither predicate costs a query: both offer arms eager load ``entries``
    and ``template``, which are all ``settles_from_entries`` reads, and
    ``repays_card_spend`` is a plain column.

    Args:
        txn: The row, with ``entries`` loaded.

    Returns:
        ``False`` for an envelope worth its purchases and for a CC payback.
    """
    return not (
        transaction_service.repays_card_spend(txn)
        or transaction_service.settles_from_entries(txn)
    )


def transaction_candidate(
    txn: Transaction, calendar: "PayCalendar", amount: Decimal,
) -> "CandidateRow | None":
    """Return one transaction as the candidate value every consumer shares.

    :func:`purchase_candidate`'s twin, and it exists for the same reason plus
    one more: **an act RE-PRICES the rows it names**
    (:func:`~._resolve.resolve_rows`), so the construction the offer set uses
    and the construction a write door uses have to be one.  Two would be two
    answers to what a row is worth and when the app believes it moved, on the
    two sides of a money gate.

    Args:
        txn: The row, with ``entries`` loaded.
        calendar: The pass's
            :class:`~app.services.pay_calendar.PayCalendar`, which the row's
            window is read from.
        amount: Its signed cash effect, already resolved by :func:`transaction_price` --
            taken rather than computed here because the caller has to tell an
            UNPRICEABLE row (reported) from a zero-valued one (not offerable),
            and a constructor returning ``None`` for both could not.

    Returns:
        Its :class:`~._subjects.CandidateRow`, or ``None`` when the row is worth
        nothing or its pay period is not one this calendar carries -- neither
        is offerable, and neither is an error.
    """
    if not amount:
        return None
    # The row's PAY PERIOD is the whole of what the app asserts about when this
    # money moves, so both ends travel and the proposer bounds the row by the
    # span rather than by its opening day (finding **N-312**).
    period = calendar.period_by_id(txn.pay_period_id)
    if period is None:
        return None
    return CandidateRow(
        kind=RowKind.TRANSACTION,
        row_id=txn.id,
        label=_label(txn),
        cash_amount=amount,
        settled_on=txn.settled_on,
        is_settled=txn.status.is_settled,
        states_own_figure=_states_own_figure(txn),
        transfer_id=txn.transfer_id,
        # Its own paycheck, whole: ``expected_on`` / ``expected_through`` are
        # its two ends, derived.
        period=period,
        # The same fact its twin carries, from the same column and for the same
        # reason.  A transaction settled through the reconcile panel takes the
        # assertion's day (``reconcile_service._transactions`` for a bill,
        # ``transfer_service._settle`` for a shadow leg), so its window opens at
        # the period rather than closing on that day.
        # WHICH REVISION the screen is about to show (plan step
        # ``bank_import:X-f6d-3``, finding **N-336**).  Read here rather than
        # by the reader that emits it, for the reason every fact beside it is:
        # the OFFER SET and the ACCEPT DOOR both build a candidate through this
        # one constructor, so the state a review is checked against and the
        # state it was taken against come from the same read.
        version_id=txn.version_id,
        settle_day_basis=_day_basis(txn),
    )


def settlement_price(
    entry: TransactionEntry, basis: "cash_ledger.AmountBasis",
) -> "Decimal | None":
    """Return what a row's covering *entry* is worth to a statement, or ``None``.

    Two arms, and the day is what tells them apart (plan step
    ``credit_card:CC-5-4a-1``):

    * a DATED movement is the row's settled record and is worth
      :func:`app.services.cash_ledger.movement_cash_leg` -- its stored figure
      in its parent's direction, the ONE valuation the fold and the ledger
      book for it (ruling **R-BAL35**), ``0.00`` under a parent that no
      longer contributes;
    * an UN-DATED one is a REVERTED row's kept record (ruling **R-BAL61**,
      retained where the money moved by ruling **R-CC42**), and a match
      re-settles that row through its own door, so it is worth what the door
      would book: :func:`transaction_price`, which honours a kept STATED figure and
      re-prices a ``resolved`` one -- not the movement's stored column,
      which for a ``resolved`` record is the figure of a plan the owner may
      have edited since.

    Args:
        entry: The covering movement, with its transaction loaded.
        basis: The pass's :class:`~app.services.cash_ledger.AmountBasis`.

    Returns:
        Its signed cash effect on the account it is on, or ``None`` when the
        amount model cannot price the row's re-settle.
    """
    if entry.settled_on is not None:
        return cash_ledger.movement_cash_leg(entry.transaction, entry)
    return transaction_price(entry.transaction, basis)


def row_is_offered_here(txn: Transaction, account_id: int) -> bool:
    """Return whether *txn* is a candidate AS A ROW on *account_id*'s screen.

    **The partition between the two arms, in Python** (plan step
    ``credit_card:CC-5-4a-1``, ruling **R-CC43**): a Projected row on the
    screen's account is offered as itself by :func:`~._candidates._transaction_candidates`,
    and a covering movement is offered by :func:`~._candidates._settlement_candidates`
    exactly when its row is NOT -- a settled row's, on whichever account the
    money moved; a reverted row's kept movement, where the row's screen is
    another account's.  One subject per row per screen, never both.  The
    two queries spell the same partition in SQL from the same two published
    predicates (``is_projected_clause``, ``Transaction.account_id``); this is
    :func:`repriced`'s re-read of it against the row as it stands NOW, so a
    kept movement whose row has since moved onto this account -- where the
    row is the candidate -- is declined rather than matched twice over.

    Args:
        txn: The row.
        account_id: The screen's account.

    Returns:
        ``True`` when the row itself is this screen's candidate.
    """
    return is_projected(txn) and txn.account_id == account_id


def _settlement_label(txn: Transaction, account_id: int) -> str:
    """Return what to call *txn*'s payment on *account_id*'s review screen.

    The row's own name (:func:`_label`), and where the row is budgeted on
    ANOTHER account -- a checking bill paid from the card, offered on the
    card's screen -- the account it is budgeted on, so the reviewer reading
    the card's feed is told which plan this payment settles.  The same fact
    the grid's account chip states for a cell whose row is elsewhere
    (plan step ``credit_card:CC-4-2``), spoken here in words because the
    review pane has no chip.

    Args:
        txn: The row the payment covers, with ``account`` loaded or loadable.
        account_id: The screen's account.

    Returns:
        The display label.
    """
    label = _label(txn)
    if txn.account_id == account_id:
        return label
    return f"{label} (budgeted on {txn.account.name})"


def settlement_candidate(
    entry: TransactionEntry, calendar: "PayCalendar", amount: Decimal,
    account_id: int,
) -> "CandidateRow | None":
    """Return one covering movement as the candidate value every consumer shares.

    The third constructor (plan step ``credit_card:CC-5-4a-1``, ruling
    **R-CC43**), and it exists for the reason the other two do: the offer
    set and the accept door's re-price build it through ONE function, so
    what a row's payment is worth and when the app believes it moved come
    from one read on both sides of the money gate.

    **A SETTLEMENT is the movement's identity with the ROW's record**
    (:class:`~._subjects.RowKind`): the member names the movement
    (``row_id``, on the account the money moved through), the price is the
    row's (*amount*, from :func:`settlement_price`), the window is the row's
    paycheck (``period``; no ``purchased_on``, a payment's stored purchase
    day being its settle day, ruling **R-BAL39**), the door is the row's
    (``parent_id`` is the row :func:`~._moving._apply_day` re-settles, with
    the movement's account as the tender -- an echo the door drops), and
    whether the bank's own figure may be written to it is the row's answer
    (``states_own_figure``; a shadow leg's ``transfer_id`` travels for the
    same reason it does on a TRANSACTION).  The REVISION is the movement's
    counter PLUS the row's: the seam mirrors the row's assertion onto the
    movement, so a settle, a revert, a day or figure correction and a tender
    re-point all UPDATE the movement -- but a reverted row can be moved to
    another paycheck or renamed without its kept movement changing, and the
    row's counter is what sees those.  Both counters only ever rise, so the
    sum rises on any change to either and a form drawn against an older
    state of EITHER home is refused as moved
    (:func:`~._resolve._reject_moved_since_review`); the review of this leaf
    named the movement's counter alone as narrower than its docstring
    claimed.

    Args:
        entry: The covering movement, with its transaction loaded.
        calendar: The pass's :class:`~app.services.pay_calendar.PayCalendar`,
            which the row's window is read from.
        amount: Its signed cash effect, already resolved by
            :func:`settlement_price` -- taken rather than computed here so the
            caller can tell an UNPRICEABLE row (reported) from a zero-valued
            one (not offerable), exactly as :func:`transaction_candidate` does.
        account_id: The screen's account, which decides the label and the
            partition (:func:`row_is_offered_here`).

    Returns:
        Its :class:`~._subjects.CandidateRow`, or ``None`` when the movement is
        worth nothing, is not ON this screen's account, its row is this
        screen's candidate as a row, or its pay period is not one this
        calendar carries -- none is offerable, and none is an error.  The
        account test is the offer set's first clause re-asked in Python for
        :func:`repriced`'s sake: a payment re-pointed onto another account
        since the screen offered it (a "Paid from" correction, plan step
        ``credit_card:CC-5-3``) is refused as no longer available rather
        than written as a member the key
        ``fk_statement_match_members_entry_account`` would reject at the
        flush -- a first cut of this leaf declined it in SQL alone, and its
        own re-price docstring claimed a refusal it did not make.
    """
    if not amount or entry.account_id != account_id:
        return None
    txn = entry.transaction
    if row_is_offered_here(txn, account_id):
        return None
    period = calendar.period_by_id(txn.pay_period_id)
    if period is None:
        return None
    return CandidateRow(
        kind=RowKind.SETTLEMENT,
        row_id=entry.id,
        label=_settlement_label(txn, account_id),
        cash_amount=amount,
        settled_on=entry.settled_on,
        is_settled=entry.settled_on is not None,
        # The ROW's answer: the row's door refuses a figure on a row whose
        # figure is not its own to state, and this is that row's record.
        states_own_figure=_states_own_figure(txn),
        transfer_id=txn.transfer_id,
        parent_id=txn.id,
        period=period,
        version_id=entry.version_id + txn.version_id,
        settle_day_basis=_day_basis(entry),
    )


def repriced(
    row: CandidateRow, calendar: "PayCalendar",
    basis: "cash_ledger.AmountBasis", account_id: int,
) -> "CandidateRow | None":
    """Return *row* as it stands NOW, re-read and re-valued on *account_id*.

    **The scope answers WHICH rows an act may reach; this answers what one of
    them is WORTH, and the two must be asked at different moments.**  Plan step
    ``bank_import:X-f6a-3c-2`` first shared both, on the argument that the only
    way one act can move another's figure is by adding a purchase to it or
    posting one under it -- which makes the two an envelope and its own child,
    and is refused.  **Adversarial financial review measured that argument
    false on 2026-08-19**, with a counterexample and a booked figure:

    ``entry_service.update_entry`` -- which every matched PURCHASE goes through
    -- calls ``entry_credit_workflow.sync_entry_payback``, and that WRITES the
    envelope's CC Payback ``estimated_amount``.  A payback is a transaction on
    the SAME account, so it is a candidate, and it is priced from that column.
    The purchase and the payback are SIBLINGS under one envelope rather than a
    parent and its own child, so no guard here can see the relation.  Measured:
    matching a `$25.00` purchase and then the payback drops the payback from
    `$60.00` to `$50.00`, and the second match is accepted against the stale
    `$60.00` -- the ledger books `$50.00` for a `-$60.00` bank line and the
    account reads **`$10.00` high**.  A fresh derivation refuses it by name.

    **So the figure is re-derived per act and the enumeration is abandoned.**
    Enumerating sibling writes is a guard the next unenumerated writer
    reopens; re-pricing is total.  It is also cheap in the only way that
    matters: the 3.593 s belongs to the 827-row SCAN, which is still derived
    once, and an act names one to four rows.

    Args:
        row: The candidate the scope offered.
        calendar: The pass's
            :class:`~app.services.pay_calendar.PayCalendar`.
        basis: The pass's
            :class:`~app.services.cash_ledger.AmountBasis` (plan step X-au-j).
            **Sharing it does NOT weaken the re-derivation this function
            exists for**, and the counterexample above is why it cannot: an
            :class:`~app.services.cash_ledger.AmountBasis` holds the owner's
            salary and loan DERIVATIONS, never a per-row answer, and the
            sibling write that defect turns on writes a ROW's own column.
            This re-reads the row and its entries from the database either
            way.  Nothing an accept act does -- settling rows, creating
            purchases -- writes a salary profile, a payday or a loan
            parameter, which is the same argument that lets the calendar
            beside it be shared across the pass.
        account_id: The pass's account (:class:`~._scope.ReviewScope`), the
            screen a SETTLEMENT is re-read for (:func:`settlement_candidate`:
            its partition and its label) -- so a payment re-pointed onto
            another account since the screen offered it, or one whose row has
            since become this screen's candidate as a row, re-prices to
            nothing here and the act naming it is refused rather than booked
            against money this account never moved.

    Returns:
        The row as it stands now, or ``None`` when it has gone, cannot be
        priced, or is no longer worth anything -- each of which means the act
        naming it must be refused rather than applied against a stale figure.
        **A TRANSACTION that has SETTLED since it was offered is ``None``
        too** (plan step ``credit_card:CC-5-4a-1``, ruling **R-CC43**): its
        subject is its movement now, and the act that named the row is a
        stale form.
    """
    if row.kind is RowKind.PURCHASE:
        return _repriced_purchase(row, calendar)
    if row.kind is RowKind.SETTLEMENT:
        return _repriced_settlement(row, calendar, basis, account_id)
    return _repriced_transaction(row, calendar, basis)


def _repriced_purchase(
    row: CandidateRow, calendar: "PayCalendar",
) -> "CandidateRow | None":
    """Return :func:`repriced`'s PURCHASE arm: the purchase as it stands now."""
    entry = db.session.get(TransactionEntry, row.row_id)
    if entry is None or not entry.amount:
        return None
    return purchase_candidate(entry, calendar)


def _repriced_settlement(
    row: CandidateRow, calendar: "PayCalendar",
    basis: "cash_ledger.AmountBasis", account_id: int,
) -> "CandidateRow | None":
    """Return :func:`repriced`'s SETTLEMENT arm: the payment as it stands now.

    Re-read by the movement's id and re-valued by :func:`settlement_price`,
    then rebuilt for *account_id*'s screen, whose partition
    (:func:`row_is_offered_here`) is re-asked against the row as it stands.
    An entry that is no longer a covering movement is not this subject and
    answers ``None`` -- unreachable through any door (the mark is written once
    and never cleared; the seam deletes the row instead), stated so a stale
    id cannot be re-valued as a purchase.
    """
    entry = db.session.get(TransactionEntry, row.row_id)
    if entry is None or not entry.covers_settlement:
        return None
    amount = settlement_price(entry, basis)
    if amount is None:
        return None
    return settlement_candidate(entry, calendar, amount, account_id)


def _repriced_transaction(
    row: CandidateRow, calendar: "PayCalendar",
    basis: "cash_ledger.AmountBasis",
) -> "CandidateRow | None":
    """Return :func:`repriced`'s TRANSACTION arm: the Projected row as it stands now.

    A row that has SETTLED since it was offered answers ``None`` (plan step
    ``credit_card:CC-5-4a-1``, ruling **R-CC43**): its subject is its
    covering movement now, and an act still naming the row is a stale form
    the caller refuses.
    """
    txn = db.session.get(Transaction, row.row_id)
    if txn is None or txn.status.is_settled:
        return None
    amount = transaction_price(txn, basis)
    if amount is None:
        return None
    return transaction_candidate(txn, calendar, amount)
