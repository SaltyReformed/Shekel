"""The account-anchor reconciliation oracle (Build-Order Step 5, Commit 8).

The correctness gate for the WRITE side of Step 5: every NON-loan account
(checking, savings, investment, property, interest-bearing, non-loan liability)
posts an OPENING equity correction for its earliest ``AccountAnchorHistory`` row
and a TRUE-UP correction per later row, mirroring the shipped loan genesis
pattern.  After this the trial balance closes app-wide and every linked ledger
sums to an ABSOLUTE balance.  This oracle validates the POSTED ledger against an
independent second opinion re-derived from the SOURCE tables
(``budget.transactions`` + ``budget.account_anchor_history``), never against a
displayed balance and never against the walk / reconcile that produced it.

The absolute invariant, per non-loan account A in scenario S::

    linked_ledger(A, S) == latest_anchor(A)
                           + SUM(net of A's settled sources in S whose
                                 SETTLE DAY is STRICTLY AFTER the latest
                                 assertion's BUSINESS DAY)

An anchor is a CLOSING-BALANCE fact for its own civil day (ruling **R-DH (a)**):
the engine excludes settled items from every period's sum because the anchor
already reflects the settled activity of the day it is about
(``apply_anchor_true_up``: "the user is declaring 'my real checking is now $X'
-- every past-dated debit purchase is already in that number").  So sources
dated ON OR BEFORE the latest assertion's day are ABSORBED into the opening /
true-up deltas and only a strictly LATER day rides on top.

**Both sides of that comparison are stored DAYS, and this oracle was written in
INSTANTS end to end until plan step X-f1** (ruling **R-EC**).  The source side
is ``transactions.settled_on`` -- a transfer's read off its income shadow, equal
to the expense shadow's by Transfer Invariant 3 -- with NO fallback: an undated
settled row is a broken invariant the engine refuses, and this oracle asserts
the same rather than substituting a pay-period start.  The assertion side is
``account_anchor_history.observed_on``, and "latest" orders on
``(observed_on, created_at, id)``: the BUSINESS day first, because
``observed_on`` has been user-supplied since plan step 2, so a balance asserted
for an earlier day but typed later is not the current one.  A rename would have
left this oracle restating the OLD rule against the NEW engine, which is worse
than leaving it broken; each rule is re-derived instead.

A period-granular reading would mis-state the balance sheet by every pre-true-up
settle in the anchor period (the plan review's CRITICAL-1); the
``TestAbsoluteInvariantPerAccount`` fixture pins that exact regression.

**Non-tautological by construction**, the same three independent ways the Step-3
cash oracle is (``test_posting_ledger_cash_reconciliation.py``):

  * **hand-computed literals** -- the expected ledger totals and correction
    deltas are the test author's arithmetic over the seeded anchors and
    amounts (e.g. anchor 500, spend 200 pre-true-up, true-up to 350, spend 100
    post-true-up => linked ledger 250.00), owing nothing to any producer;
  * **independent cross-table queries** -- the ledger side
    (``_independent_linked_ledger_sum`` / ``_linked_ledger_sum_as_of``) reads
    ``account_postings`` and the source side
    (``_independent_post_assertion_source_effect``) reads ``transactions`` with
    independently-written Python / SQL, so asserting the two reconcile checks
    what the producers WROTE against the transaction source of truth;
  * **the production service helpers** -- ``account_posting_total`` and
    ``settled_transfer_effect`` / ``settled_transaction_effect`` must match the
    hand-computed literals too.

Every account and every settle is produced through the REAL go-forward
primitives -- ``create_account`` (fires the C6 opening sync),
``create_settled_cash_transaction`` / ``create_settled_transfer`` (the status
seam + posting builder), ``apply_anchor_true_up`` (the true-up chokepoint) --
so every reconciled row was produced exactly as production produces it.  The one
non-production affordance is pinning an anchor row's ``created_at`` (via
:func:`_assert_balance_at`) so the civil-day partition under test is deterministic
regardless of the test clock or timezone -- the same technique the C5 unit suite
uses.  Assertion instants are always built RELATIVE to the factory origination
row's stored ``created_at`` (the one instant the test cannot choose).

Two adversarial cases prove the oracle is not vacuous: tampering the latest
anchor balance makes the per-account reconciliation FAIL under the real sweep
helper, and injecting one extra leg makes the trial balance go non-zero.  All
money is ``Decimal`` from strings, with the arithmetic shown per the testing
standard.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import (
    account_posting_service,
    anchor_service,
    cash_ledger,
    posting_service,
    transfer_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    create_account_via_service,
    create_settled_cash_transaction,
    create_settled_transfer,
    ledger_account_of_kind,
    ledger_net,
    linked_ledger_account,
    load_migration_module,
    observed_day_of,
    restamp_opening_assertion,
    restate_account_opening,
)
from app.services.row_valuation import owned_contribution


# The Step-5 data-boundary migration, loaded once so its idempotent raw-SQL
# teardown ``_remove_account_anchor_postings`` can reproduce the pre-C6
# historical state (an account whose anchor was asserted before the go-forward
# wiring, carrying no correction) for the backfill == go-forward test -- the
# same pattern the C7 backfill suite uses.
_BOUNDARY_MIGRATION = load_migration_module(
    "c9f2e6a4b1d8_account_anchor_postings_data_boundary.py"
)


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These deliberately re-derive each side from scratch so the oracle is a genuine
# second opinion: a bug shared by the walk and the reconcile cannot hide,
# because the ledger side reads ``account_postings`` and the source side reads
# ``transactions`` / ``account_anchor_history`` with independently-written
# code, and both are also pinned to hand-computed literals.  Some mirror the
# Step-2 / Step-3 oracles (``_trial_balance``, ``_entries_violating_balance``);
# the duplication is DELIBERATE -- each oracle keeps its OWN independent queries
# so it remains a self-contained second opinion.


def _as_utc(instant: datetime) -> datetime:
    """Return *instant* as an aware-UTC datetime (independent of the walk).

    The oracle's own copy of the walk's instant convention (an aware value
    converts to UTC, a naive value is assumed UTC -- every ``timestamptz`` in
    this app is stored UTC), written here rather than imported from the SUT so
    the ``<=`` / ``>`` partition comparisons are an independent restatement,
    not a reuse of the code under test.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def _independent_linked_ledger_sum(account_id: int, scenario_id: int) -> Decimal:
    """Sum a non-loan account's LINKED ledger legs in a scenario (independent).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` (for the real ``account_id``), summing the signed
    ``amount`` over the LINKED kind only.  Keyed off the REAL account via
    ``ledger_accounts.account_id`` -- a different join shape than
    ``posting_service.account_posting_total`` (which resolves the ledger row
    first), so the two cannot share a lookup bug.

    Filtered to the LINKED kind because an anchor correction lands ``+delta``
    on the linked row and ``-delta`` on the ``anchor_equity`` twin, which shares
    the ``account_id`` column: a bare-``account_id`` sum would cancel the
    correction pairwise and silently reproduce the pre-Step-5 changes-only
    figure, making the absolute assertions vacuous.
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


def _linked_ledger_sum_as_of(
    account_id: int, scenario_id: int, civil_date,
) -> Decimal:
    """Sum a LINKED ledger's legs on entries dated at or before *civil_date*.

    The "ledger through an assertion day" reader: every linked-ledger entry --
    a source OR an anchor correction -- carries an ``entry_date`` equal to the
    civil day its money moved (``posting_service._entry_date`` /
    ``_transaction_entry_date`` read the STORED ``transactions.settled_on``
    since plan step X-f1; corrections take the anchor's observed day), so
    summing legs with ``entry_date <= civil_date`` reconstructs the ledger as of
    the END of that civil day -- the ``TestLedgerThroughEachAssertionDay``
    invariant.  The fixture places every event on a DISTINCT, increasing civil
    day so each as-of names exactly one state.
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
            JournalEntry.entry_date <= civil_date,
        )
        .scalar()
    )


