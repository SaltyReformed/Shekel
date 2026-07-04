"""Tests for pay-period CRUD slice (a): the bounded full reset.

``reset_pay_periods`` is the highest-risk operation in the feature: it
deletes the user's ENTIRE schedule -- including the account anchor period
and every historical period, which regenerate can never touch -- and
rebuilds it from a corrected start, re-anchoring each account onto the new
schedule.  It clears the anchor FK landmine by deferring the FK to commit
(``SET CONSTRAINTS ... DEFERRED``): the old anchor period is deleted and
each account re-pointed inside ONE transaction, validated only at commit.

Bounded for safety: it refuses if the user has ANY settled transaction.

All four disciplines apply, and carry extra weight here because the
failure mode is silent balance corruption: structural invariants after
every mutation (Discipline 1), hand-computed as-of balances with the
anchor balance PRESERVED across the wipe-and-rebuild (Discipline 2), the
production integrity checker (Discipline 3), and the adversarial set --
settled refusal with the DB unchanged, the deferred-FK commit path, a
brand-new not-yet-anchored user, recurrence re-pointing, and multi-account
re-anchoring (Discipline 4).  ``today`` is pinned with ``freeze_today`` so
the anchor resolution is deterministic.  See
``docs/plans/implementation_plan_pay_period_crud.md``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.exceptions import PayPeriodResetBlocked, ValidationError
from app.enums import (
    PostingSourceEnum,
    RecurrencePatternEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.services import (
    balance_resolver,
    loan_posting_service,
    pay_period_admin,
    pay_period_service,
    pay_schedule_service,
    posting_service,
)
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    add_txn,
    assert_pay_period_invariants,
    create_loan_with_trueup,
    create_savings_account,
    freeze_today,
    make_expense_template,
    make_transfer_template,
)


# Pinned "today".  The rebuilt schedules below start 2026-06-05 at a 14-day
# cadence, so index 0 (06-05..06-18) contains today and becomes the
# resolved anchor period.
FROZEN_TODAY = date(2026, 6, 15)
_NEW_START = date(2026, 6, 5)

# A configured loan for the genesis-resync regression (review M2 / R7):
# origination $250,000, trued up to $100,000, so its genesis ledger nets
# -(100000) = opening -250000 + true-up +150000.  Both anchor dates predate the
# rebuilt schedule's 2026-06-05 start, so after reset every correction
# re-attributes to the new earliest period.
_LOAN_ORIGINATION = Decimal("250000.00")
_LOAN_ANCHOR_BALANCE = Decimal("100000.00")
_LOAN_ANCHOR_DATE = date(2026, 1, 10)
_LOAN_ORIGINATION_DATE = date(2025, 1, 1)
_LOAN_RATE = Decimal("0.06000")
_LOAN_GENESIS_NET = Decimal("-100000.00")


def _loan_genesis_entries(db_session, user_id):
    """Return the user's loan opening + true-up journal entries.

    The genesis corrections a full reset must re-post: their
    ``source_kind_id`` is ``loan_opening`` or ``loan_trueup`` (never a
    settled-transaction source), and each carries a ``pay_period_id`` that
    CASCADE-deletes with the wiped periods.
    """
    opening = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
    trueup = ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP)
    return (
        db_session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.source_kind_id.in_([opening, trueup]),
        )
        .all()
    )


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` to FROZEN_TODAY for every test in this module."""
    freeze_today(monkeypatch, FROZEN_TODAY)


def _seed_old_schedule(db_session, seed_user, count=5):
    """Append a stale schedule (indices 1..count) after the bootstrap.

    seed_user already has the 2024 bootstrap period (index 0) that its
    Checking account anchors to; these are the extra periods the reset
    will wipe alongside it.
    """
    pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()


def _all_indices(user_id):
    """The set of period_index values the user currently has."""
    return {p.period_index for p in pay_period_service.get_all_periods(user_id)}


