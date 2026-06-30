"""Tests for ``posting_service`` (Build-Order Steps 2 and 3).

``posting_service`` is the sole go-forward writer of the double-entry posting
ledger.  Step 2 posts settled **transfers**
(:func:`~app.services.posting_service.sync_transfer_postings`); Step 3 (Commit
4) adds settled ordinary **cash transactions**
(:func:`~app.services.posting_service.sync_transaction_postings`).  Both
reconcile a source's net posted ledger effect to a target by emitting one
balanced delta journal entry, idempotently.
:func:`~app.services.posting_service.account_posting_total`,
:func:`~app.services.posting_service.settled_transfer_effect`, and
:func:`~app.services.posting_service.settled_transaction_effect` are the
reconciliation helpers the oracle consumes.

The transfer tests pin the load-bearing properties with hand-computed
arithmetic:

  * **Sign + balance** -- a settle posts ``-amount`` on the from-account's
    ledger and ``+amount`` on the to-account's, summing to zero; the rule is
    class-independent (asset->asset AND asset->liability).
  * **Effective amount, not transfer amount** -- a settled shadow
    ``actual_amount`` overrides the nominal transfer amount (the value the
    balance calculator and the oracle use).
  * **Idempotency** -- a repeat settle computes ``delta = 0`` and writes
    nothing.
  * **Reversal reads the ledger** -- a revert / delete negates exactly what
    was posted, not the (possibly-edited) transfer amount; a revert ->
    edit-amount -> re-settle posts the new amount.
  * **Reconciliation** -- the per-account posting total equals the
    settled-shadow effect, and both net to zero after a reversal.
  * **Fail loud** -- a ``None`` scenario, a missing ledger-account pairing,
    and an unbalanced set of legs each raise :class:`PostingError`.

The transfer states are built through ``transfer_service`` (the sole transfer
writer, via the ``create_settled_transfer`` helper), so every shadow obeys
the transfer invariants exactly as production produces them.  Commit 5 wires
``posting_service`` into that service, so settling a transfer already
auto-posts its ledger entry; the settle tests below read that auto-posted
entry back, while the idempotency / reversal tests still invoke
``posting_service`` directly to prove a re-sync no-ops or reverses.

The Step-3 transaction tests (the ``TestTransaction*`` /
``TestSettledTransactionEffect`` classes at the foot of the file) cover the
plan's one cash-effect formula -- ``effective - Sigma(credit entries)``, signed
``+`` income / ``-`` expense -- with hand-computed worked examples (plain
income/expense, the debit-only envelope effect, the all-credit no-op), the
correct-by-construction reconcile (idempotency; reversal; the 2.8 CRITICAL
revert -> recategorize -> re-settle, proven at the service layer), counter-leg
routing into the per-category / Uncategorized-fallback account, the transfer-
shadow no-op guard, and fail-loud.  Step 3 has no service wiring yet (that is
Commit 6), so these tests build settled rows via direct ORM and invoke
``sync_transaction_postings`` directly.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; test bodies bind fixtures by name.
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service, posting_service, transfer_service
from app.services.posting_service import (
    PostingError,
    _emit_balanced_entry,
    _PostingLeg,
)
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    create_envelope_txn,
    create_settled_transfer,
    ledger_accounts_for_account,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger_id(account):
    """Return the linked ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


def _entries_for_transfer(transfer_id):
    """Return every journal entry for *transfer_id*, oldest first."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transfer_id=transfer_id)
        .order_by(JournalEntry.id)
        .all()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for one entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


def _scenario_id(seed_user):
    """Return the seed user's baseline scenario id."""
    return seed_user["scenario"].id


def _entries_for_transaction(transaction_id):
    """Return every journal entry for *transaction_id*, oldest first."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transaction_id=transaction_id)
        .order_by(JournalEntry.id)
        .all()
    )


def _ledger_total(ledger_account_id):
    """Return the net of all posting legs on one ledger account.

    Sums ``account_postings.amount`` directly by ledger account id -- the
    counter-account (category / fallback) analog of
    ``posting_service.account_posting_total``, which keys off a REAL account's
    linked ledger and so cannot be pointed at a category ledger account.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .filter(Posting.ledger_account_id == ledger_account_id)
        .scalar()
    )


def _add_txn_entry(seed_user, txn, amount, *, is_credit):
    """Attach one purchase entry (debit or credit) to *txn* and flush.

    The shared ``add_entry`` helper only builds debit entries; the envelope
    tests need explicit ``is_credit`` control to exercise the
    ``effective - Sigma(credit)`` formula, so this sets it directly.
    """
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="purchase",
        entry_date=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    _db.session.add(entry)
    _db.session.flush()
    return entry


