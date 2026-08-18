"""The cash-transaction reconciliation oracle (Build-Order Step 3, Commit 8).

The correctness gate for the ordinary-transaction half of the double-entry
posting ledger.  Build-Order Step 2 posts settled **transfer** shadows; Step 3
backfills and posts settled **cash** (ordinary, non-transfer) transactions, so a
real account's linked ledger now accumulates BOTH sources at once.  Reads stay on
the ``balance_at`` seam over ``budget.transactions`` (Step 3 changes no read
path), so -- exactly as in Step 2 -- the ledger is validated against the SOURCE
transaction rows, never against a displayed balance.  The invariants below are
plan Section 6:

  1. **Per linked account (cash side).**  For each real account A (its linked
     ledger account), the net of A's posting legs equals
     ``settled_transfer_effect(A) + settled_transaction_effect(A)`` -- the
     combined effect of A's settled, non-deleted transfer shadows AND ordinary
     transactions.  The transaction term is the signed
     ``effective - Sigma(credit entries)`` (``+`` income / ``-`` expense), where
     ``effective`` is what the row RECORDED as having moved
     (``posting_reads.settled_figure_clause``) -- it was
     ``COALESCE(actual_amount, estimated_amount)`` until plan step X-au-c3 made
     a settled row's figure its own record rather than a fallback to its plan.
  2. **Per counter account (category / fallback / orphan).**  For each non-linked
     (Income/Expense) ledger account CA, ``SUM(postings on CA)`` equals the
     negation of the signed effects of the transactions whose legs CURRENTLY
     reside on CA -- identified by the ``journal_entries.transaction_id``
     **linkage**, NOT by ``category_id`` matching.  The linkage formulation is
     load-bearing for an **orphan** (a deleted category's former ledger account):
     the transactions that posted to it now read ``category_id IS NULL`` -- so a
     ``category_id`` match could no longer find them, while the
     ``transaction_id`` linkage reconciles the orphan against exactly the
     transactions whose legs landed on it (see the ``ledger_account.py``
     "Reconciliation of orphans" note).  It is equally load-bearing through a
     **recategorize** (A -> B): the transaction's reversal nets it to zero on A
     (excluded by the non-zero-net guard) and posts its effect to B.
  3. **Per-entry balance.**  Every journal entry's legs ``SUM(amount) = 0`` and
     ``COUNT(*) >= 2`` (also DB-enforced by ``ck_account_postings_balanced``).
  4. **Trial balance.**  ``SUM(account_postings.amount) = 0`` across the whole
     ledger (follows from 3, asserted directly as a cheap self-check).
  5. **Per-transaction completeness.**  Every settled, non-deleted, non-transfer
     transaction with a NONZERO confirmed cash effect has at least one journal
     entry -- no settled cash transaction is silently unposted.  A zero-effect
     row (an all-credit envelope) is correctly NOT required to post.
  6. **Multi-scenario isolation** and **owner isolation** (via
     ``journal_entry.user_id``) -- a posting carries no ``user_id``; its owner is
     reached only through its journal entry, and one owner's / scenario's
     reconciliation never picks up another's.
  7. **Revert-and-recategorize reconciles (the plan Section 2.8 CRITICAL
     regression lock)** -- driven through the real PATCH route, then swept.

**A "backfill == go-forward" invariant was listed here and its case is DELETED**
(plan step X-f1; see the note above ``TestRevertAndRecategorizeReconciles``).  It
compared the ``posting_service`` Python builder against the raw-SQL Commit-7
migration backfill on one transaction.  **Losing it lost something real and
saying so is the point**: it was the only check that an INDEPENDENT
implementation of the sign / amount / date rules agreed with the go-forward
builder, and the two surviving "backfill == go-forward" tests elsewhere
(``test_posting_ledger_account_backfill.py``, ``test_loan_posting_backfill.py``)
reuse the go-forward builder, so they are true by construction.  It went because
the SQL it drove reads a column the head revision drops, not because it was
redundant.

Two adversarial cases prove the oracle is not vacuous: tampering a settled
transaction's estimate makes the per-account reconciliation FAIL -- driven
through the real ``_assert_full_reconciliation`` sweep helper under
``pytest.raises``, so a regression in the helper itself is caught, not only in an
inline re-derivation (a real ledger drift would be caught) -- and injecting one
extra leg makes the trial balance go non-zero (the ``= 0`` assertion is a real
check, not one the per-entry trigger makes unconditionally true).  A reverted
transaction reconciles at zero (original + reversal net to zero; the source-side
query drops it once it is no longer settled), proving the append-only correction
discipline end to end.

**Non-tautological by construction**, the same three independent ways as Step 2:

  * **hand-computed literals** -- the expected ledger sums are the test author's
    arithmetic over the seeded amounts (e.g. Checking pays $50 and receives
    $2,000 of cash plus sends $350 of transfers, so its ledger MUST be exactly
    ``+1600.00``), owing nothing to either producer or any service helper;
  * **independent cross-table queries** -- the ledger side
    (``_independent_ledger_sum`` / ``_ledger_account_sum``) reads
    ``account_postings`` through a different join shape than the
    ``posting_service`` readers, and the source side
    (``_independent_combined_source_effect`` / ``_signed_cash_effect``) reads the
    ``transactions`` table; asserting the two equal reconciles what the producers
    WROTE against the transaction source of truth;
  * **the production service helpers** -- ``account_posting_total``,
    ``settled_transfer_effect``, and ``settled_transaction_effect`` (the readers
    Steps 4-5 will switch balances onto) must match the hand-computed literals
    too.

Cash transactions are settled through the real go-forward primitives -- the
status seam plus the posting builder -- via
``create_settled_cash_transaction`` (the cash analog of the Step-2
``create_settled_transfer``), so every reconciled row was produced exactly as the
mark-done route produces it.  All money is ``Decimal`` from strings, with the
arithmetic shown per the testing standard.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import case

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db as _db
from app.models.account import Account
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service, posting_service, status_seam
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    settled_status_ids,
)
from tests._test_helpers import (
    settlement_if_settling,
    add_txn,
    create_account_of_type,
    create_envelope_txn,
    create_settled_cash_transaction,
    create_settled_transfer,
    linked_ledger_account,
)
from app.services import cash_ledger
from app.services.row_valuation import owned_contribution


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These deliberately re-derive each side from scratch so the oracle is a genuine
# second opinion: a bug shared by the two service readers cannot hide, because
# the ledger side here reads ``account_postings`` and the source side reads
# ``transactions`` with independently-written SQL/Python, and both are also
# pinned to hand-computed literals.  (The source side necessarily restates the
# one correct definition of a settled transaction's confirmed cash effect, so it
# mirrors ``settled_transaction_effect``'s semantics; the hand-computed literals,
# not these queries, are what make the oracle non-tautological -- this layer adds
# the cross-table, whole-DB sweep the literals cannot.)
#
# Some of these (``_independent_ledger_sum``, ``_trial_balance``,
# ``_entries_violating_balance``, ``_independent_transfer_shadow_effect``) mirror
# the Step-2 oracle (``test_posting_ledger_reconciliation.py``).  The duplication
# is DELIBERATE, not an oversight: each oracle keeps its OWN independent
# reconciliation queries so it remains a self-contained second opinion (the
# Step-2 module docstring states the same "re-derive from scratch" intent).
# Genuinely shared *utilities* with no independence role (the migration loader,
# the raw-SQL posting clear, the account-pairing lookup) live in
# ``tests/_test_helpers.py`` instead.


def _independent_ledger_sum(account_id: int, scenario_id: int) -> Decimal:
    """Sum a REAL account's posting legs in a scenario (independent query).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` (for the real ``account_id``), summing the signed
    ``amount`` over BOTH transfer and transaction legs.  Keyed off the REAL
    account via ``ledger_accounts.account_id``, a different join shape than
    ``posting_service.account_posting_total`` (which resolves the ledger account
    first), so the two cannot share a lookup bug.

    Filtered to the LINKED kind (Step 5): an anchor correction lands
    ``+delta`` on the linked row and ``-delta`` on the ``anchor_equity``
    twin, which shares the ``account_id`` column -- a bare-``account_id``
    sum would cancel the correction pairwise and silently reproduce the
    pre-Step-5 changes-only figure, making the absolute assertions vacuous.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _opening_anchor(account_id: int) -> Decimal:
    """Return an account's anchor balance -- its posted opening's target.

    The Step-5 opening correction drives the linked ledger to exactly the
    account's asserted anchor (every account in this suite carries only its
    origination assertion, and every settle is stamped at server-now, after
    it), so the absolute reconciliation is ``linked ledger == anchor +
    settled source effect``.
    """
    # The account's asserted balance, read from the assertion itself: ruling
    # R-EH deleted the ``accounts.current_anchor_balance`` column this queried,
    # which was a copy of the same row.
    return cash_ledger.resolve_anchor(
        _db.session.get(Account, account_id),
    ).balance