def _make_every_n_template(db_session, seed_user, start_period, interval_n=2):
    """Build an EVERY_N_PERIODS expense template phased to ``start_period``.

    Mirrors the production form helper's offset derivation
    (``offset_periods = start_period.period_index % interval_n``) so the
    rule fires every ``interval_n`` periods aligned to ``start_period`` --
    the exact phased state a reset must re-base onto the new schedule.
    Returns the created template (flushed; the caller commits).
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_N_PERIODS,
        ),
        interval_n=interval_n,
        offset_periods=start_period.period_index % interval_n,
        start_period_id=start_period.id,
    )
    db_session.add(rule)
    db_session.flush()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Every-other Bill",
        default_amount=Decimal("300.00"),
    )
    db_session.add(template)
    db_session.flush()
    return template


class TestResetHappyPath:
    """Reset wipes everything (incl. the anchor period) and rebuilds."""

    def test_wipes_all_including_anchor_and_reanchors(self, app, db, seed_user):
        """The old anchor period is deleted; the account re-anchors anew.

        Proves the deferred-FK path end to end: the account's anchor
        period (the 2024 bootstrap) is among the deleted rows, yet the
        commit succeeds because the FK is validated only after the account
        has been re-pointed at a live new period.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            account = seed_user["account"]
            old_anchor_period_id = account.current_anchor_period_id

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # Whole schedule rebuilt from index 0; old anchor period gone.
            assert _all_indices(user_id) == {0, 1, 2, 3, 4, 5}
            assert [p.period_index for p in new_periods] == [0, 1, 2, 3, 4, 5]
            live_ids = {p.id for p in new_periods}
            assert old_anchor_period_id not in live_ids
            assert db.session.get(PayPeriod, old_anchor_period_id) is None

            # Account re-anchored to a live new period, balance preserved.
            account = db.session.get(Account, account.id)
            assert account.current_anchor_period_id in live_ids
            assert account.current_anchor_balance == Decimal("1000.00")
            # The anchor is the period containing today (index 0 here).
            assert account.current_anchor_period_id == new_periods[0].id

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_fresh_origination_history_row_written(self, app, db, seed_user):
        """The wipe clears old anchor history; reset writes one fresh row.

        The cascade deletes the old ``AccountAnchorHistory`` rows along
        with their pay periods, so after reset the account has exactly one
        history row -- the new origination, against the new anchor period.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            account = seed_user["account"]

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            rows = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .all()
            )
            assert len(rows) == 1
            assert rows[0].pay_period_id == new_periods[0].id
            assert rows[0].anchor_balance == Decimal("1000.00")
            assert rows[0].notes == "origination (pay-period reset)"

    def test_balance_preserved_and_correct_after_reset(self, app, db, seed_user):
        """Disciplines 2 + 3: anchor balance preserved, balances recompute.

        Anchor $1000 at the new index 0 (the period containing today); a
        $1200 every-period expense repopulates all six new periods,
        including the anchor period itself.  ``balance_as_of_date`` adds
        the anchor period's own net to the anchor balance, so the end
        balance at the close of index ``n`` is ``1000 - (n + 1) * 1200``.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # End of the anchor period (index 0): 1000 - 1*1200.
            assert balance_resolver.balance_as_of_date(
                account, scen, new_periods[0].end_date,
            ) == Decimal("-200.00")
            # End of index 5: 1000 - 6*1200.
            assert balance_resolver.balance_as_of_date(
                account, scen, new_periods[5].end_date,
            ) == Decimal("-6200.00")

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_repopulates_transactions_and_transfers(self, app, db, seed_user):
        """The rebuilt periods get recurring transactions AND transfers.

        The transfer path exercises the two-shadow invariant
        ``assert_pay_period_invariants`` enforces: each new period holds
        one transfer with exactly two shadows in the same period.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=seed_user["bootstrap_period"].id,
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            for period in new_periods:
                # One template expense per period.
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id, transfer_id=None,
                ).count() == 1
                # One transfer per period (with its two shadows).
                assert db.session.query(Transfer).filter_by(
                    pay_period_id=period.id,
                ).count() == 1
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_recurrence_rule_anchor_repointed(self, app, db, seed_user):
        """A rule with an explicit start period re-points to the new first.

        Before the wipe the rule anchors to an old period; the cascade
        NULLs it, and reset re-points it to the rebuilt schedule's first
        period so the rule keeps an explicit start (and that period
        classifies as a RECURRENCE_ANCHOR).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            template = make_expense_template(db.session, seed_user)
            rule = template.recurrence_rule
            rule.start_period_id = seed_user["bootstrap_period"].id
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            rule = db.session.get(RecurrenceRule, rule.id)
            assert rule.start_period_id == new_periods[0].id
            # Re-pointing also re-phases the offset to the new start (0).
            assert rule.offset_periods == 0

    def test_every_n_rule_rephased_onto_new_schedule(self, app, db, seed_user):
        """An EVERY_N_PERIODS rule re-phases to the new first period.

        The regression for the offset half of the re-point: a rule phased
        to an OLD odd-index start (offset = 1, n = 2) must, after reset,
        generate every other period STARTING at the new first period
        (indices 0, 2, 4) -- not on the stale odd phase (1, 3, 5) the old
        offset would produce.  Repopulation runs after the re-point, so the
        rebuilt rows must already carry the corrected phase.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            old_periods = pay_period_service.get_all_periods(user_id)
            # Phase the rule to an OLD odd index (3) -> offset 1 under n=2.
            template = _make_every_n_template(
                db.session, seed_user, old_periods[3], interval_n=2,
            )
            assert template.recurrence_rule.offset_periods == 1
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            rule = db.session.get(RecurrenceRule, template.recurrence_rule_id)
            assert rule.start_period_id == new_periods[0].id
            assert rule.offset_periods == 0
            # Generated rows land on indices 0, 2, 4 -- phased to the new
            # first period, not the stale 1, 3, 5.
            counts = {
                p.period_index: db.session.query(Transaction).filter_by(
                    pay_period_id=p.id, template_id=template.id,
                ).count()
                for p in new_periods
            }
            assert counts == {0: 1, 1: 0, 2: 1, 3: 0, 4: 1, 5: 0}
            assert_pay_period_invariants(db.session, user_id)

    def test_rule_without_start_period_stays_unanchored(self, app, db, seed_user):
        """A rule that had no explicit start period is not blanket-repointed.

        Only rules that carried a start period before the wipe are
        re-pointed; a NULL-start rule (the common case) must stay NULL so
        its semantics ("no explicit start") are preserved.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            template = make_expense_template(db.session, seed_user)
            rule_id = template.recurrence_rule.id
            assert template.recurrence_rule.start_period_id is None
            db.session.commit()

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            rule = db.session.get(RecurrenceRule, rule_id)
            assert rule.start_period_id is None

    def test_multiple_accounts_all_reanchored(self, app, db, seed_user):
        """Every account re-anchors with its own balance preserved.

        With two accounts, BOTH dangle after the wipe; the deferred FK is
        what lets reset re-point them both before the single commit
        validates.  Each keeps its distinct balance.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
                anchor_period_id=seed_user["bootstrap_period"].id,
            )
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            live_ids = {p.id for p in new_periods}
            checking = db.session.get(Account, checking.id)
            savings = db.session.get(Account, savings.id)
            assert checking.current_anchor_period_id in live_ids
            assert savings.current_anchor_period_id in live_ids
            assert checking.current_anchor_balance == Decimal("1000.00")
            assert savings.current_anchor_balance == Decimal("500.00")
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_not_yet_anchored_user_can_reset(self, app, db, bare_user):
        """A user with periods but no accounts resets cleanly.

        bare_user has a schedule (generated below) but no account, so there
        is nothing to re-anchor; reset must wipe and rebuild without
        touching the (empty) account set.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_service.generate_pay_periods(
                user_id, date(2026, 1, 2), num_periods=4, cadence_days=14,
            )
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            assert [p.period_index for p in new_periods] == [0, 1, 2]
            assert _all_indices(user_id) == {0, 1, 2}
            assert_pay_period_invariants(db.session, user_id)

    def test_persists_new_cadence(self, app, db, seed_user):
        """Reset stores the new cadence and builds at it."""
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                cadence_days=7,
            )
            db.session.commit()

            schedule = pay_schedule_service.get_schedule(user_id)
            assert schedule.cadence_days == 7
            assert (
                new_periods[0].end_date - new_periods[0].start_date
            ).days + 1 == 7


