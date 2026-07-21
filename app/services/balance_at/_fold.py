"""Balance-at-T seam -- the loan fold: a WALK of facts sampled into a balance.

Plan step **D-fold** (``docs/audits/balance_architecture/README.md``).  "A loan's
balance is a fold over its event stream" (Section 3): this module owns the FOLD --
the balance half -- while the ``loan_ledger`` leaf owns the WALK it samples (the
facts).  The split is the step's whole point: *a fold is a balance; a walk is a
fact.*

The leaf's :func:`app.services.loan_ledger.walk_loan_ledger` replays a loan's
anchors and settled payments into per-payment splits and per-anchor corrections in
CONTRACT-time order -- FACTS, which both the posting writer and the seam need.
Turning those facts into "what is owed on date D" is what this module does, and it
does it the way the whole arc turns on (Section 3):

* re-key each event by the date it became VISIBLE (:func:`_dated_deltas`), then
* prefix-sum the paydowns and read each requested date off the running total
  (:func:`sample_cumulative`) -- ``0.00`` for a date before any event, the honest
  fold of an empty prefix.

**Why the fold is in the seam and the walk is not.**  Because it lives here, a
consumer that legitimately holds a :class:`~app.services.loan_ledger.LoanLedgerWalk`
(the writer, the read pass) cannot reach a balance from a public leaf name -- the
only code that turns a walk into money is seam-private.  That is what lets the walk
shed its fence entry (a walk one call from a balance had to be fenced; a walk that is
not, does not).  This is the balance-side twin of the forward fold
(:func:`app.services.balance_at._plan.fold_forward`), which has lived seam-private
since step C6a; the two share the one date-sampling core :func:`sample_cumulative`,
so the past and the future cannot drift on how a running balance is read at a date.

**TOTAL and clock-free.**  :func:`fold_loan_balances` refuses no date and no
account: a date before any event, or an account with no
:class:`~app.models.loan_params.LoanParams`, folds to ``0.00``; a future date
answers (holding the last recorded balance flat) rather than raising.  That totality
is the property that deletes the posting readers' partiality -- there is nothing to
compose it with, so no seed / flag / splice / fallback can disagree with it.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from bisect import bisect_right
from datetime import date
from decimal import Decimal

from app.services.loan_ledger import (
    LoanLedgerWalk,
    anchor_visible_on,
    payment_visible_on,
    walk_loan_ledger,
)
from app.utils.money import round_money

_ZERO_MONEY = Decimal("0.00")


def sample_cumulative(
    start: Decimal,
    steps: list[tuple[date, Decimal]],
    dates: list[date],
) -> dict[date, Decimal]:
    """Prefix-sum date-keyed *steps* from *start* and read each of *dates* off it.

    The shared date-sampling core of every running-balance fold: given a base
    value and a list of ``(date, delta)`` steps ALREADY ascending by date, it
    accumulates the running total (collapsing steps that share a date to their
    combined prefix) and answers each requested date with the cumulative at the
    latest step on or before it -- one bisect per date, not a re-sum.  A date
    before every step reads *start* (the empty prefix).

    Both the ACTUAL past fold (:func:`fold_from_walk`, seeded at ``0.00`` over the
    walk's dated principal deltas) and the forward projection fold
    (:func:`app.services.balance_at._plan.fold_forward`, seeded at the confirmed
    present over the plan's paydowns) sample through here, so the past and the
    future cannot drift on how a running balance is read at a date.

    Args:
        start: The balance before any step -- ``0.00`` for the from-zero event
            fold, the seed balance for the forward projection.
        steps: ``(date, delta)`` pairs, ASCENDING by date (the caller sorts).
        dates: The dates to value, in any order.  Duplicates collapse.

    Returns:
        ``{date: cent-quantized cumulative}`` -- one per distinct requested date.
    """
    boundaries: list[date] = []
    cumulative_at_boundary: list[Decimal] = []
    running = start
    for on_date, delta in steps:
        running += delta
        if boundaries and boundaries[-1] == on_date:
            cumulative_at_boundary[-1] = running
            continue
        boundaries.append(on_date)
        cumulative_at_boundary.append(running)

    sampled: dict[date, Decimal] = {}
    for on_date in dates:
        # The count of boundaries at or before this date; the last one's prefix is
        # the answer (start when none precede -- the empty prefix).
        count = bisect_right(boundaries, on_date)
        sampled[on_date] = round_money(
            cumulative_at_boundary[count - 1] if count > 0 else start
        )
    return sampled


def _dated_deltas(walk: LoanLedgerWalk) -> list[tuple[date, Decimal]]:
    """Return the walk's ``(visible_on, delta)`` steps, ascending by visible date.

    The bridge from the walk (which orders events by when they HAPPENED, in
    CONTRACT time) to a balance read (which counts them from when they became
    VISIBLE).  Each event contributes the amount it moved the running balance by:

    * an anchor: ``anchor_balance - owed_before`` -- the jump its reset booked;
    * a payment: ``-principal`` -- the debt its cash actually paid down.

    Those are exactly the amounts the posting writer books onto the loan's linked
    ledger (negated for the debit-positive convention), which is why prefix-summing
    them reproduces the sum-of-postings readers.

    **The deltas are computed in EVENT (contract) order and then re-keyed by
    VISIBLE date, and that is deliberate.**  A payment's split depends on the
    balance at its installment, so the walk must run in due-date order
    (:func:`app.services.loan_ledger.merge_anchor_and_payment_events`); the ledger
    then stores those amounts and a reader sums whichever are visible.  Under step
    C2's one clock a payment's visible date is its SETTLED date and an anchor's is
    its own date (:mod:`app.services.loan_ledger._visible`) -- the same day each
    posting carries in ``entry_date`` -- so a late-settled payment's principal is
    shown from the day its cash moved, while its split stays fixed to the
    installment it paid.  Visibility no longer needs the owner's calendar, so this
    is a pure re-key of the walk with no query.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`
            (:func:`app.services.loan_ledger.walk_loan_ledger`).

    Returns:
        ``[(visible_on, delta), ...]`` ascending by ``(visible_on, tag)``, where a
        PAYMENT tags before an ANCHOR on a shared date -- mirroring the walk's own
        tie-break (:func:`app.services.loan_ledger.merge_anchor_and_payment_events`),
        so reading the list shows the same chronology the walk applied.  The order
        within a date is immaterial to the prefix sum (addition commutes); mirroring
        it keeps the two chronologies reading identically.
    """
    if not walk.anchor_corrections:
        # No LoanParams -> no facts at all (walk_loan_ledger's N1 guard; a
        # configured loan ALWAYS has its opening fact, per
        # ``load_loan_anchor_facts``).  Nothing to date.
        return []
    # Tag 0 = payment, 1 = anchor: the same tie-break the walk applies, so a
    # payment sharing an anchor's date reads before it here too.
    tagged: list[tuple[date, int, Decimal]] = [
        (
            anchor_visible_on(correction.anchor.anchor_date),
            1,
            correction.anchor.anchor_balance - correction.owed_before,
        )
        for correction in walk.anchor_corrections
    ] + [
        (payment_visible_on(split.income_shadow), 0, -split.principal)
        for split in walk.payment_splits
    ]
    tagged.sort(key=lambda step: (step[0], step[1]))
    return [(visible_on, delta) for visible_on, _tag, delta in tagged]


def fold_from_walk(
    walk: LoanLedgerWalk, dates: list[date],
) -> dict[date, Decimal]:
    """Sample an ALREADY-COMPUTED walk at each of *dates* -- the fold's core.

    The date-sampling half of :func:`fold_loan_balances`, taking the walk as a
    parameter rather than computing it, so a read pass that folds one loan at
    several date lists (the seam's scalar, per-period map, and liability band all
    read :func:`app.services.balance_at.positions`) can walk it ONCE and sample the
    memoized walk here each time -- the redundant-derivation the read pass's
    context exists to kill (:meth:`~app.services.balance_at.BalanceContext.loan_walk`).

    Re-keys each event by its visible-on date (:func:`_dated_deltas`), prefix-sums
    the deltas, and reads each requested date off the cumulative -- ``0.00`` for a
    date before any event (the empty prefix's honest fold).  Identical output to
    :func:`fold_loan_balances` for the same walk, so threading a memoized walk
    through here moves no balance.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`
            (:func:`app.services.loan_ledger.walk_loan_ledger`).
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.
    """
    # The fold's balance seeds at ZERO and the empty prefix (before any event)
    # is 0.00 -- the honest fold of no facts.
    return sample_cumulative(_ZERO_MONEY, _dated_deltas(walk), dates)


def fold_loan_balances(
    loan_account_id: int,
    scenario_id: int,
    dates: list[date],
) -> dict[date, Decimal]:
    """Return the loan's folded balance at each of *dates* -- from SOURCE events.

    The fold's read side, and the reference the optimized readers are graded
    against (plan step B2).  ONE walk of the loan's facts
    (:func:`app.services.loan_ledger.walk_loan_ledger`), sampled at every requested
    date (:func:`fold_from_walk`) -- so N dates cost one walk, not N.  A read pass
    that would fold the same loan more than once memoizes the walk on its context
    and calls :func:`fold_from_walk` directly; this entry is the standalone form for
    a caller (B2's oracle) that holds no context.

    **It reads the loan's SOURCE rows and never the postings table.**  That is the
    whole point: the posted ledger is a PROJECTION of this fold, so an answer
    derived independently of it is what can prove the projection faithful.  Where
    the two disagree, the ledger is a stale cache -- a detectable, repairable
    inconsistency -- rather than the outage a missing posting is today.

    **TOTAL over every DATE and every ACCOUNT.**  There is no date it refuses and
    no account it refuses: asked about a date before any event -- or about an
    account with no :class:`~app.models.loan_params.LoanParams` at all -- it
    returns ``0.00``, the correct fold of an empty prefix; asked about a future
    date it answers rather than raising.  This is the property the whole arc turns
    on (plan Section 3): the posting readers' partiality (``None`` for an unopened
    ledger, a RAISE for a future date) is what forces every caller to compose them
    with a projection, a seed, a flag or a fallback, and every composition is a new
    producer that can disagree with the others.  A total function has nothing to
    compose with.  A caller that must distinguish "owed nothing" from "no loan"
    asks the FACT (``origination_date``), never this function's zero.

    That totality is genuine since step C2: an event's visible-on date is its own
    date (an anchor) or its settled date (a payment), neither of which needs the
    owner's calendar, so the fold no longer has a "loan has facts but its owner has
    no pay periods" state to raise on.  It answers from the loan's facts alone.

    **ACTUAL events only.**  It folds what is RECORDED -- the loan's anchors and
    its settled payments -- so it answers the past.  PLANNED payments (the future)
    are the seam's forward fold (:func:`app.services.balance_at._plan.fold_forward`
    over the loan's plan); asked about a future date this holds the last recorded
    balance flat, which is honest for what it knows but is NOT the projection the
    seam shows.  Grade it on the past.

    **N-11 (a raw transaction typed onto a loan) is closed by construction (BG,
    ruling R-E), not a live gap.**  A raw settled transaction typed directly onto a
    loan account would post onto the loan's linked ledger where the sum-of-postings
    readers count it, while the fold -- whose payment set is transfer-linked shadows
    only (:func:`~app.services.loan_loaders.settled_income_shadows`) -- would not
    see it, so the two would disagree by that transaction's amount ($300.00
    measured).  Ruling R-E FORBIDS that write at every source (the two
    transaction-create routes, the recurrence-template form, and the salary-profile
    picker each refuse an amortizing account -- BG, ``dba91dc0``), so no such row
    can enter the fold's domain; B2 both demonstrates the divergence on a forced
    row and asserts the sources refuse it.  Do NOT read a disagreement in that
    shape as a stale cache to be repaired.

    Reads only -- no writes, no commit.  This standalone entry (walk + sample in
    one call) is B2's parallel-run oracle subject; it DELEGATES to
    :func:`fold_from_walk`, which is the code the seam's AMORTIZING dispatch now
    runs in production (over the read pass's memoized walk, step C3b) -- so the
    oracle grades the exact sampling the user's balance is answered by, not a copy
    of it.

    Args:
        loan_account_id: The loan account to fold.
        scenario_id: The budget scenario the payments live in.
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.
    """
    return fold_from_walk(
        walk_loan_ledger(loan_account_id, scenario_id), dates,
    )
