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
``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  A settled row
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
assertion, with no exception for the opening (finding N-133 / F1, whose
one-day-old exception is recorded in ``anchor_settle_partition.md`` at R-DH (a)
along with the ``$2,057.42`` it cost).  The instant partition it
replaces decided that question by CLICK ORDER -- neither
``Transaction.paid_at`` (``db.func.now()`` at the click, deleted at plan step
X-f1) nor ``AccountAnchorHistory.created_at`` measures when
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

**Nothing here DERIVES a day any more, and that is plan step X-f1** (ruling
R-EC).  A settled row stores the civil day its money moved in
``transactions.settled_on``, so this module reads a fact where it used to
convert ``paid_at``'s instant into the display timezone and fall back to the pay
period's ``start_date`` when the instant was NULL.  That fallback was a guess --
8 live settled rows relied on it -- and it is gone with the derivation: a settled
row with no recorded day is REFUSED by
:func:`app.utils.balance_predicates.settled_day` rather than dated by this
module's opinion.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes.  All money is
:class:`~decimal.Decimal`.

Plan of record: ``docs/audits/balance_architecture/README.md`` (step X-a).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.account_opening import AccountOpening
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.utils.balance_predicates import (
    balance_contributing_clause,
    settled_day,
    settled_status_ids,
)
from app.utils.dates import utc_instant

from ._amounts import ReconciledThrough
from ._cash_leg import settled_cash_leg
from ._clearing import StatementCoverage, statement_coverage
from ._facts import _unwindowed_contributing_rows


@dataclass(frozen=True)
class CashOpeningFact:
    """What an account held BEFORE its records begin, as a loaded fact.

    The governing :class:`~app.models.account_opening.AccountOpening` row for
    one account (plan step **X-f3c-2a**, ruling **R-GX**), read once per walk
    and carried on :class:`~._walk.CashLedgerWalk` beside the assertions and the
    movements.  It is the LEVEL the fold's running total starts from: every
    balance the app renders is this figure plus what the records say happened
    since.

    **It replaces a derivation, and the model docstring lists the four defects
    that derivation caused.**  Until this step the same quantity was recomputed
    on every read as "the earliest assertion minus the movements dated at or
    before it" (ruling **R-I**), which made it move when an assertion was
    back-dated, differ between scenarios, impossible to correct, and derived a
    second time by the posted ledger.

    Attributes:
        opening_id: The ``budget.account_openings`` row's own id -- the
            restatement this fact is, so a reader can tell two apart.
        account_id: The account whose books these are.
        opened_on: The civil day the books opened.  The ``account_opening``
            journal entry is dated on it, which is what stops a back-dated
            assertion re-dating that entry.
        opening_equity: The capital the books opened with, LEDGER-NATIVE and
            in the same sign convention as
            :attr:`CashAnchorFact.anchor_balance`.
        source_id: ``ref.account_opening_sources.id`` -- whether a human stated
            this figure or the X-f3c-2a migration derived it.  Carried because
            a derived figure is the old inference frozen and may be WRONG
            (finding **N-275** measures one wrong by ``$436.05``), so a surface
            must be able to tell a guess from an observation.  The walk itself
            never branches on it: an opening is an opening whatever wrote it.
        recorded_at: The RECORDING instant, aware-UTC.  It is what ORDERS the
            restatements (see :func:`account_opening_fact`) and it dates
            nothing -- :attr:`opened_on` is the business date, the same
            two-clock split :attr:`CashAnchorFact.asserted_at` documents one
            table over.  Carried since plan step **X-f3c-2b** so the history
            card can caption a RESTATED opening the way it captions a
            back-dated assertion: books opening in March, recorded in August,
            is a fact the owner should be able to see rather than infer.
    """

    opening_id: int
    account_id: int
    opened_on: date
    opening_equity: Decimal
    source_id: int
    recorded_at: datetime


