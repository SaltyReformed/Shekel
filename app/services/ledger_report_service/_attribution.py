"""The shared read core both confirmed-ledger statements consume (Step 5).

Two statements read the append-only posting ledger: an income statement
(:mod:`._income_statement`) and a balance sheet (:mod:`._balance_sheet`).  They
share this module so their attribution -- WHICH source date each posting lands
on -- is defined ONCE, which is what makes the statements articulate
automatically (the income net over a window equals the equity delta between the
bounding balance sheets, with no reconciliation code).

**The attribution rule (reader-contract C-3).**  Every posting is attributed by
its SOURCE, in whole: a source's per-ledger-account net lands on the source's
CURRENT settle day -- the stored ``transactions.settled_on``, which IS the user's
wall-clock civil day (the decided L9 rule -- tax-year and calendar figures follow
that clock, so an 8:05pm-Eastern Dec-31 settle attributes to Dec 31, not the
Jan 1 it becomes in UTC).  There is no fallback: a settled row missing its day is
refused by :func:`app.utils.balance_predicates.settled_day` rather than dated
from its pay period, which is what the derivation this replaced did:

* transaction-linked entries (``transaction`` and ``loan_payment`` sources, both
  carrying ``transaction_id`` with ``transfer_id`` NULL): by the transaction's
  ``settled_on`` -- for a loan payment, the loan-side income shadow it links;
* transfer-linked entries (``transfer_id`` set): by the transfer's INCOME
  shadow's ``settled_on`` (Transfer Invariant 3 mirrors the day onto both
  shadows, and ``posting_service._entry_date`` dates the entry off exactly this
  shadow), so a transfer's two legs land on one date;
* sourceless corrections (``loan_opening`` / ``loan_trueup`` / ``account_opening``
  / ``account_trueup``, both concrete FKs NULL): by the stored ``entry_date`` (a
  correction is an anchor fact dated by the anchor's observed civil day, and
  never had a settle day of its own);
* hard-delete residue (a ``transaction`` / ``transfer`` / ``loan_payment`` source
  whose concrete FK was SET-NULLed): DROPPED, as whole entries -- each sums to
  zero (the reverse-before-delete discipline), so dropping it leaves the trial
  balance closed.

The ``transfer_id IS NULL`` guard on the transaction bucket makes the three
buckets a PARTITION of the live ledger identical to the write-side walk's
(:func:`app.services.account_posting_service._walk._transaction_source_days`):
a hypothetical dual-linked entry classifies as transfer-linked, never both.

**One civil date, shared, since ruling R-DH.**  This reader and the write-side
walk both attribute a source to :func:`app.utils.balance_predicates.settled_day`
of its ``settled_on`` -- the same day, from the same accessor.  They were
deliberately different until 2026-07-31: the walk partitioned by UTC INSTANT
against each anchor's ``created_at`` while this side used the display-timezone
civil date for calendar windows, and the paragraph here justified an
independent restatement of the attribution rule on exactly that difference.
The difference is gone (the instant partition cost production ``$4,001.42``),
so what survives is a weaker and narrower reason: this package restates the
write side's LOADERS rather than importing a write-package internal, keeping
the reader decoupled from the writer the way the reconciliation oracles rely
on.  **That is a stance about package boundaries, not about the rule** -- the
DAY itself is one helper call on both sides, and plan step 3's one-predicate
sweep is where the two loaders stop being two.

**Flask-isolated** and read-only: plain ids in, plain data out; no writes.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingSourceEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.posting_reads import PostingError
from app.utils.balance_predicates import settled_day

from ._types import StatementLine, StatementSection

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class StatementClassIds:
    """The accounting-class ref ids the statements section and sign by.

    Resolved once per statement call (:func:`statement_class_ids`) so the
    readers compare a ledger account's ``class_id`` against integer ids -- the
    IDs-for-logic invariant -- rather than reading the string class name.  Used
    to place each account in its section, to derive the Income + Expense set
    the retained-earnings line closes, and to derive the Unrealized set the
    accumulated-unrealized line closes.

    Attributes:
        asset: The Asset class ref id.
        liability: The Liability class ref id.
        income: The Income class ref id.
        expense: The Expense class ref id.
        equity: The Equity class ref id.
        unrealized: The Unrealized (other comprehensive income) class ref id
            (ruling **R-FO**).  Its accounts appear BELOW the net-income line
            on the income statement and are folded into one derived Equity line
            on the balance sheet, so a price movement nobody sold into cash is
            never counted as earnings and the trial balance still closes.
    """

    asset: int
    liability: int
    income: int
    expense: int
    equity: int
    unrealized: int


def statement_class_ids() -> StatementClassIds:
    """Return the accounting-class ref ids as a :class:`StatementClassIds`.

    A one-shot resolve of the cached class ids the statements branch on, so
    each reader resolves them once (not per account) and compares by id.

    Returns:
        The populated :class:`StatementClassIds`.

    Raises:
        RuntimeError: If the ref cache is not initialized.
    """
    return StatementClassIds(
        asset=ref_cache.ledger_account_class_id(LedgerAccountClassEnum.ASSET),
        liability=ref_cache.ledger_account_class_id(
            LedgerAccountClassEnum.LIABILITY,
        ),
        income=ref_cache.ledger_account_class_id(LedgerAccountClassEnum.INCOME),
        expense=ref_cache.ledger_account_class_id(
            LedgerAccountClassEnum.EXPENSE,
        ),
        equity=ref_cache.ledger_account_class_id(LedgerAccountClassEnum.EQUITY),
        unrealized=ref_cache.ledger_account_class_id(
            LedgerAccountClassEnum.UNREALIZED,
        ),
    )


def present_natural(class_id: int, debit_net: Decimal) -> Decimal:
    """Return a debit-positive posting net as its natural-balance value (C-4).

    The reader-contract C-4 presentation rule: a debit-normal class (Asset,
    Expense) presents its debit-positive net as-is; a credit-normal class
    (Liability, Income, Equity, Unrealized) presents the NEGATED net, so a
    revenue, liability, equity or unrealized-change line reads positive when the
    account holds its natural balance -- and an unrealized LOSS therefore reads
    negative, which is the honest rendering of that account's contra position.
    The ledger is presented FAITHFULLY -- there is no ``-abs``
    normalization, so an owed-as-negative non-loan liability renders as a
    positive Liabilities line while a positively-anchored one would render
    negative (the stated non-loan liability sign rule).

    ``_ZERO_MONEY - debit_net`` (not ``-debit_net``) so a zero net presents as
    ``0.00``, never ``-0.00``.

    Args:
        class_id: The ledger account's ``ref.ledger_account_classes`` id.
        debit_net: The account's debit-positive posting net.

    Returns:
        The natural-balance value as a ``Decimal``.

    Raises:
        RuntimeError: If the ref cache is not initialized.
        KeyError: If *class_id* is not a known ledger-account-class id.
    """
    if ref_cache.ledger_class_is_debit_normal(class_id):
        return debit_net
    return _ZERO_MONEY - debit_net


def load_chart(user_id: int) -> dict[int, LedgerAccount]:
    """Return a user's chart of accounts keyed by ledger account id.

    Every :class:`~app.models.ledger_account.LedgerAccount` the user owns, so a
    reader resolves each net's class / kind / label without a per-account query.
    ``account`` is eager on the model (the linked-row display reads it);
    ``category`` is joined here so a live category row's ``display_name``
    resolves without a lazy load per line.

    Args:
        user_id: The owner whose chart to load.

    Returns:
        ``{ledger_account_id: LedgerAccount}`` over the user's whole chart.
    """
    rows = (
        db.session.query(LedgerAccount)
        .options(joinedload(LedgerAccount.category))
        .filter(LedgerAccount.user_id == user_id)
        .all()
    )
    return {row.id: row for row in rows}


def ledger_account_label(ledger_account: LedgerAccount) -> str:
    """Return a chart account's display label, branching on ``kind_id``.

    The reader-contract display rule, resolved by KIND (never by which FK is
    NULL): a linked row reads the live ``account.name``; a category row reads
    the live ``category.display_name`` (so a rename reflects) while its category
    exists, falling back to its own ``name`` snapshot once the category is
    deleted (``category_id`` SET NULL leaves ``kind_id`` unchanged, so a
    deleted-category row is a category-kind row with no live category); every
    other kind (fallback, per-loan interest / escrow / refund / opening, and
    the three per-account counter kinds -- anchor-equity, interest-income,
    unrealized-change) reads its ``name`` snapshot.  The snapshot -- not
    ``account.name`` -- is used for a per-account counter row even though it
    carries an ``account_id``, because the COALESCE display rule is the
    linked-row rule only.  That is why the fall-through is the DEFAULT arm
    rather than an enumeration: ruling R-FO's two new kinds needed no edit
    here, and a future counter kind will need none either.

    Args:
        ledger_account: The chart row to label (its ``account`` / ``category``
            relationships are loaded by :func:`load_chart`).

    Returns:
        The display label string.

    Raises:
        RuntimeError: If the ref cache is not initialized.
    """
    kind_id = ledger_account.kind_id
    if kind_id == ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED):
        return ledger_account.account.name
    if kind_id == ref_cache.ledger_account_kind_id(
        LedgerAccountKindEnum.CATEGORY,
    ):
        if ledger_account.category is not None:
            return ledger_account.category.display_name
        return ledger_account.name
    return ledger_account.name


def section_lines(
    nets: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_id: int,
) -> list[StatementLine]:
    """Return the natural-signed, label-sorted lines for one accounting class.

    The presentation step both statements share (extracted so the income
    statement's sections and the balance sheet's sections cannot drift): for
    every account of *class_id* with a nonzero net, one :class:`StatementLine`
    labeled by :func:`ledger_account_label` and signed by
    :func:`present_natural`, sorted by label.  Zero-net accounts are dropped
    (an account with no position in the window), so no zero line is emitted.

    Args:
        nets: ``{ledger_account_id: debit_net}`` (any classes; this filters to
            *class_id*).
        chart: The user's chart (:func:`load_chart`), supplying each account's
            class and label.
        class_id: The accounting class to build lines for.

    Returns:
        The class's :class:`StatementLine` list, sorted by label.
    """
    lines = [
        StatementLine(
            label=ledger_account_label(chart[ledger_account_id]),
            amount=present_natural(class_id, debit_net),
            ledger_account_id=ledger_account_id,
        )
        for ledger_account_id, debit_net in nets.items()
        if chart[ledger_account_id].class_id == class_id and debit_net != 0
    ]
    lines.sort(key=lambda line: line.label)
    return lines


def build_section(
    nets: dict[int, Decimal],
    chart: dict[int, LedgerAccount],
    class_id: int,
) -> StatementSection:
    """Return one accounting class's lines and total as a :class:`StatementSection`.

    The section builder both statements share: the class's :func:`section_lines`
    paired with their summed total, so a section's lines and total are derived
    together, from the same set, and cannot drift.  The balance sheet's Equity
    section is instead built directly (it appends the derived retained-earnings
    line before totaling), so this covers Assets / Liabilities / Income /
    Expense.

    Args:
        nets: ``{ledger_account_id: debit_net}`` (any classes; filtered to
            *class_id*).
        chart: The user's chart (:func:`load_chart`).
        class_id: The accounting class to build the section for.

    Returns:
        The :class:`StatementSection` (label-sorted lines + total).
    """
    lines = section_lines(nets, chart, class_id)
    return StatementSection(
        lines=lines,
        total=sum((line.amount for line in lines), _ZERO_MONEY),
    )


def dated_account_nets(
    user_id: int, scenario_id: int,
) -> dict[tuple[int, date], Decimal]:
    """Return every posting's net keyed by (ledger account, attribution date).

    The shared attribution core: the union of the three source buckets
    (transaction-linked, transfer-linked, sourceless corrections; residue
    dropped), each posting's net placed on its source's attribution date per the
    module docstring's C-3 rule.  Sources landing on the same (ledger account,
    date) are summed, so a caller folds the map by a date bound (as-of for the
    balance sheet, a calendar window for the income statement) with no
    per-account query.  Every entry's legs share one attribution date (all of a
    source's legs attribute together), so folding by date always includes whole
    entries -- the reader-contract C-1 guarantee that keeps the trial balance
    closed over any window.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).

    Returns:
        ``{(ledger_account_id, attribution_date): net Decimal}``; empty when the
        scenario has no posted sources.  Nets are the signed, debit-positive
        posting sums.

    Raises:
        PostingError: If a transaction- or transfer-linked source with a nonzero
            net cannot resolve its date (a broken SET-NULL or Transfer-Invariant
            linkage that must fail loudly rather than mis-attribute real money).
    """
    contributions = (
        _transaction_dated_nets(user_id, scenario_id)
        + _transfer_dated_nets(user_id, scenario_id)
        + _correction_dated_nets(user_id, scenario_id)
    )
    nets: dict[tuple[int, date], Decimal] = defaultdict(lambda: _ZERO_MONEY)
    for ledger_account_id, attribution_date, net in contributions:
        nets[(ledger_account_id, attribution_date)] += net
    return dict(nets)


def _grouped_source_nets(user_id, scenario_id, source_id_column, extra_filters):
    """Return ``(ledger_account_id, source_id, net)`` over a bucket's postings.

    The shared grouped query of the transaction and transfer buckets: sum
    ``account_postings.amount`` over the user's entries in *scenario_id* matching
    the bucket's *extra_filters*, grouped by leg ledger account and the bucket's
    source-identity column (``transaction_id`` or ``transfer_id``).  Zero-net
    groups are dropped -- a reverted / reversed-before-delete source nets to
    zero and needs no date -- so the caller resolves dates only for live sources.
    The ``(user_id, scenario_id)`` filter uses
    ``idx_journal_entries_user_scenario_period``.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.
        source_id_column: ``JournalEntry.transaction_id`` or
            ``JournalEntry.transfer_id`` -- the column identifying the source.
        extra_filters: The bucket's partition filters (linkage predicates).

    Returns:
        ``[(ledger_account_id, source_id, net), ...]`` for every nonzero group.
    """
    rows = (
        db.session.query(
            Posting.ledger_account_id,
            source_id_column,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
            *extra_filters,
        )
        .group_by(Posting.ledger_account_id, source_id_column)
        .all()
    )
    return [
        (ledger_account_id, source_id, net)
        for ledger_account_id, source_id, net in rows
        if net != 0
    ]


def _transaction_dated_nets(
    user_id: int, scenario_id: int,
) -> list[tuple[int, date, Decimal]]:
    """Return transaction-linked nets, each dated by its transaction's paid date.

    The transaction-linked bucket: entries carrying ``transaction_id`` with
    ``transfer_id`` NULL (the ``transaction`` cash sources and the
    ``loan_payment`` interest / escrow / refund / principal-split corrections),
    grouped by (ledger account, transaction) and attributed to the transaction's
    display-timezone paid date (falling back to its pay period ``start_date``
    when the day is missing).  For a loan payment, ``transaction_id`` is the
    loan-side income shadow, so the split is dated by the payment's paid date --
    the same basis the loan tax reader uses.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(ledger_account_id, attribution_date, net), ...]``; empty when no
        transaction-linked source is posted.

    Raises:
        PostingError: If a nonzero net's ``transaction_id`` resolves no
            transaction row (a broken SET-NULL linkage invariant).
    """
    nets = _grouped_source_nets(
        user_id, scenario_id, JournalEntry.transaction_id,
        [
            JournalEntry.transaction_id.isnot(None),
            JournalEntry.transfer_id.is_(None),
        ],
    )
    if not nets:
        return []
    dates = _transaction_attribution_dates(
        {transaction_id for _, transaction_id, _ in nets},
    )
    return [
        (ledger_account_id, dates[transaction_id], net)
        for ledger_account_id, transaction_id, net in nets
    ]


def _transaction_attribution_dates(
    transaction_ids: set[int],
) -> dict[int, date]:
    """Return each transaction's display-timezone attribution date, keyed by id.

    One batched load of ``(id, settled_on)`` over the given transactions, each
    read through the shared :func:`app.utils.balance_predicates.settled_day` --
    the same accessor the write-side walk and both folds ask the question
    through.  It loaded ``(id, paid_at, pay_period.start_date)`` and converted
    the instant until plan step X-f1 (ruling R-EC); the day is stored now, so
    the pay-period join is gone with the fallback it fed.

    Args:
        transaction_ids: The transaction ids whose paid dates to resolve.

    Returns:
        ``{transaction_id: attribution_date}`` over every id.

    Raises:
        PostingError: If any id resolves no transaction row -- the SET-NULL
            linkage invariant is broken (a live ``transaction_id`` always
            resolves; a miss must fail loudly, not mis-attribute money).
    """
    rows = (
        db.session.query(Transaction.id, Transaction.settled_on)
        .filter(Transaction.id.in_(transaction_ids))
        .all()
    )
    dates = {
        transaction_id: settled_day(transaction_id, stored_day)
        for transaction_id, stored_day in rows
    }
    missing = transaction_ids - set(dates)
    if missing:
        raise PostingError(
            f"Ledger holds a nonzero net for transaction ids {sorted(missing)} "
            f"but no such transaction rows exist; the SET NULL linkage "
            f"invariant is broken."
        )
    return dates


def _transfer_dated_nets(
    user_id: int, scenario_id: int,
) -> list[tuple[int, date, Decimal]]:
    """Return transfer-linked nets, each dated by the income shadow's paid date.

    The transfer-linked bucket: entries carrying ``transfer_id`` (a settled
    transfer's two cash legs), grouped by (ledger account, transfer) and
    attributed to the transfer's INCOME shadow's display-timezone paid date, so
    a transfer's two legs land on one date.  Transfers only ever post onto the
    two linked Asset/Liability accounts, so they never reach the income
    statement (both legs sit outside its Income/Expense filter).

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(ledger_account_id, attribution_date, net), ...]``; empty when no
        transfer is posted.

    Raises:
        PostingError: If a nonzero net's transfer has no active income shadow,
            or more than one (a Transfer-Invariant-1 violation).
    """
    nets = _grouped_source_nets(
        user_id, scenario_id, JournalEntry.transfer_id,
        [JournalEntry.transfer_id.isnot(None)],
    )
    if not nets:
        return []
    dates = _transfer_attribution_dates(
        {transfer_id for _, transfer_id, _ in nets},
    )
    return [
        (ledger_account_id, dates[transfer_id], net)
        for ledger_account_id, transfer_id, net in nets
    ]


def _transfer_attribution_dates(transfer_ids: set[int]) -> dict[int, date]:
    """Return each transfer's income-shadow attribution date, keyed by transfer.

    One batched load of the INCOME shadow (the ``to_account`` side, non-deleted)
    per transfer -- its stored ``settled_on`` -- read through the shared
    :func:`app.utils.balance_predicates.settled_day`.  Mirrors the write-side
    walk's transfer loader: a settled transfer has exactly its two shadows
    (Transfer Invariant 1), so a missing or duplicate income shadow is a broken
    invariant that fails loudly rather than dating the transfer off an arbitrary
    shadow.

    Args:
        transfer_ids: The transfer ids whose income-shadow dates to resolve.

    Returns:
        ``{transfer_id: attribution_date}`` over every id.

    Raises:
        PostingError: If any transfer resolves more than one active income
            shadow, or none.
    """
    # Pylint: ``duplicate-code`` -- the income-shadow query mirrors the
    # write-side walk's loader
    # (``account_posting_service._walk._transfer_source_days``) by
    # construction: the reader RESTATES the walk's transfer attribution rather
    # than importing a write-package internal, keeping this read package
    # decoupled from the write package (the same independent-restatement stance
    # the reconciliation oracles rely on).  Both sides now READ the same stored
    # day off the shadow through the same accessor (ruling R-DH deleted the
    # walk's instant partition; plan step X-f1 deleted the derivation itself),
    # so what is restated here is the LOADER, not the rule.  Re-measured at
    # X-f1 with the disable stripped: R0801 still fires over 11 shared lines,
    # so this suppression is load-bearing rather than stale residue.
    # **Plan step 3 SHIPPED and deliberately did not resolve this**:
    # it converged the partition RULE, and extracting a third shared home for
    # these loaders would be scaffolding for a caller plan step X-d deletes --
    # X-d retires the write-side walk onto the read walk, taking its twin of
    # this query with it.  X-d owns it.  One-sided disable so the walk stays
    # un-disabled.
    # pylint: disable=duplicate-code
    rows = (
        db.session.query(
            Transaction.transfer_id, Transaction.id, Transaction.settled_on,
        )
        .join(
            Transfer,
            db.and_(
                Transaction.transfer_id == Transfer.id,
                Transaction.account_id == Transfer.to_account_id,
            ),
        )
        .filter(
            Transaction.transfer_id.in_(transfer_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )
    # pylint: enable=duplicate-code
    dates = {
        transfer_id: settled_day(shadow_id, stored_day)
        for transfer_id, shadow_id, stored_day in rows
    }
    if len(rows) != len(dates):
        raise PostingError(
            "A transfer resolved more than one active income shadow; Transfer "
            "Invariant 1 is broken and the attribution date would be arbitrary."
        )
    missing = transfer_ids - set(dates)
    if missing:
        raise PostingError(
            f"Ledger holds a nonzero net for transfer ids {sorted(missing)} "
            f"but no active income shadow resolves them; Transfer Invariant 1 "
            f"is broken."
        )
    return dates


def _correction_dated_nets(
    user_id: int, scenario_id: int,
) -> list[tuple[int, date, Decimal]]:
    """Return sourceless-correction nets, each dated by the entry's ``entry_date``.

    The correction bucket: entries with both concrete source FKs NULL and a
    correction source kind (``loan_opening`` / ``loan_trueup`` /
    ``account_opening`` / ``account_trueup``), grouped by (ledger account,
    ``entry_date``).  A correction is an anchor fact with no ``paid_at`` instant,
    so it is dated by its stored civil ``entry_date`` (the anchor's civil date).
    The ``source_kind_id`` filter is what DROPS hard-delete residue: a
    residue entry is also both-FK-NULL but carries a transaction / transfer /
    loan_payment source kind, so it falls outside this bucket and is excluded
    whole.  Dropping residue is REQUIRED, not merely tidy: residue nets to zero
    per account (reverse-before-delete), but its original and reversal entries
    carry DIFFERENT ``entry_date`` s, so attributing them would land them on
    different calendar days and a windowed reader could see one without the
    other -- dropping the whole (zero-sum) pair keeps every window's tie-out
    closed.

    **Positive allowlist, updated in lock-step.**  This bucket KEEPS only the
    enumerated sourceless correction kinds; every other sourceless entry is
    treated as residue and dropped.  So a FUTURE sourceless correction kind must
    be added here or its (real, non-zero) legs are silently dropped -- an
    understated balance, not a broken tie-out (a dropped whole entry still sums
    to zero).  A future transaction- or transfer-LINKED kind needs no change
    here (its residue is correctly dropped).  The write-side walk's residue
    loader uses the inverse (negative) list because it is per-non-loan-account
    and never sees loan corrections; this reader spans the whole ledger, so it
    lists all four.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(ledger_account_id, entry_date, net), ...]`` for every nonzero group;
        empty when no correction is posted.
    """
    correction_source_ids = [
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP),
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
        ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
    ]
    rows = (
        db.session.query(
            Posting.ledger_account_id,
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.transaction_id.is_(None),
            JournalEntry.transfer_id.is_(None),
            JournalEntry.source_kind_id.in_(correction_source_ids),
        )
        .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
        .all()
    )
    return [
        (ledger_account_id, entry_date, net)
        for ledger_account_id, entry_date, net in rows
        if net != 0
    ]