def _ledger_account_sum(ledger_account_id: int, scenario_id: int) -> Decimal:
    """Sum one SPECIFIC ledger account's posting legs in a scenario.

    The counter-account (category / fallback / orphan) analog of
    :func:`_independent_ledger_sum`: keyed on the ledger account's own ``id``
    rather than a real ``account_id``, because a non-linked Income/Expense
    account has no ``account_id``.  Scenario-scoped via the journal entry's
    denorm, so the same owner-scoped category account reconciles independently in
    each scenario that booked into it.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _independent_transfer_shadow_effect(
    account_id: int, scenario_id: int
) -> Decimal:
    """Sum an account's settled transfer-shadow effect (independent query).

    The transfer half of the balance-side truth: over the account's settled
    (``status.is_settled``), non-deleted transfer shadows
    (``transfer_id IS NOT NULL``) in *scenario_id*, add ``+effective`` for an
    income shadow (money in) and ``-effective`` for an expense shadow (money
    out), where ``effective = COALESCE(actual, estimated)``.  The same shape as
    the Step-2 oracle's transfer reconciliation, read from ``transactions``.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = _db.func.coalesce(
        Transaction.settled_amount, Transaction.estimated_amount
    )
    signed = case(
        (Transaction.transaction_type_id == income_type_id, effective),
        else_=-effective,
    )
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(signed), Decimal("0"))
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def _independent_cash_txn_effect(account_id: int, scenario_id: int) -> Decimal:
    """Sum an account's settled ordinary-transaction effect (independent query).

    The cash half of the balance-side truth: over the account's settled,
    non-deleted, NON-transfer (``transfer_id IS NULL``) transactions in
    *scenario_id*, sum the signed confirmed cash effect
    ``effective - Sigma(credit entries)`` -- ``+`` income / ``-`` expense, where
    ``effective = COALESCE(actual, estimated)`` and the per-transaction credit
    sum is an independently-written correlated subquery.  Reads ``transactions``,
    a different table than :func:`_independent_ledger_sum`, so asserting the two
    equal reconciles what the producers wrote against the transaction source.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = _db.func.coalesce(
        Transaction.settled_amount, Transaction.estimated_amount
    )
    credit_sum = (
        _db.session.query(
            _db.func.coalesce(
                _db.func.sum(TransactionEntry.amount), Decimal("0")
            )
        )
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.is_credit.is_(True),
        )
        .correlate(Transaction)
        .scalar_subquery()
    )
    cash_effect = effective - credit_sum
    signed = case(
        (Transaction.transaction_type_id == income_type_id, cash_effect),
        else_=-cash_effect,
    )
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(signed), Decimal("0"))
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def _independent_posted_purchase_effect(
    account_id: int, scenario_id: int
) -> Decimal:
    """Sum the purchases posted under a NOT-YET-SETTLED parent (independent query).

    The third term of the balance-side truth, and ruling **R-FM** is why it
    exists (plan step X-f3b): a purchase whose bank posting day the owner
    recorded books its own cash leg whatever its envelope's status is, so an
    account's ledger now holds money no SETTLED row's ``effective`` figure
    accounts for.

    Only the purchases whose PARENT is unsettled.  A purchase on a settled
    parent is already inside :func:`_independent_cash_txn_effect`: that
    expression sums ``effective - Sigma(credit)`` over the whole row, which is
    exactly what the parent's own leg and its purchases' legs add up to, so
    counting one here would double it.

    Restated in SQL rather than shared with ``posting_service._purchase_posts``,
    for the reason every helper in this file is: an oracle that imported the
    rule it grades could not grade it.
    """
    return (
        _db.session.query(
            _db.func.coalesce(
                _db.func.sum(-TransactionEntry.amount), Decimal("0")
            )
        )
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.notin_(settled_status_ids()),
            Transaction.status_id.notin_(balance_excluded_status_ids()),
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .scalar()
    )


def _independent_combined_source_effect(
    account_id: int, scenario_id: int
) -> Decimal:
    """Sum an account's combined settled transfer + transaction source effect.

    The full balance-side truth a linked account's ledger must equal in Step 3:
    transfer shadows AND ordinary transactions, both signed debit-positive, plus
    the purchases posted under a still-unsettled parent (plan step X-f3b, ruling
    **R-FM**).  The independent restatement of ``settled_transfer_effect +
    settled_transaction_effect + posted_purchase_effect``.
    """
    return (
        _independent_transfer_shadow_effect(account_id, scenario_id)
        + _independent_cash_txn_effect(account_id, scenario_id)
        + _independent_posted_purchase_effect(account_id, scenario_id)
    )


def _signed_cash_effect(txn: Transaction) -> Decimal:
    """Return a transaction's signed, debit-positive confirmed cash effect.

    The per-row independent computation used by the per-counter sweep:
    ``(effective_amount - Sigma(credit entries))`` signed ``+`` for income / ``-``
    for an expense.  ``effective_amount`` is the model property (``actual`` over
    ``estimated``, or ``0`` for a deleted / excluded row); the credit sum is over
    the loaded entries.  Independent of ``posting_service`` (it never imports the
    builder's ``_signed_cash_leg``); the counter leg the ledger should hold for
    *txn* is the negation of this.
    """
    credit_sum = sum(
        (entry.amount for entry in txn.entries if entry.is_credit),
        Decimal("0"),
    )
    effect = owned_contribution(txn) - credit_sum
    return effect if txn.is_income else -effect


def _trial_balance() -> Decimal:
    """Return ``SUM(account_postings.amount)`` over the whole ledger."""
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .scalar()
    )


def _entries_violating_balance() -> list[tuple[int, Decimal, int]]:
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.  Any
    row returned here is a violation -- the per-entry invariant the deferred
    trigger also enforces, re-checked from the ORM side.
    """
    rows = (
        _db.session.query(
            Posting.journal_entry_id,
            _db.func.sum(Posting.amount),
            _db.func.count(Posting.id),
        )
        .group_by(Posting.journal_entry_id)
        .all()
    )
    return [
        (entry_id, leg_sum, leg_count)
        for entry_id, leg_sum, leg_count in rows
        if leg_sum != 0 or leg_count < 2
    ]


# ---------------------------------------------------------------------------
# Sweep assertions (production-wide, run after each scenario's mutations)
# ---------------------------------------------------------------------------


def _assert_linked_accounts_reconcile(scenario_id: int) -> None:
    """Assert every non-loan LINKED ledger reconciles ABSOLUTELY in *scenario_id*.

    For each of the scenario owner's non-loan real accounts (its LINKED
    ledger row), the independent ledger sum equals the account's opening
    anchor plus the independent combined (transfer + transaction) source
    effect -- the Step-5 absolute form (every settle in this suite is
    stamped at server-now, after the origination assertion; every swept
    scenario carries its owner's openings, posted in the baseline at create
    time or into a what-if by the effect-time self-heal alongside that
    scenario's first settle -- the latter ONLY because these fixtures
    settle on the same UTC day the accounts were created; not a general
    guarantee, and R8 owns the residual multi-scenario policy).  Amortizing loans are excluded -- their
    absolute invariant couples on the amortization split and is the loan
    oracle's job.  Holds over every such account, not only the ones a given
    test hand-computes.
    """
    scenario_owner_id = (
        _db.session.query(Scenario.user_id)
        .filter(Scenario.id == scenario_id)
        .scalar()
    )
    linked = (
        _db.session.query(LedgerAccount)
        .join(Account, LedgerAccount.account_id == Account.id)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            AccountType.has_amortization.is_(False),
            Account.user_id == scenario_owner_id,
        )
        .all()
    )
    # Every caller settles at least one cash movement, which mints the Checking
    # linked ledger account, so an empty result means the query silently found
    # nothing (a minting or filter regression) and the loop below would pass
    # vacuously -- assert non-empty so the sweep cannot be a no-op.
    assert linked, (
        "no linked ledger accounts to reconcile -- the linked sweep would be "
        "vacuous (expected at least the Checking account's linked ledger)"
    )
    for ledger_account in linked:
        ledger = _independent_ledger_sum(ledger_account.account_id, scenario_id)
        effect = _independent_combined_source_effect(
            ledger_account.account_id, scenario_id
        )
        opening = _opening_anchor(ledger_account.account_id)
        assert ledger == opening + effect, (
            f"account {ledger_account.account_id}: ledger {ledger} != "
            f"opening {opening} + combined source effect {effect} in "
            f"scenario {scenario_id}"
        )