def _resolve_category_ledger(seed_user, category_key, ledger_class):
    """Return the category ledger account for a seed category and class.

    Idempotent: returns the row ``sync_transaction_postings`` created during
    the test (the resolver respects the partial unique), so a leg's
    ``ledger_account_id`` can be asserted against it.  Passing
    ``category_key=None`` resolves the per-(owner, class) Uncategorized
    fallback.
    """
    category_id = (
        None if category_key is None
        else seed_user["categories"][category_key].id
    )
    return ledger_account_service.get_or_create_category_ledger_account(
        seed_user["user"].id, category_id, ledger_class,
    )


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer has a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned account stays bound to the live session, the same pattern
    ``seed_user`` uses.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Posting Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Settle: one balanced entry per confirmed transfer
# ---------------------------------------------------------------------------


class TestSyncSettlePostsBalancedEntry:
    """A settled transfer posts exactly one balanced two-leg entry."""

    def test_asset_to_asset_signs_balance_and_metadata(
        self, app, db, seed_user, savings,
    ):
        """Checking -> Savings $100 posts -100 / +100, summing to zero.

        Arithmetic (plan Section 1): the from leg is -100.00 (a credit: money
        leaving Checking), the to leg is +100.00 (a debit: money entering
        Savings); -100.00 + 100.00 = 0.00.  Both ledgers are Asset class, but
        the builder never branches on class -- the sign follows direction.
        Also pins the header metadata (source kind, transfer link, owner,
        scenario, period) and the per-leg posting kind.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)

            # Commit-5 wiring: settling through the transfer service already
            # auto-posted the entry; read it back (a re-sync would no-op).
            entry = _entries_for_transfer(transfer.id)[0]

            # Header metadata.
            assert entry.transfer_id == transfer.id
            assert entry.user_id == seed_user["user"].id
            assert entry.scenario_id == _scenario_id(seed_user)
            assert entry.pay_period_id == seed_user["bootstrap_period"].id
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER,
            )
            assert entry.description == "Transfer: Checking to Posting Savings"
            # entry_date is a concrete civil date: the server-side
            # ``db.func.now()`` paid_at (create_settled_transfer's default) was
            # materialized, not left as an unresolved SQL expression.
            assert isinstance(entry.entry_date, date)
            # Legs: -100 from Checking, +100 to Savings, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("-100.00")
            assert legs[savings_ledger] == Decimal("100.00")
            assert sum(legs.values()) == Decimal("0.00")
            # Every leg carries the transfer posting kind.
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
            }
            # Exactly one entry for the transfer.
            assert len(_entries_for_transfer(transfer.id)) == 1

    def test_asset_to_liability_signs(self, app, db, seed_user):
        """Checking -> Mortgage $250 posts -250 / +250 (pay-down).

        Arithmetic (plan Section 1, second worked example): paying down a
        liability is still from=-amount / to=+amount.  -250.00 on the Asset
        Checking ledger, +250.00 on the Liability Mortgage ledger, summing to
        zero -- the sign rule is class-independent.
        """
        with app.app_context():
            mortgage = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Posting Mortgage",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], mortgage,
                seed_user["bootstrap_period"], amount=Decimal("250.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            mortgage_ledger = _ledger_id(mortgage)

            # Commit-5 wiring: the mortgage pay-down auto-posted on settle;
            # read the entry back (a re-sync would no-op).
            entry = _entries_for_transfer(transfer.id)[0]

            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("-250.00")
            assert legs[mortgage_ledger] == Decimal("250.00")
            assert sum(legs.values()) == Decimal("0.00")

    def test_settle_uses_effective_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A settled shadow ``actual_amount`` overrides the transfer amount.

        The transfer's nominal amount is $100, but the settled actual is
        $97.50 (mirrored to both shadows), so the shadow ``effective_amount``
        is $97.50 -- the value the balance calculator and the oracle use.  The
        posting must be -97.50 / +97.50, NOT -100 / +100 (the plan Section 5
        prose said ``xfer.amount``; the correct, oracle-reconciling value is
        the shadow effective amount, matching the Commit-3 backfill).
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                actual_amount=Decimal("97.50"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)

            # Commit-5 wiring: the divergent settled actual auto-posted; read
            # the entry back (a re-sync would no-op).
            entry = _entries_for_transfer(transfer.id)[0]

            legs = _legs_by_ledger(entry.id)
            assert legs[checking_ledger] == Decimal("-97.50")
            assert legs[savings_ledger] == Decimal("97.50")


class TestSyncSettleEntryDate:
    """``entry_date`` is the shadow paid_at (UTC), else the period start."""

    def test_entry_date_from_paid_at_utc_not_display_tz(
        self, app, db, seed_user, savings,
    ):
        """A settled paid_at maps to its UTC civil date, NOT the display tz.

        Arithmetic: paid_at 2026-05-10 02:00 UTC is 2026-05-09 22:00 in the
        America/New_York display timezone (UTC-4 in May), so the UTC civil
        date (2026-05-10) and the Eastern civil date (2026-05-09) differ.  The
        entry date must be the UTC date 2026-05-10 -- the Python counterpart of
        the backfill's ``(paid_at AT TIME ZONE 'UTC')::date`` and the app's
        UTC storage convention.  A display-timezone conversion would wrongly
        yield 2026-05-09 and fail this assertion (the regression guard).
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                paid_at=datetime(2026, 5, 10, 2, 0, tzinfo=timezone.utc),
            )
            _db.session.commit()
            # Commit-5 wiring: the settle auto-posted; read the entry back.
            entry = _entries_for_transfer(transfer.id)[0]
            # UTC civil date 2026-05-10, NOT the Eastern 2026-05-09.
            assert entry.entry_date == date(2026, 5, 10)

    def test_entry_date_falls_back_to_period_start_when_paid_at_null(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer with NULL paid_at uses the pay-period start.

        ``entry_date`` is NOT NULL; with no ``paid_at`` recorded (a historical
        settle, or a reverted shadow), the entry date is the period's
        ``start_date`` (here the bootstrap period's 2024-01-05).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                period, amount=Decimal("100.00"), paid_at=None,
            )
            _db.session.commit()
            # Commit-5 wiring: the settle auto-posted; read the entry back.
            entry = _entries_for_transfer(transfer.id)[0]
            assert entry.entry_date == period.start_date


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestSyncIdempotency:
    """A repeat sync at the same target writes nothing."""

    def test_repeat_settle_is_noop(self, app, db, seed_user, savings):
        """Re-syncing an already-auto-posted settle returns None, no 2nd entry.

        Arithmetic: ``create_settled_transfer`` auto-posted +100 to the Savings
        ledger (Commit-5 wiring), so both manual re-syncs see current 100 ==
        target 100, delta 0, and write nothing.  This is the double-mark-done
        guard -- a repeated settle never double-posts.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            # Both manual re-syncs are no-ops: create_settled_transfer already
            # auto-posted the +100 entry, so current == target and delta == 0.
            first = posting_service.sync_transfer_postings(
                transfer, settled=True,
            )
            second = posting_service.sync_transfer_postings(
                transfer, settled=True,
            )
            _db.session.commit()

            assert first is None
            assert second is None
            assert len(_entries_for_transfer(transfer.id)) == 1

    def test_cancel_with_nothing_posted_is_noop(
        self, app, db, seed_user, savings,
    ):
        """settled=False on a never-posted transfer writes nothing.

        Arithmetic: current 0, target 0, delta 0 -> no entry.  This is the
        projected -> cancelled path (a transfer cancelled before it ever
        settled has no ledger effect to reverse).
        """
        with app.app_context():
            transfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_user["bootstrap_period"].id,
                    scenario_id=_scenario_id(seed_user),
                    amount=Decimal("100.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            _db.session.commit()

            result = posting_service.sync_transfer_postings(
                transfer, settled=False,
            )
            _db.session.commit()

            assert result is None
            assert _entries_for_transfer(transfer.id) == []


# ---------------------------------------------------------------------------
# Reversal: negate exactly what was posted
# ---------------------------------------------------------------------------


class TestSyncReversal:
    """A reversal reads the posted amount back from the ledger."""

    def test_reverse_negates_posted_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """Reverting posts the negation of what is posted, ignoring xfer.amount.

        Arithmetic: settle posts +100 to the Savings ledger.  Then the
        transfer amount is mutated to 999 (the value a naive
        ``target = xfer.amount`` reversal would use).  The reversal instead
        reads the posted net (+100) and posts the delta to reach 0:
        0 - 100 = -100 on Savings, +100 on Checking.  The Savings ledger nets
        to zero; the reversal leg is -100, NOT -999.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)

            posting_service.sync_transfer_postings(transfer, settled=True)
            _db.session.commit()

            # Mutate the transfer amount to a value a naive reversal would
            # wrongly use; the reversal must still negate the posted 100.
            transfer.amount = Decimal("999.00")
            _db.session.flush()

            reversal = posting_service.sync_transfer_postings(
                transfer, settled=False,
            )
            _db.session.commit()

            assert reversal is not None
            legs = _legs_by_ledger(reversal.id)
            assert legs[savings_ledger] == Decimal("-100.00")
            assert legs[checking_ledger] == Decimal("100.00")
            # The Savings ledger now nets to zero (settled then reversed).
            assert posting_service.account_posting_total(
                savings.id, _scenario_id(seed_user),
            ) == Decimal("0.00")
            # Two entries survive (append-only correction, never an edit).
            assert len(_entries_for_transfer(transfer.id)) == 2

    def test_revert_edit_amount_resettle_posts_new_amount(
        self, app, db, seed_user, savings,
    ):
        """A revert -> edit-amount -> re-settle posts the new amount.

        Arithmetic: settle $100 (+100), revert (-100, net 0), edit the amount
        to $150, re-settle (current 0 -> target 150, delta +150).  The Savings
        ledger nets to +150 across the three entries, matching the new settled
        shadow effect.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            posting_service.sync_transfer_postings(transfer, settled=True)
            _db.session.commit()

            # Revert to Projected, then reverse the posting.
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            posting_service.sync_transfer_postings(transfer, settled=False)
            _db.session.commit()

            # Edit the amount while Projected, then re-settle and re-post.
            transfer_service.update_transfer(
                transfer.id, user_id, amount=Decimal("150.00"),
            )
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                paid_at=_db.func.now(),
            )
            posting_service.sync_transfer_postings(transfer, settled=True)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            # +100 (settle) - 100 (reverse) + 150 (re-settle) = +150.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("150.00")
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("150.00")
            # Three entries: settle, reverse, re-settle.
            assert len(_entries_for_transfer(transfer.id)) == 3


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------


class TestReconciliationHelpers:
    """The per-account posting total equals the settled-shadow effect."""

    def test_helpers_match_after_settle_both_accounts(
        self, app, db, seed_user, savings,
    ):
        """After a $100 settle, both helpers agree on both accounts.

        Arithmetic: Savings (the to-account, income shadow) is +100; Checking
        (the from-account, expense shadow) is -100.  ``account_posting_total``
        (sum over postings) and ``settled_transfer_effect`` (sum over settled
        shadows) compute the same number from independent tables.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            posting_service.sync_transfer_postings(transfer, settled=True)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            checking = seed_user["account"]
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("-100.00")
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-100.00")

    def test_helpers_net_to_zero_after_reverse(
        self, app, db, seed_user, savings,
    ):
        """A settled-then-reverted transfer reconciles at zero on both sides.

        Arithmetic: the postings net to zero (+100 then -100); the reverted
        income shadow is no longer settled, so the settled-shadow effect drops
        it to zero too.  Both helpers return 0 for both accounts.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            posting_service.sync_transfer_postings(transfer, settled=True)
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            posting_service.sync_transfer_postings(transfer, settled=False)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            for account in (savings, seed_user["account"]):
                assert posting_service.account_posting_total(
                    account.id, scenario_id,
                ) == Decimal("0.00")
                assert posting_service.settled_transfer_effect(
                    account.id, scenario_id,
                ) == Decimal("0.00")

    def test_settled_effect_uses_effective_amount(
        self, app, db, seed_user, savings,
    ):
        """The settled-shadow effect honours a divergent ``actual_amount``.

        Arithmetic: nominal $100, settled actual $97.50, so both helpers (and
        the posting) reconcile at +97.50 on the Savings to-account.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                actual_amount=Decimal("97.50"),
            )
            _db.session.commit()
            posting_service.sync_transfer_postings(transfer, settled=True)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("97.50")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("97.50")


