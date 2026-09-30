"""
Shekel Budget App -- Pay Period Lock Classifier

**The one place that decides whether a pay period may be deleted or
rebuilt**, and nothing here deletes or rebuilds anything.  Truncate and
regenerate consult it before touching a row; the settings UI renders its
result as a per-period lock badge.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``, and issues no write of any
kind.

**It lived inside ``pay_period_admin`` until plan step C3-a** (developer
ruling, 2026-08-10), which is where its own docstring called it "the module's
foundation" while that module's other job was the four destructive writers
built ON the foundation.  Two concerns, and the seam is sharp: everything here
answers a read-only question about a period's state, everything there acts on
the answer.  The line count is what reported it -- ``pay_period_admin`` reached
pylint's 1000-line ceiling -- but the ceiling was the symptom.

The precedence lives in :func:`_resolve_lock`, which
:func:`classify_schedule_locks` is the only caller of.  *That paragraph said
the precedence was "shared by the single-period and bulk classifiers, so the two
query strategies (scalar EXISTS vs. set membership) cannot disagree" until plan
step C2-f3b re-read it: there had been ONE query strategy since the
single-period door became a delegating wrapper (``0c7bb2a``), so the property it
claimed had no subject, and that door -- which no module under ``app/`` had
called since -- is DELETED.*

**It classifies a whole PAY CALENDAR since plan step C2-f3b**, not a list of
ORM rows.  Two things moved with that, and the second is what the first is for.

**The HISTORICAL test reads the DERIVED end.**  It compared
``budget.pay_periods.end_date`` -- a stored copy of ``lead(start_date) - 1``
that plan step **C4-c** dropped and that nothing reconciles against the paydays it
derives from.  A period this classifier calls historical is HARD-LOCKED, so a
stale column was a paycheck the app either protected or offered to delete for
the wrong reason.  Reading it off the derivation means the lock decision and
every other "which paycheck" answer in the application come from one rule, and
that this module survives C4 untouched.

**And the DOOR takes the calendar rather than a period set, which is what makes
a wrong input unconstructible.**  A first cut of this step took an iterable of
:class:`~app.services.pay_calendar.DerivedPeriod` and REFUSED one carrying no
``period_id`` -- a projection past the owner's horizon, which would key every
such period in a call under the same ``None``.  That refusal was a fence, and
all three ``app/`` callers were passing exactly one value:
:meth:`~app.services.pay_calendar.PayCalendar.saved`.  **An argument a caller
can get wrong is a defect rather than a contract** -- the sentence
:func:`~app.services.pay_calendar._views.saved_window` already makes one layer
down -- so the argument is gone and the door reads the window itself.  There is
nothing left to refuse (adversarial design review, 2026-08-19).

**``as_of`` is REQUIRED, which is a rule about clocks rather than about
defaults.**  It defaulted to ``date.today()``, so ``regenerate_pay_periods``
read the wall clock THREE times for one decision -- benign only because a
period cannot become historical between two statements of one transaction, an
argument that holds by timing rather than by construction.  A caller now
resolves the owner's civil day ONCE and hands it down.  The value every
``app/`` caller supplies is :func:`app.utils.dates.display_today`, ruled
2026-08-19 by the developer: this decides something against the OWNER's
calendar, and the process clock is the container's (finding **balance:N-191**,
which named this function as one of the two sites that needed the ruling).
"""

import enum
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Query
from sqlalchemy.sql.expression import ColumnElement

from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import transfer_legs
from app.services.pay_calendar import PayCalendar
from app.utils import archive_helpers
from app.utils.balance_predicates import settled_status_ids

logger = logging.getLogger(__name__)


