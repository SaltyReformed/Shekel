"""
Shekel Budget App -- Ad-hoc transaction flag tests (F2 / F3)

Ad-hoc transactions (template_id IS NULL) carry their own
``is_envelope`` (purchase tracking) and ``companion_visible`` flags,
since they have no template to inherit from.  These tests cover:

  * F3 -- purchase tracking on ad-hoc rows: entry creation, the
    expense-only guard, settle-from-entries on mark-done, the
    Credit-status block, the popover / create-form controls, the
    checkbox-persistence semantics of the shared update schema, and
    carry-forward (ad-hoc envelopes move whole, no rollover).
  * F2 -- companion visibility of ad-hoc rows: the companion query and
    the entry-access check resolve the row's own flag.

Resolution of the underlying properties is unit-tested in
tests/test_models/test_transaction_flag_resolution.py.
"""
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import carry_forward_service


def _make_adhoc(seed_user, period, *, is_envelope=False, companion_visible=False,
                income=False, name="Ad-hoc", amount="100.00",
                status=StatusEnum.PROJECTED):
    """Create and commit an ad-hoc (template_id IS NULL) transaction."""
    type_enum = TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE
    txn = Transaction(
        name=name,
        estimated_amount=Decimal(amount),
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        status_id=ref_cache.status_id(status),
        pay_period_id=period.id,
        account_id=seed_user["account"].id,
        category_id=list(seed_user["categories"].values())[0].id,
        scenario_id=seed_user["scenario"].id,
        template_id=None,
        is_envelope=is_envelope,
        companion_visible=companion_visible,
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def _add_entry(txn, seed_user, amount, description, entry_date=None):
    """Attach a debit entry to a transaction directly via ORM."""
    entry = TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=Decimal(str(amount)),
        description=description,
        entry_date=entry_date or date.today(),
        is_credit=False,
    )
    db.session.add(entry)
    db.session.commit()
    return entry


# ── F3: purchase tracking on ad-hoc transactions ─────────────────────