@dataclass(frozen=True)
class CashAnchorFact:
    """One assertion of an account's real balance, as a plain fact.

    Wraps one :class:`~app.models.account.AccountAnchorHistory` row for the walk
    to replay.  Rows are ordered by ``(observed_on, created_at, id)`` --
    BUSINESS date first, with the recording instant and then ``id`` breaking a
    same-day tie -- the same key :func:`app.services.cash_ledger.resolve_anchor`
    takes descending, and the FIRST is the account's OPENING.

    **That order is a CONTRACT two walks depend on, not a convenience.**  Both
    replays advance a monotonic pointer through day-sorted sources
    (:func:`app.services.cash_ledger.walk_cash_ledger` and
    :func:`app.services.account_posting_service.walk_account_ledger`), so a
    fact list not non-decreasing in :attr:`observed_on` makes the pointer skip
    sources it should absorb and mis-state a ``ledger_before`` the posting
    walk WRITES to the general ledger.  The read side used to re-sort and so
    self-healed it; since the one-partition step neither side does, because one
    ordering stated where the rows are read is what finding N-133 / R1 ruled.
    The key was ``(created_at, id)`` until plan step 2 made ``observed_on``
    user-supplied and the two orders could differ -- which is how a
    ``$1,307.66`` true-up once posted to the ledger tagged as the OPENING.

    **It carried the row's stored ``pay_period_id`` until plan step X-f1c3b**
    (ruling R-EO), which deleted the COLUMN.  Finding N-169 had already
    measured the field to have ZERO consumers in ``app/``; what the ruling
    added is that the column behind it was a cache of a derivation both
    posting reconciles make from ``observed_on``, was wrong on 2 of 78
    production rows, and carried an ``ON DELETE CASCADE`` that let a
    pay-period reset destroy the user's balance record.  A reader wanting
    "which period does this assertion book in" derives it from the day
    (:meth:`app.services.pay_calendar.PayCalendar.filing_period`).

    Attributes:
        anchor_id: The ``budget.account_anchor_history`` row's own id -- the
            value a cleared line NAMES (``reconciled_by_id``, ruling **R-FL**),
            so :class:`~._clearing.StatementCoverage` can say WHICH assertion
            cleared a movement rather than only that one did.  It is the row's
            identity and nothing more: no rule reads it as an ordering, which is
            :attr:`observed_on`'s job with :attr:`asserted_at` breaking a tie.
        account_id: The ``budget.accounts`` id the assertion belongs to.
        anchor_balance: The asserted balance, LEDGER-NATIVE sign: an
            owed-as-negative liability anchor stays negative.  The walk never
            branches on account class (ruling R-J), and neither does the fold
            above it; classifying asset vs liability belongs to the net-worth
            consumers.
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
        recorded_on: The civil day the assertion was ENTERED, in the user's
            timezone.  **Read from the stored
            ``account_anchor_history.recorded_on``, not derived from
            :attr:`asserted_at`** (finding **N-299**, developer ruling
            2026-08-25).  It partitions nothing and orders nothing; its one
            reader is the balance-history card, which captions a row as
            back-dated when it differs from :attr:`observed_on`.  Deriving it
            put that comparison across two clocks -- this column is the
            APPLICATION's ``display_today()`` and :attr:`asserted_at` is
            PostgreSQL's ``now()`` -- so an ordinary same-day true-up read as
            back-dated wherever the two could disagree.  Nothing else in the
            walk may read it: an assertion's effect on a balance is a function
            of :attr:`observed_on` alone.
        is_opening: True for the account's first history row; False for a
            true-up.  **A LABEL, not a partition input** (finding N-133 / F1):
            the walk treats both kinds identically -- an assertion closes its
            civil day, whichever kind it is.  It was a partition input for one
            day and cost ``$2,057.42`` of period 0's remainder while it was.

            **NOTHING reads it any more, and plan step X-f3c-2a is why.**  Its
            two consumers were the posting source kind an assertion books under
            and ruling R-I's back-projection; the first reads
            ``AccountAnchorCorrection.opens_the_books`` now (a stored fact) and
            the second is deleted.  The balance-history card's "Opening" badge
            asks ``budget.account_openings`` which day the books opened rather
            than which assertion sorts first.  The field is kept because the
            walk's ORDERING contract is still load-bearing and this is the
            cheapest statement of it; it decides no figure.
    """

    anchor_id: int
    account_id: int
    anchor_balance: Decimal
    observed_on: date
    asserted_at: datetime
    recorded_on: date
    is_opening: bool

    @property
    def reconciled_through(self) -> ReconciledThrough:
        """Return the coverage boundary this assertion establishes.

        An assertion is the closing balance for its civil day, so it reconciles
        every movement dated on or before :attr:`observed_on` (ruling R-DH (a)).
        Both walks ask their sources through this -- the read replay in
        :func:`app.services.cash_ledger.walk_cash_ledger` and the posted
        ledger's in
        :func:`app.services.account_posting_service.walk_account_ledger` -- so
        the rule they apply is one implementation rather than two statements
        held in step by convention.

        Returns:
            The :class:`~app.services.cash_ledger.ReconciledThrough` for this
            assertion's own civil day.
        """
        return ReconciledThrough(self.observed_on)


