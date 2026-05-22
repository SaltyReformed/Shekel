"""Tests for ``scripts/scan_destroyed_received_history.py`` (Commit 22 / OPT-2).

The scan is the optional, read-only diagnostic for CRIT-05 -- it reads
``system.audit_log`` DELETE rows on ``budget.transactions`` and reports
the rows the post-Commit-21 guard would block today.  These tests cover
the three Section 9 gates:

* C22-1: a known settled DELETE that pre-Commit-21 would have destroyed
  is surfaced by the scan, with template/period/amount/status intact.
* C22-2: the scan is purely read-only -- ``system.audit_log`` row count
  is identical before and after.
* C22-3: a clean database produces an empty report and exit code 0.

The fixtures rely on the project's real audit trigger to write the
DELETE row, because the production scan is meant to read the trigger's
own output.  Inserting fake ``system.audit_log`` rows directly would
test the SELECT but not the JSONB shape the trigger function emits.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from scripts.scan_destroyed_received_history import (
    format_report,
    parse_args,
    run_cli,
    scan_destroyed_received_history,
    summarise,
)


# ── Helpers ──────────────────────────────────────────────────────


def _audit_log_count() -> int:
    """Return the current row count in ``system.audit_log``."""
    return db.session.execute(
        db.text("SELECT COUNT(*) FROM system.audit_log")
    ).scalar()


def _delete_count_for_transactions() -> int:
    """Return the count of DELETE audit rows on ``budget.transactions``."""
    return db.session.execute(db.text(
        "SELECT COUNT(*) FROM system.audit_log "
        " WHERE table_schema = 'budget' "
        "   AND table_name = 'transactions' "
        "   AND operation = 'DELETE'"
    )).scalar()


def _create_template_with_received(seed_user, seed_periods_today, *,
                                   estimated="2000.00", actual="2000.00"):
    """Create a template + RECEIVED paycheck transaction and return both.

    Mirrors the C21-1 test setup but does not exercise the route -- the
    caller chooses how to delete the row.  The settled Received status
    matches the pre-fix CRIT-05 victim profile (every paycheck is
    marked Received on mark-done).
    """
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    received_status = db.session.query(Status).filter_by(name="Received").one()
    salary_cat = seed_user["categories"]["Salary"]

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=salary_cat.id,
        transaction_type_id=income_type.id,
        name="Biweekly Paycheck",
        default_amount=Decimal(estimated),
    )
    db.session.add(template)
    db.session.flush()

    paycheck = Transaction(
        template_id=template.id,
        pay_period_id=seed_periods_today[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        category_id=salary_cat.id,
        transaction_type_id=income_type.id,
        name="Biweekly Paycheck",
        estimated_amount=Decimal(estimated),
        actual_amount=Decimal(actual),
        status_id=received_status.id,
    )
    db.session.add(paycheck)
    db.session.commit()
    return template, paycheck


# ── C22-1 ────────────────────────────────────────────────────────


class TestScanReportsKnownDeletion:
    """C22-1: the scan surfaces a destroyed settled template row."""

    def test_received_paycheck_delete_appears_in_scan(
        self, app, db, seed_user, seed_periods_today,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """A RECEIVED paycheck deleted while template-linked is reported.

        Setup mirrors the pre-Commit-21 bug victim profile: an income
        template with a RECEIVED paycheck whose ``template_id`` is
        non-NULL at the moment of deletion.  Deleting through the ORM
        fires the ``audit_transactions`` trigger and writes a
        ``system.audit_log`` row with ``operation='DELETE'`` and the
        full ``old_data`` payload.  The scan must surface exactly one
        row whose fields match what was destroyed.

        Hand-computed expectations: amount $2000.00 (Decimal from
        string), status name "Received", template_id and pay_period_id
        equal to the originals.
        """
        with app.app_context():
            template, paycheck = _create_template_with_received(
                seed_user, seed_periods_today,
            )
            template_id = template.id
            transaction_id = paycheck.id
            pay_period_id = paycheck.pay_period_id
            received_status_id = paycheck.status_id

            # Simulate the pre-Commit-21 bug: physically delete the
            # RECEIVED row while still template-linked.  Cannot go
            # through the post-Commit-21 route -- it would refuse,
            # which is exactly the protection the scan attests to.
            db.session.delete(paycheck)
            db.session.commit()

            rows = scan_destroyed_received_history(db.session)

        assert len(rows) == 1
        row = rows[0]
        assert row.transaction_id == transaction_id
        assert row.template_id == template_id
        assert row.pay_period_id == pay_period_id
        assert row.status_id == received_status_id
        assert row.status_name == "Received"
        # Decimal from string: $2000.00, the actual_amount of the
        # destroyed paycheck.  ``COALESCE(actual, estimated)`` in the
        # scan SQL takes actual first.
        assert row.amount == Decimal("2000.00")
        assert row.transaction_name == "Biweekly Paycheck"

    def test_paid_expense_delete_also_appears(
        self, app, db, seed_user, seed_periods_today,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """A Paid expense delete is reported too -- not just Received.

        The CRIT-05 finding focused on RECEIVED because the pre-fix
        guard's ``[DONE, SETTLED]`` enumeration omitted it specifically,
        but the post-Commit-21 guard blocks every settled status.  The
        scan therefore must report any settled DELETE, not just
        RECEIVED, so the operator sees the full blast radius.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            rent_cat = seed_user["categories"]["Rent"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=rent_cat.id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.flush()

            expense = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=rent_cat.id,
                transaction_type_id=expense_type.id,
                name="Rent",
                estimated_amount=Decimal("1200.00"),
                actual_amount=Decimal("1200.00"),
                status_id=paid_status.id,
            )
            db.session.add(expense)
            db.session.commit()

            db.session.delete(expense)
            db.session.commit()

            rows = scan_destroyed_received_history(db.session)

        assert len(rows) == 1
        # Hand-computed: $1200.00 actual, "Paid" status name.
        assert rows[0].amount == Decimal("1200.00")
        assert rows[0].status_name == "Paid"

    def test_projected_delete_not_in_scan(
        self, app, db, seed_user, seed_periods_today,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """Deleting a Projected row is allowed and not reported.

        Projected rows are non-settled -- ``is_settled = FALSE`` per
        ``ref_seeds.py`` -- so deleting them is the intended behavior
        of the hard-delete path even after Commit 21.  Reporting these
        would drown the diagnostic in benign noise.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            projected_status = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            rent_cat = seed_user["categories"]["Rent"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=rent_cat.id,
                transaction_type_id=expense_type.id,
                name="Rent (future)",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.flush()

            txn = Transaction(
                template_id=template.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=rent_cat.id,
                transaction_type_id=expense_type.id,
                name="Rent (future)",
                estimated_amount=Decimal("1200.00"),
                status_id=projected_status.id,
            )
            db.session.add(txn)
            db.session.commit()

            db.session.delete(txn)
            db.session.commit()

            rows = scan_destroyed_received_history(db.session)

        assert rows == []

    def test_settled_without_template_id_not_in_scan(
        self, app, db, seed_user, seed_periods_today,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """A settled DELETE without ``template_id`` is out of scope.

        The CRIT-05 bug was specifically in the template hard-delete
        path; single-transaction deletes do not touch
        ``template_has_paid_history``.  Filtering on
        ``template_id IS NOT NULL`` keeps the report focused on rows
        the post-Commit-21 guard actually addresses.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            received_status = (
                db.session.query(Status).filter_by(name="Received").one()
            )
            salary_cat = seed_user["categories"]["Salary"]

            # No template_id: an ad-hoc settled income, e.g. one-off.
            ad_hoc = Transaction(
                template_id=None,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                category_id=salary_cat.id,
                transaction_type_id=income_type.id,
                name="Bonus",
                estimated_amount=Decimal("500.00"),
                actual_amount=Decimal("500.00"),
                status_id=received_status.id,
            )
            db.session.add(ad_hoc)
            db.session.commit()

            db.session.delete(ad_hoc)
            db.session.commit()

            rows = scan_destroyed_received_history(db.session)

        assert rows == []


# ── C22-2 ────────────────────────────────────────────────────────


class TestScanIsReadOnly:
    """C22-2: the scan never writes to the database."""

    def test_scan_does_not_change_audit_log_count(
        self, app, db, seed_user, seed_periods_today,  # pylint: disable=unused-argument,redefined-outer-name
    ):
        """Audit log row count is identical before and after the scan.

        The scan reads ``system.audit_log`` -- a tamper-resistant
        forensic record -- and must not alter it.  Any DML (INSERT
        from a stray test side-effect, an accidental DELETE, an
        UPDATE that triggers the on-table audit) would change the
        count.  Equality before and after is the strongest single
        assertion of read-only behavior.
        """
        with app.app_context():
            # Materialise some audit volume so the count is non-zero
            # -- a more demanding test than an empty trail.
            _create_template_with_received(seed_user, seed_periods_today)
            before = _audit_log_count()
            assert before > 0, (
                "test precondition failed: audit_log should have rows "
                "after the template+paycheck creation"
            )

            scan_destroyed_received_history(db.session)
            # ``scan_destroyed_received_history`` does not commit, but
            # neither does it open its own transaction -- the read
            # piggybacks on the test session.  Any write would still
            # show up as a row delta here.
            after = _audit_log_count()

        assert after == before


# ── C22-3 ────────────────────────────────────────────────────────


class TestScanEmptyOnCleanDatabase:
    """C22-3: a clean database produces an empty report and exit 0."""

    def test_no_deletions_produces_empty_list(self, app, db):  # pylint: disable=unused-argument,redefined-outer-name
        """No DELETE audit rows -> scan returns empty list."""
        with app.app_context():
            # Sanity: the per-test template clone wipes audit_log to
            # empty (conftest "system.audit_log empty" contract).
            assert _delete_count_for_transactions() == 0
            rows = scan_destroyed_received_history(db.session)
        assert rows == []

    def test_empty_scan_report_text(self):
        """The empty-scan report names the finding and prescribes no action."""
        text = format_report([])
        assert "CRIT-05" in text
        assert "OPT-2" in text
        assert "no settled template-linked transactions" in text
        assert "Action: none required" in text

    def test_run_cli_exits_zero_on_clean_database(self, app, db):  # pylint: disable=unused-argument,redefined-outer-name
        """``run_cli`` returns 0 against a clean test DB.

        ``run_cli`` builds its own Flask app via ``create_app()``,
        which on CI defaults to ``DevConfig`` (FLASK_ENV unset) and
        would otherwise try to reach a local Unix socket.  Pass the
        per-worker DB URL through the documented ``--database-url``
        override so the CLI's app construction targets the same
        database the fixture session is bound to.  Asserting exit 0
        covers the success path; a failed connection would return 3
        per the script's documented exit codes.
        """
        # ``app`` fixture is already inside an app context; ``run_cli``
        # creates its own and pops it cleanly.
        exit_code = run_cli(database_url=app.config["SQLALCHEMY_DATABASE_URI"])
        assert exit_code == 0


# ── Report formatting & aggregation ─────────────────────────────


class TestReportFormatting:
    """Smoke checks on the report formatter and aggregation helpers.

    Pure functions over the DestroyedRow list, so these tests stay
    out of the database and exercise the rendering edge cases the
    DB tests above do not reach (verbose-mode detail, multi-status
    breakdown, multiple-template aggregation).
    """

    def _make_row(self, **overrides):
        """Build a ``DestroyedRow`` with sensible defaults."""
        from datetime import datetime  # pylint: disable=import-outside-toplevel
        from scripts.scan_destroyed_received_history import (  # pylint: disable=import-outside-toplevel
            DestroyedRow,
        )
        defaults = {
            "audit_id": 1,
            "executed_at": datetime(2026, 1, 15, 12, 0, 0),
            "transaction_id": 100,
            "template_id": 10,
            "pay_period_id": 5,
            "status_id": 3,
            "status_name": "Received",
            "transaction_name": "Paycheck",
            "amount": Decimal("2000.00"),
            "user_id": 1,
            "db_user": "shekel_app",
        }
        defaults.update(overrides)
        return DestroyedRow(**defaults)

    def test_summarise_aggregates_by_status_and_template(self):
        """Per-status counts/sums and unique-template id list."""
        rows = [
            self._make_row(
                audit_id=1, status_name="Received",
                amount=Decimal("2000.00"), template_id=10,
            ),
            self._make_row(
                audit_id=2, status_name="Received",
                amount=Decimal("2500.00"), template_id=10,
            ),
            self._make_row(
                audit_id=3, status_name="Paid",
                amount=Decimal("1200.00"), template_id=20,
            ),
        ]
        summary = summarise(rows)
        assert summary["total_count"] == 3
        # Hand-computed: 2000 + 2500 + 1200 = 5700.
        assert summary["total_amount"] == Decimal("5700.00")
        # Hand-computed: Received 2 rows summing 4500; Paid 1 row at 1200.
        assert summary["by_status"]["Received"] == (2, Decimal("4500.00"))
        assert summary["by_status"]["Paid"] == (1, Decimal("1200.00"))
        # Two distinct templates touched.
        assert summary["affected_templates"] == [10, 20]

    def test_format_report_verbose_lists_each_row(self):
        """Verbose output includes a detail line per destroyed row."""
        rows = [
            self._make_row(audit_id=1, transaction_name="Paycheck"),
            self._make_row(audit_id=2, transaction_name="Second Paycheck"),
        ]
        text = format_report(rows, verbose=True)
        assert "Total destroyed rows: 2" in text
        assert "'Paycheck'" in text
        assert "'Second Paycheck'" in text
        assert "Detail (oldest first):" in text

    def test_format_report_summary_omits_detail_by_default(self):
        """Non-verbose output is summary only -- no per-row detail."""
        rows = [self._make_row(transaction_name="Paycheck")]
        text = format_report(rows, verbose=False)
        assert "Total destroyed rows: 1" in text
        assert "Detail (oldest first):" not in text


# ── CLI parsing ──────────────────────────────────────────────────


class TestParseArgs:
    """The CLI accepts ``--database-url`` and ``--verbose``."""

    def test_default_args(self):
        """No flags -> ``database_url=None`` and ``verbose=False``."""
        args = parse_args([])
        assert args.database_url is None
        assert args.verbose is False

    def test_verbose_flag(self):
        """``--verbose`` sets the bool."""
        args = parse_args(["--verbose"])
        assert args.verbose is True

    def test_database_url_override(self):
        """``--database-url URL`` captures the value verbatim."""
        args = parse_args(["--database-url", "postgresql://x/y"])
        assert args.database_url == "postgresql://x/y"

    def test_unknown_flag_rejected(self):
        """An unrecognised flag exits with SystemExit (argparse default)."""
        with pytest.raises(SystemExit):
            parse_args(["--force"])
