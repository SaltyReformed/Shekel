"""Integration: a derive-from-loan transfer's cash amount is live-derived.

Commit 5 of the loan rate-period work.  A recurring loan-payment
transfer flagged ``derive_from_loan`` reflects the loan's current
monthly payment (P&I + escrow) via the read-time override
(:func:`app.services.loan_payment_service.live_loan_transfer_amounts`),
and an escrow change reflows that amount WITHOUT regenerating the
transfer -- the stored ``Transfer.amount`` stays put; only the live
override changes.

Every monetary expectation is hand-computed with the arithmetic shown.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RecurrencePatternEnum
from app.extensions import db
from app.models.loan_features import EscrowComponent
from app.models.loan_params import LoanParams
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service, loan_payment_service, transfer_recurrence
from app.services.rate_period_engine import monthly_due_date
from tests._test_helpers import insert_origination_event


def _build_derived_loan_transfer(seed_user, escrow_annual):
    """Create a $200k/6%/360 mortgage + a derive_from_loan recurring transfer.

    Returns ``(loan_account, escrow_component, scenario_id)``.  The
    transfer's stored default amount is intentionally a stale value so
    the test can prove the live override, not the stored amount, drives
    the result.
    """
    user = seed_user["user"]
    scenario_id = seed_user["scenario"].id
    checking = seed_user["account"]

    loan_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    loan = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=loan_type.id,
            name="Live Mortgage",
            anchor_balance=Decimal("200000.00"),
        ),
    )
    db.session.add(loan)
    db.session.flush()

    params = LoanParams(
        account_id=loan.id,
        original_principal=Decimal("200000.00"),
        current_principal=Decimal("200000.00"),
        interest_rate=Decimal("0.06000"),
        term_months=360,
        origination_date=date(2026, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    insert_origination_event(params)

    escrow = EscrowComponent(
        account_id=loan.id, name="Property Tax",
        annual_amount=escrow_annual, is_active=True,
    )
    db.session.add(escrow)

    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.MONTHLY,
        ),
        day_of_month=1,
    )
    db.session.add(rule)
    db.session.flush()
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=checking.id,
        to_account_id=loan.id,
        recurrence_rule_id=rule.id,
        name="Live Mortgage Payment",
        # Deliberately stale stored amount -- the live override must win.
        default_amount=Decimal("1.00"),
        derive_from_loan=True,
    )
    db.session.add(template)
    db.session.flush()

    periods = seed_user["periods"] if "periods" in seed_user else None
    return loan, escrow, scenario_id, template, rule, periods


def _loan_transfer_shadows(loan_id, scenario_id):
    """Return the projected shadow transactions of the loan's transfers."""
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id.isnot(None),
            Transaction.scenario_id == scenario_id,
        )
        .all()
    )


def test_derived_transfer_amount_tracks_escrow_without_regeneration(
    app, db, seed_user, seed_periods,
):
    """The transfer's live cash amount = P&I + escrow, and reflows on escrow change.

    Loan $200,000 / 6% / 360mo, escrow $3,600/yr:
        P&I    = amortize(200000, 0.06, 360) = 1,199.10
        escrow = 3600 / 12 = 300.00
        PITI   = 1,199.10 + 300.00 = 1,499.10
    After escrow rises to $4,800/yr (400.00/mo):
        PITI   = 1,199.10 + 400.00 = 1,599.10
    The stored Transfer.amount never changes (no regeneration); only the
    live override reflects the new escrow.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, seed_periods, scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        assert shadows, "expected generated shadow transactions"

        overrides = loan_payment_service.live_loan_transfer_amounts(
            scenario_id, shadows,
        )
        # Every shadow of this loan's transfer gets the live PITI.
        assert overrides, "expected live overrides for the derive_from_loan transfer"
        assert all(v == Decimal("1499.10") for v in overrides.values())

        # The stored transfer amounts are untouched (the stale $1.00),
        # proving the amount is live-derived, not regenerated.
        stored_amounts = {
            xfer.amount
            for xfer in db.session.query(Transfer)
            .filter_by(scenario_id=scenario_id)
            .all()
        }
        assert stored_amounts == {Decimal("1.00")}

        # Raise escrow; the live override reflows without regeneration.
        escrow.annual_amount = Decimal("4800.00")
        db.session.commit()

        overrides_after = loan_payment_service.live_loan_transfer_amounts(
            scenario_id, shadows,
        )
        assert all(v == Decimal("1599.10") for v in overrides_after.values())
        # Still no regeneration: stored transfer amounts unchanged.
        stored_after = {
            xfer.amount
            for xfer in db.session.query(Transfer)
            .filter_by(scenario_id=scenario_id)
            .all()
        }
        assert stored_after == {Decimal("1.00")}


def test_non_derived_transfer_has_no_live_override(
    app, db, seed_user, seed_periods,
):
    """A transfer whose template is NOT derive_from_loan gets no override.

    Confirms the seam is dormant unless explicitly enabled (the
    "only new transfers" choice: every pre-existing template is False).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.derive_from_loan = False
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, seed_periods, scenario_id,
        )
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        overrides = loan_payment_service.live_loan_transfer_amounts(
            scenario_id, shadows,
        )
        assert overrides == {}


def test_derived_transfer_due_date_matches_loan_due_date(
    app, db, seed_user, seed_periods,
):
    """A derive_from_loan transfer is due on the loan's true monthly due date.

    The loan card derives its due dates from LoanParams.payment_day via
    rate_period_engine.monthly_due_date.  The transfer recurrence now uses the
    shared _compute_due_date, and the loan template's rule carries
    day_of_month = payment_day (1), so the transfer's parent + both shadows
    land on the 1st of each month -- matching the loan card -- rather than the
    pay-period start (~2 weeks early) they used before.  Over seed_periods
    (biweekly from 2026-01-02), day 1 falls in P2/P4/P6/P8, giving due dates
    2026-02-01, 03-01, 04-01, 05-01.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        created = transfer_recurrence.generate_for_template(
            template, seed_periods, scenario_id,
        )
        db.session.commit()

        assert sorted(x.due_date for x in created) == [
            date(2026, 2, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
            date(2026, 5, 1),
        ]
        for xfer in created:
            # Parent due date equals the loan's contractual monthly due date.
            assert xfer.due_date == monthly_due_date(
                xfer.pay_period.start_date, 1,
            )
            assert xfer.due_date.day == 1
            # Both shadows mirror the parent (Transfer Invariant 3).
            shadows = (
                db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id)
                .all()
            )
            assert len(shadows) == 2
            for s in shadows:
                assert s.due_date == xfer.due_date
