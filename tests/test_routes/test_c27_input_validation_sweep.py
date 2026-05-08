"""
Shekel Budget App -- C-27 Route Input Validation Tests

Route-level coverage for commit C-27 of the 2026-04-15 security
remediation plan: every endpoint that previously hand-parsed form
input or trusted FK IDs without route-boundary ownership checks
now goes through a Marshmallow schema and/or a per-FK
``current_user.id`` probe.  The plan-cited tests live here:

  * ``transactions.mark_done`` -- both branches (regular path and
    transfer-shadow path) parse ``actual_amount`` via
    :class:`MarkDoneSchema`; a malformed or negative value is
    rejected at the schema tier instead of the route's old
    ``InvalidOperation`` catch.

  * ``dashboard.mark_paid`` -- same parse rule as
    ``transactions.mark_done`` regular branch.

  * ``transfers.create_ad_hoc`` -- route-boundary FK ownership for
    ``from_account_id``, ``to_account_id``, ``pay_period_id``,
    ``scenario_id``, ``category_id`` collapsed into a single loop
    that returns 404 on the first failure.

  * ``transfers.update_transfer`` -- route-boundary ownership probe
    on ``category_id`` (the only user-scoped FK the schema accepts).

  * ``transfers.create_transfer_template`` /
    ``transfers.update_transfer_template`` -- ownership probes on
    every user-scoped FK accepted by the form.

The IDOR rejections all return 404 (security response rule:
"404 for both not found and not yours") and prove no row was
created or mutated in the victim's namespace.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.ref import AccountType
from app.services import pay_period_service, transfer_service


# ── Helpers ──────────────────────────────────────────────────────────


def _add_txn(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED,
    is_income=False,
    due_date=None,
    transfer_id=None,
):
    """Create a Transaction for the seeded user.

    Mirrors the helper in ``test_dashboard.py``; reproduced locally
    so this test module stays self-contained and does not introduce
    a cross-test-file import dependency.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=ref_cache.status_id(status_enum),
        name=name,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=type_id,
        estimated_amount=Decimal(str(amount)),
        due_date=due_date,
        transfer_id=transfer_id,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _create_savings_account(seed_user):
    """Add a Savings account on the seeded user for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="C-27 Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _create_transfer(seed_user, period, savings):
    """Create a transfer + 2 shadow transactions via the service."""
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    xfer = transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=Decimal("100.00"),
        status_id=projected_id,
        category_id=seed_user["categories"]["Rent"].id,
        name="C-27 Transfer",
    )
    db.session.commit()
    return xfer


def _seed_second_user_transfer_assets():
    """Seed a second user, their period, scenario, and a category.

    Returns a dict with keys: user, account, savings, period,
    scenario, category.  Used to source cross-user FKs the
    authenticated user must NOT be allowed to reference.
    """
    from app.models.user import User, UserSettings  # pylint: disable=import-outside-toplevel
    from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
    from app.services.auth_service import hash_password  # pylint: disable=import-outside-toplevel

    other = User(
        email="cross-c27@shekel.local",
        password_hash=hash_password("crossc27pass"),
        display_name="Cross C27",
    )
    db.session.add(other)
    db.session.flush()
    db.session.add(UserSettings(user_id=other.id))

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    other_checking = Account(
        user_id=other.id, account_type_id=checking_type.id,
        name="Other Cross Checking", current_anchor_balance=Decimal("100.00"),
    )
    other_savings = Account(
        user_id=other.id, account_type_id=savings_type.id,
        name="Other Cross Savings", current_anchor_balance=Decimal("0"),
    )
    db.session.add_all([other_checking, other_savings])

    other_scenario = Scenario(
        user_id=other.id, name="Baseline", is_baseline=True,
    )
    db.session.add(other_scenario)

    other_category = Category(
        user_id=other.id, group_name="Home", item_name="Rent",
    )
    db.session.add(other_category)
    db.session.flush()

    other_periods = pay_period_service.generate_pay_periods(
        user_id=other.id,
        start_date=date(2026, 1, 2),
        num_periods=2,
        cadence_days=14,
    )
    db.session.commit()

    return {
        "user": other,
        "account": other_checking,
        "savings": other_savings,
        "period": other_periods[0],
        "scenario": other_scenario,
        "category": other_category,
    }


# ── transactions.mark_done -- F-042 / F-162 ──────────────────────────


class TestTransactionsMarkDoneActualAmount:
    """``transactions.mark_done`` parses ``actual_amount`` via MarkDoneSchema.

    F-042 / F-162 -- replaces two raw ``Decimal(...)`` calls (one in
    the regular branch, one in the transfer-shadow branch) with a
    single schema parse run before the branches diverge.  All
    behaviour aside from validation is unchanged.
    """

    def test_negative_actual_amount_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative ``actual_amount`` fails at the schema tier (regular branch)."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "-50.00"},
            )
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED), (
                "negative actual_amount must not transition the row"
            )

    def test_non_numeric_actual_amount_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Non-numeric ``actual_amount`` fails Marshmallow's Decimal coercion."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "abc"},
            )
            assert resp.status_code == 400
            payload = resp.get_json()
            assert payload is not None
            assert "actual_amount" in payload["errors"]

    def test_negative_actual_amount_rejected_on_transfer_shadow(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative actual_amount is rejected on the transfer-shadow branch.

        Pre-C-27 the transfer-shadow branch had its own raw
        ``Decimal(...)`` parse; the schema now runs once before the
        branch and applies identical validation to both paths.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[0], savings)
            shadow = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .first()
            )

            resp = auth_client.post(
                f"/transactions/{shadow.id}/mark-done",
                data={"actual_amount": "-25.00"},
            )
            assert resp.status_code == 400

            db.session.expire_all()
            db.session.refresh(xfer)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED), (
                "negative actual_amount must not transition the transfer"
            )

    def test_valid_actual_amount_persists(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Happy-path: a positive actual_amount transitions the row.

        Anchors the schema-based parse to the existing behaviour --
        the regression we are guarding against is "the schema
        rejected a value the route used to accept."
        """
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"actual_amount": "85.50"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.actual_amount == Decimal("85.50")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_missing_actual_amount_leaves_column_untouched(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark-done without ``actual_amount`` does not nullify a prior value.

        Pre-C-27 the route's ``if actual:`` truthy check left the
        column untouched on missing input; the schema's
        ``strip_empty_strings`` plus ``data.get(...)`` preserves
        that contract -- a button-click with no body must not
        clear the previously recorded actual amount.
        """
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            txn.actual_amount = Decimal("90.00")
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.actual_amount == Decimal("90.00"), (
                "mark-done with no body must not clear actual_amount"
            )


# ── dashboard.mark_paid -- F-042 ────────────────────────────────────


class TestDashboardMarkPaidActualAmount:
    """``dashboard.mark_paid`` shares MarkDoneSchema with the grid path."""

    def test_negative_actual_amount_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative ``actual_amount`` is rejected at the schema tier."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                data={"actual_amount": "-1.00"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 400

            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED), (
                "negative actual_amount must not transition the row"
            )

    def test_non_numeric_actual_amount_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Non-numeric ``actual_amount`` produces a per-field schema error."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/dashboard/mark-paid/{txn.id}",
                data={"actual_amount": "not-a-number"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 400
            payload = resp.get_json()
            assert payload is not None
            assert "actual_amount" in payload["errors"]


# ── transfers.create_ad_hoc -- F-043 ──────────────────────────────────


class TestTransfersAdHocFkOwnership:
    """``transfers.create_ad_hoc`` rejects cross-user FKs at the route layer.

    Each test asserts (1) the response is 404 (security response
    rule), and (2) no transfer row was created in either user's
    namespace -- the route's defense-in-depth check must short-
    circuit before ``transfer_service.create_transfer`` reaches the
    database.
    """

    def _baseline_payload(self, seed_user, period, savings):
        """Construct a valid ad-hoc payload for the seed user."""
        return {
            "pay_period_id": period.id,
            "from_account_id": seed_user["account"].id,
            "to_account_id": savings.id,
            "amount": "75.00",
            "scenario_id": seed_user["scenario"].id,
            "name": "C-27 cross-user attempt",
            "category_id": str(seed_user["categories"]["Rent"].id),
        }

    def test_cross_user_from_account_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``from_account_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["from_account_id"] = other["account"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before, (
                "cross-user from_account_id must not create a transfer"
            )

    def test_cross_user_to_account_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``to_account_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["to_account_id"] = other["savings"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before

    def test_cross_user_pay_period_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``pay_period_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["pay_period_id"] = other["period"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before

    def test_cross_user_scenario_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``scenario_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["scenario_id"] = other["scenario"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before

    def test_cross_user_category_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``category_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["category_id"] = str(other["category"].id)

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before


# ── transfers.update_transfer -- F-043 ──────────────────────────────


class TestUpdateTransferCategoryOwnership:
    """``transfers.update_transfer`` rejects a cross-user category_id.

    The schema only exposes one user-scoped FK on update --
    ``category_id`` -- and the route now verifies ownership before
    delegating to ``transfer_service.update_transfer``.
    """

    def test_cross_user_category_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A cross-user category_id is rejected before the service is called.

        Asserts the transfer's category_id is unchanged after the
        rejected request (proves the failure short-circuited
        before the service mutation).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[0], savings)
            other = _seed_second_user_transfer_assets()
            original_category_id = xfer.category_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "category_id": str(other["category"].id),
                },
            )
            assert resp.status_code == 404

            db.session.expire_all()
            db.session.refresh(xfer)
            assert xfer.category_id == original_category_id, (
                "cross-user category_id must not mutate the transfer"
            )

    def test_clearing_category_is_permitted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Setting category_id to empty (None) bypasses the ownership check.

        ``TransferUpdateSchema.category_id`` is ``allow_none=True``;
        sending the field empty must succeed because clearing the
        category is legitimate.  The new ownership probe correctly
        skips ``None`` values.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[0], savings)
            xfer_id = xfer.id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "category_id": "",
                },
            )
            assert resp.status_code in (200, 201)

            # Refresh; the empty-string strip in the schema's
            # pre_load drops category_id from the loaded dict
            # rather than coercing it to None, so the service is
            # called without the field and the transfer's
            # category_id is unchanged.  The important assertion
            # here is the lack of 404, proving the ownership probe
            # correctly skipped the empty input.
            db.session.expire_all()
            db.session.refresh(xfer)


# ── transfers.create_transfer_template -- F-043 ──────────────────────


class TestCreateTransferTemplateFkOwnership:
    """``transfers.create_transfer_template`` rejects cross-user FKs.

    The form-redirect UX makes the test pattern slightly different
    from the JSON-returning ad-hoc endpoint: a 302 redirect with
    no template row created counts as the rejection.  Each test
    counts the rows before and after to prove no side effects
    leaked.
    """

    def _baseline_form(self, seed_user, savings):
        """Valid template-create payload for the seeded user."""
        return {
            "name": "C-27 Template",
            "default_amount": "100.00",
            "from_account_id": seed_user["account"].id,
            "to_account_id": savings.id,
            "category_id": str(seed_user["categories"]["Rent"].id),
        }

    def test_cross_user_from_account_blocked(
        self, app, auth_client, seed_user,
    ):
        """``from_account_id`` from another user blocks template creation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(TransferTemplate).count()
            form = self._baseline_form(seed_user, savings)
            form["from_account_id"] = other["account"].id

            resp = auth_client.post(
                "/transfers", data=form, follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            assert db.session.query(TransferTemplate).count() == count_before

    def test_cross_user_to_account_blocked(
        self, app, auth_client, seed_user,
    ):
        """``to_account_id`` from another user blocks template creation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(TransferTemplate).count()
            form = self._baseline_form(seed_user, savings)
            form["to_account_id"] = other["savings"].id

            resp = auth_client.post(
                "/transfers", data=form, follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            assert db.session.query(TransferTemplate).count() == count_before

    def test_cross_user_category_blocked(
        self, app, auth_client, seed_user,
    ):
        """``category_id`` from another user blocks template creation.

        Pre-C-27 this gap existed: the template-create route checked
        account ownership but not category ownership, so a
        cross-user ``category_id`` would persist on the template
        and only fail later when the service tried to use it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(TransferTemplate).count()
            form = self._baseline_form(seed_user, savings)
            form["category_id"] = str(other["category"].id)

            resp = auth_client.post(
                "/transfers", data=form, follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            assert db.session.query(TransferTemplate).count() == count_before


# ── transfers.update_transfer_template -- F-043 ──────────────────────


class TestUpdateTransferTemplateFkOwnership:
    """``transfers.update_transfer_template`` rejects cross-user FKs.

    The update path is partial: only fields actually submitted run
    through the ownership probe.  Each test verifies the per-FK
    rejection and asserts the template's row was not mutated.
    """

    def _create_template(self, seed_user, savings):
        """Make a saved template owned by the seeded user."""
        from app.models.transfer_template import TransferTemplate as TplModel  # pylint: disable=import-outside-toplevel
        tpl = TplModel(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="C-27 Template To Update",
            default_amount=Decimal("80.00"),
        )
        db.session.add(tpl)
        db.session.commit()
        return tpl

    def test_cross_user_from_account_blocked(
        self, app, auth_client, seed_user,
    ):
        """Submitting a cross-user from_account_id leaves the template unchanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            tpl = self._create_template(seed_user, savings)
            other = _seed_second_user_transfer_assets()
            original_from = tpl.from_account_id

            resp = auth_client.post(
                f"/transfers/{tpl.id}",
                data={
                    "name": tpl.name,
                    "default_amount": str(tpl.default_amount),
                    "from_account_id": other["account"].id,
                    "to_account_id": tpl.to_account_id,
                    "category_id": str(tpl.category_id),
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            db.session.refresh(tpl)
            assert tpl.from_account_id == original_from

    def test_cross_user_category_blocked(
        self, app, auth_client, seed_user,
    ):
        """Submitting a cross-user category_id leaves the template unchanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            tpl = self._create_template(seed_user, savings)
            other = _seed_second_user_transfer_assets()
            original_category = tpl.category_id

            resp = auth_client.post(
                f"/transfers/{tpl.id}",
                data={
                    "name": tpl.name,
                    "default_amount": str(tpl.default_amount),
                    "from_account_id": tpl.from_account_id,
                    "to_account_id": tpl.to_account_id,
                    "category_id": str(other["category"].id),
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            db.session.refresh(tpl)
            assert tpl.category_id == original_category