@dataclass(frozen=True)
class CashSourceFact:
    """One cash movement's signed effect on the account, when, and whose column.

    The ACTUAL half of the event stream: cash that really moved.  TWO kinds of
    row produce one, and ruling **R-FM** is why the second exists (plan step
    X-f3b): a SETTLED TRANSACTION, and a PURCHASE whose bank posting day the
    owner has recorded.  They are one record rather than two because the fold,
    the period regrouping and the posted walk ask the same four questions of
    both -- how much, on which day, in whose budget column, and which statement
    showed it -- and a second record would be a second set of consumers that
    could come to answer them differently.

    A transaction's delta is the SHARED
    :func:`app.services.cash_ledger.settled_cash_leg` -- the same
    ``owned_contribution - Sigma(credit entries) - Sigma(posted purchases)`` the
    posting writer books -- so for an ORDINARY transaction the walk and the
    posted ledger value one row identically by construction, not by two rules
    that happen to agree.  A purchase's is its own amount, negated: a purchase
    is always an expense, and its whole amount leaves the account.

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

    **Why it is not simply the row's own figure, measured.**  An envelope's
    CREDIT-card entries never leave checking: each is settled by its own CC
    Payback sibling, so counting them here would debit the money twice.  On
    production data 2026-07-25, valuing settled rows at their own figure
    diverged from the posted ledger on 10 of the real Checking account's 130
    settled rows -- by ``$181.58`` on one grocery envelope, and by the row's WHOLE
    amount on three rows whose entries are all credit (their true checking effect
    is ``$0.00`` and the ledger correctly posts nothing at all).

    The projection's own read-time adjustments cannot reach a settled row and are
    deliberately not applied: the entries-aware RESERVATION models money still to
    leave (this has left), and the live override map is built from
    ``income_service.live_projected_net`` / ``LoanPricing.live_cash``, both of
    which filter to ``is_projected`` candidates.

    Attributes:
        transaction_id: The source row's id -- the transaction itself, or for a
            PURCHASE the envelope it was recorded against (identity for the
            walk's output and for the posting writer's attribution at plan step
            X-d).
        entry_id: The ``budget.transaction_entries`` id when this fact is a
            PURCHASE, else ``None``.  It is what makes the pair
            ``(transaction_id, entry_id)`` the fact's identity: an envelope and
            each of its posted purchases are distinct movements sharing one
            parent, and the sort below breaks their tie with it.  REQUIRED at
            construction rather than defaulted -- a fact built without saying
            which kind it is would sort and attribute as the parent's.
        pay_period_id: The BUDGET clock -- the ``budget.pay_periods`` row the
            transaction is attributed to (NOT NULL on the column).  A purchase
            takes its PARENT's, because a purchase spends the envelope's budget
            and has no column of its own; that is what keeps the budget-clock
            regrouping (``balance_at._cash_periods._budget_legs``) reading a
            partially-spent envelope at its whole cost -- the spent part as
            movements and the rest as the reservation.  Never used to date the
            event; the cash clock is :attr:`settled_on` alone.
        is_income: Whether the source row is an INCOME transaction (its
            ``transaction_type_id``), so a budget-clock reduction can split the
            income and expense legs by type rather than by the sign of
            :attr:`delta`.
        settled_on: The civil day this row's cash MOVED -- the one date the
            assertion partition compares against, the fold samples on, and the
            period index buckets by.  **Read from the stored
            ``transactions.settled_on`` (or, for a purchase, from
            ``transaction_entries.settled_on``), not derived** (plan step X-f1,
            ruling R-EC), through the shared
            :func:`app.utils.balance_predicates.settled_day` so a settled row
            missing one fails loudly here rather than being dated by a fallback.
            It was ``paid_at``'s display-timezone day with the pay period's
            ``start_date`` as a NULL fallback until the column existed, and the
            migration backfilled exactly that derivation -- so the switch moved
            no figure and every row keeps the day the engine already gave it.
            The partition now compares two real-world dates and guesses at
            neither.
        reconciled_by_id: WHICH statement was recorded as showing this row --
            the ``account_anchor_history`` id its ``reconciled_by_id`` names, or
            ``None`` when none has been (ruling **R-FL**).  It sits beside
            :attr:`settled_on` rather than replacing it because the two are
            different facts: one is when the money moved, the other is which
            statement was seen to show it, and a statement legitimately shows a
            line that moved days earlier.  What the walk does with the pair is
            :class:`~._clearing.StatementCoverage`'s rule and not this record's.
        delta: The signed confirmed cash effect
            (:func:`app.services.cash_ledger.settled_cash_leg`): positive for
            income, negative for an expense, and ``0.00`` for a row whose entries
            are entirely credit-card purchases or entirely already posted.  For
            a PURCHASE it is ``-amount``: a purchase is always an expense and
            its whole amount leaves the account.

    **There is no instant on this record, and its absence is the ruling** (R-DH).
    It carried ``occurred_at`` -- ``paid_at`` normalized to UTC -- until
    2026-07-31, and every consumer that wanted a DAY re-derived one from it.  The
    instant was never a fact about the money: ``paid_at`` was stamped
    ``db.func.now()`` when the user clicked and the API refused any other
    value (it was ``dump_only``), so its sub-day precision described
    bookkeeping keystrokes and
    the partition that consumed it decided ``$4,001.42`` of real money by click
    order.  Storing only what is known keeps a consumer from reaching for
    precision the datum does not have.
    """

    transaction_id: int
    entry_id: "int | None"
    pay_period_id: int
    is_income: bool
    settled_on: date
    reconciled_by_id: "int | None"
    delta: Decimal