# ---------------------------------------------------------------------------
# Fail loud
# ---------------------------------------------------------------------------


class TestFailLoud:
    """Broken invariants raise PostingError rather than posting silently."""

    def test_account_posting_total_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``account_posting_total`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.account_posting_total(
                    seed_user["account"].id, None,
                )

    def test_settled_transfer_effect_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``settled_transfer_effect`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.settled_transfer_effect(
                    seed_user["account"].id, None,
                )

    def test_missing_ledger_account_fails_loud(
        self, app, db, seed_user, savings,
    ):
        """A transfer whose account has no ledger pairing raises PostingError.

        Removing the Savings account's linked ledger row (an impossible state
        in production -- every account is paired) makes the settle sync fail
        loudly rather than post a one-legged or silently-wrong entry.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Drop the to-account's ledger pairing via raw SQL.
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": savings.id})
            _db.session.commit()

            with pytest.raises(PostingError, match="ledger account"):
                posting_service.sync_transfer_postings(transfer, settled=True)

    def test_settle_missing_income_shadow_fails_loud(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer with no active income shadow raises PostingError.

        Removing the to-account income shadow (an impossible
        Transfer-Invariant-1 violation in production -- a transfer always has
        its two shadows) makes the settle sync fail loudly rather than post a
        one-legged or silently-wrong entry.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Remove the income shadow (the income-type row on the to-account)
            # via raw SQL.
            _db.session.execute(_db.text(
                "DELETE FROM budget.transactions "
                "WHERE transfer_id = :t AND account_id = :a"
            ), {"t": transfer.id, "a": savings.id})
            _db.session.commit()

            with pytest.raises(PostingError, match="income shadow"):
                posting_service.sync_transfer_postings(transfer, settled=True)

    def test_emit_balanced_entry_rejects_single_leg(self, app, db, seed_user):
        """The builder refuses an entry with fewer than two legs.

        The service-side backstop for the ``COUNT(*) >= 2`` half of the
        deferred balanced-journal trigger: a one-legged entry fails loudly at
        the call site before any write.
        """
        with app.app_context():
            with pytest.raises(PostingError, match="at least 2 legs"):
                _emit_balanced_entry(
                    JournalEntry(),
                    [_PostingLeg(1, Decimal("100.00"), 1)],
                )

    def test_emit_balanced_entry_rejects_unbalanced_legs(
        self, app, db, seed_user,
    ):
        """The builder refuses legs that do not sum to zero.

        The service-side backstop for the ``SUM(amount) = 0`` half of the
        deferred balanced-journal trigger: an unbalanced pair (+100 / +50,
        summing to +150) fails loudly at the call site before any write.
        """
        with app.app_context():
            with pytest.raises(PostingError, match="sum to 0"):
                _emit_balanced_entry(
                    JournalEntry(),
                    [
                        _PostingLeg(1, Decimal("100.00"), 1),
                        _PostingLeg(2, Decimal("50.00"), 1),
                    ],
                )


# ===========================================================================
# Build-Order Step 3: ordinary cash transaction postings
# ===========================================================================


class TestTransactionSettlePostsBalancedEntry:
    """A settled ordinary transaction posts one balanced two-leg entry."""

    def test_plain_expense_signs_balance_and_metadata(
        self, app, db, seed_user,
    ):
        """A $50 Paid Groceries expense posts -50 / +50, summing to zero.

        Arithmetic (plan Section 1): a plain expense has no entries, so the
        effect is ``effective_amount`` (50) with the expense sign; the cash
        leg is -50.00 (a credit: money leaving Checking) and the category leg
        is +50.00 (a debit: the expense lands in Food: Groceries).  -50.00 +
        50.00 = 0.00.  Also pins the header metadata (source kind, transaction
        link, owner / scenario / period -- all sourced from
        ``txn.pay_period``) and the per-leg posting kind (expense).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            # Header metadata, all from txn / txn.pay_period.
            assert entry.transaction_id == txn.id
            assert entry.transfer_id is None
            assert entry.user_id == seed_user["user"].id
            assert entry.scenario_id == _scenario_id(seed_user)
            assert entry.pay_period_id == period.id
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION,
            )
            assert entry.description == "Groceries"
            assert isinstance(entry.entry_date, date)
            # Legs: -50 from Checking, +50 to Groceries, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-50.00")
            assert legs[groceries_ledger] == Decimal("50.00")
            assert sum(legs.values()) == Decimal("0.00")
            # Every leg carries the expense posting kind.
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            }
            assert len(_entries_for_transaction(txn.id)) == 1

    def test_income_signs(self, app, db, seed_user):
        """A $2000 Received Salary income posts +2000 / -2000.

        Arithmetic (plan Section 1, second worked example): income has no
        entries, so the effect is ``effective_amount`` (2000) with the income
        sign; the cash leg is +2000.00 (a debit: money entering Checking) and
        the category leg is -2000.00 (a credit: income earned in Income:
        Salary).  The sign follows the transaction TYPE, never the account
        class.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            salary_ledger = _resolve_category_ledger(
                seed_user, "Salary", LedgerAccountClassEnum.INCOME,
            ).id

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("2000.00")
            assert legs[salary_ledger] == Decimal("-2000.00")
            assert sum(legs.values()) == Decimal("0.00")
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
            }

    def test_expense_uses_effective_actual_not_estimated(
        self, app, db, seed_user,
    ):
        """A manual ``actual_amount`` (not the estimate) drives the cash leg.

        Arithmetic: estimated $50 but settled actual $45, so
        ``effective_amount`` is 45 (``actual_amount`` overrides), and the
        expense cash leg is -45.00, NOT -50.00.  Locks that the service reads
        the ``effective_amount`` property (the value the balance calculator
        and the oracle use), not the raw estimate.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                actual_amount="45.00",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-45.00")
            assert legs[groceries_ledger] == Decimal("45.00")

    def test_envelope_posts_debit_only_effect(self, app, db, seed_user):
        """A settled envelope posts the DEBIT-only outflow (credit excluded).

        Arithmetic (plan Section 1 worked example): a $200 Groceries envelope
        with entries $60 debit / $50 debit / $40 credit, marked Paid.  At
        settle ``actual_amount`` = sum of ALL entries = 150, so
        ``effective`` = 150 and ``effect = effective(150) - credit_sum(40) =
        110`` of debit spending.  The cash leg is -110.00 (the two debit
        purchases) and the Groceries leg +110.00; the $40 credit purchase
        posts nothing here (its CC Payback posts when it settles), so there is
        no double-count.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            _add_txn_entry(seed_user, txn, "60.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "50.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            # Simulate settle_from_entries: actual = sum(all entries), Paid.
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            txn.actual_amount = Decimal("150.00")
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-110.00")
            assert legs[groceries_ledger] == Decimal("110.00")
            assert sum(legs.values()) == Decimal("0.00")


class TestTransactionAllCreditNoop:
    """An all-credit envelope has zero debit effect and posts nothing."""

    def test_all_credit_envelope_posts_nothing(self, app, db, seed_user):
        """An envelope whose only entry is a credit purchase posts no entry.

        Arithmetic: a single $40 credit entry, ``actual_amount`` = 40, so
        ``effect = effective(40) - credit_sum(40) = 0``.  The target is
        ``{cash: 0, category: 0}``, nothing is posted yet, so every delta is
        zero and the sync is a no-op (returns None) -- the entire $40 flows
        through the separate CC Payback instead.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "All Credit Env",
                Decimal("200.00"),
            )
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            txn.actual_amount = Decimal("40.00")
            _db.session.commit()

            result = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            assert result is None
            assert _entries_for_transaction(txn.id) == []


