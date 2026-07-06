"""Tests for the c4f8a1b6e9d2 escrow-lines EXPAND backfill migration.

The migration (``docs/design/escrow_line_identity_refactor.md``, Commit 1)
creates ``budget.escrow_lines`` + ``budget.escrow_component_versions`` and
backfills them from the legacy ``budget.escrow_components``, mapping the old
``[effective_date, end_date)`` range rows onto supersession versions (no
``end_date``; removal/gaps expressed as ``is_removed`` tombstones) so "escrow as
of any date D" is preserved exactly.

The migration is at HEAD when these tests run (the template builder upgraded
base->head), so the new tables already exist and are empty on a loan-free
template.  Each test engineers ``escrow_components`` rows and drives the
migration's ``backfill_escrow_lines`` helper directly against them (the pattern
``test_escrow_overlap_fix_migration.py`` uses), then asserts the resulting lines
and versions.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.loan_features import EscrowComponent
from tests._test_helpers import create_loan_account, load_migration_module

_MIGRATION = "c4f8a1b6e9d2_escrow_lines_and_versions_expand.py"
_ORIGINATION = date(2018, 12, 1)


def _run_backfill() -> None:
    """Execute the migration's backfill against the test session and commit."""
    load_migration_module(_MIGRATION).backfill_escrow_lines(db.session)
    db.session.commit()


def _add_component(loan_id, name, annual, effective_date, end_date,
                   inflation_rate=None):
    """Insert one legacy escrow component version (flushed)."""
    comp = EscrowComponent(
        account_id=loan_id, name=name, annual_amount=annual,
        effective_date=effective_date, end_date=end_date,
        inflation_rate=inflation_rate,
    )
    db.session.add(comp)
    db.session.flush()
    return comp


def _lines(loan_id):
    """Return the loan's backfilled escrow lines, ordered by name."""
    return (
        db.session.query(EscrowLine)
        .filter_by(account_id=loan_id)
        .order_by(EscrowLine.name)
        .all()
    )


def _versions(line_id):
    """Return a line's versions, ordered by effective date."""
    return (
        db.session.query(EscrowComponentVersion)
        .filter_by(line_id=line_id)
        .order_by(EscrowComponentVersion.effective_date)
        .all()
    )


class TestMigrationChain:
    """The migration is correctly chained onto the current head."""

    def test_migration_chained(self):
        """revision / down_revision link this migration onto a1c8e4f2b7d6."""
        module = load_migration_module(_MIGRATION)
        assert module.revision == "c4f8a1b6e9d2"
        assert module.down_revision == "a1c8e4f2b7d6"