class TestResetRefusals:
    """The bounded reset refuses unsafe states (Discipline 4)."""

    def test_settled_transaction_blocks_and_changes_nothing(
        self, app, db, seed_user,
    ):
        """ANY settled transaction refuses the reset; the DB is unchanged.

        The gate runs before the lock, the FK deferral, and any delete, so
        a refused reset leaves the schedule, the settled row, and the
        anchor byte-for-byte intact -- never a partial wipe.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            periods = pay_period_service.get_all_periods(user_id)
            settled = add_txn(
                db.session, seed_user, periods[2], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            before_ids = {p.id for p in periods}
            account = seed_user["account"]
            before_anchor = account.current_anchor_period_id

            with pytest.raises(PayPeriodResetBlocked) as exc_info:
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=_NEW_START, num_periods=4,
                    cadence_days=14,
                )
            db.session.rollback()

            assert exc_info.value.settled_count == 1
            after_ids = {p.id for p in pay_period_service.get_all_periods(user_id)}
            assert after_ids == before_ids  # nothing deleted
            assert db.session.get(Transaction, settled.id) is not None
            account = db.session.get(Account, account.id)
            assert account.current_anchor_period_id == before_anchor
            assert_pay_period_invariants(db.session, user_id)

    def test_soft_deleted_settled_does_not_block(self, app, db, seed_user):
        """A soft-deleted settled row does not count -- matches the classifier.

        The gate mirrors the lock classifier's notion of "settled"
        (non-deleted), so a removed settled row neither locks a period nor
        blocks a reset.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            periods = pay_period_service.get_all_periods(user_id)
            add_txn(
                db.session, seed_user, periods[2], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                is_deleted=True,
            )
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                cadence_days=14,
            )
            db.session.commit()
            assert [p.period_index for p in new_periods] == [0, 1, 2]
            assert_pay_period_invariants(db.session, user_id)

    def test_invalid_cadence_rolls_back_partial_wipe(self, app, db, seed_user):
        """An invalid cadence raises after the wipe; rollback restores all.

        The wipe runs before generate validates the cadence, so the
        route's rollback (simulated here) must restore the deleted
        schedule and the account's original anchor -- nothing partial
        survives the failure.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            before_ids = {p.id for p in pay_period_service.get_all_periods(user_id)}
            account = seed_user["account"]
            before_anchor = account.current_anchor_period_id

            with pytest.raises(ValidationError):
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=_NEW_START, num_periods=4,
                    cadence_days=0,  # generate_pay_periods rejects < 1
                )
            db.session.rollback()

            after_ids = {p.id for p in pay_period_service.get_all_periods(user_id)}
            assert after_ids == before_ids
            account = db.session.get(Account, account.id)
            assert account.current_anchor_period_id == before_anchor
            assert_pay_period_invariants(db.session, user_id)


class TestResetResyncsLoanGenesis:
    """A full reset re-posts the loan genesis entries the period wipe cascades.

    A configured loan's opening / true-up postings carry a ``pay_period_id`` and
    so CASCADE-delete with the wiped periods, yet they exist independently of any
    settled transaction -- the zero-settled reset gate does NOT protect them.
    Their source facts (``LoanParams``, the ``user_trueup`` ``LoanAnchorEvent``)
    survive, so reset must re-derive and re-post them onto the rebuilt schedule
    in the same transaction (review M2 / R7).  Without the re-sync the loan's
    ledger reads empty until the next loan write and every ledger-authoritative
    loan surface degrades to the replay fallback.
    """

    def test_genesis_reposted_and_reconciles_after_reset(
        self, app, db, seed_user,
    ):
        """Genesis nets -(anchor) before AND after reset, re-attributed anew.

        Opening -250000 + true-up +150000 = -100000 == -(anchor 100000).  Before
        reset the two entries attribute to old periods; the wipe CASCADE-deletes
        them; the re-sync re-posts exactly two entries (opening + true-up), now
        attributed to the rebuilt schedule's earliest period (both anchor dates
        precede its 2026-06-05 start), and the loan-linked ledger nets -100000
        again -- it would be 0 without the fix.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            _seed_old_schedule(db.session, seed_user)
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=_LOAN_ORIGINATION,
                anchor_balance=_LOAN_ANCHOR_BALANCE,
                anchor_date=_LOAN_ANCHOR_DATE,
                rate=_LOAN_RATE,
                origination_date=_LOAN_ORIGINATION_DATE,
            )
            # Post the genesis corrections (opening + true-up) as the precondition.
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()

            old_ids = {p.id for p in pay_period_service.get_all_periods(user_id)}
            before = _loan_genesis_entries(db.session, user_id)
            assert len(before) == 2
            assert {e.pay_period_id for e in before} <= old_ids
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == _LOAN_GENESIS_NET

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # The genesis entries re-post onto the rebuilt schedule: exactly two,
            # attributed to the new earliest period (both anchor dates precede
            # it), and the loan-linked ledger reconciles to -(anchor) again.
            after = _loan_genesis_entries(db.session, user_id)
            assert len(after) == 2
            assert {e.pay_period_id for e in after} == {new_periods[0].id}
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == _LOAN_GENESIS_NET
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestResetResyncsAccountOpenings:
    """A full reset re-posts the non-loan account openings the wipe cascades.

    The Step-5 analogue of the loan re-sync above (plan Section 3.3, point
    4): a non-loan account's opening correction carries a ``pay_period_id``
    and CASCADE-deletes with the wiped periods -- along with its
    ``AccountAnchorHistory`` rows -- yet ``_reanchor_accounts`` stages one
    fresh origination row per account, so the per-user account re-sync must
    re-derive each opening onto the rebuilt schedule in the same
    transaction.  Without it every non-loan ledger reads empty (the
    absolute invariant silently degrades to changes-only) until the
    account's next anchor event.
    """

    def test_openings_reposted_and_reconcile_after_reset(
        self, app, db, seed_user,
    ):
        """The Checking opening nets +1000 before AND after, re-attributed anew.

        The seed Checking's $1000.00 opening posts at fixture time.  The
        wipe disposes it with the old periods; the re-sync re-posts exactly
        one opening entry attributed to the re-anchored period (the rebuilt
        schedule's resolved anchor), and the linked ledger nets 1000.00
        again -- it would be 0 without the fix.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking_id = seed_user["account"].id
            _seed_old_schedule(db.session, seed_user)
            assert posting_service.account_posting_total(
                checking_id, scenario_id,
            ) == Decimal("1000.00")

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # Re-fetch: the reset's identity-map wipe detached the fixture
            # object.
            checking = db.session.get(Account, checking_id)
            openings = (
                db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == user_id,
                    JournalEntry.source_kind_id == ref_cache.posting_source_id(
                        PostingSourceEnum.ACCOUNT_OPENING,
                    ),
                )
                .all()
            )
            assert len(openings) == 1
            assert openings[0].pay_period_id == checking.current_anchor_period_id
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))
