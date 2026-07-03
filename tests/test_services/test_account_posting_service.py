"""Tests for the account posting service (Build-Order Step 5, C5 + the C6 wiring).

:mod:`app.services.account_posting_service` posts a NON-loan account's anchor
assertions into the double-entry ledger: the once-per-account OPENING (its
earliest ``AccountAnchorHistory`` row) and a TRUE-UP per later row, each a
balanced correction driving the linked ledger's total to the asserted balance
at the assertion MOMENT.  The walk partitions source facts by attribution
INSTANT (``paid_at``, transfers by the income shadow's, period-start fallback)
against each anchor's ``created_at`` -- never by pay period (the plan review's
CRITICAL-1).  These tests drive the walk and the sync entry points directly;
since C6 the lifecycle chokepoints are ALSO live underneath them --
``create_account`` posts each opening at fixture time and the effect-time
self-heal reconciles at every settle / revert -- so the explicit sync calls
double as idempotency proofs, and the step-count assertions reflect the eager
per-mutation reconcile (each intermediate state lands exactly on the anchor).

Fixtures are SYNTHETIC with HAND-COMPUTED literals, each docstring showing the
arithmetic.  Assertion instants are constructed RELATIVE to the factory
origination row's stored ``created_at`` (the one instant the test cannot
choose), so every pre/post/tie partition is deterministic regardless of clock
or timezone.  All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.user import User
from app.services import (
    account_posting_service,
    account_service,
    anchor_service,
    posting_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.auth_service import hash_password
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    ledger_net,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_account(seed_user, balance, type_name="Savings", name="Anchor Acct"):
    """Create an account with a controlled opening anchor; commit; return it."""
    account = create_account_of_type(
        seed_user, _db.session, type_name, name,
        anchor_balance=Decimal(balance),
    )
    _db.session.commit()
    return account


def _origin_instant(account):
    """Return the factory origination row's stored assertion instant (UTC).

    The one instant a test cannot choose (the row's ``created_at`` is the
    INSERT transaction's ``now()``); every other instant in a fixture is
    built relative to it so the pre/post/tie partitions are deterministic.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.created_at, AccountAnchorHistory.id)
        .first()
    )
    return row.created_at.astimezone(timezone.utc)


def _add_assertion(account, balance, created_at):
    """Append a true-up ``AccountAnchorHistory`` row at a controlled instant.

    Mirrors ``anchor_service.stage_anchor_true_up`` (history row + the
    ``current_anchor_*`` cache write) but pins ``created_at`` explicitly so
    the moment partition under test is exact.  Anchors against the
    account's current anchor period (the fixture bootstrap).  Flushes.
    """
    row = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=account.current_anchor_period_id,
        anchor_balance=Decimal(str(balance)),
        created_at=created_at,
    )
    account.current_anchor_balance = Decimal(str(balance))
    _db.session.add(row)
    _db.session.flush()
    return row


def _ledger_of_kind(account_id, kind):
    """Return the account's ledger row of *kind* (linked / twin), or None."""
    return (
        _db.session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


def _correction_entries(account_id, scenario_id, source_enum):
    """Return the account's correction entries of *source_enum*, linked-scoped.

    Finds the ``account_opening`` / ``account_trueup`` journal entries in
    *scenario_id* that touch the account's LINKED ledger -- the way the
    reconcile scopes them to one account.
    """
    linked = _ledger_of_kind(account_id, LedgerAccountKindEnum.LINKED)
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
            JournalEntry.id.in_(
                _db.session.query(Posting.journal_entry_id)
                .filter(Posting.ledger_account_id == linked.id)
            ),
        )
        .all()
    )


