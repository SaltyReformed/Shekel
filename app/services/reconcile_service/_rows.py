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

**What is NOT here, and why, because the finding guessed at more.**  N-225
proposed the shared shape as ``(extra scope clause, settle callable,
OfferKind)`` -- i.e. the WRITER's loop parameterised too.  Measured against the
two arms as built, that loop is six lines of glue around one rule, *count what
the verb applied rather than what the column did* (finding **N-231**), and a
rule is stated once by PUBLISHING it, not by threading two callables through a
reduction: each settle verb publishes its own ``is_correction`` predicate and
each arm reads it.  The arms keep their own writers, which is also what lets
each own its log event and its refusals.  What is shared here is the part where
copying would have been a defect: the SCOPE, which is the security property,
and the BOUND, which is the money one.

**Membership of an arm is the arm's own clause, and it is the ONE thing this
module refuses to decide.**  ``kind_clauses`` is not a convenience: the
transaction arm's ``transfer_id IS NULL`` and the transfer arm's
``transfer_id IS NOT NULL`` partition the table, and a shared default would be
a third place for that partition to be stated.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in, ORM rows out.
  - Reads only.  Nothing here mutates, flushes or commits.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import (
    balance_contributing_clause,
    is_projected_clause,
)
from app.utils.dates import attribution_date


@dataclass(frozen=True)
class Arm:
    """Which rows are one arm's, and what it needs loaded to price them.

    The two things :func:`outstanding_rows` cannot decide for a caller, as one
    value rather than two parameters -- so an arm states its own shape ONCE, as
    a module constant, and its reader and its writer are structurally incapable
    of asking for different rows.  That is the same property the purchase arm
    gets from sharing a clause list, which is the security property this
    package is built on.

    Attributes:
        kind_clauses: The arm's membership clauses.  It has no default and must
            not acquire one: the transaction arm's ``transfer_id IS NULL`` and
            the transfer arm's ``transfer_id IS NOT NULL`` PARTITION the table,
            and a default would be a third place for that partition to be
            stated.
        load_options: Eager loads this arm needs beyond the two the shared
            bound already requires (``pay_period`` and ``entries``).  Empty for
            an arm that prices its rows from columns alone.
    """

    kind_clauses: tuple
    load_options: tuple = field(default=())


def outstanding_scope(
    owner_id: int,
    account_id: int,
    observed_on: date,
    kind_clauses: tuple,
) -> list:
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
        owner_id: The user_id whose rows to scope to.
        account_id: The cash account the balance was asserted for.
        observed_on: The civil day that balance was true for.
        kind_clauses: The calling arm's own membership clauses.

    Returns:
        A list of SQLAlchemy filter clauses to apply to a
        :class:`~app.models.transaction.Transaction` query.
    """
    return [
        Transaction.account_id == account_id,
        *kind_clauses,
        is_projected_clause(Transaction),
        balance_contributing_clause(),
        Transaction.pay_period_id.in_(
            db.session.query(PayPeriod.id).filter(
                PayPeriod.user_id == owner_id,
                PayPeriod.start_date <= observed_on,
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
    owner_id: int,
    account_id: int,
    observed_on: date,
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
        owner_id: The user_id whose rows to scope to.
        account_id: The cash account whose balance was asserted.
        observed_on: The civil day that balance was true for.
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
        .filter(
            *outstanding_scope(
                owner_id, account_id, observed_on, arm.kind_clauses,
            )
        )
    )
    if transaction_ids is not None:
        query = query.filter(Transaction.id.in_(transaction_ids))
    rows = [
        txn for txn in query.all()
        if lands_on_or_before(txn, observed_on)
        and wholly_spent_by(txn, observed_on)
    ]
    rows.sort(key=lambda txn: (attributed_on(txn), txn.id))
    return rows
