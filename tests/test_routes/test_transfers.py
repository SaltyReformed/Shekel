"""
Shekel Budget App -- Transfer Route Tests

Tests for transfer template CRUD, grid cell endpoints, transfer instance
operations, and ad-hoc transfer creation (§2.3 of the test plan).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.recurrence_rule import RecurrenceRule
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.ref import AccountType, RecurrencePattern, Status
from app.services import transfer_service
from app.services.auth_service import hash_password
from app.services import account_service


def _create_savings_account(seed_user):
    """Helper: create a second (savings) account for the test user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _create_template(seed_user, savings_acct, with_rule=True):
    """Helper: create a transfer template with optional recurrence rule."""
    rule = None
    if with_rule:
        every_period = db.session.query(RecurrencePattern).filter_by(name="Every Period").one()
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


def _create_transfer(
    seed_user, seed_periods_today, savings_acct,
    template=None, amount=Decimal("200.00"), name="Monthly Savings",
):
    """Helper: create a transfer with shadow transactions via the service.

    ``amount`` and ``name`` are parameterised so callers that need
    multiple ad-hoc transfers in the same period can distinguish
    them and avoid the F-050 / C-22 partial unique index
    ``uq_transfers_adhoc_dedupe`` (which legitimately rejects two
    active ad-hoc rows with identical parameters).
    """
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            transfer_template_id=template.id if template else None,
            name=name,
        ),
    )
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


    # Bootstrap pay period (E-19, Commit 3): the
    # account_service factory requires the user to have at
    # least one pay period to anchor against.
    from datetime import date as _date, timedelta as _td
    from app.models.pay_period import PayPeriod as _PayPeriod
    _bootstrap = _PayPeriod(
        user_id=other_user.id,
        start_date=_date(2024, 1, 5),
        end_date=_date(2024, 1, 5) + _td(days=13),
        period_index=0,
    )
    db.session.add(_bootstrap)
    db.session.flush()
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

    checking = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add_all([checking, savings])

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)

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

    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=other_user.id,
            from_account_id=checking.id,
            to_account_id=savings.id,
            pay_period_id=periods[0].id,
            scenario_id=scenario.id,
            amount=Decimal("100.00"),
            status_id=projected.id,
            category_id=category.id,
            transfer_template_id=template.id,
            name="Other Transfer",
        ),
    )
    db.session.commit()

    return {
        "user": other_user,
        "template": template,
        "transfer": xfer,
    }


# ── Template Management ───────────────────────────────────────────


