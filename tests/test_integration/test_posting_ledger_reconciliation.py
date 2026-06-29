"""The posting-ledger reconciliation oracle (Build-Order Step 2, Commit 6).

This is the correctness gate for the double-entry posting ledger.  Reads stay
on the ``balance_at`` seam over ``budget.transactions`` (Step 2 changes no read
path), so the ledger is validated against the **settled-transfer subset** of
transactions rather than against full projected balances.  The invariants
below are exactly plan Section 6:

  1. **Per-account reconciliation** (asset AND liability legs): for each real
     account A, the net of A's posting legs equals the net effect of A's
     settled, non-deleted transfer shadows -- ``+effective`` for an income
     shadow (money in -> a debit), ``-effective`` for an expense shadow (money
     out -> a credit), where ``effective = COALESCE(actual, estimated)``.
  2. **Per-entry balance**: every journal entry's legs ``SUM(amount) = 0`` and
     ``COUNT(*) >= 2`` (also DB-enforced by ``ck_account_postings_balanced``).
  3. **Trial balance**: ``SUM(account_postings.amount) = 0`` across the whole
     ledger (follows from 2, asserted directly as a cheap self-check).
  4. **Multi-scenario isolation**: postings in scenario X never reconcile
     against transactions in scenario Y (the ``scenario_id`` denorm is honored).
  5. **Owner isolation via ``journal_entry.user_id``**: a posting carries no
     ``user_id`` of its own; its owner is reached only through its journal
     entry, and one owner's reconciliation never picks up another owner's
     postings.  (Plan Section 6 calls this the "companion-owner case".  A
     companion user cannot in fact OWN a transfer -- ``transfer_service``
     refuses any actor that does not own the from/to accounts, and a companion
     owns none -- so the property the plan actually names, ownership inherited
     via ``journal_entry.user_id``, is proven here with a second independent
     owner, which exercises the identical join.)

The oracle also holds over BOTH historical-backfilled postings (the raw-SQL
migration builder) and go-forward postings (the ``posting_service`` Python
builder): a dedicated case posts one transfer each way and asserts the two
producers agree leg-for-leg and reconcile identically.

**Non-tautological by construction.**  Each invariant is checked three
independent ways that must all agree:

  * **hand-computed literals** -- the expected ledger sums are the test
    author's arithmetic over the seeded transfer amounts (e.g. Checking sends
    $100 + $250, so its ledger MUST be exactly ``-350.00``), owing nothing to
    either producer or either service helper;
  * **independent cross-table reconciliation** -- the ledger side
    (``_independent_ledger_sum``) reads the ``account_postings`` table through a
    different join shape than ``account_posting_total``, and the transaction
    side (``_independent_txn_effect``) reads the ``transactions`` table;
    asserting the two equal reconciles what the producers WROTE against the
    transaction source of truth.  (The transaction side necessarily restates the
    one correct definition of the settled-shadow effect, so it mirrors
    ``settled_transfer_effect``'s semantics; the hand-computed literals, not this
    query, are what make the oracle non-tautological -- this layer adds the
    cross-table, whole-DB sweep the literals cannot.)
  * **the production service helpers** -- ``account_posting_total`` and
    ``settled_transfer_effect`` (the readers Steps 4-5 will switch balances
    onto) must match the hand-computed literals too.

Two adversarial cases prove the oracle is not vacuous: tampering a settled
shadow makes the per-account reconciliation FAIL (so a real ledger drift would
be caught), and injecting one extra leg makes the trial balance go non-zero
(so the ``= 0`` assertion is a real check, not one the per-entry trigger makes
unconditionally true).

All money is ``Decimal`` from strings, with the arithmetic shown per the
testing standard.  Transfers are built through ``transfer_service`` (the sole
writer), so every shadow obeys the transfer invariants exactly as production
produces them; Commit 5 wiring auto-posts each settle.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import PostingKindEnum, StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import posting_service, transfer_service
from app.utils.balance_predicates import settled_status_ids
from tests._test_helpers import (
    clear_postings_for_transfer,
    create_account_of_type,
    create_settled_transfer,
    ledger_accounts_for_account,
    load_migration_module,
)


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These deliberately re-derive each side from scratch so the oracle is a
# genuine second opinion: a bug shared by the two service helpers cannot hide,
# because the ledger side here reads ``account_postings`` and the transaction
# side reads ``transactions`` with independently-written SQL, and both are also
# pinned to hand-computed literals.


def _independent_ledger_sum(account_id: int, scenario_id: int) -> Decimal:
    """Sum a real account's posting legs in a scenario (independent query).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` (for the real ``account_id``), summing the signed
    ``amount``.  Keyed off the REAL account via ``ledger_accounts.account_id``,
    a different join shape than ``posting_service.account_posting_total`` (which
    resolves the ledger account first), so the two cannot share a lookup bug.
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