def _latest_assertion(account_id: int) -> tuple[date, Decimal]:
    """Return an account's latest ``(asserted business day, anchor balance)``.

    Reads ``account_anchor_history`` directly -- the row with the max
    ``(observed_on, created_at, id)``, exactly the row
    ``cash_ledger.resolve_anchor`` picks -- so the "latest anchor" the invariant
    references is taken from the source of truth, not the
    ``current_anchor_balance`` cache the writer also maintains (which a bug
    could desync).

    **All THREE keys are load-bearing and this oracle ordered on only two until
    plan step X-f1.**  ``observed_on`` is the BUSINESS day the balance was true
    for and has been a user-supplied column since plan step 2, so the recording
    order and the business order can differ outright: a balance asserted for an
    earlier day but typed later is not the current one, and ``created_at`` first
    names it.  ``created_at`` then separates two assertions about one day, and
    ``id`` breaks a tie between two stamped at one instant.  The oracle stating
    a DIFFERENT "latest" rule than the engine is the shape that lets both be
    wrong together while the sweep reports clean.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(
            AccountAnchorHistory.observed_on.desc(),
            AccountAnchorHistory.created_at.desc(),
            AccountAnchorHistory.id.desc(),
        )
        .first()
    )
    assert row is not None, (
        f"account {account_id} has no anchor history -- every real account "
        f"carries its origination row, so this is a broken fixture"
    )
    return row.observed_on, Decimal(str(row.anchor_balance))


def _source_settled_day(txn) -> date:
    """Return a settled source's civil settle day, independently, or REFUSE.

    The source's stored ``settled_on`` -- the day its cash moved.  It derived
    the day from ``paid_at``, falling back to the pay period's ``start_date``,
    until plan step X-f1 replaced the instant with the stored day and the
    fallback with a refusal; this restates that refusal rather than importing
    it, because an oracle that shares the engine's accessor cannot catch the
    engine dating a row it should have refused.

    For a transfer shadow the walk attributes by the INCOME shadow's day; both
    shadows carry the same day (Transfer Invariant 3 mirrors it), so reading
    each shadow's own is the same value computed independently of the "income
    shadow" concept.
    """
    assert txn.settled_on is not None, (
        f"transaction {txn.id} is settled but carries no settled_on -- the "
        "engine refuses this row, so a fixture that produced it is broken"
    )
    return txn.settled_on


def _independent_source_effect(txn) -> Decimal:
    """Return a settled source's signed, debit-positive effect on its account.

    The per-source truth the linked ledger must reflect: a transfer shadow
    contributes ``+effective`` when it is the income shadow (money in) and
    ``-effective`` when it is the expense shadow (money out); an ordinary cash
    transaction contributes ``effective - Sigma(credit entries)`` signed ``+``
    for income / ``-`` for an expense.  ``effective`` is the model property
    (``actual`` over ``estimated``).  Independent of the posting builder (it
    never imports ``_signed_cash_leg``); the linked leg for *txn* equals this.
    """
    if txn.transfer_id is not None:
        return owned_contribution(txn) if txn.is_income else -owned_contribution(txn)
    credit_sum = sum(
        (entry.amount for entry in txn.entries if entry.is_credit),
        Decimal("0"),
    )
    effect = owned_contribution(txn) - credit_sum
    return effect if txn.is_income else -effect


def _independent_post_assertion_source_effect(
    account_id: int, scenario_id: int, latest_asserted_day: date,
) -> Decimal:
    """Sum an account's settled source effect dated AFTER the latest anchor's day.

    Over the account's settled (``status.is_settled``), non-deleted
    transactions AND transfer shadows in *scenario_id*, add each source's signed
    effect (:func:`_independent_source_effect`) iff its settle day is STRICTLY
    AFTER *latest_asserted_day* -- the sources that ride on top of the asserted
    balance.  An assertion is the CLOSING balance for its own civil day (ruling
    R-DH (a)), so a source dated ON that day is already inside it and is
    absorbed by the opening / true-up delta; only a strictly later day rides.
    Read from ``transactions`` (a different table than the ledger side), so
    asserting the equality reconciles what the producers wrote against the
    transaction source of truth.
    """
    txns = (
        _db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    return sum(
        (
            _independent_source_effect(txn)
            for txn in txns
            if _source_settled_day(txn) > latest_asserted_day
        ),
        Decimal("0"),
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


def _opening_correction_count(account_id: int, scenario_id: int) -> int:
    """Count an account's posted OPENING correction entries in one scenario.

    Read independently of the producer (a join from the account's LINKED
    ledger to the ``account_opening`` journal entries), so a test can assert
    that a reconcile wrote NOTHING new rather than that it produced the right
    number -- the difference between "the skip still works" and "the answer is
    right", which the sibling assertions cover.
    """
    linked = linked_ledger_account(_db.session, account_id)
    entry_ids = (
        _db.session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id == linked.id)
    )
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            ),
            JournalEntry.id.in_(entry_ids),
        )
        .count()
    )


def _assert_account_anchors_reconcile(scenario_id: int) -> None:
    """Assert every non-loan LINKED ledger reconciles ABSOLUTELY in *scenario_id*.

    For each of the scenario owner's non-loan real accounts, the independent
    linked-ledger sum equals the latest anchor balance plus the independent
    post-assertion source effect -- the Step-5 absolute invariant, holding over
    EVERY such account, not only the ones a given test hand-computes.  Amortizing
    loans are excluded: their absolute invariant couples on the amortization
    split and is the loan oracle's job.  The per-entry balance and trial balance
    are global self-checks (always true for a balanced ledger, asserted cheaply
    on every sweep).
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
    # Every owner carries at least the seeded Checking's linked ledger, so an
    # empty result means the query silently found nothing (a minting or filter
    # regression) and the loop below would pass vacuously -- assert non-empty so
    # the sweep cannot be a no-op.
    assert linked, (
        "no non-loan linked ledger accounts to reconcile -- the sweep would "
        "be vacuous (expected at least the Checking account's linked ledger)"
    )
    for ledger_account in linked:
        account_id = ledger_account.account_id
        latest_asserted_day, latest_anchor = _latest_assertion(account_id)
        ledger = _independent_linked_ledger_sum(account_id, scenario_id)
        effect = _independent_post_assertion_source_effect(
            account_id, scenario_id, latest_asserted_day,
        )
        assert ledger == latest_anchor + effect, (
            f"account {account_id}: linked ledger {ledger} != latest anchor "
            f"{latest_anchor} + post-assertion source effect {effect} in "
            f"scenario {scenario_id}"
        )
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")


# ---------------------------------------------------------------------------
# Local fixture helpers (controlled assertion instants)
# ---------------------------------------------------------------------------


