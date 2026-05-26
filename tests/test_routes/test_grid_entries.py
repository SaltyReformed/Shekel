"""
Shekel Budget App -- Grid Entry Progress Indicator Tests (Commit 7)

Tests the entry progress display ("X / Y" format) and enhanced tooltip
for tracked transactions in the grid cell and mobile grid views.

Covers:
  - build_entry_sums_dict computation correctness (unit tests).
  - Cell endpoint rendering with progress format (integration tests).
  - Tooltip enhancement with entry breakdown.
  - Non-tracked transaction regression (display unchanged).
  - Grid page flow: entry_sums passes through to template context.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transaction_entry import TransactionEntry
from app.models.ref import Status, TransactionType
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.services import pay_period_service


def _create_tracked_txn(seed_user, seed_periods_today, period_index=0,
                         estimated=Decimal("500.00")):
    """Create a tracked expense transaction backed by a tracking-enabled template.

    Returns:
        tuple of (Transaction, TransactionTemplate).
    """
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    projected = db.session.query(Status).filter_by(name="Projected").one()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        name="Groceries",
        default_amount=estimated,
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()

    txn = Transaction(
        pay_period_id=seed_periods_today[period_index].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Groceries",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        template_id=template.id,
        estimated_amount=estimated,
    )
    db.session.add(txn)
    db.session.flush()

    return txn, template


def _create_plain_txn(seed_user, seed_periods_today, period_index=0,
                       estimated=Decimal("200.00"), name="Test Expense"):
    """Create a non-tracked ad-hoc expense transaction (no template)."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    projected = db.session.query(Status).filter_by(name="Projected").one()

    txn = Transaction(
        pay_period_id=seed_periods_today[period_index].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=estimated,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _add_entry(txn, seed_user, amount, is_credit=False,
               description="Purchase"):
    """Add a purchase entry to a transaction."""
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        entry_date=date(2026, 4, 12),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


class TestBuildEntrySumsDict:
    """Unit tests for the build_entry_sums_dict helper function."""

    def test_debit_entries_only(self, app, seed_user, seed_periods_today):
        """Tracked txn with only debit entries: credit sum is Decimal('0')."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("100.00"))
            _add_entry(txn, seed_user, Decimal("50.00"))
            db.session.commit()

            result = build_entry_sums_dict([txn])

            assert txn.id in result
            sums = result[txn.id]
            # 100 + 50 = 150 debit, 0 credit
            assert sums["debit"] == Decimal("150.00")
            assert sums["credit"] == Decimal("0")
            assert isinstance(sums["credit"], Decimal)
            assert sums["total"] == Decimal("150.00")
            assert sums["count"] == 2

    def test_credit_entries_only(self, app, seed_user, seed_periods_today):
        """Tracked txn with only credit entries: debit sum is Decimal('0')."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("75.00"), is_credit=True)
            db.session.commit()

            result = build_entry_sums_dict([txn])

            sums = result[txn.id]
            assert sums["debit"] == Decimal("0")
            assert isinstance(sums["debit"], Decimal)
            assert sums["credit"] == Decimal("75.00")
            assert sums["total"] == Decimal("75.00")
            assert sums["count"] == 1

    def test_mixed_entries(self, app, seed_user, seed_periods_today):
        """Both debit and credit sums correct for mixed entries."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            result = build_entry_sums_dict([txn])

            sums = result[txn.id]
            # 150 + 80 = 230 debit, 100 credit, 330 total
            assert sums["debit"] == Decimal("230.00")
            assert sums["credit"] == Decimal("100.00")
            assert sums["total"] == Decimal("330.00")
            assert sums["count"] == 3

    def test_no_entries_excluded(self, app, seed_user, seed_periods_today):
        """Transaction with empty entries list is NOT in the result dict."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = build_entry_sums_dict([txn])

            assert txn.id not in result

    def test_non_tracked_excluded(self, app, seed_user, seed_periods_today):
        """Non-tracked transaction (no template) is NOT in the result dict."""
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = build_entry_sums_dict([txn])

            assert txn.id not in result

    def test_multiple_txns_independent(self, app, seed_user, seed_periods_today):
        """Multiple tracked txns each have independent entry sums."""
        with app.app_context():
            txn1, _ = _create_tracked_txn(
                seed_user, seed_periods_today, estimated=Decimal("500.00"),
            )
            _add_entry(txn1, seed_user, Decimal("100.00"))

            txn2, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=1,
                estimated=Decimal("300.00"),
            )
            _add_entry(txn2, seed_user, Decimal("250.00"))
            _add_entry(txn2, seed_user, Decimal("50.00"), is_credit=True)
            db.session.commit()

            result = build_entry_sums_dict([txn1, txn2])

            assert result[txn1.id]["total"] == Decimal("100.00")
            assert result[txn1.id]["count"] == 1
            assert result[txn2.id]["total"] == Decimal("300.00")
            assert result[txn2.id]["count"] == 2

    def test_empty_list_returns_empty_dict(self, app):
        """Empty transaction list returns empty dict."""
        with app.app_context():
            result = build_entry_sums_dict([])
            assert result == {}

    def test_c31_4_remaining_server_computed(
        self, app, seed_user, seed_periods_today,
    ):
        """C31-4 -- ``remaining`` and ``over_budget`` are server-computed.

        Previously :file:`grid/_transaction_cell.html` subtracted
        ``estimated_amount - es.total`` in Jinja (TA-01); the value
        now arrives pre-computed in the dict so the template renders
        without inline arithmetic.

        Arithmetic: estimated $500.00 budget, two debit entries
        $150.00 + $80.00 = $230.00 spent; remaining = 500 - 230 = 270.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            sums = build_entry_sums_dict([txn])[txn.id]
            assert sums["remaining"] == Decimal("270.00")
            assert sums["over_budget"] is False
            assert isinstance(sums["remaining"], Decimal)

    def test_c31_4_over_budget_flag_server_computed(
        self, app, seed_user, seed_periods_today,
    ):
        """C31-4 -- the over-budget flag matches a remaining < 0 test.

        Arithmetic: estimated $500 budget, $600 spent; remaining = -100,
        over_budget = True (matches the prior Jinja conditional
        ``remaining < 0``).
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("600.00"))
            db.session.commit()

            sums = build_entry_sums_dict([txn])[txn.id]
            assert sums["remaining"] == Decimal("-100.00")
            assert sums["over_budget"] is True


