"""
Shekel Budget App -- Anchor Service Tests

Unit tests for :mod:`app.services.anchor_service`.  Pins the three
outcomes of :func:`apply_anchor_true_up` and the non-F-103
``IntegrityError`` re-raise contract.

Pre-extraction these branches were covered indirectly by the grid
HTMX-route test suites (``TestTrueUpSameDayDuplicate`` and
``TestTrueUpStaleForm``).  The route suites still exercise the
wiring; these tests pin the helper's contract directly so a future
change to the route cannot accidentally drift the shared semantics,
and they close the pre-existing coverage gap for the F-103 same-day
idempotency path.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import account_service, anchor_service, pay_period_service
from app.services.anchor_service import (
    ANCHOR_HISTORY_UNIQUE_INDEX,
    LOAN_ANCHOR_EVENT_UNIQUE_INDEX,
    AnchorTrueUpOutcome,
    apply_anchor_true_up,
    apply_loan_anchor_true_up,
)
from tests._test_helpers import insert_origination_rate


def _bump_account_version_outside_session(account_id):
    """Simulate a concurrent commit by bumping ``version_id`` directly.

    Mirrors the helper of the same name in ``test_accounts.py``;
    factored to module scope so each test file owns its copy.  Uses a
    fresh DB connection so the calling session's in-memory identity
    map is unaffected; commit is essential because READ COMMITTED MVCC
    would otherwise hide the UPDATE from the test session.
    """
    with db.engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE budget.accounts "
                "SET version_id = version_id + 1 "
                "WHERE id = :id"
            ),
            {"id": account_id},
        )
        conn.commit()


def _make_checking_account(seed_user, periods, anchor_balance="1000.00"):
    """Create a fresh Checking account anchored at ``periods[0]``."""
    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Helper Checking",
            anchor_balance=Decimal(anchor_balance),
            anchor_period_id=periods[0].id,
        ),
    )


def _make_savings_account(seed_user, periods, anchor_balance="500.00"):
    """Create a fresh Savings account anchored at ``periods[0]``."""
    savings_type = db.session.query(AccountType).filter_by(
        name="Savings",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Helper Savings",
            anchor_balance=Decimal(anchor_balance),
            anchor_period_id=periods[0].id,
        ),
    )


def _make_projected_expense_with_past_dated_entry(seed_user, period, amount):
    """Create a Projected expense with one uncleared past-dated debit entry.

    Used by the checking-clears-entries test: the helper's contract is
    that a checking true-up flips ``is_cleared = TRUE`` on past-dated
    debit entries of projected parents.  Returns the
    :class:`TransactionEntry` so the caller can re-read its
    ``is_cleared`` flag after the true-up.
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = db.session.query(TransactionType).filter_by(
        name="Expense",
    ).one()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name="Groceries",
        default_amount=Decimal("500.00"),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Groceries",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("500.00"),
    )
    db.session.add(txn)
    db.session.flush()

    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="Past-dated debit",
        entry_date=date.today() - timedelta(days=1),
        is_credit=False,
        is_cleared=False,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


