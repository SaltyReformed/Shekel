"""
Shekel Budget App -- Carry-Forward Preview Modal Route Tests

Phase 5 of ``docs/carry-forward-aftermath-implementation-plan.md``
introduces a pre-flight confirmation modal: clicking "Carry Fwd" on
a past period now opens
``GET /pay-periods/<id>/carry-forward-preview`` instead of POSTing
the mutation directly.  The user reviews the planned actions and
confirms via the modal's button, which then posts to the existing
``carry_forward`` endpoint.

These tests exercise the route layer:

  * Returns 200 + rendered modal HTML for the user's own period.
  * Lists every source row with a kind label (envelope / discrete /
    transfer).
  * Disables the Confirm button when any envelope target is
    blocked (settled, missing+inactive, duplicates, soft-deleted).
  * Returns 404 for unknown periods and other-user periods
    (security response rule).
  * Returns 400 when the user has no current period or no baseline
    scenario (mirrors the existing carry_forward POST).
  * Companion users are blocked by ``@require_owner``.

The service-layer tests in
``tests/test_services/test_carry_forward_service.py``
(``TestPreviewCarryForward*``) cover the data correctness; this
file focuses on HTTP-status, rendering, and security.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RecurrencePatternEnum, StatusEnum, RoleEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    AccountType, RecurrencePattern, Status, TransactionType,
)
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.services import pay_period_service, transfer_service
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _make_envelope_template(
    seed_user, *, name="Envelope Spending", default_amount="100.00",
    category_key="Groceries",
):
    """Create an envelope expense template with an EVERY_PERIOD rule."""
    every_period = (
        db.session.query(RecurrencePattern)
        .filter_by(name=RecurrencePatternEnum.EVERY_PERIOD.value)
        .one()
    )
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"][category_key].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=Decimal(default_amount),
        is_envelope=True,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_envelope_txn(
    seed_user, period, template, *,
    estimated_amount=None, status_name="Projected",
):
    """Create one envelope transaction in *period*."""
    status = db.session.query(Status).filter_by(name=status_name).one()
    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=status.id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        estimated_amount=Decimal(
            estimated_amount if estimated_amount is not None
            else str(template.default_amount)
        ),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _add_entry(txn, seed_user, amount):
    """Attach a debit entry to *txn*."""
    db.session.add(TransactionEntry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="Test purchase",
        entry_date=date(2026, 1, 5),
    ))
    db.session.flush()


def _make_discrete_template(seed_user, *, name="Recurring Bill",
                            amount="500.00", category_key="Rent"):
    """Create a discrete (is_envelope=False) expense template."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"][category_key].id,
        transaction_type_id=expense_type.id,
        name=name,
        default_amount=Decimal(amount),
    )
    db.session.add(template)
    db.session.flush()
    return template


