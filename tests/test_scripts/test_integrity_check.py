"""Tests for scripts/integrity_check.py (Phase 8C WU-4)."""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import Status, TransactionType
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User
from app.enums import StatusEnum
from app.services.auth_service import hash_password
from app.services import account_service
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    account_never_asserted,
    open_owner_calendar,
    settle_day_columns,
    settlement_columns,
)
from tests._test_helpers import (
    add_txn,
    make_every_period_rule,
    open_books_before_the_first_assertion,
)
from scripts.integrity_check import (
    CheckResult,
    check_balance_anomalies,
    check_data_consistency,
    check_orphaned_records,
    check_referential_integrity,
    run_all_checks,
)
from app.models.amount_ownership import AmountOwnership


# ── CheckResult dataclass ────────────────────────────────────────


class TestCheckResult:
    """Tests for the CheckResult dataclass."""

    def test_passing_check(self):
        """A passing CheckResult has passed=True and detail_count=0."""
        result = CheckResult(
            check_id="TEST-01",
            category="test",
            severity="critical",
            description="test check",
            passed=True,
            detail_count=0,
        )
        assert result.passed is True
        assert result.detail_count == 0
        assert result.details == []

    def test_failing_check(self):
        """A failing CheckResult has passed=False and detail_count > 0."""
        result = CheckResult(
            check_id="TEST-02",
            category="test",
            severity="warning",
            description="test check",
            passed=False,
            detail_count=3,
            details=[{"id": 1}, {"id": 2}, {"id": 3}],
        )
        assert result.passed is False
        assert result.detail_count == 3
        assert len(result.details) == 3


# ── Referential Integrity ────────────────────────────────────────