def account_opening_fact(account_id: int) -> CashOpeningFact:
    """Return *account_id*'s GOVERNING opening-equity record.

    The level a cash fold starts from (plan step **X-f3c-2a**).  The table is
    append-only, so an account may carry several rows -- each a restatement of
    what its books opened with -- and the one with the greatest
    ``(created_at, id)`` governs.

    **The order is the RECORDING instant, and that is what makes it safe.**
    The positional read this step deletes (``is_opening = index == 0``) ordered
    by ``observed_on``, a business date any owner may back-date, so an ordinary
    act silently re-elected the opening.  ``created_at`` is set by the database
    on INSERT and no door lets a user move it, so "the latest restatement" is
    monotone by construction.  ``id`` breaks a same-instant tie, exactly as
    :func:`~._facts._governing_row` breaks one for an assertion, and for the
    same reason: without it the plan decides which of two rows is authoritative.

    **It RAISES on an account with no row, and that is reachable only through a
    broken invariant.**  Every account gets one at creation
    (``account_service.create_account``) and migration ``a7c41f9d2b60``
    backfilled every account that predated the table -- including the two
    amortizing loans, because ``balance_at.balance_at`` falls through to this
    fold for an amortizing account carrying no ``LoanParams``.  Answering a
    missing row with ``Decimal("0.00")`` was the alternative and it is exactly
    the fabrication this step exists to delete: it would silently move every
    balance on the account to a level nothing recorded.  The same fail-loud
    placement :func:`~._facts.resolve_anchor` documents for the assertion half.

    Args:
        account_id: The account whose opening to load.

    Returns:
        The account's governing :class:`CashOpeningFact`.

    Raises:
        RuntimeError: When the account carries no ``AccountOpening`` row --
            a broken invariant, not an empty state.
    """
    row = (
        db.session.query(AccountOpening)
        .filter_by(account_id=account_id)
        .order_by(
            AccountOpening.created_at.desc(),
            AccountOpening.id.desc(),
        )
        .first()
    )
    if row is None:
        raise RuntimeError(
            f"account_opening_fact: account id={account_id} has zero "
            "AccountOpening rows.  Every account carries one -- "
            "account_service.create_account writes it and migration "
            "a7c41f9d2b60 backfilled every account that predated the table -- "
            "so investigate any code path that constructed the Account row "
            "without routing through the canonical factory.  A balance cannot "
            "be folded without the level it starts from, and answering 0.00 "
            "would move every figure on this account silently."
        )
    return CashOpeningFact(
        opening_id=row.id,
        account_id=account_id,
        opened_on=row.opened_on,
        opening_equity=Decimal(str(row.opening_equity)),
        source_id=row.source_id,
        # Normalised the same way ``cash_anchor_facts`` normalises an
        # assertion's instant: PostgreSQL hands back an aware value, and
        # ``utc_instant`` is where a naive one from a fixture is refused
        # rather than compared against an aware one further downstream.
        recorded_at=utc_instant(row.created_at),
    )


