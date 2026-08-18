"""Account-anchor genesis walk: the DAY-granular correction producer.

The pure READ/COMPUTE half of the Build-Order Step 5 write side: one
chronological walk (:func:`walk_account_ledger`) replays a NON-loan account's
anchor assertions -- its :class:`~app.models.account.AccountAnchorHistory`
rows -- against the settled source facts already posted on its linked ledger,
and produces the balanced OPENING / TRUE-UP corrections that drive the
ledger's running total to each asserted balance at its assertion moment.  The
reconcile / posting of those corrections lives in the sibling module
(:mod:`._anchors`); this module only reads and computes (no writes, no
commit).

**A RECORDED clearing fact where there is one, and the civil DAY where there is
not** (ruling **R-FL**, plan step X-f3a-1).  A source names the
``account_anchor_history`` row whose statement showed it, and that assertion is
the one that absorbs it.  Where none is recorded, an anchor is the CLOSING
BALANCE for the civil day it was observed on, so it already reflects every
settled movement dated that day or earlier (``apply_anchor_true_up``: "the user
is declaring 'my real checking is now $X' -- every past-dated debit purchase is
already in that number").  Never by pay period -- a period-granular walk would
mis-state the ledger by every pre-true-up settle in the anchor period (the plan
review's CRITICAL-1) -- and never by instant, which decided the question by
which button the user pressed first and cost production ``$4,001.42`` on
2026-07-31 (ruling R-DH,
``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  The
DAY rule survives as the answer for an UNKNOWN source rather than as the rule,
because the developer's own bank exports measured it: only 33 of 110 matched
movements carry the day the bank posted them.

**One boundary for both anchor kinds**, opening and true-up alike (finding
N-133 / F1).  The OPENING was excepted for one day -- its boundary stepped back
a day so its own day's sources rode on top -- and the exception was reverted
once scored: it made the walk read ``$4,804.00`` for a day production's bank
showed ``$2,746.58``, and it forced the rule to be stated twice by hand, as a
date boundary here and as a sort position in the read fold.

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
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.posting_reads import PostingError, _ledger_account_for
from app.services.cash_ledger import (
    CashAnchorFact,
    cash_anchor_facts,
    statement_coverage,
)
from app.utils.balance_predicates import settled_day

_ZERO_MONEY = Decimal("0.00")


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


@dataclass(frozen=True)
class _PostedSource:
    """One source's posted effect on a linked ledger, with what cleared it.

    The four source loaders' shared return shape, and it is a RECORD rather
    than the ``(day, net)`` tuple it replaced because the walk now needs a third
    fact: which statement was recorded as showing this money (ruling **R-FL**).
    It satisfies :class:`app.services.cash_ledger.ClearableLine`, so
    :class:`~app.services.cash_ledger.StatementCoverage` is asked of it directly
    -- the same rule, over the same two fields, that the read replay asks of a
    :class:`~app.services.cash_ledger.CashSourceFact`.

    Attributes:
        settled_on: The civil day this source's cash moved -- a transaction's, a
            transfer shadow's or a PURCHASE's stored ``settled_on``, and for
            RESIDUE the period's ``start_date``.
        reconciled_by_id: The ``account_anchor_history`` row whose statement was
            recorded as showing it, or ``None``.  **Residue always answers
            ``None``, structurally**: its journal entries carry every source FK
            NULL by construction, so there is no row to read a link from.  That
            is not a gap -- an unlinked source is UNKNOWN, and the clearing rule
            answers UNKNOWN from the date exactly as this walk always has.
            Measured on a 2026-08-14 production clone: every nonzero residue
            group belongs to a LOAN account, which
            :func:`walk_account_ledger` refuses outright, so the bucket is empty
            for every account this walk actually runs.
        net: The source's current posted net on the linked ledger.
    """

    settled_on: date
    reconciled_by_id: "int | None"
    net: Decimal


def _linked_net_rows(
    linked_ledger_id: int, scenario_id: int, group_column, extra_filters: list,
) -> list:
    """Return ``(group_key, net)`` sums over one linked ledger's postings.

    The shared query shape of the four source loaders: sum
    ``account_postings.amount`` over the linked ledger's legs in
    *scenario_id*, grouped by the caller's source-identity column
    (``transaction_id``, ``transfer_id``, ``transaction_entry_id``, or the
    residue period start).

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
) -> list[_PostedSource]:
    """Return one :class:`_PostedSource` per transaction-linked source.

    Groups the linked ledger's postings under transaction-linked journal
    entries by ``transaction_id`` (any source kind -- the ``transaction``
    entries Step 3 posts today, and any future transaction-linked kind, by
    construction) and attributes each nonzero net to the civil day the
    transaction's cash moved -- the STORED ``transactions.settled_on``, read
    through the shared :func:`app.utils.balance_predicates.settled_day`, so this
    walk and the read fold partition on one FACT rather than on two derivations
    that agree (ruling R-DH, and plan step X-f1 which removed the derivation).
    A reverted source nets to zero and is dropped before any date is
    resolved.  The pay-period join this used to carry is gone with the
    NULL-instant fallback it fed.

    The ``transfer_id IS NULL`` and ``transaction_entry_id IS NULL`` filters
    make the FOUR source loaders a provable PARTITION of the linked ledger: no
    writer produces a multi-linked entry today (the docstring convention in
    :mod:`app.models.journal_entry`), but nothing at the storage tier forbids
    one, and without the filters such an entry would be summed into two loaders
    -- double-counted in ``ledger_before``.  A multi-linked entry classifies as
    transfer-linked first and purchase-linked second (the plan's
    reader-contract C-3 ordering, extended at plan step X-f3b).

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources, unordered (the walk sorts the merged set).

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
                JournalEntry.transaction_entry_id.is_(None),
            ],
        )
        if net != 0
    }
    if not nets:
        return []
    # The clearing link comes off the SAME row the day does, in the same read:
    # two queries for two fields of one fact is how the two come to describe
    # different rows.
    dated = (
        db.session.query(
            Transaction.id, Transaction.settled_on,
            Transaction.reconciled_by_id,
        )
        .filter(Transaction.id.in_(nets))
        .all()
    )
    facts = {
        transaction_id: (
            settled_day(transaction_id, stored_day), reconciled_by_id,
        )
        for transaction_id, stored_day, reconciled_by_id in dated
    }
    missing = set(nets) - set(facts)
    if missing:
        raise PostingError(
            f"Ledger account {linked_ledger_id} holds a nonzero net for "
            f"transaction ids {sorted(missing)} but no such transaction "
            f"rows exist; the SET NULL linkage invariant is broken."
        )
    return [
        _PostedSource(
            settled_on=facts[key][0],
            reconciled_by_id=facts[key][1],
            net=nets[key],
        )
        for key in nets
    ]


def _transfer_source_days(
    account_id: int, linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return one :class:`_PostedSource` per transfer-linked source.

    Groups the linked ledger's postings under transfer-linked journal
    entries by ``transfer_id`` and attributes each nonzero net to the shadow
    **on the account being walked**, through the SHARED
    :func:`app.utils.balance_predicates.settled_day` -- which REFUSES a shadow
    carrying no day rather than falling back to its pay-period start, because
    the day is a stored fact now and its absence is a broken invariant.  A
    reverted or reversed-before-delete transfer nets to zero and is dropped
    before the shadow is resolved, so only a shadow with live posted effect is
    ever dated.  The shadow's own id is passed to the accessor (not the
    transfer's) so a refusal names the row that is actually broken.

    **It read the INCOME shadow until plan step X-f3a-1, whichever account was
    being walked, and that was safe for the DAY and is not safe for the LINK.**
    Transfer Invariant 3 mirrors ``settled_on`` onto both shadows, so the day is
    identical either way and nothing moves by this change (``_entry_date``
    dates transfer entries off the income shadow for the same reason).  Clearing
    is NOT mirrored and must not be: a transfer leaves one account and arrives
    at another, so the checking statement shows the outgoing leg and the savings
    statement shows the incoming one, and they clear on different days or not at
    all.  Reading the income shadow's link while walking the FROM account's
    ledger would attribute the other account's statement to this account's
    money.

    Args:
        account_id: The account being walked -- which of the transfer's two
            shadows is this ledger's.
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources, unordered (the walk sorts the merged set).

    Raises:
        PostingError: If a nonzero net's transfer has no active shadow on this
            account, or has MORE than one.  A transfer with a live posted
            effect is settled, and a settled transfer has exactly its two
            shadows (Transfer Invariant 1); a miss or a duplicate is a
            broken invariant that must fail loudly rather than silently
            mis-partition real money on an arbitrary shadow's ``settled_on``.
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
            Transaction.transfer_id, Transaction.id, Transaction.settled_on,
            Transaction.reconciled_by_id,
        )
        .filter(
            Transaction.transfer_id.in_(nets),
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )
    facts = {
        transfer_id: (settled_day(shadow_id, stored_day), reconciled_by_id)
        for transfer_id, shadow_id, stored_day, reconciled_by_id in dated
    }
    if len(dated) != len(facts):
        raise PostingError(
            f"Ledger account {linked_ledger_id} resolved more than one "
            f"active shadow on account {account_id} for a transfer; Transfer "
            f"Invariant 1 is broken and the attribution day would be arbitrary."
        )
    missing = set(nets) - set(facts)
    if missing:
        raise PostingError(
            f"Ledger account {linked_ledger_id} holds a nonzero net for "
            f"transfer ids {sorted(missing)} but no active shadow on account "
            f"{account_id} resolves them; Transfer Invariant 1 is broken."
        )
    return [
        _PostedSource(
            settled_on=facts[key][0],
            reconciled_by_id=facts[key][1],
            net=nets[key],
        )
        for key in nets
    ]


def _purchase_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return one :class:`_PostedSource` per purchase-linked source.

    The fourth partition (ruling **R-FM**, plan step X-f3b): a PURCHASE whose
    bank posting day the owner recorded books its own cash leg, so the walk must
    read that leg's own day and its own clearing link.  Groups the linked
    ledger's postings under purchase-linked journal entries by
    ``transaction_entry_id`` and attributes each nonzero net to the STORED
    ``transaction_entries.settled_on``.

    **This is the loader the linkage exists for.**  Grouping a purchase's legs
    under its parent's ``transaction_id`` instead would date them at
    ``transactions.settled_on`` -- a day a still-projected envelope does not
    have at all -- and would read the PARENT's ``reconciled_by_id``, attributing
    whichever statement showed the envelope's close to money that left the bank
    days earlier.  Both facts are per-purchase, so the source is per-purchase.

    Unlike its transaction twin this reads no shared ``settled_day`` accessor:
    that one REFUSES a settled row carrying no day, because a settled
    transaction with no day is a broken invariant.  A purchase with no day is an
    ordinary outstanding purchase -- it simply posts nothing, so a nonzero net
    against one is the broken state, and it is refused below with the missing-
    linkage case rather than dated by any fallback.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources, unordered (the walk sorts the merged set).

    Raises:
        PostingError: If a nonzero net's ``transaction_entry_id`` resolves no
            purchase row, or resolves one carrying no ``settled_on``.  The FK is
            ON DELETE SET NULL, so a live id always resolves; a miss means the
            reverse-before-delete discipline was violated
            (``posting_service.reverse_purchase_postings_before_delete``), and a
            dayless resolution means a leg was posted for a purchase that has
            not been seen to move.  Either must fail loudly rather than silently
            mis-partition real money.
    """
    nets = {
        entry_id: net
        for entry_id, net in _linked_net_rows(
            linked_ledger_id, scenario_id, JournalEntry.transaction_entry_id,
            [
                JournalEntry.transaction_entry_id.isnot(None),
                # The exclusion that makes the FOUR loaders a partition rather
                # than three plus an overlap: no writer produces a multi-linked
                # entry (``source_entry_builder`` sets exactly one FK), and
                # nothing at the storage tier forbids one, so without this a
                # transfer-linked entry carrying a purchase id would be summed
                # by this loader AND the transfer loader -- double-counted into
                # ``ledger_before``.  Transfer-linked wins, which is the
                # reader-contract C-3 ordering the sibling loader states.
                JournalEntry.transfer_id.is_(None),
            ],
        )
        if net != 0
    }
    if not nets:
        return []
    # The clearing link comes off the SAME row the day does, in the same read:
    # two queries for two fields of one fact is how the two come to describe
    # different rows.
    dated = (
        db.session.query(
            TransactionEntry.id, TransactionEntry.settled_on,
            TransactionEntry.reconciled_by_id,
        )
        .filter(TransactionEntry.id.in_(nets))
        .all()
    )
    facts = {
        entry_id: (settled_on, reconciled_by_id)
        for entry_id, settled_on, reconciled_by_id in dated
        if settled_on is not None
    }
    missing = set(nets) - set(facts)
    if missing:
        raise PostingError(
            f"Ledger account {linked_ledger_id} holds a nonzero net for "
            f"purchase ids {sorted(missing)} that either no longer exist or "
            f"carry no recorded posting day; a purchase's legs are reversed "
            f"before it is deleted and are only ever written for a purchase "
            f"that has one, so the linkage invariant is broken."
        )
    return [
        _PostedSource(
            settled_on=facts[key][0],
            reconciled_by_id=facts[key][1],
            net=nets[key],
        )
        for key in nets
    ]