class TestApplyAnchorTrueUpCommitted:
    """COMMITTED outcome: helper writes balance + history and commits."""

    def test_savings_true_up_committed_no_entries_cleared(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Non-checking account: helper commits without touching entries.

        Setup: a Savings account anchored at periods[0]; one
        projected checking-account expense with an uncleared past-
        dated debit entry (created on the seed_user checking account,
        NOT on the savings account being trued up).

        Hand-check: after ``apply_anchor_true_up`` on the savings
        account, the outcome is COMMITTED, the savings anchor balance
        is the new value, exactly one new history row exists for the
        savings account, and the unrelated checking entry's
        ``is_cleared`` is unchanged (debit entries only hit checking
        and the savings true-up must not touch them).
        """
        with app.app_context():
            savings = _make_savings_account(
                seed_user, seed_periods_today, anchor_balance="500.00",
            )
            db.session.commit()

            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            # An unrelated past-dated checking debit -- must stay
            # uncleared because the savings true-up does not run the
            # entry reconcile.
            entry = _make_projected_expense_with_past_dated_entry(
                seed_user, current_period, amount="50.00",
            )
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )

            outcome = apply_anchor_true_up(
                account=savings,
                new_balance=Decimal("750.00"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, savings.id)
            assert reloaded.current_anchor_balance == Decimal("750.00")
            assert reloaded.current_anchor_period_id == current_period.id

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )
            assert history_count_after == history_count_before + 1, (
                "Savings true-up must append exactly one history row."
            )

            # Debit entry untouched: savings true-up does NOT clear
            # checking-only past-dated entries.
            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.is_cleared is False, (
                "Savings true-up must not flip is_cleared on debit entries "
                "(debits only hit checking; only a checking true-up "
                "reconciles them)."
            )

    def test_checking_true_up_committed_clears_past_dated_entries(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Checking account: helper commits AND clears past-dated entries.

        Setup: the seed_user account is Checking; one projected
        envelope expense with one uncleared past-dated debit entry of
        ``$50.00`` on the current period.

        Hand-check: after ``apply_anchor_true_up`` on the seed_user
        checking account, the outcome is COMMITTED, the anchor
        balance is the new value, exactly one new history row exists,
        AND the past-dated debit entry's ``is_cleared`` flipped to
        True (the entry-reconcile contract -- see
        ``entry_service.clear_entries_for_anchor_true_up``).

        Re-fetches the account via ``db.session.get`` so it is
        attached to the current scoped session.  The conftest's ``db``
        fixture removes/disposes the session at the start of each
        test, so the cached ``seed_user["account"]`` reference is in
        a stale session and would not flush correctly otherwise.  This
        mirrors the route pattern (every route opens with
        ``db.session.get(Account, account_id)``).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            entry = _make_projected_expense_with_past_dated_entry(
                seed_user, current_period, amount="50.00",
            )
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )

            outcome = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("2500.00"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert reloaded.current_anchor_balance == Decimal("2500.00")
            assert reloaded.current_anchor_period_id == current_period.id

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )
            assert history_count_after == history_count_before + 1

            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.is_cleared is True, (
                "Checking true-up MUST flip past-dated debit entries "
                "(entry_service.clear_entries_for_anchor_true_up contract)."
            )


class TestApplyAnchorTrueUpStaleConflict:
    """STALE_CONFLICT outcome: SQLAlchemy version_id race -> rollback + 409.

    Engineers a true mid-flush race using a SQLAlchemy ``before_update``
    listener that bumps ``version_id`` from a separate connection at
    the exact moment SQLAlchemy issues its version-pinned UPDATE.
    Mirrors the proven route-level test ``test_true_up_route_catches_
    stale_data_error_as_409`` so the unit test exercises the same code
    path the routes do, without the HTTP round-trip.
    """

    def test_helper_returns_stale_conflict_and_rolls_back(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Mid-flush ``version_id`` bump produces ``STALE_CONFLICT`` and
        rolls back every pending mutation.

        Hand-check: after the helper returns ``STALE_CONFLICT``:
          * The account's persisted ``current_anchor_balance`` equals
            the pre-call value (rolled back).
          * The history-row count for the account equals the pre-call
            count (no new row).
          * The account's ``version_id`` has advanced by exactly 1
            (the OUT-of-session bump applied; the in-session UPDATE
            did not).

        Account is re-fetched via ``db.session.get`` to attach it to
        the current scoped session (see
        ``test_checking_true_up_committed_clears_past_dated_entries``
        for the rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )
            balance_before = account.current_anchor_balance
            version_before = account.version_id
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )

            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                """Bump version_id from a separate connection mid-flush."""
                if fired["flag"] or target.id != account.id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(account.id)

            event.listen(Account, "before_update", make_stale)
            try:
                outcome = apply_anchor_true_up(
                    account=account,
                    new_balance=Decimal("9999.99"),
                    anchor_period=current_period,
                    user_id=seed_user["user"].id,
                )
            finally:
                event.remove(Account, "before_update", make_stale)

            assert outcome is AnchorTrueUpOutcome.STALE_CONFLICT

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert reloaded.current_anchor_balance == balance_before, (
                "STALE_CONFLICT must roll back the pending balance write."
            )
            assert reloaded.version_id == version_before + 1, (
                "Only the OUT-of-session version bump survived; the "
                "in-session UPDATE was rolled back."
            )
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            ) == history_count_before, (
                "STALE_CONFLICT must roll back the pending history INSERT."
            )


class TestApplyAnchorTrueUpDuplicateSameDay:
    """DUPLICATE_SAME_DAY outcome: F-103 / C-22 idempotent success.

    The partial unique expression index
    ``uq_anchor_history_account_period_balance_day`` rejects a second
    INSERT for the same ``(account_id, pay_period_id, anchor_balance,
    UTC day)`` tuple.  The helper translates that into
    ``DUPLICATE_SAME_DAY`` rather than letting the ``IntegrityError``
    escape.
    """

    def test_double_call_same_balance_returns_duplicate_same_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical helper calls produce exactly one history row.

        Hand-check: ``apply_anchor_true_up`` called twice with the
        same ``(account, balance, period)`` on the same UTC day
        returns ``COMMITTED`` then ``DUPLICATE_SAME_DAY``, and the
        on-disk history shows exactly one row at the duplicate balance
        (plus whatever origination/prior history the fixture wrote).

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )

            outcome_first = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            # Second call: same balance, same period, same UTC day.
            # The F-103 partial unique index rejects the INSERT and
            # the helper translates that into idempotent success.
            outcome_second = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )
            assert outcome_second is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY

            db.session.expire_all()
            rows_at_duplicate_balance = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("1234.56"),
                )
                .all()
            )
            assert len(rows_at_duplicate_balance) == 1, (
                f"F-103 / C-22: expected exactly one history row at the "
                f"duplicate balance after the double call, found "
                f"{len(rows_at_duplicate_balance)}."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        F-103 / C-22 negative case: the unique constraint includes
        ``anchor_balance``, so a legitimate same-day correction (the
        user noticed an error and re-trued at a different amount)
        MUST NOT be blocked.  Both calls return ``COMMITTED``.

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )

            outcome_a = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1000.00"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )
            outcome_b = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1100.00"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )
            assert outcome_a is AnchorTrueUpOutcome.COMMITTED
            assert outcome_b is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            balances = {
                row.anchor_balance for row in
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .all()
            }
            # The fixture's origination row sits at $1000.00 (seed
            # balance); the unique index correctly suppresses the
            # second $1000.00, but the $1100.00 row survives.  Assert
            # the two true-up balances are both represented.
            assert Decimal("1100.00") in balances, (
                "$1100.00 same-day correction must commit a history row."
            )
            assert Decimal("1000.00") in balances, (
                "$1000.00 history row must survive the same-day double."
            )


class TestApplyAnchorTrueUpReraisesUnknownIntegrityError:
    """Unknown ``IntegrityError`` re-raises (no silent F-103 swallowing)."""

    def test_non_f103_integrity_error_is_reraised(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A different constraint failure must NOT be treated as idempotent.

        Engineers the case by patching ``is_unique_violation`` (the
        helper's discriminator) to return False -- the helper sees an
        IntegrityError but cannot confirm it is the F-103 unique-index
        violation, so it must re-raise.  This pins the
        "don't silently swallow IntegrityError" contract independent of
        which constraint the engine fired.

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see ``TestApplyAnchorTrueUpCommitted`` rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id
            )

            # Commit one history row at this balance so the next call
            # WILL produce an IntegrityError from F-103.
            outcome_first = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("2222.22"),
                anchor_period=current_period,
                user_id=seed_user["user"].id,
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            # Now patch the discriminator so the IntegrityError is
            # not recognised as F-103.  The helper must re-raise.
            with patch(
                "app.services.anchor_service.is_unique_violation",
                return_value=False,
            ):
                with pytest.raises(IntegrityError):
                    apply_anchor_true_up(
                        account=account,
                        new_balance=Decimal("2222.22"),
                        anchor_period=current_period,
                        user_id=seed_user["user"].id,
                    )

            # And the session is clean (the helper rolled back before
            # re-raising, so subsequent work is unimpeded).
            db.session.rollback()  # defensive; idempotent.
            reloaded = db.session.get(Account, account.id)
            assert reloaded.current_anchor_balance == Decimal("2222.22"), (
                "First commit's balance survives; the re-raised "
                "IntegrityError on the second call rolled back its "
                "own attempted mutation."
            )


class TestApplyAnchorTrueUpModuleContract:
    """Pins the public surface of the module so renames are caught."""

    def test_unique_index_constant_matches_model_index_name(self, app):
        """The exported constant must match the live index name.

        Three sites must agree: the model
        (``AccountAnchorHistory.__table_args__``), the migration
        (``e8b14f3a7c22``), and the helper (this module).  A drift in
        any one would break the F-103 catch silently -- the
        IntegrityError would no longer be recognised and the helper
        would re-raise instead of returning ``DUPLICATE_SAME_DAY``.
        """
        with app.app_context():
            assert ANCHOR_HISTORY_UNIQUE_INDEX == (
                "uq_anchor_history_account_period_balance_day"
            )
            # Confirm the index name is present on the live model.
            from app.models.account import AccountAnchorHistory  # pylint: disable=import-outside-toplevel
            index_names = {
                idx.name for idx in AccountAnchorHistory.__table_args__
                if hasattr(idx, "name")
            }
            assert ANCHOR_HISTORY_UNIQUE_INDEX in index_names

    def test_outcome_enum_has_exactly_three_members(self):
        """The outcome enum is the route's switch discriminant; pin its size.

        Adding a new outcome would require route-side handling for
        every consumer; the test fails loud if the enum grows without
        a coordinated route update.
        """
        members = {m.name for m in AnchorTrueUpOutcome}
        assert members == {
            "COMMITTED", "STALE_CONFLICT", "DUPLICATE_SAME_DAY",
        }


# ── Loan Anchor Trueup ────────────────────────────────────────────────


def _make_loan_account(seed_user, name="Helper Loan",
                       original_principal="20000.00",
                       interest_rate="0.05000",
                       term_months=60,
                       origination_date=None):
    """Create a fresh Auto Loan account + LoanParams + origination event.

    Builds the minimal loan inventory the loan-anchor true-up tests
    need: an account, a :class:`LoanParams` row, and an origination
    :class:`LoanAnchorEvent`.  Mirrors the production setup in
    :func:`app.routes.loan.create_params` so the same anchor-event
    invariants the resolver relies on are satisfied.

    Returns the Account ORM instance.
    """
    if origination_date is None:
        origination_date = date.today() - timedelta(days=365)

    auto_type = db.session.query(AccountType).filter_by(
        name="Auto Loan",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=auto_type.id,
            name=name,
            anchor_balance=Decimal(original_principal),
        ),
    )
    db.session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal(original_principal),
        current_principal=Decimal(original_principal),
        term_months=term_months,
        origination_date=origination_date,
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()

    insert_origination_rate(params, Decimal(interest_rate))

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=origination_date,
        anchor_balance=Decimal(original_principal),
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()
    return account


class TestApplyLoanAnchorTrueUpCommitted:
    """COMMITTED outcome: helper appends an event and commits."""

    def test_commits_appends_new_event_without_mutating_prior(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Helper writes a single new user_trueup event; prior rows untouched.

        Hand-check: fixture seeds exactly one origination event at
        $20,000 dated 365 days ago.  After
        :func:`apply_loan_anchor_true_up` with anchor $18,500 dated
        today:
          * Outcome is COMMITTED.
          * Total event count is 2 (origination + new trueup).
          * The new event has ``source_id`` == USER_TRUEUP id, the
            posted balance, and the posted date.
          * The origination event is byte-identical (no UPDATE).
            Compared by primary key + every persisted column to
            prove append-only semantics.
          * :class:`LoanParams.current_principal` is unchanged
            (the column is non-authoritative seed; the trueup writes
            an event, not the column).
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            origination = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .one()
            )
            orig_snapshot = (
                origination.id,
                origination.anchor_date,
                origination.anchor_balance,
                origination.source_id,
                origination.created_at,
            )
            params_before = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            seed_principal = params_before.current_principal

            outcome = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("18500.00"),
                anchor_date=date.today(),
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .order_by(LoanAnchorEvent.id)
                .all()
            )
            assert len(events) == 2, (
                "Trueup must append exactly one new event."
            )

            origination_after = next(e for e in events if e.id == orig_snapshot[0])
            after_snapshot = (
                origination_after.id,
                origination_after.anchor_date,
                origination_after.anchor_balance,
                origination_after.source_id,
                origination_after.created_at,
            )
            assert after_snapshot == orig_snapshot, (
                "Prior origination event must NOT be mutated by a "
                "trueup (LoanAnchorEvent is structurally append-only)."
            )

            trueup = next(e for e in events if e.id != orig_snapshot[0])
            user_trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            assert trueup.source_id == user_trueup_source_id
            assert trueup.anchor_balance == Decimal("18500.00")
            assert trueup.anchor_date == date.today()

            # :class:`LoanParams.current_principal` is non-authoritative
            # seed (E-18) -- the trueup must NOT mutate it.
            params_after = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            assert params_after.current_principal == seed_principal


class TestApplyLoanAnchorTrueUpDuplicateSameDay:
    """DUPLICATE_SAME_DAY: same (date, balance) on the same UTC day -> idempotent."""

    def test_double_call_same_balance_returns_duplicate_same_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical trueups produce exactly one new event row.

        Hand-check: :func:`apply_loan_anchor_true_up` called twice
        with the same ``(date, balance)`` on the same UTC day
        returns ``COMMITTED`` then ``DUPLICATE_SAME_DAY``, and the
        event log shows exactly one trueup row (plus the
        origination).
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_first = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            outcome_second = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            assert outcome_second is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY

            db.session.expire_all()
            trueups = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("17000.00"),
                    anchor_date=today,
                )
                .all()
            )
            assert len(trueups) == 1, (
                "Same-day same-balance double-submit must produce "
                "exactly one row (uq_loan_anchor_events_acct_date_bal_day)."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day trueups with different balances both succeed.

        The unique index covers ``anchor_balance`` as well as
        ``anchor_date``, so a legitimate same-day correction (the
        user noticed an error and re-trued at a different amount)
        MUST NOT be blocked.  Both calls return ``COMMITTED``; the
        resolver's (anchor_date, created_at) DESC ordering naturally
        picks the later one for display.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_a = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=today,
            )
            outcome_b = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("16500.00"),
                anchor_date=today,
            )
            assert outcome_a is AnchorTrueUpOutcome.COMMITTED
            assert outcome_b is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            balances = {
                row.anchor_balance for row in
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            }
            assert Decimal("17000.00") in balances
            assert Decimal("16500.00") in balances


class TestApplyLoanAnchorTrueUpReraisesUnknownIntegrityError:
    """Unknown ``IntegrityError`` re-raises (no silent same-day swallowing)."""

    def test_non_duplicate_integrity_error_is_reraised(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A different constraint failure must NOT be treated as idempotent.

        Engineers the case by patching ``is_unique_violation`` -- the
        same-day-duplicate discriminator, which the Build-Order Step 4
        wiring moved into the shared
        :func:`app.services.loan_posting_service.sync_all_scenarios_or_duplicate`
        helper (the loan true-up now re-splits every scenario in the same
        transaction and delegates the duplicate translation there) -- to return
        False.  The helper sees an IntegrityError but cannot confirm it is the
        same-day uniqueness violation, so it must re-raise.  Pins the "don't
        silently swallow IntegrityError" contract independent of which
        constraint the engine fired.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            outcome_first = apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("19000.00"),
                anchor_date=today,
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            with patch(
                "app.services.loan_posting_service.is_unique_violation",
                return_value=False,
            ):
                with pytest.raises(IntegrityError):
                    apply_loan_anchor_true_up(
                        account=account,
                        anchor_balance=Decimal("19000.00"),
                        anchor_date=today,
                    )

            # Session is clean after the re-raise (the helper rolled
            # back before raising, so subsequent work is unimpeded).
            db.session.rollback()
            trueups_at_balance = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("19000.00"),
                )
                .all()
            )
            assert len(trueups_at_balance) == 1, (
                "First trueup row survives; the re-raised "
                "IntegrityError rolled back the second attempt."
            )


class TestApplyLoanAnchorTrueUpModuleContract:
    """Pins the public surface of the loan-anchor helper."""

    def test_loan_unique_index_constant_matches_model(self, app):
        """The exported constant must match the live index name.

        Three sites must agree: the model
        (:class:`LoanAnchorEvent.__table_args__`), the
        loan_anchor_events migration, and the helper (this module).
        A drift in any one would break the same-day catch silently --
        the IntegrityError would no longer be recognised and the
        helper would re-raise instead of returning
        ``DUPLICATE_SAME_DAY``.
        """
        with app.app_context():
            assert LOAN_ANCHOR_EVENT_UNIQUE_INDEX == (
                "uq_loan_anchor_events_acct_date_bal_day"
            )
            index_names = {
                idx.name for idx in LoanAnchorEvent.__table_args__
                if hasattr(idx, "name")
            }
            assert LOAN_ANCHOR_EVENT_UNIQUE_INDEX in index_names
