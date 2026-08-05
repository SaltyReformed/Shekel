"""The cash walk: ONE running-balance replay over ONE event stream -- FACTS.

The single chronological walk a cash account's balance derives from, and the
exact counterpart of :mod:`app.services.loan_ledger._walk`.  It seeds the balance
at zero and, in event order (:mod:`._events`), applies each ASSERTION as a RESET
and each settled source as a signed step -- so an account's opening, every
true-up, and every settle come from ONE running balance and can never disagree
about which settles a given assertion already covered.

**The walk yields FACTS, not a balance-at-T.**  Its output is a
:class:`CashLedgerWalk`: one :class:`~._events.CashSourceFact` per settled row and
one :class:`CashAnchorCorrection` per assertion, in DAY order -- every source
dated a day, then the assertions that close it (ruling R-DH).  Turning those
facts into "what is the balance on date D" is the FOLD -- collect each event's
dated delta (:func:`dated_deltas`), prefix-sum, sample -- and the prefix-sum
lives in the balance seam, not here.  A consumer holding a walk therefore cannot
reach a balance from a public leaf name, which is why the walk needs no call
fence (plan step D-fold's ruling, restated for cash).

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

from ._amounts import ReconciledThrough
from ._events import (
    CashAnchorFact,
    CashSourceFact,
    cash_anchor_facts,
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

    @property
    def observed_on(self) -> date:
        """Return the civil day this correction is the closing balance FOR.

        The assertion twin of :attr:`~._events.CashSourceFact.settled_on`, and
        the same rule: the day is resolved ONCE on the fact this correction wraps
        (ruling R-DH) and every consumer reads that one field rather than
        re-deriving one from a timestamp.

        It reads THROUGH to the fact rather than re-deriving, which is the point:
        the property existed to convert ``asserted_at`` to a UTC civil day, and
        that conversion was a second statement of a rule the fact now owns.

        Returns:
            The civil day of the assertion this correction books.
        """
        return self.anchor.observed_on

    @property
    def delta(self) -> Decimal:
        """Return the jump this assertion's RESET booked over the records.

        ``anchor_balance - balance_before``: what the user's declaration moved
        the running balance by, on top of what the recorded facts alone had
        produced.  ``0.00`` for the healthy steady state (an account whose every
        movement is recorded needs no correction).

        **Stated here so it is stated ONCE.**  Three readers need this pair --
        :func:`dated_deltas` below, the fold's R-I seed
        (``balance_at._cash_fold._actual_steps``), and the period view's
        assertion component (``balance_at._cash_periods.cash_period_view``, plan
        step X-c1) -- and until X-c1 the fold RE-DERIVED it, which its own
        docstring had to pin with a test.  A property on the record retires that
        re-derivation, exactly as :class:`~._events.CashSourceFact` already
        carries its own ``settled_on`` / ``delta``.

        Returns:
            The signed correction as a ``Decimal``, in the same LEDGER-NATIVE
            sign as the asserted balance.
        """
        return self.anchor.anchor_balance - self.balance_before


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
            ``(settled_on, transaction_id)`` -- including rows dated BEFORE
            the account's opening assertion, which the opening's correction
            absorbs (the same treatment the loan walk gives a pre-origination
            payment).
        anchor_corrections: One :class:`CashAnchorCorrection` per assertion the
            account carries, chronological.
    """

    source_facts: list[CashSourceFact]
    anchor_corrections: list[CashAnchorCorrection]

    @property
    def reconciled_through(self) -> ReconciledThrough:
        """Return the coverage boundary the account's LAST assertion establishes.

        The boundary every "is this already inside the balance the user
        declared" question is asked through
        (:meth:`app.services.cash_ledger.ReconciledThrough.covers`): the
        modelled accrual window opens on it (ruling R-L), and the entry
        reservation reconciles a purchase against it (ruling R-DH (d)).

        **It reads the LAST element and that is only correct because of where
        the ordering lives.**  ``cash_anchor_facts`` loads its rows
        ``(observed_on, created_at, id)`` ascending -- BUSINESS date first --
        so the last correction is the one the walk replayed last and the one
        ``resolve_anchor`` calls current.  Re-deriving "the latest" with a
        ``max()`` here would be a second statement of that order, and the two
        agreed for free only while ``observed_on`` was derived from
        ``created_at``; plan step 2 made the column user-supplied and broke
        that, which is exactly how a ``$1,307.66`` true-up once posted to the
        ledger tagged as the account's OPENING.

        Returns:
            The account's :class:`~app.services.cash_ledger.ReconciledThrough`.
            Its ``observed_day`` is ``None`` for an account with no assertion
            history -- production-unreachable (migration ``cfb15e782f86`` plus
            ``account_service.create_account`` guarantee an opening row) -- so
            such an account reconciles nothing rather than raising, because a
            walk of no facts is honestly empty.  A consumer that cannot proceed
            without an assertion refuses on its own ground.
        """
        if not self.anchor_corrections:
            return ReconciledThrough(None)
        return self.anchor_corrections[-1].anchor.reconciled_through