def _residue_source_days(
    linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return one :class:`_PostedSource` for each unlinked non-correction group.

    The residue bucket: journal entries on the linked ledger with ALL THREE
    concrete source FKs NULL and a non-correction source kind -- in
    practice hard-delete residue, whose ``transaction_id`` / ``transfer_id`` /
    ``transaction_entry_id`` were SET-NULLed when the source row was deleted.
    Each
    period's residue is attributed at that period's ``start_date`` -- a civil
    date used AS a civil date, never routed through an instant, which is the
    same discipline the settled-source loaders keep (ruling R-DH):
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

    **Residue can carry NO clearing link, structurally**, because every source
    FK is NULL by construction -- there is no row to read one from.  It is
    therefore always UNKNOWN, and the clearing rule answers an UNKNOWN source
    from its date, which is exactly what this loader has always supplied
    (ruling **R-FL**; the ruling's own amendment names this loader as the third
    reason a link could not simply REPLACE the date rule).  Measured on a
    2026-08-14 production clone: every nonzero residue group belongs to a LOAN
    account, which :func:`walk_account_ledger` refuses outright, so the bucket
    is empty for every account this walk actually runs.

    Args:
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources, unordered (the walk sorts the merged set); empty whenever
        the reverse-before-delete discipline held.
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
                JournalEntry.transaction_entry_id.is_(None),
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
        _PostedSource(
            settled_on=start_date,
            reconciled_by_id=None,
            net=nets[pay_period_id],
        )
        for pay_period_id, start_date in dated
    ]


