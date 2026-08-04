"""
Shekel Budget App -- Anchor Service Tests

Unit tests for :mod:`app.services.anchor_service`.  Pins both outcomes of
:func:`apply_anchor_true_up` and its loan twin, ruling **R-EQ**'s duplicate
rule, and the contract that an unexpected ``IntegrityError`` propagates.

Pre-extraction these branches were covered indirectly by the grid
HTMX-route test suites (``TestTrueUpSameDayDuplicate`` and
``TestTrueUpStaleForm``).  The route suites still exercise the
wiring; these tests pin the helper's contract directly so a future
change to the route cannot accidentally drift the shared semantics.

**The idempotency tests graded a UNIQUE INDEX until plan step X-f1c4b** and
now grade the write-door rule that replaced it: an assertion is refused only
when it changes nothing.  The index answered the double-submit cases the same
way; it answered a re-assertion of a superseded balance WRONGLY, and no test
had asked it -- so each door gained one
(``test_reasserting_a_superseded_balance_is_recorded``).
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy as sa
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
from app.services import (
    account_service,
    anchor_service,
    loan_loaders,
    pay_period_service,
)
from app.services.anchor_service import (
    AnchorTrueUpOutcome,
    apply_anchor_true_up,
    apply_loan_anchor_true_up,
    record_loan_tracking_start,
)
from app.utils.dates import display_today
from tests._test_helpers import insert_origination_rate
from app.services import cash_ledger


def _make_checking_account(seed_user, anchor_balance="1000.00"):
    """Create a fresh Checking account carrying one balance assertion.

    It took a ``periods`` argument, to anchor the account at ``periods[0]``,
    until ruling R-EH deleted ``AccountSpec.anchor_period_id`` (plan step
    X-f1c3c).  An assertion is a day and a balance now; the caller's periods
    have nothing to say about it.
    """
    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Helper Checking",
            anchor_balance=Decimal(anchor_balance),
        ),
    )


def _make_savings_account(seed_user, anchor_balance="500.00"):
    """Create a fresh Savings account carrying one balance assertion.

    See :func:`_make_checking_account` for why it no longer takes ``periods``.
    """
    savings_type = db.session.query(AccountType).filter_by(
        name="Savings",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Helper Savings",
            anchor_balance=Decimal(anchor_balance),
        ),
    )


def _make_projected_expense_with_past_dated_entry(seed_user, period, amount):
    """Create a Projected expense with one unobserved past-dated debit entry.

    Used by the two tests that pin what a true-up does NOT do: its
    ``settled_on`` starts NULL -- the bank has not been seen to take it -- and
    it must still be NULL afterwards.  Returns the
    :class:`TransactionEntry` so the caller can re-read that column.
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
        purchased_on=date.today() - timedelta(days=1),
        is_credit=False,
        settled_on=None,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


class TestApplyAnchorTrueUpCommitted:
    """COMMITTED outcome: helper writes balance + history and commits."""

    def test_savings_true_up_commits_without_touching_entries(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Non-checking account: helper commits without touching entries.

        Setup: a Savings account anchored at periods[0]; one
        projected checking-account expense with an unobserved past-
        dated debit entry (created on the seed_user checking account,
        NOT on the savings account being trued up).

        Hand-check: after ``apply_anchor_true_up`` on the savings
        account, the outcome is COMMITTED, the savings anchor balance
        is the new value, exactly one new history row exists for the
        savings account, and the unrelated checking purchase still has
        NO recorded posting day.
        """
        with app.app_context():
            savings = _make_savings_account(
                seed_user, anchor_balance="500.00",
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
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, savings.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal("750.00")

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )
            assert history_count_after == history_count_before + 1, (
                "Savings true-up must append exactly one history row."
            )

            # The purchase is untouched.  This was true before plan step S1-c
            # too -- the bulk clear was checking-only -- so this arm is a
            # rename rather than a re-ruling; the checking test beside it is
            # the one whose expectation INVERTED.
            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.settled_on is None, (
                "A true-up records no posting day on any account's "
                "purchases (ruling R-DH (d))."
            )

    def test_checking_true_up_records_no_posting_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """RE-RULED: a checking true-up touches NO entry (ruling R-DH (d)).

        Setup: the seed_user account is Checking; one projected
        envelope expense with one unobserved past-dated debit entry of
        ``$50.00`` on the current period.

        Hand-check: after ``apply_anchor_true_up`` on the seed_user
        checking account, the outcome is COMMITTED, the anchor
        balance is the new value, exactly one new history row exists,
        AND the purchase still has NO recorded posting day.

        **This test asserted the OPPOSITE until plan step S1-c**, and the
        inversion is the point of the step.  A bulk ``UPDATE`` flipped
        ``is_cleared = TRUE`` on every entry dated on or before the SERVER's
        today at every true-up, so whether a purchase counted as reconciled
        was decided by the order two buttons were pressed: record then true up
        and it cleared, true up then record and it never did.  The engine now
        never guesses a posting day; the user supplies it by ticking the
        purchase off a statement (``entry_service.record_settled_days``, whose
        own tests live with that service and whose ROUTE is graded in
        ``test_routes/test_accounts.py``).

        That reconcile step is deliberately a SEPARATE request: folding it in
        here would put it inside the transaction an UNCHANGED submission rolls
        back and reports as idempotent success -- so a re-assert of the
        governing balance would silently discard every reconciliation just made
        while the UI said it saved.

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
            )

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal("2500.00")

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )
            assert history_count_after == history_count_before + 1

            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.settled_on is None, (
                "A true-up must record NO posting day: whether the bank has "
                "taken a purchase is not derivable from a balance reading "
                "(ruling R-DH (d) / plan step S1-c)."
            )