class TestTransactionIdempotency:
    """A repeat sync at the same target writes nothing."""

    def test_repeat_settle_is_noop(self, app, db, seed_user):
        """Re-syncing an already-posted settle returns None, no 2nd entry.

        Arithmetic: the first settle posts -50 / +50; the second sync sees
        current == target (delta 0 on every account) and writes nothing.  This
        is the double-mark-done guard -- a repeated settle never double-posts.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            first = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            second = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            assert first is not None
            assert second is None
            assert len(_entries_for_transaction(txn.id)) == 1


class TestTransactionReversal:
    """A reversal reads the posted amount back from the ledger."""

    def test_reverse_nets_to_zero(self, app, db, seed_user):
        """Reverting a settled transaction nets both ledgers to zero.

        Arithmetic: settle posts -100 (Checking) / +100 (Groceries).  The
        revert (``settled=False``, target ``{}``) reverses exactly what is
        posted: +100 (Checking) / -100 (Groceries).  Both the cash account and
        the category account net to zero, and two entries survive (append-only
        correction, never an edit).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            posting_service.sync_transaction_postings(txn, settled=True)
            _db.session.commit()

            reversal = posting_service.sync_transaction_postings(
                txn, settled=False,
            )
            _db.session.commit()

            assert reversal is not None
            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("0.00")
            assert len(_entries_for_transaction(txn.id)) == 2

    def test_revert_recategorize_resettle_posts_new_zeroes_old(
        self, app, db, seed_user,
    ):
        """Revert -> recategorize -> re-settle posts to NEW, zeroes OLD (2.8 CRITICAL).

        The exact scenario the per-site approach got wrong.  Arithmetic:

          1. Settle a $100 expense in category A (Groceries): cash -100, A +100.
          2. Recategorize to B (Rent) and reconcile with ``settled=False`` (the
             single-PATCH revert): the reversal reads the LEDGER (category A),
             not the now-B ``category_id``, so it posts +100 cash / -100 A.  A
             nets to zero.
          3. Re-settle (``settled=True``, category now B): cash -100 / +100 B.

        Final books: category A nets to **zero**, category B carries the
        +100.00 expense, and Checking nets to -100.00.  Reading the posted side
        from the ledger -- never from ``txn.category_id`` -- is what makes the
        per-category books correct.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            a_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            # 1. Settle in category A.
            posting_service.sync_transaction_postings(txn, settled=True)
            _db.session.commit()

            # 2. Recategorize to B, then reconcile the revert (settled=False).
            txn.category_id = seed_user["categories"]["Rent"].id
            posting_service.sync_transaction_postings(txn, settled=False)
            _db.session.commit()

            # 3. Re-settle with the new category.
            posting_service.sync_transaction_postings(txn, settled=True)
            _db.session.commit()

            b_ledger = _resolve_category_ledger(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            ).id
            assert _ledger_total(a_ledger) == Decimal("0.00")
            assert _ledger_total(b_ledger) == Decimal("100.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-100.00")
            # settle + revert + re-settle = three append-only entries.
            assert len(_entries_for_transaction(txn.id)) == 3

    def test_recategorize_while_settled_posts_cashless_reclassification(
        self, app, db, seed_user,
    ):
        """Recategorizing a still-settled transaction moves the category leg only.

        Arithmetic: settle a $100 expense in A (Groceries): cash -100, A +100.
        Then recategorize to B (Rent) and re-sync while STILL settled
        (``settled=True``, no revert).  The amount is unchanged, so the cash
        delta is zero and drops out; the entry carries TWO category legs and NO
        cash leg (A -100 / B +100).  This exercises the union reconcile's
        balanced-by-construction property in the cash-leg-absent case: the
        non-zero deltas still sum to zero and still yield >= 2 legs.  Net books:
        A nets to zero, B carries the +100 expense, Checking stays at -100.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            a_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id
            cash_ledger = _ledger_id(seed_user["account"])

            posting_service.sync_transaction_postings(txn, settled=True)
            _db.session.commit()

            # Recategorize WITHOUT reverting; re-sync while still settled.
            txn.category_id = seed_user["categories"]["Rent"].id
            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            assert entry is not None
            b_ledger = _resolve_category_ledger(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            ).id
            # Two category legs, no cash leg, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs == {
                a_ledger: Decimal("-100.00"),
                b_ledger: Decimal("100.00"),
            }
            assert cash_ledger not in legs
            assert sum(legs.values()) == Decimal("0.00")
            # Net books: A zero, B carries the expense, cash unchanged at -100.
            assert _ledger_total(a_ledger) == Decimal("0.00")
            assert _ledger_total(b_ledger) == Decimal("100.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-100.00")