def walk_cash_ledger(account_id: int, scenario_id: int) -> CashLedgerWalk:
    """Replay an account's assertions and settled rows into one running balance.

    Seeds the running balance at zero and, per assertion in business-date
    order, advances the balance by every settled source that assertion
    RECONCILES (:meth:`app.services.cash_ledger.ReconciledThrough.covers`),
    records the correction, then RESETS the balance to the asserted value.

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

    **The absorb loop's precondition is the LOADERS' ordering, and it is stated
    once where the rows are read rather than restated as a re-sort here**
    (finding N-133 / R1).  ``cash_anchor_facts`` returns assertions ascending by
    ``(observed_on, created_at, id)`` -- BUSINESS date first -- and
    ``settled_cash_facts`` returns sources ascending by
    ``(settled_on, transaction_id)``.  The monotonic ``absorbed`` pointer below
    depends on both: a fact list not non-decreasing in its day would make it
    skip sources it should absorb.  Two assertions about ONE day are not a
    conflict -- they apply in recording order, the first absorbs the day's
    sources and the second absorbs nothing, so the LAST is that day's closing
    balance, which is what a user re-reading their bank later the same day
    means.

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
    absorbed = 0
    for anchor in anchors:
        # Absorb every source this assertion RECONCILES -- one implementation
        # of that rule (``ReconciledThrough.covers``) rather than a placement
        # convention.  This loop is deliberately the same shape as the posted
        # ledger's in
        # :func:`app.services.account_posting_service.walk_account_ledger`,
        # which walks the SAME assertions against the SAME rule over the
        # POSTED copy of these events; plan step X-d deletes that copy and
        # this becomes the only walk.
        while absorbed < len(sources) and anchor.reconciled_through.covers(
            sources[absorbed].settled_on,
        ):
            running += sources[absorbed].delta
            absorbed += 1
        corrections.append(
            CashAnchorCorrection(anchor=anchor, balance_before=running)
        )
        # The assertion resets the walked total to the asserted balance --
        # the user's declaration outranks the recorded facts before it.
        running = anchor.anchor_balance
    # Sources dated after the LAST assertion ride on top of it and move no
    # correction, so the walk stops here: ``running`` is not returned, and the
    # fold re-derives the balance from ``dated_deltas`` rather than from it.
    return CashLedgerWalk(sources, corrections)


def dated_deltas(walk: CashLedgerWalk) -> list[tuple[date, Decimal]]:
    """Return the walk's ``(day, delta)`` steps, ascending by date.

    The bridge from the walk (events in the order the running balance applied
    them) to the civil day each event COUNTS FROM -- the cash twin of
    :func:`app.services.loan_ledger.dated_deltas`.  It MERGES the two per-event
    statements of that clock rather than restating either: each fact and each
    correction carries its own day (``settled_on`` / ``observed_on``) and its own
    ``delta``, so a reader that needs the two kinds apart (the fold's R-I seed,
    the period view's assertion component) reads the same pair this list is built
    from.  Each event contributes the amount it moved the running balance by:

    * an assertion: :attr:`CashAnchorCorrection.delta`
      (``anchor_balance - balance_before``) -- the jump its reset booked;
    * a settled source: its signed :attr:`~._events.CashSourceFact.delta` -- the
      cash that moved.

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

    **The walk and this re-key are now on ONE granularity, and that is ruling
    R-DH.**  They were not: the walk partitioned on the INSTANT while this list
    keyed the resulting deltas by civil DAY, so a day's total depended on
    sub-day click ordering even though the fold above reads only day boundaries.
    This function's own prior text stated the split as a design -- "which settles
    an assertion already covers turns on order within a day" -- and that sentence
    is what cost production ``$4,001.42`` on 2026-07-31 (see
    :class:`app.services.cash_ledger.ReconciledThrough`).  Both halves key on
    the day now, so the collapse below is not merely safe, it is the same rule
    the walk applied.

    **Dated FACTS, not a balance-at-T** (the W9909 ruling): each pair says what
    ONE event contributed and when it counts, both readable off the public source
    facts and corrections.  Turning them into a balance is the prefix-sum, which
    is seam-private.

    Args:
        walk: The account's :class:`CashLedgerWalk` (:func:`walk_cash_ledger`).

    Returns:
        ``[(day, delta), ...]`` ascending by ``(day, tag)``, where a SOURCE tags
        before an ASSERTION on a shared date -- mirroring the walk's own
        tie-break, so reading the list shows the same chronology the walk
        applied.  Empty for a walk with no facts.
    """
    # Tag 0 = source, 1 = assertion: the same tie-break the walk applies, so a
    # source sharing an assertion's day reads before it here too.  It holds for
    # BOTH anchor kinds because the walk has one placement for both -- while the
    # OPENING was excepted there and not here (finding N-133 / F5) these two
    # orders were opposite for an opening, and the Returns docstring below
    # asserted a chronology this list did not have.
    tagged: list[tuple[date, int, Decimal]] = [
        (fact.settled_on, 0, fact.delta) for fact in walk.source_facts
    ] + [
        (correction.observed_on, 1, correction.delta)
        for correction in walk.anchor_corrections
    ]
    tagged.sort(key=lambda step: (step[0], step[1]))
    return [(day, delta) for day, _tag, delta in tagged]
