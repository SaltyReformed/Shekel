"""Tests for ``scripts/backfill_transfer_due_dates.py``.

The backfill recomputes recurring-transfer due dates from the recurrence
rule (via the shared ``recurrence_engine.compute_due_date``) and writes them
through ``transfer_service`` so the parent transfer and both shadow
transactions stay equal.  It targets template-linked, non-override,
non-immutable transfers only; ad-hoc, overridden, and settled transfers are
left untouched.

These tests simulate pre-migration data by staling each transfer's due date
to the pay-period start (the value the old hardcoded recurrence produced),
then assert the backfill corrects it to the rule's day_of_month.
"""

from decimal import Decimal

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrencePattern, Status
from app.models.transaction import Transaction
from app.models.transfer_template import TransferTemplate
from app.services import account_service, transfer_recurrence, transfer_service
from scripts.backfill_transfer_due_dates import (
    apply_due_date_changes,
    collect_due_date_changes,
)


def _make_monthly_template(seed_user, day_of_month=15):
    """Create a Savings account + Monthly recurrence rule + transfer template."""
    pattern = (
        db.session.query(RecurrencePattern).filter_by(name="Monthly").one()
    )
    savings_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.add(savings)
    db.session.flush()

    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=pattern.id,
        day_of_month=day_of_month,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        recurrence_rule_id=rule.id,
        name="Monthly Transfer",
        default_amount=Decimal("100.00"),
    )
    db.session.add(template)
    db.session.flush()
    db.session.refresh(template)
    return template, savings


def _stale_due_dates_to_period_start(transfers):
    """Reset due_date to the pay-period start on the parent and both shadows.

    Reproduces the pre-migration state the backfill is meant to correct.
    """
    for xfer in transfers:
        start = xfer.pay_period.start_date
        xfer.due_date = start
        for shadow in (
            db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
        ):
            shadow.due_date = start
    db.session.flush()


def test_backfill_recomputes_stale_monthly_due_dates(
    app, db, seed_user, seed_periods
):
    """Stale (pay-period-start) due dates are corrected to the rule's day_of_month.

    day_of_month=15 over seed_periods produces five transfers due on the 15th
    of Jan-May 2026 (see test_transfer_recurrence for the period math).  After
    staling them to the period start, collect+apply must restore the 15th on
    the parent and both shadows, and a second pass must find nothing.
    """
    with app.app_context():
        template, _savings = _make_monthly_template(seed_user, day_of_month=15)
        created = transfer_recurrence.generate_for_template(
            template, seed_periods, seed_user["scenario"].id,
        )
        _stale_due_dates_to_period_start(created)
        db.session.commit()

        summary, changes = collect_due_date_changes(db.session)
        assert summary["to_update"] == len(created)
        assert summary["skipped_no_rule"] == 0

        apply_due_date_changes(db.session, changes)
        db.session.commit()

        for xfer in created:
            db.session.refresh(xfer)
            assert xfer.due_date.day == 15
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            assert all(s.due_date == xfer.due_date for s in shadows)

        # Idempotent: nothing left to update on a second pass.
        summary_again, changes_again = collect_due_date_changes(db.session)
        assert summary_again["to_update"] == 0
        assert changes_again == []


def test_backfill_touches_each_transfer_once(
    app, db, seed_user, seed_periods
):
    """Each corrected transfer's version_id bumps exactly once (no double-touch).

    The backfill iterates parent transfers, not shadows, so a transfer with two
    shadows is updated a single time.
    """
    with app.app_context():
        template, _savings = _make_monthly_template(seed_user, day_of_month=15)
        created = transfer_recurrence.generate_for_template(
            template, seed_periods, seed_user["scenario"].id,
        )
        _stale_due_dates_to_period_start(created)
        db.session.commit()
        versions_before = {x.id: x.version_id for x in created}

        _summary, changes = collect_due_date_changes(db.session)
        apply_due_date_changes(db.session, changes)
        db.session.commit()

        for xfer in created:
            db.session.refresh(xfer)
            assert xfer.version_id == versions_before[xfer.id] + 1


def test_backfill_skips_immutable_override_and_adhoc(
    app, db, seed_user, seed_periods
):
    """Settled, overridden, and ad-hoc transfers are excluded from the backfill."""
    with app.app_context():
        template, savings = _make_monthly_template(seed_user, day_of_month=15)
        created = transfer_recurrence.generate_for_template(
            template, seed_periods, seed_user["scenario"].id,
        )
        _stale_due_dates_to_period_start(created)

        # Mark the first generated transfer Paid (immutable status).
        paid = db.session.query(Status).filter_by(name="Paid").one()
        transfer_service.update_transfer(
            created[0].id, seed_user["user"].id, status_id=paid.id,
        )
        # Flag the second as an override (carried-forward semantics).
        transfer_service.update_transfer(
            created[1].id, seed_user["user"].id, is_override=True,
        )

        # An ad-hoc transfer (no template) with a stale due date.
        adhoc = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("77.00"),
                status_id=db.session.query(Status).filter_by(name="Projected").one().id,
                category_id=seed_user["categories"]["Rent"].id,
                due_date=seed_periods[0].start_date,
            ),
        )
        db.session.commit()

        _summary, changes = collect_due_date_changes(db.session)
        changed_ids = {xfer.id for xfer, _ in changes}

        assert created[0].id not in changed_ids  # immutable (Paid) skipped
        assert created[1].id not in changed_ids  # override skipped
        assert adhoc.id not in changed_ids        # ad-hoc (no rule) skipped