class TestApplyAnchorTrueUpUnchanged:
    """UNCHANGED outcome: ruling R-EQ's idempotent success.

    An assertion is refused only when it changes nothing.  The write door
    compares the submission against the assertion that currently GOVERNS
    (``cash_ledger.resolve_anchor``) and appends only when they differ.

    **This class graded a UNIQUE INDEX until plan step X-f1c4b**
    (``uq_anchor_history_account_period_balance_day``, dropped by migration
    ``a3f6c1d84b90``).  The index answered the first two cases below the same
    way; it answered the THIRD wrongly, and no test had ever asked it.
    """

    def test_double_call_same_balance_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical helper calls produce exactly one history row.

        The retry case the rule exists for.  ``apply_anchor_true_up`` called
        twice with the same balance on the same civil day returns ``COMMITTED``
        then ``UNCHANGED``, and the on-disk history shows exactly one row at
        that balance (plus whatever origination/prior history the fixture
        wrote).

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            outcome_first = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
            )
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            # Second call: the same balance for the same civil day is now what
            # governs, so there is nothing to append.
            outcome_second = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
            )
            assert outcome_second is AnchorTrueUpOutcome.UNCHANGED

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
                f"A repeated submission must append nothing: expected exactly "
                f"one history row at that balance, found "
                f"{len(rows_at_duplicate_balance)}."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) MUST NOT be blocked.  Both calls
        return ``COMMITTED``.

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            outcome_a = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1000.00"),
            )
            outcome_b = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1100.00"),
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
            # The fixture's origination row already sits at $1000.00 for an
            # earlier day, so the first call above appends nothing new to this
            # SET while the $1100.00 row is added.  Assert both balances are
            # represented.
            assert Decimal("1100.00") in balances, (
                "$1100.00 same-day correction must commit a history row."
            )
            assert Decimal("1000.00") in balances, (
                "$1000.00 history row must survive the same-day double."
            )

    def test_reasserting_a_superseded_balance_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-asserting a balance that was corrected away is a real change.

        **The defect ruling R-EQ exists for, and the deleted unique index's
        negative control.**  Three submissions on one civil day:

          1. ``$500.00`` -- committed.
          2. ``$600.00`` -- committed (the user corrects themselves).
          3. ``$500.00`` -- the user decides the first reading was right.

        The third changes what governs from ``$600.00`` to ``$500.00``, so it
        must be recorded and the resolver must answer ``$500.00``.  Under
        ``uq_anchor_history_account_period_balance_day`` it collided with row 1,
        was swallowed as idempotent success, and every surface kept rendering
        ``$600.00`` while the app reported the correction saved.  Account 1 on
        the 2026-08-04 production clone carries 2-3 assertions on 3 of its 50
        assertion days, so this sequence is an ordinary bookkeeping session.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("500.00"),
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("600.00"),
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("500.00"),
            ) is AnchorTrueUpOutcome.COMMITTED, (
                "Re-asserting a superseded balance CHANGES what governs "
                "($600.00 -> $500.00), so it is a fact and must be recorded."
            )

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal(
                "500.00"
            ), (
                "The correction must be what governs.  A content-keyed unique "
                "index answered $600.00 here while reporting success."
            )
            rows_at_500 = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account.id, anchor_balance=Decimal("500.00"),
                )
                .all()
            )
            assert len(rows_at_500) == 2, (
                f"Both $500.00 assertions are facts -- the original and the "
                f"correction back to it -- so the append-only history holds "
                f"two, not {len(rows_at_500)}."
            )