class TestBackfillDerivation:
    """The backfill maps legacy ranges onto supersession versions exactly."""

    def test_single_open_line_one_version_no_tombstone(self, app, db, seed_user):
        """One open [orig, open) row -> one line, one real version, no tombstone.

        The common case (and the account-3 shape after the f2a7c1e9b4d3 fix): a
        single active escrow line becomes one line with one non-removed version at
        origination and no tombstone.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Open Line Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Tax and Insurance", Decimal("7403.88"),
                _ORIGINATION, None,
            )
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            assert lines[0].name == "Tax and Insurance"
            versions = _versions(lines[0].id)
            assert len(versions) == 1
            assert versions[0].effective_date == _ORIGINATION
            assert versions[0].annual_amount == Decimal("7403.88")
            assert versions[0].is_removed is False

    def test_amount_change_two_versions_no_tombstone(self, app, db, seed_user):
        """Adjacent amount change -> one line, two real versions, NO tombstone.

        Old [orig, 2026-01-01) $7,403.88 + new [2026-01-01, open) $8,003.88 share
        a name: the new version supersedes the old at 2026-01-01, so no tombstone
        is needed and the line's amount steps cleanly.
        """
        change = date(2026, 1, 1)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Amount Change Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Tax", Decimal("7403.88"), _ORIGINATION, change,
            )
            _add_component(loan.id, "Tax", Decimal("8003.88"), change, None)
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            versions = _versions(lines[0].id)
            assert [v.effective_date for v in versions] == [_ORIGINATION, change]
            assert [v.annual_amount for v in versions] == [
                Decimal("7403.88"), Decimal("8003.88"),
            ]
            assert all(v.is_removed is False for v in versions)

    def test_removal_creates_tombstone(self, app, db, seed_user):
        """A closed row with no successor -> a real version + a removal tombstone.

        PMI [orig, 2023-01-01) with nothing after it: the backfill adds a
        tombstone at 2023-01-01 so the line resolves to 0 from the removal date.
        """
        removed = date(2023, 1, 1)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Removal Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "PMI", Decimal("1200.00"), _ORIGINATION, removed,
            )
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            versions = _versions(lines[0].id)
            assert len(versions) == 2
            real, tomb = versions
            assert real.effective_date == _ORIGINATION
            assert real.annual_amount == Decimal("1200.00")
            assert real.is_removed is False
            assert tomb.effective_date == removed
            assert tomb.is_removed is True
            assert tomb.annual_amount == Decimal("0.00")  # tombstone carries no amount

    def test_gap_then_readd_puts_tombstone_in_the_gap(self, app, db, seed_user):
        """A gapped removal + re-add -> tombstone bridges the gap.

        [orig, 2022-01-01) removed, re-added [2024-01-01, open): the gap
        [2022-01-01, 2024-01-01) must resolve to 0, so a tombstone lands at
        2022-01-01 and the re-add is a fresh real version at 2024-01-01.
        """
        gone = date(2022, 1, 1)
        back = date(2024, 1, 1)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Gap Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(loan.id, "Flood", Decimal("600.00"), _ORIGINATION, gone)
            _add_component(loan.id, "Flood", Decimal("720.00"), back, None)
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            versions = _versions(lines[0].id)
            # orig real, gap tombstone, re-add real -- in date order.
            assert [(v.effective_date, v.is_removed) for v in versions] == [
                (_ORIGINATION, False),
                (gone, True),
                (back, False),
            ]

    def test_collapsed_row_at_end_date_does_not_suppress_tombstone(
        self, app, db, seed_user,
    ):
        """A collapsed row sitting AT a closed row's end_date still gets a tombstone.

        The subtlest correctness path: the tombstone-suppression check requires the
        successor to be NON-collapsed, so a zero-length row at 2023-01-01 must NOT
        count as an adjacent successor to [orig, 2023-01-01).  The line must still
        resolve to 0 after 2023-01-01, i.e. a tombstone lands there.  (If the
        successor predicate ever dropped its non-collapsed qualifier, resolution
        would silently break; this test guards that.)
        """
        removed = date(2023, 1, 1)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Collapsed At End Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(loan.id, "Tax", Decimal("1200.00"), _ORIGINATION, removed)
            # A same-day add-then-delete AT the removal date (zero-length range).
            _add_component(loan.id, "Tax", Decimal("1300.00"), removed, removed)
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            versions = _versions(lines[0].id)
            # orig real + tombstone at the removal date; the collapsed row dropped.
            assert [(v.effective_date, v.is_removed) for v in versions] == [
                (_ORIGINATION, False),
                (removed, True),
            ]

    def test_amount_change_then_removal(self, app, db, seed_user):
        """Two amounts then a removal -> real, real, tombstone in date order."""
        change = date(2022, 1, 1)
        removed = date(2024, 1, 1)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Change Then Remove Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(loan.id, "Tax", Decimal("1200.00"), _ORIGINATION, change)
            _add_component(loan.id, "Tax", Decimal("1400.00"), change, removed)
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            versions = _versions(lines[0].id)
            assert [
                (v.effective_date, v.annual_amount, v.is_removed)
                for v in versions
            ] == [
                (_ORIGINATION, Decimal("1200.00"), False),
                (change, Decimal("1400.00"), False),
                (removed, Decimal("0.00"), True),
            ]

    def test_same_name_across_accounts_yields_separate_lines(
        self, app, db, seed_user,
    ):
        """Two accounts each with a "Tax" line -> two independent lines.

        Guards the ``GROUP BY account_id, name`` scoping: identically-named
        escrow on different loans must never be merged into one line.
        """
        with app.app_context():
            loan_a = create_loan_account(
                seed_user, db.session, name="Loan A",
                origination_date=_ORIGINATION,
            )
            loan_b = create_loan_account(
                seed_user, db.session, name="Loan B",
                origination_date=_ORIGINATION,
            )
            _add_component(loan_a.id, "Tax", Decimal("1200.00"), _ORIGINATION, None)
            _add_component(loan_b.id, "Tax", Decimal("3400.00"), _ORIGINATION, None)
            db.session.commit()

            _run_backfill()

            lines_a = _lines(loan_a.id)
            lines_b = _lines(loan_b.id)
            assert len(lines_a) == 1
            assert len(lines_b) == 1
            assert lines_a[0].id != lines_b[0].id
            assert _versions(lines_a[0].id)[0].annual_amount == Decimal("1200.00")
            assert _versions(lines_b[0].id)[0].annual_amount == Decimal("3400.00")

    def test_collapsed_row_alone_yields_no_line(self, app, db, seed_user):
        """A single zero-length collapsed row -> no line at all.

        The f2a7c1e9b4d3 fix collapses a subsumed row to
        ``effective_date = end_date`` (never active).  Such a row contributed
        nothing, so it must not manufacture a line or version.
        """
        collapsed = date(2026, 4, 3)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Collapsed Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Old Name", Decimal("7403.88"), collapsed, collapsed,
            )
            db.session.commit()

            _run_backfill()

            assert _lines(loan.id) == []
            assert db.session.query(EscrowComponentVersion).join(
                EscrowLine,
                EscrowComponentVersion.line_id == EscrowLine.id,
            ).filter(EscrowLine.account_id == loan.id).count() == 0

    def test_account3_shape_single_surviving_line(self, app, db, seed_user):
        """Collapsed old name + open new name -> one line (new name), one version.

        The real Mortgage after the f2a7c1e9b4d3 fix: "Property Tax & Insurance"
        collapsed to [2026-04-03, 2026-04-03) and "Tax and Insurance"
        [orig, open).  The backfill drops the collapsed old-name row (no line) and
        yields exactly one line at $7,403.88 from origination.
        """
        collapsed = date(2026, 4, 3)
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Property Tax & Insurance", Decimal("7403.88"),
                collapsed, collapsed,
            )
            _add_component(
                loan.id, "Tax and Insurance", Decimal("7403.88"),
                _ORIGINATION, None,
            )
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            assert lines[0].name == "Tax and Insurance"
            versions = _versions(lines[0].id)
            assert len(versions) == 1
            assert versions[0].annual_amount == Decimal("7403.88")
            assert versions[0].is_removed is False

    def test_inflation_rate_is_preserved_on_the_version(self, app, db, seed_user):
        """A legacy component's ``inflation_rate`` carries onto its backfilled version.

        Escalation is a forward-projection input, so it must survive the
        restructure verbatim; a removal tombstone carries no rate (NULL).
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Inflation Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Tax", Decimal("7403.88"), _ORIGINATION,
                date(2023, 1, 1), inflation_rate=Decimal("0.0300"),
            )
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert len(lines) == 1
            versions = _versions(lines[0].id)
            real, tomb = versions
            assert real.inflation_rate == Decimal("0.0300")
            assert real.is_removed is False
            assert tomb.inflation_rate is None
            assert tomb.is_removed is True

    def test_distinct_names_become_distinct_lines(self, app, db, seed_user):
        """Two differently-named active charges -> two independent lines."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Multi Line Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(loan.id, "Tax", Decimal("7403.88"), _ORIGINATION, None)
            _add_component(loan.id, "PMI", Decimal("1200.00"), _ORIGINATION, None)
            db.session.commit()

            _run_backfill()

            lines = _lines(loan.id)
            assert [line.name for line in lines] == ["PMI", "Tax"]
            for line in lines:
                assert len(_versions(line.id)) == 1

    def test_idempotent(self, app, db, seed_user):
        """A second backfill run inserts no duplicate lines or versions."""
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Idempotent Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Tax", Decimal("7403.88"), _ORIGINATION,
                date(2023, 1, 1),
            )
            db.session.commit()

            _run_backfill()
            first_lines = len(_lines(loan.id))
            first_versions = db.session.query(EscrowComponentVersion).count()

            _run_backfill()
            assert len(_lines(loan.id)) == first_lines
            assert (
                db.session.query(EscrowComponentVersion).count()
                == first_versions
            )


class TestOverlapGuard:
    """The backfill aborts on an unresolvable different-amount overlap."""

    def test_overlapping_different_amount_raises(self, app, db, seed_user):
        """Two non-collapsed same-name rows that overlap in date abort the backfill.

        An amount change the temporal migration backfilled to origination (old
        $1,200 [orig, 2023-01-01), new $1,400 [orig, open)) overlaps on
        [orig, 2023-01-01) and cannot be auto-resolved -- indistinguishable from
        two real charges -- so the guard raises rather than mangling the data.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Overlap Loan",
                origination_date=_ORIGINATION,
            )
            _add_component(
                loan.id, "Tax", Decimal("1200.00"), _ORIGINATION,
                date(2023, 1, 1),
            )
            _add_component(loan.id, "Tax", Decimal("1400.00"), _ORIGINATION, None)
            db.session.commit()

            with pytest.raises(RuntimeError, match="overlapping non-collapsed"):
                _run_backfill()
