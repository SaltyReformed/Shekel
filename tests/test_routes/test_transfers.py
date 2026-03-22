"""
Shekel Budget App -- Transfer Route Tests

Tests for transfer template CRUD, grid cell endpoints, transfer instance
operations, and ad-hoc transfer creation (§2.3 of the test plan).
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.recurrence_rule import RecurrenceRule
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.ref import AccountType, RecurrencePattern, Status
from app.services.auth_service import hash_password


def _create_savings_account(seed_user):
    """Helper: create a second (savings) account for the test user."""
    savings_type = db.session.query(AccountType).filter_by(name="savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _create_template(seed_user, savings_acct, with_rule=True):
    """Helper: create a transfer template with optional recurrence rule."""
    rule = None
    if with_rule:
        every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()
        rule = RecurrenceRule(
            user_id=seed_user["user"].id,
            pattern_id=every_period.id,
        )
        db.session.add(rule)
        db.session.flush()

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        recurrence_rule_id=rule.id if rule else None,
        name="Monthly Savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(template)
    db.session.commit()
    return template


def _create_transfer(seed_user, seed_periods, savings_acct, template=None):
    """Helper: create a transfer instance in the first period."""
    projected = db.session.query(Status).filter_by(name="projected").one()
    xfer = Transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        status_id=projected.id,
        transfer_template_id=template.id if template else None,
        name="Monthly Savings",
        amount=Decimal("200.00"),
    )
    db.session.add(xfer)
    db.session.commit()
    return xfer


def _create_other_user_with_template():
    """Create a second user with their own template and transfer.

    Returns:
        dict with keys: user, account, savings, template, transfer.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="savings").one()

    checking = Account(
        user_id=other_user.id, account_type_id=checking_type.id,
        name="Other Checking", current_anchor_balance=Decimal("500.00"),
    )
    savings = Account(
        user_id=other_user.id, account_type_id=savings_type.id,
        name="Other Savings", current_anchor_balance=Decimal("0"),
    )
    db.session.add_all([checking, savings])

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    template = TransferTemplate(
        user_id=other_user.id,
        from_account_id=checking.id,
        to_account_id=savings.id,
        name="Other Transfer",
        default_amount=Decimal("100.00"),
    )
    db.session.add(template)
    db.session.flush()

    from app.services import pay_period_service
    from datetime import date
    periods = pay_period_service.generate_pay_periods(
        user_id=other_user.id,
        start_date=date(2026, 1, 2),
        num_periods=3,
        cadence_days=14,
    )
    db.session.flush()

    projected = db.session.query(Status).filter_by(name="projected").one()
    xfer = Transfer(
        user_id=other_user.id,
        from_account_id=checking.id,
        to_account_id=savings.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        status_id=projected.id,
        transfer_template_id=template.id,
        name="Other Transfer",
        amount=Decimal("100.00"),
    )
    db.session.add(xfer)
    db.session.commit()

    return {
        "user": other_user,
        "template": template,
        "transfer": xfer,
    }


# ── Template Management ───────────────────────────────────────────


class TestTemplateList:
    """Tests for GET /transfers and GET /transfers/new."""

    def test_list_templates(self, app, auth_client, seed_user, seed_periods):
        """GET /transfers renders the transfer templates list."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get("/transfers")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data

    def test_new_template_form(self, app, auth_client, seed_user):
        """GET /transfers/new renders the creation form."""
        with app.app_context():
            response = auth_client.get("/transfers/new")

            assert response.status_code == 200
            assert b'name="default_amount"' in response.data
            assert b'name="from_account_id"' in response.data
            assert b"New Recurring Transfer" in response.data


class TestTemplatePrefill:
    """Tests for GET /transfers/new with pre-filled account query params."""

    def test_new_transfer_prefills_from_account(self, app, auth_client, seed_user):
        """GET /transfers/new?from_account=<id> pre-selects the source account."""
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(f"/transfers/new?from_account={account_id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'value="{account_id}"' in html

    def test_new_transfer_prefills_to_account(self, app, auth_client, seed_user):
        """GET /transfers/new?to_account=<id> pre-selects the destination account."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            resp = auth_client.get(f"/transfers/new?to_account={savings.id}")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'value="{savings.id}"' in html