class TestReferentialIntegrity:
    """Tests for FK-* referential integrity checks."""

    def test_clean_database_passes_all(self, app, db, seed_user, seed_periods):
        """All FK checks pass on a properly seeded database."""
        results = check_referential_integrity(db.session)
        assert all(r.passed for r in results), (
            f"Failed checks: {[r.check_id for r in results if not r.passed]}"
        )
        # 12 since plan step X-f1c3c: FK-03 ("accounts pointing to a
        # nonexistent anchor period") went with the column it queried.
        assert len(results) == 12

    def test_fk01_detects_orphaned_account(self, app, db, seed_user):
        """FK-01 detects an account whose user_id references a nonexistent user."""
        # Insert an account with a bogus user_id via raw SQL to bypass FK.
        # It needed anchor columns pointed at a real period until plan step
        # X-f1c3c (ruling R-EH) deleted them; an account row is now just its
        # owner, type and name, and the orphan under test is the user_id.
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text("""
            INSERT INTO budget.accounts (user_id, account_type_id, name)
            VALUES (99999, 1, 'Orphaned Account')
        """))
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk01 = next(r for r in results if r.check_id == "FK-01")
        assert not fk01.passed
        assert fk01.detail_count == 1  # 1 orphaned account inserted

        # Restore FK enforcement.
        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk05_detects_transaction_with_missing_period(
        self, app, db, seed_user, seed_periods
    ):
        """FK-05 detects a transaction referencing a nonexistent pay period."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        status = db.session.query(Status).filter_by(name="Projected").one()
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        db.session.execute(db.text("""
            INSERT INTO budget.transactions
                (pay_period_id, user_id, scenario_id, account_id, status_id,
                 name, transaction_type_id, estimated_amount)
            VALUES (99999, :uid, :sid, :aid, :stid, 'Ghost Txn', :ttid, 50.00)
        """), {
            # ``session_replication_role = 'replica'`` suppresses referential
            # TRIGGERS, which is what lets ``pay_period_id`` dangle -- it does
            # not suppress NOT NULL, so the owner plan step
            # ``pay_calendar:C13-a`` added is stated like every other column
            # here.  It is the seeded owner's: the row is forged in its PARENT
            # POINTER, which is what FK-05 is about, and nowhere else.
            "uid": seed_user["user"].id,
            "sid": seed_user["scenario"].id,
            "aid": seed_user["account"].id,
            "stid": status.id,
            "ttid": txn_type.id,
        })
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk05 = next(r for r in results if r.check_id == "FK-05")
        assert not fk05.passed
        assert fk05.detail_count == 1  # 1 transaction with missing period

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk10_detects_template_with_missing_category(self, app, db, seed_user):
        """FK-10 detects a transaction template with an invalid category_id."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        db.session.execute(db.text("""
            INSERT INTO budget.transaction_templates
                (user_id, account_id, category_id, transaction_type_id,
                 name, default_amount)
            VALUES (:uid, :aid, 99999, :ttid, 'Bad Template', 25.00)
        """), {
            "uid": seed_user["user"].id,
            "aid": seed_user["account"].id,
            "ttid": txn_type.id,
        })
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk10 = next(r for r in results if r.check_id == "FK-10")
        assert not fk10.passed
        assert fk10.detail_count == 1  # 1 template with missing category

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk02_detects_account_with_invalid_type(self, app, db, seed_user):
        """FK-02: Accounts with invalid account_type_id."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text("""
            INSERT INTO budget.accounts (user_id, account_type_id, name)
            VALUES (:uid, 99999, 'Bad Type Account')
        """), {"uid": seed_user["user"].id})
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk02 = next(r for r in results if r.check_id == "FK-02")
        assert not fk02.passed
        assert fk02.detail_count == 1
        # Verify the detail identifies the offending row.
        assert fk02.details[0]["name"] == "Bad Type Account"
        assert fk02.details[0]["account_type_id"] == 99999

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))


# ── Orphan Detection ─────────────────────────────────────────────


class TestOrphanDetection:
    """Tests for OR-* orphan detection checks."""

    def test_clean_database_no_orphans(self, app, db, seed_user, seed_periods):
        """No orphans detected on a properly seeded database.

        Note: OR-03 (unused categories) and OR-04 (empty pay periods) will
        flag results on a minimal seed because categories have no templates
        and periods have no transactions. These are warnings, not errors.
        We verify the check runs without crashing; specific orphan detection
        is tested in dedicated methods below.
        """
        results = check_orphaned_records(db.session)
        # 5: OR-02 retired at plan step R-F6, when the owning FK moved onto
        # ``budget.recurrence_rules`` and made a rule with no definition a row
        # the database refuses rather than one a scan finds.
        assert len(results) == 5
        # All should return CheckResult objects regardless of pass/fail.
        assert all(isinstance(r, CheckResult) for r in results)

    def test_or02_is_retired_because_its_state_is_inexpressible(self, app, db):
        """OR-02 is GONE, and the schema is what replaced it (plan step R-F6).

        It scanned for recurrence rules no template referenced -- finding
        **F-6**, three rows on production -- and that state stopped being one
        the database will hold when the owning FK moved onto
        ``budget.recurrence_rules``: ``ck_recurrence_rules_one_owner`` refuses
        a rule with no definition, and ``ON DELETE CASCADE`` takes the rule
        when the definition goes.  A checker for a state the schema forbids
        reports on the constraint's behalf and can only ever say zero.

        Asserted as an ABSENCE rather than deleted silently: this is the arm
        that fails if a later edit reinstates the check, and the id is left
        retired rather than reused so an old report's OR-02 still means what
        it meant.
        """
        ids = {r.check_id for r in check_orphaned_records(db.session)}
        assert "OR-02" not in ids, (
            "OR-02 is retired -- its state is refused by "
            "ck_recurrence_rules_one_owner, so a check for it can only pass"
        )

    def test_or03_detects_unused_category(self, app, db, seed_user):
        """OR-03 detects a category not used by any template or transaction."""
        # The seed_user fixture creates categories that are unused by default.
        results = check_orphaned_records(db.session)
        or03 = next(r for r in results if r.check_id == "OR-03")
        # Seed categories are not referenced by any templates or transactions.
        assert not or03.passed
        # seed_user creates 5 categories (Salary, Rent, Car Payment, Groceries, Payback)
        # none referenced by any template or transaction
        assert or03.detail_count == 5

    def test_or01_detects_orphaned_template(self, app, db, seed_user):
        """OR-01: Template with no recurrence rule and no transactions."""
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        category = list(seed_user["categories"].values())[0]

        # Create a template with no recurrence_rule_id and no transactions.
        orphan_template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="Orphaned Template",
            default_amount=Decimal("50.00"),
        )
        db.session.add(orphan_template)
        db.session.flush()
        # NO cadence and no transactions, which is the state OR-01 reports --
        # expressed as the ABSENCE of a rule row since plan step R-F6, where
        # it used to be a NULL ``recurrence_rule_id`` on this template.

        results = check_orphaned_records(db.session)
        or01 = next(r for r in results if r.check_id == "OR-01")
        assert not or01.passed
        assert or01.detail_count == 1
        assert or01.details[0]["name"] == "Orphaned Template"

    def test_or06_detects_goal_on_inactive_account(self, app, db, seed_user):
        """OR-06 flags a savings goal on an inactive account."""
        account = seed_user["account"]
        account.is_active = False
        db.session.flush()

        goal = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=account.id,
            name="Bad Goal",
            target_amount=Decimal("1000.00"),
            is_active=True,
        )
        db.session.add(goal)
        db.session.flush()

        results = check_orphaned_records(db.session)
        or06 = next(r for r in results if r.check_id == "OR-06")
        assert not or06.passed
        assert or06.detail_count == 1  # 1 goal on inactive account


# ── Balance Anomalies ────────────────────────────────────────────


class TestBalanceAnomalies:
    """Tests for BA-* balance anomaly checks."""

    def test_clean_database_no_anomalies(self, app, db, seed_user, seed_periods):
        """No balance anomalies on a properly seeded database."""
        results = check_balance_anomalies(db.session)
        # 3: BA-02 ("anchor period beyond the user's last period") went with
        # the column it queried at plan step X-f1c3c and BA-01 was RE-POINTED
        # at "an account with no balance assertion at all" -- the state that
        # actually breaks a producer -- leaving 4; BA-06 was added 2026-08-11
        # with the deletion of pay_calendar C3-b's coverage rule, and BA-07
        # with pay_calendar C2-b2.  Plan step pay_calendar:C4-c then took
        # BA-03 (an ordinal gap), BA-04 (a span overlap) and BA-07 (a day
        # covered by no period) together: all three query columns that step
        # DROPPED, and all three name states the derived calendar cannot
        # express.
        assert len(results) == 3
        ba01 = next(r for r in results if r.check_id == "BA-01")
        assert ba01.passed

    def test_ba01_detects_account_with_no_assertion(
        self, app, db, seed_user, seed_periods,
    ):
        """BA-01 detects an account carrying no balance assertion at all.

        **The check was RE-POINTED at plan step X-f1c3c and became
        exercisable again.**  It used to look for "balance set but no anchor
        period (or vice versa)", a state the storage tier made unreachable --
        so its detection test was deleted as unexercisable and the check
        survived as raw-SQL defence only.  What it asks now is the invariant
        those columns existed to serve: every account carries at least one
        ``account_anchor_history`` row (E-19 / Commit 3), because an account
        the resolver cannot answer for breaks every producer downstream.

        **The firing control BUILDS such an account rather than emptying one**
        (plan step X-f3c-2c).  It used to delete the seeded account's
        assertions with one raw statement; ``budget.account_anchor_history`` is
        append-only at the database tier now, so no statement does that.  What
        remains reachable -- and what this check exists for -- is an ``Account``
        row that never went through the factory, which is exactly the shape a
        restore, a hand-written script or a future writer would leave behind.
        """
        account = account_never_asserted(
            seed_user, db.session, name="Unanswerable",
            opening_equity=Decimal("0.00"),
        )
        account_id = account.id
        db.session.flush()

        results = check_balance_anomalies(db.session)
        ba01 = next(r for r in results if r.check_id == "BA-01")
        assert not ba01.passed
        assert ba01.detail_count == 1
        assert ba01.details[0]["id"] == account_id
        # The SEVERITY is the operationally load-bearing half, and the
        # re-pointing nearly lost it: this family used to stamp one
        # ``"warning"`` across every member, so an account the resolver RAISES
        # for would have exited 2 rather than 1 and ``verify_backup.sh`` would
        # have logged a broken restore as a WARNING.
        assert ba01.severity == "critical", (
            "an account with no assertion makes resolve_anchor raise on every "
            "page; it must fail the sweep, not warn"
        )

    def test_the_other_balance_checks_stay_warnings(
        self, app, db, seed_user, seed_periods,
    ):
        """BA-05 and BA-06 are warnings; only BA-01 escalated.

        The complement of the assertion above, so "severity is per check"
        is graded in both directions: an edit that promoted the whole family
        to critical would pass the BA-01 test alone.

        **BA-06 belongs on this side deliberately.**  A settled row whose cash
        day no paycheck covers writes no wrong figure -- each column is valued
        at its own period end, so the day cancels on both sides of ruling
        R-K's identity and reports as ``period_timing``.  Promoting it would
        exit the backup sweep 2 over a state the developer ruled ACCEPTABLE on
        2026-08-11 when deleting the writer refusal that used to forbid it.
        """
        results = check_balance_anomalies(db.session)
        by_id = {r.check_id: r.severity for r in results}
        assert by_id["BA-01"] == "critical"
        assert by_id["BA-05"] == "warning"
        assert by_id["BA-06"] == "warning"

    def _settled_txn(self, seed_user, period, settled_on):
        """File a SETTLED transaction in *period* whose cash moved on a day."""
        return add_txn(
            db.session, seed_user, period, "Gas", "59.04",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=settled_on,
        )

    def test_ba06_detects_a_settle_day_no_paycheck_covers(
        self, app, db, seed_user, seed_periods,
    ):
        """BA-06 fires on the state C3-b's deleted coverage rule used to refuse.

        The last seeded paycheck ends 2026-05-21.  A row filed in it that the
        bank took on 2026-06-15 has a cash day outside every period the owner
        has -- reachable since 2026-08-11, when the developer deleted the
        writer refusal that forbade producing it, on the ground that it writes
        no wrong figure.  It still deserves a human's attention, and this is
        where the arc puts a derivable question rather than in the writer.
        """
        txn = self._settled_txn(
            seed_user, seed_periods[-1], date(2026, 6, 15),
        )

        results = check_balance_anomalies(db.session)
        ba06 = next(r for r in results if r.check_id == "BA-06")
        assert not ba06.passed
        assert ba06.detail_count == 1
        assert ba06.details[0]["transaction_id"] == txn.id
        assert ba06.details[0]["settled_on"] == date(2026, 6, 15)

    def _ba06(self, db_session):
        """Return the BA-06 result from a fresh sweep."""
        return next(
            r for r in check_balance_anomalies(db_session)
            if r.check_id == "BA-06"
        )

    def _schedule_bounds(self, user_id):
        """Return the owner's ``(first payday, horizon)`` from the DERIVATION.

        The two days BA-06's rewritten predicate compares against, taken from
        ``pay_calendar`` rather than restated here: the check's whole claim
        since plan step ``pay_calendar:C4-c`` is that it bounds the span the
        application derives, and a hand-written expectation would only prove
        this file can do the same arithmetic twice.
        """
        saved = calendar_for(user_id).saved()
        return saved[0].start_date, saved[-1].end_date

    # **The four cases below are the boundary PAIRS, and an adversarial review
    # is why they exist** (2026-09-01).  BA-06's predicate was rewritten at
    # plan step ``pay_calendar:C4-c`` from ``NOT EXISTS (a period whose STORED
    # span contains the day)`` to two comparisons against the derived opening
    # and the derived horizon -- and the two cases that stood used days 25 days
    # past the horizon and deep inside it, so NEITHER sat at a boundary.  Three
    # mutations of the horizon term were driven against them and all passed:
    # dropping ``+ (cadence_days - 1)`` entirely, making it ``+ cadence_days``,
    # and making it ``- (cadence_days - 1)`` -- the last of which would report
    # every settled row in the owner's final paycheck, on every backup sweep.

    def test_ba06_accepts_the_HORIZON_day_itself(
        self, app, db, seed_user, seed_periods,
    ):
        """The last day the schedule covers is COVERED.

        ``MAX(start_date) + (cadence_days - 1)`` is the derivation's own
        projected end for the last period, so a row settling exactly there is
        inside the schedule.  A horizon term one day short reports it.
        """
        _first, horizon = self._schedule_bounds(seed_user["user"].id)
        self._settled_txn(seed_user, seed_periods[-1], horizon)

        assert self._ba06(db.session).passed, self._ba06(db.session).details

    def test_ba06_detects_the_day_AFTER_the_horizon(
        self, app, db, seed_user, seed_periods,
    ):
        """One day past the last covered day is outside every paycheck.

        The pair to the case above, and the ``last_day`` it reports is asserted
        too: a check that fires on the right row while naming the wrong bound
        tells an operator to look in the wrong place.
        """
        _first, horizon = self._schedule_bounds(seed_user["user"].id)
        beyond = horizon + timedelta(days=1)
        txn = self._settled_txn(seed_user, seed_periods[-1], beyond)

        ba06 = self._ba06(db.session)
        assert not ba06.passed
        assert ba06.detail_count == 1
        assert ba06.details[0]["transaction_id"] == txn.id
        assert ba06.details[0]["settled_on"] == beyond
        assert ba06.details[0]["last_day"] == horizon

    def test_ba06_accepts_the_FIRST_PAYDAY_itself(
        self, app, db, seed_user, seed_periods,
    ):
        """A period covers its own opening day, so the first payday is inside."""
        first, _horizon = self._schedule_bounds(seed_user["user"].id)
        self._settled_txn(seed_user, seed_periods[0], first)

        assert self._ba06(db.session).passed, self._ba06(db.session).details

    def test_ba06_detects_the_day_BEFORE_the_first_payday(
        self, app, db, seed_user, seed_periods,
    ):
        """The OPENING arm, which had no case at all until plan step C4-c.

        Derived periods TILE -- each ends the day before the next opens -- so
        the only days outside every paycheck are the ones before the first
        payday and the ones past the horizon.  Two comparisons say that, and
        an arm with no case is how a rewrite comes to be half graded.  The
        ``first_day`` it reports is asserted for the reason its sibling asserts
        ``last_day``.
        """
        first, _horizon = self._schedule_bounds(seed_user["user"].id)
        before = first - timedelta(days=1)
        txn = self._settled_txn(seed_user, seed_periods[0], before)

        ba06 = self._ba06(db.session)
        assert not ba06.passed
        assert ba06.detail_count == 1
        assert ba06.details[0]["transaction_id"] == txn.id
        assert ba06.details[0]["settled_on"] == before
        assert ba06.details[0]["first_day"] == first

    def test_ba06_ignores_a_row_merely_outside_its_OWN_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The control, and it is the whole reason this check is narrow.

        Production carries 21 of 160 settled rows dated outside their OWN
        paycheck and that state is ACCEPTED -- the cash clock and the budget
        clock are different clocks, and ``period_timing`` exists to carry the
        difference.  A check that fired on it would report the app working as
        designed, on every sweep, forever.  This row is filed in the first
        paycheck and settles inside the second: outside its own, inside the
        schedule.  The day is the SECOND paycheck's last covered day, taken
        from the derived calendar since plan step ``pay_calendar:C4-c`` dropped
        the column that used to carry it.
        """
        second = calendar_for(seed_user["user"].id).period_by_id(
            seed_periods[1].id,
        )
        self._settled_txn(seed_user, seed_periods[0], second.end_date)

        results = check_balance_anomalies(db.session)
        ba06 = next(r for r in results if r.check_id == "BA-06")
        assert ba06.passed, ba06.details

