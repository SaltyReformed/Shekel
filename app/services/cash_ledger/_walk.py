"""The cash walk: ONE running-balance replay over ONE event stream -- FACTS.

The single chronological walk a cash account's balance derives from, and the
exact counterpart of :mod:`app.services.loan_ledger._walk`.  It seeds the balance
at zero and, in event order (:mod:`._events`), applies each ASSERTION as a RESET
and each settled source as a signed step -- so an account's opening, every
true-up, and every settle come from ONE running balance and can never disagree
about which settles a given assertion already covered.

**The walk yields FACTS, not a balance-at-T.**  Its output is a
:class:`CashLedgerWalk`: one :class:`~._events.CashSourceFact` per settled row and
one :class:`CashAnchorCorrection` per assertion, in INSTANT order.  Turning those
facts into "what is the balance on date D" is the FOLD -- re-key each event by the
day it becomes visible (:func:`dated_deltas`), prefix-sum, sample -- and the
prefix-sum lives in the balance seam, not here.  A consumer holding a walk
therefore cannot reach a balance from a public leaf name, which is why the walk
needs no call fence (plan step D-fold's ruling, restated for cash).

**Two consumers, one walk** (ruling R-H).  The seam's read pass folds it into a
balance at a date; at plan step X-d the posting writer projects it into the
balanced corrections it reconciles onto the general ledger, replacing the
postings-sourced :func:`app.services.account_posting_service.walk_account_ledger`.
Today those are two independent statements of what happened to an account -- one
period-granular over transaction rows, one instant-granular over the postings it
is correcting -- and their disagreement IS the defect Phase X exists to close
(findings cash D1-D4).  One walk closes it by construction rather than by a test
holding two implementations in step.

**Takes no as-of, and reads no clock.**  Its output is a function of the
account's data ALONE, which is what makes it re-derivable; deciding which facts
have HAPPENED as of a date belongs to a reader.  PLANNED (still-Projected) rows
are not in it at all: their effective date depends on the reader's as-of (ruling
R-G), so they are the seam fold's tier, exactly as the loan plan's PLANNED tier
lives in ``balance_at._plan`` and not in ``loan_ledger``.

**Three differences from the account POSTING walk, all deliberate, all settled at
X-d.**  (1) This walk reads SOURCE rows where that one reads back the postings;
that is the direction the whole arc turns on -- the posted ledger is a projection
of the facts, not a second opinion about them (plan Section 1, root cause 2).
(2) It does not refuse an amortizing account.  That refusal is a WRITE concern
(which correction family a loan's anchors book into), not a property of a
running-balance replay, and the cash-flow seam view deliberately consults no kind
-- its balance must reconcile with the transaction rows rendered beside it,
whatever the account.  What keeps a LOAN out of that view is a gate at the
SOURCE, on every resolver that feeds it: ``resolve_grid_account`` since ruling
D4 / plan step A1, and ``resolve_analytics_account`` since plan step X-a1, which
closed the calendar door finding N-38 measured open (the Van Loan rendered at
``$531.94`` against ``$15,663.59`` owed).  So this walk stays total and
kind-blind, the writer keeps the guard it needs, and no screen reaches a
cash-basis loan balance through either.  (3) It sees no RESIDUE.  The
posting walk reads a third source bucket -- entries whose ``transaction_id`` /
``transfer_id`` were SET-NULLed by a hard delete -- precisely so its running total
"stays equal to the live linked total even if the reverse-before-delete
discipline is ever violated".  A source-row walk cannot see a posting whose
source row is gone, by construction, so X-d must decide whether that defence
moves to the checked-projection assert (where a residue row becomes a LOUD
mismatch instead of a silently absorbed one) or is ceded; it is named here so
that decision is made rather than discovered.

Reads the account's rows; no writes, no commit.

Plan of record: ``docs/audits/balance_architecture/README.md`` (step X-a).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.utils.dates import utc_civil_date

from ._events import (
    CashAnchorFact,
    CashSourceFact,
    cash_anchor_facts,
    merge_anchor_and_cash_events,
    settled_cash_facts,
)

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class CashAnchorCorrection:
    """One assertion's balance correction: an opening or a true-up.

    The per-anchor result of :func:`walk_cash_ledger`.  The correction's delta is
    ``anchor_balance - balance_before``: the jump the user's assertion booked
    over what the recorded facts alone would have produced.  A correction whose
    ``balance_before`` already equals the asserted balance books nothing, which
    is the healthy steady state -- an account whose every movement is recorded
    needs no correction at all.

    On the write side (plan step X-d) each of these becomes a balanced journal
    entry, the opening tagged ``account_opening`` and every later one
    ``account_trueup``, exactly as
    :class:`app.services.account_posting_service.AccountAnchorCorrection` is
    today.

    Attributes:
        anchor: The :class:`~._events.CashAnchorFact` this correction books for.
        balance_before: The walk's running balance JUST BEFORE this assertion
            resets it -- ``Decimal("0.00")`` for an opening with no pre-assertion
            settled history, and otherwise the sum of every source attributed at
            or before the assertion instant, on top of the prior assertions.
    """

    anchor: CashAnchorFact
    balance_before: Decimal


@dataclass(frozen=True)
class CashLedgerWalk:
    """An account's full walk output for one scenario: sources and corrections.

    The complete output of the single chronological replay
    (:func:`walk_cash_ledger`): every settled source fact AND every assertion
    correction, sharing ONE running balance so they are guaranteed mutually
    consistent.

    Carries no as-of, because the walk takes none: it is the account's FACTS
    replayed, whole, and a reader bounds them to a date.

    Attributes:
        source_facts: One :class:`~._events.CashSourceFact` per settled
            balance-contributing row, ascending by
            ``(occurred_at, transaction_id)`` -- including rows attributed BEFORE
            the account's opening assertion, which the opening's correction
            absorbs (the same treatment the loan walk gives a pre-origination
            payment).
        anchor_corrections: One :class:`CashAnchorCorrection` per assertion the
            account carries, chronological.
    """

    source_facts: list[CashSourceFact]
    anchor_corrections: list[CashAnchorCorrection]


def walk_cash_ledger(account_id: int, scenario_id: int) -> CashLedgerWalk:
    """Replay an account's assertions and settled rows into one running balance.

    Seeds the running balance at zero and, in event order
    (:func:`._events.merge_anchor_and_cash_events`), either records an assertion's
    correction and RESETS the balance to the asserted value, or advances the
    balance by a settled source's signed delta.

    **Resetting at EVERY assertion -- not seeding from the latest one -- is the
    step's point.**  The shipping projection reads only the newest anchor
    (:func:`app.services.cash_ledger.resolve_anchor`) and carries it BACKWARD over
    the past, which is why a pre-anchor date reads today's balance rather than
    the balance the user actually asserted then (finding B-18 / cash D3: measured
    on production 2026-07-25, the scalar answers ``$2,932.41`` for 2026-06-03
    while the period map omits those 8 periods entirely).  Replaying all of them
    -- 52 on the real Checking account over 119 days -- means the past is the
    assertion history rather than a back-projection of the present.

    **A settled source attributed AFTER the latest assertion rides on top of it,
    and that is the money the app currently loses.**  Today the projection
    excludes every settled row (the anchor is assumed to reflect them) and the
    anchor predates them, so they are counted by NO producer until the user
    re-asserts the balance: measured on production 2026-07-25, ``$2,108.15``
    invisible at that instant, and ``$53,880.81`` gross across 130 rows over 45
    assertion gaps historically (finding cash D1).

    Reads only (no writes, no commit).

    Args:
        account_id: The account whose ledger to walk.
        scenario_id: The budget scenario whose settled rows to walk against.
            Assertions are per-ACCOUNT (``AccountAnchorHistory`` carries no
            scenario), so the same assertions walk in every scenario against that
            scenario's own rows -- the same split
            :func:`app.services.account_posting_service.walk_account_ledger`
            documents.

    Returns:
        A :class:`CashLedgerWalk` (source facts + assertion corrections, both
        chronological).  Both lists are EMPTY when the account carries no
        assertion history -- production-unreachable (migration ``cfb15e782f86``
        plus the account factory guarantee an opening row), and returned rather
        than raised because a walk of no facts is honestly empty; the caller that
        must distinguish "no account" asks the account row, never this emptiness.
    """
    anchors = cash_anchor_facts(account_id)
    if not anchors:
        return CashLedgerWalk([], [])

    sources = settled_cash_facts(account_id, scenario_id)
    corrections: list[CashAnchorCorrection] = []
    running = _ZERO_MONEY
    for _instant, is_anchor, item in merge_anchor_and_cash_events(
        anchors, sources,
    ):
        if is_anchor:
            corrections.append(
                CashAnchorCorrection(anchor=item, balance_before=running)
            )
            # The assertion resets the walked total to the asserted balance --
            # the user's declaration outranks the recorded facts before it.
            running = item.anchor_balance
            continue
        running += item.delta
    return CashLedgerWalk(sources, corrections)


def dated_deltas(walk: CashLedgerWalk) -> list[tuple[date, Decimal]]:
    """Return the walk's ``(visible_on, delta)`` steps, ascending by date.

    The bridge from the walk (events ordered by the INSTANT they happened) to the
    civil day each event COUNTS FROM -- the cash twin of
    :func:`app.services.loan_ledger.dated_deltas`, and the ONE statement of that
    clock.  Each event contributes the amount it moved the running balance by:

    * an assertion: ``anchor_balance - balance_before`` -- the jump its reset
      booked;
    * a settled source: its signed ``delta`` -- the cash that moved.

    **These are the amounts the posting writer books onto the account's LINKED
    ledger, in the same sign -- NOT their negatives.**  The loan twin says
    "negated" and is right to, because a loan walk tracks OWED against a
    credit-normal liability ledger; cash does not.  An assertion's
    ``anchor_balance`` is ledger-native (an owed-as-negative liability anchor
    stays negative) and
    :func:`app.services.cash_ledger.settled_cash_leg` is debit-positive, so this
    walk's running balance IS the linked ledger's balance in one convention, for
    assets and liabilities alike.  Verified against both writers:
    ``posting_service._settled_target`` books ``settled_cash_leg(txn)`` -- this
    module's :attr:`~._events.CashSourceFact.delta` -- onto the linked ledger,
    and ``account_posting_service._anchors`` books
    ``anchor_balance - ledger_before`` onto it.  The NEGATIVES are the counter
    legs (the category ledger, the anchor-equity account).  Getting this backwards
    is not a cosmetic error: plan step X-d wires the writer onto this walk, and a
    sign flip there still balances every entry -- so the trial balance closes and
    only the balance sheet is upside down.

    That equality is why the re-key lives on the LEAF: two consumers, one
    derivation.  A third statement of "which day does this event count from, and
    for how much" is precisely how the fold and the posted ledger drift apart
    (plan step E1a's finding, on the loan side).

    **Sources attributed BEFORE the account's opening assertion are emitted at
    their OWN dates, and that is deliberate but not yet ruled.**  The walk
    absorbs them into the opening's correction (they are inside the asserted
    balance), so the running total lands exactly on the assertion at its date and
    every date after -- but the prefix at a date BEFORE the opening is those
    sources summed from a zero seed, which is not a balance the account ever had.
    It is faithful to the POSTED ledger, which holds the same partial sum there,
    so keying them onto the opening instead would break the equality above.  On
    production data 2026-07-25 two accounts carry the shape (Fidelity Savings 1
    row, the Money Market 4), and the prefix reads ``$500.00`` on both.  What a
    READER should answer before an account's first assertion is the fold's
    ruling, recorded as finding **N-37** for plan step X-b; this leaf states the
    facts and does not decide it.

    **Deltas are computed in INSTANT order and then re-keyed by civil DAY, and
    that is deliberate.**  Which settles an assertion already covers turns on
    order within a day (see :func:`._events.merge_anchor_and_cash_events`), while
    "what is the balance on date D" is a question about days.  Doing the
    partition first and the re-key second answers both without either rule
    reaching into the other -- and it is safe to collapse same-day steps
    afterwards because a prefix sum does not care in what order a day's deltas
    are added, only which side of the assertion each was computed on, which the
    walk has already settled.

    **Dated FACTS, not a balance-at-T** (the W9909 ruling): each pair says what
    ONE event contributed and when it counts, both readable off the public source
    facts and corrections.  Turning them into a balance is the prefix-sum, which
    is seam-private.

    Args:
        walk: The account's :class:`CashLedgerWalk` (:func:`walk_cash_ledger`).

    Returns:
        ``[(visible_on, delta), ...]`` ascending by ``(visible_on, tag)``, where a
        SOURCE tags before an ASSERTION on a shared date -- mirroring the walk's
        own tie-break, so reading the list shows the same chronology the walk
        applied.  Empty for a walk with no facts.
    """
    # Tag 0 = source, 1 = assertion: the same tie-break the walk applies, so a
    # source sharing an assertion's day reads before it here too.
    tagged: list[tuple[date, int, Decimal]] = [
        (fact.visible_on, 0, fact.delta) for fact in walk.source_facts
    ] + [
        (
            utc_civil_date(correction.anchor.asserted_at),
            1,
            correction.anchor.anchor_balance - correction.balance_before,
        )
        for correction in walk.anchor_corrections
    ]
    tagged.sort(key=lambda step: (step[0], step[1]))
    return [(visible_on, delta) for visible_on, _tag, delta in tagged]
