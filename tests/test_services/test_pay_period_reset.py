"""Tests for pay-period CRUD slice (a): the bounded full reset.

``reset_pay_periods`` is the highest-risk operation in the feature: it
deletes the user's ENTIRE schedule -- including every historical period,
which regenerate can never touch -- and rebuilds it from a corrected start.

**It no longer re-anchors anything, and the FK it used to defer is gone**
(rulings R-EH and R-EO, plan step X-f1c3c).  This docstring described the
old machinery: the account carried a ``current_anchor_period_id`` and the
assertion a ``pay_period_id``, so the reset had to delete the anchor period
and re-point every account inside ONE transaction with the FK deferred to
commit (``SET CONSTRAINTS ... DEFERRED``).  Both columns are deleted.  A
balance assertion is now untouchable by a schedule operation, so the reset
deletes periods, rebuilds, and re-derives the postings -- there is no window
in which the schema is inconsistent and nothing to defer.

Bounded for safety: it refuses if the user has ANY settled transaction.

**It does NOT repopulate the rebuilt schedule; the ROUTE does, and that is
ruling R-R38** (plan step R7d-c-1).  The door wipes, rebuilds and re-syncs
both posting families, then returns EMPTY periods; the caller opens the
generate pass and fills them, which is why the populate now runs AFTER both
re-syncs rather than between the rebuild and them.  That order is safe because
every read either re-sync makes of ``budget.transactions`` or
``budget.transfers`` is keyed on a set of ids taken from the POSTED ledger, and
a freshly generated row is ``Projected`` and posts nothing.  A case here that
asserts recurring ROWS runs ``_reset_and_populate``; ``POST
/pay-periods/reset`` is graded in
``tests/test_routes/test_pay_period_admin.py``.

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

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.exceptions import PayPeriodResetBlocked, ValidationError
from app.enums import (
    PostingSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.routes._period_population import populate_new_periods
from app.services.pay_calendar import calendar_for
from app.services.recurrence import reauthor_rule, recurrence_spec, resolve
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    loan_posting_service,
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
    posting_service,
)
from scripts.integrity_check import (
    check_balance_anomalies,
    check_referential_integrity,
)
from tests._test_helpers import (
    add_txn,
    all_periods,
    assert_pay_period_invariants,
    create_loan_with_trueup,
    create_savings_account,
    derived_span,
    freeze_today,
    last_covered_day,
    make_cadence_rule,
    make_expense_template,
    make_transfer_template,
    seam_cash_balance_at,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
)
from app.services import cash_ledger


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
    pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=count,
        cadence_days=14,
    )
    db_session.commit()


def _all_indices(user_id):
    """The set of period_index values the user currently has."""
    return {derived_span(p).period_index for p in all_periods(user_id)}


def _make_every_n_template(db_session, seed_user, start_period, interval_n=2):
    """Build an EVERY_N_PERIODS expense template phased to ``start_period``.

    States the FIRST OCCURRENCE -- ``start_period``'s own payday -- and lets
    the write door derive the phase from it (plan step R7b-4), which is the
    exact phased state a reset must re-base onto the new schedule.  The
    ``offset_periods`` column comes out of that derivation against the OLD
    schedule, so it really is the stale value a pre-reset rule holds, and the
    point of the case is what happens to it.

    **Authored through the write door since plan step R7c-b** rather than
    written column by column: the two-axis columns are ``NOT NULL``, and the
    phase is now derived on every read rather than trusted off the column, so
    hand-writing a phase would state a fact nothing consults.

    Returns the created template (flushed; the caller commits).
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Every-other Bill",
        default_amount=Decimal("300.00"),
    )
    db_session.add(template)
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_cadence_rule(
        template,
        EVERY_N_PERIODS,
        starts_on=start_period.start_date,
        interval_n=interval_n,
    )
    return template


def _reset_and_populate(user_id, **kwargs):
    """Run BOTH halves of a reset, exactly as the route does.

    ``reset_pay_periods`` wipes, rebuilds and re-syncs both posting families;
    ``populate_new_periods`` opens the generate pass afterwards and fills the
    rebuilt schedule.  Ruling **R-R38**: the pass may only be opened above the
    service layer, and only after the write.

    Args:
        user_id: The owning user's id.
        **kwargs: Forwarded to
            :func:`~app.services.pay_period_admin.reset_pay_periods`.

    Returns:
        The rebuilt periods, now populated.
    """
    new_periods = pay_period_admin.reset_pay_periods(user_id, **kwargs)
    populate_new_periods(user_id, new_periods)
    return new_periods