def _entry_legs(entry_id):
    """Return ``{ledger_account_id: (amount, posting_kind_id)}`` for an entry."""
    return {
        leg.ledger_account_id: (leg.amount, leg.posting_kind_id)
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


def _settle_expense(seed_user, account, amount, paid_at):
    """Settle an expense on *account* at a pinned ``paid_at``; return it.

    ``paid_at`` may be an instant or None (the period-start fallback under
    test), pinned BEFORE the ledger emission so the posted entry and the
    walk's attribution agree, as in production (the C6 effect-time
    self-heal reads the emitted ``entry_date``s).  The transaction is
    placed in the seed bootstrap period; the walk attributes by instant,
    so the period placement is immaterial except for the NULL-``paid_at``
    fallback.
    """
    return create_settled_cash_transaction(
        seed_user, _db.session, seed_user["bootstrap_period"],
        Decimal(str(amount)), account=account, paid_at=paid_at,
    )


# ---------------------------------------------------------------------------
# walk_account_ledger -- the moment partition (the core)
# ---------------------------------------------------------------------------


class TestWalkAccountLedger:
    """The pure walk partitions sources by instant and resets at assertions."""

    def test_opening_only_walk(self, app, db, seed_user):
        """A fresh account walks to one opening correction from zero.

        Savings anchored $500.00 with no settled activity: one correction,
        the opening, with ledger_before 0.00 (so its delta is the full
        anchor, 500.00).
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].anchor.is_opening is True
            assert corrections[0].anchor.anchor_balance == Decimal("500.00")
            assert corrections[0].ledger_before == Decimal("0.00")

    def test_pre_assertion_settle_is_absorbed(self, app, db, seed_user):
        """A settle paid BEFORE the assertion instant is inside ledger_before.

        Savings anchored $500.00; a $200.00 expense settled with paid_at one
        hour BEFORE the origination assertion: the source is absorbed, so the
        opening's ledger_before is -200.00 (and its delta 500 - (-200) =
        +700.00 -- the anchor already reflected that spend).
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            _settle_expense(
                seed_user, account, "200.00", origin - timedelta(hours=1),
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].ledger_before == Decimal("-200.00")

    def test_post_assertion_settle_rides_on_top(self, app, db, seed_user):
        """A settle paid AFTER the assertion instant is NOT absorbed.

        Savings anchored $500.00; a $200.00 expense settled with paid_at one
        hour AFTER the origination assertion: the opening's ledger_before
        stays 0.00 (the settle rides on top of the asserted balance).
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].ledger_before == Decimal("0.00")

    def test_null_paid_at_falls_back_to_period_start(
        self, app, db, seed_user,
    ):
        """A NULL-paid_at settle is attributed at its period start (absorbed).

        The $200.00 expense sits in the 2024 bootstrap period with paid_at
        NULL; its fallback instant (2024-01-05 midnight UTC) precedes the
        origination assertion (test-run time, 2026+), so it is absorbed:
        ledger_before -200.00.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            _settle_expense(seed_user, account, "200.00", None)
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert corrections[0].ledger_before == Decimal("-200.00")

    def test_transfer_attribution_uses_income_shadow_instant(
        self, app, db, seed_user,
    ):
        """A transfer is attributed by its INCOME shadow's paid_at.

        Two Checking -> Savings transfers into a $500.00-anchored Savings:
        $50.00 settled one hour BEFORE the origination assertion (absorbed)
        and $150.00 one hour AFTER (rides on top).  The opening's
        ledger_before is therefore exactly +50.00.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("50.00"),
                paid_at=origin - timedelta(hours=1),
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
                paid_at=origin + timedelta(hours=1), name="Post-anchor",
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].ledger_before == Decimal("50.00")

    def test_trueup_moment_partition(self, app, db, seed_user):
        """The CRITICAL-1 case: a true-up absorbs only pre-assertion settles.

        Savings anchored $500.00 (origination, instant T).  A $200.00
        expense paid at T+1h, a true-up asserting $350.00 at T+2h, a $100.00
        expense paid at T+3h:

          opening: ledger_before 0.00              (delta +500.00)
          true-up: ledger_before 500 - 200 = 300.00 (delta +50.00 -- the
                   engine's answer was 300, the user asserted 350)

        The T+3h settle is attributed AFTER the true-up and perturbs
        nothing.  A period-granular walk would have absorbed BOTH settles
        (all three events share one period) and mis-stated the true-up.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            _add_assertion(
                account, "350.00", origin + timedelta(hours=2),
            )
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(hours=3),
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 2
            assert corrections[0].anchor.is_opening is True
            assert corrections[0].ledger_before == Decimal("0.00")
            assert corrections[1].anchor.is_opening is False
            assert corrections[1].ledger_before == Decimal("300.00")

    def test_same_instant_settle_is_absorbed(self, app, db, seed_user):
        """A settle at EXACTLY the assertion instant is absorbed (<= is inclusive).

        Savings anchored $500.00; a $75.00 expense whose paid_at EQUALS the
        true-up's created_at (T+2h); the true-up asserts $425.00.  Absorbed:
        ledger_before 500 - 75 = 425.00 (a strict < partition would report
        500.00 and book a spurious -75.00 correction).
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            instant = _origin_instant(account) + timedelta(hours=2)
            _settle_expense(seed_user, account, "75.00", instant)
            _add_assertion(account, "425.00", instant)
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert corrections[1].ledger_before == Decimal("425.00")

    def test_reverted_source_drops_out(self, app, db, seed_user):
        """A reverted settle nets to zero in the ledger and leaves the walk.

        The $200.00 expense (paid T+1h) is reversed (the revert path's
        ``settled=False`` reconcile); a true-up at T+2h asserting $480.00
        (distinct from the $500.00 origination -- the F-103 same-day
        same-balance unique index would reject a literal duplicate) then
        sees ledger_before 500.00 -- the reverted source contributes
        nothing, whatever its paid_at said.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            posting_service.sync_transaction_postings(txn, settled=False)
            _add_assertion(account, "480.00", origin + timedelta(hours=2))
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert corrections[1].ledger_before == Decimal("500.00")

    def test_walk_refuses_amortizing_loan(self, app, db, seed_user):
        """Walking a loan is a caller bug and fails loudly.

        Loans book their anchor corrections through the loan posting
        package; the walk raising here (not just the equity resolver at
        mint time) is what keeps the two correction families structurally
        disjoint.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, _db.session)
            _db.session.commit()
            with pytest.raises(ValueError, match="amortizing loan"):
                account_posting_service.walk_account_ledger(
                    loan.id, seed_user["scenario"].id,
                )

    def test_missing_account_returns_empty(self, app, db, seed_user):
        """A nonexistent account id walks to no corrections."""
        with app.app_context():
            assert account_posting_service.walk_account_ledger(
                10**9, seed_user["scenario"].id,
            ) == []


# ---------------------------------------------------------------------------
# sync_account_anchor_postings -- reconcile-to-target on the ledger
# ---------------------------------------------------------------------------


class TestSyncAccountAnchorPostings:
    """The reconcile posts balanced corrections and self-heals to target."""

    def test_opening_posts_balanced_correction(self, app, db, seed_user):
        """The opening books linked +anchor / equity -anchor, dated + attributed.

        Savings anchored $500.00: ONE ``account_opening`` entry with legs
        linked +500.00 / anchor-equity -500.00 (both kind ``opening``), both
        concrete source FKs NULL, ``entry_date`` the assertion instant's UTC
        civil date, and ``pay_period_id`` the history row's own period.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("500.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            equity = _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert equity is not None
            assert ledger_net(
                _db.session, equity.id, scenario_id,
            ) == Decimal("-500.00")

            entries = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )
            assert len(entries) == 1
            entry = entries[0]
            opening_kind = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
            assert _entry_legs(entry.id) == {
                linked.id: (Decimal("500.00"), opening_kind),
                equity.id: (Decimal("-500.00"), opening_kind),
            }
            assert entry.transfer_id is None
            assert entry.transaction_id is None
            history_row = (
                _db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .one()
            )
            assert entry.pay_period_id == history_row.pay_period_id
            assert entry.entry_date == (
                history_row.created_at.astimezone(timezone.utc).date()
            )
            assert _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == []

    def test_zero_anchor_books_nothing(self, app, db, seed_user):
        """A $0.00 opening books no entry and mints no anchor-equity row.

        The zero-delta rule keeps a fresh $0 account hard-deletable: no
        entry, no equity twin (Guard 5 never engages).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "0.00")
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            ) == []
            assert _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None

    def test_sync_is_idempotent(self, app, db, seed_user):
        """A second sync at the same state writes no new entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()
            assert len(_correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )) == 1
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("500.00")

    def test_trueup_posts_delta_and_absolute_invariant(
        self, app, db, seed_user,
    ):
        """The CRITICAL-1 fixture reconciles to the absolute invariant.

        From the walk fixture (anchor 500, spend 200 at T+1h, true-up 350 at
        T+2h, spend 100 at T+3h):

          opening +500.00, true-up +50.00, sources -200.00 - 100.00
          => linked total 250.00 == latest anchor 350 + post-assertion -100
          equity nets -(500 + 50) = -550.00
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            _add_assertion(account, "350.00", origin + timedelta(hours=2))
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(hours=3),
            )
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("250.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            equity = _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                _db.session, equity.id, scenario_id,
            ) == Decimal("-550.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 1
            trueup_kind = ref_cache.posting_kind_id(PostingKindEnum.TRUEUP)
            assert _entry_legs(trueups[0].id) == {
                linked.id: (Decimal("50.00"), trueup_kind),
                equity.id: (Decimal("-50.00"), trueup_kind),
            }

    def test_trueup_self_heals_when_pre_assertion_source_changes(
        self, app, db, seed_user,
    ):
        """Reverting a pre-true-up settle re-bases the true-up on resync.

        Continuing the CRITICAL-1 fixture: the $200.00 pre-true-up expense
        is reverted, so the walk's true-up ledger_before moves 300 -> 500
        and its delta +50 -> -150.  The resync appends the balancing -200.00
        delta on the SAME (source, date) key -- no stale snapshot survives:

          linked: 500 (opening) - 150 (true-up, healed) - 100 (post settle)
                  = 250.00 == anchor 350 + post-assertion -100
          the true-up key now nets -150.00 across its two entries.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            _add_assertion(account, "350.00", origin + timedelta(hours=2))
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(hours=3),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            posting_service.sync_transaction_postings(txn, settled=False)
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("250.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 2
            trueup_linked_net = sum(
                (
                    _entry_legs(entry.id)[linked.id][0]
                    for entry in trueups
                ),
                Decimal("0"),
            )
            assert trueup_linked_net == Decimal("-150.00")

    def test_trueup_retired_by_matching_balance_reverses_to_zero(
        self, app, db, seed_user,
    ):
        """A true-up whose walked delta becomes zero reverses via its empty-target key.

        Anchor 500; a $200.00 expense paid T+1h; true-up 350.00 at T+2h
        posts +50.00 (ledger_before 300).  The user then reverts the $200
        and instead settles $150.00 paid T+1.5h (still pre-true-up).  The
        C6 effect-time self-heal reconciles at EACH mutation, landing the
        ledger exactly on the anchor at every step:

          revert:     ledger_before 500, target -150, posted +50
                      -> delta -200.00 (linked total 500 - 150 = 350)
          settle 150: ledger_before 500 - 150 = 350 -- delta 0.  The
                      zero-delta correction still creates its (trueup,
                      day) KEY with an empty leg map, so the stale
                      -150.00 net REVERSES via the empty-target path
                      -> delta +150.00 (linked total 350 again)

        The final explicit resync books nothing (idempotent).  All three
        key entries are attributed to the history row's own period, and:

          linked: 500 (opening) + 0 (true-up net) - 150 = 350.00 == anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            trueup_row = _add_assertion(
                account, "350.00", origin + timedelta(hours=2),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            posting_service.sync_transaction_postings(txn, settled=False)
            _settle_expense(
                seed_user, account, "150.00",
                origin + timedelta(hours=1, minutes=30),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("350.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 3
            assert all(
                entry.pay_period_id == trueup_row.pay_period_id
                for entry in trueups
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in trueups),
                Decimal("0"),
            ) == Decimal("0.00")

    def test_posted_only_key_reverses_into_the_period_it_corrected(
        self, app, db, seed_user,
    ):
        """A posted correction with no surviving anchor row reverses, R2-attributed.

        Posts the CRITICAL-1 true-up (+50.00), then surgically deletes its
        history row ALONE -- unreachable through production lifecycles
        (a period wipe CASCADEs the entry away with the row, and history is
        otherwise append-only), which is exactly why the reconcile's
        posted-only branch is defensive.  The resync sees the (trueup, day)
        key only on the posted side and reverses it, taking the period OF
        THE POSTINGS IT REVERSES (read back from the latest posted entry --
        the R2 rule), leaving:

          linked: 500 (opening) + 0 (true-up net) - 200 = 300.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(hours=1),
            )
            trueup_row = _add_assertion(
                account, "350.00", origin + timedelta(hours=2),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()
            original_period_id = trueup_row.pay_period_id

            _db.session.delete(trueup_row)
            _db.session.flush()
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("300.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 2
            assert all(
                entry.pay_period_id == original_period_id
                for entry in trueups
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in trueups),
                Decimal("0"),
            ) == Decimal("0.00")

    def test_same_day_trueups_merge_to_one_entry_landing_later(
        self, app, db, seed_user,
    ):
        """Two same-UTC-day true-ups merge to one target on the LATER value.

        Both assertions sit on one future UTC day (06:00 and 07:00, so no
        midnight crossing): $600.00 then $550.00 on a $500.00-anchored
        account.  Their deltas +100.00 and -50.00 share the (trueup, day)
        key and merge to ONE +50.00 entry; the ledger lands on 550.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            day = (_origin_instant(account) + timedelta(days=30)).replace(
                hour=6, minute=0, second=0, microsecond=0,
            )
            _add_assertion(account, "600.00", day)
            _add_assertion(account, "550.00", day + timedelta(hours=1))
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("550.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 1
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            assert _entry_legs(trueups[0].id)[linked.id][0] == Decimal("50.00")

    def test_liability_anchor_keeps_ledger_native_sign(
        self, app, db, seed_user,
    ):
        """An owed-as-negative liability anchor posts with no sign branch.

        A Credit Card anchored -500.00 (the owed-as-negative convention):
        the opening books linked -500.00 / equity +500.00 -- the ledger
        presents the anchor faithfully, exactly like the engine (no class
        branch, no ``-abs`` normalization).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(
                seed_user, "-500.00", type_name="Credit Card", name="CC",
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("-500.00")
            equity = _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                _db.session, equity.id, scenario_id,
            ) == Decimal("500.00")


# ---------------------------------------------------------------------------
# _sync entry points -- scenarios, loans, per-user resync
# ---------------------------------------------------------------------------


class TestSyncEntryPoints:
    """Scenario enumeration, the loan no-op, and the per-user resync."""

    def test_all_scenarios_covers_posted_scenarios_and_baseline(
        self, app, db, seed_user,
    ):
        """The all-scenarios sync posts in the baseline AND posted scenarios.

        A $40.00 expense settled (post-assertion) in a NON-baseline scenario
        puts a posting on the account's linked ledger there, so the
        all-scenarios sync reconciles both: the opening posts per scenario
        (anchors are per-account), and each scenario's total reflects its
        OWN sources -- baseline 500.00, what-if 500 - 40 = 460.00.
        """
        with app.app_context():
            baseline_id = seed_user["scenario"].id
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            _db.session.add(what_if)
            _db.session.flush()
            account = _make_account(seed_user, "500.00")
            origin = _origin_instant(account)
            txn = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=account, scenario=what_if,
            )
            txn.paid_at = origin + timedelta(hours=1)
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings_all_scenarios(
                account.id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, baseline_id,
            ) == Decimal("500.00")
            assert posting_service.account_posting_total(
                account.id, what_if.id,
            ) == Decimal("460.00")
            for scenario_id in (baseline_id, what_if.id):
                assert len(_correction_entries(
                    account.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )) == 1

    def test_loan_account_is_a_noop_everywhere(self, app, db, seed_user):
        """A loan syncs nothing here and is excluded from the resync set.

        The loan carries an ``AccountAnchorHistory`` row like every account,
        but its corrections belong to the loan posting package; the account
        entry points skip it and the per-user enumerator never returns it.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(seed_user, _db.session)
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                loan.id, scenario_id,
            )
            all_scenarios = (
                account_posting_service
                .sync_account_anchor_postings_all_scenarios
            )
            all_scenarios(loan.id)
            _db.session.commit()

            for source in (
                PostingSourceEnum.ACCOUNT_OPENING,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            ):
                assert _correction_entries(loan.id, scenario_id, source) == []
            resynced = (
                account_posting_service.resync_user_account_anchor_postings(
                    seed_user["user"].id,
                )
            )
            assert loan.id not in resynced

    def test_resync_user_posts_every_non_loan_account(
        self, app, db, seed_user,
    ):
        """The per-user resync enumerates non-loan accounts and posts openings.

        seed_user carries the fixture Checking ($1000.00 origination); add a
        Savings ($500.00) and a loan.  The resync returns exactly the two
        non-loan ids (ascending) and posts each opening: Checking 1000.00,
        Savings 500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = _make_account(seed_user, "500.00")
            create_loan_account(seed_user, _db.session)
            _db.session.commit()

            resynced = (
                account_posting_service.resync_user_account_anchor_postings(
                    seed_user["user"].id,
                )
            )
            _db.session.commit()

            assert resynced == sorted([checking.id, savings.id])
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("500.00")

    def test_wired_create_posts_opening_unprompted(self, app, db, seed_user):
        """``create_account`` posts the opening with NO manual sync call (C6).

        The factory itself drives the all-scenarios sync after the ledger
        pairing, so a $750.00 account carries its balanced opening the
        moment the creating transaction commits -- nothing here invokes the
        posting package.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "750.00")
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("750.00")
            assert len(_correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )) == 1

    def test_wired_self_heal_absorbs_pre_assertion_settle(
        self, app, db, seed_user,
    ):
        """A pre-assertion settle re-bases the opening with NO manual sync (C6).

        A NULL-``paid_at`` settle in the 2024 bootstrap period is attributed
        at the period start -- BEFORE the account's origination assertion --
        so the effect-time self-heal at the ``sync_transaction_postings``
        tail re-derives the opening in the same transaction: the opening key
        moves to +700.00 (500 - (-200)) and the account's total stays
        exactly on the 500.00 anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            _settle_expense(seed_user, account, "200.00", None)
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("500.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            openings = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in openings),
                Decimal("0"),
            ) == Decimal("700.00")

    def test_wired_trueup_and_revert_self_heal_end_to_end(
        self, app, db, seed_user,
    ):
        """The true-up chokepoint books the delta; a revert re-bases it (C6).

        End to end with NO manual account-sync call anywhere:

          settle -200 at server-now, then assert $350.00 through
          ``anchor_service.apply_anchor_true_up`` (a later instant, so the
          settle is absorbed): the wiring books the true-up delta
          350 - (500 - 200) = +50.00 and the total lands on the anchor.

          revert the settle: the ``posting_service`` tail self-heal alone
          re-derives the true-up (ledger_before 500, delta -150; heal
          -200), keeping the total exactly on the 350.00 anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            txn = _settle_expense(
                seed_user, account, "200.00", _db.func.now(),
            )
            _db.session.commit()

            outcome = anchor_service.apply_anchor_true_up(
                account=account,
                new_balance=Decimal("350.00"),
                anchor_period=seed_user["bootstrap_period"],
                user_id=seed_user["user"].id,
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("350.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 1
            assert _entry_legs(trueups[0].id)[linked.id][0] == Decimal("50.00")

            posting_service.sync_transaction_postings(txn, settled=False)
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("350.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in trueups),
                Decimal("0"),
            ) == Decimal("-150.00")

    def test_baselineless_owner_skips_loudly_then_recovers(
        self, app, db, seed_user, caplog,
    ):
        """No baseline + no postings: skip with a loud log; resync recovers.

        A second user with pay periods but NO scenario: the all-scenarios
        sync has nowhere to post (postings are scenario-scoped), logs the
        skip, and writes nothing.  Once a baseline exists, the per-user
        resync posts the stranded opening -- the ``create_baseline``
        recovery path.
        """
        with app.app_context():
            user2 = User(
                email="second@shekel.local",
                password_hash=hash_password("testpass-2"),
                display_name="Second User",
            )
            _db.session.add(user2)
            _db.session.flush()
            period2 = PayPeriod(
                user_id=user2.id, start_date=seed_user["bootstrap_period"].start_date,
                end_date=seed_user["bootstrap_period"].end_date, period_index=0,
            )
            _db.session.add(period2)
            _db.session.flush()
            checking_type_id = seed_user["account"].account_type_id
            account2 = account_service.create_account(
                account_service.AccountSpec(
                    user_id=user2.id,
                    account_type_id=checking_type_id,
                    name="U2 Checking",
                    anchor_balance=Decimal("100.00"),
                    anchor_period_id=period2.id,
                ),
            )
            _db.session.commit()

            with caplog.at_level("WARNING"):
                sync_all = (
                    account_posting_service
                    .sync_account_anchor_postings_all_scenarios
                )
                sync_all(account2.id)
            _db.session.commit()
            assert "no baseline scenario" in caplog.text
            assert (
                _db.session.query(JournalEntry)
                .filter_by(user_id=user2.id)
                .count()
            ) == 0

            baseline2 = Scenario(
                user_id=user2.id, name="Baseline", is_baseline=True,
            )
            _db.session.add(baseline2)
            _db.session.commit()
            account_posting_service.resync_user_account_anchor_postings(
                user2.id,
            )
            _db.session.commit()
            assert posting_service.account_posting_total(
                account2.id, baseline2.id,
            ) == Decimal("100.00")