def reject_movement_before_books_open(account_id: int, day: date) -> None:
    """Refuse a cash movement dated on or before *account_id*'s opening day.

    **The one statement of the boundary between an account's OPENING and its
    RECORDS** (plan step X-f3c-2b, finding **N-378**).  An account's opening
    equity is what it held at the CLOSE of
    :attr:`CashOpeningFact.opened_on` -- the same rule
    :attr:`CashAnchorFact.observed_on` states for an assertion (ruling
    R-DH (a)) -- so a movement dated on or before that day is ALREADY INSIDE
    the figure, and recording it counts the money twice.

    **What the double count costs, and why the balance healing is not a
    defence.**  The fold seeds at the opening equity and
    :func:`~._walk.dated_deltas` emits every source at its own day, so between
    the movement's day and the next assertion the running total carries it a
    second time.  The next assertion RESETS to what the owner declared, so the
    rendered balance heals -- but the correction that heals it is booked to the
    general ledger, and on a MODELLED account (ruling **R-FO**) its counter leg
    is ``unrealized_change``, not ``anchor_equity``.  A transfer therefore
    becomes market performance that never unwinds.  Measured on a fixture: a
    Roth declared ``$1,000.00`` with a ``$1,000.00`` pre-opening transfer
    reports ``$850.00`` of unrealized change against a real ``$150.00``.

    **It is stated here because this module owns the opening record**, and
    asked by the TWO writers of a settle day -- the ORM one
    (:func:`app.services.settle_day.record_settle_day`, which every door for
    both ``budget.transactions`` and ``budget.transaction_entries`` goes
    through) and the bulk one
    (``reconcile_service.record_settled_days``, a ``query.update()`` with no
    ORM instance to hand that function).  Two callers, one predicate.

    **Its structural backstop is the database, not this function.**  Migration
    ``d3b6f1c8a274`` adds a deferrable constraint trigger over both movement
    tables AND over ``budget.account_openings``, so the state is unstorable
    from any client -- a bulk ``UPDATE``, a raw statement, a restatement that
    moves an opening FORWARD past a movement that already exists.  This
    function exists so an ordinary date box gets a sentence instead of a
    ``psycopg2`` exception at COMMIT: the same pairing
    ``ck_transactions_settle_day_needs_a_record`` has with
    :func:`app.services.status_seam.reject_settle_day_without_a_record`.

    **The CALLER owns the ownership scoping, and the message is why that
    matters.**  The refusal names the account's opening equity so a date box
    can render it verbatim, and this function applies no ``user_id`` filter of
    its own.  Every caller today reaches it behind an ownership check -- the
    routes resolve the row by owner before any settle door is entered -- so
    the figure only ever reaches the owner.  A future caller that took an
    account id straight from a request would turn this message into a balance
    oracle.

    Args:
        account_id: The account the movement belongs to.  Assumed already
            scoped to the acting user by the caller.
        day: The civil day the movement's cash moved.

    Raises:
        ValidationError: When *day* is on or before the account's opening day.
            A 400 rather than a programming error: the day arrives from a date
            box, and the message names both the offending value and the bound
            it broke so a surface can render it verbatim.
        RuntimeError: When the account carries no opening record, propagated
            from :func:`account_opening_fact` -- a broken invariant, and
            deliberately not softened here into "then anything is allowed".
    """
    # **Under ``no_autoflush``, and that is a defect this step's own suite
    # caught rather than a precaution.**  This is the only READ on a write
    # path, and SQLAlchemy autoflushes pending mutations before a query: the
    # caller has already assigned part of the row it is midway through writing
    # -- ``apply_status_change`` sets ``status_id`` before it reaches
    # :func:`app.services.settle_day.record_settle_day` -- so the flush lands a
    # half-written row against constraints that describe the finished one.
    # Measured: a row already carrying a settle day and no settlement record,
    # which is the LEGACY shape ``ck_transactions_settle_day_needs_a_record``
    # exists to let an owner repair, failed with a raw ``CheckViolation``
    # raised "as a result of Query-invoked autoflush".  Suppressing the flush
    # cannot hide a pending opening from this read: every writer of
    # ``budget.account_openings`` flushes -- ``account_service.create_account``
    # before it stages the origination assertion, and the migration through
    # ``op.execute`` -- so there is no unflushed opening for a settle to race.
    with db.session.no_autoflush:
        opening = account_opening_fact(account_id)
    if day > opening.opened_on:
        return
    raise ValidationError(
        f"Money cannot have moved on {day.isoformat()}: this account's books "
        f"open on {opening.opened_on.isoformat()} holding "
        f"${opening.opening_equity}, and that figure is the closing balance "
        "for its own day -- so anything that moved by then is already inside "
        "it.  Restate the account's opening to an earlier day if the records "
        "really do start before it."
    )


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
    Getting this wrong is not cosmetic, though what it costs CHANGED at plan
    step X-f3c-2a.  It used to choose which correction books
    ``account_opening`` versus ``account_trueup`` and which one the fold's seed
    swallowed -- three consumers, one keyed on the flag and two on the
    position, which is why "the FIRST is the opening" had to be a single true
    statement rather than three that happen to agree.  All three read
    ``budget.account_openings`` now.  What still rests on this order is the
    REPLAY: :func:`app.services.balance_at._assertions.assertion_corrections`
    walks the assertions in it, and each correction is measured against the one
    before, so a mis-ordered pair still moves two corrections.

    Measured: a fixture that pinned a true-up's instant to an
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
            anchor_id=row.id,
            account_id=account_id,
            anchor_balance=Decimal(str(row.anchor_balance)),
            # The business date the partition turns on, READ rather than
            # derived (ruling R-DH, plan step 2), beside the recording instant
            # that only breaks a same-day tie.  ``observed_on`` was
            # ``to_display_date(created_at)`` until the column existed, and the
            # backfill is that derivation verbatim -- so the switch moved no
            # figure and every row keeps the day the engine already gave it.
            observed_on=row.observed_on,
            asserted_at=utc_instant(row.created_at),
            # The day it was TYPED, stored rather than converted out of
            # ``created_at`` -- finding N-299.  See the attribute's docstring:
            # the caption that reads it compares against ``observed_on``, and
            # the two must come off one clock.
            recorded_on=row.recorded_on,
            is_opening=(index == 0),
        )
        for index, row in enumerate(rows)
    ]


