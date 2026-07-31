"""Account-anchor genesis walk: the moment-granular correction producer.

The pure READ/COMPUTE half of the Build-Order Step 5 write side: one
chronological walk (:func:`walk_account_ledger`) replays a NON-loan account's
anchor assertions -- its :class:`~app.models.account.AccountAnchorHistory`
rows -- against the settled source facts already posted on its linked ledger,
and produces the balanced OPENING / TRUE-UP corrections that drive the
ledger's running total to each asserted balance at its assertion moment.  The
reconcile / posting of those corrections lives in the sibling module
(:mod:`._anchors`); this module only reads and computes (no writes, no
commit).

**Day-granular, not period-granular -- and not instant-granular either.**  An
anchor is the CLOSING BALANCE for the civil day it was observed on, so it
already reflects every settled movement dated that day or earlier
(``apply_anchor_true_up``: "the user is declaring 'my real checking is now $X'
-- every past-dated debit purchase is already in that number").  The walk
therefore partitions source facts by their settled DAY against each anchor's
``observed_on``, never by pay period -- a period-granular walk would mis-state
the ledger by every pre-true-up settle in the anchor period (the plan review's
CRITICAL-1) -- and never by instant, which decided the question by which button
the user pressed first and cost production ``$4,001.42`` on 2026-07-31 (ruling
R-DH, ``docs/audits/balance_architecture/anchor_settle_partition.md``).

**Source facts are read back from the ledger, never re-derived from
transaction rows.**  Grouping the linked ledger's own postings by source is
timing-proof against the reverse-before-delete discipline (a reverted source
nets to zero and drops out regardless of partition) and future-proof for new
source kinds (any transaction- or transfer-linked entry is attributed by its
linkage, not by an enumerated source-kind list).

The loan analogue is :mod:`app.services.loan_ledger`; loans
never walk here (their anchors are dated ``LoanAnchorEvent`` facts replayed
with amortization, a different mechanism), and :func:`walk_account_ledger`
refuses an amortizing account outright.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.posting_reads import PostingError, _ledger_account_for
from app.services.cash_ledger import (
    CashAnchorFact,
    cash_anchor_facts,
    settled_civil_day,
)

_ZERO_MONEY = Decimal("0.00")
# The step back an OPENING's absorption boundary takes, so its own day's
# sources ride on top of it.  Spelled the same way as the cash fold's twin
# (``balance_at._cash_fold._ONE_DAY``) because it is the same unit: the
# civil day both partitions now read.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class AccountAnchorCorrection:
    """One anchor's balance correction: an opening or a true-up.

    The per-anchor result of :func:`walk_account_ledger`.  The correction's
    linked-ledger delta is ``anchor_balance - ledger_before`` (its equity
    leg the negative), so the two sum to zero and the linked ledger's total
    through the assertion instant lands exactly on the asserted balance.  A
    correction whose ``ledger_before`` already equals the anchor balance
    books nothing (a fresh $0 account mints no entries and no equity row).

    Attributes:
        anchor: The :class:`~app.services.cash_ledger.CashAnchorFact` this
            correction books for.
        ledger_before: The walked ledger total JUST BEFORE this assertion
            resets it -- the prior corrections' cumulative effect plus every
            source net attributed on or before this instant.
            ``Decimal("0.00")`` for the opening of an account with no
            pre-anchor settled history.
    """

    anchor: CashAnchorFact
    ledger_before: Decimal


def _linked_net_rows(
    linked_ledger_id: int, scenario_id: int, group_column, extra_filters: list,
) -> list:
    """Return ``(group_key, net)`` sums over one linked ledger's postings.

    The shared query shape of the three source loaders: sum
    ``account_postings.amount`` over the linked ledger's legs in
    *scenario_id*, grouped by the caller's source-identity column
    (``transaction_id``, ``transfer_id``, or the residue period start).

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.
        group_column: The column to group (and select) by.
        extra_filters: The caller's linkage filters (and, for the residue
            loader, the correction-source exclusion).

    Returns:
        ``(group_key, net Decimal)`` rows (zero-net groups included; the
        callers drop them).
    """
    return (
        db.session.query(group_column, db.func.sum(Posting.amount))
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
            *extra_filters,
        )
        .group_by(group_column)
        .all()
    )


def _transaction_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return (settled civil day, net) per transaction-linked source.

    Groups the linked ledger's postings under transaction-linked journal
    entries by ``transaction_id`` (any source kind -- the ``transaction``
    entries Step 3 posts today, and any future transaction-linked kind, by
    construction) and attributes each nonzero net to the civil day the
    transaction's cash moved -- the SHARED
    :func:`app.services.cash_ledger.settled_civil_day`, so this walk and the
    read fold partition on one rule rather than two that agree (ruling R-DH).
    A reverted source nets to zero and is dropped before any date is
    resolved.

    The ``transfer_id IS NULL`` filter makes the three source loaders a
    provable PARTITION of the linked ledger: no writer produces a
    dual-linked entry today (the docstring convention in
    :mod:`app.models.journal_entry`), but nothing at the storage tier
    forbids one, and without the filter such an entry would be summed into
    both this loader and the transfer loader -- double-counted in
    ``ledger_before``.  A dual-linked entry classifies as transfer-linked
    (the plan's reader-contract C-3 ordering).

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``(day, net)`` pairs, unordered (the walk sorts the merged set).

    Raises:
        PostingError: If a nonzero net's ``transaction_id`` resolves no
            transaction row.  ``journal_entries.transaction_id`` is ON
            DELETE SET NULL, so a live id always resolves; a miss is a
            broken linkage that must fail loudly rather than silently
            mis-partition real money.
    """
    nets = {
        transaction_id: net
        for transaction_id, net in _linked_net_rows(
            linked_ledger_id, scenario_id, JournalEntry.transaction_id,
            [
                JournalEntry.transaction_id.isnot(None),
                JournalEntry.transfer_id.is_(None),
            ],
        )
        if net != 0
    }
    if not nets:
        return []
    dated = (
        db.session.query(
            Transaction.id, Transaction.paid_at, PayPeriod.start_date,
        )
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(Transaction.id.in_(nets))
        .all()
    )
    days = {
        transaction_id: settled_civil_day(paid_at, start_date)
        for transaction_id, paid_at, start_date in dated
    }
    missing = set(nets) - set(days)
    if missing:
        raise PostingError(
            f"Ledger account {linked_ledger_id} holds a nonzero net for "
            f"transaction ids {sorted(missing)} but no such transaction "
            f"rows exist; the SET NULL linkage invariant is broken."
        )
    return [(days[key], nets[key]) for key in nets]


