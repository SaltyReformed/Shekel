"""The cash fold's EVENT STREAM: what happened to an account, when it happened.

A cash account's balance is a fold over its event stream, and this module builds
that stream.  Two kinds of fact enter it, and nothing else:

* an **ASSERTION** -- the user declaring "my real balance is now $X", one
  :class:`~app.models.account.AccountAnchorHistory` row, RESETTING the running
  balance at its assertion instant.  The first row is the account's OPENING (the
  origination row ``account_service.create_account`` appends); every later row is
  a TRUE-UP.
* an **ACTUAL** -- a SETTLED balance-contributing transaction row: the record
  that cash really moved.  Transfer effects arrive here automatically, because a
  transfer's legs ARE ``Transaction`` rows (``transfer_id IS NOT NULL``) --
  Transfer Invariant 5, the same reason the projection engine never queries
  ``Transfer`` directly.

**PLANNED (still-Projected) rows are deliberately NOT here** (ruling R-G).  A
plan cannot have already happened, so a projected row's effective date is
``max(its attribution date, as_of + 1 day)`` -- it depends on the READER's
as-of, and this leaf reads no clock.  The projected tier therefore lives in the
seam's fold, exactly as the loan plan's PLANNED tier lives in ``balance_at._plan``
rather than in ``loan_ledger`` (plan step C6a's ruling, restated for cash).

**Every fact enters the stream, whatever its date, and nothing here reads the
clock.**  Deciding which facts have HAPPENED as of a date is a READER's job.  The
loan half learned this the expensive way: a walk that took an ``as_of`` made the
persisted ledger a function of the wall clock at the moment the sync happened to
run, which is a corruption generator rather than a cache (plan step A3,
``4e46a0a8``).

**One instant per fact; its civil day falls out of it.**  Attribution is resolved
ONCE, into an instant (:func:`attribution_instant`), and the civil date an event
counts FROM is that instant's UTC day.  Two independently-derived keys for one
question is finding N-34's shape -- the loan split keyed its rate on the pay
period while its ordering keyed on the due date, and the two disagreed by $500.00
on a single payment.  Here they cannot: there is one key.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes.  All money is
:class:`~decimal.Decimal`.

Plan of record: ``docs/audits/balance_architecture/README.md`` (step X-a).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import utc_civil_date, utc_day_start_instant, utc_instant

from ._amounts import settled_cash_leg
from ._facts import _unwindowed_contributing_rows


def attribution_instant(
    paid_at: datetime | None, period_start: date,
) -> datetime:
    """Return the instant a settled source is attributed to.

    The ONE statement of "when did this cash actually move", shared by every
    consumer that partitions source facts against an anchor's assertion instant:
    this leaf's walk (:func:`app.services.cash_ledger.walk_cash_ledger`) and the
    account posting walk (:mod:`app.services.account_posting_service`), which
    reaches the same rule from the postings side until plan step X-d retires it
    onto this one.  A second copy of the rule is precisely how the projection and
    the posted ledger would drift about which settles an anchor already covers --
    the divergence Phase X exists to close, so the rule is not written twice.

    The instant is ``paid_at`` normalized to UTC, falling back to midnight UTC of
    the source's pay-period ``start_date`` when ``paid_at`` was never recorded (a
    historical settle predating the ``paid_at`` sync, or a row whose timestamp a
    revert cleared).  That is the instant analogue of the
    ``COALESCE(paid_at, start_date)`` rule
    :func:`app.utils.dates.to_utc_civil_date` applies to civil dates, so a
    NULL-``paid_at`` row lands on the SAME civil day in the instant partition and
    in the date sampling instead of on two different ones.  Measured on
    production 2026-07-25: 8 of the 146 settled balance-contributing rows across
    ALL accounts carry no ``paid_at`` (4 of them on the 130-row Checking account),
    so the fallback is load-bearing, not defensive.

    Args:
        paid_at: The source row's settle instant, or ``None``.  Naive values are
            assumed UTC (the storage convention).
        period_start: The source's pay-period ``start_date`` -- the fallback
            civil day when ``paid_at`` is ``None``.

    Returns:
        The aware-UTC attribution instant.
    """
    if paid_at is None:
        return utc_day_start_instant(period_start)
    return utc_instant(paid_at)


@dataclass(frozen=True)
class CashAnchorFact:
    """One assertion of an account's real balance, as a plain fact.

    Wraps one :class:`~app.models.account.AccountAnchorHistory` row for the walk
    to replay.  Rows are ordered by ``(created_at, id)`` -- the same
    latest-``created_at`` chronology :func:`app.services.cash_ledger.resolve_anchor`
    reads, with ``id`` as the deterministic tie-breaker -- and the FIRST is the
    account's OPENING.

    Attributes:
        account_id: The ``budget.accounts`` id the assertion belongs to.
        anchor_balance: The asserted balance, LEDGER-NATIVE sign: an
            owed-as-negative liability anchor stays negative.  The walk never
            branches on account class, exactly as
            :func:`app.services.balance_at._calculator.calculate_balances` never
            does; classifying asset vs liability belongs to the net-worth
            consumers.
        pay_period_id: The history row's pay period (NOT NULL) -- the period a
            correction derived from this assertion is attributed to.
        asserted_at: The assertion instant, aware-UTC.  A source attributed at or
            before this instant is already INSIDE the asserted balance.
        is_opening: True for the account's first history row; False for a
            true-up.
    """

    account_id: int
    anchor_balance: Decimal
    pay_period_id: int
    asserted_at: datetime
    is_opening: bool


@dataclass(frozen=True)
class CashSourceFact:
    """One settled transaction's signed effect on the account, when, and whose column.

    The ACTUAL half of the event stream: cash that really moved.  Its delta is
    the SHARED :func:`app.services.cash_ledger.settled_cash_leg` -- the same
    ``effective_amount - Sigma(credit entries)`` the posting writer books -- so
    for an ORDINARY transaction the walk and the posted ledger value one row
    identically by construction, not by two rules that happen to agree.

    **It carries TWO clocks, and the second one is not decoration** (plan step
    X-c1).  :attr:`occurred_at` is the CASH clock -- when the money moved, which
    is what a balance is folded on -- while :attr:`pay_period_id` is the BUDGET
    clock, the column the row was budgeted in.  They are the same period for 111
    of the real Checking account's 130 settled rows and different for 19
    (measured on the prod-shape clone 2026-07-25), and that difference IS the
    grid's Reconciliation row: a row settled outside its own pay period moves the
    balance in one column while its income / expense subtotal sits in another.
    Carrying both here is what lets ONE valued row set be grouped on either
    clock, rather than a second load answering the second question (ruling R-K).
    :attr:`is_income` is the leg the budget clock sorts the row into, and it is
    the row's TYPE rather than the sign of :attr:`delta` because the two can
    disagree: a settled expense whose ``actual_amount`` was corrected below its
    credit-card entries has a POSITIVE cash leg and is still an expense (one that
    came back), while :func:`app.services.cash_ledger.settled_cash_leg` derives
    that sign FROM the type in the first place -- reading it back off the sign is
    inverting a lossy function.

    **The scope of that "by construction" is ordinary transactions, and stating
    the exception is part of the claim.**  A TRANSFER shadow is posted by
    ``posting_service.sync_transfer_postings``, whose magnitude is
    ``_settle_effective`` -- a ``COALESCE(actual_amount, estimated_amount)`` read
    off the transfer's INCOME shadow, with no credit term and no call to this
    rule (``sync_transaction_postings`` returns ``[]`` for any row carrying a
    ``transfer_id``).  The two agree today only because Transfer Invariant 3
    mirrors ``actual_amount`` onto both shadows and ``entry_service`` refuses
    entries on a shadow at all, so the credit term is always zero -- which is
    exactly the "two rules that happen to agree" shape this module claims to have
    ended, surviving on the rows that carry the largest cash movements.  Plan step
    X-d must either unify the transfer path onto this rule or except it
    explicitly; it is not left implicit.

    **Why it is not simply ``effective_amount``, measured.**  An envelope's
    CREDIT-card entries never leave checking: each is settled by its own CC
    Payback sibling, so counting them here would debit the money twice.  On
    production data 2026-07-25, valuing settled rows at ``effective_amount``
    diverged from the posted ledger on 10 of the real Checking account's 130
    settled rows -- by ``$181.58`` on one grocery envelope, and by the row's WHOLE
    amount on three rows whose entries are all credit (their true checking effect
    is ``$0.00`` and the ledger correctly posts nothing at all).

    The projection's own read-time adjustments cannot reach a settled row and are
    deliberately not applied: the entries-aware RESERVATION models money still to
    leave (this has left), and the live override map is built from
    ``live_projected_net`` / ``live_loan_transfer_amounts``, both of which filter
    to ``is_projected`` candidates.

    Attributes:
        transaction_id: The source row's id (identity for the walk's output and
            for the posting writer's attribution at plan step X-d).
        pay_period_id: The BUDGET clock -- the ``budget.pay_periods`` row the
            transaction is attributed to (NOT NULL on the column).  Never used
            to date the event; the cash clock is :attr:`occurred_at` alone.
        is_income: Whether the source row is an INCOME transaction (its
            ``transaction_type_id``), so a budget-clock reduction can split the
            income and expense legs by type rather than by the sign of
            :attr:`delta`.
        occurred_at: The attribution instant (:func:`attribution_instant`) --
            what the anchor partition compares against.
        delta: The signed confirmed cash effect
            (:func:`app.services.cash_ledger.settled_cash_leg`): positive for
            income, negative for an expense, and ``0.00`` for a row whose entries
            are entirely credit-card purchases.
    """

    transaction_id: int
    pay_period_id: int
    is_income: bool
    occurred_at: datetime
    delta: Decimal

    @property
    def visible_on(self) -> date:
        """Return the civil day this fact COUNTS from -- its instant's UTC day.

        The date key the fold samples on, derived from the one attribution
        instant rather than resolved a second time, so the instant partition and
        the date sampling cannot disagree about which day a settle landed on (see
        the module docstring's "one instant per fact").

        Returns:
            The UTC calendar date of :attr:`occurred_at`.
        """
        return utc_civil_date(self.occurred_at)


def cash_anchor_facts(account_id: int) -> list[CashAnchorFact]:
    """Return an account's balance assertions as facts, in assertion order.

    Loads every :class:`~app.models.account.AccountAnchorHistory` row for the
    account ordered by ``(created_at, id)`` -- so the LAST is the row
    :func:`app.services.cash_ledger.resolve_anchor` resolves, and ``id`` breaks a
    (practically unreachable) same-instant tie deterministically -- and marks the
    first as the OPENING.

    **Every assertion, not just the latest.**  Reading only the newest row is
    what makes today's projection fabricate the past: measured on production
    2026-07-25, Checking carries 52 assertions over 119 days and the shipping
    scalar answers ALL 8 pre-anchor periods with today's balance while the
    period map omits them entirely (finding B-18 / cash D3).  A fold over every
    assertion has no such state to invent.

    Args:
        account_id: The account whose assertion history to load.

    Returns:
        The account's :class:`CashAnchorFact` list, chronological.  Empty only
        for an account with no history rows -- unreachable in production
        (migration ``cfb15e782f86`` plus the ``account_service.create_account``
        factory guarantee one), and the state
        :func:`app.services.cash_ledger.resolve_anchor` raises on.
    """
    rows = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.created_at, AccountAnchorHistory.id)
        .all()
    )
    return [
        CashAnchorFact(
            account_id=account_id,
            anchor_balance=Decimal(str(row.anchor_balance)),
            pay_period_id=row.pay_period_id,
            asserted_at=utc_instant(row.created_at),
            is_opening=(index == 0),
        )
        for index, row in enumerate(rows)
    ]


def settled_cash_facts(
    account_id: int, scenario_id: int,
) -> list[CashSourceFact]:
    """Return an account's SETTLED transaction rows as dated facts.

    The ACTUAL events the walk folds: every balance-contributing row for the
    account in the scenario whose status is settled, valued and dated once --
    and carrying the budget column it was attributed to, so the ONE valued row
    set can be grouped on either clock (see :class:`CashSourceFact`).  Both
    extra fields are free: the shared loader already joins ``pay_period``, and
    the transaction TYPE is a column on the row it already holds.

    **It loads its own rows and takes no period window, deliberately.**  An
    argument a caller can get wrong is a defect, not a contract (plan Section 8):
    the loan fold once TOOK the period list its visibility rule needed, and the
    grid passing a WINDOW moved a balance by $150,000.00 (plan step B1).  A fold
    over a windowed event stream is a fold over a different account.

    The rows come from the shared ``_facts._unwindowed_contributing_rows`` -- the
    ONE load this and its PLAN twin
    (:func:`app.services.cash_ledger.planned_cash_rows`) narrow, so the account /
    scenario scope, the shared
    :func:`~app.utils.balance_predicates.balance_contributing_clause` eligibility
    gate (``is_deleted = FALSE AND status_id NOT IN (Credit, Cancelled)``) and
    both eager loads are stated once for the two halves of the event stream
    rather than copied per half.  That gate is the same one
    :func:`app.services.cash_ledger.load_balance_transactions` applies, so the
    fold and the projection cannot disagree about which rows exist at all.

    This half supplies the SETTLED narrowing, in SQL rather than as a Python
    post-filter, and the difference is real work: the contributing gate alone
    admits every PROJECTED row too -- roughly two years of forward projection --
    so filtering afterwards would eager-load entries for the whole horizon to
    keep ~130 rows.  :func:`~app.utils.balance_predicates.settled_status_ids` is
    the same status set ``txn.status.is_settled`` tests, in its SQL form.

    The shared loader's eager ``entries`` are load-bearing here rather than an
    optimization: :func:`app.services.cash_ledger.settled_cash_leg` subtracts the
    row's credit-card entries, so an unloaded relationship would issue one SELECT
    per settled row (130 on the real Checking account) -- and the same
    eager-loading discipline is what closed CRIT-01 / F-009 on the projection
    side, where a caller's forgotten ``selectinload`` silently changed a balance.

    Args:
        account_id: The account whose settled rows to load.
        scenario_id: The budget scenario the rows live in.

    Returns:
        One :class:`CashSourceFact` per settled row, ASCENDING by
        ``(occurred_at, transaction_id)`` -- the order the walk consumes them in,
        with the id breaking a same-instant tie deterministically.
    """
    rows = _unwindowed_contributing_rows(
        account_id, scenario_id, Transaction.status_id.in_(settled_status_ids()),
    )
    facts = [
        CashSourceFact(
            transaction_id=txn.id,
            pay_period_id=txn.pay_period_id,
            is_income=txn.is_income,
            occurred_at=attribution_instant(
                txn.paid_at, txn.pay_period.start_date,
            ),
            delta=settled_cash_leg(txn),
        )
        for txn in rows
    ]
    facts.sort(key=lambda fact: (fact.occurred_at, fact.transaction_id))
    return facts


def merge_anchor_and_cash_events(
    anchor_facts: list[CashAnchorFact],
    source_facts: list[CashSourceFact],
) -> list[tuple[datetime, bool, CashAnchorFact | CashSourceFact]]:
    """Merge assertions and settled sources into one chronological stream.

    Returns ``(instant, is_anchor, item)`` tuples in the order the running-balance
    walk must process them, so each assertion's RESET lands at the right point
    relative to the cash that moved around it.

    **The ordering key is the INSTANT, not the civil date, and that is the whole
    point of the step.**  Measured on production 2026-07-25: the Checking anchor
    was asserted at 12:57:08 UTC and two expenses settled at 13:07:11 and
    13:07:18 -- the SAME UTC civil day, ten minutes later.  Keyed by date they
    are absorbed into the assertion and their confirmed cash effect ($108.15 --
    the second row's purchases are entirely credit-card, so it correctly moves
    nothing) disappears from the projection; keyed by instant they ride on top of
    it, which is what actually happened.  That is finding cash D1, live.

    A source at EXACTLY the assertion instant sorts BEFORE the anchor and is
    therefore subsumed by its reset -- the ``<=`` boundary
    :func:`app.services.account_posting_service.walk_account_ledger` already
    applies (``sources[i][0] <= fact.asserted_at``), reproduced here so the read
    fold and the posted ledger partition one boundary identically rather than two
    that happen to agree.

    Args:
        anchor_facts: The account's :class:`CashAnchorFact` list, PRE-SORTED by
            ``(created_at, id)`` (:func:`cash_anchor_facts`).  The re-sort here
            is by ``asserted_at`` alone and is STABLE, so two assertions sharing
            an instant keep the loader's ``id`` order -- the fact carries no
            ``id``, so that stability is the only thing making a same-instant
            pair deterministic, and it is load-bearing rather than incidental.
        source_facts: The settled :class:`CashSourceFact` list, PRE-SORTED by
            ``(occurred_at, transaction_id)`` (:func:`settled_cash_facts`).

    Returns:
        ``(instant, is_anchor, item)`` tuples in walk order -- ``instant`` is the
        anchor's ``asserted_at`` or the source's ``occurred_at``, and ``item`` is
        a :class:`CashAnchorFact` when ``is_anchor``, else a
        :class:`CashSourceFact`.
    """
    # Tag 0 = source, 1 = anchor: a source sorts before an anchor sharing its
    # instant, so it is walked (and then overwritten) by that reset -- the
    # posting walk's ``<=`` boundary, expressed as a sort key.  A stable sort of
    # [sources..., anchors...] preserves each type's pre-sorted order for equal
    # keys.
    events: list[tuple[datetime, int, CashAnchorFact | CashSourceFact]] = [
        (fact.occurred_at, 0, fact) for fact in source_facts
    ] + [
        (anchor.asserted_at, 1, anchor)
        for anchor in sorted(anchor_facts, key=lambda a: a.asserted_at)
    ]
    events.sort(key=lambda event: (event[0], event[1]))
    return [(instant, tag == 1, item) for instant, tag, item in events]