class TestTemplateCreate:
    """Tests for POST /transfers."""

    def test_create_template(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers creates a template with recurrence and generates transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers", data={
                "name": "Weekly Savings",
                "default_amount": "150.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_pattern": "every_period",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(user_id=seed_user["user"].id, name="Weekly Savings")
                .one()
            )
            assert tmpl.default_amount == Decimal("150.00")
            assert tmpl.recurrence_rule is not None

    def test_create_template_validation_error(self, app, auth_client, seed_user):
        """POST /transfers with missing fields shows a validation error."""
        with app.app_context():
            response = auth_client.post("/transfers", data={
                "name": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_template_same_accounts(self, app, auth_client, seed_user):
        """POST /transfers with from == to account shows a validation error."""
        with app.app_context():
            acct_id = seed_user["account"].id

            response = auth_client.post("/transfers", data={
                "name": "Self Transfer",
                "default_amount": "100.00",
                "from_account_id": acct_id,
                "to_account_id": acct_id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_template_double_submit(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers twice with the same name returns a flash warning
        on the second attempt instead of a 500 error, and creates exactly
        one template in the database."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            form_data = {
                "name": "Duplicate Transfer",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_pattern": "every_period",
            }

            # -- First submission: succeeds --
            resp1 = auth_client.post("/transfers", data=form_data)
            assert resp1.status_code == 302, (
                f"First submit returned {resp1.status_code}, expected 302"
            )

            # Verify creation via DB.
            template = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Duplicate Transfer",
            ).one()
            assert template.default_amount == Decimal("100.00")

            # Record how many transfers were generated.
            first_submit_transfer_count = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count()
            assert first_submit_transfer_count > 0, (
                "Recurrence should have generated at least one transfer"
            )

            # -- Second submission: duplicate name, handled gracefully --
            resp2 = auth_client.post("/transfers", data=form_data)
            assert resp2.status_code == 302, (
                f"Second submit returned {resp2.status_code}, expected 302 "
                "(not 500 -- IntegrityError should be caught)"
            )

            # Verify redirect target.
            location = resp2.headers.get("Location", "")
            assert "/transfers" in location, (
                f"Redirect went to {location}, expected /transfers list"
            )

            # Follow redirect and verify flash warning.
            resp3 = auth_client.get(location)
            assert resp3.status_code == 200
            assert b"already exists" in resp3.data, (
                "Flash warning about duplicate name not found in response"
            )

            # -- Verify database state: exactly 1 template, no orphans --
            template_count = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
                name="Duplicate Transfer",
            ).count()
            assert template_count == 1, (
                f"Expected exactly 1 template, found {template_count}"
            )

            # Transfer count unchanged (second submit was rolled back).
            final_transfer_count = db.session.query(Transfer).filter_by(
                transfer_template_id=template.id,
            ).count()
            assert final_transfer_count == first_submit_transfer_count, (
                f"Transfer count changed from {first_submit_transfer_count} "
                f"to {final_transfer_count} after rolled-back duplicate"
            )

            # RecurrenceRule count: exactly 1 for this template.
            rule_count = db.session.query(RecurrenceRule).filter_by(
                id=template.recurrence_rule_id,
            ).count()
            assert rule_count == 1, (
                f"Expected 1 recurrence rule, found {rule_count}"
            )

            # Session health check: a subsequent query must not raise
            # InvalidRequestError (proves rollback was effective).
            total_templates = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert total_templates >= 1


class TestTemplateUpdate:
    """Tests for GET/POST /transfers/<id>/edit and /delete and /reactivate."""

    def test_edit_template_form(self, app, auth_client, seed_user, seed_periods):
        """GET /transfers/<id>/edit renders the edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get(f"/transfers/{template.id}/edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data

    def test_update_template(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers/<id> updates the template."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)

            response = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Updated Savings",
                "default_amount": "300.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_pattern": "every_period",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(template)
            assert template.default_amount == Decimal("300.00")

    def test_delete_template(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers/<id>/delete deactivates the template and soft-deletes transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods, savings, template)

            response = auth_client.post(
                f"/transfers/{template.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deactivated" in response.data

            db.session.refresh(template)
            assert template.is_active is False

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_reactivate_template(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers/<id>/reactivate restores the template and its transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods, savings, template)

            # Deactivate first.
            template.is_active = False
            xfer.is_deleted = True
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}/reactivate",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"reactivated" in response.data

            db.session.refresh(template)
            assert template.is_active is True

            db.session.refresh(xfer)
            assert xfer.is_deleted is False

    def test_update_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id> for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Recurring transfer not found." in response.data

    def test_delete_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id>/delete for another user's template redirects."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Recurring transfer not found." in response.data


# ── Grid Cell Routes ───────────────────────────────────────────────


class TestGridCells:
    """Tests for grid cell HTMX partial endpoints."""

    def test_get_cell(self, app, auth_client, seed_user, seed_periods):
        """GET /transfers/cell/<id> returns the cell partial."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            response = auth_client.get(f"/transfers/cell/{xfer.id}")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b"200" in response.data

    def test_get_quick_edit(self, app, auth_client, seed_user, seed_periods):
        """GET /transfers/quick-edit/<id> returns the quick-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            response = auth_client.get(f"/transfers/quick-edit/{xfer.id}")

            assert response.status_code == 200
            assert b'name="amount"' in response.data
            assert b"200" in response.data

    def test_get_full_edit(self, app, auth_client, seed_user, seed_periods):
        """GET /transfers/<id>/full-edit returns the full-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            response = auth_client.get(f"/transfers/{xfer.id}/full-edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b'name="amount"' in response.data

    def test_get_cell_other_users_transfer(self, app, auth_client, seed_user):
        """GET /transfers/cell/<id> for another user's transfer returns 404.

        Read-path IDOR: response must not leak the other user's transfer data.
        """
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.get(f"/transfers/cell/{other['transfer'].id}")

            assert response.status_code == 404
            # Verify no leakage of the other user's transfer data.
            assert b"Other Transfer" not in response.data
            assert b"100.00" not in response.data


# ── Transfer Instance Operations ──────────────────────────────────


class TestTransferInstance:
    """Tests for transfer update, mark-done, cancel, and delete."""

    def test_update_transfer_amount(self, app, auth_client, seed_user, seed_periods):
        """PATCH /transfers/instance/<id> updates the transfer amount."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00"},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("250.00")

    def test_mark_done(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers/instance/<id>/mark-done sets status to done."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "done"

    def test_cancel_transfer(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers/instance/<id>/cancel sets status to cancelled."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "cancelled"

    def test_delete_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods):
        """DELETE /transfers/instance/<id> hard-deletes an ad-hoc transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Ad-hoc transfer (no template).
            xfer = _create_transfer(seed_user, seed_periods, savings, template=None)
            xfer_id = xfer.id

            response = auth_client.delete(f"/transfers/instance/{xfer_id}")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Should be hard-deleted.
            assert db.session.get(Transfer, xfer_id) is None

    def test_delete_template_transfer_soft_deletes(
        self, app, auth_client, seed_user, seed_periods
    ):
        """DELETE /transfers/instance/<id> soft-deletes a template transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods, savings, template)

            response = auth_client.delete(f"/transfers/instance/{xfer.id}")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_template_transfer_override_on_amount_change(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Updating amount on a template transfer sets is_override=True."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods, savings, template)
            assert xfer.is_override is False

            auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "999.00"},
            )

            db.session.refresh(xfer)
            assert xfer.is_override is True

    def test_cancelled_transfer_effective_amount_zero(
        self, app, auth_client, seed_user, seed_periods
    ):
        """A cancelled transfer has effective_amount of Decimal('0')."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            db.session.refresh(xfer)
            assert xfer.effective_amount == Decimal("0")

    def test_update_other_users_transfer(self, app, auth_client, seed_user):
        """PATCH /transfers/instance/<id> for another user's transfer returns 404.

        IDOR write-path (HIGH priority): must prove the transfer was not modified.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            target = other["transfer"]
            orig_amount = target.amount
            orig_name = target.name

            response = auth_client.patch(
                f"/transfers/instance/{target.id}",
                data={"amount": "9999.00"},
            )

            assert response.status_code == 404

            # Prove no state change occurred.
            db.session.expire_all()
            db.session.refresh(target)
            assert target.amount == orig_amount, (
                "IDOR attack modified victim's transfer amount!"
            )
            assert target.name == orig_name, (
                "IDOR attack modified victim's transfer name!"
            )


# ── Ad-Hoc Creation ───────────────────────────────────────────────


class TestAdHoc:
    """Tests for POST /transfers/ad-hoc."""

    def test_create_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods):
        """POST /transfers/ad-hoc creates a transfer and returns 201."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Quick Transfer",
            })

            assert response.status_code == 201
            assert response.headers.get("HX-Trigger") == "balanceChanged"

    def test_create_ad_hoc_validation_error(self, app, auth_client, seed_user):
        """POST /transfers/ad-hoc with missing fields returns 400."""
        with app.app_context():
            response = auth_client.post("/transfers/ad-hoc", data={
                "name": "Bad Transfer",
            })

            assert response.status_code == 400
            body = response.get_json()
            assert "errors" in body

    def test_create_ad_hoc_other_users_period(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transfers/ad-hoc with another user's period returns 404.

        Create-path IDOR: must verify no transfer was created in the other
        user's period.
        """
        with app.app_context():
            other = _create_other_user_with_template()
            savings = _create_savings_account(seed_user)

            # Use other user's period.
            from app.models.pay_period import PayPeriod
            other_period = (
                db.session.query(PayPeriod)
                .filter_by(user_id=other["user"].id)
                .first()
            )

            # Count transfers in the other user's period before the request.
            count_before = db.session.query(Transfer).filter_by(
                pay_period_id=other_period.id,
            ).count()

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": other_period.id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
            })

            assert response.status_code == 404

            # Prove no transfer was created.
            db.session.expire_all()
            count_after = db.session.query(Transfer).filter_by(
                pay_period_id=other_period.id,
            ).count()
            assert count_after == count_before, (
                "IDOR attack created a transfer in victim's period!"
            )

    def test_create_ad_hoc_double_submit(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transfers/ad-hoc twice succeeds both times (no unique constraint on ad-hoc).

        Both submissions should create a transfer, resulting in exactly 2
        transfers named 'Double Transfer' in the period.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            data = {
                "pay_period_id": seed_periods[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Double Transfer",
            }

            response1 = auth_client.post("/transfers/ad-hoc", data=data)
            assert response1.status_code == 201

            response2 = auth_client.post("/transfers/ad-hoc", data=data)
            assert response2.status_code == 201

            # Verify exactly 2 transfers were created.
            db.session.expire_all()
            count = db.session.query(Transfer).filter_by(
                pay_period_id=seed_periods[0].id,
                name="Double Transfer",
            ).count()
            assert count == 2, (
                f"Expected exactly 2 ad-hoc transfers, found {count}"
            )


# ── Helpers for Negative-Path Tests ───────────────────────────────


def _create_second_user_transfer(second_user_data):
    """Create a transfer for the second_user fixture (IDOR testing).

    Creates a savings account, pay periods, and a transfer instance
    for the second user.

    Args:
        second_user_data: Dict from the second_user conftest fixture.

    Returns:
        Transfer: the created transfer.
    """
    from datetime import date as _date  # pylint: disable=import-outside-toplevel
    from app.services import pay_period_service  # pylint: disable=import-outside-toplevel

    savings_type = db.session.query(AccountType).filter_by(name="savings").one()
    savings = Account(
        user_id=second_user_data["user"].id,
        account_type_id=savings_type.id,
        name="Other Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(savings)
    db.session.flush()

    periods = pay_period_service.generate_pay_periods(
        user_id=second_user_data["user"].id,
        start_date=_date(2026, 1, 2),
        num_periods=3,
        cadence_days=14,
    )
    db.session.flush()

    projected = db.session.query(Status).filter_by(name="projected").one()
    xfer = Transfer(
        user_id=second_user_data["user"].id,
        from_account_id=second_user_data["account"].id,
        to_account_id=savings.id,
        pay_period_id=periods[0].id,
        scenario_id=second_user_data["scenario"].id,
        status_id=projected.id,
        name="Other Transfer",
        amount=Decimal("100.00"),
    )
    db.session.add(xfer)
    db.session.commit()
    return xfer


# ── Negative Paths ────────────────────────────────────────────────


class TestTransferNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, idempotent ops, validation."""

    def test_update_nonexistent_transfer_instance(self, app, auth_client, seed_user):
        """PATCH /transfers/instance/999999 for a nonexistent transfer returns 404."""
        with app.app_context():
            resp = auth_client.patch(
                "/transfers/instance/999999",
                data={"amount": "100.00"},
            )

            assert resp.status_code == 404

    def test_mark_done_already_done_transfer(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transfers/instance/<id>/mark-done on an already-done transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            # Set to done first.
            done_status = db.session.query(Status).filter_by(name="done").one()
            xfer.status_id = done_status.id
            db.session.commit()

            # Mark done again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            # Route does not guard against double mark-done; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "done"

    def test_cancel_already_cancelled_transfer(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transfers/instance/<id>/cancel on an already-cancelled transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods, savings)

            # Cancel first.
            cancelled_status = db.session.query(Status).filter_by(name="cancelled").one()
            xfer.status_id = cancelled_status.id
            db.session.commit()

            # Cancel again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            # Route does not guard against double cancel; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "cancelled"

    def test_quick_edit_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """GET /transfers/quick-edit/<id> for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)

            resp = auth_client.get(f"/transfers/quick-edit/{other_xfer.id}")

            assert resp.status_code == 404
            # No transfer data should leak.
            assert b"Other Transfer" not in resp.data
            assert b"100.00" not in resp.data

    def test_full_edit_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """GET /transfers/<id>/full-edit for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)

            resp = auth_client.get(f"/transfers/{other_xfer.id}/full-edit")

            assert resp.status_code == 404

    def test_mark_done_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /transfers/instance/<id>/mark-done for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)
            original_status_id = other_xfer.status_id

            resp = auth_client.post(
                f"/transfers/instance/{other_xfer.id}/mark-done"
            )

            assert resp.status_code == 404

            # Verify DB state unchanged.
            db.session.expire_all()
            refreshed = db.session.get(Transfer, other_xfer.id)
            assert refreshed.status_id == original_status_id

    def test_cancel_other_users_transfer_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /transfers/instance/<id>/cancel for another user's transfer returns 404."""
        with app.app_context():
            other_xfer = _create_second_user_transfer(second_user)
            original_status_id = other_xfer.status_id

            resp = auth_client.post(
                f"/transfers/instance/{other_xfer.id}/cancel"
            )

            assert resp.status_code == 404

            # Verify DB state unchanged.
            db.session.expire_all()
            refreshed = db.session.get(Transfer, other_xfer.id)
            assert refreshed.status_id == original_status_id

    def test_create_template_with_missing_accounts(self, app, auth_client, seed_user):
        """POST /transfers with empty from/to accounts fails schema validation."""
        with app.app_context():
            resp = auth_client.post("/transfers", data={
                "name": "Bad Transfer",
                "default_amount": "100.00",
                "from_account_id": "",
                "to_account_id": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no template was created.
            count = db.session.query(TransferTemplate).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_ad_hoc_with_zero_amount(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transfers/ad-hoc with amount=0.00 fails validation (must be > 0)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "0.00",
                "scenario_id": seed_user["scenario"].id,
            })

            # TransferCreateSchema requires amount > 0 (min_inclusive=False).
            assert resp.status_code == 400
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_ad_hoc_with_negative_amount(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /transfers/ad-hoc with negative amount fails schema validation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "-100.00",
                "scenario_id": seed_user["scenario"].id,
            })

            assert resp.status_code == 400
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0