def _assert_counter_accounts_reconcile(scenario_id: int) -> None:
    """Assert every COUNTER ledger account reconciles by transaction_id linkage.

    For each non-linked (category / fallback / orphan) ledger account CA, sum its
    posting legs in *scenario_id* (the LHS), then -- via the
    ``journal_entries.transaction_id`` linkage, NOT a ``category_id`` match --
    group those legs by transaction and check each transaction's net on CA equals
    the negation of its independently-computed signed cash effect.  A transaction
    whose net on CA is zero (reversed, or recategorized away) is excluded by the
    non-zero-net guard, so a recategorize (which leaves a net-zero reversal pair
    on the OLD account) reconciles; an orphan, whose transactions now read
    ``category_id IS NULL`` and could not be found by ``category_id`` matching,
    reconciles because the linkage still points its legs at it.

    Beyond the magnitude, a still-categorized transaction's leg is also checked
    for correct ROUTING -- its current ``category_id`` must resolve to CA -- so a
    same-class miscategorization (a $50 Groceries expense whose counter leg landed
    on the Rent-Expense account: same class, same magnitude) is caught, not just a
    wrong amount.  A NULL-category transaction skips the routing check: it resolves
    to the fallback, but its leg may legitimately sit on an ORPHAN (its category
    was deleted and the row never re-synced) -- precisely why the orphan is
    reconciled by the linkage, not by re-resolving ``category_id``.

    **A PURCHASE's leg is resolved to its PARENT before grouping** (plan step
    X-f3b, ruling **R-FM**): it links by ``transaction_entry_id`` and carries no
    ``transaction_id``, so grouping on that column alone would drop every one
    into the hard-deleted bucket and fail with the wrong cause.  Resolved, the
    per-transaction expectation stays whole -- a SETTLED row's counter net is
    its own leg plus its purchases', and an unsettled row's IS its purchases'.

    A ``transaction_id IS NULL`` group is a hard-deleted transaction's
    SET-NULL'd legs: the reverse-before-delete pair MUST net to zero, asserted
    here so a stranded (un-reversed) leg would be caught.
    """
    counters = (
        _db.session.query(LedgerAccount)
        .filter(LedgerAccount.account_id.is_(None))
        .all()
    )
    for counter in counters:
        lhs = _ledger_account_sum(counter.id, scenario_id)
        # A PURCHASE's counter leg links by ``transaction_entry_id`` and carries
        # NO ``transaction_id`` (plan step X-f3b, ruling **R-FM**), so grouping
        # on that column alone would drop every one of them into the
        # hard-deleted bucket below and fail with the wrong cause.  Resolving it
        # to the purchase's PARENT is what keeps the per-transaction expectation
        # whole: a settled row's counter net is its own leg plus its purchases'.
        parent_of_purchase = (
            _db.session.query(TransactionEntry.transaction_id)
            .filter(TransactionEntry.id == JournalEntry.transaction_entry_id)
            .correlate(JournalEntry)
            .scalar_subquery()
        )
        source_transaction_id = _db.func.coalesce(
            JournalEntry.transaction_id, parent_of_purchase,
        )
        rows = (
            _db.session.query(
                source_transaction_id,
                _db.func.sum(Posting.amount),
            )
            .select_from(Posting)
            .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
            .filter(
                Posting.ledger_account_id == counter.id,
                JournalEntry.scenario_id == scenario_id,
            )
            .group_by(source_transaction_id)
            .all()
        )
        rhs = Decimal("0")
        for transaction_id, net in rows:
            if transaction_id is None:
                # Legs whose source transaction was hard-deleted (transaction_id
                # SET NULL): the reverse-before-delete pair must net to zero.
                assert net == 0, (
                    f"counter {counter.id}: orphaned (transaction_id NULL) "
                    f"legs net {net}, not zero -- a delete failed to reverse"
                )
                continue
            if net == 0:
                # Reversed or recategorized-away on this account: contributes
                # nothing, and the source row is correctly accounted elsewhere.
                continue
            txn = _db.session.get(Transaction, transaction_id)
            assert txn is not None, (
                f"counter {counter.id}: transaction {transaction_id} linked to "
                f"a non-zero leg no longer exists (link should have SET NULL)"
            )
            # A non-zero net on a counter account comes from an active,
            # non-transfer transaction that has either SETTLED or had a purchase
            # post under it (plan step X-f3b); everything else nets to zero.
            assert txn.transfer_id is None
            assert txn.is_deleted is False
            if txn.status.is_settled:
                # Its own leg plus its purchases' legs, which sum to the whole
                # confirmed cash effect however that total is split.
                expected_counter = -_signed_cash_effect(txn)
            else:
                # Not settled: the row's own leg is absent, so the net IS its
                # posted purchases -- an expense's counter leg is a debit, hence
                # positive.  Restated from the entries rather than read off the
                # ledger, so the two sides stay independent.
                expected_counter = sum(
                    (
                        entry.amount for entry in txn.entries
                        if not entry.is_credit and entry.settled_on is not None
                    ),
                    Decimal("0"),
                )
            assert net == expected_counter, (
                f"counter {counter.id}: transaction {transaction_id} net {net} "
                f"!= expected counter leg {expected_counter}"
            )
            # Routing: a still-categorized transaction's leg must land on the
            # account its CURRENT category resolves to (catches a same-class
            # wrong-category post the magnitude check alone would miss).  A
            # NULL-category transaction is skipped -- it resolves to the fallback,
            # but its leg may legitimately sit on an orphan (deleted category).
            if txn.category_id is not None:
                ledger_class = (
                    LedgerAccountClassEnum.INCOME if txn.is_income
                    else LedgerAccountClassEnum.EXPENSE
                )
                assert _counter_ledger_id(
                    txn.pay_period.user_id, ledger_class, txn.category_id,
                ) == counter.id, (
                    f"counter {counter.id}: transaction {transaction_id} routed "
                    f"its counter leg to the wrong category account"
                )
            rhs += net
        assert lhs == rhs, (
            f"counter {counter.id}: ledger {lhs} != linkage-summed source "
            f"effect {rhs} in scenario {scenario_id}"
        )