class PeriodLockReason(enum.Enum):
    """Why a pay period may not be deleted or rebuilt.

    A non-``None`` reason is a HARD lock: the period either is historical
    or holds irreplaceable state (settled money, posted ledger entries), and
    no operation may delete or rebuild it --
    not even with ``confirm_discard``.  **An account's balance ASSERTION is no
    longer among them, and it did not become deletable**: ruling R-EO moved the
    assertion off the pay period entirely, so a schedule operation cannot reach
    it at all (see :func:`_resolve_lock`).  ``None`` means the
    period is the mutable payload truncate / regenerate may rewrite; its
    projected and ad-hoc rows are guarded separately by the overridable
    discard gate.

    The members are ordered by precedence.  The classifier returns the
    FIRST applicable reason, so a historical period that also holds a
    settled transaction reports ``HISTORICAL``.

    **``HOLDS_MOVEMENT`` joined at plan step ``credit_card:CC-5-4a-4``**
    (rulings **R-CC54**: "truncate/regenerate lock its period", and
    **R-CC66**'s badge): a period holding a row or a transfer -- any status,
    soft-deleted or not -- that holds a payment or a purchase, dated or not.
    A movement is money that moved, so an item holding one is history, and
    deleting the period deleted it through the ``pay_period_id`` cascades;
    the movement's key refuses that now, and this makes the refusal a
    designed one.  It outranks ``LEDGER_POSTINGS`` because it is
    the truer sentence where both hold: a dated purchase's own legs are
    what make a period's ledger non-zero.

    **A TRANSFER answers ``SETTLED_TXN`` and ``HOLDS_MOVEMENT`` for itself
    since plan step ``balance:X-bi-6-4a-3``** (rulings **R-BAL125**,
    **R-BAL157**): by its own status, and by the one ``transfer_legs``
    question, never through its shadow rows (:func:`settled_items`,
    :func:`items_holding_a_movement`).
    """

    HISTORICAL = "historical"
    SETTLED_TXN = "settled"
    HOLDS_MOVEMENT = "holds_movement"
    LEDGER_POSTINGS = "ledger_postings"


def _resolve_lock(
    *, is_historical: bool, has_settled: bool, holds_movement: bool,
    has_unbalanced_ledger: bool,
) -> PeriodLockReason | None:
    """Apply the lock-reason precedence to four already-computed booleans.

    The single source of truth for the ordering; :func:`classify_schedule_locks`
    is its only caller (the module docstring says why the single-period
    classifier this once also served is gone).

    **``ACCOUNT_ANCHOR`` left this set at plan step X-f1c3c** (ruling R-EO),
    and it left by becoming unreachable rather than by being relaxed.  It
    refused a period an account's ``current_anchor_period_id`` pointed at; that
    column is deleted, and a balance ASSERTION no longer references a pay
    period either, so no period delete can take one.  What is still worth
    protecting is the period's POSTED state, and ``LEDGER_POSTINGS`` -- which
    outranked ``ACCOUNT_ANCHOR`` anyway -- covers it: measured on the
    developer's production data, all 10 periods holding an assertion carry an
    unbalanced ledger account, so the deleted reason was refusing nothing that
    survives without it.

    **``RECURRENCE_ANCHOR`` left the same way at plan step R7b-4.**  It
    refused a period some recurrence rule's ``start_period_id`` pointed at,
    and the hazard was real while it stood: that FK is ``ON DELETE SET NULL``,
    so deleting the period silently erased the rule's opening bound.  R7b-4
    folded the FK into ``recurrence_rules.start_date`` -- a DATE, which no
    schedule operation can cascade -- so a rule's opening bound now survives
    the deletion of any period.  The lock was protecting a bound that can no
    longer be lost, which makes it unreachable rather than relaxed.

    Args:
        is_historical: The period has already ended (``end_date`` is
            before the reference date).
        has_settled: The period holds a non-deleted settled row or transfer
            (see :func:`settled_items`).
        holds_movement: A row or a transfer in the period holds a payment or
            a purchase (see :func:`items_holding_a_movement`).
        has_unbalanced_ledger: The period's journal entries do NOT net to
            zero per ledger account -- posted financial state a CASCADE
            delete would mis-state (see
            :func:`_period_ids_with_unbalanced_ledger`).

    Returns:
        The first applicable :class:`PeriodLockReason`, or ``None`` when
        the period is mutable.
    """
    if is_historical:
        return PeriodLockReason.HISTORICAL
    if has_settled:
        return PeriodLockReason.SETTLED_TXN
    if holds_movement:
        return PeriodLockReason.HOLDS_MOVEMENT
    if has_unbalanced_ledger:
        return PeriodLockReason.LEDGER_POSTINGS
    return None