def _independent_txn_effect(account_id: int, scenario_id: int) -> Decimal:
    """Sum an account's settled transfer-shadow effect (independent query).

    The balance-side truth the ledger must equal: over the account's settled
    (``status.is_settled``), non-deleted transfer shadows in *scenario_id*, add
    ``+effective`` for an income shadow (money in) and ``-effective`` for an
    expense shadow (money out), where ``effective = COALESCE(actual,
    estimated)``.  Reads the ``transactions`` table -- a different table than
    :func:`_independent_ledger_sum` -- so asserting the two equal reconciles
    what the producers wrote against the transaction source of truth.
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

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.
    Any row returned here is a violation -- the per-entry invariant the
    deferred trigger also enforces, re-checked from the ORM side.
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


def _assert_full_reconciliation(scenario_id: int) -> None:
    """Assert every linked ledger account reconciles in *scenario_id*.

    The production-wide sweep: for each real account (its linked ledger
    account), the independent ledger sum equals the independent settled-shadow
    effect.  Holds over every account that has postings, not only the ones a
    given test hand-computes.
    """
    linked = (
        _db.session.query(LedgerAccount)
        .filter(LedgerAccount.account_id.isnot(None))
        .all()
    )
    for ledger_account in linked:
        ledger = _independent_ledger_sum(ledger_account.account_id, scenario_id)
        effect = _independent_txn_effect(ledger_account.account_id, scenario_id)
        assert ledger == effect, (
            f"account {ledger_account.account_id}: ledger {ledger} != "
            f"settled-shadow effect {effect} in scenario {scenario_id}"
        )


def _legs_by_account(account_id: int) -> dict[int, Decimal]:
    """Return ``{journal_entry_id: leg_amount}`` for a real account's legs.

    Used to compare a transfer posted go-forward against the same transfer
    posted by the backfill: both must land the same signed amount on the
    account's ledger.
    """
    return {
        entry_id: amount
        for entry_id, amount in _db.session.query(
            Posting.journal_entry_id, Posting.amount
        )
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(LedgerAccount.account_id == account_id)
        .all()
    }


def _build_asset_and_liability_books(seed_user) -> tuple:
    """Settle Checking -> Savings $100 and Checking -> Mortgage $250 (committed).

    Assumes an app context is active.  Builds a Savings (asset) and a Mortgage
    (liability) destination so a caller can hand-check BOTH an asset leg and a
    liability leg, plus one unsettled (Projected) $40 transfer that must
    contribute nothing to either side.  Hand-computed ledger expectations in
    the baseline scenario:

        Checking  -100.00 - 250.00 = -350.00   (the $40 Projected posts nothing)
        Savings   +100.00
        Mortgage  +250.00
        trial     -350.00 + 100.00 + 250.00 = 0.00

    Returns ``(savings, mortgage)``.
    """
    checking = seed_user["account"]
    period = seed_user["bootstrap_period"]
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Oracle Savings",
    )
    mortgage = create_account_of_type(
        seed_user, _db.session, "Mortgage", "Oracle Mortgage",
    )
    _db.session.commit()

    create_settled_transfer(
        seed_user, _db.session, checking, savings, period,
        amount=Decimal("100.00"),
    )
    create_settled_transfer(
        seed_user, _db.session, checking, mortgage, period,
        amount=Decimal("250.00"),
    )
    # An unsettled (Projected) transfer: it must post nothing and drop out of
    # the settled-shadow effect, so neither side of the oracle sees it.
    transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=checking.id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount=Decimal("40.00"),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
        ),
    )
    _db.session.commit()
    return savings, mortgage


# The Commit-3 migration module, loaded once so its idempotent
# ``_backfill_settled_transfers`` raw-SQL builder can be invoked directly (the
# historical producer), the same pattern the backfill suite uses.
_BACKFILL_MIGRATION = load_migration_module(
    "db239773c2fd_create_journal_entries_account_postings_.py"
)


# ---------------------------------------------------------------------------
# 1. Per-account reconciliation (asset + liability legs)
# ---------------------------------------------------------------------------


class TestPerAccountReconciliation:
    """Each account's ledger sum equals its settled transfer-shadow effect."""

    def test_asset_and_liability_legs_reconcile_three_ways(
        self, app, db, seed_user,
    ):
        """Checking/Savings/Mortgage reconcile by hand, query, and helper.

        Arithmetic (baseline scenario): Checking sends $100 to Savings and $250
        to Mortgage, so its ledger nets -350.00; Savings receives +100.00;
        Mortgage (a liability paid down) receives +250.00.  The unsettled $40
        Projected transfer posts nothing.  All three independent computations
        -- hand-computed literal, independent cross-table query, and the
        production service helper -- must agree on every account.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings, mortgage = _build_asset_and_liability_books(seed_user)

            expected = {
                checking.id: Decimal("-350.00"),
                savings.id: Decimal("100.00"),
                mortgage.id: Decimal("250.00"),
            }
            for account_id, want in expected.items():
                # (a) hand-computed literal == independent ledger-table query.
                assert _independent_ledger_sum(account_id, scenario_id) == want
                # (b) independent ledger query == independent txn-table query
                #     (postings reconcile to the transaction source of truth).
                assert _independent_txn_effect(account_id, scenario_id) == want
                # (c) the production service helpers agree too (the readers
                #     Steps 4-5 will switch balances onto).
                assert posting_service.account_posting_total(
                    account_id, scenario_id,
                ) == want
                assert posting_service.settled_transfer_effect(
                    account_id, scenario_id,
                ) == want

            # Production-wide sweep: every linked ledger account reconciles.
            _assert_full_reconciliation(scenario_id)

    def test_reverted_transfer_reconciles_at_zero(self, app, db, seed_user):
        """A settled-then-reverted transfer reconciles to zero on both sides.

        Arithmetic: settle +100 (Savings), then revert.  The ledger nets to
        zero (+100 settle, -100 reversal -- append-only), and the reverted
        income shadow is no longer ``is_settled`` so it drops from the
        settled-shadow effect too.  Both sides read 0.00, and two entries
        survive (the original is never edited).  This is the append-only
        correction discipline proven through the oracle.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Revert Savings",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Revert to Projected (Commit-5 wiring auto-reverses the posting).
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()

            for account_id in (checking.id, savings.id):
                assert _independent_ledger_sum(
                    account_id, scenario_id,
                ) == Decimal("0.00")
                assert _independent_txn_effect(
                    account_id, scenario_id,
                ) == Decimal("0.00")
            # Two entries survive (settle + reversal); neither was edited.
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transfer_id=transfer.id)
                .count()
            ) == 2
            _assert_full_reconciliation(scenario_id)

    def test_divergent_actual_amount_reconciles_on_effective(
        self, app, db, seed_user,
    ):
        """A settled actual that differs from the estimate reconciles on actual.

        This exercises the ``effective = COALESCE(actual, estimated)`` property
        the WHOLE ledger correction rests on: the posted amount is the shadow's
        effective amount, never ``transfers.amount`` / the estimate.  The two
        diverge exactly when a settled shadow carries an ``actual_amount`` (the
        grid shadow-edit path), so without this case every other test -- where
        ``actual`` is NULL and ``effective == estimated == amount`` -- would
        stay green even against a producer that wrongly posted the estimate.

        Arithmetic: a $100 nominal Checking -> Savings transfer settles with an
        actual of $97.50, so the income shadow's effective is
        COALESCE(97.50, 100.00) = 97.50.  The posting MUST be -97.50 / +97.50,
        NOT -100 / +100, and every reconciliation side must read 97.50.  A
        producer that posted the $100 estimate would leave the ledger at +100
        while the settled-shadow effect is +97.50 -- a divergence this case
        catches and the others cannot.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Divergent Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                actual_amount=Decimal("97.50"),
            )
            _db.session.commit()

            # effective = COALESCE(97.50, 100.00) = 97.50 (actual over estimate),
            # NOT the $100 nominal amount.
            expected = {
                savings.id: Decimal("97.50"),
                checking.id: Decimal("-97.50"),
            }
            for account_id, want in expected.items():
                assert _independent_ledger_sum(account_id, scenario_id) == want
                assert _independent_txn_effect(account_id, scenario_id) == want
                assert posting_service.account_posting_total(
                    account_id, scenario_id,
                ) == want
                assert posting_service.settled_transfer_effect(
                    account_id, scenario_id,
                ) == want
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# Per-transfer completeness: no settled transfer is silently unposted
# ---------------------------------------------------------------------------


class TestEverySettledTransferPosts:
    """Every settled, non-deleted transfer posts at least one journal entry."""

    def test_no_settled_transfer_is_silently_unposted(
        self, app, db, seed_user,
    ):
        """Each settled transfer has >= 1 entry; none is silently skipped.

        The direct per-transfer backstop (the localized companion to the
        aggregate per-account reconciliation): the per-account check catches a
        SHORT account, but this pinpoints a settled transfer that posted
        NOTHING -- e.g. one whose account lacked a paired ledger account, which
        the historical backfill's INNER JOIN on ``ledger_accounts`` would
        silently skip.  The Commit-2 pairing guarantees coverage, so this holds
        in normal operation; the assertion is the production backstop the
        Commit-4 review asked the oracle to carry.

        Arithmetic: the two simple settles ($100 to Savings, $250 to Mortgage)
        each post exactly one entry, so the 2 settled non-deleted transfers map
        one-to-one onto 2 journal entries; the Projected $40 transfer is not
        settled and posts none.
        """
        with app.app_context():
            _build_asset_and_liability_books(seed_user)
            settled = (
                _db.session.query(Transfer)
                .filter(
                    Transfer.user_id == seed_user["user"].id,
                    Transfer.is_deleted.is_(False),
                    Transfer.status_id.in_(settled_status_ids()),
                )
                .all()
            )
            # Two settled transfers; the Projected $40 is excluded.
            assert len(settled) == 2
            for xfer in settled:
                entry_count = (
                    _db.session.query(JournalEntry)
                    .filter_by(transfer_id=xfer.id)
                    .count()
                )
                assert entry_count >= 1, (
                    f"settled transfer {xfer.id} posted no journal entry"
                )
            # Simple settles post exactly one entry each, so the settled
            # transfers and the journal entries are in bijection -- no skip
            # (an entry-less settled transfer) and no stray double-post.
            assert _db.session.query(JournalEntry).count() == len(settled)


# ---------------------------------------------------------------------------
# 2. Per-entry balance + 3. global trial balance
# ---------------------------------------------------------------------------


class TestPerEntryAndTrialBalance:
    """Every entry sums to zero with >= 2 legs; the whole ledger sums to zero."""

    def test_every_entry_balances_and_trial_balance_is_zero(
        self, app, db, seed_user,
    ):
        """Two settled transfers -> two balanced entries; trial balance 0.

        Arithmetic: the $100 and $250 settles each post a two-leg entry summing
        to zero, so no entry violates ``SUM = 0`` / ``COUNT >= 2``, and the
        whole-ledger total is -350 + 100 + 250 = 0.00.
        """
        with app.app_context():
            _build_asset_and_liability_books(seed_user)

            # Exactly the two settled transfers produced entries (the $40
            # Projected posted none).
            assert _db.session.query(JournalEntry).count() == 2
            # No entry violates the per-entry balanced invariant.
            assert _entries_violating_balance() == []
            # Whole-ledger trial balance is zero.
            assert _trial_balance() == Decimal("0.00")


# ---------------------------------------------------------------------------
# 4. Multi-scenario isolation
# ---------------------------------------------------------------------------


class TestMultiScenarioIsolation:
    """Postings in one scenario never reconcile against another scenario."""

    def test_postings_are_isolated_per_scenario(self, app, db, seed_user):
        """A $100 baseline and a $70 what-if transfer never bleed together.

        Arithmetic: Savings receives $100 in the baseline scenario and $70 in a
        separate what-if scenario.  Scoped to baseline the Savings ledger is
        +100.00 (NOT +170); scoped to the what-if it is +70.00.  The
        ``scenario_id`` denorm on the journal entry keeps the two apart, and
        each scenario reconciles independently.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            baseline = seed_user["scenario"]
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Scenario Savings",
            )
            # A second, non-baseline scenario for the same user (the partial
            # unique index permits only one baseline, so is_baseline=False).
            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            _db.session.add(whatif)
            _db.session.commit()

            create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                scenario=baseline,
            )
            create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("70.00"),
                scenario=whatif,
            )
            _db.session.commit()

            # Savings: +100 in baseline, +70 in the what-if -- never +170.
            assert _independent_ledger_sum(
                savings.id, baseline.id,
            ) == Decimal("100.00")
            assert _independent_ledger_sum(
                savings.id, whatif.id,
            ) == Decimal("70.00")
            # Checking mirrors: -100 baseline, -70 what-if.
            assert _independent_ledger_sum(
                checking.id, baseline.id,
            ) == Decimal("-100.00")
            assert _independent_ledger_sum(
                checking.id, whatif.id,
            ) == Decimal("-70.00")
            # The service helper agrees, and each scenario reconciles alone.
            assert posting_service.account_posting_total(
                savings.id, baseline.id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                savings.id, whatif.id,
            ) == Decimal("70.00")
            _assert_full_reconciliation(baseline.id)
            _assert_full_reconciliation(whatif.id)