def _assert_full_reconciliation(scenario_id: int) -> None:
    """Assert the whole ledger reconciles: linked, counter, per-entry, trial.

    The production-wide sweep run after each test's mutations.  Linked and
    counter reconciliation are scenario-scoped; the per-entry balance and trial
    balance are global self-checks (always true for a balanced ledger, asserted
    cheaply on every sweep).
    """
    _assert_linked_accounts_reconcile(scenario_id)
    _assert_counter_accounts_reconcile(scenario_id)
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")


def _assert_every_settled_transaction_posts(user_id: int) -> None:
    """Assert every settled, nonzero-effect cash transaction posted >= 1 entry.

    The per-transaction completeness backstop: a settled, non-deleted,
    non-transfer transaction with a NONZERO confirmed cash effect must carry at
    least one journal entry (no silent unposted row).  A zero-effect row (an
    all-credit envelope: ``effective == Sigma(credit)``) posts nothing and is
    correctly NOT required to have an entry.
    """
    settled = (
        _db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    for txn in settled:
        if _signed_cash_effect(txn) == 0:
            # A zero-effect settled row (all-credit envelope) posts nothing.
            continue
        entry_count = (
            _db.session.query(JournalEntry)
            .filter_by(transaction_id=txn.id)
            .count()
        )
        assert entry_count >= 1, (
            f"settled transaction {txn.id} with a nonzero effect posted no "
            f"journal entry"
        )


def _counter_ledger_id(
    user_id: int, ledger_class, category_id: int | None
) -> int:
    """Resolve the category / fallback ledger account id, mirroring the resolver.

    Returns the row the go-forward reconcile created, so a leg can be hand-checked
    against it; ``category_id=None`` resolves the per-(owner, class) Uncategorized
    fallback.
    """
    return ledger_account_service.get_or_create_category_ledger_account(
        user_id, category_id, ledger_class,
    ).id


# ---------------------------------------------------------------------------
# 1. Per linked account: transfer + transaction legs combine and reconcile
# ---------------------------------------------------------------------------


class TestPerLinkedAccountReconciliation:
    """A linked account's ledger sums its transfer AND transaction legs."""

    @pytest.mark.server_clock
    def test_combined_transfer_and_cash_legs_reconcile_three_ways(
        self, app, db, seed_user,
    ):
        """Checking reconciles over $350 of transfers plus $1,950 of cash.

        Arithmetic (baseline scenario), all on Checking:
          - transfer Checking -> Savings   $100  -> Checking -100, Savings +100
          - transfer Checking -> Mortgage  $250  -> Checking -250, Mortgage +250
          - cash EXPENSE $50 Groceries           -> Checking  -50, Groceries +50
          - cash INCOME  $2000 Salary            -> Checking +2000, Salary  -2000

        Checking's combined effect = -100 -250 -50 +2000 = +1600.00, riding
        its $1000.00 Step-5 opening: ledger 2600.00.  Its transfer effect is
        -350 and its transaction effect is +1950, and -350 + 1950 = +1600.
        Savings 100 (opening) + 100 = 200.00; the Mortgage is amortizing (no
        account-walk opening, and it carries no LoanParams so no loan genesis
        either), leaving its ledger at the bare +250.00.  All three
        independent computations -- the hand-computed literal, the
        independent cross-table query, and the production service helpers --
        must agree on every account.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Combined Savings",
            )
            mortgage = create_account_of_type(
                seed_user, db.session, "Mortgage", "Combined Mortgage",
            )
            db.session.commit()

            create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("100.00"),
            )
            create_settled_transfer(
                seed_user, db.session, checking, mortgage, period,
                amount=Decimal("250.00"),
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("2000.00"),
                is_income=True, category=seed_user["categories"]["Salary"],
            )
            db.session.commit()

            expected = {
                checking.id: (Decimal("2600.00"), Decimal("1600.00")),
                savings.id: (Decimal("200.00"), Decimal("100.00")),
                mortgage.id: (Decimal("250.00"), Decimal("250.00")),
            }
            for account_id, (want_ledger, want_effect) in expected.items():
                # (a) hand-computed literal == independent ledger-table query.
                assert _independent_ledger_sum(
                    account_id, scenario_id,
                ) == want_ledger
                # (b) independent source query == the hand-computed effect.
                assert _independent_combined_source_effect(
                    account_id, scenario_id,
                ) == want_effect
                # (c) the production service helpers agree too.
                assert posting_service.account_posting_total(
                    account_id, scenario_id,
                ) == want_ledger
                assert (
                    posting_service.settled_transfer_effect(
                        account_id, scenario_id,
                    )
                    + posting_service.settled_transaction_effect(
                        account_id, scenario_id,
                    )
                ) == want_effect

            # Checking's split is exactly transfers -350 + transactions +1950.
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-350.00")
            assert posting_service.settled_transaction_effect(
                checking.id, scenario_id,
            ) == Decimal("1950.00")

            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 2. Per counter account: category / fallback / orphan, by transaction_id link
# ---------------------------------------------------------------------------


class TestPerCounterAccountReconciliation:
    """Category, fallback, and orphan counter accounts reconcile by linkage."""

    def test_category_and_fallback_counter_accounts_reconcile(
        self, app, db, seed_user,
    ):
        """A categorized expense, a NULL-category expense, and income reconcile.

        Arithmetic (all on Checking):
          - cash EXPENSE $50 Groceries  -> Groceries-Expense counter +50.00
          - cash EXPENSE $30 (no cat)   -> Uncategorized-Expense fallback +30.00
          - cash INCOME  $2000 Salary   -> Salary-Income counter -2000.00

        The categorized expense books the Groceries-Expense category row; the
        uncategorized one books the per-(owner, class) Expense fallback
        (``is_fallback`` True); the income books the Salary-Income row.  Each
        counter total is hand-checked, and the counter-account sweep reconciles
        every one by the ``transaction_id`` linkage.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            period = seed_user["bootstrap_period"]

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("30.00"),
                category=None,
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("2000.00"),
                is_income=True, category=seed_user["categories"]["Salary"],
            )
            db.session.commit()

            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            fallback_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE, None,
            )
            salary_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.INCOME,
                seed_user["categories"]["Salary"].id,
            )

            # Hand-computed counter totals (the negation of each cash leg).
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("50.00")
            assert _ledger_account_sum(
                fallback_counter, scenario_id,
            ) == Decimal("30.00")
            assert _ledger_account_sum(
                salary_counter, scenario_id,
            ) == Decimal("-2000.00")

            # The fallback row is the is_fallback singleton, not a category row.
            fallback = db.session.get(LedgerAccount, fallback_counter)
            assert fallback.is_fallback is True
            assert fallback.category_id is None

            _assert_full_reconciliation(scenario_id)

    def test_orphan_counter_account_reconciles_after_category_delete(
        self, app, db, seed_user,
    ):
        """A deleted category's former counter account reconciles by linkage.

        A $50 expense in a fresh "Leisure: Hobbies" category posts its counter
        leg into that category's ledger account.  Deleting the budget category
        (the schema's ``ON DELETE SET NULL`` clears ``category_id`` on BOTH the
        ledger account -- which becomes an orphan -- and the transaction) leaves
        the orphan still holding +50.00 while the transaction now reads
        ``category_id IS NULL``.  A ``category_id`` match could no longer find
        the transaction (it would mis-attribute it to the fallback), but the
        ``transaction_id`` linkage reconciles the orphan against exactly the
        transaction whose leg landed on it -- the property the counter sweep
        relies on.  (The category-delete *route* archives a category still in use
        by a transaction; this raw-SQL delete reproduces the DB-level SET NULL
        directly to lock the defensive linkage reconciliation -- see the
        ``ledger_account.py`` "Reconciliation of orphans" note.)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            period = seed_user["bootstrap_period"]

            hobbies = Category(
                user_id=user_id, group_name="Leisure", item_name="Hobbies",
            )
            db.session.add(hobbies)
            db.session.flush()
            hobbies_id = hobbies.id
            # Capture the display name BEFORE the delete so the post-delete
            # snapshot can be checked against it (not a bare string literal).
            hobbies_display_name = hobbies.display_name

            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=hobbies,
            )
            db.session.commit()
            txn_id = txn.id
            orphan_id = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE, hobbies_id,
            )
            assert _ledger_account_sum(
                orphan_id, scenario_id,
            ) == Decimal("50.00")

            # Delete the budget category: the FK SET NULL turns its ledger
            # account into an orphan and clears the transaction's category_id.
            db.session.execute(
                _db.text("DELETE FROM budget.categories WHERE id = :c"),
                {"c": hobbies_id},
            )
            db.session.commit()
            db.session.expire_all()

            orphan = db.session.get(LedgerAccount, orphan_id)
            assert orphan.category_id is None
            assert orphan.account_id is None
            assert orphan.is_fallback is False  # an orphan, not the fallback
            assert orphan.name == hobbies_display_name  # snapshot survives delete
            assert db.session.get(Transaction, txn_id).category_id is None

            # The orphan still holds +50 and still reconciles -- by linkage.
            assert _ledger_account_sum(
                orphan_id, scenario_id,
            ) == Decimal("50.00")
            # A category_id match would find NO counter account for the now-NULL
            # transaction (proving why the linkage formulation is required).
            assert (
                db.session.query(LedgerAccount)
                .filter_by(
                    user_id=user_id, account_id=None, category_id=hobbies_id,
                )
                .count()
            ) == 0
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 3 + 4. Per-entry balance and global trial balance
# ---------------------------------------------------------------------------


class TestPerEntryAndTrialBalance:
    """Every entry sums to zero with >= 2 legs; the whole ledger sums to zero."""

    def test_every_entry_balances_and_trial_balance_is_zero(
        self, app, db, seed_user,
    ):
        """A transfer plus a cash expense and income each post a balanced entry.

        Arithmetic: one $100 transfer (Checking -> Savings), one $50 Groceries
        expense, and one $2000 Salary income each post a single two-leg entry
        summing to zero -- and the Step-5 openings (Checking +1000/-1000,
        Savings +100/-100 against their equity twins) are balanced pairs too
        -- so no entry violates ``SUM = 0`` / ``COUNT >= 2`` and the
        whole-ledger total stays 0.00.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Entry Savings",
            )
            db.session.commit()
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings, period,
                amount=Decimal("100.00"),
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("2000.00"),
                is_income=True, category=seed_user["categories"]["Salary"],
            )
            db.session.commit()

            # Three settled sources -> three source-linked balanced entries
            # (the Step-5 openings carry their own correction sources).
            assert (
                _db.session.query(JournalEntry)
                .filter(_db.or_(
                    JournalEntry.transfer_id.isnot(None),
                    JournalEntry.transaction_id.isnot(None),
                ))
                .count()
            ) == 3
            assert _entries_violating_balance() == []
            assert _trial_balance() == Decimal("0.00")