def _make_discrete_txn(seed_user, period, template):
    """Create one discrete transaction in *period*."""
    projected = (
        db.session.query(Status).filter_by(name="Projected").one()
    )
    txn = Transaction(
        template_id=template.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=projected.id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        estimated_amount=template.default_amount,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _make_savings_account(seed_user):
    """Create a Savings account for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = Account(
        user_id=seed_user["user"].id,
        account_type_id=savings_type.id,
        name="CFP Savings",
        current_anchor_balance=Decimal("0"),
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _make_transfer(seed_user, period):
    """Create a transfer in *period*; returns the parent Transfer."""
    savings = _make_savings_account(seed_user)
    projected = db.session.query(Status).filter_by(name="Projected").one()
    return transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount=Decimal("75.00"),
        status_id=projected.id,
        category_id=seed_user["categories"]["Rent"].id,
        name="CFP Transfer",
    )


def _login_companion(app):
    """Authenticate a companion test client."""
    comp = app.test_client()
    resp = comp.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302
    return comp


# ── Status code + content tests ──────────────────────────────────────


class TestCarryForwardPreviewSuccess:
    """Happy-path response: 200 + rendered modal partial."""

    def test_returns_200_with_modal_html_for_owned_period(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """GET preview returns 200 and a renderable modal partial.

        The response body must contain the marker attribute used by
        ``app.js`` (``data-modal-auto-show``) so the modal opens
        itself after HTMX swaps it in -- without this attribute the
        modal would render but never appear.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _make_envelope_txn(seed_user, seed_periods[1], template)
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Modal markers required for the JS auto-show pattern.
            assert "data-modal-auto-show" in html
            assert "carryForwardPreviewModal" in html
            # Source row appears in the modal.
            assert template.name in html
            # Confirm button posts to the existing carry_forward route.
            assert (
                f'/pay-periods/{seed_periods[0].id}/carry-forward'
                in html
            )

    def test_envelope_row_labeled_as_envelope(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Envelope source row gets the 'Envelope' badge in the modal.

        The badge tells the user the row will settle-and-roll instead
        of move-whole.  Asserting on the visible text keeps the test
        from coupling to the exact CSS classes used.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _make_envelope_txn(seed_user, seed_periods[1], template)
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert ">Envelope<" in html, (
                "Envelope rows must be visibly labeled in the modal."
            )

    def test_discrete_row_labeled_as_discrete(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Discrete source row gets the 'Discrete' badge."""
        with app.app_context():
            template = _make_discrete_template(seed_user)
            _make_discrete_txn(seed_user, seed_periods[0], template)
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert ">Discrete<" in html

    def test_transfer_row_labeled_as_transfer(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Transfer source row gets the 'Transfer' badge."""
        with app.app_context():
            _make_transfer(seed_user, seed_periods[0])
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert ">Transfer<" in html

    def test_summary_line_includes_counts(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Modal summary line names the per-kind action counts.

        The count words ("envelope", "discrete", "transfer") must
        appear so the user can quickly see the batch composition
        before confirming.
        """
        with app.app_context():
            envelope_t = _make_envelope_template(
                seed_user, category_key="Groceries",
            )
            envelope_source = _make_envelope_txn(
                seed_user, seed_periods[0], envelope_t,
            )
            _make_envelope_txn(seed_user, seed_periods[1], envelope_t)
            _add_entry(envelope_source, seed_user, "20.00")

            discrete_t = _make_discrete_template(seed_user)
            _make_discrete_txn(seed_user, seed_periods[0], discrete_t)

            _make_transfer(seed_user, seed_periods[0])
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            # Counts text appears in the summary.
            assert "envelope" in html.lower()
            assert "discrete" in html.lower()
            assert "transfer" in html.lower()


# ── Confirm-button gating ────────────────────────────────────────────


class TestCarryForwardPreviewConfirmGating:
    """Confirm button enabled vs disabled by preview state."""

    def test_confirm_enabled_when_all_actionable(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """All-actionable preview: Confirm button is NOT disabled.

        ``hx-disabled-elt`` is unrelated to the HTML disabled
        attribute -- HTMX uses it to grey out the button while the
        request is in flight.  Use ``aria-disabled="true"`` (which
        only renders when the modal's gating condition fires) as
        the canonical marker so the assertion does not false-match
        on the HTMX attribute name.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _make_envelope_txn(seed_user, seed_periods[1], template)
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert "data-carry-forward-confirm" in html
            assert 'aria-disabled="true"' not in html, (
                "Actionable preview must NOT mark Confirm as disabled."
            )

    def test_confirm_disabled_when_any_envelope_blocked(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Settled envelope target -> Confirm is disabled.

        The plan calls this out explicitly: blocked rows propagate
        to the Confirm button so the user must resolve them
        manually before retrying.  The disabled attribute prevents
        HTMX from posting the mutation that would just refuse
        anyway.

        The target row must live in the period the route resolves
        as ``current_period`` (via ``pay_period_service.get_current_period``)
        -- placing it in any other period leaves the actual target
        period empty and the engine auto-generates a canonical there,
        producing an actionable plan that misses the assertion.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current_period is not None
            target = _make_envelope_txn(
                seed_user, current_period, template,
                status_name="Paid",
            )
            target.actual_amount = Decimal("100.00")
            _add_entry(source, seed_user, "40.00")
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert "data-carry-forward-confirm" in html
            assert 'aria-disabled="true"' in html, (
                "Blocked envelope row must disable the Confirm button."
            )
            # And the blocked alert appears in the body.
            assert "blocked" in html.lower()

    def test_confirm_disabled_when_period_is_empty(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Empty source period: Confirm has nothing to do, disabled.

        The modal still renders so the user sees "no items to carry
        forward"; Confirm is disabled to prevent a no-op POST.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            html = resp.data.decode()
            assert "data-carry-forward-confirm" in html
            assert 'aria-disabled="true"' in html


# ── Security ─────────────────────────────────────────────────────────


class TestCarryForwardPreviewSecurity:
    """404 for unowned/missing periods; companion blocked."""

    def test_returns_404_for_unowned_period(
        self, app, auth_client, db, seed_user, seed_periods,
        seed_second_user, seed_second_periods,
    ):
        """Cross-user period -> 404 (security response rule).

        Mirrors the POST carry_forward route's behaviour to avoid
        leaking which periods exist for other users.
        """
        with app.app_context():
            other_period_id = seed_second_periods[0].id

            resp = auth_client.get(
                f"/pay-periods/{other_period_id}/carry-forward-preview"
            )
            assert resp.status_code == 404

    def test_returns_404_for_missing_period(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Unknown period_id -> 404."""
        with app.app_context():
            resp = auth_client.get(
                "/pay-periods/9999999/carry-forward-preview"
            )
            assert resp.status_code == 404

    def test_companion_blocked(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """Companion gets 404 from @require_owner.

        The preview endpoint is not relevant to companions and
        leaking even read-only carry-forward data could expose
        envelope-tracked spending the owner has not chosen to
        share.  The decorator returns the same 404 the POST route
        does so the two are consistent.
        """
        comp = _login_companion(app)
        resp = comp.get(
            f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
        )
        assert resp.status_code == 404


# ── Configuration errors ─────────────────────────────────────────────


class TestCarryForwardPreviewConfiguration:
    """Mirrors the POST route's configuration checks."""

    def test_returns_400_when_no_baseline_scenario(
        self, app, db, seed_user, seed_periods, auth_client,
    ):
        """No baseline scenario -> 400 (matches POST route's check).

        Carry-forward is scoped to the baseline scenario; without
        one there is no defined target for the operation.  The
        message is the same as the POST route's so the modal
        could display it cleanly if rendered (in practice the UI
        only ever calls preview when a scenario exists, but the
        defensive check makes both routes safe).
        """
        with app.app_context():
            # Remove the baseline scenario set up by the seed.
            scenario = (
                db.session.query(Scenario)
                .filter_by(user_id=seed_user["user"].id, is_baseline=True)
                .one()
            )
            scenario.is_baseline = False
            db.session.commit()

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 400
            assert b"baseline scenario" in resp.data.lower()

    def test_returns_400_when_no_current_period(
        self, app, db, seed_user, monkeypatch, auth_client, seed_periods,
    ):
        """No current period -> 400 (matches POST route's check).

        Patches the service helper so the test does not have to
        construct a non-overlapping period range.  Mirrors the same
        defensive behaviour the POST route already has.
        """
        with app.app_context():
            from app.services import pay_period_service as pps  # pylint: disable=import-outside-toplevel
            monkeypatch.setattr(
                pps, "get_current_period", lambda user_id: None,
            )

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 400
            assert b"current period" in resp.data.lower()


# ── Read-only invariant ──────────────────────────────────────────────


class TestCarryForwardPreviewReadOnly:
    """Repeated GETs do not mutate any state."""

    def test_repeated_preview_does_not_change_database(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Calling the preview twice produces identical state -- proof of read-only.

        The whole point of Phase 5 is to let users review before
        committing.  If preview mutated, repeated clicks would alter
        the very state the user is trying to inspect.  Catch that
        by snapshotting transaction state, calling preview twice,
        and asserting equality after each call.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            target = _make_envelope_txn(
                seed_user, seed_periods[1], template,
            )
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            def _snapshot():
                """Capture relevant state for the test fixture."""
                db.session.expire_all()
                src = db.session.get(Transaction, source.id)
                tgt = db.session.get(Transaction, target.id)
                return (
                    src.status_id, src.actual_amount, src.paid_at,
                    src.estimated_amount, src.is_override,
                    tgt.estimated_amount, tgt.is_override,
                    tgt.status_id,
                )

            before = _snapshot()
            for _ in range(3):
                resp = auth_client.get(
                    f"/pay-periods/{seed_periods[0].id}"
                    f"/carry-forward-preview"
                )
                assert resp.status_code == 200
            after = _snapshot()

            assert before == after, (
                "preview must be read-only -- repeated GETs cannot "
                "mutate transaction state."
            )

    def test_preview_does_not_generate_canonical_for_missing_target(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """Engine-would-generate case must not actually generate.

        Specifically guards against the trap where the preview's
        ``can_generate_in_period`` check could be misimplemented as a
        ``generate_for_template`` call -- that would create the
        canonical at preview time rather than confirm time, breaking
        the read-only contract.
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "30.00")
            db.session.commit()

            assert (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                ).count()
                == 0
            )

            resp = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            assert resp.status_code == 200

            assert (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=seed_periods[1].id,
                ).count()
                == 0
            ), (
                "Preview must not trigger the recurrence engine; "
                "canonical generation should happen only on confirm."
            )


# ── End-to-end through both endpoints ────────────────────────────────


class TestCarryForwardPreviewEndToEnd:
    """Preview -> Confirm POST flow produces the predicted state."""

    def test_preview_then_confirm_runs_carry_forward(
        self, app, auth_client, db, seed_user, seed_periods,
    ):
        """GET preview, then POST carry_forward, sees expected state.

        Two-step flow: the GET preview must not mutate, the POST
        confirm must execute the same plan the preview described.
        Asserts on the final state matches the preview's prediction.

        Note on the target period: both routes resolve it via
        ``pay_period_service.get_current_period(user_id)`` so the
        test must place its target row in the period that contains
        the test-runner's "today".  Pre-creating the canonical in
        a hard-coded index would be wrong -- the route ignores it
        and uses the current period instead.  Letting the engine
        auto-generate the canonical at confirm time is cleaner and
        matches the realistic flow (the user clicks Carry Fwd from
        a past period; current_period may not yet have the row).
        """
        with app.app_context():
            template = _make_envelope_template(seed_user)
            source = _make_envelope_txn(
                seed_user, seed_periods[0], template,
            )
            _add_entry(source, seed_user, "65.00")
            db.session.commit()

            # The route resolves target via get_current_period.
            current_period = pay_period_service.get_current_period(
                seed_user["user"].id,
            )
            assert current_period is not None, (
                "Test fixture must include the current period."
            )

            # Step 1: preview.
            resp_preview = auth_client.get(
                f"/pay-periods/{seed_periods[0].id}/carry-forward-preview"
            )
            assert resp_preview.status_code == 200

            # Step 2: confirm POST -- the route the modal's button
            # would hit when the user clicks Confirm.
            resp_confirm = auth_client.post(
                f"/pay-periods/{seed_periods[0].id}/carry-forward"
            )
            assert resp_confirm.status_code == 200
            assert resp_confirm.headers.get("HX-Trigger") == "gridRefresh"

            # The state matches the preview's prediction.
            db.session.refresh(source)
            done_id = ref_cache.status_id(StatusEnum.DONE)
            assert source.status_id == done_id
            assert source.actual_amount == Decimal("65.00")

            # Engine auto-generated the canonical at the current
            # period's default ($100) and the carry-forward bumped
            # it by the leftover (35) -> 135.
            target = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template.id,
                    pay_period_id=current_period.id,
                    scenario_id=seed_user["scenario"].id,
                    is_deleted=False,
                ).one()
            )
            assert target.estimated_amount == Decimal("135.00")
            assert target.is_override is True
