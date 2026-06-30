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
     ``effective = COALESCE(actual, estimated)``.
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
  7. **Backfill == go-forward.**  A transaction posted by the ``posting_service``
     Python builder and the same transaction posted by the raw-SQL Commit-7
     migration backfill produce identical legs and reconcile identically.
  8. **Revert-and-recategorize reconciles (the plan Section 2.8 CRITICAL
     regression lock)** -- driven through the real PATCH route, then swept.

Two adversarial cases prove the oracle is not vacuous: tampering a settled
transaction's estimate makes the per-account reconciliation FAIL (a real ledger
drift would be caught), and injecting one extra leg makes the trial balance go
non-zero (the ``= 0`` assertion is a real check, not one the per-entry trigger
makes unconditionally true).  A reverted transaction reconciles at zero (original
+ reversal net to zero; the source-side query drops it once it is no longer
settled), proving the append-only correction discipline end to end.

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

from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db as _db
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service, posting_service, status_seam
from app.utils.balance_predicates import settled_status_ids
from tests._test_helpers import (
    add_txn,
    clear_postings_for_transaction,
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    ledger_accounts_for_account,
    load_migration_module,
)


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
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


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
        Transaction.actual_amount, Transaction.estimated_amount
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
        Transaction.actual_amount, Transaction.estimated_amount
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