# ---------------------------------------------------------------------------
# 5. Per-transaction completeness (no settled cash row is silently unposted)
# ---------------------------------------------------------------------------


class TestEverySettledTransactionPosts:
    """Every settled, nonzero-effect cash transaction posts at least one entry."""

    def test_no_settled_transaction_is_silently_unposted(
        self, app, db, seed_user,
    ):
        """Two posted expenses post entries; a zero-effect envelope posts none.

        Two settled expenses ($50 Groceries, $40 Rent) each post one entry; an
        all-credit "envelope" (a $75 actual with a single $75 credit entry,
        effect = 75 - 75 = 0) posts nothing and is correctly NOT flagged as
        unposted.  The completeness sweep requires an entry for every settled
        nonzero-effect row and excludes the zero-effect one.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            period = seed_user["bootstrap_period"]

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("40.00"),
                category=seed_user["categories"]["Rent"],
            )
            # An all-credit row: settled, nonzero amount, but zero cash effect.
            all_credit = add_txn(
                db.session, seed_user, period, "All Credit", "75.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_amount="75.00",
            )
            db.session.add(TransactionEntry(
                transaction_id=all_credit.id, account_id=all_credit.account_id, user_id=user_id,
                amount=Decimal("75.00"), description="cc purchase",
                purchased_on=period.start_date, is_credit=True,
            ))
            db.session.commit()
            all_credit_id = all_credit.id

            _assert_every_settled_transaction_posts(user_id)
            # The zero-effect row posted nothing (no silent spurious entry).
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=all_credit_id)
                .count()
            ) == 0
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 6. Multi-scenario and owner isolation
# ---------------------------------------------------------------------------


class TestMultiScenarioIsolation:
    """Postings in one scenario never reconcile against another scenario."""

    def test_cash_postings_are_isolated_per_scenario(
        self, app, db, seed_user,
    ):
        """A $100 baseline and a $70 what-if expense never bleed together.

        Arithmetic: a $100 Groceries expense in the baseline scenario and a $70
        Groceries expense in a separate what-if scenario, both on Checking --
        each scenario also carrying Checking's $1000.00 opening (posted in
        the baseline at fixture time, into the what-if by the effect-time
        self-heal alongside its settle).  Scoped to baseline the Checking
        ledger is 1000 - 100 = 900.00 (NOT 830) and the Groceries-Expense
        counter +100.00; scoped to the what-if they are 1000 - 70 = 930.00
        and +70.00.  The ``scenario_id`` denorm keeps the two apart, and
        each scenario reconciles independently.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            baseline = seed_user["scenario"]
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"], scenario=baseline,
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("70.00"),
                category=seed_user["categories"]["Groceries"], scenario=whatif,
            )
            db.session.commit()

            # Checking: opening - 100 in baseline, opening - 70 in the
            # what-if -- never the cross-scenario 830.
            assert _independent_ledger_sum(
                checking.id, baseline.id,
            ) == Decimal("900.00")
            assert _independent_ledger_sum(
                checking.id, whatif.id,
            ) == Decimal("930.00")
            assert posting_service.account_posting_total(
                checking.id, baseline.id,
            ) == Decimal("900.00")
            assert posting_service.account_posting_total(
                checking.id, whatif.id,
            ) == Decimal("930.00")

            # The shared Groceries-Expense counter splits per scenario: +100 / +70.
            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                groceries_counter, baseline.id,
            ) == Decimal("100.00")
            assert _ledger_account_sum(
                groceries_counter, whatif.id,
            ) == Decimal("70.00")

            _assert_full_reconciliation(baseline.id)
            _assert_full_reconciliation(whatif.id)


