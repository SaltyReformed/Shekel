"""
Shekel Budget App -- C-21 Route-Level State Machine Tests

PATCH /transactions/<id> and the transfer service status path both
funnel through ``app.services.state_machine.verify_transition``.
These tests exercise the route layer's translation of an illegal
transition into a 400 -- and confirm that legal transitions still
produce the cell-render 200 the HTMX UI depends on.

The same PATCH route also enforces the finalised-row field-edit lock
(``finalised_edit_rejection``, #26): money / period / category /
due-date edits are refused on a finalised row unless the same request
reverts it to Projected.  ``TestPatchRejectsFinalisedFieldEdit`` /
``TestPatchAllowsEditableField`` cover that policy at the route layer.

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction


def _create_projected_expense(seed_user, seed_periods_today):
    """Insert a projected expense -- the typical PATCH starting state.

    Uses the same fixture set as ``tests/test_routes/test_grid.py`` so
    these tests share the auth_client / seed_user wiring without
    introducing a parallel fixture stack.
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = Transaction(
        pay_period_id=seed_periods_today[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name="Test Expense",
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("123.45"),
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _walk_to_settled(txn):
    """Drive a freshly-projected row through projected -> done -> settled.

    The state machine forbids a direct projected -> settled jump, so
    tests that need a settled row to attack must first land on Done.
    Bypasses the route layer to keep the helper terse -- the
    transitions exercised here are themselves covered by the legal
    suite and by the existing transfer-service tests.
    """
    done_id = ref_cache.status_id(StatusEnum.DONE)
    settled_id = ref_cache.status_id(StatusEnum.SETTLED)
    txn.status_id = done_id
    db.session.commit()
    txn.status_id = settled_id
    db.session.commit()


# ── Route returns 400 on illegal transition ─────────────────────────


class TestPatchRejectsIllegalTransition:
    """The PATCH /transactions/<id> handler must surface a 400 with a
    state-machine message when the proposed status_id is unreachable
    from the row's current status."""

    def test_settled_to_projected_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A settled row PATCHed to projected returns 400 and stays settled."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.status_id == settled_id

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 400
            # Body names the transition so the user understands why
            # the request was refused.
            body = response.data.decode()
            assert "transaction" in body
            assert str(settled_id) in body
            assert str(projected_id) in body

            db.session.refresh(txn)
            assert txn.status_id == settled_id

    def test_settled_to_cancelled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A settled row cannot be cancelled via PATCH."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)
            cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(cancelled_id)},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.status_id == settled_id

    def test_projected_to_settled_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Projected rows must land on Done/Received first before Settled."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(settled_id)},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.status_id == projected_id


# ── Route accepts legal transitions ─────────────────────────────────


class TestPatchAcceptsLegalTransition:
    """Legal transitions still produce the HTMX cell-render 200 that
    the grid depends on -- the state-machine guard is non-disruptive
    on every workflow path the user actually exercises."""

    def test_projected_to_done_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The most common transition still returns 200."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(done_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == done_id

    def test_done_to_projected_revert_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Revert path stays open so users can fix mismarks."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            # Walk to Done first.
            txn.status_id = done_id
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == projected_id

    def test_projected_to_projected_identity_accepted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Idempotent re-submission of the same status is silently
        accepted -- HTMX double-clicks must not produce 400s."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(projected_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == projected_id


# ── Finalised-row field-edit lock (#26) ─────────────────────────────


class TestPatchRejectsFinalisedFieldEdit:
    """A finalised (Paid/Received/Settled/Credit/Cancelled) row's
    money / period / category / due-date fields cannot be rewritten via
    PATCH unless the same request reverts it to Projected.  Each test
    drives a row to Paid (or Settled), edits a locked field with NO
    status change, and asserts a 400 plus an unchanged stored value --
    the gap was that a status-less PATCH skipped every guard."""

    def test_paid_row_amount_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Editing estimated_amount on a Paid row is refused and the
        stored amount is unchanged (the headline silent-rewrite gap)."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "999.99"},
            )
            assert response.status_code == 400
            body = response.data.decode()
            assert "finalised" in body
            assert "Paid" in body
            assert "Revert" in body

            db.session.refresh(txn)
            # The pre-edit value (123.45 from _create_projected_expense)
            # survives -- the rewrite never reached the row.
            assert txn.estimated_amount == Decimal("123.45")

    def test_settled_row_category_edit_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Re-categorising a Settled row is refused; category unchanged."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)
            original_category_id = txn.category_id
            other_category_id = seed_user["categories"]["Rent"].id
            assert other_category_id != original_category_id

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"category_id": str(other_category_id)},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.category_id == original_category_id

    def test_settled_row_period_move_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a Settled row to another period is refused; period kept."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            _walk_to_settled(txn)
            original_period_id = txn.pay_period_id
            other_period_id = seed_periods_today[1].id
            assert other_period_id != original_period_id

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"pay_period_id": str(other_period_id)},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            assert txn.pay_period_id == original_period_id

    def test_paid_row_amount_edit_while_archiving_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A combined PATCH that archives (Paid -> Settled) AND rewrites the
        amount is refused -- archiving is not a revert, so the lock holds."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.commit()
            settled_id = ref_cache.status_id(StatusEnum.SETTLED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": str(settled_id), "estimated_amount": "5.00"},
            )
            assert response.status_code == 400
            db.session.refresh(txn)
            # Neither the status nor the amount moved.
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.estimated_amount == Decimal("123.45")


class TestPatchAllowsEditableField:
    """The lock is scoped: it leaves Projected rows fully editable, lets a
    finalised row edit non-locked display fields, and permits the
    revert-and-correct edit in a single request."""

    def test_projected_row_amount_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Projected row's amount is still freely editable (no regression)."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"estimated_amount": "200.00"},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("200.00")

    def test_paid_row_notes_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A display field (notes) stays editable on a Paid row -- only the
        money / period / category / due-date fields are locked."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": "Reconciled against statement"},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.notes == "Reconciled against statement"

    def test_paid_row_revert_and_amount_edit_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Reverting Paid -> Projected AND editing the amount in one PATCH is
        allowed -- the escape hatch the lock deliberately preserves."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            db.session.commit()
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "status_id": str(projected_id),
                    "estimated_amount": "200.00",
                },
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.status_id == projected_id
            assert txn.estimated_amount == Decimal("200.00")

    def test_finalised_full_edit_notes_save_with_money_omitted_allowed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The real finalised full-edit save: the disabled money inputs are
        OMITTED from the POST and only ``notes`` + the unchanged Status
        dropdown value are submitted.  The guard passes (no locked field in
        the payload) and the notes update commits while the amount is
        untouched -- proving the template's ``disabled`` (not ``readonly``)
        choice is what lets a notes-only save through (#26)."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            txn.status_id = done_id
            db.session.commit()

            response = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"notes": "Reconciled", "status_id": str(done_id)},
            )
            assert response.status_code == 200
            db.session.refresh(txn)
            assert txn.notes == "Reconciled"
            assert txn.status_id == done_id
            assert txn.estimated_amount == Decimal("123.45")
