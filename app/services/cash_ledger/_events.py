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

**ONE CIVIL DAY per fact, and it is the USER'S day** (ruling R-DH,
``docs/audits/balance_architecture/anchor_settle_partition.md``).  A settled row
carries :attr:`CashSourceFact.settled_on` and an assertion carries
:attr:`CashAnchorFact.observed_on`; both are resolved ONCE at construction, and
every consumer -- the partition, the fold's sampling, the period bucketing, the
posting writer's entry dating -- reads that one field.  Two independently-derived
keys for one question is finding N-34's shape (the loan split keyed its rate on
the pay period while its ordering keyed on the due date, and the two disagreed by
$500.00 on a single payment).  Here they cannot: there is one key.

**This module carried INSTANTS until 2026-07-31, and the change is ruling R-DH.**
An assertion is the CLOSING BALANCE for its civil day, so it absorbs every
movement dated that day whatever order the two were recorded in -- EVERY
assertion, with no exception for the opening (finding N-133 / F1; see the order
constants for what the one-day-old exception cost).  The instant partition it
replaces decided that question by CLICK ORDER -- neither
``Transaction.paid_at`` (``db.func.now()`` at the click,
``status_seam.py:105``) nor ``AccountAnchorHistory.created_at`` measures when
money moved -- and on production 2026-07-31 an ordinary bookkeeping session
(read the bank, enter the anchor, tick off what cleared) subtracted ``$4,001.42``
of already-cleared payments a second time, rendering the grid's projected end
balance at ``-$4,021.37`` against a true ``-$19.95``.  Measured over four months
of real data, the day partition cuts the correction the model must plug at its
53 true-ups from ``$40,554.34`` gross / ``-$6,998.90`` net to ``$15,367.94`` /
``-$940.06``, and it is the only rule under which the walk lands on the balance
the bank shows.

**The day is ``America/New_York``, not UTC, and that is ruling R-DH (b).**
``pay_periods.start_date`` / ``end_date`` and ``transactions.due_date`` are plain
``DATE`` columns meaning the USER's civil days, so deriving an event's day in UTC
and comparing it against them compares two different calendars.  Measured: 22 of
139 real settled rows land on a different day under UTC, 5 of them in a different
pay period (a ``$1,910.95`` mortgage payment twice), and two Eastern evenings had
ONE bookkeeping session split across two UTC days -- the shape that would defeat
the partition above.  Storage is unchanged; every instant is still stored UTC.

**A row with no ``paid_at`` keeps its civil date UNCONVERTED, and that is
load-bearing rather than defensive.**  Its fallback is the pay period's
``start_date`` -- already a civil date, never an instant -- so converting it
would move it to the previous day.  Four of the real Checking account's settled
rows carry the shape, and 3 of the 4 would cross a pay-period boundary.
:func:`settled_civil_day` therefore falls back to the date itself rather than
manufacturing an instant to convert.

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
from app.utils.dates import to_display_civil_date, utc_instant

from ._amounts import settled_cash_leg
from ._facts import _unwindowed_contributing_rows


# Walk order WITHIN one civil day (ruling R-DH (a)).  A day's sources walk
# first and EVERY assertion about that day then resets over them -- opening and
# true-up alike, because an assertion is the CLOSING balance for its civil day
# and a movement dated that day is inside it by definition.  ONE placement, so
# the rule is one statement.
#
# **The OPENING was an EXCEPTION for one day, and the exception was measurably
# wrong** (finding N-133 / F1, both ruled 2026-07-31).  It sorted BEFORE its own
# day's sources on the argument that an opening states what an account holds as
# recording BEGINS rather than what a day closed at.  Scored afterwards against
# four months of production: Checking's opening asserts $2,746.58 on 2026-03-27
# and FOUR settled rows carry that same civil day, netting +$2,057.42 -- every
# one clicked 33 seconds to 1.6 hours AFTER the opening was typed, so the bank
# the user read already showed them.  Riding them on top made the walk read
# $4,804.00 for a day the bank showed $2,746.58, and made the next assertion
# book -$1,986.16 where absorbing them books +$71.26.  It also cost the rule its
# one-statement property: the exception had to be hand-mirrored as a sort key
# here and as a date boundary in the posting walk, and ``dated_deltas``'
# tie-break never moved with it at all.  The case it protected -- open an
# account at $0 and fund it the same day -- is answered by the OPENING'S OWN
# DATE instead (``CashAnchorFact.observed_on``, user-supplied): a real date on
# both sides beats a placement rule guessing at one.
#
# Sources on EARLIER days precede every assertion, which is what ruling R-I
# back-projects into the fold's seed; this ordering does not touch that arm.
_SOURCE_ORDER = 0
_ASSERTION_ORDER = 1


