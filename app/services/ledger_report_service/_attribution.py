"""The shared read core both confirmed-ledger statements consume (Step 5).

Two statements read the append-only posting ledger: an income statement
(:mod:`._income_statement`) and a balance sheet (:mod:`._balance_sheet`).  They
share this module so their attribution -- WHICH source date each posting lands
on -- is defined ONCE, which is what makes the statements articulate
automatically (the income net over a window equals the equity delta between the
bounding balance sheets, with no reconciliation code).

**The attribution rule (reader-contract C-3).**  Every posting is attributed by
its SOURCE, in whole: a source's per-ledger-account net lands on the source's
CURRENT paid date, converted to the DISPLAY timezone (the decided L9 rule --
tax-year and calendar figures follow the user's wall clock, so an 8:05pm-Eastern
Dec-31 settle attributes to Dec 31, not the Jan 1 it becomes in UTC), falling
back to the source's pay period ``start_date`` when ``paid_at`` is NULL:

* transaction-linked entries (``transaction`` and ``loan_payment`` sources, both
  carrying ``transaction_id`` with ``transfer_id`` NULL): by the transaction's
  ``paid_at`` -- for a loan payment, the loan-side income shadow it links;
* transfer-linked entries (``transfer_id`` set): by the transfer's INCOME
  shadow's ``paid_at`` (Transfer Invariant 3 mirrors ``paid_at`` onto both
  shadows, and ``posting_service._entry_date`` dates the entry off exactly this
  shadow), so a transfer's two legs land on one date;
* sourceless corrections (``loan_opening`` / ``loan_trueup`` / ``account_opening``
  / ``account_trueup``, both concrete FKs NULL): by the stored ``entry_date`` (a
  correction is an anchor fact dated by the anchor's civil date -- it has no
  ``paid_at`` instant to convert);
* hard-delete residue (a ``transaction`` / ``transfer`` / ``loan_payment`` source
  whose concrete FK was SET-NULLed): DROPPED, as whole entries -- each sums to
  zero (the reverse-before-delete discipline), so dropping it leaves the trial
  balance closed.

The ``transfer_id IS NULL`` guard on the transaction bucket makes the three
buckets a PARTITION of the live ledger: a hypothetical dual-linked entry
classifies as transfer-linked, never both.  It was written to match the
write-side walk's own three-bucket partition over the postings; plan step X-d
deleted that walk (the writer reads the SOURCE rows now), so this reader is the
only place the posted ledger is still bucketed by source kind, and the partition
is load-bearing here alone.

**One civil date, and since plan step X-d there is no second loader to keep in
step with.**  This reader attributes a source to
:func:`app.utils.dates.to_display_civil_date` of its ``paid_at``.  The posting
WRITER used to hold a mirror of these loaders over the POSTED copy of the same
events, and the two were deliberately different until 2026-07-31 -- that walk
partitioned by UTC INSTANT against each anchor's ``created_at`` while this side
used the display-timezone civil day, and this paragraph justified an
independent restatement on exactly that difference.  Ruling R-DH removed the
difference (the instant partition cost production ``$4,001.42``), plan step 3
converged the predicate, and X-d deleted the mirror outright: the writer reads
the SOURCE rows through :func:`app.services.cash_ledger.walk_cash_ledger` now.
So the loaders below are no longer a restatement of anything -- they are the
only place the POSTED ledger is bucketed and dated by source, which is a read
concern this package owns alone.

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
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.posting_reads import PostingError
from app.utils.dates import to_display_civil_date

from ._types import StatementLine, StatementSection

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class StatementClassIds:
    """The five accounting-class ref ids the statements section and sign by.

    Resolved once per statement call (:func:`statement_class_ids`) so the
    readers compare a ledger account's ``class_id`` against integer ids -- the
    IDs-for-logic invariant -- rather than reading the string class name.  Used
    to place each account in its section (Asset / Liability / Income / Expense /
    Equity) and to derive the Income + Expense set the retained-earnings line
    closes.

    Attributes:
        asset: The Asset class ref id.
        liability: The Liability class ref id.
        income: The Income class ref id.
        expense: The Expense class ref id.
        equity: The Equity class ref id.
    """

    asset: int
    liability: int
    income: int
    expense: int
    equity: int


def statement_class_ids() -> StatementClassIds:
    """Return the five accounting-class ref ids as a :class:`StatementClassIds`.

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
    )


def present_natural(class_id: int, debit_net: Decimal) -> Decimal:
    """Return a debit-positive posting net as its natural-balance value (C-4).

    The reader-contract C-4 presentation rule: a debit-normal class (Asset,
    Expense) presents its debit-positive net as-is; a credit-normal class
    (Liability, Income, Equity) presents the NEGATED net, so a revenue,
    liability, or equity line reads positive when the account holds its natural
    balance.  The ledger is presented FAITHFULLY -- there is no ``-abs``
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
    other kind (fallback, per-loan interest / escrow / refund / opening,
    anchor-equity) reads its ``name`` snapshot.  The snapshot -- not
    ``account.name`` -- is used for an anchor-equity row even though it carries
    an ``account_id``, because the COALESCE display rule is the linked-row rule
    only.

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
    when ``paid_at`` is NULL).  For a loan payment, ``transaction_id`` is the
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

    One batched load of ``(id, paid_at, pay_period.start_date)`` over the
    given transactions, each mapped to
    :func:`app.utils.dates.to_display_civil_date` of its ``paid_at`` with the
    pay period ``start_date`` as the NULL fallback.

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
        db.session.query(
            Transaction.id, Transaction.paid_at, PayPeriod.start_date,
        )
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(Transaction.id.in_(transaction_ids))
        .all()
    )
    dates = {
        transaction_id: to_display_civil_date(paid_at, start_date)
        for transaction_id, paid_at, start_date in rows
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
    per transfer -- its ``(paid_at, pay_period.start_date)`` -- mapped to
    :func:`app.utils.dates.to_display_civil_date`.  A settled transfer has
    exactly its two shadows (Transfer Invariant 1), so a missing or duplicate
    income shadow is a broken invariant that fails loudly rather than dating the
    transfer off an arbitrary shadow.

    **It carried a ``duplicate-code`` disable until plan step X-d, and the
    disable is DELETED rather than re-justified.**  The query it was held apart
    from was the write-side walk's own transfer loader; X-d deleted that walk,
    so there is no second copy left and the suppression suppressed nothing.
    That is measured, not assumed -- removing the two pragma lines leaves
    ``pylint app/`` at 10.00/10 -- and it had to be measured, because pylint's
    ``useless-suppression`` does NOT report a stale ``duplicate-code`` disable
    (verified by planting one back and re-running: still 10.00/10, no I0021).
    A disable this gate cannot invalidate is one a reader has to check by hand.

    Args:
        transfer_ids: The transfer ids whose income-shadow dates to resolve.

    Returns:
        ``{transfer_id: attribution_date}`` over every id.

    Raises:
        PostingError: If any transfer resolves more than one active income
            shadow, or none.
    """
    rows = (
        db.session.query(
            Transaction.transfer_id, Transaction.paid_at, PayPeriod.start_date,
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
            Transaction.transfer_id.in_(transfer_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )
    dates = {
        transfer_id: to_display_civil_date(paid_at, start_date)
        for transfer_id, paid_at, start_date in rows
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