def _transfer_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return (settled civil day, net) per transfer-linked source.

    Groups the linked ledger's postings under transfer-linked journal
    entries by ``transfer_id`` and attributes each nonzero net to the
    transfer's INCOME shadow's CURRENT ``paid_at`` (Transfer Invariant 3
    mirrors ``paid_at`` onto both shadows, and
    ``posting_service._entry_date`` already dates transfer entries off
    exactly this shadow), through the SHARED
    :func:`app.services.cash_ledger.settled_civil_day` -- so it falls back to
    the shadow's pay period start UNCONVERTED (== the transfer's period,
    Invariant 3 again) when ``paid_at`` is NULL.  A reverted or
    reversed-before-delete transfer nets to zero and is dropped before the
    shadow is resolved.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``(day, net)`` pairs, unordered (the walk sorts the merged set).

    Raises:
        PostingError: If a nonzero net's transfer has no active income
            shadow, or has MORE than one.  A transfer with a live posted
            effect is settled, and a settled transfer has exactly its two
            shadows (Transfer Invariant 1); a miss or a duplicate is a
            broken invariant that must fail loudly rather than silently
            mis-partition real money on an arbitrary shadow's ``paid_at``.
    """
    nets = {
        transfer_id: net
        for transfer_id, net in _linked_net_rows(
            linked_ledger_id, scenario_id, JournalEntry.transfer_id,
            [JournalEntry.transfer_id.isnot(None)],
        )
        if net != 0
    }
    if not nets:
        return []
    dated = (
        db.session.query(
            Transaction.transfer_id, Transaction.paid_at,
            PayPeriod.start_date,
        )
        .join(
            Transfer,
            db.and_(
                Transaction.transfer_id == Transfer.id,
                Transaction.account_id == Transfer.to_account_id,
            ),
        )
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            Transaction.transfer_id.in_(nets),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )
    days = {
        transfer_id: settled_civil_day(paid_at, start_date)
        for transfer_id, paid_at, start_date in dated
    }
    if len(dated) != len(days):
        raise PostingError(
            f"Ledger account {linked_ledger_id} resolved more than one "
            f"active income shadow for a transfer; Transfer Invariant 1 is "
            f"broken and the attribution day would be arbitrary."
        )
    missing = set(nets) - set(days)
    if missing:
        raise PostingError(
            f"Ledger account {linked_ledger_id} holds a nonzero net for "
            f"transfer ids {sorted(missing)} but no active income shadow "
            f"resolves them; Transfer Invariant 1 is broken."
        )
    return [(days[key], nets[key]) for key in nets]


def _residue_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return (settled civil day, net) for unlinked non-correction entries.

    The residue bucket: journal entries on the linked ledger with BOTH
    concrete source FKs NULL and a non-correction source kind -- in
    practice hard-delete residue, whose ``transaction_id`` /
    ``transfer_id`` were SET-NULLed when the source row was deleted.  Each
    period's residue is attributed at that period's ``start_date`` -- a civil
    date used AS a civil date, never routed through an instant, which is the
    same discipline :func:`app.services.cash_ledger.settled_civil_day` keeps
    for its own NULL-``paid_at`` fallback (ruling R-DH):
    the reverse-before-delete discipline nets residue to zero per account
    AND per period (R2 stamps a reversal into the period of the postings
    it reverses), so every group here sums to zero and is dropped -- but
    reading it keeps the walk's ``ledger_before`` equal to the live linked
    total even if that discipline is ever violated, instead of silently
    diverging from what the true-up chokepoint reconciled against.

    The account's OWN ``account_opening`` / ``account_trueup`` corrections
    are excluded -- they are the walk's OUTPUT, not source facts (the loan
    correction kinds never appear on a non-loan linked ledger: the chart
    resolvers guarantee loan and non-loan corrections book onto disjoint
    accounts).

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``(day, net)`` pairs, unordered (the walk sorts the merged set);
        empty whenever the reverse-before-delete discipline held.
    """
    excluded_source_ids = [
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
    ]
    nets = {
        pay_period_id: net
        for pay_period_id, net in _linked_net_rows(
            linked_ledger_id, scenario_id, JournalEntry.pay_period_id,
            [
                JournalEntry.transaction_id.is_(None),
                JournalEntry.transfer_id.is_(None),
                JournalEntry.source_kind_id.notin_(excluded_source_ids),
            ],
        )
        if net != 0
    }
    if not nets:
        return []
    # Two-step like the sibling loaders: group by the entry's own NOT NULL
    # pay_period_id (no extra join in the grouped query), then batch-load
    # the period starts -- the FK guarantees every id resolves.
    dated = (
        db.session.query(PayPeriod.id, PayPeriod.start_date)
        .filter(PayPeriod.id.in_(nets))
        .all()
    )
    return [
        (start_date, nets[pay_period_id])
        for pay_period_id, start_date in dated
    ]