def settled_civil_day(paid_at: datetime | None, period_start: date) -> date:
    """Return the civil day a settled source's cash counts from.

    The ONE statement of "which day did this cash move", shared by every consumer
    that partitions source facts against an assertion: this leaf's walk
    (:func:`app.services.cash_ledger.walk_cash_ledger`), the fold that samples it
    (:mod:`app.services.balance_at._cash_fold`), and the account posting walk
    (:mod:`app.services.account_posting_service`), which reaches the same rule
    from the postings side until plan step X-d retires it onto this one.  A
    second copy of the rule is precisely how the projection and the posted ledger
    would drift about which settles an assertion already covers -- the divergence
    Phase X exists to close, so the rule is not written twice.

    **It is the DISPLAY-timezone day** (ruling R-DH (b)).  This day is compared
    against, and bucketed into, plain ``DATE`` columns that mean the user's civil
    days (``pay_periods.start_date`` / ``end_date``); deriving it in UTC compares
    two different calendars.  Measured on production 2026-07-31: 22 of 139
    settled Checking rows land on a different day under UTC and 5 of those in a
    different pay period, including a ``$1,910.95`` mortgage payment on both
    2026-04-22 and 2026-07-01.

    **A ``None`` ``paid_at`` returns *period_start* UNCONVERTED**, and that is
    the half a naive implementation gets wrong.  The fallback is already a civil
    date -- it was never an instant -- so converting it (or manufacturing
    midnight-UTC to convert) shifts it a day earlier and can move the row into
    the previous pay period.  Measured: 4 of the real Checking account's settled
    rows carry no ``paid_at``, and 3 of the 4 would cross a period boundary.
    Delegating to :func:`app.utils.dates.to_display_civil_date` is what keeps the
    two arms honest, because that helper applies the fallback WITHOUT converting
    it.

    This function replaced ``attribution_instant`` at ruling R-DH; see the module
    docstring for what the instant partition cost on production.

    Args:
        paid_at: The source row's settle instant, or ``None``.  Naive values are
            assumed UTC (the storage convention).
        period_start: The source's pay-period ``start_date`` -- the civil day to
            fall back to when ``paid_at`` is ``None``.

    Returns:
        The civil day the source's cash counts from.
    """
    return to_display_civil_date(paid_at, period_start)


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
            branches on account class (ruling R-J), and neither does the fold
            above it; classifying asset vs liability belongs to the net-worth
            consumers.
        pay_period_id: The history row's pay period (NOT NULL) -- the period a
            correction derived from this assertion is attributed to.
        observed_on: The civil day this balance was TRUE (ruling R-DH) -- the
            business date the whole partition turns on.  A source whose
            :attr:`CashSourceFact.settled_on` is at or before it is already
            INSIDE the asserted balance, because an assertion is the CLOSING
            balance for its day.  **Read from the stored
            ``account_anchor_history.observed_on``, not derived** (plan step 2,
            the opening half): the user supplies it when creating an account,
            and a true-up defaults it to today in the display timezone.  It was
            ``created_at``'s display-timezone day until the column shipped, and
            the column was backfilled from exactly that derivation, so nothing
            moved.  ``LoanAnchorEvent.anchor_date`` has been the loan side's
            version since Commit 16; this is the cash half that never had one
            (finding X5), and its absence is why the partition compared two
            data-entry timestamps and cost production ``$4,001.42``.
        asserted_at: The RECORDING instant, aware-UTC.  It dates nothing and
            partitions nothing; its one job is to order two assertions that
            share an :attr:`observed_on`, so the LAST one recorded is the day's
            closing balance.  Keeping it is not a second clock: one is a
            business date and one is a tie-break over assertions about the same
            business date.  It was the partition key until ruling R-DH, which is
            what cost production ``$4,001.42`` (see the module docstring).
        is_opening: True for the account's first history row; False for a
            true-up.  **A LABEL, not a partition input** (finding N-133 / F1):
            the walk treats both kinds identically -- an assertion closes its
            civil day, whichever kind it is -- and this flag survives only for
            the two places the DISTINCTION is real: the posting source kind an
            assertion books under (``account_opening`` vs ``account_trueup``,
            ``account_posting_service._anchors``) and ruling R-I's back-
            projection, which moves the FIRST assertion's correction into the
            fold's seed.  It was a partition input for one day and cost
            ``$2,057.42`` of period 0's remainder while it was.
    """

    account_id: int
    anchor_balance: Decimal
    pay_period_id: int
    observed_on: date
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
    X-c1).  :attr:`settled_on` is the CASH clock -- the day the money moved,
    which is what a balance is folded on -- while :attr:`pay_period_id` is the
    BUDGET clock, the column the row was budgeted in.  They are the same period for 111
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
            to date the event; the cash clock is :attr:`settled_on` alone.
        is_income: Whether the source row is an INCOME transaction (its
            ``transaction_type_id``), so a budget-clock reduction can split the
            income and expense legs by type rather than by the sign of
            :attr:`delta`.
        settled_on: The civil day this row's cash MOVED
            (:func:`settled_civil_day`) -- the one date the assertion partition
            compares against, the fold samples on, and the period index buckets
            by.  Derived here from ``paid_at``'s display-timezone day, falling
            back to the pay period's ``start_date`` unconverted; plan step 2 of
            ``anchor_settle_partition.md`` replaces the derivation with a stored
            ``transactions.settled_on`` the user supplies, at which point the
            partition compares two real-world dates and stops guessing.
        delta: The signed confirmed cash effect
            (:func:`app.services.cash_ledger.settled_cash_leg`): positive for
            income, negative for an expense, and ``0.00`` for a row whose entries
            are entirely credit-card purchases.

    **There is no instant on this record, and its absence is the ruling** (R-DH).
    It carried ``occurred_at`` -- ``paid_at`` normalized to UTC -- until
    2026-07-31, and every consumer that wanted a DAY re-derived one from it.  The
    instant was never a fact about the money: ``paid_at`` is stamped
    ``db.func.now()`` when the user clicks (``status_seam.py:105``) and the API
    refuses any other value (``schemas/validation/transactions.py:62`` is
    ``dump_only``), so its sub-day precision described bookkeeping keystrokes and
    the partition that consumed it decided ``$4,001.42`` of real money by click
    order.  Storing only what is known keeps a consumer from reaching for
    precision the datum does not have.
    """

    transaction_id: int
    pay_period_id: int
    is_income: bool
    settled_on: date
    delta: Decimal