def classify_schedule_locks(
    calendar: PayCalendar, *, as_of: date,
) -> "dict[int, PeriodLockReason | None]":
    """Return ``{period_id: reason | None}`` for every SAVED period of *calendar*.

    Three set queries plus an in-memory date check -- the no-N+1 path the truncate
    and regenerate doors run before they delete anything, and the settings page
    renders as a per-period badge.

    **It takes the CALENDAR, not a period set** (plan step C2-f3b).  The result
    is keyed by ``budget.pay_periods.id``, so an unmaterialised period -- a
    projection past the owner's horizon, which carries ``period_id = None`` --
    would key every such period in one call under the same entry and collapse
    them onto each other (ledger row **P21**'s shape).  Reading
    :meth:`~app.services.pay_calendar.PayCalendar.saved` here rather than taking
    its result means no caller can supply that set at all: the door's one
    argument is the owner's whole schedule, and every value it admits is one the
    derivation produced.  The refusal a first cut of this step carried has no
    subject.

    **The HISTORICAL test reads the DERIVED end**: a period has ended when the
    day before its successor's payday is behind *as_of*.  That is the same
    figure ``budget.pay_periods.end_date`` used to store, read from the
    derivation instead so this decision could not be one a stale column moved
    -- and so plan step **C4-c**, which dropped the column, reached this
    module with nothing to change.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
            Only its SAVED periods are classified; a projection names no row to
            answer about.
        as_of: The owner's civil day, for the historical test: the period
            containing *as_of* and every later one is not historical.
            **Keyword-only and REQUIRED.**  It defaulted to ``date.today()``
            until plan step C2-f3b, which is how ``regenerate_pay_periods`` came
            to read the wall clock three times for one decision; every ``app/``
            caller now resolves :func:`app.utils.dates.display_today` once and
            threads it, which is the ruling of 2026-08-19 on the two sites
            finding **balance:N-191** named.

    Returns:
        A dict mapping each saved period's ``period_id`` to its lock reason (or
        ``None``).  Empty for an owner who has never generated a schedule -- no
        periods, no queries.

    Raises:
        PayCalendarError: *calendar*'s saved periods do not cover an unbroken
            span, which :meth:`~app.services.pay_calendar.PayCalendar.saved`
            refuses.  Unreachable through
            :func:`~app.services.pay_calendar.calendar_for`, which reads saved
            rows only.
    """
    saved = calendar.saved()
    period_ids = [period.period_id for period in saved]
    if not period_ids:
        return {}

    settled = _period_ids_of(settled_items(period_ids))
    holding = _period_ids_of(items_holding_a_movement(period_ids))
    unbalanced = _period_ids_with_unbalanced_ledger(period_ids)

    return {
        period.period_id: _resolve_lock(
            is_historical=period.end_date < as_of,
            has_settled=period.period_id in settled,
            holds_movement=period.period_id in holding,
            has_unbalanced_ledger=period.period_id in unbalanced,
        )
        for period in saved
    }


def items_holding_a_movement(periods: "list[int] | Query") -> "tuple[Query, Query]":
    """Return the ITEMS filed in *periods* that hold a payment or purchase.

    **Every item, soft-deleted or not, and every status** (plan step
    ``credit_card:CC-5-4a-4``, ruling **R-CC54**): the period's delete takes
    every row and transfer filed under it through the ``pay_period_id``
    cascades, hidden ones included, so any of them holding a movement is
    one the database now refuses to lose.  Dated or not: an un-dated
    purchase is money in flight, as recorded as a dated one.  A transfer is
    asked the ONE ``transfer_legs`` question (rulings **R-BAL125**,
    **R-BAL157**), which reaches a DEAD shadow's kept payment too (finding
    **BAL-532**); the cascade takes those.

    The pay-period doors' one reading of it: the lock classifier's
    ``HOLDS_MOVEMENT`` (per period), the reset gate's second count
    (``pay_period_gates.movement_holding_row_count``) and "Remove earlier
    paychecks"' refusal naming each item (``pay_period_gates``,
    ruling **R-PC115**).

    Args:
        periods: The pay-period ids to look in -- a list, or a query of
            ids (the reset gate's: every period of one owner).

    Returns:
        :func:`_items`' pair of queries.
    """
    return _items(
        periods,
        rows=(archive_helpers.holds_a_movement(),),
        transfers=(transfer_legs.transfer_holds_a_movement(),),
    )