class TestResetHappyPath:
    """Reset wipes everything (incl. the anchor period) and rebuilds."""

    def test_wipes_all_and_the_balance_survives(self, app, db, seed_user):
        """Every old period is deleted and the asserted balance is untouched.

        It proved the DEFERRED-FK path end to end: the account's anchor period
        was among the deleted rows, and the commit succeeded only because the
        FK was validated after the account had been re-pointed.  Rulings R-EH
        and R-EO deleted both the account's anchor column and the assertion's
        pay period, so there is no FK to defer, nothing to re-point, and no
        window in which the schema is inconsistent.  What is worth proving is
        what the deferral existed to protect: the user's balance comes through
        a schedule rebuild unchanged.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            account = seed_user["account"]
            old_period_ids = {
                p.id for p in all_periods(user_id)
            }
            balance_before = cash_ledger.resolve_anchor(account).balance

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # Whole schedule rebuilt from index 0; every old period gone.
            assert _all_indices(user_id) == {0, 1, 2, 3, 4, 5}
            assert [derived_span(p).period_index for p in new_periods] == [0, 1, 2, 3, 4, 5]
            for old_id in old_period_ids:
                assert db.session.get(PayPeriod, old_id) is None

            # The asserted balance is untouched by the rebuild.
            account = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(account).balance == balance_before
            assert balance_before == Decimal("1000.00")

            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))

    def test_the_reset_preserves_every_balance_assertion(
        self, app, db, seed_user,
    ):
        """A schedule rebuild does not touch what the user said their bank held.

        **This test asserted the OPPOSITE until ruling R-EO** (plan step
        X-f1c3b), and the inversion is the finding.  It read: "the cascade
        deletes the old ``AccountAnchorHistory`` rows along with their pay
        periods, so after reset the account has exactly one history row -- the
        new origination".  That was a true description of a defect.  A balance
        assertion is a fact about a BANK -- "on day D this account held $B" --
        and it stays true however the user re-schedules their paychecks;
        ``account_anchor_history.pay_period_id`` filed it under a budgeting
        artifact on an ``ON DELETE CASCADE`` FK, so a reset destroyed it.
        Measured on the developer's production data before the column was
        dropped: **all 78 assertions deleted, 9 fabricated
        ``"origination (pay-period reset)"`` rows written in their place.**

        The row-for-row comparison is what makes this falsifiable: asserting
        only a COUNT would pass against a reset that deleted every real
        assertion and wrote the same number of synthetic ones, which is very
        nearly what the old behaviour did.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            account = seed_user["account"]

            def _assertions():
                return sorted(
                    (row.id, row.anchor_balance, row.observed_on)
                    for row in db.session.query(AccountAnchorHistory)
                    .filter_by(account_id=account.id)
                )

            before = _assertions()
            assert before, "fixture must write at least one assertion"

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            after = _assertions()
            # **The named diagnostic MOVED ONTO this assertion at plan step
            # X-f1e2**, and nothing is ungraded by the move.  A second line
            # used to scan the post-reset snapshot for a row whose ``notes``
            # read ``"origination (pay-period reset)"`` -- the exact string the
            # old behaviour fabricated -- purely so a failure would NAME the
            # defect; its own comment recorded that it was a diagnostic and not
            # an independent grader, because the row-for-row equality here
            # already fails for any fabricated row and fails first.  Ruling
            # R-ES deleted the ``notes`` column, so the string cannot be
            # searched for; the message it carried is stated here instead.
            # What is lost is the ability to distinguish "fabricated by the
            # reset" from "fabricated by something else", which no assertion
            # in this test ever made use of.
            assert after == before, (
                "the reset changed this account's assertion history.  Measured "
                "on production before ruling R-EO: a reset deleted all 78 "
                "assertions and wrote 9 fabricated replacements, because the "
                "row was filed under a pay period on a CASCADE FK.  A balance "
                "assertion is a fact about a BANK and survives any schedule "
                f"rebuild.\nbefore={before}\nafter ={after}"
            )

    def test_the_door_leaves_the_rebuilt_schedule_EMPTY(
        self, app, db, seed_user,
    ):
        """The door wipes, rebuilds, re-syncs -- and generates nothing.

        The door's half of ruling **R-R38**, over BOTH engines: the fixture
        seeds a transaction template AND a transfer template, because a door
        that regressed to running only one of the two would otherwise pass.
        ``test_repopulates_transactions_and_transfers`` runs both halves over
        this same fixture and finds a row and a transfer in every period, so
        this case cannot pass by the templates being unable to generate.

        *The transaction assertion carried a ``transfer_id=None`` filter until
        an adversarial review of this step: copied from the positive sibling,
        where the fixture makes it load-bearing, and inert here in a way that
        narrowed what the negative case could see.*
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            for period in new_periods:
                assert db.session.query(Transaction).filter_by(
                    pay_period_id=period.id,
                ).count() == 0, (
                    "reset_pay_periods generated a recurring row; since R-R38 "
                    "it rebuilds the schedule and the caller populates it"
                )
                assert db.session.query(Transfer).filter_by(
                    pay_period_id=period.id,
                ).count() == 0, (
                    "reset_pay_periods generated a recurring TRANSFER; the "
                    "transfer engine is the other half of the repopulation "
                    "and the door must run neither"
                )

    def test_balance_preserved_and_correct_after_reset(self, app, db, seed_user):
        """Disciplines 2 + 3: anchor balance preserved, balances recompute.

        Anchor $1000 at the new index 0 (the period containing today); a
        $1200 every-period expense repopulates all six new periods,
        including the anchor period itself.  A balance read at the CLOSE of a
        period counts that period's own net, so the end balance at the close of
        index ``n`` is ``1000 - (n + 1) * 1200``.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            _seed_old_schedule(db.session, seed_user)
            make_expense_template(db.session, seed_user, amount="1200.00")
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # End of the anchor period (index 0): 1000 - 1*1200.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(new_periods[0]),
            ) == Decimal("-200.00")
            # End of index 5: 1000 - 6*1200.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(new_periods[5]),
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
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = _reset_and_populate(
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

    def test_a_rules_stated_start_survives_the_wipe(self, app, db, seed_user):
        """The reset does not touch the rule, and its bound is still there.

        The wipe used to SET NULL every rule's ``start_period_id`` -- the FK
        is ``ON DELETE SET NULL`` and reset deletes every period -- so the
        reset had to capture the anchored rules first and re-point them at the
        rebuilt schedule's first period afterwards, or a rule with an explicit
        start silently became one with none.

        Plan step R7b-4 made the bound a DATE, which the cascade cannot reach.
        There is nothing to capture, nothing to re-point, and the stated start
        is still stated when the reset returns -- so the rule opens where the
        user said, measured against whatever schedule now exists.

        **Asserted on ``starts_on``, which is where the fact lives since plan
        step R7c-b.**  This read ``start_date`` and set it directly, and both
        halves rotted with that step: the write door stopped writing that
        column and ``start_period_id`` entirely, so BOTH assertions read
        ``None``-forever values and held for any reset -- including one that
        rewrote the rule from end to end, which is the whole state they exist
        to refuse.  The rule is authored through the door here for the same
        reason: a hand-set column is not a bound the application would ever
        produce.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            # A date inside the OLD schedule and a year before the rebuilt
            # one, so a reset that re-pointed anything could not leave it here.
            stated_start = date(2026, 1, 30)
            template = make_expense_template(db.session, seed_user)
            # The shared factory gives the template an every-paycheck rule
            # opening at the schedule's own start; this case needs one opening
            # INSIDE the old schedule, so the rule it carries is re-pointed
            # rather than a second one authored beside it -- which
            # ``uq_recurrence_rules_transaction_template_id`` refuses since
            # plan step R-F6, and refuses for the reason this line relies on:
            # a definition has ONE cadence.
            rule = template.recurrence_rule
            reauthor_rule(
                rule,
                replace(recurrence_spec(rule), starts_on=stated_start),
                calendar_for(user_id),
            )
            db.session.commit()
            assert rule.starts_on == stated_start
            assert stated_start < _NEW_START

            pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            rule = db.session.get(RecurrenceRule, rule.id)
            assert rule.starts_on == stated_start

    def test_every_n_rule_rephased_onto_new_schedule(self, app, db, seed_user):
        """An EVERY_N_PERIODS rule re-phases onto the rebuilt schedule.

        The regression the re-point half existed for: a rule phased to an OLD
        odd index must, after a reset, generate every other period STARTING at
        the new schedule's opening (indices 0, 2, 4) -- not on the stale odd
        phase (1, 3, 5).

        **Nothing re-points it now** (plan step R7b-4).  The rule keeps its
        stated bound, which precedes the rebuilt schedule, so the effective
        start is ``max(new opening payday, start_date)`` -- the new opening,
        index 0 -- and the phase is that paycheck's ordinal modulo 2, which is
        0.  The generated rows are the same ones the re-point produced,
        without a writer that had to remember to produce them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _seed_old_schedule(db.session, seed_user)
            old_periods = all_periods(user_id)
            # Phase the rule to an OLD odd index (3) -> offset 1 under n=2.
            template = _make_every_n_template(
                db.session, seed_user, old_periods[3], interval_n=2,
            )
            rule = template.recurrence_rule
            assert resolve(
                recurrence_spec(rule), calendar_for(rule.user_id),
            ).offset_periods == 1
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=6,
                cadence_days=14,
            )
            db.session.commit()

            # Generated rows land on indices 0, 2, 4 -- phased to the new
            # first period, not the stale 1, 3, 5.
            counts = {
                derived_span(p).period_index: db.session.query(Transaction).filter_by(
                    pay_period_id=p.id, template_id=template.id,
                ).count()
                for p in new_periods
            }
            assert counts == {0: 1, 1: 0, 2: 1, 3: 0, 4: 1, 5: 0}
            assert_pay_period_invariants(db.session, user_id)

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
            )
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            checking = db.session.get(Account, checking.id)
            savings = db.session.get(Account, savings.id)
            assert cash_ledger.resolve_anchor(checking).balance == Decimal("1000.00")
            assert cash_ledger.resolve_anchor(savings).balance == Decimal("500.00")
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
            pay_period_write.record_paydays(
                user_id, date(2026, 1, 2), num_periods=4, cadence_days=14,
            )
            db.session.commit()

            new_periods = pay_period_admin.reset_pay_periods(
                user_id, new_start_date=_NEW_START, num_periods=3,
                cadence_days=14,
            )
            db.session.commit()

            assert [derived_span(p).period_index for p in new_periods] == [0, 1, 2]
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
                last_covered_day(new_periods[0]) - new_periods[0].start_date
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
            periods = all_periods(user_id)
            settled = add_txn(
                db.session, seed_user, periods[2], "Paycheck", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            before_ids = {p.id for p in periods}
            account = seed_user["account"]

            with pytest.raises(PayPeriodResetBlocked) as exc_info:
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=_NEW_START, num_periods=4,
                    cadence_days=14,
                )
            db.session.rollback()

            assert exc_info.value.settled_count == 1
            after_ids = {p.id for p in all_periods(user_id)}
            assert after_ids == before_ids  # nothing deleted
            assert db.session.get(Transaction, settled.id) is not None
            account = db.session.get(Account, account.id)
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
            periods = all_periods(user_id)
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
            assert [derived_span(p).period_index for p in new_periods] == [0, 1, 2]
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
            before_ids = {p.id for p in all_periods(user_id)}
            account = seed_user["account"]

            with pytest.raises(ValidationError):
                pay_period_admin.reset_pay_periods(
                    user_id, new_start_date=_NEW_START, num_periods=4,
                    cadence_days=0,  # generate_pay_periods rejects < 1
                )
            db.session.rollback()

            after_ids = {p.id for p in all_periods(user_id)}
            assert after_ids == before_ids
            account = db.session.get(Account, account.id)
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

            old_ids = {p.id for p in all_periods(user_id)}
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

    def test_the_repopulation_cannot_invalidate_either_resync(
        self, app, db, seed_user,
    ):
        """The re-syncs are still at their FIXPOINT after the repopulation.

        **This is what makes ruling R-R38's reordering safe, graded rather
        than argued.**  The repopulation used to run BETWEEN the rebuild and
        the two posting re-syncs; the door now returns before it, so it runs
        AFTER both.  If a freshly generated row could move what either re-sync
        derives, re-running them at this point would rewrite something.

        The argument the code makes is that neither can see such a row: every
        read either side makes of ``budget.transactions`` or
        ``budget.transfers`` is keyed on ids taken from the POSTED ledger --
        the linked ledger's nonzero per-row nets on the account side, the stale
        lineage transfers and stale payment shadows on the loan side, and the
        loan walk's own ``settled_income_shadows`` -- and a ``Projected`` row
        posts nothing, so it is in none of them.  **A first draft of this
        docstring said the account side reads no transaction or transfer at
        all, and an adversarial review MEASURED that false**; the conclusion
        survived and the roll-call is what was wrong, which is why the property
        is stated instead.  This case is the measurement of it: an owner
        holding a loan AND both kinds of active template, reset and
        repopulated, whose re-syncs then write nothing.

        The reconcile is self-healing, so a re-run that DID change something
        would change it here -- which is exactly what the assertions read.
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
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            make_expense_template(db.session, seed_user, amount="1200.00")
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            new_periods = _reset_and_populate(
                user_id, new_start_date=_NEW_START, num_periods=4,
                cadence_days=14,
            )
            db.session.commit()

            # The repopulation really ran: without rows in the new periods
            # there is nothing that could have disturbed a re-sync, and this
            # case would pass for the wrong reason.
            generated = db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_([p.id for p in new_periods]),
                Transaction.template_id.isnot(None),
            ).count()
            assert generated == len(new_periods), (
                f"the repopulation wrote {generated} template rows into "
                f"{len(new_periods)} periods; with none there is nothing for "
                f"a re-sync to have been disturbed by"
            )

            def _fingerprint():
                """Every genesis entry as (source, period, its posting pairs)."""
                return sorted(
                    (
                        entry.source_kind_id, entry.pay_period_id,
                        tuple(sorted(
                            (posting.ledger_account_id, posting.amount)
                            for posting in entry.postings
                        )),
                    )
                    for entry in _loan_genesis_entries(db.session, user_id)
                )

            before = _fingerprint()
            net_before = posting_service.account_posting_total(
                loan.id, scenario_id,
            )
            assert before, "no genesis entry was posted, so nothing is graded"
            assert net_before == _LOAN_GENESIS_NET

            # Re-run BOTH re-syncs at the post-repopulation state.  Each is
            # reconcile-to-target and self-healing, so anything the
            # repopulation had invalidated is rewritten here.
            loan_posting_service.resync_user_loan_postings(user_id)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.flush()

            assert _fingerprint() == before, (
                "re-running the re-syncs after the repopulation rewrote a "
                "genesis entry, so the order R-R38 puts them in is NOT "
                "order-independent"
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == net_before
            assert all(r.passed for r in check_referential_integrity(db.session))


class TestResetResyncsAccountOpenings:
    """A full reset re-posts the non-loan account openings the wipe cascades.

    The Step-5 analogue of the loan re-sync above (plan Section 3.3, point
    4): a non-loan account's opening correction carries a ``pay_period_id``
    and CASCADE-deletes with the wiped periods, so the per-user account
    re-sync must re-derive each opening onto the rebuilt schedule in the same
    transaction.  Without it every non-loan ledger reads empty (the
    absolute invariant silently degrades to changes-only) until the
    account's next anchor event.

    **What it re-derives FROM changed at ruling R-EO** (plan step X-f1c3c).
    The wipe used to take the account's ``AccountAnchorHistory`` rows with it
    -- they carried a ``pay_period_id`` on a CASCADE FK -- and a
    ``_reanchor_accounts`` pass staged one fabricated origination row per
    account for this re-sync to read.  The assertion carries no period now, so
    the wipe cannot reach it and the re-sync reads the observations that were
    always there.  ``_reanchor_accounts`` is deleted; this docstring named it
    until X-f1c3c.
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
            # The opening correction books in the period CONTAINING the
            # assertion's own day (ruling R-EA: the period is DERIVED from the
            # day, never read beside it).  The containing period is resolved
            # HERE from the dates, not by calling the resolver the writer
            # calls, so the two cannot agree by sharing one implementation.
            #
            # A first version of this assertion only checked membership in the
            # set of all live periods -- true of any of the six, so it would
            # have passed against a correction filed in the wrong one.
            rebuilt = all_periods(user_id)
            opening_assertion = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=checking_id)
                .order_by(AccountAnchorHistory.observed_on.asc())
                .first()
            )
            # The fixture's Checking is asserted on 2024-01-05 and the rebuilt
            # schedule starts 2026-06-05, so the assertion's day precedes EVERY
            # period.  ``pay_calendar.PayCalendar.filing_period`` clamps such a
            # correction into the user's EARLIEST period -- index 0 -- so the
            # reader, which bounds by period start, counts it from the first
            # period on.
            assert all(
                opening_assertion.observed_on < p.start_date for p in rebuilt
            ), (
                f"this test's expected period is the no-containing-period "
                f"branch; the assertion ({opening_assertion.observed_on}) must "
                f"precede every rebuilt period"
            )
            earliest = min(rebuilt, key=lambda p: derived_span(p).period_index)
            assert derived_span(earliest).period_index == 0
            assert openings[0].pay_period_id == earliest.id
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert_pay_period_invariants(db.session, user_id)
            assert all(r.passed for r in check_referential_integrity(db.session))