def _source_net_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return every source fact on one linked ledger, sorted by day.

    The union of the three source partitions -- transaction-linked,
    transfer-linked, and residue -- covering every posting on the linked
    ledger except the account's own anchor corrections, each as a
    ``(settled civil day, current net)`` fact.  Sorted ascending by day, the
    order the walk consumes them in.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``(day, net)`` pairs ascending by day (nets are nonzero).
    """
    sources = (
        _transaction_source_days(linked_ledger_id, scenario_id)
        + _transfer_source_days(linked_ledger_id, scenario_id)
        + _residue_source_days(linked_ledger_id, scenario_id)
    )
    sources.sort(key=lambda source: source[0])
    return sources


def walk_account_ledger(
    account_id: int, scenario_id: int,
) -> list[AccountAnchorCorrection]:
    """Replay a non-loan account's anchors into its genesis corrections.

    The single DAY-granular walk the account anchor ledger derives from.
    Seeds the running ledger total at zero and, per anchor fact in
    assertion order, absorbs every source fact dated on or before that
    fact's ``observed_on``, records the correction with the total JUST
    BEFORE the assertion (``ledger_before``), then resets the running total
    to the asserted balance -- so the corrections' cumulative effect plus
    the absorbed sources equals each asserted balance as of the day it is
    the closing balance for, and the account's ABSOLUTE ledger total equals
    the latest anchor plus the later source nets.

    **The partition is the READ FOLD's, and it is the same rule rather than
    a copy** (ruling R-DH).  Both consume
    :func:`app.services.cash_ledger.settled_civil_day` for a source's day and
    :attr:`~app.services.cash_ledger.CashAnchorFact.observed_on` for an
    assertion's, so the posted ledger and the projection cannot disagree
    about which settles an assertion already covers -- the divergence Phase X
    exists to close.  Both were INSTANT-granular until 2026-07-31, which
    decided that question by click order and cost production ``$4,001.42``
    (``anchor_settle_partition.md``); moving one without the other is what
    would break the equality plan step X-a established, so they moved
    together.

    Why this reproduces the go-forward chokepoint after the fact: at a
    true-up the fresh delta equals ``asserted - current live linked total``
    (every settled source's day is on or before the assertion's), and
    ``created_at`` and the posted ledger are immutable, so the pure walk
    re-derives the same deltas.  Pre-anchor settles in ANY period are
    absorbed by the opening / true-up deltas (the DAY partition, not a period
    one); later settles ride on top; a source reverted after a true-up nets
    to zero and self-heals to the engine's answer on the next reconcile.

    Reads only (no writes, no commit).  All-scenario anchors, per-scenario
    sources: anchor history is per-account, so the same facts walk in every
    scenario, against that scenario's own posted sources.

    Args:
        account_id: The non-loan account whose ledger to walk.
        scenario_id: The budget scenario whose posted sources to walk
            against.

    Returns:
        One :class:`AccountAnchorCorrection` per anchor fact,
        chronological.  Empty when the account row is missing or carries no
        anchor history (fixture-only states; production accounts always
        have both).

    Raises:
        ValueError: If the account is an amortizing loan.  Loans book their
            anchor corrections through the loan posting package onto their
            per-loan ``equity_opening`` account; walking one here would
            double-book its balance across two correction families (and
            break the loan oracle's bare-``account_id`` ledger helpers).
        PostingError: If the account has anchor history but no linked
            ledger account (a broken chart-of-accounts pairing), or a
            nonzero source net's linkage cannot be resolved (see the source
            loaders).
    """
    account = db.session.query(Account).filter_by(id=account_id).first()
    if account is None:
        return []
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot walk account anchors: account id={account_id} is an "
            f"amortizing loan (loans book their anchor corrections through "
            f"the loan posting package, never the account walk)"
        )
    facts = cash_anchor_facts(account_id)
    if not facts:
        return []
    linked = _ledger_account_for(account_id)
    sources = _source_net_days(linked.id, scenario_id)

    corrections: list[AccountAnchorCorrection] = []
    running = _ZERO_MONEY
    source_index = 0
    for fact in facts:
        # The LAST day whose sources this assertion absorbs.  An OPENING is
        # where tracking starts, so its own day is NOT inside it and the
        # boundary falls one day earlier; a TRUE-UP closes its day, so that day
        # is inside (ruling R-DH (a) as amended).  Stated as a date rather than
        # a predicate so the comparison below stays one ``<=`` for both kinds --
        # the boundary is the read fold's, name for name (``cash_ledger._events``
        # places the same two kinds either side of its own day's sources),
        # because a posting walk that absorbed what the fold rides on top of is
        # the exact drift plan step X-a exists to make impossible.
        last_absorbed = (
            fact.observed_on - _ONE_DAY if fact.is_opening
            else fact.observed_on
        )
        while (
            source_index < len(sources)
            and sources[source_index][0] <= last_absorbed
        ):
            running += sources[source_index][1]
            source_index += 1
        corrections.append(
            AccountAnchorCorrection(anchor=fact, ledger_before=running)
        )
        # The correction resets the walked total to the asserted balance
        # (the closing-balance reset, the account analogue of the loan
        # walk's anchor reset).
        running = fact.anchor_balance
    return corrections
