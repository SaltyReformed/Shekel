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
3. **It is keyed on the OWNER**, so a transaction takes one advisory key and
   never two, and no pair of advisory acquisitions can be ordered differently
   by two transactions (:mod:`app.services.user_write_lock`).  **That is NOT
   "deadlock is structurally impossible on every request path", which this
   docstring claimed until plan step X-f1e2**: the advisory lock is not always a
   transaction's FIRST lock, so an advisory-versus-ROW-lock cycle remains
   reachable -- finding **N-193**, reproduced against a real PostgreSQL, and
   the module's own docstring retracts the stronger claim in the same words.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models.ref import AccountType
from app.models.account import Account, AccountAnchorHistory
from app.services import (
    account_posting_service,
    account_service,
    anchor_service,
    cash_ledger,
    loan_posting_service,
    opening_service,
)
from app.utils.dates import display_today
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


class TestTheAnchorDoorsTakeTheLockBeforeTheyRead:
    """Ruling R-EQ's compare-then-append is locked, on every anchor door.

    **Every one of these was a surviving mutant.**  A neutral concurrency review
    of plan step X-f1c4b applied four mutations to ``anchor_service`` -- delete
    the cash lock, move it after the cash read, delete the loan lock, move it
    after the loan read -- and the FULL 7,813-test suite passed under all four.
    The rule the step exists for was entirely ungraded: `test_anchor_service.py`
    contained no lock assertion at all, and this module's ordering class graded
    only the reconciles, which the doors reach several statements later.

    A compare-then-append is a read-modify-write.  Locking after the read
    serialises nothing -- the loser re-reads the same pre-state it would have
    read anyway and appends its duplicate -- so PRESENCE and ORDERING are graded
    separately, exactly as :class:`TestTheReconcileTakesTheLockBeforeItReads`
    does one layer down.
    """

    def test_cash_true_up_locks_before_reading_the_governing_assertion(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``apply_anchor_true_up`` locks before it reads anchor history."""
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            db.session.commit()

            _result, statements = capture_sql_statements(
                lambda: anchor_service.apply_anchor_true_up(
                    account=account, new_balance=Decimal("1750.00"),
                ),
            )

            assert took_advisory_lock(statements), (
                "the cash true-up emitted no advisory lock; two concurrent "
                "submissions would both read the pre-state and both append"
            )
            assert any(
                "account_anchor_history" in text for text, _params in statements
            ), "the door read no anchor history -- the test graded nothing"
            assert advisory_lock_precedes(statements, "account_anchor_history"), (
                "the door read the governing assertion BEFORE taking the lock; "
                "serialising after the read serialises nothing"
            )

    def test_the_opening_door_locks_before_reading_the_governing_opening(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``apply_opening_restatement`` locks before it reads the opening.

        The THIRD compare-then-append on this family (plan step
        **X-f3c-2b-2a**), and the one whose lock is now load-bearing in
        writing: ``app.opening_infrastructure`` says the books-boundary
        triggers take no lock and that what closes the two-transaction race
        instead is that both DOORS take the owner's.  A mechanism named in a
        module docstring and enforced by nothing is the shape this whole class
        was built after.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            db.session.commit()
            standing = cash_ledger.account_opening_fact(account.id)

            _result, statements = capture_sql_statements(
                lambda: opening_service.apply_opening_restatement(
                    account=account,
                    opening=opening_service.BooksOpening(
                        standing.opened_on - timedelta(days=1),
                        Decimal("1234.00"),
                    ),
                ),
            )

            assert took_advisory_lock(statements), (
                "the restatement door emitted no advisory lock; two concurrent "
                "submissions would both read the pre-state and both append, "
                "and the books-boundary triggers take no lock of their own"
            )
            assert any(
                "account_openings" in text for text, _params in statements
            ), "the door read no opening row -- the test graded nothing"
            assert advisory_lock_precedes(statements, "account_openings"), (
                "the door read the governing opening BEFORE taking the lock; "
                "serialising after the read serialises nothing"
            )

    def test_the_opening_door_locks_before_reading_the_MOVEMENTS(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The other read the lock protects, and the one it did not at first.

        The day bound reads ``budget.transactions`` and
        ``budget.transaction_entries``, which a concurrent settle is WRITING --
        so an unlocked read lets the restatement pass its own predicate and
        then abort at COMMIT on the deferred trigger, which is a raw
        ``psycopg2`` 500 where ``cash_ledger._books`` exists to give a
        sentence.  Found by adversarial review 2026-08-31, when the lock sat
        below the bound rather than above it.

        Graded separately from the case above because the two reads are two
        statements and a lock between them satisfies one and not the other.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            db.session.commit()
            standing = cash_ledger.account_opening_fact(account.id)

            _result, statements = capture_sql_statements(
                lambda: opening_service.apply_opening_restatement(
                    account=account,
                    opening=opening_service.BooksOpening(
                        standing.opened_on - timedelta(days=1),
                        Decimal("4321.00"),
                    ),
                ),
            )

            assert any(
                "transaction_entries" in text for text, _params in statements
            ), "the door read no movement table -- the test graded nothing"
            assert advisory_lock_precedes(statements, "transaction_entries"), (
                "the door read the account's movements BEFORE taking the lock; "
                "a settle committing in that window makes the restatement abort "
                "at COMMIT instead of being refused with a sentence"
            )

    def test_the_opening_door_locks_the_OWNER_and_nothing_else(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The key is the owner, so it contends with every other door of theirs.

        Presence and ordering are both satisfied by a lock on the wrong key --
        ``pg_advisory_xact_lock`` binds both arguments, so every acquisition
        emits byte-identical SQL and only the parameters say what was locked.
        A restatement locking the ACCOUNT would serialise against nothing the
        settle path takes.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            db.session.commit()
            standing = cash_ledger.account_opening_fact(account.id)

            _result, statements = capture_sql_statements(
                lambda: opening_service.apply_opening_restatement(
                    account=account,
                    opening=opening_service.BooksOpening(
                        standing.opened_on - timedelta(days=1),
                        Decimal("999.00"),
                    ),
                ),
            )

            assert set(advisory_lock_keys(statements)) == {
                (_USER_WRITE_LOCK_NAMESPACE, seed_user["user"].id),
            }, (
                "the restatement locked something other than the owner: "
                f"{advisory_lock_keys(statements)}"
            )

    def test_loan_true_up_locks_before_reading_the_governing_event(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``apply_loan_anchor_true_up`` locks before it reads anchor events."""
        assert seed_periods_today
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Anchor Door Probe Loan",
                principal=Decimal("15000.00"), rate=Decimal("0.05000"),
            )
            db.session.commit()

            _result, statements = capture_sql_statements(
                lambda: anchor_service.apply_loan_anchor_true_up(
                    account=loan,
                    anchor_balance=Decimal("12345.67"),
                    anchor_date=display_today(),
                ),
            )

            assert took_advisory_lock(statements), (
                "the loan true-up emitted no advisory lock; the loan reconcile "
                "has carried this race since Commit 16"
            )
            assert any(
                "loan_anchor_events" in text for text, _params in statements
            ), "the door read no anchor events -- the test graded nothing"
            assert advisory_lock_precedes(statements, "loan_anchor_events"), (
                "the door read the governing event BEFORE taking the lock"
            )

    def test_account_edit_locks_before_it_touches_the_accounts_row(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """``update_account`` locks first on the branch that changes NO anchor.

        **This is the one that failed before the fix, and it is a DEADLOCK
        control rather than a race control.**  The anchor branch reaches the
        advisory lock inside ``stage_anchor_true_up`` before the ``setattr``
        loop's ``UPDATE budget.accounts`` flushes, while a type-only edit
        flushes that UPDATE first and does not reach the lock until the posting
        re-sync.  Two tabs, same account, one of each: PostgreSQL detects the
        cycle and aborts one with an unhandled 500 on a money route -- which a
        neutral review reproduced against a real database.

        Graded through the ROUTE rather than the service because the hazard is
        the route's statement ORDER, which is what a future refactor would
        silently invert.
        """
        assert seed_periods_today
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            savings_type_id = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one().id
            unchanged_balance = cash_ledger.resolve_anchor(account).balance
            form = {
                "name": account.name,
                "account_type_id": str(savings_type_id),
                "anchor_balance": str(unchanged_balance),
                "version_id": str(account.version_id),
                "is_active": "true",
            }

        _response, statements = capture_sql_statements(
            lambda: auth_client.post(
                f"/accounts/{seed_user['account'].id}", data=form,
            ),
        )

        assert took_advisory_lock(statements), (
            "the account-edit route emitted no advisory lock on its "
            "type-change branch"
        )
        assert any(
            text.strip().upper().startswith("UPDATE BUDGET.ACCOUNTS")
            for text, _params in statements
        ), "the edit wrote no accounts row -- the test graded nothing"
        assert advisory_lock_precedes(statements, "UPDATE budget.accounts"), (
            "the route took the accounts ROW lock before the advisory lock on "
            "this branch, while its anchor branch takes them the other way "
            "round -- two tabs, one of each, deadlock"
        )

    def test_account_creation_locks_before_it_writes_the_origination(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``create_account`` locks the owner before the assertion INSERT.

        **The control for plan step X-f1e2 / ruling R-ES, and it FAILS on the
        revert.**  The factory used to construct the origination
        ``AccountAnchorHistory`` row itself, so the app's first assertion about
        an account was the one assertion written with no advisory lock at all --
        the lock did not appear until two lines later, inside
        ``sync_account_anchor_postings_all_scenarios``, by which time the row
        was already in.  Routing the write through
        ``anchor_service.stage_anchor_true_up`` moves the lock in front of it.

        Graded on ORDER rather than presence, because presence was already true
        before the change and would have passed either build.
        """
        assert seed_periods_today
        with app.app_context():
            checking_type_id = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one().id

            _account, statements = capture_sql_statements(
                lambda: account_service.create_account(
                    account_service.AccountSpec(
                        user_id=seed_user["user"].id,
                        account_type_id=checking_type_id,
                        name="R-ES Origination Probe",
                        anchor_balance=Decimal("2500.00"),
                    ),
                ),
            )

            assert took_advisory_lock(statements), (
                "account creation emitted no advisory lock at all"
            )
            # The KEY, not merely the presence.  ``pg_advisory_xact_lock`` binds
            # both arguments, so every acquisition emits identical SQL and only
            # the parameters say WHAT was locked -- an adversarial review
            # mutated this call to ``lock_user_writes(account.id)`` and the
            # whole 693-test control set still passed.  Two different keys in
            # one transaction is the unordered-acquisition hazard the single
            # per-owner key exists to remove.
            owner_id = seed_user["user"].id
            assert _account.id != owner_id, (
                "this control cannot tell an owner key from an ACCOUNT key "
                f"while both are {owner_id} -- the fixture must not make them "
                "equal, or the mutation it exists to catch passes"
            )
            assert set(advisory_lock_keys(statements)) == {
                (_USER_WRITE_LOCK_NAMESPACE, owner_id),
            }, (
                "every advisory lock on the create path must be keyed on the "
                f"OWNER ({owner_id}); the run took "
                f"{advisory_lock_keys(statements)}.  More than one DISTINCT "
                "key in one transaction is the unordered-acquisition hazard "
                "the single per-owner key exists to remove (the repeats are "
                "the same re-entrant key and are harmless)"
            )
            # The INSERT specifically.  ``governing_anchor_on``'s SELECT names
            # the same table and comes first, so a build whose stager read the
            # table and returned without appending would satisfy a bare
            # "touched the table" check while writing nothing.
            assert any(
                text.strip().upper().startswith(
                    "INSERT INTO BUDGET.ACCOUNT_ANCHOR_HISTORY",
                )
                for text, _params in statements
            ), (
                "the factory INSERTed no anchor history -- the test graded "
                "nothing, and the E-19 invariant is broken besides"
            )
            # **The OPENING is the same claim one table over** (plan step
            # X-f3c-2b-2a).  The factory routes its books opening through
            # ``opening_service.stage_account_opening``, whose own
            # ``lock_user_writes`` is the FIRST acquisition on this path -- and
            # it is the only control that can see it, because
            # ``apply_opening_restatement`` (the other caller) takes a lock of
            # its own, so deleting the stager's leaves the restatement door's
            # three cases green.  Measured: with the stager's lock removed this
            # assertion is the one that fails.
            assert any(
                text.strip().upper().startswith(
                    "INSERT INTO BUDGET.ACCOUNT_OPENINGS",
                )
                for text, _params in statements
            ), (
                "the factory INSERTed no opening row -- the test graded "
                "nothing, and the fold has no level to start from besides"
            )
            assert advisory_lock_precedes(statements, "account_openings"), (
                "the books opening was read BEFORE the owner's write lock was "
                "taken; the writer's own lock is what makes the compare safe "
                "for every caller rather than for the one door that happens "
                "to lock above it"
            )
            assert advisory_lock_precedes(
                statements, "account_anchor_history",
            ), (
                "the origination assertion was written BEFORE the owner's "
                "write lock was taken, which is the second-writer shape "
                "ruling R-ES deleted"
            )

            # And the row the whole path exists to produce.  Ordering is not
            # correctness on its own: a build that locked correctly and wrote
            # the wrong day or balance passes every assertion above.
            written = db.session.query(AccountAnchorHistory).filter_by(
                account_id=_account.id,
            ).all()
            assert len(written) == 1
            assert written[0].anchor_balance == Decimal("2500.00")
            assert written[0].observed_on == display_today()