class TestAdhocPurchaseTracking:
    """Entry tracking works on ad-hoc envelope transactions."""

    def test_entry_create_succeeds_on_adhoc_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST an entry on an ad-hoc envelope row creates it (200)."""
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], is_envelope=True,
            )
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "40.00",
                    "description": "Kroger",
                    "entry_date": seed_periods_today[0].start_date.isoformat(),
                },
            )
            assert resp.status_code == 200
            assert b"Kroger" in resp.data
            entries = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id).all()
            )
            assert len(entries) == 1
            assert entries[0].amount == Decimal("40.00")

    def test_entry_create_rejected_on_adhoc_without_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST an entry on a non-envelope ad-hoc row is rejected (400)."""
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], is_envelope=False,
            )
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "40.00",
                    "description": "Kroger",
                    "entry_date": seed_periods_today[0].start_date.isoformat(),
                },
            )
            assert resp.status_code == 400
            assert b"does not support individual purchase tracking" in resp.data
            assert (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id).count() == 0
            )

    def test_mark_done_settles_adhoc_envelope_from_entries(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """mark-done on an ad-hoc envelope sets actual_amount = sum(entries).

        Two debit entries of 30.00 + 20.00 -> actual_amount 50.00, and
        the status becomes Paid (Done).
        """
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], is_envelope=True,
                amount="500.00",
            )
            _add_entry(txn, seed_user, "30.00", "Store A")
            _add_entry(txn, seed_user, "20.00", "Store B")

            resp = auth_client.post(f"/transactions/{txn.id}/mark-done")
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.actual_amount == Decimal("50.00")  # 30.00 + 20.00
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert txn.status_id == done_id

    def test_credit_status_blocked_on_adhoc_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Setting Credit status on an ad-hoc envelope row is rejected (400).

        Credit is per-entry on tracked rows, never per-transaction.
        """
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], is_envelope=True,
            )
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"status_id": credit_id, "version_id": txn.version_id},
            )
            assert resp.status_code == 400
            assert b"individual purchase tracking" in resp.data


class TestAdhocFlagUI:
    """The flag controls render only where they apply."""

    def test_full_edit_popover_shows_controls_for_adhoc_expense(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ad-hoc expense popover renders both flag checkboxes."""
        with app.app_context():
            txn = _make_adhoc(seed_user, seed_periods_today[0])
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b'name="is_envelope"' in resp.data
            assert b'name="companion_visible"' in resp.data

    def test_full_edit_popover_hides_tracking_for_adhoc_income(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ad-hoc income popover hides is_envelope but shows companion_visible.

        Purchase tracking is expense-only; companion visibility applies
        to income too.
        """
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], income=True, name="Side gig",
            )
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b'name="is_envelope"' not in resp.data
            assert b'name="companion_visible"' in resp.data

    def test_full_edit_popover_shows_purchases_when_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ad-hoc envelope popover renders the Purchases entry list."""
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], is_envelope=True,
            )
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            assert b"Purchases" in resp.data
            assert f'id="entry-list-{txn.id}"'.encode() in resp.data

    def test_full_create_form_renders_flag_controls(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The ad-hoc full-create popover renders the flag checkboxes."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.get(
                "/transactions/new/full"
                f"?category_id={category.id}"
                f"&period_id={seed_periods_today[0].id}"
                f"&account_id={seed_user['account'].id}"
                f"&transaction_type_id={expense_id}"
            )
            assert resp.status_code == 200
            assert b'name="is_envelope"' in resp.data
            assert b'name="companion_visible"' in resp.data

    def test_add_transaction_modal_renders_flag_controls(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The grid's Add Transaction modal renders both flag checkboxes.

        The tracking row carries the data-adhoc-envelope-row hook that
        app.js uses to hide it when an income type is selected.
        """
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b'name="is_envelope"' in resp.data
            assert b'name="companion_visible"' in resp.data
            assert b"data-adhoc-envelope-row" in resp.data


class TestAdhocFlagValidation:
    """Income guard and create-time flag handling."""

    def test_inline_create_expense_sets_flags(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_inline persists is_envelope / companion_visible when sent."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
                "companion_visible": "true",
            })
            assert resp.status_code == 201
            txn = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods_today[0].id)
                .order_by(Transaction.id.desc()).first()
            )
            assert txn.is_envelope is True
            assert txn.companion_visible is True

    def test_inline_create_defaults_flags_off(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An inline create omitting the flags defaults both to off."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_id,
                "scenario_id": seed_user["scenario"].id,
            })
            assert resp.status_code == 201
            txn = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods_today[0].id)
                .order_by(Transaction.id.desc()).first()
            )
            assert txn.is_envelope is False
            assert txn.companion_visible is False

    def test_inline_create_income_rejects_is_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_inline rejects is_envelope on an income transaction (422)."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": income_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
            })
            assert resp.status_code == 422
            assert (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods_today[0].id).count() == 0
            )

    def test_create_transaction_expense_sets_flags(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_transaction (the Add Transaction modal endpoint) persists flags."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
            resp = auth_client.post("/transactions", data={
                "name": "Modal Expense",
                "estimated_amount": "60.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
                "companion_visible": "true",
            })
            assert resp.status_code == 201
            txn = (
                db.session.query(Transaction)
                .filter_by(name="Modal Expense").one()
            )
            assert txn.is_envelope is True
            assert txn.companion_visible is True

    def test_create_transaction_income_rejects_is_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """create_transaction rejects is_envelope on an income transaction (422)."""
        with app.app_context():
            category = list(seed_user["categories"].values())[0]
            income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            resp = auth_client.post("/transactions", data={
                "name": "Modal Income",
                "estimated_amount": "60.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": income_id,
                "scenario_id": seed_user["scenario"].id,
                "is_envelope": "true",
            })
            assert resp.status_code == 422
            assert (
                db.session.query(Transaction)
                .filter_by(name="Modal Income").count() == 0
            )

    def test_update_income_rejects_is_envelope(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH cannot enable tracking on an ad-hoc income row (400)."""
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], income=True, name="Side gig",
            )
            resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "is_envelope": "true",
                "version_id": txn.version_id,
            })
            assert resp.status_code == 400
            db.session.refresh(txn)
            assert txn.is_envelope is False