class TestTemplateList:
    """Tests for GET /transfers and GET /transfers/new."""

    def test_list_templates(self, app, auth_client, seed_user, seed_periods_today):
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

    def test_create_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers creates a template with recurrence and generates transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()

            response = auth_client.post("/transfers", data={
                "name": "Weekly Savings",
                "default_amount": "150.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_pattern": str(every_period.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
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
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_template_double_submit(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers twice with the same name returns a flash warning
        on the second attempt instead of a 500 error, and creates exactly
        one template in the database."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()
            form_data = {
                "name": "Duplicate Transfer",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_pattern": str(every_period.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
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
    """Tests for GET/POST /transfers/<id>/edit and /archive and /unarchive."""

    def test_edit_template_form(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/<id>/edit renders the edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            response = auth_client.get(f"/transfers/{template.id}/edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data

    def test_update_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id> updates the template."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            every_period = db.session.query(RecurrencePattern).filter_by(
                name="Every Period"
            ).one()

            response = auth_client.post(f"/transfers/{template.id}", data={
                "name": "Updated Savings",
                "default_amount": "300.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_pattern": str(every_period.id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"updated" in response.data

            db.session.refresh(template)
            assert template.default_amount == Decimal("300.00")

    def test_archive_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id>/archive archives the template and soft-deletes transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            response = auth_client.post(
                f"/transfers/{template.id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data

            db.session.refresh(template)
            assert template.is_active is False

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_unarchive_template(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/<id>/unarchive restores the template and its transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            # Deactivate first.
            template.is_active = False
            xfer.is_deleted = True
            db.session.commit()

            response = auth_client.post(
                f"/transfers/{template.id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"unarchived" in response.data

            db.session.refresh(template)
            assert template.is_active is True

            db.session.refresh(xfer)
            assert xfer.is_deleted is False

    def test_update_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id> for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_archive_other_users_template_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /transfers/<id>/archive for another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()

            response = auth_client.post(
                f"/transfers/{other['template'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 404


# ── Grid Cell Routes ───────────────────────────────────────────────


class TestGridCells:
    """Tests for grid cell HTMX partial endpoints."""

    def test_get_cell(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/cell/<id> returns the cell partial."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/cell/{xfer.id}")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b"200" in response.data

    def test_get_quick_edit(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/quick-edit/<id> returns the quick-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/quick-edit/{xfer.id}")

            assert response.status_code == 200
            assert b'name="amount"' in response.data
            assert b"200" in response.data

    def test_get_full_edit(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transfers/<id>/full-edit returns the full-edit form."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.get(f"/transfers/{xfer.id}/full-edit")

            assert response.status_code == 200
            assert b"Monthly Savings" in response.data
            assert b'name="amount"' in response.data

    def test_full_edit_renders_due_date_input_for_transfer_shadow(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/<shadow>/full-edit renders an editable due_date field.

        The transfer here has no due date, yet the input renders (empty) so the
        user can add one; get_full_edit detects the shadow and returns the
        transfer edit form, which posts to the transfer update route and
        mirrors the value to both shadows.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .first()
            )

            response = auth_client.get(f"/transactions/{shadow.id}/full-edit")

            assert response.status_code == 200
            assert b'name="due_date"' in response.data
            assert b'type="date"' in response.data

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

    def test_update_transfer_amount(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transfers/instance/<id> updates the transfer amount."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00"},
            )

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("250.00")

    def test_update_transfer_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH /transfers/instance/<id> with due_date updates parent and shadows."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"due_date": "2026-04-22"},
            )

            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.due_date == date(2026, 4, 22)
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.due_date == date(2026, 4, 22) for s in shadows)

    def test_update_transfer_blank_due_date_does_not_clobber(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A blank due_date on the edit form leaves an existing due date intact.

        The empty-string due_date is stripped by TransferUpdateSchema's
        strip_empty_strings pre_load, so saving another field with an
        untouched (empty) date input cannot null out the stored due date.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, due_date=date(2026, 5, 1),
            )
            db.session.commit()

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "300.00", "due_date": ""},
            )

            assert response.status_code == 200
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("300.00")
            assert xfer.due_date == date(2026, 5, 1)

    def test_mark_done(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/instance/<id>/mark-done sets status to done."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

    def test_cancel_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/instance/<id>/cancel sets status to cancelled."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_delete_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transfers/instance/<id> hard-deletes an ad-hoc transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Ad-hoc transfer (no template).
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template=None)
            xfer_id = xfer.id

            response = auth_client.delete(f"/transfers/instance/{xfer_id}")

            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Should be hard-deleted.
            assert db.session.get(Transfer, xfer_id) is None

    def test_delete_template_transfer_soft_deletes(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """DELETE /transfers/instance/<id> soft-deletes a template transfer."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            response = auth_client.delete(f"/transfers/instance/{xfer.id}")

            assert response.status_code == 200

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

    def test_template_transfer_override_on_amount_change(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Updating amount on a template transfer sets is_override=True."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            assert xfer.is_override is False

            auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "999.00"},
            )

            db.session.refresh(xfer)
            assert xfer.is_override is True

    def test_cancelled_transfer_effective_amount_zero(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """A cancelled transfer has effective_amount of Decimal('0')."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

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

    def test_create_ad_hoc_transfer(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transfers/ad-hoc creates a transfer and returns 201."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Quick Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert response.status_code == 201
            assert response.headers.get("HX-Trigger") == "balanceChanged"

    def test_create_ad_hoc_transfer_with_due_date(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with due_date sets it on the parent and both shadows."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            response = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Dated Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "due_date": "2026-03-15",
            })

            assert response.status_code == 201
            xfer = (
                db.session.query(Transfer)
                .filter_by(name="Dated Transfer")
                .one()
            )
            assert xfer.due_date == date(2026, 3, 15)
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.due_date == date(2026, 3, 15) for s in shadows)

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
        self, app, auth_client, seed_user, seed_periods_today
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
                "category_id": str(seed_user["categories"]["Rent"].id),
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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc twice with identical params returns idempotent success.

        F-050 / C-22: the partial unique index
        ``uq_transfers_adhoc_dedupe`` on (user_id, from_account_id,
        to_account_id, amount, pay_period_id, scenario_id) rejects the
        second active ad-hoc transfer with identical parameters.  The
        route translates the IntegrityError into idempotent 201 +
        cell HTML so the user sees the transfer they intended to
        create regardless of which request reached the database
        first.  After two identical submissions the period must
        contain exactly one active ad-hoc transfer (and exactly two
        active shadow transactions, not four).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            data = {
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "50.00",
                "scenario_id": seed_user["scenario"].id,
                "name": "Double Transfer",
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            response1 = auth_client.post("/transfers/ad-hoc", data=data)
            assert response1.status_code == 201

            response2 = auth_client.post("/transfers/ad-hoc", data=data)
            # Idempotent success: the second request returns 201 too,
            # but the body references the SAME transfer the first one
            # produced (no new row was inserted).
            assert response2.status_code == 201
            assert response2.headers.get("HX-Trigger") == "balanceChanged"

            # Verify exactly 1 active ad-hoc transfer exists.
            db.session.expire_all()
            transfers = (
                db.session.query(Transfer)
                .filter_by(
                    pay_period_id=seed_periods_today[0].id,
                    user_id=seed_user["user"].id,
                    is_deleted=False,
                )
                .filter(Transfer.transfer_template_id.is_(None))
                .filter_by(amount=Decimal("50.00"))
                .all()
            )
            assert len(transfers) == 1, (
                f"Expected exactly 1 active ad-hoc transfer after "
                f"double-submit, found {len(transfers)}"
            )
            # Verify exactly 2 active shadow transactions (not 4 --
            # invariant 1 still holds with the new constraint).
            shadow_count = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfers[0].id, is_deleted=False)
                .count()
            )
            assert shadow_count == 2, (
                f"Expected exactly 2 active shadows for the deduped "
                f"transfer, found {shadow_count}"
            )

    def test_create_ad_hoc_different_amount_allowed(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Two ad-hoc transfers with different amounts both succeed.

        F-050 / C-22: the unique constraint includes ``amount`` so
        a $50 transfer and a $100 transfer between the same accounts
        in the same period are treated as different ad-hoc rows --
        the user legitimately split a payment, the constraint must
        not block it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            base = {
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            }

            r1 = auth_client.post(
                "/transfers/ad-hoc", data={**base, "amount": "50.00"},
            )
            r2 = auth_client.post(
                "/transfers/ad-hoc", data={**base, "amount": "100.00"},
            )

            assert r1.status_code == 201
            assert r2.status_code == 201

            db.session.expire_all()
            count = (
                db.session.query(Transfer)
                .filter_by(
                    pay_period_id=seed_periods_today[0].id,
                    user_id=seed_user["user"].id,
                    is_deleted=False,
                )
                .filter(Transfer.transfer_template_id.is_(None))
                .count()
            )
            assert count == 2, (
                f"Expected 2 distinct ad-hoc transfers, found {count}"
            )

    def test_mark_done_transfer_sets_paid_at_on_shadows(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/mark-done sets paid_at on both shadows.

        F-048 / C-22: parity with ``transactions.mark_done``.
        Settling a transfer must record when it was settled so
        ``Transaction.days_paid_before_due`` analytics, the
        dashboard's "paid on time" indicator, and any downstream
        report that joins on ``paid_at`` work.  Both shadow
        transactions are checked because the parent transfer has
        no ``paid_at`` column of its own.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            response = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done"
            )
            assert response.status_code == 200

            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2
            for shadow in shadows:
                assert shadow.status.name == "Paid"
                assert shadow.paid_at is not None, (
                    f"Shadow {shadow.id} has NULL paid_at after mark-done; "
                    f"the F-048 parity gap is back."
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

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user_data["user"].id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("0"),
        ),
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

    projected = db.session.query(Status).filter_by(name="Projected").one()
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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/mark-done on an already-done transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            # Set to done first.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            xfer.status_id = done_status.id
            db.session.commit()

            # Mark done again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/mark-done")

            # Route does not guard against double mark-done; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"

    def test_cancel_already_cancelled_transfer(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/instance/<id>/cancel on an already-cancelled transfer is idempotent."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            # Cancel first.
            cancelled_status = db.session.query(Status).filter_by(name="Cancelled").one()
            xfer.status_id = cancelled_status.id
            db.session.commit()

            # Cancel again.
            resp = auth_client.post(f"/transfers/instance/{xfer.id}/cancel")

            # Route does not guard against double cancel; it sets
            # the same status again. This is idempotent behavior.
            assert resp.status_code == 200

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with amount=0.00 fails validation (must be > 0)."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "0.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
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
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transfers/ad-hoc with negative amount fails schema validation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            resp = auth_client.post("/transfers/ad-hoc", data={
                "pay_period_id": seed_periods_today[0].id,
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "amount": "-100.00",
                "scenario_id": seed_user["scenario"].id,
                "category_id": str(seed_user["categories"]["Rent"].id),
            })

            assert resp.status_code == 400
            body = resp.get_json()
            assert "errors" in body

            # Verify no transfer was created.
            count = db.session.query(Transfer).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0


# ── Shadow Context Response Tests (H1 fix) ────────────────────────


def _get_expense_shadow(xfer):
    """Return the expense-side shadow transaction for a transfer."""
    from app.models.ref import TransactionType  # pylint: disable=import-outside-toplevel
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
    return (
        db.session.query(Transaction)
        .filter_by(transfer_id=xfer.id, transaction_type_id=expense_type.id)
        .one()
    )


class TestShadowContextResponse:
    """Verify that transfer route handlers render _transaction_cell.html
    (not _transfer_cell.html) when the request includes source_txn_id,
    indicating the form was opened from a shadow transaction cell in the grid.

    Fixes H1, L2, L3 from transfer_rework_verification.md.
    """

    def test_update_from_shadow_renders_transaction_cell(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with source_txn_id renders _transaction_cell.html content.

        When the transfer full edit popover is opened from a shadow
        transaction cell in the grid, the response must contain the
        transaction cell template (with ``txn-cell-`` IDs and transaction
        HTMX routes) so the cell remains interactive after the update.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = _get_expense_shadow(xfer)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "300.00", "source_txn_id": str(shadow.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Must render _transaction_cell.html (has transaction routes).
            assert "transactions.get_quick_edit" in html or f"txn_id={shadow.id}" in html or "txn-cell" in html
            # Must NOT render _transfer_cell.html (has transfer routes).
            assert "xfer-cell-" not in html
            assert "transfers/quick-edit" not in html.replace("transfers/instance", "")

            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            # Verify the transfer amount was actually updated.
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("300.00")

            # Verify the shadow amount was synced.
            db.session.refresh(shadow)
            assert shadow.estimated_amount == Decimal("300.00")

    def test_mark_done_from_shadow_renders_transaction_cell_with_grid_refresh(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST mark-done with source_txn_id renders _transaction_cell.html
        and triggers gridRefresh (not balanceChanged).

        Status changes affect subtotal rows and cell visibility, so the
        transfer route must match the transaction route guard pattern of
        triggering gridRefresh when called from a shadow cell context.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = _get_expense_shadow(xfer)

            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/mark-done",
                data={"source_txn_id": str(shadow.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Transaction cell, not transfer cell.
            assert "xfer-cell-" not in html

            # Must trigger gridRefresh for status changes.
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            # Verify the transfer and both shadows are done.
            db.session.refresh(xfer)
            assert xfer.status.name == "Paid"
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert all(s.status.name == "Paid" for s in shadows)

    def test_cancel_from_shadow_renders_transaction_cell_with_grid_refresh(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST cancel with source_txn_id renders _transaction_cell.html
        and triggers gridRefresh.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            shadow = _get_expense_shadow(xfer)

            resp = auth_client.post(
                f"/transfers/instance/{xfer.id}/cancel",
                data={"source_txn_id": str(shadow.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            assert "xfer-cell-" not in html
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            db.session.refresh(xfer)
            assert xfer.status.name == "Cancelled"

    def test_update_without_source_txn_id_renders_transfer_cell(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH without source_txn_id renders _transfer_cell.html (regression).

        When the transfer management page (not the grid) submits an
        update, there is no source_txn_id.  The response must render
        the transfer cell template with ``xfer-cell-`` IDs as before.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "350.00"},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Must render _transfer_cell.html (management page context).
            assert f"xfer-cell-{xfer.id}" in html or "xfer-cell-" in html

            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.refresh(xfer)
            assert xfer.amount == Decimal("350.00")

    def test_invalid_source_txn_id_falls_back_gracefully(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with nonexistent source_txn_id falls back to transfer cell.

        If source_txn_id is invalid (e.g., tampered or stale), the
        handler must not crash.  It falls back to the transfer cell
        template as a safe default.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "400.00", "source_txn_id": "999999"},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Falls back to transfer cell (safe default).
            assert "xfer-cell-" in html

            # Data still updated correctly.
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("400.00")

    def test_mismatched_source_txn_id_falls_back_gracefully(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """PATCH with source_txn_id from a different transfer falls back.

        If source_txn_id points to a shadow of a DIFFERENT transfer,
        the handler must not render the wrong transaction cell.  It
        falls back to the transfer cell template.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            # Distinct amounts/names so the F-050 partial unique
            # index ``uq_transfers_adhoc_dedupe`` does not collapse
            # the two ad-hoc transfers into one (they share user,
            # accounts, period, and scenario).
            xfer_a = _create_transfer(
                seed_user, seed_periods_today, savings,
                amount=Decimal("200.00"), name="Transfer A",
            )
            xfer_b = _create_transfer(
                seed_user, seed_periods_today, savings,
                amount=Decimal("250.00"), name="Transfer B",
            )

            # Get a shadow from transfer B.
            shadow_b = _get_expense_shadow(xfer_b)

            # Send it with transfer A's update.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_a.id}",
                data={"amount": "450.00", "source_txn_id": str(shadow_b.id)},
            )

            assert resp.status_code == 200
            html = resp.data.decode()

            # Falls back to transfer cell (mismatch detected).
            assert "xfer-cell-" in html

            # Transfer A still updated correctly.
            db.session.refresh(xfer_a)
            assert xfer_a.amount == Decimal("450.00")


# ── Unarchive Service Integration Tests (M1) ─────────────────────


class TestUnarchiveUsesService:
    """Verify that unarchive_transfer_template delegates to
    transfer_service.restore_transfer instead of directly manipulating
    ORM objects, ensuring all transfer mutations flow through the
    service layer.
    """

    def test_unarchive_restores_via_service_with_invariant_correction(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """Verify that the unarchive route uses the transfer service to
        restore soft-deleted transfers, including the service's invariant
        correction logic.  An intentionally drifted shadow amount should
        be corrected on unarchive, proving the service was called.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            xfer_id = xfer.id

            # Soft-delete the transfer and shadows via the service.
            transfer_service.delete_transfer(xfer_id, seed_user["user"].id, soft=True)
            db.session.commit()

            # Drift one shadow's amount while soft-deleted.
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .first()
            )
            shadow.estimated_amount = Decimal("999.00")
            db.session.commit()

            # Deactivate the template to match the route's expectations.
            template.is_active = False
            db.session.commit()

            # Unarchive via the route.
            response = auth_client.post(
                f"/transfers/{template.id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200

            # Transfer restored.
            db.session.refresh(xfer)
            assert xfer.is_deleted is False

            # Both shadows restored AND the drifted amount corrected.
            # This proves the service's invariant correction ran.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer_id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.is_deleted is False
                assert s.estimated_amount == Decimal("200.00")


# ── One-Time Transfer Tests ────────────────────────────────────────────


class TestOneTimeTransfer:
    """Tests for one-time transfer creation via the template form.

    One-time transfers can be created two ways:
      1. Pattern dropdown set to "None (one-time / manual)" (no rule).
      2. Pattern dropdown set to "Once" (ONCE rule).

    Both paths must create a Transfer with two shadow transactions when
    a start_period_id is provided.  This was a known bug (design doc
    section 1.4) where the recurrence engine returned [] for ONCE and
    the route skipped transfer creation entirely for no-pattern.
    """

    def test_once_pattern_creates_shadows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers with the ONCE recurrence pattern creates a
        template AND a single Transfer with exactly two shadow transactions.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            once = db.session.query(RecurrencePattern).filter_by(
                name="Once"
            ).one()

            response = auth_client.post("/transfers", data={
                "name": "Once Payment",
                "default_amount": "500.00",
                "from_account_id": seed_user["account"].id,
                "to_account_id": savings.id,
                "recurrence_pattern": str(once.id),
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data.lower()

            # Template was created with a ONCE recurrence rule.
            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="Once Payment",
                )
                .one()
            )
            assert tmpl.recurrence_rule is not None
            assert tmpl.recurrence_rule.pattern_id == once.id

            # Transfer was created via the service.
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            assert xfer.amount == Decimal("500.00")
            assert xfer.pay_period_id == seed_periods_today[1].id

            # Exactly two shadow transactions exist.
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )
            assert len(shadows) == 2

            types = {s.transaction_type.name for s in shadows}
            assert types == {"Expense", "Income"}

    def test_once_pattern_shadow_accounts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """ONCE transfer shadows are linked to the correct accounts:
        expense shadow on from_account, income shadow on to_account.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            checking_id = seed_user["account"].id
            once = db.session.query(RecurrencePattern).filter_by(
                name="Once"
            ).one()

            auth_client.post("/transfers", data={
                "name": "Account Check",
                "default_amount": "300.00",
                "from_account_id": str(checking_id),
                "to_account_id": str(savings.id),
                "recurrence_pattern": str(once.id),
                "start_period_id": str(seed_periods_today[0].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            tmpl = (
                db.session.query(TransferTemplate)
                .filter_by(name="Account Check")
                .one()
            )
            xfer = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=tmpl.id)
                .one()
            )
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            )

            expense_shadow = [
                s for s in shadows if s.transaction_type.name == "Expense"
            ][0]
            income_shadow = [
                s for s in shadows if s.transaction_type.name == "Income"
            ][0]

            # Expense drains the from_account (checking).
            assert expense_shadow.account_id == checking_id
            # Income fills the to_account (savings).
            assert income_shadow.account_id == savings.id

    def test_once_pattern_balance_impact(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """ONCE transfer shadow transactions affect balance calculations.

        The checking balance should decrease and savings balance should
        increase by the transfer amount.
        """
        from app.services import balance_calculator  # pylint: disable=import-outside-toplevel

        with app.app_context():
            savings = _create_savings_account(seed_user)
            savings.current_anchor_period_id = seed_periods_today[0].id
            savings.current_anchor_balance = Decimal("0.00")
            db.session.commit()

            once = db.session.query(RecurrencePattern).filter_by(
                name="Once"
            ).one()

            auth_client.post("/transfers", data={
                "name": "Balance Test",
                "default_amount": "250.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "recurrence_pattern": str(once.id),
                "start_period_id": str(seed_periods_today[1].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
            }, follow_redirects=True)

            # Get shadow transactions for checking account.
            checking_shadows = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == seed_user["account"].id,
                    Transaction.transfer_id.isnot(None),
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )
            checking_balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("1000.00"),
                anchor_period_id=seed_periods_today[0].id,
                periods=seed_periods_today[:3],
                transactions=checking_shadows,
            )
            # Checking decreased by 250 in period 2.
            assert checking_balances[seed_periods_today[1].id] == Decimal("750.00")

            # Get shadow transactions for savings account.
            savings_shadows = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == savings.id,
                    Transaction.transfer_id.isnot(None),
                    Transaction.is_deleted.is_(False),
                )
                .all()
            )
            savings_balances, _ = balance_calculator.calculate_balances(
                anchor_balance=Decimal("0.00"),
                anchor_period_id=seed_periods_today[0].id,
                periods=seed_periods_today[:3],
                transactions=savings_shadows,
            )
            # Savings increased by 250 in period 2.
            assert savings_balances[seed_periods_today[1].id] == Decimal("250.00")

    def test_one_time_transfer_idor_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /transfers with another user's period is rejected."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            once = db.session.query(RecurrencePattern).filter_by(
                name="Once"
            ).one()

            response = auth_client.post("/transfers", data={
                "name": "IDOR Attempt",
                "default_amount": "100.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_pattern": str(once.id),
                # Use second user's period.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid pay period" in response.data

            # No transfer was created.
            xfer_count = (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .count()
            )
            assert xfer_count == 0


# ── Hard Delete Tests (5A.5-3) ─────────────────────────────────────


class TestTransferTemplateHardDelete:
    """Tests for POST /transfers/<id>/hard-delete (permanent deletion).

    These tests verify transfer invariant compliance: shadow transactions
    must never be orphaned, and all deletions must flow through the
    transfer service.
    """

    def test_hard_delete_transfer_template_no_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-17: Template with only Projected transfers is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)

            template_id = template.id
            xfer_id = xfer.id

            # Verify shadows exist before deletion.
            shadow_count_before = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert shadow_count_before == 2

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template is gone.
            assert db.session.get(TransferTemplate, template_id) is None

            # Transfer is gone.
            assert db.session.get(Transfer, xfer_id) is None

            # Shadow transactions are gone.
            shadow_count_after = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert shadow_count_after == 0

    def test_hard_delete_transfer_template_no_transfers(
        self, app, auth_client, seed_user,
    ):
        """Template with zero transfers ever generated is permanently deleted."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            template_id = template.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(TransferTemplate, template_id) is None

    def test_hard_delete_transfer_template_with_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-18: Template with Paid transfer is blocked and archived instead."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            # Create two transfers: one Projected, one Paid.
            xfer_projected = _create_transfer(
                seed_user, seed_periods_today, savings, template,
            )

            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            xfer_paid = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[1].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("200.00"),
                    status_id=paid_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Monthly Savings",
                ),
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data
            assert b"archived instead" in resp.data

            # Template still exists but is archived.
            db.session.refresh(template)
            assert template.is_active is False

            # Paid transfer and its shadows are untouched.
            db.session.refresh(xfer_paid)
            assert xfer_paid.is_deleted is False
            paid_shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_paid.id,
            ).all()
            assert len(paid_shadows) == 2
            for shadow in paid_shadows:
                assert shadow.is_deleted is False

            # Projected transfer is soft-deleted.
            db.session.refresh(xfer_projected)
            assert xfer_projected.is_deleted is True

    def test_hard_delete_transfer_template_with_history_already_archived(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Already-archived template with Paid history stays archived without re-archiving."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            paid_status = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("200.00"),
                    status_id=paid_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Monthly Savings",
                ),
            )

            # Pre-archive.
            template.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/transfers/{template.id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"has payment history" in resp.data

            db.session.refresh(template)
            assert template.is_active is False

    def test_hard_delete_transfer_template_received_blocked(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C21-6: A transfer template with a RECEIVED transfer is archived, not deleted.

        Mirror of the transaction-template CRIT-05 fix proof.  The
        pre-fix predicate enumerated ``[DONE, SETTLED]`` and silently
        omitted ``RECEIVED``; ``RECEIVED`` carries ``is_settled=True``
        in ``ref_seeds.py`` so the post-fix
        ``transfer_template_has_paid_history`` -- now filtering on
        ``Status.is_settled`` -- correctly returns True for a
        RECEIVED transfer and the route archives instead of
        physically destroying the transfer plus its shadow pair.
        Verifies the predicate fix end-to-end at the route layer.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            received_status = db.session.query(Status).filter_by(name="Received").one()
            xfer_received = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=received_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Monthly Savings",
                ),
            )
            db.session.commit()

            template_id = template.id
            xfer_id = xfer_received.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived instead" in resp.data
            # The archive-fallback flash contains "cannot be permanently
            # deleted" so the broad substring check is unsafe; assert
            # the literal success-flash text never fired instead.
            assert (
                b"Recurring transfer 'Monthly Savings' permanently deleted"
                not in resp.data
            )

            # Template archived, not deleted.
            db.session.refresh(template)
            assert template.is_active is False
            assert db.session.get(TransferTemplate, template_id) is not None

            # RECEIVED transfer preserved with original amount.  Hand-
            # verified: $250.00 stays exactly $250.00 (Decimal from
            # string per coding standards).
            surviving = db.session.get(Transfer, xfer_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            assert surviving.amount == Decimal("250.00")

            # Both shadows survive untouched (transfer invariant 1: a
            # transfer always has exactly two linked shadows).
            shadow_count = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert shadow_count == 2

    def test_hard_delete_transfer_template_bulk_delete_skips_settled_rows(
        self, app, auth_client, seed_user, seed_periods_today, monkeypatch,
    ):
        """C8-1: Even if the predicate is bypassed, the bulk delete spares settled transfers.

        Defense in depth (F-14, mirror of CRIT-05 / E-22): commit C-21
        of the main remediation already fixed
        ``transfer_template_has_paid_history`` to filter on
        ``Status.is_settled`` so the guard at the route's entry
        catches every settled status.  This commit adds the second
        layer: the bulk-delete loop itself filters on
        ``Transaction.status_id.notin_(settled_status_ids)`` so a
        future regression of the predicate, a race window between
        the guard and the delete, or a different caller that bypasses
        the guard cannot physically destroy settled transfers plus
        their shadow pairs.  This test forces the bypass scenario by
        monkey-patching ``transfer_template_has_paid_history`` to
        return False even when a RECEIVED transfer exists, then
        asserts the post-conditions: the settled transfer plus its
        two shadows survive intact while the Projected transfer is
        deleted as intended.

        ``Transfer.transfer_template_id`` is a FK with ``ON DELETE
        SET NULL`` (``app/models/transfer.py``) so the surviving
        Received transfer has its ``transfer_template_id`` cleared
        but its financial data -- amount, status, period -- is
        intact.  Both linked shadow transactions ride along: they
        reference ``transfer_id`` (NOT NULL), so the transfer's
        survival guarantees their survival.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            received_status = db.session.query(Status).filter_by(name="Received").one()
            projected_status = db.session.query(Status).filter_by(name="Projected").one()

            # RECEIVED transfer in period 0 (must survive).
            xfer_received = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=received_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Past Transfer",
                ),
            )
            # PROJECTED transfer in period 1 (must be deleted).
            xfer_projected = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[1].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=projected_status.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    transfer_template_id=template.id,
                    name="Future Transfer",
                ),
            )
            db.session.commit()

            template_id = template.id
            received_id = xfer_received.id
            projected_id = xfer_projected.id
            received_shadow_ids = [
                row.id for row in db.session.query(Transaction).filter_by(
                    transfer_id=received_id,
                ).all()
            ]
            assert len(received_shadow_ids) == 2

            # Force the bypass: predicate lies and says "no history."
            # The defense-in-depth filter inside the route is what must
            # save the Received transfer plus its two shadows.
            monkeypatch.setattr(
                "app.routes.transfers.templates.archive_helpers.transfer_template_has_paid_history",
                lambda _template_id: False,
            )

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200

            # Settled (Received) transfer SURVIVES with original
            # amount and status; FK SET NULL clears transfer_template_id.
            surviving = db.session.get(Transfer, received_id)
            assert surviving is not None
            assert surviving.status_id == received_status.id
            assert surviving.is_deleted is False
            # Hand-verified: original $250.00 stays exactly $250.00
            # (Decimal from string per coding standards).
            assert surviving.amount == Decimal("250.00")
            assert surviving.transfer_template_id is None

            # Both shadows of the Received transfer survive untouched
            # (transfer invariant 1: a transfer always has exactly two
            # linked shadows).
            surviving_shadows = db.session.query(Transaction).filter_by(
                transfer_id=received_id,
            ).all()
            assert len(surviving_shadows) == 2
            for shadow in surviving_shadows:
                assert shadow.is_deleted is False
                assert shadow.status_id == received_status.id
                assert shadow.estimated_amount == Decimal("250.00")
            assert {s.id for s in surviving_shadows} == set(received_shadow_ids)

            # Non-settled (Projected) transfer was deleted by the
            # bulk loop, as intended -- the defense-in-depth filter is
            # additive, not a wholesale block.
            assert db.session.get(Transfer, projected_id) is None
            orphaned_projected_shadows = db.session.query(Transaction).filter_by(
                transfer_id=projected_id,
            ).count()
            assert orphaned_projected_shadows == 0

            # Template itself was deleted (the bypass path completed).
            assert db.session.get(TransferTemplate, template_id) is None

    def test_hard_delete_preserves_shadow_invariant(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C-5A.5-19: No orphaned shadows remain after hard-deleting a template's transfers."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)

            # Create multiple transfers via the service.
            xfer_ids = []
            for i in range(3):
                xfer = transfer_service.create_transfer(
                    transfer_service.TransferSpec(
                        user_id=seed_user["user"].id,
                        from_account_id=seed_user["account"].id,
                        to_account_id=savings.id,
                        pay_period_id=seed_periods_today[i].id,
                        scenario_id=seed_user["scenario"].id,
                        amount=Decimal("200.00"),
                        status_id=db.session.query(Status).filter_by(
                        name="Projected"
                    ).one().id,
                        category_id=seed_user["categories"]["Rent"].id,
                        transfer_template_id=template.id,
                        name="Monthly Savings",
                    ),
                )
                xfer_ids.append(xfer.id)
            db.session.commit()

            # Verify 3 transfers, 6 shadows (2 per transfer) before deletion.
            total_shadows_before = 0
            for xfer_id in xfer_ids:
                count = db.session.query(Transaction).filter_by(
                    transfer_id=xfer_id,
                ).count()
                assert count == 2, (
                    f"Transfer {xfer_id} should have exactly 2 shadows, "
                    f"found {count}"
                )
                total_shadows_before += count
            assert total_shadows_before == 6

            template_id = template.id

            # Hard-delete the template.
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Template and all transfers are gone.
            assert db.session.get(TransferTemplate, template_id) is None
            for xfer_id in xfer_ids:
                assert db.session.get(Transfer, xfer_id) is None

            # No orphaned shadows: query for any Transaction with a
            # transfer_id that was just deleted.
            orphaned_shadows = db.session.query(Transaction).filter(
                Transaction.transfer_id.in_(xfer_ids),
            ).count()
            assert orphaned_shadows == 0, (
                f"Found {orphaned_shadows} orphaned shadow transactions "
                f"after hard-deleting template {template_id}"
            )

    def test_hard_delete_transfer_template_idor(
        self, app, auth_client, seed_user,
    ):
        """C-5A.5-20: Hard-deleting another user's template returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_template()
            other_id = other["template"].id

            resp = auth_client.post(
                f"/transfers/{other_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Other user's template still exists.
            assert db.session.get(TransferTemplate, other_id) is not None

    def test_list_separates_active_and_archived_transfers(
        self, app, auth_client, seed_user,
    ):
        """C-5A.5-21: List page shows active and archived in separate sections."""
        with app.app_context():
            savings = _create_savings_account(seed_user)

            active = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Active Transfer",
                default_amount=Decimal("100.00"),
            )
            archived = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Archived Transfer",
                default_amount=Decimal("50.00"),
                is_active=False,
            )
            db.session.add_all([active, archived])
            db.session.commit()

            resp = auth_client.get("/transfers")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active template in main table.
            assert "Active Transfer" in html

            # Archived section with count indicator.
            assert "Archived (1)" in html
            assert "Archived Transfer" in html

    def test_archive_label_in_flash_transfers(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Archive flash message says 'archived' not 'deactivated'."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            _create_transfer(seed_user, seed_periods_today, savings, template)

            resp = auth_client.post(
                f"/transfers/{template.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data
            assert b"deactivated" not in resp.data

    def test_hard_delete_transfer_template_soft_deleted_transfers_cleaned(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Soft-deleted transfers and their shadows are permanently removed on hard-delete."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings, with_rule=False)
            xfer = _create_transfer(seed_user, seed_periods_today, savings, template)
            xfer_id = xfer.id

            # Soft-delete the transfer via the service.
            transfer_service.delete_transfer(xfer.id, seed_user["user"].id, soft=True)
            db.session.commit()

            db.session.refresh(xfer)
            assert xfer.is_deleted is True

            # Shadows are also soft-deleted.
            shadows = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).all()
            assert all(s.is_deleted for s in shadows)

            template_id = template.id

            # Hard-delete the template.
            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data

            # Everything is gone -- no ghost data.
            assert db.session.get(TransferTemplate, template_id) is None
            assert db.session.get(Transfer, xfer_id) is None
            orphans = db.session.query(Transaction).filter_by(
                transfer_id=xfer_id,
            ).count()
            assert orphans == 0


# ── Period move (transfer-period-move follow-up) ──────────────────


class TestTransferPeriodMove:
    """Moving a transfer's pay period from the full-edit popover.

    The transfer service already relocates the parent transfer and both
    shadow transactions together (Transfer Invariant 3); these tests
    cover the UI wiring: the filtered period selector, the override flag
    on a template move, the gridRefresh trigger, and route-boundary
    ownership of the submitted period id.
    """

    def _shadows(self, xfer_id):
        """Return the two shadow transactions for a transfer."""
        return (
            db.session.query(Transaction)
            .filter_by(transfer_id=xfer_id)
            .all()
        )

    def test_full_edit_renders_filtered_period_selector(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Popover lists current+future periods plus the transfer's own.

        seed_periods_today places today in index 4, so index 0 is past
        (the transfer's own -- included and selected), index 5 is future
        (offered), and index 2 is past and not the transfer's own
        (excluded).  The pay-period <select> is isolated so option-value
        assertions cannot collide with the status select.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            own = seed_periods_today[0]
            future = seed_periods_today[5]
            excluded_past = seed_periods_today[2]

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'name="pay_period_id"' in html
            start = html.index('name="pay_period_id"')
            period_select = html[start:html.index("</select>", start)]
            assert own.label in period_select
            assert f'value="{own.id}" selected' in period_select
            assert future.label in period_select
            assert excluded_past.label not in period_select

    def test_move_relocates_transfer_and_both_shadows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A period move relocates the transfer and both shadows; gridRefresh.

        Verifies Transfer Invariant 3 (shadow periods equal the parent's)
        is preserved through the move and that the response asks for a
        full grid refresh so the relocated rows appear under the new period.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            target = seed_periods_today[5]

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"pay_period_id": target.id, "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "gridRefresh"

            db.session.refresh(xfer)
            assert xfer.pay_period_id == target.id
            shadows = self._shadows(xfer.id)
            assert len(shadows) == 2
            assert all(s.pay_period_id == target.id for s in shadows)

    def test_template_transfer_move_sets_override(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a template-generated transfer flags it is_override.

        Without the flag the recurrence engine would regenerate the
        transfer in its original period, duplicating it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            template = _create_template(seed_user, savings)
            xfer = _create_transfer(
                seed_user, seed_periods_today, savings, template=template,
            )
            assert xfer.is_override is False
            target = seed_periods_today[5]

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"pay_period_id": target.id, "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            db.session.refresh(xfer)
            assert xfer.pay_period_id == target.id
            assert xfer.is_override is True

    def test_move_to_cross_user_period_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Submitting another user's period id returns 404 and moves nothing."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            original_period_id = xfer.pay_period_id
            other = _create_other_user_with_template()
            foreign_period_id = other["transfer"].pay_period_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "pay_period_id": foreign_period_id,
                    "version_id": xfer.version_id,
                },
            )
            assert resp.status_code == 404
            db.session.refresh(xfer)
            assert xfer.pay_period_id == original_period_id
            assert all(
                s.pay_period_id == original_period_id
                for s in self._shadows(xfer.id)
            )

    def test_inplace_edit_keeps_balancechanged(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An edit that does not change the period keeps balanceChanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"amount": "250.00", "version_id": xfer.version_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"
            db.session.refresh(xfer)
            assert xfer.amount == Decimal("250.00")