def _origin_instant(account) -> datetime:
    """Return the factory origination row's stored assertion instant (UTC).

    The one instant a test cannot choose (the origination row's ``created_at``
    is the INSERT transaction's server ``now()``); every other instant in a
    fixture is built relative to it so the pre / post / tie partitions are
    deterministic regardless of clock or timezone.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.created_at, AccountAnchorHistory.id)
        .first()
    )
    return _as_utc(row.created_at)


def _assert_balance_at(account, balance, created_at) -> AccountAnchorHistory:
    """Append a true-up ``AccountAnchorHistory`` row at a controlled instant.

    Mirrors ``anchor_service.stage_anchor_true_up`` (the history row plus the
    ``current_anchor_balance`` cache write) but pins ``created_at`` explicitly
    so the civil-day partition under test is exact -- the one non-production
    affordance, the same the C5 unit suite uses.  Anchors against the account's
    current anchor period; flushes.  The caller drives the reconcile
    (``sync_account_anchor_postings_all_scenarios``) afterward, exactly as the
    true-up chokepoint does.
    """
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created_at,
        # The civil day this assertion is the closing balance FOR, kept in step
        # with the pinned instant by the shared rule (ruling R-DH, plan step 2).
        observed_on=observed_day_of(created_at),
        # The ENTERED day, in step with the pinned instant (**N-299**).
        # The column's default is the wall clock, which a row built to sit in
        # the PAST must not inherit: it would claim to have been typed today.
        recorded_on=observed_day_of(created_at),
    )
    _db.session.add(row)
    _db.session.flush()
    return row


def _true_up_at(account, balance, created_at) -> None:
    """Assert a controlled true-up and reconcile it (the chokepoint's two steps).

    The deterministic stand-in for ``anchor_service.apply_anchor_true_up``: it
    stages the history row + cache (:func:`_assert_balance_at`) at a PINNED
    ``created_at`` and then drives the SAME all-scenarios reconcile the true-up
    chokepoint calls.  Pinning the instant is what makes the civil-day partition
    exact -- ``apply_anchor_true_up`` stamps ``created_at = now()``, which cannot
    be placed between two synthetic settles; the C5 unit suite uses the same
    affordance for the same reason.  The chokepoint itself is covered end to end
    by ``test_account_posting_service.py``; this oracle validates the resulting
    ledger against an independent second opinion.
    """
    _assert_balance_at(account, balance, created_at)
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )


def _settle_expense(seed_user, account, amount, settled_on):
    """Settle an expense on *account* on a pinned civil DAY; return it.

    ``settled_on`` is the day the cash moved, pinned so the posted entry and the
    walk's attribution agree, as in production.  Placed in the seed bootstrap
    period; the walk attributes by the settle DAY, so the period placement is
    immaterial.  It took an instant (or ``None``, for the period-start fallback)
    until plan step X-f1 removed both the derivation and the fallback.
    """
    return create_settled_cash_transaction(
        seed_user, _db.session, seed_user["bootstrap_period"],
        Decimal(str(amount)), account=account, settled_on=settled_on,
    )


# ---------------------------------------------------------------------------
# 1. The absolute invariant: civil-day partition (CRITICAL-1)
# ---------------------------------------------------------------------------


class TestAbsoluteInvariantPerAccount:
    """A multi-anchor account reconciles to latest anchor + post-assertion sources."""

    @pytest.mark.server_clock
    def test_critical1_absorb_and_ride_reconciles_three_ways(
        self, app, db, seed_user,
    ):
        """Anchor 500, spend 200 pre-true-up, true-up 350, spend 100 post: 250.00.

        The CRITICAL-1 fixture on a Savings anchored $500.00 (origination
        instant T):

          - $200.00 expense paid T+1h  -- BEFORE the true-up (absorbed)
          - true-up asserting $350.00 at T+2h
          - $100.00 expense paid T+3h  -- AFTER the true-up (rides on top)

        The engine's pre-true-up answer was 500 - 200 = 300, so the true-up
        delta is 350 - 300 = +50.00; the post-true-up spend is the only source
        attributed after the latest assertion.  So:

          linked ledger = 500 (opening) + 50 (true-up) - 200 - 100 = 250.00
                        = latest anchor 350 + post-assertion effect (-100.00)

        A period-granular reading would have absorbed BOTH settles (all three
        events share one period) and mis-stated the true-up.  All three
        independent computations agree, and the production-wide sweep ties.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Critical1 Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)

            _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=1)),
            )
            _true_up_at(savings, "350.00", origin + timedelta(days=2))
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=3)),
            )
            db.session.commit()

            latest_asserted_day, latest_anchor = _latest_assertion(savings.id)
            assert latest_anchor == Decimal("350.00")

            # (a) hand-computed literal == independent ledger-table query.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            # (b) independent source query == the hand-computed post-assertion
            #     effect (only the T+3h spend rides on top).
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("-100.00")
            # (c) the production service helpers agree too.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            assert posting_service.settled_transaction_effect(
                savings.id, scenario_id,
            ) == Decimal("-300.00")  # both spends, the ledger-native cash effect

            # The equity twin nets -(opening 500 + true-up 50) = -550.00.
            equity = ledger_account_of_kind(
                db.session, savings.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                db.session, equity.id, scenario_id,
            ) == Decimal("-550.00")

            _assert_account_anchors_reconcile(scenario_id)

    @pytest.mark.server_clock
    def test_source_at_exact_assertion_instant_is_absorbed(
        self, app, db, seed_user,
    ):
        """A source dated the assertion's OWN civil day is absorbed (<= tie).

        The exact boundary the walk's inclusive ``<=`` partition and the
        oracle's strict ``>`` post-assertion filter meet: Savings anchored
        $500.00; a $75.00 expense settled on the true-up's own business day
        (T2's); the true-up asserts $425.00.  The spend is absorbed
        into the anchor (its day is not strictly after T2's): ledger_before =
        500 - 75 = 425, so the true-up delta is 0 and books NOTHING, and the
        spend does not ride on top.

          linked = 500 (opening) - 75 = 425.00 = latest anchor 425 + post (0)

        A strict-``<`` walk would leave the spend outside the anchor, book a
        spurious -75.00 true-up, and mis-state the ledger at 350 -- so this pins
        the exact absorb/ride boundary the module advertises, which no other
        fixture (all sources strictly before or after their assertions) touches.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Tie Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            instant = _origin_instant(savings) + timedelta(hours=2)

            _settle_expense(
                seed_user, savings, "75.00", observed_day_of(instant),
            )
            _true_up_at(savings, "425.00", instant)
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("425.00")
            # The tie source is NOT strictly after the assertion, so nothing
            # rides on top (a strict-< walk would report -75.00 here).
            latest_asserted_day, latest_anchor = _latest_assertion(savings.id)
            assert latest_anchor == Decimal("425.00")
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            # The true-up delta was zero (the tie was absorbed): no true-up entry
            # -- a strict-< walk would have booked one.
            linked = linked_ledger_account(db.session, savings.id)
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert (
                db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == trueup_source,
                )
                .distinct()
                .count()
            ) == 0
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 1b. A transfer source rides on top and reconciles on both endpoints
# ---------------------------------------------------------------------------


class TestTransferSourceRidesOnTop:
    """A settled transfer reconciles on both linked ledgers by its shadow effect."""

    @pytest.mark.server_clock
    def test_transfer_into_savings_reconciles_both_accounts(
        self, app, db, seed_user,
    ):
        """A $150 Checking -> Savings transfer rides on both post-assertion anchors.

        The seeded Checking ($1000.00 opening) transfers $150.00 into a Savings
        anchored $200.00, settled at server-now (after both origination
        assertions, so it rides on top of each).  The transfer's income shadow
        lands +150.00 on Savings and its expense shadow -150.00 on Checking:

          Savings  = 200 (opening) + 150 = 350.00 = anchor 200 + post (+150.00)
          Checking = 1000 (opening) - 150 = 850.00 = anchor 1000 + post (-150.00)

        This exercises the transfer branch of the oracle's independent
        source-effect helper on BOTH shadow polarities, so the sweep's
        second-opinion computation is validated for transfers, not only cash.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Transfer Savings",
                anchor_balance=Decimal("200.00"),
            )
            db.session.commit()

            create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("850.00")

            # The independent transfer-branch effect signs each shadow correctly.
            savings_asserted_at, _sa = _latest_assertion(savings.id)
            checking_asserted_at, _ca = _latest_assertion(checking.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, savings_asserted_at,
            ) == Decimal("150.00")
            assert _independent_post_assertion_source_effect(
                checking.id, scenario_id, checking_asserted_at,
            ) == Decimal("-150.00")
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-150.00")

            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 2. Ledger through each assertion instant == the asserted balance