def cash_anchor_facts(account_id: int) -> list[CashAnchorFact]:
    """Return an account's balance assertions as facts, in assertion order.

    Loads every :class:`~app.models.account.AccountAnchorHistory` row for the
    account ordered by ``(observed_on, created_at, id)`` -- BUSINESS date first,
    the recording instant breaking a same-day tie so the last one recorded is
    that day's closing balance, and ``id`` breaking a same-instant tie
    deterministically -- and marks the first as the OPENING.

    **The order is BUSINESS-DATE, and the flag is set on THIS list, because
    every consumer of both reads business-date order.**  The rows were loaded
    ``(created_at, id)`` while ``observed_on`` was DERIVED from ``created_at``
    and therefore monotone in it; plan step 2 made the column user-supplied and
    broke that, so the loader now states the order the partition actually uses.
    Getting this wrong is not cosmetic: the flag chooses which correction books
    ``account_opening`` versus ``account_trueup``
    (:func:`app.services.account_posting_service._anchors._account_correction_kinds`)
    while ruling R-I's seed takes ``anchor_corrections[0]`` and the period
    view's assertion component takes ``[1:]``
    (:mod:`app.services.balance_at._cash_fold`) -- three consumers, one of them
    keyed on the flag and two on the position. Ordering here is what keeps
    "the FIRST is the opening" a single true statement rather than three that
    happen to agree.  Measured: a fixture that pinned a true-up's instant to an
    exact second while the origination carried the same second plus microseconds
    inverted the two and posted a ``$1,307.66`` true-up to the ledger as the
    account's OPENING.

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
        .order_by(
            AccountAnchorHistory.observed_on,
            AccountAnchorHistory.created_at,
            AccountAnchorHistory.id,
        )
        .all()
    )
    return [
        CashAnchorFact(
            account_id=account_id,
            anchor_balance=Decimal(str(row.anchor_balance)),
            pay_period_id=row.pay_period_id,
            # The business date the partition turns on, READ rather than
            # derived (ruling R-DH, plan step 2), beside the recording instant
            # that only breaks a same-day tie.  ``observed_on`` was
            # ``to_display_date(created_at)`` until the column existed, and the
            # backfill is that derivation verbatim -- so the switch moved no
            # figure and every row keeps the day the engine already gave it.
            observed_on=row.observed_on,
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
    rather than copied per half.  One gate for both halves is what makes the
    SETTLED and PLANNED tiers a partition of the contributing set rather than
    two filters that could disagree about which rows exist at all.

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
        ``(settled_on, transaction_id)`` -- the order the walk consumes them in,
        with the id breaking a same-day tie deterministically.  Order WITHIN a
        day is not observable: the walk only sums a day's sources before its
        assertions close it (ruling R-DH), and the fold reads a day's boundary
        after every step on it, so only the day's total can be read back.  The
        sort is total anyway, because a nondeterministic order in a financial
        replay is a reproducibility defect even where it is arithmetically
        inert.
    """
    rows = _unwindowed_contributing_rows(
        account_id, scenario_id, Transaction.status_id.in_(settled_status_ids()),
    )
    facts = [
        CashSourceFact(
            transaction_id=txn.id,
            pay_period_id=txn.pay_period_id,
            is_income=txn.is_income,
            settled_on=settled_civil_day(
                txn.paid_at, txn.pay_period.start_date,
            ),
            delta=settled_cash_leg(txn),
        )
        for txn in rows
    ]
    facts.sort(key=lambda fact: (fact.settled_on, fact.transaction_id))
    return facts


