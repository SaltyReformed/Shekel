"""Tests for ``posting_service`` (Build-Order Step 2, Commit 4).

``posting_service`` is the sole go-forward writer of the double-entry posting
ledger.  Its one public emission entry point,
:func:`~app.services.posting_service.sync_transfer_postings`, reconciles a
transfer's net posted ledger effect to a target (the settled effective
amount, or zero) by emitting one balanced delta journal entry, idempotently;
:func:`~app.services.posting_service.account_posting_total` and
:func:`~app.services.posting_service.settled_transfer_effect` are the two
reconciliation helpers the Commit-6 oracle consumes.

These tests pin the load-bearing properties with hand-computed arithmetic:

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
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; test bodies bind fixtures by name.
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.services import posting_service, transfer_service
from app.services.posting_service import (
    PostingError,
    _emit_balanced_entry,
    _PostingLeg,
)
from tests._test_helpers import (
    create_account_of_type,
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