def settled_items(periods: "list[int] | Query") -> "tuple[Query, Query]":
    """Return the non-deleted SETTLED items filed in *periods*.

    "Settled" is the canonical ``balance_predicates.settled_status_ids``
    (Paid or Received), asked of a row's status and of a transfer's OWN
    status (plan step ``balance:X-bi-6-4a-3``): a transfer's legs carry its
    status (Transfer Invariant 3), and ``X-bi-6``'s end is status in one
    row.  The lock classifier's ``SETTLED_TXN`` and the reset gate's first
    count (``pay_period_gates.settled_transaction_count``) both read this,
    so a row that does not lock a period cannot block a reset.

    **On the drift Transfer Invariant 3 forbids, the parent decides**
    (ruling **R-JM**): a settled parent over Projected shadows is settled
    here, where the shadow read said it was not, and a Projected parent
    over a settled shadow is not -- though any movement that shadow holds
    still holds its period, through :func:`items_holding_a_movement`.  No
    door writes either state; production held neither on the 2026-09-30
    00:11 dump (0 shadows whose status differs from their parent's).

    Args:
        periods: As :func:`items_holding_a_movement`.

    Returns:
        :func:`_items`' pair of queries.
    """
    settled = settled_status_ids()
    return _items(
        periods,
        rows=(
            Transaction.status_id.in_(settled),
            Transaction.is_deleted.is_(False),
        ),
        transfers=(
            Transfer.status_id.in_(settled),
            Transfer.is_deleted.is_(False),
        ),
    )


def _items(
    periods: "list[int] | Query",
    *,
    rows: "tuple[ColumnElement, ...]",
    transfers: "tuple[ColumnElement, ...]",
) -> "tuple[Query, Query]":
    """Return ``(rows, transfers)``: one query per KIND of item, each yielding its period.

    **An item is what the owner sees in a pay period**: a plan row, or a
    TRANSFER once for its two legs.  A shadow row is never one -- it is its
    transfer's -- so the row query excludes them (``transfer_id IS NULL``,
    the discard gate's idiom) and the transfer query asks the transfer.
    Each query yields one ``(pay_period_id,)`` per item, so a caller reads
    the set of periods (:func:`_period_ids_of`) or counts the items
    (``Query.count``); one that needs more of each item swaps the columns
    with ``with_entities``, keeping the scope.

    **One premise the DOORS hold and no key does** (the reason
    :func:`settled_items` gives for status drift, for the period a shadow
    mirrors; the account a shadow mirrors is
    ``archive_helpers.account_holding_movements``' premise).  A shadow's
    ``pay_period_id`` is its transfer's (Transfer Invariant 3:
    ``transfer_service`` moves both together, a restore re-aligns them,
    carry-forward's bulk moves exclude shadows), so the row query's
    ``transfer_id IS NULL`` drops nothing the transfer query does not ask.
    On the PERIOD drift no door writes -- a shadow filed in period B while
    its transfer is in period A -- each read misses one of the two: the
    shadow read locked B and missed A, whose delete takes the transfer and
    its shadows by cascade; this one locks A and misses B.  Deleting the
    missed period then, at worst, meets
    ``fk_transaction_entries_transaction_id``'s refusal (an error page,
    nothing lost) instead of this designed one.  0 of 358 shadows on the
    2026-09-30 00:11 production dump.

    Args:
        periods: The pay-period ids to look in -- a list or a query of ids.
        rows: Clauses over ``Transaction`` selecting the row items.
        transfers: Clauses over ``Transfer`` selecting the transfer items.

    Returns:
        ``(row query, transfer query)``, unexecuted.
    """
    return (
        db.session.query(Transaction.pay_period_id).filter(
            Transaction.pay_period_id.in_(periods),
            Transaction.transfer_id.is_(None),
            *rows,
        ),
        db.session.query(Transfer.pay_period_id).filter(
            Transfer.pay_period_id.in_(periods), *transfers,
        ),
    )


