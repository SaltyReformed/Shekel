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

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)
from app.utils.dates import attribution_date
from app.utils.log_events import BUSINESS, log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Statement:
    """The bank statement being reconciled: whose, which account, which day.

    The three scalars every function in this package takes together, as one
    value.  They are not independent -- an assertion declares the real balance
    of ONE account on ONE civil day for ONE owner, and a call site that could
    pair one account's id with another day's would be a call site that can
    reconcile against a statement nobody read.

    Attributes:
        owner_id: The user_id whose rows may be offered or settled.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for -- the bound
            every offer is measured against, and the day every tick records
            its money as having moved.
    """

    owner_id: int
    account_id: int
    observed_on: date


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
    * the parent period is this OWNER's and starts on or before *observed_on*
      -- ownership, and the SQL superset of the landing-day bound.
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
        Transaction.pay_period_id.in_(
            db.session.query(PayPeriod.id).filter(
                PayPeriod.user_id == statement.owner_id,
                PayPeriod.start_date <= statement.observed_on,
            )
        ),
    ]


def attributed_on(txn: Transaction) -> date:
    """Return the day the projection lands *txn* on.

    Stated once because two things read it: the bound
    (:func:`lands_on_or_before`) and the caption the panel prints
    (:attr:`~app.services.reconcile_service.OutstandingTransaction.attributed_on`).
    A row offered under a caption that disagrees with why it was offered is the
    "a figure and its caption never disagree" rule broken on the one screen a
    user reads against a paper statement.

    Args:
        txn: The row, with ``pay_period`` loaded.

    Returns:
        Its clamped attribution date.
    """
    period = txn.pay_period
    return attribution_date(
        txn.due_date, period.start_date, period.end_date,
    )


def lands_on_or_before(txn: Transaction, observed_on: date) -> bool:
    """Return whether the projection lands *txn* on or before *observed_on*.

    The Python half of the bound, and the reason it is not SQL: the landing day
    is :func:`~app.utils.dates.attribution_date`, the clamp the calendar's day
    cells and the balance line's daily ramp already share, so writing it as a
    ``LEAST(GREATEST(...))`` here would be a second implementation of one rule.

    The offer set is the OVERDUE set: ruling **R-G** clamps a projected row's
    landing day up to ``as_of + 1`` (``balance_at/_cash_fold.py``), so a row
    whose attribution day has already passed is precisely one the projection is
    still holding forward.  Measured on production 2026-08-11, Checking at its
    latest assertion: the SQL superset admits 5 rows and this narrows them to 3.

    Args:
        txn: A row from the SQL superset, with ``pay_period`` loaded.
        observed_on: The civil day the balance was asserted for.

    Returns:
        True when the row is OVERDUE against that day.
    """
    return attributed_on(txn) <= observed_on


def wholly_spent_by(txn: Transaction, observed_on: date) -> bool:
    """Return whether everything *txn* would BOOK moved by *observed_on*.

    **The second half of "a statement of this day could settle this row", and
    it is about the row's VALUE rather than its landing day.**  An envelope
    settles at ``sum(entries)`` over EVERY entry it holds
    (``entry_service.compute_actual_from_entries``, and
    ``entry_service._resync_settled_envelope`` re-derives it the same way after
    any later mutation), so a row still holding a purchase made AFTER the
    statement day would book that purchase too -- dated on the statement's day,
    at a figure the panel offers with no correction box because an
    entries-derived row is not correctable (ruling **R-FF**).

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
    2026-08-06, which ``ReconciledThrough.covers`` then absorbs into that day's
    anchor correction -- so the purchase contributed nothing forward and the
    projected balance rose by exactly `$137.45` at +30d, +90d and +365d.  That
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

    Args:
        txn: A row from the SQL superset, with ``entries`` loaded.
        observed_on: The civil day the balance was asserted for.

    Returns:
        True when no entry against *txn* postdates the statement.
    """
    return all(entry.purchased_on <= observed_on for entry in txn.entries)


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

    Two eager loads are ALWAYS applied because the shared bound reads them:
    ``pay_period`` feeds the attribution clamp (:func:`attributed_on`) and
    ``entries`` feeds :func:`wholly_spent_by`.  An arm adds its own through
    :attr:`Arm.load_options` -- the transaction arm loads ``template`` because
    it reads ``tracks_purchases``, which lazy-loads a template per row
    otherwise.

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
        if lands_on_or_before(txn, statement.observed_on)
        and wholly_spent_by(txn, statement.observed_on)
    ]
    rows.sort(key=lambda txn: (attributed_on(txn), txn.id))
    return rows


def record_settled(
    arm: Arm,
    statement: Statement,
    transaction_ids: "set[int]",
    corrections: "dict[int, Decimal]",
) -> int:
    """Settle every row of *arm* the form ticked, and report what landed.

    **The WRITER, once, for both source-row arms**, and what an arm keeps is
    :class:`Arm`.  Three things happen per row and none of them is a money
    rule: the arm's own settle runs, what it says about a human's figure is
    counted, and the totals are logged once.

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
