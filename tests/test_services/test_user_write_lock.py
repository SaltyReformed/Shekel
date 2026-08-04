"""
Shekel Budget App -- The per-user write lock (plan step X-f1c3c)

Grades the lock that serialises every posting-ledger reconcile and every
structural pay-period mutation for one user.

**What broke without it, measured.**  A reconcile is a read-modify-write:
read what the ledger has posted, subtract it from what the account's facts
say it should hold, write the difference.  With the interleave forced at the
read, two concurrent true-ups on an account reconciled at ``$4,000.00`` both
answered 200 and left the account's linked ledger at ``$1,000.00`` while its
resolved assertion read ``$2,000.00``.  Both sides wrong; the trial balance
still ``$0.00``, because the anchor-equity leg mirrors the error exactly, so
nothing failed loudly.  That forced-interleave probe cannot be kept as a test:
once the lock exists the second thread blocks at it, so the probe's own
mechanism is what the fix removes.  These tests grade the lock instead.

Three properties, because each can hold while another fails:

1. **The lock is taken, and taken BEFORE the reconcile reads.**  A lock
   acquired after the read serialises nothing -- the loser has already read
   the same pre-state.  Presence alone would pass such a build.
2. **It really serialises.**  A second transaction holding the same key blocks
   the reconcile, and releasing it lets the reconcile through.  This is the
   only property that grades PostgreSQL's behaviour rather than our SQL.
3. **It is keyed on the OWNER**, so it is one key per user and never two --
   which is what makes deadlock structurally impossible on every request path
   (:mod:`app.services.user_write_lock`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models.ref import AccountType
from app.services import (
    account_posting_service,
    account_service,
    loan_posting_service,
)
from app.services.user_write_lock import (
    _USER_WRITE_LOCK_NAMESPACE,
    lock_every_user_writes,
    lock_user_writes,
)
from tests._test_helpers import (
    advisory_lock_keys,
    advisory_lock_precedes,
    capture_sql_statements,
    create_loan_account,
    took_advisory_lock,
)

# Short enough that a genuinely-blocked statement fails the test in under a
# second, long enough that an unloaded but slow acquisition is not called a
# block.  Only ever applied to a statement the test EXPECTS to be blocked.
_BLOCK_TIMEOUT_MS = 750


def _checking_account(seed_user):
    """Create a second Checking account carrying one balance assertion.

    A fresh account with a non-zero anchor is the smallest thing whose
    reconcile writes: its opening correction is non-zero, so the sync under
    test does real read-modify-write work rather than short-circuiting.

    Args:
        seed_user: The seeded-user fixture dict.

    Returns:
        The created :class:`~app.models.account.Account`, flushed.
    """
    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Lock Probe Checking",
            anchor_balance=Decimal("1500.00"),
        ),
    )
    db.session.flush()
    return account


class TestTheReconcileTakesTheLockBeforeItReads:
    """The cash and loan reconciles lock the owner ahead of their first read."""

    def test_account_sync_locks_before_reading_the_ledger(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``sync_account_anchor_postings`` locks before touching the ledger.

        Both halves are graded: the lock statement is emitted, AND the first
        statement naming ``budget.journal_entries`` or
        ``budget.account_postings`` comes after it.  Ordering is the half that
        matters -- a lock taken after the posted legs are read leaves the
        racing loser holding the same pre-state it would have read anyway.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            scenario_id = seed_user["scenario"].id

            _result, statements = capture_sql_statements(
                lambda: account_posting_service.sync_account_anchor_postings(
                    account.id, scenario_id,
                ),
            )

            assert took_advisory_lock(statements), (
                "the account anchor reconcile emitted no advisory lock; "
                "two concurrent reconciles would both compute their delta "
                "against the same posted state"
            )
            assert any(
                "journal_entries" in text or "account_postings" in text
                for text, _params in statements
            ), "the reconcile read no ledger table -- the test graded nothing"
            assert advisory_lock_precedes(
                statements, "journal_entries", "account_postings",
            ), (
                "the reconcile read the ledger BEFORE taking the lock; "
                "serialising after the read serialises nothing"
            )

    def test_loan_sync_locks_before_reading_the_ledger(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``sync_loan_postings`` locks before it walks, on the same key.

        The loan half carries the identical read-modify-write and has never
        had even the accidental serialisation the cash half lost at ruling
        R-EN: a loan true-up appends a
        :class:`~app.models.loan_anchor_event.LoanAnchorEvent` and UPDATEs no
        row, so nothing serialised it between Commit 16 and plan step X-f1c3c.
        R-EN cited that append-only contract as its precedent, which is how a
        defect became a rationale -- so the loan side is pinned here rather
        than left to the argument.
        """
        assert seed_periods_today
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Lock Probe Loan",
                principal=Decimal("10000.00"), rate=Decimal("0.05000"),
            )
            db.session.flush()

            _result, statements = capture_sql_statements(
                lambda: loan_posting_service.sync_loan_postings(
                    loan.id, seed_user["scenario"].id,
                ),
            )

            assert took_advisory_lock(statements), (
                "the loan reconcile emitted no advisory lock"
            )
            assert any(
                "journal_entries" in text or "account_postings" in text
                for text, _params in statements
            ), "the reconcile read no ledger table -- the test graded nothing"
            assert advisory_lock_precedes(
                statements, "journal_entries", "account_postings",
            ), (
                "the loan reconcile read the ledger BEFORE taking the lock"
            )

    def test_all_scenarios_sync_locks_before_reading_the_scenario_set(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``..._all_scenarios`` locks before it reads which scenarios are live.

        The scenario set is itself a read this function then acts on, so the
        lock has to precede it: a scenario that became live in the window
        between that read and the loop would otherwise never be reconciled.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)

            _result, statements = capture_sql_statements(
                lambda: (
                    account_posting_service
                    .sync_account_anchor_postings_all_scenarios(account.id)
                ),
            )

            assert took_advisory_lock(statements)
            # The non-vacuity guard its two siblings carry, and which this test
            # was missing: ``advisory_lock_precedes`` returns True for a table
            # that is never read at all, so without this the ordering assertion
            # could pass over a run that touched no ledger.
            assert any(
                "journal_entries" in text or "account_postings" in text
                for text, _params in statements
            ), "the reconcile read no ledger table -- the test graded nothing"
            # The SCENARIO SET is the read this function's docstring is about,
            # so it is named explicitly rather than left to the ledger tables
            # that happen to appear in the same query.
            assert advisory_lock_precedes(
                statements, "journal_entries", "account_postings", "scenario_id",
            )