# ---------------------------------------------------------------------------
# 5. Owner isolation via journal_entry.user_id (the "companion-owner" case)
# ---------------------------------------------------------------------------


class TestOwnerIsolationViaJournalEntry:
    """A posting's owner is its journal entry's; owners never cross-contaminate."""

    def test_two_owners_reconcile_independently_and_posting_has_no_user_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two independent owners settle transfers; neither sees the other's.

        Arithmetic: owner 1 settles $100 (their Checking -> their Savings);
        owner 2 settles $200 (their Checking -> their Savings).  Owner 1's
        Savings ledger is +100.00 and owner 2's is +200.00 with no leakage.
        Every journal entry's ``user_id`` matches its account owner, the leg's
        owner is reachable only via ``Posting.journal_entry.user_id`` (a
        ``Posting`` carries no ``user_id`` column), and each owner's books
        reconcile in their own baseline scenario.
        """
        with app.app_context():
            # Owner 1.
            savings1 = create_account_of_type(
                seed_user, _db.session, "Savings", "Owner1 Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings1,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            # Owner 2 (fully independent user, accounts, scenario).
            savings2 = create_account_of_type(
                seed_second_user, _db.session, "Savings", "Owner2 Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_second_user, _db.session,
                seed_second_user["account"], savings2,
                seed_second_user["bootstrap_period"], amount=Decimal("200.00"),
            )
            _db.session.commit()

            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            # No leakage: each owner's Savings ledger holds only their own.
            assert _independent_ledger_sum(
                savings1.id, scenario1,
            ) == Decimal("100.00")
            assert _independent_ledger_sum(
                savings2.id, scenario2,
            ) == Decimal("200.00")

            # A Posting has no user_id of its own (ownership is normalized onto
            # the journal entry); the owner is reached only via the entry.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            for posting in _db.session.query(Posting).all():
                entry_owner = posting.journal_entry.user_id
                assert entry_owner in (owner1_id, owner2_id)
                # The leg's ledger account belongs to the same owner as its
                # journal entry -- the normalization holds end to end.
                assert posting.ledger_account.user_id == entry_owner

            # Every entry's user_id matches the owner whose account it touches.
            for entry in _db.session.query(JournalEntry).all():
                assert entry.user_id in (owner1_id, owner2_id)

            _assert_full_reconciliation(scenario1)
            _assert_full_reconciliation(scenario2)


# ---------------------------------------------------------------------------
# Backfilled vs go-forward postings reconcile identically
# ---------------------------------------------------------------------------


class TestBackfillAndGoForwardAgree:
    """The raw-SQL backfill and the posting_service builder produce equal legs."""

    def test_same_transfer_posts_identically_both_ways(
        self, app, db, seed_user,
    ):
        """A transfer posted go-forward then re-posted by the backfill matches.

        Arithmetic: a $100 Checking -> Savings settle posts -100 / +100
        go-forward (the ``posting_service`` Python builder).  Clearing those
        legs to the pre-ledger state and running the migration's raw-SQL
        backfill re-posts the SAME -100 / +100.  Asserting the two are equal
        leg-for-leg catches any divergence between the two producers, and the
        oracle reconciles identically over the backfilled postings.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Backfill-vs-Forward Savings",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            # Capture the go-forward legs on each account's ledger.
            forward_savings = _independent_ledger_sum(savings.id, scenario_id)
            forward_checking = _independent_ledger_sum(checking.id, scenario_id)
            assert forward_savings == Decimal("100.00")
            assert forward_checking == Decimal("-100.00")

            # Clear to the pre-ledger historical state and re-post via the
            # migration's raw-SQL backfill (the historical producer).
            clear_postings_for_transfer(transfer.id)
            assert _independent_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("0.00")  # cleared
            posted = _BACKFILL_MIGRATION._backfill_settled_transfers(_db.session)
            _db.session.commit()
            assert posted == [transfer.id]

            # The backfilled net equals the go-forward net, account for
            # account, and the oracle reconciles over the backfilled postings.
            assert _independent_ledger_sum(
                savings.id, scenario_id,
            ) == forward_savings
            assert _independent_ledger_sum(
                checking.id, scenario_id,
            ) == forward_checking
            # Exactly one balanced entry with a single +100 leg on Savings
            # (one matching leg, not two that merely net to +100).
            assert (
                _db.session.query(JournalEntry)
                .filter_by(transfer_id=transfer.id)
                .count()
            ) == 1
            assert list(_legs_by_account(savings.id).values()) == [
                Decimal("100.00"),
            ]
            assert _entries_violating_balance() == []
            assert _trial_balance() == Decimal("0.00")
            _assert_full_reconciliation(scenario_id)