# ── Data Consistency ─────────────────────────────────────────────


class TestDataConsistency:
    """Tests for DC-* data consistency checks."""

    def test_clean_database_passes(self, app, db, seed_user, seed_periods):
        """All consistency checks pass on a properly seeded database.

        DC-02 through DC-09: DC-01 was removed 2026-06-11 (settling
        without a manual actual is a designed legal state -- see the
        ``check_data_consistency`` docstring); the remaining IDs keep
        their historical numbers.
        """
        results = check_data_consistency(db.session)
        assert len(results) == 8
        # Critical checks must pass on clean data.
        critical_results = [r for r in results if r.severity == "critical"]
        assert all(r.passed for r in critical_results), (
            f"Critical failures: {[r.check_id for r in critical_results if not r.passed]}"
        )

    def test_settled_without_a_correction_is_not_flagged(
        self, app, db, seed_user, seed_periods
    ):
        """A Paid transaction nobody corrected passes every check.

        Pins the DC-01 removal: marking a row paid without typing a figure is
        the designed workflow, so no consistency check may flag it.  Before the
        removal this exact row failed DC-01 as critical, turning every backup
        verification red on routine data.

        **What such a row looks like changed at plan step X-au-c3.**  It carried
        no figure at all and every reader fell back to its estimate; it now
        RECORDS what the settle booked, on the ``derived`` basis, and the
        estimate beside it is the plan.  The check must pass on that shape,
        which is what routing the fixture through ``settlement_columns`` builds.
        """
        status_done = db.session.query(Status).filter_by(name="Paid").one()
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        settled_on = seed_periods[0].start_date
        txn = Transaction(
            user_id=seed_periods[0].user_id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=status_done.id,
            name="Done No Correction",
            transaction_type_id=txn_type.id,
            amount_ownership=AmountOwnership.own(Decimal("50.00")),
            **settle_day_columns(settled_on),
            **settlement_columns(settled_on, Decimal("50.00")),
        )
        db.session.add(txn)
        db.session.flush()

        results = check_data_consistency(db.session)
        assert all(r.passed for r in results), (
            f"Failures: {[r.check_id for r in results if not r.passed]}"
        )

    def test_dc02_detects_self_transfer(self, app, db, seed_user, seed_periods):
        """DC-02: Transfers where from_account equals to_account.

        The Transfer model has a CHECK constraint (ck_transfers_different_accounts)
        preventing this at the DB level.  We temporarily drop the constraint,
        insert the anomaly, run the check, then restore it.
        """
        account = seed_user["account"]
        status_projected = db.session.query(Status).filter_by(name="Projected").one()

        # Drop the CHECK constraint so we can insert a self-transfer.
        db.session.execute(db.text(
            "ALTER TABLE budget.transfers "
            "DROP CONSTRAINT ck_transfers_different_accounts"
        ))
        try:
            db.session.execute(db.text("""
                INSERT INTO budget.transfers
                    (user_id, pay_period_id, scenario_id, status_id,
                     from_account_id, to_account_id, name, amount,
                     is_override, is_deleted)
                VALUES (:uid, :pid, :sid, :stid, :aid, :aid,
                        'Self Transfer', 100.00, FALSE, FALSE)
            """), {
                "uid": seed_user["user"].id,
                "pid": seed_periods[0].id,
                "sid": seed_user["scenario"].id,
                "stid": status_projected.id,
                "aid": account.id,
            })
            db.session.flush()

            results = check_data_consistency(db.session)
            dc02 = next(r for r in results if r.check_id == "DC-02")
            assert not dc02.passed
            assert dc02.detail_count == 1
            assert dc02.details[0]["from_account_id"] == account.id
            assert dc02.details[0]["to_account_id"] == account.id
        finally:
            # Restore the CHECK constraint.
            db.session.execute(db.text(
                "ALTER TABLE budget.transfers "
                "ADD CONSTRAINT ck_transfers_different_accounts "
                "CHECK (from_account_id != to_account_id) NOT VALID"
            ))

    def test_dc05_detects_active_template_inactive_account(
        self, app, db, seed_user
    ):
        """DC-05 flags an active template referencing an inactive account."""
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        account = seed_user["account"]
        category = list(seed_user["categories"].values())[0]

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=account.id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="Active Template",
            default_amount=Decimal("100.00"),
            is_active=True,
        )
        db.session.add(template)
        db.session.flush()

        # Deactivate the account.
        account.is_active = False
        db.session.flush()

        results = check_data_consistency(db.session)
        dc05 = next(r for r in results if r.check_id == "DC-05")
        assert not dc05.passed
        assert dc05.detail_count == 1  # 1 active template on inactive account

    def _template_with_generated_row(self, seed_user, seed_periods):
        """Create a template plus its rule-generated (non-override) row.

        Returns:
            tuple: (template, generated Transaction).
        """
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        status_projected = db.session.query(Status).filter_by(name="Projected").one()
        category = list(seed_user["categories"].values())[0]

        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="DC06 Template",
            default_amount=Decimal("100.00"),
            is_active=True,
        )
        db.session.add(template)
        db.session.flush()

        generated = Transaction(
            template_id=template.id,
            user_id=seed_periods[0].user_id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=status_projected.id,
            name="DC06 Template",
            category_id=category.id,
            transaction_type_id=txn_type.id,
            amount_ownership=AmountOwnership.own(Decimal("100.00")),
            is_override=False,
        )
        db.session.add(generated)
        db.session.flush()
        return template, generated

    def test_dc06_allows_override_sibling(self, app, db, seed_user, seed_periods):
        """An override sibling next to the generated row is NOT a duplicate.

        Mirrors the schema's own uniqueness contract: both partial unique
        generation indexes apply only
        WHERE ``is_override = FALSE``, precisely so a carried-forward
        unpaid item (flagged ``is_override = TRUE``) can legally coexist
        with the rule-generated row for its target period.  Before the
        2026-06-11 recalibration DC-06 ignored the override predicate
        and flagged this legal pair as critical.
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )

        override_sibling = Transaction(
            template_id=template.id,
            user_id=generated.user_id,
            pay_period_id=generated.pay_period_id,
            scenario_id=generated.scenario_id,
            account_id=generated.account_id,
            status_id=generated.status_id,
            name="DC06 Template (carried forward)",
            category_id=generated.category_id,
            transaction_type_id=generated.transaction_type_id,
            amount_ownership=AmountOwnership.own(Decimal("100.00")),
            is_override=True,
        )
        db.session.add(override_sibling)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc06 = next(r for r in results if r.check_id == "DC-06")
        assert dc06.passed

    def test_dc06_detects_true_duplicate(self, app, db, seed_user, seed_periods):
        """Two undated NON-override rows in one paycheck are flagged.

        The fixture's rows carry no ``occurs_on``, so the contract that holds
        them is the UNDATED half of plan step R17's split: a row answering no
        occurrence still holds its paycheck alone
        (``idx_transactions_template_scenario_undated``).  The partial unique
        index blocks this at the DB tier, so (like the DC-02 test) the index is
        dropped to stage the corruption the check exists to catch -- a partial
        restore or manual SQL is the real-world source.  The staged rows are
        removed before the index is recreated (CREATE UNIQUE INDEX validates
        existing rows).
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )

        db.session.execute(db.text(
            "DROP INDEX budget.idx_transactions_template_scenario_undated"
        ))
        try:
            db.session.execute(db.text("""
                INSERT INTO budget.transactions
                    (template_id, pay_period_id, user_id, scenario_id,
                     account_id, status_id, name, category_id,
                     transaction_type_id, estimated_amount, is_override,
                     is_deleted)
                VALUES (:tid, :pid, :uid, :sid, :aid, :stid, 'DC06 True Dup',
                        :cid, :ttid, 100.00, FALSE, FALSE)
            """), {
                "tid": template.id,
                "pid": generated.pay_period_id,
                # The duplicate is the generated row in every column that is
                # not the index being tested, its owner included.
                "uid": generated.user_id,
                "sid": generated.scenario_id,
                "aid": generated.account_id,
                "stid": generated.status_id,
                "cid": generated.category_id,
                "ttid": generated.transaction_type_id,
            })
            db.session.flush()

            results = check_data_consistency(db.session)
            dc06 = next(r for r in results if r.check_id == "DC-06")
            assert not dc06.passed
            assert dc06.detail_count == 1
            assert dc06.details[0]["cnt"] == 2
        finally:
            # Remove the staged duplicate first -- recreating the
            # unique index validates existing rows.
            db.session.execute(db.text(
                "DELETE FROM budget.transactions WHERE name = 'DC06 True Dup'"
            ))
            # **Drain the deferred constraint triggers before the DDL** (plan
            # step X-f3c-2b).  The raw INSERT above queues an event for
            # ``ck_movement_after_books_open``, and PostgreSQL refuses
            # ``CREATE INDEX`` on a table that has pending trigger events
            # ("cannot CREATE INDEX ... because it has pending trigger
            # events").  Making them immediate runs the check now, inside the
            # same transaction, which is also the honest thing: the rows this
            # index is about to validate are the ones the trigger is about.
            db.session.execute(db.text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.session.execute(db.text("""
                CREATE UNIQUE INDEX idx_transactions_template_scenario_undated
                ON budget.transactions (template_id, scenario_id, pay_period_id)
                WHERE template_id IS NOT NULL
                  AND occurs_on IS NULL
                  AND is_deleted = FALSE
                  AND is_override = FALSE
            """))

    def test_dc06_allows_two_rows_answering_different_occurrences(
        self, app, db, seed_user, seed_periods,
    ):
        """One paycheck, two occurrences, two rows -- and that is CORRECT.

        The state plan step **R17** made storable and this check must not call
        corruption: a cadence that names one paycheck more than once (a monthly
        bill at a pay cadence of 30 days or more) legitimately funds two
        installments from one paycheck.  Asking the OLD question here -- group
        by ``(template, pay_period, scenario)`` -- reports this as a critical
        duplicate, which is exactly the second-fence failure the re-key exists
        to remove.
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )
        generated.occurs_on = date(2026, 1, 15)
        second = Transaction(
            template_id=template.id,
            user_id=generated.user_id,
            pay_period_id=generated.pay_period_id,
            scenario_id=generated.scenario_id,
            account_id=generated.account_id,
            status_id=generated.status_id,
            name="DC06 Template (second occurrence)",
            category_id=generated.category_id,
            transaction_type_id=generated.transaction_type_id,
            amount_ownership=AmountOwnership.own(Decimal("100.00")),
            occurs_on=date(2026, 2, 15),
            is_override=False,
        )
        db.session.add(second)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc06 = next(r for r in results if r.check_id == "DC-06")
        assert dc06.passed, (
            "two rows answering different occurrences of one cadence are a "
            "correct state, not a duplicate"
        )

    def test_dc06_detects_two_rows_answering_one_occurrence(
        self, app, db, seed_user, seed_periods,
    ):
        """The real duplicate since R17: one occurrence answered twice.

        The companion to the case above, and the one that keeps this check
        from having been weakened into uselessness by the re-key: the DATED
        half of the contract still holds a template to one row per occurrence,
        so staging two rows with the SAME ``occurs_on`` must be flagged
        critical.  Without this, dropping the group key to "anything goes in a
        paycheck" would pass every DC-06 test in the file.
        """
        template, generated = self._template_with_generated_row(
            seed_user, seed_periods,
        )
        generated.occurs_on = date(2026, 1, 15)
        db.session.flush()

        db.session.execute(db.text(
            "DROP INDEX budget.idx_transactions_template_scenario_occurrence"
        ))
        try:
            db.session.execute(db.text("""
                INSERT INTO budget.transactions
                    (template_id, pay_period_id, user_id, scenario_id,
                     account_id, status_id, name, category_id,
                     transaction_type_id, estimated_amount, occurs_on,
                     is_override, is_deleted)
                VALUES (:tid, :pid, :uid, :sid, :aid, :stid,
                        'DC06 Same Occurrence',
                        :cid, :ttid, 100.00, :occ, FALSE, FALSE)
            """), {
                "tid": template.id,
                "pid": seed_periods[1].id,
                # The period is a real one of the SAME owner's, so the row is
                # legal on every axis but the occurrence key under test.
                "uid": seed_periods[1].user_id,
                "sid": generated.scenario_id,
                "aid": generated.account_id,
                "stid": generated.status_id,
                "cid": generated.category_id,
                "ttid": generated.transaction_type_id,
                "occ": date(2026, 1, 15),
            })
            db.session.flush()

            results = check_data_consistency(db.session)
            dc06 = next(r for r in results if r.check_id == "DC-06")
            assert not dc06.passed
            assert dc06.detail_count == 1
            assert dc06.details[0]["cnt"] == 2
        finally:
            db.session.execute(db.text(
                "DELETE FROM budget.transactions "
                "WHERE name = 'DC06 Same Occurrence'"
            ))
            # **Drain the deferred constraint triggers before the DDL** (plan
            # step X-f3c-2b), the same two lines the sibling case above needs
            # and for the same reason.  The raw INSERT queues an event for
            # ``ck_movement_after_books_open`` whatever that trigger would
            # DECIDE -- the event is queued at statement time and the function
            # only runs at COMMIT -- and PostgreSQL refuses ``CREATE INDEX`` on
            # a table carrying pending trigger events.  Making them immediate
            # runs the check now, inside this transaction.
            db.session.execute(db.text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.session.execute(db.text("""
                CREATE UNIQUE INDEX idx_transactions_template_scenario_occurrence
                ON budget.transactions (template_id, scenario_id, occurs_on)
                WHERE template_id IS NOT NULL
                  AND occurs_on IS NOT NULL
                  AND is_deleted = FALSE
                  AND is_override = FALSE
            """))

    def test_dc07_detects_user_without_settings(self, app, db):
        """DC-07 detects a user without a user_settings row."""
        # Create a user without settings by bypassing the normal seed.
        user = User(
            email="nosettings@shekel.local",
            password_hash=hash_password("testpass"),
            display_name="No Settings",
        )
        db.session.add(user)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc07 = next(r for r in results if r.check_id == "DC-07")
        assert not dc07.passed
        assert dc07.detail_count == 1  # 1 user without settings
        assert any(
            d.get("email") == "nosettings@shekel.local"
            for d in dc07.details
        )

    def test_dc08_detects_user_without_baseline(self, app, db, seed_user):
        """DC-08 detects an owner-role user without a baseline scenario."""
        # Remove the baseline flag from the seed scenario.
        seed_user["scenario"].is_baseline = False
        db.session.flush()

        results = check_data_consistency(db.session)
        dc08 = next(r for r in results if r.check_id == "DC-08")
        assert not dc08.passed
        assert dc08.detail_count == 1  # 1 user without baseline scenario

    def test_dc08_ignores_companion_users(
        self, app, db, seed_user, seed_companion
    ):
        """A companion with no scenario is NOT flagged by DC-08.

        Companions view the linked owner's data and own no budget rows
        of their own (no accounts, periods, or scenarios) by design --
        "no baseline scenario" is their correct steady state.  Before
        the 2026-06-11 recalibration DC-08 flagged every companion as a
        critical failure on every prod run.
        """
        # Precondition: the companion really has no scenarios.
        scenario_count = (
            db.session.query(Scenario)
            .filter_by(user_id=seed_companion["user"].id)
            .count()
        )
        assert scenario_count == 0

        results = check_data_consistency(db.session)
        dc08 = next(r for r in results if r.check_id == "DC-08")
        assert dc08.passed

    def test_dc09_detects_cross_user_deduction_target(
        self, app, db, seed_user
    ):
        """DC-09 flags a deduction targeting another user's account."""
        from app.models.ref import (  # pylint: disable=import-outside-toplevel
            CalcMethod,
            DeductionTiming,
            FilingStatus,
        )
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from app.models.paycheck_deduction import PaycheckDeduction  # pylint: disable=import-outside-toplevel
        from app.models.user import UserSettings  # pylint: disable=import-outside-toplevel
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
        from app.models.ref import AccountType  # pylint: disable=import-outside-toplevel

        from datetime import date as _date  # pylint: disable=import-outside-toplevel

        # Create a second user with their own account.
        user2 = User(
            email="user2@shekel.local",
            password_hash=hash_password("testpass"),
            display_name="User Two",
        )
        db.session.add(user2)
        db.session.flush()
        settings2 = UserSettings(user_id=user2.id)
        db.session.add(settings2)
        scenario2 = Scenario(user_id=user2.id, name="Baseline", is_baseline=True)
        db.session.add(scenario2)
        # user2's calendar, so the account factory has an anchor to assign.
        # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
        _bootstrap2 = open_owner_calendar(user2.id, _date(2024, 1, 5))[0]

        checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
        account2 = account_service.create_account(
            account_service.AccountSpec(
                user_id=user2.id,
                account_type_id=checking_type.id,
                name="User2 Checking",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.flush()
        # Its BOOKS open before anything this fixture dates (plan step
        # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
        # asserts -- the owner's today -- and this suite settles on or before it.
        open_books_before_the_first_assertion(db.session, account2)

        # Create a salary profile for user 1 with a deduction targeting user 2's account.
        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Test Salary",
            annual_salary=Decimal("80000.00"),
        )
        db.session.add(profile)
        db.session.flush()

        timing = db.session.query(DeductionTiming).first()
        method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckDeduction(
            salary_profile_id=profile.id,
            deduction_timing_id=timing.id,
            calc_method_id=method.id,
            name="Cross-user deduction",
            amount=Decimal("100.00"),
            target_account_id=account2.id,  # User 2's account!
        )
        db.session.add(deduction)
        db.session.flush()

        results = check_data_consistency(db.session)
        dc09 = next(r for r in results if r.check_id == "DC-09")
        assert not dc09.passed
        assert dc09.detail_count == 1  # 1 deduction targeting another user's account


# ── run_all_checks ───────────────────────────────────────────────


class TestRunAllChecks:
    """Tests for the top-level run_all_checks() function."""

    def test_runs_all_categories_by_default(
        self, app, db, seed_user, seed_periods
    ):
        """run_all_checks() returns results from all 4 categories."""
        results = run_all_checks(db.session)
        categories = {r.category for r in results}
        assert categories == {"referential", "orphan", "balance", "consistency"}

    def test_category_filter(self, app, db, seed_user, seed_periods):
        """run_all_checks(categories=['referential']) only runs FK checks."""
        results = run_all_checks(db.session, categories=["referential"])
        assert all(r.category == "referential" for r in results)
        # 12 since plan step X-f1c3c: FK-03 ("accounts pointing to a
        # nonexistent anchor period") went with the column it queried.
        assert len(results) == 12

    def test_returns_check_result_objects(
        self, app, db, seed_user, seed_periods
    ):
        """All returned items are CheckResult instances."""
        results = run_all_checks(db.session)
        assert all(isinstance(r, CheckResult) for r in results)

    def test_exit_code_zero_on_clean_db(
        self, app, db, seed_user, seed_periods
    ):
        """No critical failures on a properly seeded database."""
        results = run_all_checks(db.session)
        critical = [r for r in results if not r.passed and r.severity == "critical"]
        assert len(critical) == 0, (
            f"Unexpected critical failures: "
            f"{[(r.check_id, r.description) for r in critical]}"
        )

    def test_clean_database_zero_critical_anomalies(
        self, app, db, seed_user, seed_periods
    ):
        """All checks on a clean seeded database report zero critical anomalies.

        This is a regression guard: if a future schema change introduces a
        latent integrity issue, this test catches it immediately.
        """
        results = run_all_checks(db.session)
        critical_failures = [
            r for r in results
            if not r.passed and r.severity == "critical"
        ]
        assert len(critical_failures) == 0, (
            f"Critical anomalies on clean DB: "
            f"{[(r.check_id, r.description, r.detail_count) for r in critical_failures]}"
        )
        # Total check count should cover all 4 categories:
        # 12 FK + 5 OR + 3 BA + 8 DC = 28 checks (DC-01 removed
        # 2026-06-11 -- estimated-only settles are a legal state).
        # It was 30 from plan step X-f1c3c, where FK-03 and BA-02 both
        # queried ``accounts.current_anchor_*`` and went with the columns;
        # BA-06 was added 2026-08-11 beside the deletion of pay_calendar
        # C3-b's coverage rule, which is what made its state reachable, and
        # BA-07 beside pay_calendar C2-b2, which took away the
        # generation-time report of a pay-period date gap.  It fell to 31 at
        # plan step R-F6, which retired OR-02: the orphaned-rule state it
        # scanned for is refused by ``ck_recurrence_rules_one_owner``.  It
        # fell to 28 at plan step pay_calendar:C4-c, which dropped
        # ``end_date`` and ``period_index`` and took BA-03, BA-04 and BA-07
        # with them -- an ordinal gap, a span overlap and an uncovered day are
        # all unexpressible once a period is one payday.
        assert len(results) == 28