# ---------------------------------------------------------------------------


class TestLedgerThroughEachAssertionDay:
    """At every historical assertion instant the ledger equalled that anchor."""

    @pytest.mark.server_clock
    def test_as_of_each_assertion_lands_on_the_asserted_balance(
        self, app, db, seed_user,
    ):
        """Distinct-day fixture: the as-of ledger lands on each anchor exactly.

        Every event on its OWN increasing civil day (whole-day offsets from the
        origination instant T), so "ledger as of the end of day D" equals
        "ledger as of the assertion instant on day D".  Savings anchored
        $500.00 (opening on day D0):

          - $200.00 expense paid T+5d   (day D0+5)
          - true-up asserting $350.00   at T+10d (day D0+10)
          - $100.00 expense paid T+15d  (day D0+15)

        Walking the as-of ledger forward:

          as of D0    : 500.00                      == opening anchor
          as of D0+5  : 500 - 200 = 300.00          (spend rode the engine value)
          as of D0+10 : 500 - 200 + 50 = 350.00     == true-up anchor
          as of D0+15 : 350 - 100 = 250.00          == latest anchor + post

        The two anchor days (D0, D0+10) land exactly on the asserted balances --
        the ledger-through-each-assertion invariant.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "AsOf Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)
            day0 = observed_day_of(origin)

            _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=5)),
            )
            _assert_balance_at(savings, "350.00", origin + timedelta(days=10))
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                savings.id,
            )
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=15)),
            )
            db.session.commit()

            # Each as-of civil day reconstructs the ledger through that instant.
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0,
            ) == Decimal("500.00")                      # opening anchor
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0 + timedelta(days=5),
            ) == Decimal("300.00")                      # pre-true-up engine value
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0 + timedelta(days=10),
            ) == Decimal("350.00")                      # true-up anchor
            assert _linked_ledger_sum_as_of(
                savings.id, scenario_id, day0 + timedelta(days=15),
            ) == Decimal("250.00")                      # latest anchor + post

            # The final ABSOLUTE total matches, and the sweep ties.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 3. Revert after a true-up self-heals to the engine's answer
# ---------------------------------------------------------------------------


class TestRevertAfterTrueupSelfHeals:
    """Reverting a pre-true-up settle re-bases the true-up and stays reconciled."""

    @pytest.mark.server_clock
    def test_revert_pre_trueup_settle_reconciles(self, app, db, seed_user):
        """The CRITICAL-1 fixture, then revert the pre-true-up spend; sweep ties.

        Continuing from anchor 500 / spend 200 (T+1h) / true-up 350 (T+2h) /
        spend 100 (T+3h) -- linked ledger 250.00 -- the $200.00 pre-true-up
        expense is reverted through the real ``sync_transaction_postings``
        (``settled=False``) path.  The true-up's walked ``ledger_before`` moves
        300 -> 500, so its delta moves +50 -> -150; the effect-time self-heal
        appends the balancing -200.00 delta on the same key.  The reverted
        source drops from the transaction truth too:

          linked = 500 (opening) - 150 (healed true-up) - 100 = 250.00
                 = latest anchor 350 + post-assertion (-100.00)

        The account total never left the anchor + post-assertion balance.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Revert Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)

            spend = _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=1)),
            )
            _true_up_at(savings, "350.00", origin + timedelta(days=2))
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=3)),
            )
            db.session.commit()
            _assert_account_anchors_reconcile(scenario_id)

            # Revert the pre-true-up spend via the real posting primitive; the
            # tail self-heal re-bases the true-up in the same transaction.
            posting_service.sync_transaction_postings(spend, settled=False)
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            # The self-heal re-based the true-up: its walked ledger_before moved
            # 300 -> 500 (the $200 no longer absorbed), so the true-up key's
            # linked legs now net -150.00 -- the original +50.00 plus the
            # appended -200.00 balancing delta.  A stale correction left at +50
            # would read the account at 450.00; this pins the re-derivation
            # itself, not merely the total it happens to reach.
            linked = linked_ledger_account(db.session, savings.id)
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            trueup_linked_net = (
                db.session.query(
                    _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == trueup_source,
                )
                .scalar()
            )
            assert trueup_linked_net == Decimal("-150.00")
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 4. Pre-anchor absorption (a settle before the opening is inside it)
# ---------------------------------------------------------------------------


class TestPreAnchorAbsorption:
    """A settle attributed before the opening is absorbed into the opening delta."""

    def test_a_spend_dated_before_the_opening_is_absorbed_into_it(
        self, app, db, seed_user,
    ):
        """A 2024-dated spend is inside the 2026 opening.

        A $200.00 expense settled on the 2024 bootstrap period's start day --
        BEFORE the account's origination assertion (server-now, 2026).  The C6
        self-heal at the settle re-derives the opening: its delta becomes
        500 - (-200) = +700.00, so the linked ledger stays exactly on the 500.00
        anchor and NO source rides on top.

          linked = 700 (opening) - 200 = 500.00 = latest anchor 500 + 0

        **The row reached that day through a FALLBACK until plan step X-f1** --
        it carried no ``paid_at`` and every reader substituted its pay period's
        ``start_date`` -- and this test was named for the fallback.  The day is
        a stored fact now and the substitution is gone, so the fixture states
        the day it always meant.  Every figure is unchanged, because the day is.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "PreAnchor Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()

            _settle_expense(
                seed_user, savings, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("500.00")
            # The OPENING books the account's opening equity ($500.00, the
            # stored fact) and the $200.00 the records moved books separately
            # as an ``account_trueup`` against the origination assertion.  The
            # account's TOTAL is unchanged at $500.00, which is the property
            # this test is about.
            #
            # *One $700.00 ``account_opening`` entry until plan step X-f3c-2a,
            # which conflated capital brought on with a correction to it -- and
            # that conflation is why a BACK-DATED assertion could re-elect the
            # whole figure.*
            linked = linked_ledger_account(db.session, savings.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            opening_net = (
                db.session.query(
                    _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .scalar()
            )
            assert opening_net == Decimal("500.00")
            # Nothing rides on top: the spend is pre-assertion.
            latest_asserted_day, _latest = _latest_assertion(savings.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 5. Same-day true-ups merge to one correction landing on the later value
# ---------------------------------------------------------------------------


class TestSameDayAnchorMerge:
    """Two same-UTC-day true-ups merge to one entry on the later value."""

    @pytest.mark.server_clock
    def test_two_same_day_trueups_reconcile(self, app, db, seed_user):
        """$600 then $550 on one future UTC day merge to a single +50.00 entry.

        Both assertions sit on one future UTC day (06:00 and 07:00, no midnight
        crossing) on a $500.00-anchored Savings, with no settled sources.  Their
        deltas +100.00 and -50.00 share the (true-up, day) reconcile key and
        merge to ONE +50.00 entry; the ledger lands on the LATER value 550.00
        (== the latest anchor, no post-assertion sources).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Merge Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            day = (_origin_instant(savings) + timedelta(days=30)).replace(
                hour=6, minute=0, second=0, microsecond=0,
            )

            _assert_balance_at(savings, "600.00", day)
            _assert_balance_at(savings, "550.00", day + timedelta(hours=1))
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                savings.id,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("550.00")
            # Exactly ONE true-up entry survives the same-day merge.
            linked = linked_ledger_account(db.session, savings.id)
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            trueup_entries = (
                db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == trueup_source,
                )
                .distinct()
                .all()
            )
            assert len(trueup_entries) == 1
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 6. A negatively-anchored (owed-as-negative) non-loan liability
# ---------------------------------------------------------------------------