# ---------------------------------------------------------------------------
# Adversarial: the oracle is not vacuous (it fails on a broken seed)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    def test_per_account_reconciliation_catches_a_tampered_shadow(
        self, app, db, seed_user,
    ):
        """Tampering a settled shadow makes ledger != settled-shadow effect.

        A reconciled $100 Checking -> Savings settle has ledger +100 and
        settled-shadow effect +100 on Savings.  Forcing the income shadow's
        estimated amount to 999 (raw SQL, no actual override, so its effective
        becomes 999) leaves the ledger at +100 but pushes the settled-shadow
        effect to +999 -- so the per-account reconciliation, which the oracle
        relies on, now FAILS.  This proves the check is a real comparison, not
        one that passes unconditionally.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Tamper Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Reconciled before tampering.
            assert _independent_ledger_sum(
                savings.id, scenario_id,
            ) == _independent_txn_effect(savings.id, scenario_id)

            # Tamper the income shadow's estimated amount (transactions carry no
            # balance trigger, so this commits); effective becomes 999.
            _db.session.execute(_db.text(
                "UPDATE budget.transactions SET estimated_amount = 999 "
                "WHERE account_id = :a AND transfer_id IS NOT NULL "
                "  AND transaction_type_id = :t"
            ), {
                "a": savings.id,
                "t": ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            })
            _db.session.commit()

            ledger = _independent_ledger_sum(savings.id, scenario_id)
            effect = _independent_txn_effect(savings.id, scenario_id)
            assert ledger == Decimal("100.00")  # ledger unchanged
            assert effect == Decimal("999.00")  # transaction truth drifted
            assert ledger != effect  # the oracle would catch this drift

    def test_trial_balance_catches_an_injected_leg(self, app, db, seed_user):
        """Injecting one extra leg pushes the trial balance off zero.

        A balanced book has trial balance 0.00.  Inserting one unmatched +50
        leg (raw SQL, flushed but never committed so the deferred per-entry
        trigger never fires) makes the whole-ledger sum 0 + 50 = 50.00 -- so
        the trial-balance ``= 0`` assertion is a real check, not one the
        per-entry trigger makes vacuously true.  Rolled back so the leg never
        lands.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "TrialBalance Savings",
            )
            _db.session.commit()
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto an existing entry.  Flush
            # (not commit) makes it visible to the query; the DEFERRED balanced
            # trigger validates only at COMMIT, which we never reach.
            entry_id = _db.session.query(JournalEntry.id).scalar()
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": ledger_accounts_for_account(_db.session, savings.id)[0].id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
            })
            _db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()