class TestOwnerIsolationViaJournalEntry:
    """A posting's owner is its journal entry's; owners never cross-contaminate."""

    def test_two_owners_reconcile_independently_and_posting_has_no_user_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two independent owners settle cash; neither sees the other's.

        Arithmetic: owner 1 settles a $100 Groceries expense on their Checking
        ($1000.00 opening); owner 2 settles a $200 Groceries expense on theirs
        ($2000.00 opening).  Owner 1's Checking ledger is 1000 - 100 = 900.00
        and owner 2's is 2000 - 200 = 1800.00 with no leakage.  Every
        journal entry's ``user_id`` matches its account owner, a ``Posting``
        carries no ``user_id`` of its own (its owner is reachable only via
        ``Posting.journal_entry.user_id``), and each owner's books reconcile in
        their own baseline scenario.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            create_settled_cash_transaction(
                seed_second_user, db.session,
                seed_second_user["bootstrap_period"], Decimal("200.00"),
                category=seed_second_user["categories"]["Groceries"],
            )
            db.session.commit()

            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            checking1 = seed_user["account"].id
            checking2 = seed_second_user["account"].id
            # No leakage: each owner's Checking ledger holds only their own.
            assert _independent_ledger_sum(
                checking1, scenario1,
            ) == Decimal("900.00")
            assert _independent_ledger_sum(
                checking2, scenario2,
            ) == Decimal("1800.00")

            # A Posting has no user_id; ownership is normalized onto the entry.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            for posting in _db.session.query(Posting).all():
                entry_owner = posting.journal_entry.user_id
                assert entry_owner in (owner1_id, owner2_id)
                # The leg's ledger account (cash or category) belongs to the
                # same owner as its journal entry -- the normalization holds.
                assert posting.ledger_account.user_id == entry_owner
            for entry in _db.session.query(JournalEntry).all():
                assert entry.user_id in (owner1_id, owner2_id)

            _assert_full_reconciliation(scenario1)
            _assert_full_reconciliation(scenario2)
# ---------------------------------------------------------------------------
# THE BACKFILL-VS-GO-FORWARD CASE WAS DELETED AT PLAN STEP X-f1
# ---------------------------------------------------------------------------
#
# ``TestBackfillAndGoForwardAgree`` drove the historical migration's frozen raw
# SQL, which reads a ``paid_at`` column migration ``a3f7c8e21b64`` DROPS.  It is
# the same class the developer ruled on for the two dedicated backfill suites
# (2026-08-03): the path is UNREACHABLE with data -- the migration runs only at
# its own point in the chain, long before the drop, over an empty table, and
# ``a3f7c8e21b64``'s downgrade REFUSES so Alembic cannot rewind past the drop --
# so the case graded a producer that can never run again against one that runs
# every day.  What it asserted, and what the rest of this oracle still asserts
# without it: the two producers post leg-for-leg identical entries for one
# settled source, and the ledger reconciles either way.  The GO-FORWARD half of
# that is covered by every other case here; the historical half has no future.


# ---------------------------------------------------------------------------
# 8. Revert-and-recategorize regression lock (plan Section 2.8 CRITICAL)
# ---------------------------------------------------------------------------