def coverage_for(account_id: int) -> StatementCoverage:
    """Return *account_id*'s clearing rule, loading its assertions.

    The DATABASE twin of :func:`~._clearing.statement_coverage`, for the callers
    that do not already hold an account's facts -- the entry list's indicator,
    reached from the grid (``entry_service.build_entry_lists_dict``) and from
    the HTMX refresh (``routes/entries.py``).  Neither the entry RESERVATION nor
    the reconcile panel is one of them: ruling **R-FM** dissolved the
    reservation's question at plan step X-f3b, the panel takes the governing
    assertion itself (``cash_ledger.governing_anchor``), and since plan step
    X-f3c-1 the ONE reader of ``walk.coverage`` in ``app/`` is the fold's
    assertion replay (``balance_at._assertions.assertion_corrections``).  It exists for the
    reason :func:`~._facts.reconciled_through` exists beside
    :attr:`~._walk.CashLedgerWalk.reconciled_through` -- a caller holding the
    walk must not pay a query, and a caller rendering one template row must not
    walk an account -- and it is a WRAPPER rather than a second rule, so the two
    cannot come to disagree.

    **It loads ROWS where the boundary it replaces was one ``MAX``**, and that
    cost belongs to the fact rather than to this function: which statement
    cleared a line is a question about a PARTICULAR assertion, so an aggregate
    over the day column cannot answer it.  It is one indexed read per ACCOUNT
    (``idx_anchor_history_account`` leads on ``account_id``) and every caller
    already memoises per account -- ``entry_service.build_entry_lists_dict``
    because a grid render passes ~60 envelopes across ~6 accounts, and the
    reservation because its basis is built once per account per read pass.

    Args:
        account_id: The account whose clearing rule to build.

    Returns:
        Its :class:`~._clearing.StatementCoverage`.  An account with no
        assertion history yields one that clears nothing -- the same honest
        emptiness :func:`~._facts.reconciled_through` answers with a ``None``
        day.
    """
    return statement_coverage(cash_anchor_facts(account_id))