def _source_net_days(
    account_id: int, linked_ledger_id: int, scenario_id: int,
) -> list[_PostedSource]:
    """Return every source fact on one linked ledger, sorted by day.

    The union of the FOUR source partitions -- transaction-linked,
    transfer-linked, purchase-linked (ruling **R-FM**, plan step X-f3b) and
    residue -- covering every posting on the linked
    ledger except the account's own anchor corrections, each as a
    :class:`_PostedSource`.  Sorted ascending by day, which is no longer what
    decides which assertion absorbs what (that is the clearing rule's, ruling
    **R-FL**) but keeps the walk's own iteration reproducible.

    Args:
        account_id: The account being walked -- which of a transfer's two
            shadows is this ledger's.
        linked_ledger_id: The account's LINKED ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        The sources ascending by day (nets are nonzero).
    """
    sources = (
        _transaction_source_days(linked_ledger_id, scenario_id)
        + _transfer_source_days(account_id, linked_ledger_id, scenario_id)
        + _purchase_source_days(linked_ledger_id, scenario_id)
        + _residue_source_days(linked_ledger_id, scenario_id)
    )
    sources.sort(key=lambda source: source.settled_on)
    return sources


def walk_account_ledger(
    account_id: int, scenario_id: int,
) -> list[AccountAnchorCorrection]:
    """Replay a non-loan account's anchors into its genesis corrections.

    The single DAY-granular walk the account anchor ledger derives from.
    Seeds the running ledger total at zero and, per anchor fact in
    assertion order, absorbs every source that assertion CLEARED -- opening and
    true-up alike, one rule (finding N-133 / F1) -- records the correction with
    the total JUST BEFORE the assertion (``ledger_before``), then resets the
    running total to the asserted balance -- so the corrections' cumulative
    effect plus the cleared sources equals each asserted balance as of the day
    it is the closing balance for, and the account's ABSOLUTE ledger total
    equals the latest anchor plus the later source nets.

    **The partition is the READ FOLD's, and it is the same rule rather than
    a copy** (ruling R-DH, re-pointed at ruling R-FL).  Both ask
    :class:`~app.services.cash_ledger.StatementCoverage` which assertion cleared
    a source, so the posted ledger and the projection cannot disagree about
    which settles an assertion already covers -- the divergence Phase X exists
    to close.  Both were INSTANT-granular until 2026-07-31, which decided that
    question by click order and cost production ``$4,001.42``
    (``anchor_settle_partition.md``); both then compared two of the app's own
    DATES, which the developer's bank exports falsified on 70% of matched
    movements; both now read the RECORDED fact and fall back to the date rule
    only where none exists.  Moving one without the other is what would break
    the equality plan step X-a established, so they have moved together each
    time.

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
    # BUSINESS-DATE order, guaranteed by the loader rather than restated here
    # (finding N-133 / F6).  It is what ``statement_coverage`` bisects for a
    # source no statement has been recorded as showing, and what makes "the
    # FIRST is the opening" true.  That held for free while ``observed_on`` was
    # DERIVED from ``created_at``; plan step 2 made it user-supplied, and the
    # fix belongs in :func:`~app.services.cash_ledger.cash_anchor_facts` -- one
    # order, stated where the rows are read, rather than a re-sort per consumer.
    facts = cash_anchor_facts(account_id)
    if not facts:
        return []
    linked = _ledger_account_for(account_id)
    sources = _source_net_days(account_id, linked.id, scenario_id)

    # Which assertion CLEARED each source -- opening and true-up alike, because
    # an assertion is the CLOSING BALANCE for its day (ruling R-DH (a)) and a
    # line it was recorded as showing is inside it whatever the dates say
    # (ruling R-FL).  ``StatementCoverage`` is the ONE implementation of that
    # rule, and this grouping is deliberately the same shape as the read
    # replay's in :func:`app.services.cash_ledger.walk_cash_ledger`, over the
    # same assertions.  It was a MONOTONIC POINTER over day-sorted sources until
    # plan step X-f3a-1, which was correct only while "cleared by this
    # assertion" was monotone in the day -- a recorded clearing fact is not, and
    # a pointer meeting an out-of-order one halts and silently shorts every
    # later assertion.  Before that it restated the rule as a bare ``<=``, and
    # while the OPENING also carried an exception the two walks had to be
    # hand-mirrored and held in step by convention (finding N-133 / F1); a
    # posting walk that absorbs what the fold rides on top of is the exact drift
    # plan step X-a exists to make impossible.
    coverage = statement_coverage(facts)
    cleared: dict[int, Decimal] = {}
    for source in sources:
        anchor_id = coverage.clearing_anchor_id(source)
        if anchor_id is not None:
            cleared[anchor_id] = cleared.get(anchor_id, _ZERO_MONEY) + source.net

    corrections: list[AccountAnchorCorrection] = []
    running = _ZERO_MONEY
    for fact in facts:
        running += cleared.get(fact.anchor_id, _ZERO_MONEY)
        corrections.append(
            AccountAnchorCorrection(anchor=fact, ledger_before=running)
        )
        # The correction resets the walked total to the asserted balance
        # (the closing-balance reset, the account analogue of the loan
        # walk's anchor reset).
        running = fact.anchor_balance
    return corrections