class TestRevertAndRecategorizeReconciles:
    """A revert+recategorize PATCH keeps the whole-ledger sweep reconciled."""

    @pytest.mark.server_clock
    def test_revert_recategorize_resettle_reconciles_full_sweep(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Settle in A, revert+recategorize to B in one PATCH, re-settle; sweep.

        The route-level 2.8 CRITICAL: a Paid Groceries expense (posted to the
        Groceries-Expense counter) is reverted to Projected AND recategorized to
        Rent in ONE PATCH (the lock lifts on the revert), then re-settled.  The
        reconcile reverses the OLD counter read from the ledger, so Groceries nets
        to zero and Rent carries the expense.  Beyond the per-account check the
        Commit-6 lifecycle test makes, this asserts the COUNTER-ACCOUNT LINKAGE
        SWEEP reconciles after the move -- proving the linkage formula excludes
        the net-zero Groceries reversal pair and attributes the effect to Rent.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods_today[0], "Groceries",
                "50.00", category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            # Settle (posts to Groceries-Expense), then revert+recategorize to
            # Rent in one PATCH, then re-settle (posts to Rent-Expense).
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "category_id": str(rent_id),
                },
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            ).status_code == 200

            # Rent now carries the expense; Groceries netted to zero; the whole
            # ledger -- linked, counter (by linkage), per-entry, trial -- ties.
            rent_counter = _counter_ledger_id(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE, rent_id,
            )
            groceries_counter = _counter_ledger_id(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                rent_counter, scenario_id,
            ) == Decimal("50.00")
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            _assert_full_reconciliation(scenario_id)


class TestRevertAndMoveReconciles:
    """A revert+move PATCH keeps every period's ledger attribution intact."""

    @pytest.mark.server_clock
    def test_revert_move_resettle_attributes_per_period(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Settle in P, revert+move to F in one PATCH, re-settle; per-period ties.

        The route-level R2 regression (the 2026-07-02 adversarial review's H1
        class): a Paid $50 expense in period P is reverted to Projected AND
        moved to a future period F in ONE PATCH (the finalised lock lifts on
        the revert), then re-settled.  The handler applies the new
        ``pay_period_id`` BEFORE the end-of-handler reconcile, so a reversal
        stamped with the row's current period would land in F -- leaving P's
        entry and its reversal straddling two periods, where truncating F
        CASCADE-deletes half the pair and permanently strands the other
        (``transaction_id`` SET NULL, unhealable).  Under the R2 attribution
        rule the reversal lands in P: P's entries net to zero per ledger
        account, F carries exactly the re-settled -50/+50, and the whole
        sweep ties after every step.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            original = seed_periods_today[0]
            moved_to = seed_periods_today[5]
            txn = add_txn(
                db.session, seed_user, original, "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            # Revert AND move in one PATCH (the H1 flow).
            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "pay_period_id": str(moved_to.id),
                },
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)
            # The reversal landed in the ORIGINAL period: P nets to zero per
            # ledger account, and F holds no entries at all yet.
            assert _period_ledger_nets(txn_id, original.id) == {}
            assert not _entry_ids_in_period(txn_id, moved_to.id)

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            ).status_code == 200
            _assert_full_reconciliation(scenario_id)

            # F carries exactly the re-settled split; P still nets to zero.
            # (LINKED-kind lookup: a bare-account_id .scalar() would raise
            # MultipleResultsFound beside the Step-5 anchor-equity twin.)
            cash_ledger = linked_ledger_account(
                _db.session, seed_user["account"].id,
            ).id
            moved_nets = _period_ledger_nets(txn_id, moved_to.id)
            assert moved_nets[cash_ledger] == Decimal("-50.00")
            assert sum(moved_nets.values()) == Decimal("0.00")
            assert _period_ledger_nets(txn_id, original.id) == {}


def _period_ledger_nets(transaction_id, pay_period_id):
    """Return ``{ledger_account_id: net}`` for one transaction in one period.

    Zero nets are dropped, so a period whose entries fully cancel (an
    original + its reversal) returns ``{}`` -- the R2 attribution tests'
    "nets to zero per ledger account" shape.  Independent of the service's
    own per-period reader (a direct grouped query).
    """
    rows = (
        _db.session.query(
            Posting.ledger_account_id, _db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.transaction_id == transaction_id,
            JournalEntry.pay_period_id == pay_period_id,
        )
        .group_by(Posting.ledger_account_id)
        .all()
    )
    return {
        ledger_id: net for ledger_id, net in rows if net != 0
    }


def _entry_ids_in_period(transaction_id, pay_period_id):
    """Return the journal-entry ids a transaction holds in one period."""
    return [
        entry_id for (entry_id,) in (
            _db.session.query(JournalEntry.id)
            .filter(
                JournalEntry.transaction_id == transaction_id,
                JournalEntry.pay_period_id == pay_period_id,
            )
            .all()
        )
    ]


# ---------------------------------------------------------------------------
# Reverted transaction reconciles at zero (append-only correction discipline)
# ---------------------------------------------------------------------------


class TestRevertedTransactionReconcilesAtZero:
    """A settled-then-reverted cash transaction reconciles to zero both sides."""

    def test_reverted_transaction_reconciles_at_zero(
        self, app, db, seed_user,
    ):
        """Settle +50 expense, revert; the ledger nets to zero and ties.

        Arithmetic: a $50 Groceries expense posts -50 / +50, then a revert to
        Projected reconciles a -50 / +50 reversal (append-only).
        Groceries-Expense nets to zero and Checking lands back on its
        $1000.00 opening, the reverted row is no longer ``is_settled`` so it
        drops from the source effect too, and two entries survive (the
        original is never edited).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("50.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            txn_id = txn.id
            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("50.00")

            # Revert to Projected via the real primitives (seam, then reconcile).
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            posting_service.sync_transaction_postings(txn, settled=False)
            db.session.commit()

            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            # Two entries survive (settle + reversal); neither was edited.
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn_id)
                .count()
            ) == 2
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# A PURCHASE is a posting source of its own (plan step X-f3b, ruling R-FM)
# ---------------------------------------------------------------------------


class TestAPostedPurchaseReconcilesUnderAnUnsettledParent:
    """The sweep must describe the ledger a posted purchase produces.

    **The whole file was blind to this shape until plan step X-f3b**, and that
    is the finding it was added for: no test here creates a ``settled_on`` on a
    purchase, so both sweep assertions could go on passing while the ledger held
    legs neither of them could account for -- a linked total no ``effective``
    figure contains, and a counter leg carrying ``transaction_id = NULL`` that
    the hard-delete branch would have called a failed reversal.
    """

    def test_a_projected_envelopes_posted_purchase_reconciles_both_sweeps(
        self, app, db, seed_user,
    ):
        """A $40 purchase under a $100 Projected envelope, taken by the bank.

        Arithmetic: the envelope has not settled, so it books nothing of its
        own; the purchase books ``Checking -40.00 / Groceries-Expense +40.00``
        on the day the bank took it.  Checking's linked ledger is therefore
        ``1000.00 - 40.00 = 960.00`` and the Groceries counter is ``+40.00``.

        Both figures are asserted directly AND the full sweep is run, because
        the sweep is what 15 other tests rely on: its linked arm needs the third
        source term (`_independent_posted_purchase_effect`) and its counter arm
        needs a purchase leg resolved to its parent, and neither is exercised by
        any other test in this file.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            # The purchase is dated AFTER the account's opening assertion, which
            # is this whole file's stated precondition ("every settle in this
            # suite is stamped after the origination assertion, so the effects
            # ride on top of the opening").  Dated on or before it, the opening's
            # own correction would absorb the leg and the ledger would land on
            # the asserted balance instead -- true, and a different invariant
            # from the one these sweeps grade.
            posted_on = _db.session.query(
                _db.func.max(AccountAnchorHistory.observed_on),
            ).filter(
                AccountAnchorHistory.account_id == checking.id,
            ).scalar() + timedelta(days=1)
            txn = create_envelope_txn(
                seed_user, db.session, period, "Groceries", Decimal("100.00"),
            )
            txn.category_id = seed_user["categories"]["Groceries"].id
            entry = TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id,
                user_id=user_id,
                amount=Decimal("40.00"),
                description="Kroger",
                purchased_on=posted_on,
                settled_on=posted_on,
                is_credit=False,
            )
            _db.session.add(entry)
            _db.session.flush()
            posting_service.sync_transaction_postings(txn, settled=False)
            db.session.commit()

            groceries_counter = _counter_ledger_id(
                user_id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("960.00")
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("40.00")
            # The parent booked nothing of its own -- otherwise the $40.00
            # above could be its cash leg rather than the purchase's.
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn.id)
                .count()
            ) == 0
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_entry_id=entry.id)
                .count()
            ) == 1
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# Hard delete: SET-NULL'd legs reconcile via the transaction_id-NULL branch
# ---------------------------------------------------------------------------