class TestGoverningAnchorOnIsDayScoped:
    """``cash_ledger.governing_anchor_on`` answers as of a DAY, not as of now.

    The cash half of ruling R-EQ's horizon, and the unit that makes plan step
    X-f1c4c safe.  The cash door stamps ``display_today()`` until that step, so
    today the day-scoped answer and ``resolve_anchor`` coincide -- which is
    exactly why the rule is installed BEFORE the field that makes them diverge.
    Graded here directly rather than through a door that cannot yet reach it,
    because a rule with no reachable caller has no negative control.
    """

    def test_it_ignores_assertions_made_for_later_days(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An assertion for a LATER day does not govern an earlier one.

        Three assertions on the days AFTER the account's opening -- $100 on
        O+1, $200 on O+5, $300 on O+10 -- then ask what governed on O+5: $200,
        not the $300 that ``resolve_anchor`` returns.  Asking ``resolve_anchor``
        here is what made every back-dated re-submit append a duplicate on the
        loan door.

        Every day is derived from the account's OWN opening rather than from
        ``display_today()``, so the fixture holds wherever in the calendar it
        runs -- the seeded origination is itself dated by the fixture's clock,
        and days offset backwards from today can land before it.
        """
        with app.app_context():
            account = _make_checking_account(seed_user, anchor_balance="50.00")
            db.session.flush()
            opening = cash_ledger.resolve_anchor(account).observed_on
            for offset, balance in ((1, "100.00"), (5, "200.00"), (10, "300.00")):
                db.session.add(AccountAnchorHistory(
                    account_id=account.id,
                    anchor_balance=Decimal(balance),
                    observed_on=opening + timedelta(days=offset),
                ))
            db.session.commit()

            governing = cash_ledger.governing_anchor_on(
                account.id, opening + timedelta(days=5),
            )
            assert governing.balance == Decimal("200.00"), (
                f"the assertion governing O+5 is the one made ON O+5, not the "
                f"later O+10 one; got {governing.balance}"
            )
            assert governing.observed_on == opening + timedelta(days=5)
            assert cash_ledger.resolve_anchor(account).balance == Decimal(
                "300.00"
            ), "resolve_anchor still answers as of NOW -- the two differ"

    def test_it_answers_none_before_the_first_assertion(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A day before every assertion has nothing governing it.

        The writer's honest answer, and the reason this returns ``None`` where
        ``resolve_anchor`` raises: a submission for a day nothing governs is
        necessarily new, which is a fact about the write, not a broken
        invariant.
        """
        with app.app_context():
            account = _make_checking_account(seed_user, anchor_balance="75.00")
            db.session.commit()
            opening = cash_ledger.resolve_anchor(account).observed_on

            assert cash_ledger.governing_anchor_on(
                account.id, opening - timedelta(days=1),
            ) is None


class TestApplyAnchorTrueUpReraisesUnknownIntegrityError:
    """An unexpected ``IntegrityError`` propagates rather than being swallowed.

    **The ``except IntegrityError`` block this graded was DELETED at plan step
    X-f1c4b** with the index that produced it.  The contract survives the
    deletion and is what this now grades directly: nothing on the true-up path
    may convert a database-level failure into an outcome the route renders as
    success.  Kept rather than deleted because the cost of re-growing such a
    handler is a silent write loss reported as a save, which is the exact defect
    ruling R-EQ was ruled on.
    """

    def test_integrity_error_from_the_resync_is_not_swallowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ``IntegrityError`` raised by the posting re-sync reaches the caller.

        Engineered at the re-sync rather than at a real constraint because
        every constraint the true-up can now violate is a programming error
        with no reachable input: the decision that used to end in a unique
        violation is made before anything is staged.  Patching the one call
        that can still raise from the database keeps the contract testable
        without asserting on a specific constraint.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            with patch(
                "app.services.account_posting_service."
                "sync_account_anchor_postings_all_scenarios",
                side_effect=IntegrityError("stmt", {}, Exception("boom")),
            ):
                with pytest.raises(IntegrityError):
                    apply_anchor_true_up(
                        account=account,
                        new_balance=Decimal("2222.22"),
                    )

            db.session.rollback()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal(
                "1000.00"
            ), (
                "The failed true-up must not have committed: the seed "
                "origination's $1,000.00 still governs, because the $2,222.22 "
                "assertion was staged in the transaction the IntegrityError "
                "aborted.  Asserting the SURVIVING figure, not merely 'not the "
                "new one' -- a negative passes for any wrong value."
            )


# ``TestApplyAnchorTrueUpStaleConflict.test_helper_returns_stale_conflict_and_rolls_back``
# was DELETED at plan step X-f1c3c (ruling R-EN).  It graded
# ``AnchorTrueUpOutcome.STALE_CONFLICT``, which the enum no longer has: a
# true-up appends an assertion and never UPDATEs the ``accounts`` row that
# ``version_id`` guards, so ``StaleDataError`` is structurally unreachable on
# this path.  The append-only contract that replaced it is graded by
# ``test_accounts.TestTrueUpIsAppendOnly`` (a stale form still records) and by
# ``test_race_conditions.TestConcurrentAnchorUpdate`` (both concurrent requests
# 200, both assertions survive).


class TestApplyAnchorTrueUpModuleContract:
    """Pins the public surface of the module so renames are caught."""

    def test_outcome_enum_has_exactly_two_members(self):
        """The outcome enum is the route's switch discriminant; pin its size.

        Adding a new outcome would require route-side handling for every
        consumer; this fails loud if the enum grows without a coordinated
        route update.

        **It pinned THREE members until plan step X-f1c3c** and was briefly
        deleted there, on the mistaken ground that it graded
        ``STALE_CONFLICT`` -- which ruling R-EN did delete.  It does not: it
        grades the member SET, and that contract is fully alive with two
        members.  Restored after an adversarial review caught the deletion,
        because "the enum shrank" and "nobody is watching the enum any more"
        are not distinguishable from a green suite.
        """
        assert {m.name for m in AnchorTrueUpOutcome} == {
            "COMMITTED", "UNCHANGED",
        }

    def test_neither_anchor_table_carries_a_uniqueness_guard(self, app, db):
        """Ruling R-EQ's structural claim, asserted against the LIVE schema.

        The duplicate rule is a write-door comparison, and re-adding a unique
        index over either anchor table's own values would silently restore the
        false refusal ``TestApplyAnchorTrueUpUnchanged`` grades -- the door's
        compare would pass, the INSERT would collide, and the caller would see
        an ``IntegrityError`` on a legitimate correction.

        Asserted against ``pg_indexes`` rather than ``__table_args__`` because
        the model and the database can disagree: a model-only assertion passes
        on a database whose index was never dropped, which is the exact state
        migration ``a3f6c1d84b90`` exists to leave behind.
        """
        with app.app_context():
            unique_indexes = {
                (row.tablename, row.indexname)
                for row in db.session.execute(sa.text(
                    "SELECT tablename, indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'budget' AND tablename IN "
                    "('account_anchor_history', 'loan_anchor_events') "
                    "AND indexdef LIKE 'CREATE UNIQUE%' "
                    "AND indexname NOT LIKE '%_pkey'"
                ))
            }
            assert unique_indexes == set(), (
                f"An anchor table has regrown a uniqueness guard: "
                f"{sorted(unique_indexes)}.  Ruling R-EQ puts the duplicate "
                f"rule at the write door, which can compare against what "
                f"governs; an index over the row's values cannot, and refuses "
                f"a legitimate re-assertion."
            )


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


class TestRecordLoanTrackingStart:
    """The tracking-start opening flow appends a tracking_start event and re-syncs."""

    def test_commits_appends_tracking_start_opening(
        self, app, db, seed_user, seed_periods_today,
    ):
        """record_loan_tracking_start appends one tracking_start event as the opening.

        The fixture loan opens at $20,000 origination.  After a tracking-start of
        $18,000 dated today:
          * outcome is COMMITTED
          * exactly one tracking_start event is appended (origination + tracking = 2)
          * it carries the TRACKING_START source, balance, and date
          * the loan still OPENS at its $20,000 origination (step C1); the
            tracking-start loads as a non-opening assertion (``is_opening`` False,
            ``is_tracking_start`` True, balance 18000) that RESETS the walk.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            outcome = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            )
            assert len(events) == 2
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            tracking = next(
                e for e in events if e.source_id == tracking_source_id
            )
            assert tracking.anchor_balance == Decimal("18000.00")
            assert tracking.anchor_date == date.today()

            params = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            facts = loan_loaders.load_loan_anchor_facts(params)
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.is_tracking_start is False
            assert opening.anchor_balance == Decimal("20000.00")
            (tracking_fact,) = [
                fact for fact in facts if fact.is_tracking_start
            ]
            assert tracking_fact.is_opening is False
            assert tracking_fact.anchor_balance == Decimal("18000.00")

    def test_double_call_same_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A resubmit of the governing (date, balance) is idempotent."""
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            first = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            second = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            assert first is AnchorTrueUpOutcome.COMMITTED
            assert second is AnchorTrueUpOutcome.UNCHANGED

    def test_resubmit_is_recognised_after_a_later_trueup(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-submitted OPENING is recognised even under a later true-up.

        Ruling R-EQ scopes the loan comparison to the submission's own SOURCE,
        and this is why.  A ``tracking_start`` is by definition the earliest
        anchor, so once any ``user_trueup`` is recorded the latest row of ANY
        source is that true-up -- and comparing against it would let a
        double-submitted opening append a second ``tracking_start``, which
        ``loan_loaders._opening_anchor_fact`` reads as the loan's opening
        whatever else was recorded later.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()
            opening_day = date.today() - timedelta(days=30)

            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=opening_day,
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED

            # The latest row of ANY source is now the true-up; the opening is
            # still what a re-submitted opening duplicates.
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=opening_day,
            ) is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            openings = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=tracking_source_id)
                .all()
            )
            assert len(openings) == 1, (
                f"A re-submitted opening must append nothing; found "
                f"{len(openings)} tracking_start rows."
            )