def _independent_combined_source_effect(
    account_id: int, scenario_id: int
) -> Decimal:
    """Sum an account's combined settled transfer + transaction source effect.

    The full balance-side truth a linked account's ledger must equal in Step 3:
    transfer shadows AND ordinary transactions, both signed debit-positive.  The
    independent restatement of
    ``settled_transfer_effect + settled_transaction_effect``.
    """
    return (
        _independent_transfer_shadow_effect(account_id, scenario_id)
        + _independent_cash_txn_effect(account_id, scenario_id)
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
    effect = txn.effective_amount - credit_sum
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
    """Assert every LINKED ledger account reconciles in *scenario_id*.

    For each real account (its linked ledger account), the independent ledger sum
    equals the independent combined (transfer + transaction) source effect.
    Holds over every linked account that has postings, not only the ones a given
    test hand-computes.
    """
    linked = (
        _db.session.query(LedgerAccount)
        .filter(LedgerAccount.account_id.isnot(None))
        .all()
    )
    for ledger_account in linked:
        ledger = _independent_ledger_sum(ledger_account.account_id, scenario_id)
        effect = _independent_combined_source_effect(
            ledger_account.account_id, scenario_id
        )
        assert ledger == effect, (
            f"account {ledger_account.account_id}: ledger {ledger} != "
            f"combined source effect {effect} in scenario {scenario_id}"
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
        rows = (
            _db.session.query(
                JournalEntry.transaction_id,
                _db.func.sum(Posting.amount),
            )
            .select_from(Posting)
            .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
            .filter(
                Posting.ledger_account_id == counter.id,
                JournalEntry.scenario_id == scenario_id,
            )
            .group_by(JournalEntry.transaction_id)
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
            # A non-zero net on a counter account can only come from a settled,
            # active, non-transfer transaction (everything else nets to zero).
            assert txn.transfer_id is None
            assert txn.is_deleted is False
            assert txn.status.is_settled
            expected_counter = -_signed_cash_effect(txn)
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


# The Commit-7 backfill migration module, loaded once so its idempotent
# ``_backfill_settled_transactions`` raw-SQL builder can be invoked directly (the
# historical producer), the same pattern the Step-2 oracle and the backfill suite
# use.
_BACKFILL_MIGRATION = load_migration_module(
    "7d63529e4300_backfill_historical_cash_postings.py"
)


# ---------------------------------------------------------------------------
# 1. Per linked account: transfer + transaction legs combine and reconcile
# ---------------------------------------------------------------------------


class TestPerLinkedAccountReconciliation:
    """A linked account's ledger sums its transfer AND transaction legs."""

    def test_combined_transfer_and_cash_legs_reconcile_three_ways(
        self, app, db, seed_user,
    ):
        """Checking reconciles over $350 of transfers plus $1,950 of cash.

        Arithmetic (baseline scenario), all on Checking:
          - transfer Checking -> Savings   $100  -> Checking -100, Savings +100
          - transfer Checking -> Mortgage  $250  -> Checking -250, Mortgage +250
          - cash EXPENSE $50 Groceries           -> Checking  -50, Groceries +50
          - cash INCOME  $2000 Salary            -> Checking +2000, Salary  -2000

        Checking ledger = -100 -250 -50 +2000 = +1600.00; its transfer effect is
        -350 and its transaction effect is +1950, and -350 + 1950 = +1600.
        Savings +100, Mortgage +250.  All three independent computations -- the
        hand-computed literal, the independent cross-table query, and the
        production service helpers -- must agree on every account.
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
                checking.id: Decimal("1600.00"),
                savings.id: Decimal("100.00"),
                mortgage.id: Decimal("250.00"),
            }
            for account_id, want in expected.items():
                # (a) hand-computed literal == independent ledger-table query.
                assert _independent_ledger_sum(account_id, scenario_id) == want
                # (b) independent ledger query == independent source query.
                assert _independent_combined_source_effect(
                    account_id, scenario_id,
                ) == want
                # (c) the production service helpers agree too.
                assert posting_service.account_posting_total(
                    account_id, scenario_id,
                ) == want
                assert (
                    posting_service.settled_transfer_effect(
                        account_id, scenario_id,
                    )
                    + posting_service.settled_transaction_effect(
                        account_id, scenario_id,
                    )
                ) == want

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
        summing to zero, so no entry violates ``SUM = 0`` / ``COUNT >= 2``, and
        the whole-ledger total is (-100 +100) + (-50 +50) + (+2000 -2000) = 0.00.
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

            # Three settled sources -> three balanced entries.
            assert _db.session.query(JournalEntry).count() == 3
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
                actual_amount="75.00",
            )
            db.session.add(TransactionEntry(
                transaction_id=all_credit.id, user_id=user_id,
                amount=Decimal("75.00"), description="cc purchase",
                entry_date=period.start_date, is_credit=True,
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
        Groceries expense in a separate what-if scenario, both on Checking.
        Scoped to baseline the Checking ledger is -100.00 (NOT -170) and the
        Groceries-Expense counter +100.00; scoped to the what-if they are -70.00
        and +70.00.  The ``scenario_id`` denorm keeps the two apart, and each
        scenario reconciles independently.
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

            # Checking: -100 in baseline, -70 in the what-if -- never -170.
            assert _independent_ledger_sum(
                checking.id, baseline.id,
            ) == Decimal("-100.00")
            assert _independent_ledger_sum(
                checking.id, whatif.id,
            ) == Decimal("-70.00")
            assert posting_service.account_posting_total(
                checking.id, baseline.id,
            ) == Decimal("-100.00")
            assert posting_service.account_posting_total(
                checking.id, whatif.id,
            ) == Decimal("-70.00")

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

        Arithmetic: owner 1 settles a $100 Groceries expense on their Checking;
        owner 2 settles a $200 Groceries expense on theirs.  Owner 1's Checking
        ledger is -100.00 and owner 2's is -200.00 with no leakage.  Every
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
            ) == Decimal("-100.00")
            assert _independent_ledger_sum(
                checking2, scenario2,
            ) == Decimal("-200.00")

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
# 7. Backfilled vs go-forward postings reconcile identically
# ---------------------------------------------------------------------------


class TestBackfillAndGoForwardAgree:
    """The raw-SQL backfill and the posting_service builder produce equal legs."""

    def test_same_transaction_posts_identically_both_ways(
        self, app, db, seed_user,
    ):
        """A cash expense posted go-forward then re-posted by the backfill matches.

        Arithmetic: a $50 Groceries expense posts -50 / +50 go-forward (the
        ``posting_service`` Python builder).  Clearing those legs to the
        pre-ledger state and running the migration's raw-SQL backfill re-posts the
        SAME -50 / +50.  Asserting the two are equal leg-for-leg catches any
        divergence between the producers, and the oracle reconciles identically
        over the backfilled postings.
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

            # Capture the go-forward legs.
            forward_checking = _independent_ledger_sum(checking.id, scenario_id)
            forward_counter = _ledger_account_sum(groceries_counter, scenario_id)
            assert forward_checking == Decimal("-50.00")
            assert forward_counter == Decimal("50.00")

            # Clear to the pre-ledger historical state and re-post via the
            # migration's raw-SQL backfill (the historical producer).  The
            # Groceries-Expense counter account survives the clear; the backfill
            # reuses it (ON CONFLICT) rather than making a second.
            clear_postings_for_transaction(txn_id)
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("0.00")  # cleared
            posted = _BACKFILL_MIGRATION._backfill_settled_transactions(
                db.session,
            )
            db.session.commit()
            assert posted == [txn_id]

            # The backfilled net equals the go-forward net, account for account.
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == forward_checking
            assert _ledger_account_sum(
                groceries_counter, scenario_id,
            ) == forward_counter
            # Exactly one balanced entry was re-posted for the transaction.
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transaction_id=txn_id)
                .count()
            ) == 1
            assert _entries_violating_balance() == []
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# 8. Revert-and-recategorize regression lock (plan Section 2.8 CRITICAL)
# ---------------------------------------------------------------------------


class TestRevertAndRecategorizeReconciles:
    """A revert+recategorize PATCH keeps the whole-ledger sweep reconciled."""

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
        Projected reconciles a -50 / +50 reversal (append-only).  Checking and
        Groceries-Expense each net to zero, the reverted row is no longer
        ``is_settled`` so it drops from the source effect too, and two entries
        survive (the original is never edited).
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
            )
            posting_service.sync_transaction_postings(txn, settled=False)
            db.session.commit()

            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == Decimal("0.00")
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
            ) == Decimal("0.00")
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
            # Reconciled before tampering.
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == _independent_cash_txn_effect(checking.id, scenario_id)

            # Tamper the estimate (transactions carry no balance trigger, so this
            # commits); with no actual, effective becomes 999.
            db.session.execute(_db.text(
                "UPDATE budget.transactions SET estimated_amount = 999 "
                "WHERE id = :i"
            ), {"i": txn_id})
            db.session.commit()

            ledger = _independent_ledger_sum(checking.id, scenario_id)
            effect = _independent_cash_txn_effect(checking.id, scenario_id)
            assert ledger == Decimal("-100.00")  # ledger unchanged
            assert effect == Decimal("-999.00")  # transaction truth drifted
            assert ledger != effect  # the oracle would catch this drift

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
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto an existing entry.  Flush (not
            # commit) makes it visible; the DEFERRED balanced trigger validates
            # only at COMMIT, which we never reach.
            entry_id = _db.session.query(JournalEntry.id).scalar()
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": ledger_accounts_for_account(
                    _db.session, seed_user["account"].id,
                )[0].id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            })
            _db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()