class TestNegativelyAnchoredLiability:
    """A non-loan liability anchor posts ledger-native, with no sign branch."""

    @pytest.mark.server_clock
    def test_credit_card_negative_anchor_reconciles(self, app, db, seed_user):
        """A Credit Card anchored -500.00, then a $120 charge, reconciles absolutely.

        The owed-as-negative convention: the opening books linked -500.00 /
        equity +500.00 (no ``-abs`` normalization, exactly like the engine).  A
        $120.00 expense charged to the card (post-assertion) rides on top:

          linked = -500 (opening) - 120 = -620.00
                 = latest anchor -500 + post-assertion (-120.00)
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            # The civil day an hour after the origination instant -- verbatim
            # what the deleted derivation computed for the instant this fixture
            # used to pass, so the figures below are unchanged (plan step X-f1).
            _settle_expense(
                seed_user, card, "120.00",
                observed_day_of(_origin_instant(card) + timedelta(hours=1)),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                card.id, scenario_id,
            ) == Decimal("-620.00")
            equity = ledger_account_of_kind(
                db.session, card.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                db.session, equity.id, scenario_id,
            ) == Decimal("500.00")  # ledger-native: +500 against the -500 anchor
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 7. A $0-anchor account books nothing and stays out of the ledger
# ---------------------------------------------------------------------------


class TestZeroDeltaBooksNothing:
    """A $0-anchor account mints no correction and no equity twin, yet reconciles."""

    def test_zero_anchor_books_nothing_and_reconciles(
        self, app, db, seed_user,
    ):
        """A fresh $0.00 Savings posts no entry, no twin, and the sweep still ties.

        The opening delta is $0, so the reconcile books nothing: no correction
        entry and no ``anchor_equity`` ledger row (the account stays
        hard-deletable, Guard 5 never engaging).  The absolute invariant is
        0.00 == latest anchor 0.00 + 0, so the production-wide sweep reconciles
        it alongside the seeded Checking.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            zero = create_account_of_type(
                seed_user, db.session, "Savings", "Zero Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                zero.id, scenario_id,
            ) == Decimal("0.00")
            assert ledger_account_of_kind(
                db.session, zero.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 7b. A BACK-DATED true-up, through the real write door (plan step X-f1c4c)
# ---------------------------------------------------------------------------


class TestBackDatedTrueUpReBasesTheLedger:
    """A user-supplied ``observed_on`` inserts an assertion MID-HISTORY.

    Plan step X-f1c4c made that reachable for the first time: every cash true-up
    stamped ``display_today()`` before it, so an assertion could only ever be
    appended at or after every existing one.  Inserting one BETWEEN two others
    is therefore a new input shape for
    ``account_posting_service.sync_account_anchor_postings_all_scenarios`` --
    an already-posted correction has to be ADJUSTED rather than posted fresh,
    and the entry's source kind can flip.

    **Three independent reviews of that step all found the posted ledger
    ungraded for it**, and one demonstrated the gap by removing the re-sync
    entirely: all eleven of the step's own new tests still passed, because
    ``cash_balance_at`` does not read ``account_postings``.  These two cases
    close that hole, using this file's independent oracles rather than the
    service's own helpers.

    Both go through ``anchor_service.apply_anchor_true_up`` -- the production
    door, with its lock, its duplicate rule and its re-sync -- not the
    deterministic ``_assert_balance_at`` stand-in the older classes use, because
    the door is what the step changed.
    """

    def test_a_mid_history_back_date_re_bases_and_reconciles(
        self, app, db, seed_user,
    ):
        """An assertion inserted between two others leaves the ledger exact.

        Opening $1,000.00 at O, a settled $200.00 expense at O+5, a true-up to
        $900.00 at O+20, then a BACK-DATED $700.00 asserted for O+10 -- which
        lands after the expense and before the later true-up, so it must
        re-partition which correction absorbs that expense and adjust the later
        correction rather than double-count it.

        Hand-computed: the walk absorbs the -$200.00 into the O+10 assertion
        (running 1000 - 200 = 800, asserted 700, delta -100), and the O+20
        correction re-bases from 700 to 900 (delta +200).  The linked ledger
        must therefore land on the LATEST anchor, $900.00, with no
        post-assertion sources.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            opening_day = display_today() - timedelta(days=40)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Back-dated Savings",
                anchor_balance=Decimal("1000.00"), observed_on=opening_day,
            )
            db.session.commit()

            _settle_expense(
                seed_user, savings, "200.00",
                opening_day + timedelta(days=5),
            )
            db.session.commit()

            anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("900.00"),
                observed_on=opening_day + timedelta(days=20),
            )
            # THE BACK-DATE: strictly between the expense and the later true-up.
            outcome = anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("700.00"),
                observed_on=opening_day + timedelta(days=10),
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED
            # The ledger lands on the LATEST anchor, read through the
            # independent join rather than the service's own total.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("900.00")
            # ...and through the seam's own reader, which resolves the ledger
            # row first, so a shared lookup bug cannot satisfy both.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("900.00")
            # Ledger-wide self-checks: nothing was double-posted or stranded.
            assert _trial_balance() == Decimal("0.00")
            assert _entries_violating_balance() == []
            _assert_account_anchors_reconcile(scenario_id)

    def test_back_dating_below_the_opening_re_designates_it_and_reconciles(
        self, app, db, seed_user,
    ):
        """An assertion before the opening BECOMES the opening, and ties exactly.

        **Developer ruling 2026-08-04**, taken at this step's adversarial
        review: a LOAN's origination is a contractual fact and its door refuses
        below it (``routes/loan/params.true_up_balance``), but a CASH account's
        opening is merely its earliest record -- so learning an earlier balance
        legitimately makes that the opening, and the cash door does NOT inherit
        the loan's third bound.  The reviewer who found this reproduced the
        re-designation and asked for it to be blocked; it was ruled ALLOWED and
        graded instead, which is what this case is.

        **What that ruling MEANT changed at plan step X-f3c-2a, and the money
        did not.**  ``cash_anchor_facts`` still marks the earliest row
        ``is_opening``, so the re-designation the 2026-08-04 ruling allows is
        still visible on the FACTS -- and it no longer decides a posting.  Which
        correction books ``account_opening`` is now read from the stored
        ``budget.account_openings`` row (**R-GX**, **R-HE**), so the opening
        entry stays where the books actually opened and the back-dated
        assertion books an ordinary ``account_trueup`` on its own day.

        *Until then the flag chose the posting: back-dating below the
        origination REVERSED the original opening entry and re-posted it as a
        true-up, moving the account's opening equity onto a day the owner had
        merely typed a balance for.  An owner who genuinely means "my books
        opened earlier, at $250" restates the opening record -- X-f3c-2b's
        door -- rather than having a true-up re-designate it implicitly.*

        The money must not move either way: the linked ledger still lands on
        the latest anchor, which is what the totals below grade.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            opening_day = display_today() - timedelta(days=20)
            # Built through the PRIMITIVE, so the books stay exactly where
            # ``create_account`` put them (plan step X-f3c-2b): this case
            # asserts WHICH DAY the ``account_opening`` entry is filed on, and
            # the shared factory opens the books earlier -- right for a
            # fixture that records movements, wrong for one whose subject is
            # the day.
            savings = create_account_via_service(
                seed_user, db.session, "Savings", "Pre-opening Savings",
                anchor_balance=Decimal("1000.00"), observed_on=opening_day,
            )
            db.session.commit()

            facts_before = cash_ledger.cash_anchor_facts(savings.id)
            assert [(f.observed_on, f.is_opening) for f in facts_before] == [
                (opening_day, True),
            ], "precondition: exactly one assertion, and it is the opening"

            earlier = opening_day - timedelta(days=10)
            anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("250.00"),
                observed_on=earlier,
            )

            # The flag MOVED -- this is the behaviour the ruling allows, and it
            # is asserted rather than merely tolerated.
            facts_after = cash_ledger.cash_anchor_facts(savings.id)
            assert [(f.observed_on, f.is_opening) for f in facts_after] == [
                (earlier, True), (opening_day, False),
            ], "the earlier assertion must become the account's opening"

            # The ledger is unmoved by the re-designation: still the latest
            # anchor, still balanced, still reconciling across the scenario.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("1000.00")
            assert _trial_balance() == Decimal("0.00")
            assert _entries_violating_balance() == []
            _assert_account_anchors_reconcile(scenario_id)

            # The source KIND flipped with the flag, and it is graded by the NET
            # opening-sourced amount per day rather than by which days carry an
            # opening entry at all.  That distinction is the finding: the
            # reconcile does not retag a row in place, it REVERSES the old
            # opening entry -- and a reversal keeps the source kind it reverses,
            # so the old day still has opening-kind entries and only their SUM
            # says they cancelled.  A first version of this assertion looked at
            # the days alone and reported both, which grades nothing.
            linked = linked_ledger_account(db.session, savings.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            opening_by_day = dict(
                db.session.query(
                    JournalEntry.entry_date,
                    db.func.coalesce(db.func.sum(Posting.amount), Decimal("0")),
                )
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .group_by(JournalEntry.entry_date)
                .all()
            )
            # The OPENING stays where the books opened, carrying the stored
            # opening equity ($1,000.00); the back-dated day gets no opening
            # entry at all.  Nothing is reversed, because nothing was
            # re-designated.
            assert opening_by_day.get(opening_day) == Decimal("1000.00")
            assert opening_by_day.get(earlier) is None
            # The two assertions book TRUE-UPS that net to zero: the back-dated
            # $250.00 corrects the books DOWN by $750.00 and the original
            # $1,000.00 assertion corrects them back UP by $750.00.  Their sum
            # is what keeps the account on $1,000.00.
            trueup_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
            ).join(
                JournalEntry, Posting.journal_entry_id == JournalEntry.id,
            ).filter(
                Posting.ledger_account_id == linked.id,
                JournalEntry.scenario_id == scenario_id,
                JournalEntry.source_kind_id == trueup_source,
            ).scalar() == Decimal("0.00")