class TestApplyLoanAnchorTrueUpUnchanged:
    """UNCHANGED: a resubmit of the governing (date, balance) is idempotent."""

    def test_double_call_same_balance_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical trueups produce exactly one new event row.

        Hand-check: :func:`apply_loan_anchor_true_up` called twice
        with the same ``(date, balance)`` returns ``COMMITTED`` then
        ``UNCHANGED``, and the event log shows exactly one trueup row
        (plus the origination).
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
            assert outcome_second is AnchorTrueUpOutcome.UNCHANGED

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
                "A repeated submission must append nothing: expected exactly "
                "one event row at that (date, balance)."
            )

    def test_reasserting_a_superseded_balance_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-asserting a loan balance that was corrected away is a real change.

        The loan twin of the checking regression, and the one whose exposure is
        LIVE today: the loan form already lets the user type the date, so the
        deleted ``uq_loan_anchor_events_acct_date_bal_day`` could refuse this
        sequence on any recording day.  ``$17,000`` -> ``$16,500`` -> back to
        ``$17,000`` for one anchor date; the third changes what governs and
        must be recorded.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            for balance in ("17000.00", "16500.00", "17000.00"):
                assert apply_loan_anchor_true_up(
                    account=account,
                    anchor_balance=Decimal(balance),
                    anchor_date=today,
                ) is AnchorTrueUpOutcome.COMMITTED, (
                    f"Asserting ${balance} changed what governs, so it is a "
                    "fact and must be recorded."
                )

            db.session.expire_all()
            trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            governing = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=trueup_source_id)
                .order_by(
                    LoanAnchorEvent.anchor_date.desc(),
                    LoanAnchorEvent.created_at.desc(),
                    LoanAnchorEvent.id.desc(),
                )
                .first()
            )
            assert governing.anchor_balance == Decimal("17000.00"), (
                "The correction back to $17,000.00 must be what governs; the "
                "deleted unique index left $16,500.00 governing while the "
                "route flashed that the balance was already recorded."
            )

    def test_back_dated_resubmit_is_idempotent(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-submitted BACK-DATED true-up appends nothing.

        **The defect the first version of ruling R-EQ's implementation had, and
        the reason the comparison is scoped to the submitted DATE.**  Comparing
        against the account's LATEST event instead, a submission for an earlier
        date can never compare equal -- so every double-click on a back-dated
        correction appended another row, permanently, in a table whose ORM
        guards refuse UPDATE and DELETE.  Two independent adversarial reviews
        reproduced it here, on the door that has had a user-supplied date field
        since Commit 16.

        Sequence: a true-up for TODAY, then one back-dated 10 days, then the
        back-dated one again.  The third governs the same date as the second and
        changes nothing, so it must write nothing -- even though a LATER event
        exists.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            back_dated = date.today() - timedelta(days=10)

            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("17000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("16000.00"),
                anchor_date=back_dated,
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account, anchor_balance=Decimal("16000.00"),
                anchor_date=back_dated,
            ) is AnchorTrueUpOutcome.UNCHANGED, (
                "The re-submitted back-dated true-up changes nothing at its own "
                "date, so it must append nothing -- comparing against the LATEST "
                "event instead makes every back-dated retry a duplicate."
            )

            db.session.expire_all()
            rows = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_date=back_dated,
                    anchor_balance=Decimal("16000.00"),
                )
                .all()
            )
            assert len(rows) == 1, (
                f"Expected exactly one event at the back-dated key, found "
                f"{len(rows)} -- each surplus row renders a phantom line on the "
                f"loan dashboard's drift card and cannot be deleted."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day trueups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) MUST NOT be blocked.  Both calls
        return ``COMMITTED``; the resolver's (anchor_date, created_at) DESC
        ordering naturally picks the later one for display.
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
    """An unexpected ``IntegrityError`` propagates rather than being swallowed.

    The loan twin of the checking class above.  Its ``except IntegrityError``
    lived in ``loan_posting_service.sync_all_scenarios_or_duplicate``, which the
    loan anchor path stopped calling at plan step X-f1c4b (that helper survives
    for the ARM rate change, whose table is EDITABLE and whose unique key is a
    real business rule).  The contract it protected is graded here directly.
    """

    def test_integrity_error_from_the_resync_is_not_swallowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ``IntegrityError`` raised by the loan re-sync reaches the caller."""
        with app.app_context():
            account = _make_loan_account(seed_user)
            today = date.today()

            with patch(
                "app.services.loan_posting_service."
                "sync_loan_postings_all_scenarios",
                side_effect=IntegrityError("stmt", {}, Exception("boom")),
            ):
                with pytest.raises(IntegrityError):
                    apply_loan_anchor_true_up(
                        account=account,
                        anchor_balance=Decimal("19000.00"),
                        anchor_date=today,
                    )

            db.session.rollback()
            trueups_at_balance = (
                db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("19000.00"),
                )
                .all()
            )
            assert not trueups_at_balance, (
                "The failed true-up must not have committed: the event was "
                "staged in the transaction the IntegrityError aborted."
            )


class TestApplyAnchorTrueUpKindGate:
    """The D4 / A1 amortizing-kind guard on the CASH true-up entry point."""

    def test_refuses_amortizing_loan_before_staging(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An amortizing account raises, with NOTHING staged or written.

        Finding B-15: this entry point wrote
        ``accounts.current_anchor_balance`` for a loan -- a second,
        stored, never-reconciled loan balance.  That COLUMN is deleted (ruling
        R-EH, plan step X-f1c3c) and the sentence above is historical, naming
        what the guard was written against; a mechanical rename over this file
        had rewritten it to say the guard prevented writing
        ``cash_ledger.resolve_anchor(accounts).balance``, which is a read and
        was never what B-15 was about.

        The guard fires before ``stage_anchor_true_up``, so the session holds
        no pending mutation and no history row: the RESOLVED balance, the
        history count, and the session's dirty/new sets are all unchanged.
        """
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            loan = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Kind Gate Loan",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.commit()
            resolved_before = cash_ledger.resolve_anchor(loan).balance
            history_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            )

            with pytest.raises(
                anchor_service.AmortizingAccountAnchorError,
            ) as excinfo:
                apply_anchor_true_up(
                    account=loan,
                    new_balance=Decimal("1.00"),
                )

            # The message names the correct path for the caller.
            assert "apply_loan_anchor_true_up" in str(excinfo.value)
            # Raised BEFORE staging: session clean, nothing written.
            assert not db.session.dirty
            assert not db.session.new
            assert cash_ledger.resolve_anchor(loan).balance == resolved_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            ) == history_before