class TestTransactionCounterLegRouting:
    """The counter leg lands in the right category / fallback account."""

    def test_categorized_expense_lands_in_category_ledger(
        self, app, db, seed_user,
    ):
        """A categorized expense books its counter leg into the category row.

        The non-cash leg lands in the (owner, Groceries, Expense) ledger
        account -- a category row (``is_fallback`` False, ``category_id`` set),
        NOT a real-account link.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            groceries = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            cash_ledger = _ledger_id(seed_user["account"])
            legs = _legs_by_ledger(entry.id)
            non_cash = [lid for lid in legs if lid != cash_ledger]
            assert non_cash == [groceries.id]
            assert groceries.is_fallback is False
            assert groceries.account_id is None
            assert groceries.category_id == seed_user["categories"][
                "Groceries"
            ].id

    def test_uncategorized_expense_lands_in_fallback(
        self, app, db, seed_user,
    ):
        """A NULL-category expense books its counter leg into the fallback.

        The non-cash leg lands in the per-(owner, Expense) Uncategorized
        fallback (``is_fallback`` True, ``category_id`` NULL), the catch-all
        for a settled transaction whose category is NULL.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Misc", "50.00",
                status_enum=StatusEnum.DONE, category_key=None,
            )
            _db.session.commit()

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()

            fallback = _resolve_category_ledger(
                seed_user, None, LedgerAccountClassEnum.EXPENSE,
            )
            cash_ledger = _ledger_id(seed_user["account"])
            legs = _legs_by_ledger(entry.id)
            non_cash = [lid for lid in legs if lid != cash_ledger]
            assert non_cash == [fallback.id]
            assert fallback.is_fallback is True
            assert fallback.category_id is None
            assert fallback.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.EXPENSE,
            )