# ---------------------------------------------------------------------------
# 8. Scenario and owner isolation
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Corrections and sources never bleed across scenarios or owners."""

    def test_a_settle_in_a_fresh_scenario_mints_that_scenarios_opening(
        self, app, db, seed_user,
    ):
        """A scenario's FIRST activity on an account opens that ledger.

        A scenario becomes live for an account the moment an entry first lands
        on its linked ledger there.  The account-global sync only visits
        scenarios that are ALREADY live, so the emission that makes a scenario
        live is the one that has to mint its corrections -- and the
        effect-time self-heal skipped it, because it tested only whether a
        posted correction had gone STALE and a brand-new scenario has none to
        stale.

        Hand-computed, in the shape production would reach it: Checking opened
        2026-01-02 at $1,000.00, a fresh scenario, one $70.00 expense settled
        2026-03-03 -- two months AFTER the opening, so the change rides on top
        of every assertion and the staleness arm correctly declines.  The
        scenario's linked ledger must read 1000 - 70 = $930.00.  Before the
        fix it read ``-$70.00``: the activity alone, with the opening and its
        equity twin both missing, which is why the trial balance still closed
        while the account's balance was wrong by its entire opening.

        Latent rather than live today -- production creates baseline scenarios
        only, and a baseline carries its corrections from account-create time
        -- so this is the guard for the scenario-clone feature that would
        otherwise ship on top of it.
        """
        with app.app_context():
            checking = seed_user["account"]
            opened = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
            restamp_opening_assertion(db.session, checking, opened)
            # **The BOOKS go back further than the restamp puts them** (plan
            # step X-f3c-2b, ruling **R-HG**).  The second settle below is
            # dated 2025-12-01 on purpose -- BEFORE the assertion, which is
            # what makes the staleness arm fire -- and ``restamp_opening_
            # assertion`` would open the books one day before the assertion,
            # which is after it.  Before the ASSERTION and after the BOOKS is
            # exactly the span this case needs, and is the production shape.
            restate_account_opening(db.session, checking, date(2025, 11, 30))
            db.session.commit()

            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=checking, scenario=whatif,
                settled_on=date(2026, 3, 3),
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, whatif.id,
            ) == Decimal("930.00")
            # The baseline is untouched by the fresh scenario's activity.
            assert posting_service.account_posting_total(
                checking.id, seed_user["scenario"].id,
            ) == Decimal("1000.00")
            _assert_account_anchors_reconcile(whatif.id)
            _assert_account_anchors_reconcile(seed_user["scenario"].id)

    def test_a_settle_that_rides_on_top_does_not_rewalk_an_opened_ledger(
        self, app, db, seed_user, monkeypatch,
    ):
        """The skip survives: an ordinary settle re-derives nothing.

        The non-vacuity twin of the test above.  The fix widened the
        self-heal's fire condition, so this pins that it did not widen it to
        "always": a settle in a scenario that already carries its corrections,
        dated after every assertion, must not walk the account at all.

        **It counts the CALL, not the effect, and it has to.**  The reconcile
        is idempotent -- running it here writes nothing either way -- so an
        assertion about entries cannot tell a skipped walk from a performed
        one, and a test that claimed to pin the skip while asserting entry
        counts would pass with the skip deleted.  The spy delegates, so the
        ledger still reconciles and the sibling assertions below are real.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_posting_service import _sync

        with app.app_context():
            checking = seed_user["account"]
            opened = datetime(2026, 1, 2, 9, tzinfo=timezone.utc)
            restamp_opening_assertion(db.session, checking, opened)
            # **The BOOKS go back further than the restamp puts them** (plan
            # step X-f3c-2b, ruling **R-HG**).  The second settle below is
            # dated 2025-12-01 on purpose -- BEFORE the assertion, which is
            # what makes the staleness arm fire -- and ``restamp_opening_
            # assertion`` would open the books one day before the assertion,
            # which is after it.  Before the ASSERTION and after the BOOKS is
            # exactly the span this case needs, and is the production shape.
            restate_account_opening(db.session, checking, date(2025, 11, 30))
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            # First settle: the scenario is already open (the baseline carries
            # its corrections from account-create time), so this must skip.
            real = _sync.sync_account_anchor_postings
            calls: list[tuple[int, int]] = []

            def _spy(account_id, sync_scenario_id):
                calls.append((account_id, sync_scenario_id))
                real(account_id, sync_scenario_id)

            monkeypatch.setattr(
                _sync, "sync_account_anchor_postings", _spy,
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=checking,
                settled_on=date(2026, 3, 3),
            )
            db.session.commit()

            assert calls == [], (
                "an on-top settle in an already-opened scenario walked the "
                f"account anyway: {calls}"
            )
            # ...and a settle dated BEFORE the assertion still fires, so the
            # staleness arm is not what was disabled.
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("25.00"), account=checking,
                settled_on=date(2025, 12, 1),
            )
            db.session.commit()
            assert (checking.id, scenario_id) in calls

            # The opening ABSORBS the pre-assertion settle (nothing rides on
            # top of it) while the on-top one reduces the balance:
            # 1000 - 40 = $960.00.
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("960.00")
            _assert_account_anchors_reconcile(scenario_id)

    def test_scenarios_reconcile_independently(self, app, db, seed_user):
        """Checking's opening + sources reconcile per scenario, never bleeding.

        The seeded Checking's $1000.00 opening lives in the baseline.  A $40.00
        baseline expense and a $70.00 what-if expense (both settled at
        server-now, the same UTC day as the account's origination, so the
        effect-time self-heal posts Checking's opening into the what-if too) land
        on it.  Scoped to the baseline the Checking ledger is 1000 - 40 = 960.00;
        scoped to the what-if it is 1000 - 70 = 930.00 -- never the
        cross-scenario 890.  Each scenario carries its OWN copy of the opening
        and its OWN sources, and reconciles independently.

        Same accepted same-UTC-day caveat as the sibling cash oracle: the
        what-if opening posts only because the settle's civil day is at or before
        Checking's origination assertion, so the effect-time self-heal fires --
        not a general multi-scenario guarantee (R8 owns that policy).
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            checking = seed_user["account"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=checking, scenario=baseline,
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=checking, scenario=whatif,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                checking.id, baseline.id,
            ) == Decimal("960.00")
            assert posting_service.account_posting_total(
                checking.id, whatif.id,
            ) == Decimal("930.00")
            assert _independent_linked_ledger_sum(
                checking.id, baseline.id,
            ) == Decimal("960.00")
            assert _independent_linked_ledger_sum(
                checking.id, whatif.id,
            ) == Decimal("930.00")

            _assert_account_anchors_reconcile(baseline.id)
            _assert_account_anchors_reconcile(whatif.id)

    @pytest.mark.server_clock
    def test_owners_reconcile_independently(
        self, app, db, seed_user, seed_second_user,
    ):
        """Two owners settle on their own savings; neither sweep sees the other.

        Owner 1 settles a $60.00 expense on a $400.00 Savings (ledger 340.00);
        owner 2 settles an $80.00 expense on a $900.00 Savings (ledger 820.00).
        Each owner's sweep reconciles only their own accounts, and a ``Posting``
        carries no ``user_id`` (ownership is reachable only through its journal
        entry).
        """
        with app.app_context():
            scenario1 = seed_user["scenario"].id
            scenario2 = seed_second_user["scenario"].id
            savings1 = create_account_of_type(
                seed_user, db.session, "Savings", "Owner1 Savings",
                anchor_balance=Decimal("400.00"),
            )
            savings2 = create_account_of_type(
                seed_second_user, db.session, "Savings", "Owner2 Savings",
                anchor_balance=Decimal("900.00"),
            )
            db.session.commit()
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("60.00"), account=savings1,
            )
            create_settled_cash_transaction(
                seed_second_user, db.session,
                seed_second_user["bootstrap_period"],
                Decimal("80.00"), account=savings2,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                savings1.id, scenario1,
            ) == Decimal("340.00")
            assert posting_service.account_posting_total(
                savings2.id, scenario2,
            ) == Decimal("820.00")
            # A Posting has no user_id; ownership is normalized onto the entry.
            assert not hasattr(Posting, "user_id")
            owner1_id = seed_user["user"].id
            owner2_id = seed_second_user["user"].id
            for posting in _db.session.query(Posting).all():
                entry_owner = posting.journal_entry.user_id
                assert entry_owner in (owner1_id, owner2_id)
                assert posting.ledger_account.user_id == entry_owner

            _assert_account_anchors_reconcile(scenario1)
            _assert_account_anchors_reconcile(scenario2)


# ---------------------------------------------------------------------------
# 9. Backfill == go-forward (the historical sweep reproduces the wiring)
# ---------------------------------------------------------------------------


class TestBackfillEqualsGoForward:
    """Clearing then backfilling reproduces the go-forward ledger exactly."""

    @pytest.mark.server_clock
    def test_backfill_restores_the_absolute_ledger(self, app, db, seed_user):
        """A multi-anchor account's ledger is identical after clear + backfill.

        A Savings anchored $500.00 with a pre-true-up spend ($200 at T+1h), a
        true-up to $350.00 (T+2h), and a post-true-up spend ($100 at T+3h) posts
        its opening + true-up go-forward, reaching linked ledger 250.00.  The
        boundary migration's raw-SQL teardown clears every account correction +
        equity twin (the pre-C6 historical state), leaving the ledger short by
        the corrections; the deploy backfill re-derives them through the SAME
        go-forward sync, restoring the exact 250.00 and re-minting the equity
        twin.  The whole-DB sweep ties before AND after -- backfill ==
        go-forward by construction.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Backfill Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            origin = _origin_instant(savings)
            _settle_expense(
                seed_user, savings, "200.00",
                observed_day_of(origin + timedelta(days=1)),
            )
            _true_up_at(savings, "350.00", origin + timedelta(days=2))
            _settle_expense(
                seed_user, savings, "100.00",
                observed_day_of(origin + timedelta(days=3)),
            )
            db.session.commit()
            forward_total = posting_service.account_posting_total(
                savings.id, scenario_id,
            )
            assert forward_total == Decimal("250.00")
            _assert_account_anchors_reconcile(scenario_id)

            # Reproduce the pre-C6 historical state: corrections + twins gone.
            _BOUNDARY_MIGRATION._remove_account_anchor_postings(db.session)
            db.session.commit()
            assert ledger_account_of_kind(
                db.session, savings.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None
            # The ledger is now the bare source legs (no opening / true-up).
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) != forward_total

            # The deploy backfill re-derives the corrections identically.
            account_posting_service.backfill_all_account_anchor_postings()
            db.session.commit()

            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == forward_total
            assert ledger_account_of_kind(
                db.session, savings.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is not None
            _assert_account_anchors_reconcile(scenario_id)


# ---------------------------------------------------------------------------
# 10. Adversarial: the oracle is not vacuous (it fails on real breakage)
# ---------------------------------------------------------------------------


class TestOracleIsNotVacuous:
    """Prove the reconciliation and trial-balance checks catch real breakage."""

    def test_tampered_latest_anchor_makes_the_sweep_fail(
        self, app, db, seed_user,
    ):
        """Forcing the latest anchor balance off its posted value breaks the sweep.

        A reconciled $500.00 Savings has linked ledger 500.00 and latest anchor
        500.00.  Forcing the anchor-history row's balance to 999 via raw SQL
        leaves the posted ledger at 500.00 but pushes the invariant's RHS to
        999 + 0 -- so the per-account reconciliation the oracle relies on now
        FAILS.  Driven through the REAL sweep helper under ``pytest.raises`` (so
        a regression in the helper itself is caught, not only an inline
        re-derivation), with ``match`` pinning the anchor comparison message
        specifically -- the tooth cannot be lost undetected.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Tamper Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            # Reconciled before tampering.
            _assert_account_anchors_reconcile(scenario_id)

            # Tamper the latest anchor balance (history carries no balance
            # trigger, so this commits); the posted ledger is unchanged.
            row_id = (
                _db.session.query(AccountAnchorHistory.id)
                .filter_by(account_id=savings.id)
                .order_by(
                    AccountAnchorHistory.created_at.desc(),
                    AccountAnchorHistory.id.desc(),
                )
                .scalar()
            )
            db.session.execute(_db.text(
                "UPDATE budget.account_anchor_history "
                "SET anchor_balance = 999 WHERE id = :i"
            ), {"i": row_id})
            db.session.commit()

            # Ledger unchanged (500), but the anchor truth drifted (999) -- the
            # absolute invariant no longer holds, so the real sweep raises.
            assert _independent_linked_ledger_sum(
                savings.id, scenario_id,
            ) == Decimal("500.00")
            _, latest_anchor = _latest_assertion(savings.id)
            assert latest_anchor == Decimal("999.00")
            with pytest.raises(AssertionError, match="latest anchor"):
                _assert_account_anchors_reconcile(scenario_id)

    def test_trial_balance_catches_an_injected_leg(self, app, db, seed_user):
        """Injecting one extra leg pushes the trial balance off zero.

        A balanced book has trial balance 0.00.  A $500.00 Savings opening posts
        a balanced +500 / -500 pair; inserting one unmatched +50 leg onto its
        opening entry (raw SQL, flushed but never committed, so the DEFERRED
        per-entry trigger never fires) makes the whole-ledger sum 0 + 50 =
        50.00 -- so the trial-balance ``= 0`` assertion is a real check, not one
        the per-entry trigger makes vacuously true.  Rolled back so the leg
        never lands.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Inject Savings",
                anchor_balance=Decimal("500.00"),
            )
            db.session.commit()
            assert _trial_balance() == Decimal("0.00")

            # Inject one extra, unmatched leg onto the opening entry (picked by
            # its correction source).  Flush (not commit) makes it visible; the
            # DEFERRED balanced trigger validates only at COMMIT, never reached.
            linked = linked_ledger_account(_db.session, savings.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            # The LATEST opening entry, and named as a choice rather than
            # taken as the only one.  Since plan step X-f3c-2b the factory
            # restates an account's books, and a restatement REVERSES the
            # opening entry and re-posts it -- three opening-sourced entries
            # where there used to be one, which is production's own shape
            # after the same act.  Any of them carries a leg on this ledger,
            # so the injection below is equally unbalanced whichever is
            # picked; ``.scalar()`` over the set would simply raise.
            entry_id = (
                _db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .order_by(JournalEntry.id.desc())
                .limit(1)
                .scalar()
            )
            assert entry_id is not None, (
                "no opening entry to inject into -- this class's whole name is "
                "a promise that it is not vacuous, and a None here would "
                "inject nothing and still pass"
            )
            _db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked.id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.OPENING),
            })
            _db.session.flush()

            assert _trial_balance() == Decimal("50.00")  # 0.00 + 50.00
            assert _trial_balance() != Decimal("0.00")

            # Discard the injected leg; the deferred trigger never fires.
            _db.session.rollback()