class TestBuildEntryListsDict:
    """Unit tests for the ``build_entry_lists_dict`` helper.

    The helper pre-computes the entry-list rendering inputs that the
    grid + companion routes feed to ``render_row_card`` so the
    inline ``_transaction_entries.html`` include can render on the
    initial response instead of fanning out to one ``/entries`` GET
    per envelope card (which blew past ``RATELIMIT_DEFAULT`` of
    "30 per minute" on a 6-period grid).  Tests mirror the
    ``TestBuildEntrySumsDict`` shape above.
    """

    def test_envelope_with_entries_has_data(
        self, app, seed_user, seed_periods_today,
    ):
        """Envelope txn with entries appears in result with full data.

        Arithmetic: $500 estimated, two debit entries $150 + $80 =
        $230; remaining = 500 - 230 = 270.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            result = build_entry_lists_dict([txn])

            assert txn.id in result
            data = result[txn.id]
            assert len(data["entries"]) == 2
            assert data["remaining"] == Decimal("270.00")
            assert isinstance(data["remaining"], Decimal)
            # The seeded entries are dated 2026-04-12; whether they
            # fall inside the seed_periods_today range depends on the
            # period dates -- assert the set is well-formed without
            # over-constraining the membership.
            assert isinstance(data["out_of_period_ids"], set)

    def test_envelope_without_entries_still_included(
        self, app, seed_user, seed_periods_today,
    ):
        """Envelope txn with zero entries is included with empty list.

        Mirrors the macro's expectation: an envelope card without
        entries still renders the entries section (showing "No
        purchases recorded yet" + the add-entry form), so the dict
        must carry the empty-list entry rather than excluding the
        txn.  Remaining equals the full estimated amount.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = build_entry_lists_dict([txn])

            assert txn.id in result
            data = result[txn.id]
            assert data["entries"] == []
            assert data["remaining"] == Decimal("500.00")
            assert data["out_of_period_ids"] == set()

    def test_non_envelope_excluded(
        self, app, seed_user, seed_periods_today,
    ):
        """Non-envelope (no template) txn is NOT in the result dict.

        The macro's ``txn.template.is_envelope`` guard means the
        inline entries section is only rendered for envelope
        templates.  Pre-computing entries for non-envelopes would
        waste work and leak a misleading entry record.
        """
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = build_entry_lists_dict([txn])

            assert txn.id not in result

    def test_non_envelope_template_excluded(
        self, app, seed_user, seed_periods_today,
    ):
        """Txn whose template has is_envelope=False is NOT in the result.

        A template can exist without enabling envelope tracking
        (recurring bills, e.g. mortgage).  Such transactions do not
        get inline entries.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            projected = (
                db.session.query(Status).filter_by(name="Projected").one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                name="Mortgage",
                default_amount=Decimal("2000.00"),
                is_envelope=False,
            )
            db.session.add(template)
            db.session.flush()
            txn = Transaction(
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Mortgage",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                template_id=template.id,
                estimated_amount=Decimal("2000.00"),
            )
            db.session.add(txn)
            db.session.commit()

            result = build_entry_lists_dict([txn])

            assert txn.id not in result

    def test_multiple_envelopes_independent(
        self, app, seed_user, seed_periods_today,
    ):
        """Each envelope txn's data is computed independently.

        Two envelope txns with different entry counts and remaining
        balances must produce two independent dict entries with the
        correct per-txn values.
        """
        with app.app_context():
            txn1, _ = _create_tracked_txn(
                seed_user, seed_periods_today, estimated=Decimal("500.00"),
            )
            _add_entry(txn1, seed_user, Decimal("100.00"))

            txn2, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=1,
                estimated=Decimal("300.00"),
            )
            _add_entry(txn2, seed_user, Decimal("250.00"))
            db.session.commit()

            result = build_entry_lists_dict([txn1, txn2])

            assert result[txn1.id]["remaining"] == Decimal("400.00")
            assert len(result[txn1.id]["entries"]) == 1
            assert result[txn2.id]["remaining"] == Decimal("50.00")
            assert len(result[txn2.id]["entries"]) == 1

    def test_empty_list_returns_empty_dict(self, app):
        """Empty transaction list returns empty dict, no errors."""
        with app.app_context():
            result = build_entry_lists_dict([])
            assert result == {}


class TestCellProgressDisplay:
    """Tests for progress display via the GET /transactions/<id>/cell endpoint."""

    def test_tracked_projected_shows_progress(self, app, auth_client,
                                               seed_user, seed_periods_today):
        """Cell shows 'X / Y' format for tracked projected txn with entries.

        Arithmetic: 2 entries @ $150 + $80 = $230 spent on $500 budget.
        Cell should display '230 / 500' (no dollar sign, no cents).
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            assert b"230 / 500" in resp.data

    def test_over_budget_has_danger_class(self, app, auth_client,
                                          seed_user, seed_periods_today):
        """Over-budget progress cell includes text-danger styling.

        Arithmetic: entries total $530 on $500 budget -> over by $30.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("300.00"))
            _add_entry(txn, seed_user, Decimal("230.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            assert b"530 / 500" in resp.data
            assert b"text-danger" in resp.data
            assert b"fw-semibold" in resp.data

    def test_under_budget_no_danger_class(self, app, auth_client,
                                           seed_user, seed_periods_today):
        """Under-budget progress cell does NOT have text-danger styling.

        Arithmetic: entry total $100 on $500 budget -> $400 remaining.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("100.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            assert b"100 / 500" in resp.data
            # The progress span should NOT have text-danger.
            # Check that the progress span uses font-mono without danger.
            assert b'class="font-mono"' in resp.data

    def test_no_entries_shows_standard_estimated(self, app, auth_client,
                                                  seed_user, seed_periods_today):
        """Tracked txn with no entries shows standard estimated amount.

        No progress format -- just '500' in standard font-mono span.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Standard display: just the estimated amount.
            assert ">500</span>" in html
            # Progress format must NOT appear.
            assert "/ 500" not in html

    def test_done_shows_actual_not_progress(self, app, auth_client,
                                             seed_user, seed_periods_today):
        """Paid (DONE) txn shows actual amount, not progress format.

        Entry total is $330 on a $500 budget. After mark-paid, actual
        is set to $330. Cell should show '330' (actual), not '330 / 500'.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("200.00"))
            _add_entry(txn, seed_user, Decimal("130.00"))

            # Mark as paid.
            done = db.session.query(Status).filter_by(name="Paid").one()
            txn.status_id = done.id
            txn.actual_amount = Decimal("330.00")
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Should show actual amount, not progress format.
            assert "/ 500" not in html

    def test_non_tracked_unchanged(self, app, auth_client,
                                    seed_user, seed_periods_today):
        """Non-tracked transaction renders standard amount (regression).

        Plain ad-hoc expense with no template: shows '200' in font-mono span.
        """
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert ">200</span>" in html
            assert "/ 200" not in html


class TestCellProgressTooltip:
    """Tests for the enhanced tooltip on tracked transactions with entries."""

    def test_tooltip_remaining_under_budget(self, app, auth_client,
                                             seed_user, seed_periods_today):
        """Tooltip shows spent/budget and 'remaining' when under budget.

        Arithmetic: $230 spent on $500 budget -> $270 remaining.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "$230.00 / $500.00" in html
            assert "$270.00 remaining" in html
            assert "2 entries" in html

    def test_tooltip_over_budget(self, app, auth_client,
                                  seed_user, seed_periods_today):
        """Tooltip shows 'over' when over budget.

        Arithmetic: $530 spent on $500 budget -> $30 over.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("300.00"))
            _add_entry(txn, seed_user, Decimal("230.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "$530.00 / $500.00" in html
            assert "$30.00 over" in html

    def test_tooltip_singular_entry(self, app, auth_client,
                                     seed_user, seed_periods_today):
        """Tooltip says '1 entry' (singular) for a single entry."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("100.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "1 entry" in html
            # Must NOT say "1 entries".
            assert "1 entries" not in html

    def test_tooltip_plural_entries(self, app, auth_client,
                                     seed_user, seed_periods_today):
        """Tooltip says '3 entries' (plural) for multiple entries."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("50.00"), description="Store A")
            _add_entry(txn, seed_user, Decimal("60.00"), description="Store B")
            _add_entry(txn, seed_user, Decimal("70.00"), description="Store C")
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "3 entries" in html

    def test_tooltip_credit_note(self, app, auth_client,
                                  seed_user, seed_periods_today):
        """Tooltip mentions CC portion when credit entries exist.

        Arithmetic: $150 debit + $100 credit = $250 total.
        Credit note: 'includes $100.00 on CC'.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "includes $100.00 on CC" in html

    def test_tooltip_no_credit_note_when_zero(self, app, auth_client,
                                               seed_user, seed_periods_today):
        """Tooltip omits CC note when no credit entries exist."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "on CC" not in html

    def test_standard_tooltip_no_entries(self, app, auth_client,
                                          seed_user, seed_periods_today):
        """Non-entry txn gets standard tooltip with status name."""
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            # Standard tooltip includes status name.
            assert "Projected" in html
            # Enhanced tooltip markers must be absent.
            assert "remaining" not in html
            assert "entries" not in html


class TestGridPageEntrySums:
    """Integration test: entry_sums flows through the grid page render."""

    def test_grid_page_shows_progress(self, app, auth_client,
                                       seed_user, seed_periods_today):
        """GET /grid renders progress format for tracked txns with entries.

        Creates the transaction in the current period so it appears in
        the default grid view.
        """
        with app.app_context():
            # Find the current period so the txn is visible in the grid.
            current = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            # Find which seed_periods_today index matches the current period.
            period_idx = next(
                (i for i, p in enumerate(seed_periods_today) if p.id == current.id),
                0,
            )

            txn, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=period_idx,
            )
            _add_entry(txn, seed_user, Decimal("180.00"))
            _add_entry(txn, seed_user, Decimal("70.00"))
            db.session.commit()

            resp = auth_client.get("/grid")

            assert resp.status_code == 200
            # The desktop grid cell should show progress format.
            # 180 + 70 = 250 spent on 500 budget.
            assert b"250 / 500" in resp.data
