"""
Shekel Budget App -- The shape a SOURCE-ROW arm of the outstanding set has

The scope, the bound and the loader that the two ``budget.transactions`` arms
share: the transaction arm (an envelope's own close, and a bill) and the
transfer arm (a transfer's shadow on this account).  Both ask the same
question of the same table -- *which rows on this account had the bank not yet
taken by the statement's day* -- and differ only in WHICH rows are theirs and
what a tick MEANS for one.

**This is finding N-225 fixed rather than paid.**  That row was opened by
X-f2-c2's own adversarial design review, which measured the package's stated
cut: :mod:`app.services.reconcile_service` says it has "three row kinds whose
settle verbs are genuinely different", and only ONE of the three is different
in shape.  A PURCHASE settles by stamping one column, so its arm's scope is
over :class:`~app.models.transaction_entry.TransactionEntry` and its writer is
a bulk ``UPDATE`` (:mod:`._purchases`).  The other two are both "query
``Transaction`` under a scope, narrow in Python by attribution date, loop
dispatching to a per-row service verb" -- so building the transfer arm by
copying the transaction arm would have put ~250 lines of one scope into two
files that can then drift about what "outstanding" means, on a screen that
moves money.

**The WRITER is here too, and this leaf had to be argued out of leaving it per
arm.**  N-225 named the shared shape as *(extra scope clause, settle callable,
OfferKind)*; X-f2-c3's first design read that as over-reach and kept each arm's
loop, on the argument that it was six lines of glue around one rule.  Writing
the second one settled it: pylint's cross-file ``duplicate-code`` check
reported the pair twice -- first the telemetry tails, then the whole body once
those were shared -- because the two loops were not similar, they were the same
function.  The finding was right and the first reading of it was not.

So :func:`record_settled` narrows the ticked ids through the arm's own scope,
settles each row through the arm's own verb, counts what that verb says was a
HUMAN's correction (finding **N-231**), and reports how much of what was asked
for landed.  What stays the arm's is :class:`Arm`.

**Membership of an arm is the arm's own clause, and it is the ONE thing this
module refuses to decide.**  ``kind_clauses`` is not a convenience: the
transaction arm's ``transfer_id IS NULL`` and the transfer arm's
``transfer_id IS NOT NULL`` partition the table, and a shared default would be
a third place for that partition to be stated.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, ORM rows out.
  - The reads are pure.  :func:`record_settled` MUTATES through the arms'
    service verbs and does NOT commit -- the caller owns the session boundary.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.cash_ledger import AnchorPoint
from app.services.pay_calendar import FiledRow, PayCalendar
from app.services.settle_day import SettleDay
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)
from app.utils.log_events import BUSINESS, log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Statement:
    """The bank statement being reconciled: whose, which account, which ASSERTION.

    The facts every function in this package takes together, as one value.  They
    are not independent -- an assertion declares the real balance of ONE account
    on ONE civil day for ONE owner, and a call site that could pair one
    account's id with another day's would be a call site that can reconcile
    against a statement nobody read.

    **It carried a bare ``observed_on`` until plan step X-f3a-1** and now
    carries the ASSERTION, with the day derived from it.  Ruling **R-FL** makes
    a tick record WHICH statement showed the money, and a civil day cannot name
    one: production carries three days on which Checking holds two or three
    assertions.  The day is a property rather than a second field for the reason
    the value exists at all -- two fields that must agree are two fields a
    caller can mismatch, and here the mismatch would stamp a line with a
    statement it was not measured against.

    **It carried a bare ``owner_id`` until pay-calendar plan step C4-a-2** and
    now carries that owner's CALENDAR, with the id derived from it, for exactly
    the same reason one line up.  This package has to date every row it offers
    (:func:`attributed_on`), and a pay period's span is DERIVED -- so the
    calendar arrived as a fact this value needs.  Carrying both an ``owner_id``
    and a calendar would have been two statements of whose rows these are, and
    the mismatch -- one owner's rows dated against another owner's paydays --
    produces a plausible wrong day rather than an error.  Every scope clause in
    this module now reads its owner THROUGH the calendar it dates by.

    Attributes:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            resolved by the ROUTE and threaded (this package holds no read pass
            of its own).  It answers two questions here: whose rows may be
            offered or settled (:attr:`owner_id`), and which span each offered
            row is budgeted in (:func:`attributed_on`).
        account_id: The cash account whose balance was asserted.
        anchor: The governing :class:`~app.services.cash_ledger.AnchorPoint` --
            the assertion being reconciled against.  Its ``anchor_id`` is what a
            tick writes into ``reconciled_by_id``, and its ``observed_on`` is the
            day every tick records -- as a BOUND, which :attr:`settle_day` is
            what says (plan step **X-az**).
    """

    calendar: PayCalendar
    account_id: int
    anchor: AnchorPoint

    @property
    def owner_id(self) -> int:
        """Return the user_id whose rows may be offered or settled.

        Read THROUGH the calendar rather than stored beside it, so the rows a
        scope admits and the paydays they are dated against always describe one
        owner.

        Returns:
            The calendar's ``user_id``.
        """
        return self.calendar.user_id

    @property
    def owned_period_ids(self) -> "frozenset[int]":
        """Return every SAVED pay-period id of this statement's owner.

        **The OWNERSHIP scope for every arm of this package, and it comes off
        the CALENDAR rather than off ``pay_periods.user_id``** (pay-calendar
        plan step C4-a-2).  Whose a row is is reached through its paycheck
        here, and doing that with a correlated subquery would ask the TABLE a
        question this value already answers -- two statements of one fact
        inside one request, the same defect :attr:`owner_id` exists to remove
        one clause down.

        **``budget.transactions`` HAS carried a ``user_id`` since plan step
        ``pay_calendar:C13-a``, and this scope has not moved onto it.**  The
        sentence here used to be "it carries no ``user_id``, so ownership has
        to be reached through the paycheck", which is no longer the reason for
        anything.  Whether this scope should become one equality on that
        column, or stay the calendar's saved ids, is a question with an
        answer already: what this scope needs is the PERIOD SET and not the
        owner, so ``Transaction.user_id`` cannot replace it.  It is also NOT
        one of finding **P75**'s nineteen -- that census counts reads that
        REFUSE and excludes scopes by name -- and the clause is graded:
        deleting ``_purchases.py``'s copy of it fails
        ``test_the_PURCHASE_arm_is_scoped_the_same_way`` (measured
        2026-09-02).  A ``C13-b`` that treated it as a comparison to retire
        would delete load-bearing code.  **``C13-b`` did not** (2026-09-03):
        it moved the ELEVEN reads that walk a row's paycheck for its owner and
        the EIGHT that refetch a submitted period, and left every SCOPE --
        this one and ``statement_match._candidates``' two -- exactly as it
        found them.

        **The "is this period SAVED" filter is the CALENDAR's own** since
        pay-calendar plan step C4-a-4:
        :meth:`~app.services.pay_calendar.PayCalendar.saved_by_id` states it,
        and this property and :attr:`offerable_period_ids` beside it each wrote
        ``period_id is not None`` for themselves until then -- two spellings of
        one predicate inside one class, in a package that owns neither.

        **What it buys is a REFUSAL becoming unconstructible rather than
        merely unlikely.**  A row this scope admits names a period the calendar
        was built from, so :meth:`~app.services.pay_calendar.PayCalendar.require_period`
        -- which :func:`attributed_on` and
        :func:`~._assemble._block_headings` both call, on these rows -- cannot
        answer for a period the calendar lacks.  That is the rule
        ``require_period``'s own docstring states: *where the precondition is
        carried by the QUERY, the total form is honest; where it rests on two
        reads agreeing, it is not.*  It is also the shape
        ``statement_match._candidates._transaction_candidates`` already has,
        for the same reason and at the same scale.

        **The state it makes inexpressible is REACHABLE, which is why this is
        not decoration** (balance finding **N-358**).  Under ``READ COMMITTED``
        a command's post-write re-render is a second snapshot, and ``/grid``
        and ``/dashboard`` append paydays AND generate rows into them inside
        one ``write_transaction`` (``routes/grid/page.py``,
        ``routes/_period_population.py``, ruling **R-R38**).  So a concurrent
        render on a lapsed schedule can create a period dated on or before the
        statement's day and file projected rows in it between this request's
        calendar read and its row read.  Scoped by ``pay_periods.user_id`` the
        query returns those rows and the span lookup raises; scoped by the
        calendar's own ids it does not ask about them, which is the answer the
        request would have given had the concurrent write not landed.

        Returns:
            The owner's saved period ids.  A projected period carries no id and
            is not here; the empty set for an owner with no paydays, which
            admits nothing and is the correct offer set for them.
        """
        return frozenset(self.calendar.saved_by_id())

    @property
    def offerable_period_ids(self) -> "frozenset[int]":
        """Return the owned period ids a row could be OFFERED from.

        :attr:`owned_period_ids` narrowed to the periods that had started by
        the statement's day -- ownership, and the SQL superset of the
        landing-day bound :func:`lands_on_or_before` applies in Python.

        **The two sets are not interchangeable and the difference is an arm.**
        The source-row arms bound the ROW's own landing day, so a period that
        starts after the statement can hold nothing they may offer.  The
        purchase arm bounds the ENTRY's ``purchased_on`` instead and its
        parent's period is unbounded -- a purchase made today against next
        period's envelope is a state the app can express, and it is the
        purchase-date warning ``pay_calendar:C4-a-3`` owns -- so that arm takes
        :attr:`owned_period_ids` whole.  Handing it this narrower set would
        silently stop offering those purchases.

        Returns:
            The offerable subset, empty when no period had started yet.
        """
        return frozenset(
            period_id
            for period_id, period in self.calendar.saved_by_id().items()
            if period.start_date <= self.observed_on
        )

    @property
    def observed_on(self) -> date:
        """Return the civil day this statement's balance was true for.

        The bound every offer is measured against, and the day every tick
        records its money as having moved.  Read THROUGH the assertion rather
        than stored beside it, so the day and the statement a tick names can
        never describe different rows.

        Returns:
            The assertion's ``observed_on``.
        """
        return self.anchor.observed_on

    @property
    def settle_day(self) -> SettleDay:
        """Return the day a tick records, and WHAT KIND of day it is.

        **``asserted``, and the basis is the whole point of plan step X-az**
        (finding **N-332**).  The owner did not observe this money posting on
        this day; they asserted a BALANCE for this day and this money was inside
        it, so the true posting day is on or BEFORE it.  That distinction is a
        money fact rather than a label: the statement matcher bounds a purchase
        by ``(purchased_on, settled_on)`` when the day is a bound and pins it to
        a point when it is not, and reading this panel's bound as a point put 59
        of the developer's 61 reconciled purchases out of reach of their own
        bank lines -- which the merchant policy then offered to RECORD, for 50
        duplicates worth ``$3,590.00``.

        **It is a property of the STATEMENT rather than a value each arm
        builds**, for the reason :attr:`observed_on` is: three arms tick against
        one assertion, and three constructions of "what kind of day is this" is
        three chances for one of them to say something the other two do not.
        The purchase arm cannot use it -- its writer is a bulk ``UPDATE`` with
        no ORM row to hand -- and it resolves the same member there by name; the
        two are one sentence apart in this module's own package.

        Returns:
            A :class:`~app.services.settle_day.SettleDay` over
            :attr:`observed_on` on the ``asserted`` basis.
        """
        return SettleDay(
            day=self.observed_on,
            basis=SettledDayBasisEnum.ASSERTED,
        )


@dataclass(frozen=True)
class Arm:
    """What one SOURCE-ROW arm IS: which rows are its own, and what a tick does.

    The four things this module cannot decide for an arm, as ONE value -- so an
    arm states its shape once, as a module constant, and its reader and its
    writer are structurally incapable of asking for different rows.  That is
    the same property the purchase arm gets from sharing a clause list, and it
    is the security property this package is built on rather than a tidiness
    one.

    Attributes:
        kind_clauses: The arm's membership clauses.  It has no default and must
            not acquire one: the transaction arm's ``transfer_id IS NULL`` and
            the transfer arm's ``transfer_id IS NOT NULL`` PARTITION the table,
            and a default would be a third place for that partition to be
            stated.
        settle: ``(row, submitted, statement) -> bool`` -- settles one row
            through the arm's own service verb and returns whether a HUMAN's
            figure was booked.  The bool is asked of the verb's own published
            predicate rather than read off the column afterwards, which is
            finding **N-231**: an envelope's close always writes
            ``actual_amount``, so a column reading counts machine writes as
            hand-typed corrections.
        event: The ``EVT_*`` constant this arm reports under.  Each arm has its
            OWN rather than sharing one, because a reader asking why a second
            account's balance moved has to be able to find the transfer arm
            without knowing to look under transactions.
        load_options: Eager loads this arm needs beyond the two the shared
            bound already requires (``pay_period`` and ``entries``).  Empty for
            an arm that prices its rows from columns alone.
    """

    kind_clauses: tuple
    settle: object
    event: str
    load_options: tuple = field(default=())


def outstanding_scope(statement: Statement, kind_clauses: tuple) -> list:
    """Return the SQL half of "this row is still waiting on the bank".

    A SUPERSET, by construction: the day bound below is on the pay period's
    start rather than on the row's own landing day, and
    :func:`lands_on_or_before` narrows what comes back.  The two halves are
    always applied together by :func:`outstanding_rows`, which is the only
    caller, so there is no shape in which a reader or a writer gets one of
    them.

    **The offer set is a SUBSET of the account's PLAN**, and the clauses say so
    by sharing the plan loader's own builders rather than re-deriving them:
    ``is_projected_clause`` composed with ``balance_contributing_clause`` is
    exactly what
    :func:`app.services.cash_ledger._facts.planned_cash_rows` narrows with.
    The status pair inside the second is redundant by construction (Projected
    is neither Credit nor Cancelled) and composed anyway, for the reason that
    loader gives: one shared statement of "which rows exist at all" beats two
    hand-written filters that agree today.

    Four shared clauses, each load-bearing, plus the arm's own:

    * the row is on THIS account -- a balance assertion declares the real
      balance of one account, and a user may hold more than one checking
      account.  Settling across accounts would book money against a statement
      that never showed it.
    * PROJECTED -- a settled row has already been recorded, and a Credit or
      Cancelled row is not money this account owes.
    * contributing and not soft-deleted -- the shared gate above.
    * the parent period is one of :attr:`Statement.offerable_period_ids` --
      ownership, and the SQL superset of the landing-day bound, as ONE clause.
      **It was a correlated subquery on ``pay_periods.user_id`` and
      ``start_date`` until pay-calendar plan step C4-a-2**; the ids come off
      the calendar now, which is what makes :func:`attributed_on`'s span
      lookup total rather than merely unlikely to refuse.  That property, and
      the reachable state it closes, are on :attr:`Statement.owned_period_ids`.
    * *kind_clauses* -- which rows are this ARM's.  See the module docstring
      for why it has no default.

    Not scoped by ``scenario_id``, for the same reason
    :func:`app.services.reconcile_service._purchases._outstanding_scope` is
    not: Phase 1 is baseline-only, so ``account_id`` fully isolates the set
    today, and when what-if scenarios land the callers must thread an
    operating-scenario context into EVERY arm.  One deferral, stated once per
    scope rather than differently per arm.

    Args:
        statement: The statement being reconciled.
        kind_clauses: The calling arm's own membership clauses.

    Returns:
        A list of SQLAlchemy filter clauses to apply to a
        :class:`~app.models.transaction.Transaction` query.
    """
    return [
        Transaction.account_id == statement.account_id,
        *kind_clauses,
        is_projected_clause(Transaction),
        balance_contributing_clause(),
        Transaction.pay_period_id.in_(statement.offerable_period_ids),
    ]


def attributed_on(statement: Statement, txn: Transaction) -> date:
    """Return the day the projection lands *txn* on.

    Stated once because two things read it: the bound
    (:func:`lands_on_or_before`) and the caption the panel prints
    (:attr:`~app.services.reconcile_service.OutstandingTransaction.attributed_on`).
    A row offered under a caption that disagrees with why it was offered is the
    "a figure and its caption never disagree" rule broken on the one screen a
    user reads against a paper statement.

    **The SPAN it clamps against is DERIVED, since pay-calendar plan step
    C4-a-2**, which is why this takes the statement -- see
    :meth:`~app.services.pay_calendar.PayCalendar.require_period`, the ONE
    statement of that rule for every caller placing a stored row, and
    :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day`, the rule
    itself.  It read ``txn.pay_period`` until then, so this panel offered and
    captioned a row against the STORED ``end_date`` while the cash fold under
    the same balance clamped the very same row at its derived one.

    **The refusal cannot fire, and it is carried by the QUERY rather than by an
    argument.**  Every row reaching this comes back from
    :func:`outstanding_scope`, whose ownership clause is
    :attr:`Statement.offerable_period_ids` -- the calendar's OWN saved ids -- so
    a row it returns names a period the calendar was built from and
    :meth:`~app.services.pay_calendar.PayCalendar.require_period` has nothing to
    refuse.  ``require_period`` stays anyway, as the raising twin a caller
    holding a stored ``pay_period_id`` is supposed to use: it now documents an
    invariant nothing can violate instead of guarding a state something could.

    **A first cut of this leaf scoped on ``pay_periods.user_id`` and argued the
    refusal was unreachable; the argument was measured FALSE** (adversarial
    design review, 2026-08-28), which is why the scope moved rather than the
    prose.  It said an appended payday could hold no rows because the doors that
    record one no longer generate into it -- true of the DOOR and false of the
    REQUEST: ``/grid`` and ``/dashboard`` append and then populate inside one
    ``write_transaction`` (``routes/_period_population.py``, ruling **R-R38**),
    so a concurrent render on a lapsed schedule creates exactly the rows the
    argument said could not exist.  Both of this package's COMMAND doors then
    render after committing -- the reconcile POST, and the true-up PATCH through
    ``prompt_fragment`` -- which is balance finding **N-358**'s shape on two
    money screens.  The query-carried scope closes it here without waiting for
    ``balance:X-i5``, and follows the rule ``require_period``'s own docstring
    states: *where the precondition is carried by the QUERY, the total form is
    honest; where it rests on two reads agreeing, it is not.*

    Args:
        statement: The statement being reconciled, carrying the owner's
            calendar.
        txn: The row, whose ``pay_period_id`` names its span.

    Returns:
        Its clamped attribution date.

    Raises:
        RuntimeError: *txn* names a pay period the statement's calendar does
            not hold -- unconstructible through :func:`outstanding_scope`, and
            loud rather than silent if a future caller reaches this with a row
            from somewhere else
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).
    """
    period = statement.calendar.require_period(FiledRow.for_row(txn))
    return period.attribution_day(txn.due_date)


def lands_on_or_before(statement: Statement, txn: Transaction) -> bool:
    """Return whether the projection lands *txn* on or before the statement day.

    The Python half of the bound, and the reason it is not SQL: the landing day
    is :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day`, the
    clamp the calendar's day cells and the balance line's daily ramp already
    share, so writing it as a ``LEAST(GREATEST(...))`` here would be a second
    implementation of one rule -- and since plan step C4-a-2 the span it clamps
    against is not in the row's own table to join to.

    The offer set is the OVERDUE set: ruling **R-G** clamps a projected row's
    landing day up to ``as_of + 1`` (``balance_at/_cash_fold.py``), so a row
    whose attribution day has already passed is precisely one the projection is
    still holding forward.  Measured on production 2026-08-11, Checking at its
    latest assertion: the SQL superset admits 5 rows and this narrows them to 3.

    Args:
        statement: The statement being reconciled -- its ``observed_on`` is the
            bound and its calendar dates the row.
        txn: A row from the SQL superset.

    Returns:
        True when the row is OVERDUE against that day.
    """
    return attributed_on(statement, txn) <= statement.observed_on


def wholly_spent_by(statement: Statement, txn: Transaction) -> bool:
    """Return whether everything *txn* would BOOK moved by the statement day.

    **The second half of "a statement of this day could settle this row", and
    it is about the row's VALUE rather than its landing day.**  An envelope
    settles at ``sum(entries)`` over EVERY entry it holds -- that IS its
    ``purchases``-basis settlement record, answered on read by
    ``row_valuation.settled_figure`` (plan step X-au-c3), where it used to be a
    figure a deleted hook wrote into ``actual_amount`` and re-derived after any
    later mutation.  So a row still holding a purchase made AFTER the statement
    day would book that purchase too -- dated on the statement's day, at a
    figure the panel offers with no correction box because an entries-derived
    row is not correctable (ruling **R-FF**).

    **This is the bound the purchase arm has always had, applied to the
    aggregate.**  ``_purchases._outstanding_scope`` refuses an entry with
    ``purchased_on > observed_on`` and says why: a statement cannot show a
    purchase made after the day it covers.  Without this the two arms disagree
    about the same dollars -- the sibling arm refuses a purchase and this one
    re-admits it inside its parent's total.

    **Measured, because it is a money defect and not a tidiness one.**  On a
    clone of production, planting one `$137.45` purchase three days after
    Checking's 2026-08-06 assertion made the panel offer *Close Groceries* at
    `$622.55` instead of `$485.10`; ticking it booked `$622.55` stamped
    2026-08-06, which that day's assertion then absorbs into its own correction
    -- so the purchase contributed nothing forward and the projected balance
    rose by exactly `$137.45` at +30d, +90d and +365d.  (The measurement was
    taken while the absorbing rule was ``ReconciledThrough.covers``; plan step
    X-f3a-1 made it ``StatementCoverage``, which answers the same for an
    unlinked row and so leaves the figure standing.)  That
    is already-spent money handed back to the projection, which is the class of
    defect this arc exists to remove.

    A row with no entries answers True over an empty sequence, so a bill, a
    deposit and a TRANSFER SHADOW are unaffected: they carry a single amount,
    and :func:`lands_on_or_before` is the whole bound for them.  **A shadow
    structurally cannot carry one** -- ``entry_service.create_entry`` refuses a
    parent that is not ``tracks_purchases``, and a shadow has no template and a
    False ``is_envelope`` -- which production confirms at 342 shadows and 0
    entries against any of them.  It is asked of the transfer arm anyway, and
    that is the point of a shared bound: an arm does not get to decide that
    half of "could this statement settle this row" does not apply to it.

    **It takes the STATEMENT rather than a bare day**, which is the shape all
    three per-row predicates here share since plan step C4-a-2.  Two of them
    need the calendar the statement carries; leaving the third on a loose
    ``observed_on`` would be one predicate of three a caller can hand a
    different day from the one the other two were measured against.

    Args:
        statement: The statement being reconciled -- its ``observed_on`` is the
            bound.
        txn: A row from the SQL superset, with ``entries`` loaded.

    Returns:
        True when no entry against *txn* postdates the statement.
    """
    return all(
        entry.purchased_on <= statement.observed_on for entry in txn.entries
    )


def outstanding_rows(
    arm: Arm,
    statement: Statement,
    *,
    transaction_ids: "set[int] | None" = None,
) -> "list[Transaction]":
    """Return the rows *arm* offers, both halves of its scope applied.

    **The ONE place an arm's scope is expressed**, so its reader and its writer
    cannot come to disagree about what "outstanding" means -- the property the
    purchase arm gets by sharing a clause list, expressed as a shared loader
    here because these arms' writers need the ROWS (their settle is a per-row
    service verb, not a bulk ``UPDATE``).

    Two eager loads are ALWAYS applied.  ``entries`` feeds
    :func:`wholly_spent_by`.  ``pay_period`` feeds NOTHING NAMED any more, and
    the paragraph below is why it is still here.  **Its reason changed at
    pay-calendar plan step C4-a-2 and the load did not**, so the reason is
    written out rather than left to be reconstructed from the load's presence:
    it fed the attribution clamp until then; :func:`attributed_on` now reads
    the span off the statement's calendar and the row's ``pay_period_id``
    column, so the READER touches no relationship at all.

    **The one consumer it was KEPT for is GONE, and the option is not**
    (plan step ``pay_calendar:C13-b``).  It was kept for a single line --
    ``transaction_service._settle.settle_from_entries`` logging
    ``txn.pay_period.user_id`` -- and that line reads ``txn.user_id`` now, so
    no code this loader feeds names the relationship any more.  Three reads in
    ``loan_posting_service._payments`` moved the same way in the same step.

    **What has NOT been established is whether anything else on the TRANSFER
    arm reaches it**, and that is exactly the claim an adversarial code review
    narrowed here on 2026-08-28 -- so this step declines to widen it back by
    deleting the option on a census it did not take.  The predicate the next
    reader owes, rather than a count: an attribute read of ``pay_period`` on a
    row :func:`outstanding_rows` RETURNED, reachable from either arm's write
    half, which is a grep for an attribute read of ``pay_period`` across
    ``app/`` with the docstring mentions struck out and each survivor traced
    to the query that produced its row.  ``loan_loaders`` has three, and whether a row from THIS
    scope can reach them is the open half.  Removing the option is a
    MEASUREMENT -- a lazy load here lands an AUTOFLUSH in the middle of a
    settle that has already mutated the row -- and it belongs to whoever takes
    that census, not to the step that emptied the named consumer.

    An arm adds its own through :attr:`Arm.load_options` -- the transaction arm
    loads ``template`` because it reads ``tracks_purchases``, which lazy-loads a
    template per row otherwise.

    Args:
        arm: Which rows are the caller's, and what it needs loaded.
        statement: The statement being reconciled.
        transaction_ids: The writer's narrowing -- the ids a form submitted.
            ``None`` (the reader) means "everything in scope".  An id outside
            the scope simply does not come back, which is the set-operation
            form of the project's "404 for both not-found and not-yours" rule.

    Returns:
        The matching rows, ordered by their landing day and then by id so the
        panel's blocks and the writer's loop are both deterministic.
    """
    query = (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.pay_period),
            selectinload(Transaction.entries),
            *arm.load_options,
        )
        .filter(*outstanding_scope(statement, arm.kind_clauses))
    )
    if transaction_ids is not None:
        query = query.filter(Transaction.id.in_(transaction_ids))
    rows = [
        txn for txn in query.all()
        if lands_on_or_before(statement, txn)
        and wholly_spent_by(statement, txn)
    ]
    rows.sort(key=lambda txn: (attributed_on(statement, txn), txn.id))
    return rows


def record_settled(
    arm: Arm,
    statement: Statement,
    transaction_ids: "set[int]",
    corrections: "dict[int, Decimal]",
) -> int:
    """Settle every row of *arm* the form ticked, and report what landed.

    **The WRITER, once, for both source-row arms**, and what an arm keeps is
    :class:`Arm`.  Three things happen per row and none of them is a money rule:
    the arm's own settle runs, what it says about a human's figure is counted,
    and the totals are logged once.

    **Recording WHICH statement showed the row is the ARM's** (ruling **R-FL**),
    and that is not a preference: for the transfer arm the row is a SHADOW, and
    ``CLAUDE.md``'s transfer invariant 4 says no code path mutates one directly.
    A write here would be the first exception to a rule the project treats as
    critical, so the transfer arm goes through
    ``transfer_service.record_clearing`` and the transaction arm -- whose scope
    is that arm's complement, ``transfer_id IS NULL`` -- writes its own row.

    **It is one function because the two writers HAD BECOME one**, and the gate
    is what said so rather than a preference.  X-f2-c2 left the loop per-arm on
    the argument that it was six lines of glue; X-f2-c3 wrote the second and
    pylint's cross-file ``duplicate-code`` check reported the pair, twice --
    first the telemetry tails, then the whole body once the tails were shared.
    Finding **N-225** predicted this shape in its own words, *(extra scope
    clause, settle callable, OfferKind)*, and was right where the leaf's first
    reading of it was not.

    **The ids are re-derived through the arm's own scope rather than trusted.**
    An id belonging to another user, another account, a settled row or a row
    this arm does not own simply does not come back from
    :func:`outstanding_rows` and is silently skipped -- the set-operation form
    of the project's "404 for both not-found and not-yours" rule.  Both arms
    are handed the SAME id set, and their scopes are complements, so an id
    settles through exactly one of them and can never settle twice.

    **The count is the VERB's answer, not the column's** (finding **N-231**).
    Reading ``actual_amount`` before and after counted every envelope close as
    a hand-typed correction, because that settle always writes the column --
    measured on a probe of one envelope with nothing submitted, which logged
    ``corrected_count: 1``.  Ruling **R-FB**'s production figure ("11 of 93
    settled bills carry a hand-typed correction") is made of this same signal,
    so it is the one number here that had to be right.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        arm: Which rows are the caller's, and what a tick does to one.
        statement: The statement being reconciled.
        transaction_ids: The ids the user ticked.  An empty set is a no-op that
            issues no query.
        corrections: ``{transaction id: amount}`` from the panel's amount
            boxes.  An id with no entry settles at the row's own figure.

    Returns:
        How many rows settled -- what actually CHANGED, never what was asked
        for.  The caller compares the two and tells the user their ticks landed
        on rows something else had already moved.

    Raises:
        ValidationError: Propagated from the arm's settle verb -- an illegal
            transition a stale panel can still submit.  A 400 at the route.
        PostingError: Propagated from the verb's ledger reconcile.  Fails loud.
    """
    if not transaction_ids:
        return 0

    rows = outstanding_rows(arm, statement, transaction_ids=transaction_ids)
    corrected = 0
    for row in rows:
        if arm.settle(row, corrections.get(row.id), statement):
            corrected += 1

    if rows:
        log_event(
            logger, logging.INFO,
            arm.event, BUSINESS,
            "Outstanding rows settled against a bank statement",
            user_id=statement.owner_id,
            account_id=statement.account_id,
            observed_on=statement.observed_on.isoformat(),
            settled_count=len(rows),
            requested_count=len(transaction_ids),
            corrected_count=corrected,
        )

    return len(rows)