class TestAdhocFlagPersistence:
    """The shared update schema must not clobber flags it was not sent."""

    def test_quick_edit_does_not_clear_flags(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A PATCH omitting the flag fields leaves them untouched.

        The quick-edit form sends only estimated_amount; without a
        load_default on the shared update schema, the absent flags are
        not applied, so an ad-hoc envelope row stays an envelope.
        """
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=True,
            )
            resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "estimated_amount": "150.00",
                "version_id": txn.version_id,
            })
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.estimated_amount == Decimal("150.00")
            assert txn.is_envelope is True
            assert txn.companion_visible is True

    def test_unchecking_flag_persists_false(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An unchecked box (hidden 'false' only) clears the flag.

        Mirrors the popover markup: when the box is unchecked the form
        submits only the hidden ``companion_visible=false``.
        """
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0], companion_visible=True,
            )
            resp = auth_client.patch(f"/transactions/{txn.id}", data={
                "companion_visible": "false",
                "version_id": txn.version_id,
            })
            assert resp.status_code == 200
            db.session.refresh(txn)
            assert txn.companion_visible is False


# ── F2: companion visibility of ad-hoc transactions ──────────────────


class TestAdhocCompanionVisibility:
    """A companion sees ad-hoc rows by their own companion_visible flag."""

    def test_companion_sees_visible_adhoc(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """An ad-hoc companion_visible row appears in the companion view."""
        with app.app_context():
            _make_adhoc(
                seed_user, seed_periods_today[0],
                companion_visible=True, name="Shared Dinner",
            )
            resp = companion_client.get(
                f"/companion/period/{seed_periods_today[0].id}",
            )
            assert resp.status_code == 200
            assert b"Shared Dinner" in resp.data

    def test_companion_cannot_see_hidden_adhoc(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """An ad-hoc row with companion_visible=False is hidden."""
        with app.app_context():
            _make_adhoc(
                seed_user, seed_periods_today[0],
                companion_visible=False, name="Secret Gift",
            )
            resp = companion_client.get(
                f"/companion/period/{seed_periods_today[0].id}",
            )
            assert resp.status_code == 200
            assert b"Secret Gift" not in resp.data

    def test_companion_entry_access_on_visible_adhoc(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """A companion may read entries on a visible ad-hoc envelope row."""
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=True,
            )
            _add_entry(txn, seed_user, "25.00", "Lunch")
            resp = companion_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 200
            assert b"Lunch" in resp.data

    def test_companion_entry_access_denied_on_hidden_adhoc(
        self, app, db, seed_user, seed_periods_today,
        seed_companion, companion_client,
    ):
        """A companion cannot read entries on a hidden ad-hoc row (404)."""
        with app.app_context():
            txn = _make_adhoc(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=False,
            )
            _add_entry(txn, seed_user, "25.00", "Lunch")
            resp = companion_client.get(f"/transactions/{txn.id}/entries")
            assert resp.status_code == 404


# ── F3: carry-forward of ad-hoc envelope rows ────────────────────────


class TestAdhocEnvelopeCarryForward:
    """Ad-hoc envelopes carry forward as a whole-row move, not a rollover."""

    def test_adhoc_envelope_moves_whole_with_entries(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ad-hoc envelope carries forward whole, keeping its entries.

        A recurring envelope TEMPLATE would settle-and-roll, but an
        ad-hoc envelope has no next canonical to roll into, so it falls
        into the discrete bucket: relocated to the target period, status
        unchanged (Projected), is_override left False, entries intact.
        """
        with app.app_context():
            source = seed_periods_today[0]
            target = seed_periods_today[1]
            txn = _make_adhoc(
                seed_user, source, is_envelope=True, name="Ad-hoc Envelope",
            )
            _add_entry(txn, seed_user, "15.00", "Partial spend")

            carry_forward_service.carry_forward_unpaid(
                source.id, target.id,
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            db.session.refresh(txn)
            assert txn.pay_period_id == target.id
            assert txn.is_override is False
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.status_id == projected_id
            entries = (
                db.session.query(TransactionEntry)
                .filter_by(transaction_id=txn.id).all()
            )
            assert len(entries) == 1
            assert entries[0].amount == Decimal("15.00")