def _posted_purchase_facts(
    account_id: int, scenario_id: int,
) -> list[CashSourceFact]:
    """Return an account's POSTED purchases as dated facts -- ruling **R-FM**.

    The second kind of ACTUAL event (plan step X-f3b): a purchase recorded
    against an envelope whose bank posting day the owner has recorded is cash
    that left the account on that day, whatever its envelope has or has not
    done.  Until this step a purchase was never a cash movement -- it only shrank
    its envelope's reservation, and the money left the book when the WHOLE
    envelope closed, which is finding **N-274**.

    Three narrowings, each load-bearing:

    * ``settled_on IS NOT NULL`` -- the trigger itself (ruling R-FM as refined
      by **R-FR**).  It is the same fact that makes a TRANSACTION an actual
      event, asked of the row in front of it; whether a statement was recorded
      as showing it is :class:`~._clearing.StatementCoverage`'s separate
      question, asked identically of both kinds by the walk.
    * ``is_credit IS FALSE`` -- a card purchase never touches checking; it
      leaves later through its own CC Payback sibling, which is why
      :func:`~._cash_leg.credit_entry_sum` removes it from the parent's leg too.
    * the parent is BALANCE-CONTRIBUTING -- the same
      :func:`~app.utils.balance_predicates.balance_contributing_clause` gate the
      transaction half applies, so a soft-deleted or Credit / Cancelled envelope
      contributes nothing and neither do its purchases.  That is
      :func:`~._amounts.settled_cash_leg`'s totality rule extended to the family
      it now has, and it is what a delete or a cancel reverses in the ledger.

    Deliberately NOT narrowed by the parent's STATUS: a purchase against a
    still-Projected envelope has left the bank exactly as one against a closed
    envelope has.  Measured on a production clone 2026-08-14: 2 of the 9 posted
    purchases (``$45.85``) sit on a Projected row.

    Args:
        account_id: The account whose purchases to load.
        scenario_id: The budget scenario the parent rows live in.

    Returns:
        One :class:`CashSourceFact` per posted debit purchase, unordered (the
        caller sorts the merged set).
    """
    rows = (
        db.session.query(
            TransactionEntry.id,
            TransactionEntry.amount,
            TransactionEntry.settled_on,
            TransactionEntry.reconciled_by_id,
            Transaction.id,
            Transaction.pay_period_id,
        )
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            balance_contributing_clause(),
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .all()
    )
    return [
        CashSourceFact(
            transaction_id=transaction_id,
            entry_id=entry_id,
            pay_period_id=pay_period_id,
            is_income=False,
            settled_on=settled_on,
            reconciled_by_id=reconciled_by_id,
            delta=-Decimal(str(amount)),
        )
        for (
            entry_id, amount, settled_on, reconciled_by_id,
            transaction_id, pay_period_id,
        ) in rows
    ]