# ---------------------------------------------------------------------------
# 11. F1: a settled transfer's attribution mutation stays reconciled
# ---------------------------------------------------------------------------


def _transfer_net_in_period(account_id, scenario_id, period_id) -> Decimal:
    """Sum an account's transfer-linked LINKED-ledger legs in one pay period.

    The transfer cash on *account_id*'s linked ledger (entries carrying a
    ``transfer_id``) scoped to a single ``pay_period_id`` -- so a settled period
    move can be checked to have moved the effect (R2): the old period nets to
    zero and the new period carries it.
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
            JournalEntry.pay_period_id == period_id,
            JournalEntry.transfer_id.isnot(None),
        )
        .scalar()
    )


class TestSettledTransferAttributionMutation:
    """A settled transfer's period / settle-day edit keeps the anchor sound (F1)."""

    @pytest.mark.server_clock
    def test_settled_period_move_reposts_and_reconciles(
        self, app, db, seed_user,
    ):
        """Moving a settled transfer's period re-posts its cash (R2) and reconciles.

        A $150.00 Checking -> Savings transfer settled in the bootstrap period
        (riding on top of both openings; Savings 200 + 150 = 350).  Moving it to
        a second period through ``update_transfer`` -- the settled-row period
        edit the C6 review M2 flagged as unreconciled -- now fires the Step-2
        reconcile because ``pay_period_id`` is in ``_POSTING_RELEVANT_FIELDS``:
        the Savings transfer cash nets to ZERO in the old period and +150.00 in
        the new one (R2), the account total is unchanged (350.00, no
        double-count), and the absolute invariant still holds with NO manual
        account-posting sync.  Before F1 the period edit skipped the reconcile,
        stranding the cash in the old period.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Move Savings",
                anchor_balance=Decimal("200.00"),
            )
            period2 = PayPeriod(
                user_id=user_id, start_date=date(2026, 2, 6),
                end_date=date(2026, 2, 19), period_index=1,
            )
            db.session.add(period2)
            db.session.commit()

            transfer = create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            _assert_account_anchors_reconcile(scenario_id)

            # Move the SETTLED transfer's period through the service.
            transfer_service.update_transfer(
                transfer.id, user_id, pay_period_id=period2.id,
            )
            db.session.commit()

            # R2: the transfer cash moved to the new period (old nets to zero),
            # with no double-count in the account total.
            assert _transfer_net_in_period(
                savings.id, scenario_id, seed_user["bootstrap_period"].id,
            ) == Decimal("0.00")
            assert _transfer_net_in_period(
                savings.id, scenario_id, period2.id,
            ) == Decimal("150.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            _assert_account_anchors_reconcile(scenario_id)

    @pytest.mark.server_clock
    def test_settle_day_move_across_the_anchor_reconciles(
        self, app, db, seed_user,
    ):
        """Moving a settled transfer's settle day across the anchor re-derives openings.

        A $150.00 Checking -> Savings transfer settled AFTER both origination
        anchors (rides on top): Savings 200 + 150 = 350, Checking 1000 - 150 =
        850.  Moving its settle day to BEFORE the anchors (a 2024 day)
        through ``update_transfer`` makes the transfer PRE-assertion, so it must
        be ABSORBED into the openings.  The settle day changes no leg, so the
        Step-2 reconcile-to-target writes nothing and its effect-time self-heal
        never fires; the F1 direct resync re-derives both endpoints' openings so
        the absolute invariant still holds with NO manual sync.  Post-move both
        accounts read their anchors (Savings 200, Checking 1000): the transfer
        is absorbed, nothing rides on top.  Before F1 the openings stayed at
        their ride-on-top values (Savings 350) while the invariant's RHS dropped
        to 200 -- a silently stale correction.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "PaidAt Savings",
                anchor_balance=Decimal("200.00"),
            )
            db.session.commit()

            transfer = create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("350.00")
            _assert_account_anchors_reconcile(scenario_id)

            # Move the settle day BEFORE both origination anchors (server-now 2026).
            transfer_service.update_transfer(
                transfer.id, user_id,
                settle_day=an_entered_day(date(2024, 1, 5)),
            )
            db.session.commit()

            # Pre-assertion now: absorbed into the openings, nothing on top.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            latest_asserted_day, _latest = _latest_assertion(savings.id)
            assert _independent_post_assertion_source_effect(
                savings.id, scenario_id, latest_asserted_day,
            ) == Decimal("0.00")
            _assert_account_anchors_reconcile(scenario_id)