class TestTransactionShadowNoop:
    """A transfer shadow is never posted as an ordinary transaction."""

    def test_transfer_shadow_is_noop(self, app, db, seed_user, savings):
        """``sync_transaction_postings`` on a transfer shadow is a no-op.

        A transfer's shadow transactions carry ``transfer_id``; Step 2 posts
        them via the ``transfer_id`` linkage.  The defensive guard returns None
        for a shadow so it can never get a second, ``transaction_id``-linked
        entry (which would double-count it).
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            shadow = (
                _db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
                .first()
            )
            assert shadow.transfer_id is not None

            result = posting_service.sync_transaction_postings(
                shadow, settled=True,
            )
            _db.session.commit()

            assert result is None
            # No transaction-sourced entry exists for the shadow.
            assert _entries_for_transaction(shadow.id) == []


class TestTransactionEntryDate:
    """``entry_date`` is the paid_at UTC civil date, else the period start."""

    def test_entry_date_from_paid_at_utc_not_display_tz(
        self, app, db, seed_user,
    ):
        """A settled paid_at maps to its UTC civil date, NOT the display tz.

        Arithmetic: paid_at 2026-05-10 02:00 UTC is 2026-05-09 22:00 in
        America/New_York (UTC-4 in May), so the UTC civil date (2026-05-10) and
        the Eastern civil date (2026-05-09) differ.  The entry date must be the
        UTC date -- the Python counterpart of the backfill's
        ``(paid_at AT TIME ZONE 'UTC')::date`` -- read back via a query so a
        server-side ``db.func.now()`` would also materialise correctly.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            txn.paid_at = datetime(2026, 5, 10, 2, 0, tzinfo=timezone.utc)
            _db.session.commit()

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()
            assert entry.entry_date == date(2026, 5, 10)

    def test_entry_date_falls_back_to_period_start_when_paid_at_null(
        self, app, db, seed_user,
    ):
        """A settled transaction with NULL paid_at uses the pay-period start.

        ``entry_date`` is NOT NULL; with no ``paid_at`` recorded the entry date
        falls back to the period's ``start_date``.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            entry = posting_service.sync_transaction_postings(
                txn, settled=True,
            )
            _db.session.commit()
            assert entry.entry_date == period.start_date


class TestTransactionFailLoud:
    """Broken invariants raise PostingError rather than posting silently."""

    def test_missing_cash_ledger_fails_loud(self, app, db, seed_user):
        """A transaction whose account has no ledger pairing raises PostingError.

        Removing the Checking account's linked ledger row (an impossible state
        in production -- every account is paired) makes the settle sync fail
        loudly rather than post a one-legged or silently-wrong entry.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": seed_user["account"].id})
            _db.session.commit()

            with pytest.raises(PostingError, match="ledger account"):
                posting_service.sync_transaction_postings(txn, settled=True)

    def test_settled_transaction_effect_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``settled_transaction_effect`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.settled_transaction_effect(
                    seed_user["account"].id, None,
                )


class TestSettledTransactionEffect:
    """The transaction-effect helper agrees with the posting total."""

    def test_effect_matches_posting_total(self, app, db, seed_user):
        """The signed transaction effect equals the ledger posting total.

        Arithmetic: a $50 Paid expense (-50) and a $2000 Received income
        (+2000) on Checking net to +1950.00.  ``settled_transaction_effect``
        (a source-table query) and ``account_posting_total`` (the ledger sum)
        compute the same number from independent tables -- and they agree only
        because no transfers exist here, so the cash legs are entirely
        transaction-sourced.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            expense = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            income = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            _db.session.commit()
            posting_service.sync_transaction_postings(expense, settled=True)
            posting_service.sync_transaction_postings(income, settled=True)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            account_id = seed_user["account"].id
            # -50 (expense) + 2000 (income) = 1950.
            assert posting_service.settled_transaction_effect(
                account_id, scenario_id,
            ) == Decimal("1950.00")
            assert posting_service.account_posting_total(
                account_id, scenario_id,
            ) == Decimal("1950.00")

    def test_effect_excludes_credit_portion(self, app, db, seed_user):
        """The effect helper sums the DEBIT-only envelope effect.

        Arithmetic: a settled envelope with $60 + $50 debit and $40 credit,
        ``actual_amount`` 150, has a confirmed cash effect of
        ``effective(150) - credit_sum(40) = 110`` of expense, so the signed
        effect is -110.00 -- the SQL credit-sum subquery excludes the credit
        portion exactly as the go-forward ``_credit_entry_sum`` does.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            _add_txn_entry(seed_user, txn, "60.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "50.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            txn.actual_amount = Decimal("150.00")
            _db.session.commit()

            assert posting_service.settled_transaction_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-110.00")

    def test_effect_correlates_credit_sum_per_transaction(
        self, app, db, seed_user,
    ):
        """The credit-sum subquery is per-transaction, not one global sum.

        Two settled expenses on one account: an envelope X ($60 + $50 debit,
        $40 credit, ``actual`` 150 -> effect -110) and a plain Y ($30, no
        entries -> effect -30), netting to -140.00.  An UNCORRELATED subquery
        would subtract the single $40 credit from EVERY row (X -110, Y +10,
        total -100), so the -140 result is the regression lock that proves the
        subquery correlates the credit sum to each transaction -- the
        load-bearing property of the oracle's source-of-truth side that a
        single-credit-transaction test cannot catch.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            envelope = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            _add_txn_entry(seed_user, envelope, "60.00", is_credit=False)
            _add_txn_entry(seed_user, envelope, "50.00", is_credit=False)
            _add_txn_entry(seed_user, envelope, "40.00", is_credit=True)
            envelope.status_id = ref_cache.status_id(StatusEnum.DONE)
            envelope.actual_amount = Decimal("150.00")
            add_txn(
                _db.session, seed_user, period, "Rent", "30.00",
                status_enum=StatusEnum.DONE, category_key="Rent",
            )
            _db.session.commit()

            # -110 (envelope, debit-only) + -30 (plain) = -140; an uncorrelated
            # credit sum would instead give -100.
            assert posting_service.settled_transaction_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-140.00")