class TestTheLockActuallySerialises:
    """PostgreSQL's own behaviour, not just the SQL we emit."""

    def test_a_held_key_blocks_the_reconcile(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A second connection holding the user's key blocks the reconcile.

        Takes the SAME ``(namespace, user_id)`` key on an independent
        connection inside its own transaction, then runs the reconcile with a
        short ``lock_timeout``.  PostgreSQL cancels the blocked acquisition,
        which is the proof that the reconcile really waits for the holder
        rather than proceeding beside it.

        Non-vacuity is graded by the sibling test below: the SAME call with
        the SAME timeout completes when nothing holds the key, so a failure
        here cannot be a slow-database artifact.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            db.session.commit()
            user_id = seed_user["user"].id

            holder = db.engine.connect()
            try:
                holder.execute(
                    text("SELECT pg_advisory_xact_lock(:ns, :uid)"),
                    {"ns": _USER_WRITE_LOCK_NAMESPACE, "uid": user_id},
                )
                db.session.execute(
                    text(f"SET LOCAL lock_timeout = '{_BLOCK_TIMEOUT_MS}ms'"),
                )
                with pytest.raises(OperationalError) as excinfo:
                    account_posting_service \
                        .sync_account_anchor_postings_all_scenarios(account.id)
                assert "lock timeout" in str(excinfo.value).lower(), (
                    f"blocked for a reason other than the lock: {excinfo.value}"
                )
            finally:
                # Roll the holder's transaction back before closing, so the
                # advisory lock is released even if the assertion failed.
                holder.rollback()
                holder.close()
                db.session.rollback()

    def test_the_same_call_completes_when_the_key_is_free(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The non-vacuity control for the blocking test above.

        Same account, same call, same ``lock_timeout`` -- and nothing holding
        the key.  It completes, so the timeout in the sibling test is the held
        lock and not the statement being slow.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            db.session.commit()

            db.session.execute(
                text(f"SET LOCAL lock_timeout = '{_BLOCK_TIMEOUT_MS}ms'"),
            )
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                account.id,
            )
            db.session.rollback()


class TestTheKeyIsTheOwner:
    """One key per user -- the property that makes deadlock impossible."""

    def test_two_accounts_of_one_user_share_the_key(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Reconciling two of one user's accounts takes ONE distinct key.

        This is the whole deadlock argument, graded: a transaction that can
        only ever hold one key cannot hold two in the wrong order.  A
        per-ACCOUNT lock would emit two distinct keys here and would need every
        multi-account caller to acquire in a global order.
        """
        assert seed_periods_today
        with app.app_context():
            first = _checking_account(seed_user)
            second = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=first.account_type_id,
                    name="Lock Probe Checking Two",
                    anchor_balance=Decimal("2500.00"),
                ),
            )
            db.session.flush()

            _result, statements = capture_sql_statements(
                lambda: [
                    account_posting_service
                    .sync_account_anchor_postings_all_scenarios(acct.id)
                    for acct in (first, second)
                ],
            )

            # Asserted on the emitted BIND PARAMETERS, never on statement text.
            # SQLAlchemy binds both arguments of
            # ``pg_advisory_xact_lock(namespace, key)``, so every acquisition
            # emits byte-identical SQL whatever it locks -- a first version of
            # this test compared `len(set(statement_text)) == 1` and passed
            # unchanged when the key was mutated to ``account.id``, which is
            # precisely the design it claims to grade.  Two adversarial reviews
            # found that independently.
            keys = advisory_lock_keys(statements)
            assert keys, "no lock was taken at all"
            owner = seed_user["user"].id
            assert first.user_id == owner and second.user_id == owner
            assert set(keys) == {(_USER_WRITE_LOCK_NAMESPACE, owner)}, (
                f"reconciling two accounts of one owner must take exactly ONE "
                f"key, the owner's; got {sorted(set(keys))}.  A per-account or "
                f"per-scenario key would show as two, and would reintroduce "
                f"the acquisition-order problem the single key removes"
            )

    def test_lock_every_user_returns_ascending_ids(self, app, db, seed_user):
        """The all-owners form acquires ascending by user id.

        The deploy-wide backfills are the only transactions that reconcile more
        than one owner, so they are the only ones that hold more than one key.
        They pre-take them in this order, which is what keeps two concurrent
        sweeps from taking the same two keys in opposite orders.
        """
        assert seed_user
        with app.app_context():
            locked, statements = capture_sql_statements(lock_every_user_writes)
            # The ACQUISITION order, read off the emitted binds -- not the
            # returned list, which is sorted by its own ``ORDER BY`` and would
            # stay sorted even if the loop acquired in reverse.
            acquired = [key for _ns, key in advisory_lock_keys(statements)]
            assert acquired, "no lock was taken at all"
            assert acquired == sorted(acquired), (
                f"locks were ACQUIRED out of order: {acquired}"
            )
            assert acquired == locked, (
                f"the returned ids {locked} disagree with what was locked "
                f"{acquired}"
            )
            assert seed_user["user"].id in acquired
            db.session.rollback()

    def test_lock_is_reentrant_within_one_transaction(self, app, db, seed_user):
        """Taking the same key twice in one transaction is harmless.

        Nested callers rely on this: the all-scenarios sync locks and then
        loops the per-scenario sync, which locks again, and a pay-period reset
        locks and then resyncs every account.  A non-re-entrant lock would
        self-deadlock on the second acquisition.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.execute(
                text(f"SET LOCAL lock_timeout = '{_BLOCK_TIMEOUT_MS}ms'"),
            )
            _result, statements = capture_sql_statements(
                lambda: [lock_user_writes(user_id), lock_user_writes(user_id)],
            )
            keys = advisory_lock_keys(statements)
            assert keys == [
                (_USER_WRITE_LOCK_NAMESPACE, user_id),
                (_USER_WRITE_LOCK_NAMESPACE, user_id),
            ], (
                f"expected two acquisitions of the one key; got {keys}"
            )
            db.session.rollback()