class TestHardDeletedTransactionReconcilesAtZero:
    """A hard-deleted settled cash transaction leaves a balanced net-zero pair."""

    def test_hard_delete_settled_cash_reconciles_via_null_linkage(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Hard-deleting a Paid ad-hoc expense severs the link but stays balanced.

        Arithmetic: a $50 Groceries expense posts -50 / +50.  Deleting the ad-hoc
        (template-less) row hard-deletes it: the route reverses the postings FIRST
        (a -50 / +50 reversal), then the row is removed and
        ``journal_entries.transaction_id`` SET-NULLs on BOTH the original and the
        reversal (the immutable legs survive, append-only).  The Groceries-Expense
        counter therefore nets to zero across a ``transaction_id IS NULL`` group --
        the exact branch of the counter sweep that asserts a hard-deleted
        transaction's legs were reversed, not stranded.  This is the route-driven
        production path (``delete_transaction`` ->
        ``reverse_postings_before_delete`` -> FK SET NULL) the oracle claims to
        cover.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods_today[0], "Groceries",
                "50.00", category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            groceries_counter = _counter_ledger_id(
                seed_user["user"].id, LedgerAccountClassEnum.EXPENSE,
                seed_user["categories"]["Groceries"].id,
            )
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("50.00")

            # Hard delete (ad-hoc row): reverse-before-delete, then SET NULL.
            assert auth_client.delete(
                f"/transactions/{txn_id}",
            ).status_code == 200

            # The row is gone; both legs' transaction_id SET-NULLed; the counter
            # nets to zero across the NULL-linkage group.
            assert db.session.get(Transaction, txn_id) is None
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn_id)
                .count()
            ) == 0
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == Decimal("0.00")
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            # Drives the transaction_id-NULL branch of the counter sweep.
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    def test_per_account_reconciliation_catches_a_tampered_transaction(
        self, app, db, seed_user,
    ):
        """Tampering a settled expense's estimate makes ledger != source effect.

        A reconciled $100 Groceries expense has Checking ledger -100 and a source
        effect of -100.  Forcing the row's estimated amount to 999 via raw SQL
        (no actual override, so its effective becomes 999) leaves the ledger at
        -100 but pushes the source effect to -999 -- so the per-account
        reconciliation the oracle relies on now FAILS.  This proves the check is a
        real comparison, not one that passes unconditionally.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]

            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            txn_id = txn.id
            # Reconciled (absolutely) before tampering.
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == _opening_anchor(checking.id) + _independent_cash_txn_effect(
                checking.id, scenario_id,
            )

            # Tamper the RECORDED figure, not the estimate (plan step
            # X-au-c3): a settled row's effect is what it recorded as having
            # moved, and its plan is beside that rather than behind it -- so
            # moving the estimate on a settled row is now inert, which is the
            # substitution this step exists to remove.  Transactions carry no
            # balance trigger, so the tamper commits.
            db.session.execute(_db.text(
                "UPDATE budget.transactions SET settled_amount = 999 "
                "WHERE id = :i"
            ), {"i": txn_id})
            db.session.commit()

            ledger = _independent_ledger_sum(checking.id, scenario_id)
            effect = _independent_cash_txn_effect(checking.id, scenario_id)
            assert ledger == Decimal("900.00")  # ledger unchanged
            assert effect == Decimal("-999.00")  # transaction truth drifted
            # opening (1000) + effect (-999) != ledger (900): the drift shows.
            assert ledger != _opening_anchor(checking.id) + effect
            # Drive the REAL production-wide sweep helper (not just the inline
            # re-derivation above) so a regression that broke the helper itself --
            # e.g. one that stopped comparing the linked ledger to its source --
            # would fail here.  ``match`` pins the linked-account reconciliation
            # message specifically, so a future edit that weakened THAT comparison
            # but left the non-empty guard or trial balance firing under tamper no
            # longer keeps this test green -- the tooth cannot be lost undetected.
            with pytest.raises(AssertionError, match="combined source effect"):
                _assert_full_reconciliation(scenario_id)

    def test_trial_balance_catches_an_injected_leg(self, app, db, seed_user):
        """Injecting one extra leg pushes the trial balance off zero.

        A balanced book has trial balance 0.00.  Inserting one unmatched +50 leg
        (raw SQL, flushed but never committed so the deferred per-entry trigger
        never fires) makes the whole-ledger sum 0 + 50 = 50.00 -- so the
        trial-balance ``= 0`` assertion is a real check, not one the per-entry
        trigger makes vacuously true.  Rolled back so the leg never lands.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto the transaction's entry
            # (picked by its link -- the Step-5 openings mean several entries
            # exist).  Flush (not commit) makes it visible; the DEFERRED
            # balanced trigger validates only at COMMIT, which we never reach.
            entry_id = (
                _db.session.query(JournalEntry.id)
                .filter_by(transaction_id=txn.id)
                .scalar()
            )
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked_ledger_account(
                    _db.session, seed_user["account"].id,
                ).id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            })
            _db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()