def settled_cash_facts(
    account_id: int, scenario_id: int,
) -> list[CashSourceFact]:
    """Return an account's cash movements as dated facts.

    The ACTUAL events the walk folds, and since plan step X-f3b there are TWO
    kinds of them (ruling **R-FM**): every balance-contributing row for the
    account in the scenario whose status is settled, and every POSTED PURCHASE
    recorded against one of its rows (:func:`_posted_purchase_facts`).  Both are
    valued and dated once, and both carry the budget column they were attributed
    to, so the ONE valued row set can be grouped on either clock (see
    :class:`CashSourceFact`).  For the transaction half both extra fields are
    free: the budget column is the row's own ``pay_period_id`` and the
    transaction TYPE is a column beside it, so neither costs a join.  *That
    first clause read "the shared loader already joins ``pay_period``" until
    pay-calendar plan step C4-a-1 deleted that eager load; the field this
    carries was never the relationship, and saying it was made a stale
    justification out of a true sentence.*

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
    its ``selectinload(entries)`` are stated once for the two halves of the
    event stream rather than copied per half.  One gate for both halves is what makes the
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
        One :class:`CashSourceFact` per settled row and per posted purchase,
        ASCENDING by ``(settled_on, transaction_id, entry_id)`` -- the order the
        walk consumes them in, with the ids breaking a same-day tie
        deterministically and a parent sorting before its own purchases.  Order
        WITHIN a day is not observable: the walk only sums a day's sources
        before its assertions close it (ruling R-DH), and the fold reads a day's
        boundary after every step on it, so only the day's total can be read
        back.  The sort is total anyway, because a nondeterministic order in a
        financial replay is a reproducibility defect even where it is
        arithmetically inert.
    """
    rows = _unwindowed_contributing_rows(
        account_id, scenario_id, Transaction.status_id.in_(settled_status_ids()),
    )
    facts = [
        CashSourceFact(
            transaction_id=txn.id,
            entry_id=None,
            pay_period_id=txn.pay_period_id,
            is_income=txn.is_income,
            settled_on=settled_day(txn.id, txn.settled_on),
            reconciled_by_id=txn.reconciled_by_id,
            delta=settled_cash_leg(txn),
        )
        for txn in rows
    ]
    facts.extend(_posted_purchase_facts(account_id, scenario_id))
    # ``entry_id or 0`` orders a parent's own leg before its purchases and keeps
    # the key total: entry ids are positive, so ``0`` is unambiguously "the
    # transaction itself" and no ``None`` reaches the comparison.
    facts.sort(
        key=lambda fact: (
            fact.settled_on, fact.transaction_id, fact.entry_id or 0,
        )
    )
    return facts