def merge_anchor_and_cash_events(
    anchor_facts: list[CashAnchorFact],
    source_facts: list[CashSourceFact],
) -> list[tuple[date, bool, CashAnchorFact | CashSourceFact]]:
    """Merge assertions and settled sources into one chronological stream.

    Returns ``(day, is_anchor, item)`` tuples in the order the running-balance
    walk must process them, so each assertion's RESET lands at the right point
    relative to the cash that moved around it.

    **An assertion CLOSES its civil day: every source dated that day is walked
    first, and the day's assertions then reset over them** (ruling R-DH).  A
    balance a user reads off their bank is a statement balance, and a statement
    balance is an end-of-day figure -- so a movement dated that day is inside it
    by definition, whichever of the two the user happened to record first.

    **What this replaced, and what it cost.**  The key was the INSTANT until
    2026-07-31.  Neither instant available is a fact about money:
    ``Transaction.paid_at`` is ``db.func.now()`` at the click and
    ``AccountAnchorHistory`` has no date column at all, only ``created_at``.  So
    the partition asked "which button was pressed first" and answered a question
    about cash with it.  On production that day, an ordinary session -- read the
    bank, enter ``$1,307.66``, tick off what cleared -- recorded three already
    cleared payments in the NINE SECONDS after the assertion and subtracted
    ``$4,001.42`` from the balance a second time, rendering ``-$4,021.37``
    against a true ``-$19.95``.  Across four months of that account, 65 of 139
    settled rows (``$19,602.13`` gross) were classified by click order, and the
    correction the model was forced to plug at each assertion totalled
    ``$40,554.34`` gross / ``-$6,998.90`` net against ``$15,367.94`` /
    ``-$940.06`` under this rule.

    **The rule this arc's own instrument argued for was measured wrong.**  The
    instant partition's stated evidence was one 2026-07-25 pair -- an anchor at
    12:57:08 UTC and two expenses at 13:07 the same day, ``$108.15`` that a
    date-keyed rule absorbs (finding cash D1).  That case is real; it is also
    the exception.  Scored over the whole account rather than one pair, the
    day rule books a SMALLER plug at that very assertion (``$39.27`` against
    ``$68.88``), because the rows it absorbs were overwhelmingly recorded late
    rather than cleared late.

    **The residual is stated rather than hidden.**  A payment that genuinely
    clears AFTER the balance was observed on the same day is absorbed, and the
    projection reads high until the next assertion.  It is bounded (median
    ``$184.55`` a day against the ``$4,161.47`` the instant rule produced on
    2026-07-31) and it self-corrects at the next assertion.

    **What removes the residual is an OBSERVATION, not a second derived date,
    and this docstring claimed otherwise until 2026-08-01.**  It said step 2 of
    ``anchor_settle_partition.md`` "removes the guess entirely by recording the
    two real dates the question actually turns on".  Section 10.3 of that
    document disproves it: with both dates recorded, a movement made after the
    balance was read still carries the same civil day as one made before it, so
    no rule comparing the two dates can tell them apart.  The guess ends only
    where the user says what their statement showed -- which is what plan step
    S1-c built for PURCHASES (``entry_service.record_settled_days``) and what a
    bank import would do for settles.  Until then this rule is the best
    available guess on the settle side, and saying so is the honest form of it.

    Args:
        anchor_facts: The account's :class:`CashAnchorFact` list, PRE-SORTED by
            ``(observed_on, created_at, id)`` (:func:`cash_anchor_facts`) and
            consumed in that order rather than re-sorted -- one ordering, stated
            where the rows are read.  ``sort`` below is STABLE, so assertions
            sharing a day keep it.  Two assertions about one day are not a
            conflict: they apply in recording order and the LAST is that day's
            closing balance, which is what a user re-reading their bank later
            the same day means.
        source_facts: The settled :class:`CashSourceFact` list, PRE-SORTED by
            ``(settled_on, transaction_id)`` (:func:`settled_cash_facts`).

    Returns:
        ``(day, is_anchor, item)`` tuples in walk order -- ``day`` is the
        anchor's ``observed_on`` or the source's ``settled_on``, and ``item`` is
        a :class:`CashAnchorFact` when ``is_anchor``, else a
        :class:`CashSourceFact`.
    """
    # Sources fall inside the day; every assertion about it closes it (see the
    # order constants).  A stable sort of [sources..., anchors...] preserves
    # each type's pre-sorted order for equal keys.
    events: list[tuple[date, int, CashAnchorFact | CashSourceFact]] = [
        (fact.settled_on, _SOURCE_ORDER, fact) for fact in source_facts
    ] + [
        (anchor.observed_on, _ASSERTION_ORDER, anchor)
        for anchor in anchor_facts
    ]
    events.sort(key=lambda event: (event[0], event[1]))
    return [
        (day, tag == _ASSERTION_ORDER, item) for day, tag, item in events
    ]