def _period_ids_of(items: "tuple[Query, Query]") -> set[int]:
    """Return the periods holding any of *items* (a pair from :func:`_items`)."""
    rows, transfers = items
    return {period_id for (period_id,) in rows.union(transfers)}


def _period_ids_with_unbalanced_ledger(period_ids: list[int]) -> set[int]:
    """Return the ``period_ids`` whose entries do NOT net to zero per ledger.

    The double-entry gate of the lock classifier (the 2026-07-02 adversarial
    review's R2 defense-in-depth): ``journal_entries.pay_period_id`` is
    ``ON DELETE CASCADE``, so deleting a period disposes its entries and legs
    at the DB tier -- outside the ORM, where the balanced-journal trigger
    never fires on DELETE.  That disposal is safe ONLY when the period's
    postings net to zero per ledger account (e.g. an original + its reversal,
    which the R2 attribution rule keeps in one period): the cascade then
    removes a self-cancelling pair and no account's sum moves.  A period
    whose postings carry a NON-zero per-account net -- a loan opening /
    true-up correction, or any attribution drift -- holds posted financial
    state a cascade would silently mis-state, so it hard-locks.

    A period holding a settled transaction is already locked upstream
    (``SETTLED_TXN`` precedence); this catches the posted state settled-row
    counting cannot see.

    Args:
        period_ids: The pay-period ids being classified.

    Returns:
        The subset whose postings have a non-zero net on any ledger account.
    """
    rows = (
        db.session.query(JournalEntry.pay_period_id)
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(JournalEntry.pay_period_id.in_(period_ids))
        .group_by(JournalEntry.pay_period_id, Posting.ledger_account_id)
        .having(db.func.sum(Posting.amount) != 0)
        .all()
    )
    return {row[0] for row in rows}


def posted_totals(user_id: int) -> "dict[tuple[int, int], Decimal]":
    """Return the owner's posted total per ``(scenario_id, ledger_account_id)``.

    **What "Remove earlier paychecks" must leave unchanged** (plan step
    ``pay_calendar:C21``, ruling **R-PC114**, amending **R-PC109**).  That
    door deletes paychecks whose journal entries the ``CASCADE`` takes, then
    re-files what the posted ledger rebuilds from the owner's records (loan
    and account openings, true-ups) through Reset's two re-syncs; it asks
    this before and after, and is refused -- rolled back -- if any total
    moved.  Grouped by SCENARIO as well as ledger account, because journal
    entries are scenario-scoped and a move in one scenario must not be
    cancelled by another's.  It lives here because this is where a period's
    ledger is read: the ledger-model fence (W9908) admits this module and
    not the gates.

    Args:
        user_id: The owning user's id.

    Returns:
        The summed ``account_postings.amount`` per key, for every key the
        owner's entries touch.
    """
    return {
        (scenario_id, ledger_account_id): total
        for scenario_id, ledger_account_id, total in (
            db.session.query(
                JournalEntry.scenario_id, Posting.ledger_account_id,
                db.func.sum(Posting.amount),
            )
            .join(Posting, Posting.journal_entry_id == JournalEntry.id)
            .filter(JournalEntry.user_id == user_id)
            .group_by(JournalEntry.scenario_id, Posting.ledger_account_id)
            .all()
        )
    }


def ledger_account_names(ledger_account_ids) -> "list[str]":
    """Return the names a refusal gives *ledger_account_ids*, distinct and sorted.

    A ledger account paired with one of the owner's accounts is named as that
    account, because the owner knows "Checking" and not its chart rows; one
    paired with none is named as itself.

    Args:
        ledger_account_ids: ``budget.ledger_accounts.id`` values.

    Returns:
        The names, each once, alphabetical.
    """
    rows = (
        db.session.query(LedgerAccount.name, Account.name)
        .outerjoin(Account, Account.id == LedgerAccount.account_id)
        .filter(LedgerAccount.id.in_(list(ledger_account_ids)))
        .all()
    )
    return sorted({account or ledger for ledger, account in rows if account or ledger})
