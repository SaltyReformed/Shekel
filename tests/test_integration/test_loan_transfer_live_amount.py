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
from app.models.escrow_line import EscrowComponentVersion
from app.models.loan_params import LoanParams
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    account_service,
    loan_payment_service,
    loan_posting_service,
    transfer_recurrence,
)
from app.services.rate_period_engine import monthly_due_date
from tests._test_helpers import (
    add_escrow_line,
    insert_origination_event,
    insert_origination_rate,
)


def _build_derived_loan_transfer(seed_user, escrow_annual):
    """Create a $200k/6%/360 mortgage + a derive_from_loan recurring transfer.

    Returns ``(loan_account, escrow_version, scenario_id)``.  The
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
        term_months=360,
        origination_date=date(2026, 1, 1),
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    insert_origination_event(params)
    insert_origination_rate(params, Decimal("0.06000"))

    escrow = add_escrow_line(
        db.session, loan.id, "Property Tax", escrow_annual,
        effective_date=params.origination_date,
    )

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
    shared compute_due_date, and the loan template's rule carries
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


def test_derived_override_is_per_shadow_date_aware(
    app, db, seed_user, seed_periods,
):
    """A future-dated escrow version changes only the shadows on/after its date.

    Loan $200k / 6% / 360mo, P&I 1,199.10.  Escrow $3,600/yr (300/mo) from
    origination (2026-01-01), then a NEW version $4,800/yr (400/mo) effective
    2026-03-15 on the SAME line.  The live override resolves escrow per shadow
    DATE: a shadow whose pay-period start is before 2026-03-15 keeps PITI
    1,499.10; one on or after picks up 1,599.10.  A single figure per loan
    (today's escrow for every shadow) would wrongly give them all 1,599.10 --
    the bug this per-shadow resolution fixes, and the cash side of the
    cash==split invariant for future-dated escrow.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, seed_periods, scenario_id,
        )
        # Append a second version on the SAME line: 400/mo effective 2026-03-15.
        db.session.add(EscrowComponentVersion(
            line_id=escrow.line_id,
            effective_date=date(2026, 3, 15),
            annual_amount=Decimal("4800.00"),
        ))
        db.session.commit()

        shadows = _loan_transfer_shadows(loan.id, scenario_id)
        overrides = loan_payment_service.live_loan_transfer_amounts(
            scenario_id, shadows,
        )
        cutoff = date(2026, 3, 15)
        before = [s for s in shadows if s.pay_period.start_date < cutoff]
        after = [s for s in shadows if s.pay_period.start_date >= cutoff]
        assert before and after, (
            "seed_periods must place shadows on both sides of 2026-03-15"
        )
        # Old escrow ($300) for pre-effective shadows: 1199.10 + 300 = 1499.10.
        assert all(overrides[s.id] == Decimal("1499.10") for s in before)
        # New escrow ($400) for on/after shadows: 1199.10 + 400 = 1599.10.
        assert all(overrides[s.id] == Decimal("1599.10") for s in after)


def test_settling_derived_loan_payment_captures_live_amount(
    app, db, auth_client, seed_user, seed_periods,
):
    """A one-click settle freezes the LIVE payment-date amount, not the estimate.

    Capture-on-settle (escrow redesign, Option A): the transfer's stored
    default is a deliberately stale $1.00, and the operator settles via the
    ``mark_done`` route WITHOUT typing an actual.  The frozen ``actual_amount``
    must be the live PITI (P&I 1,199.10 + escrow 300.00 = 1,499.10), NOT the
    $1.00 estimate -- so the settled cash carries exactly the escrow the
    genesis split subtracts (cash == split).  The split then divides 1,499.10
    into interest 1,000.00 (200,000 * 0.06 / 12), escrow 300.00, and principal
    199.10 (= P&I 1,199.10 - interest 1,000.00).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, seed_periods, scenario_id,
        )
        db.session.commit()

        income_shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
            )
            .order_by(Transaction.id)
            .first()
        )
        assert income_shadow is not None
        # Pre-settle the shadow shows the stale stored estimate ($1.00).
        assert income_shadow.effective_amount == Decimal("1.00")
        income_shadow_id = income_shadow.id
        transfer_id = income_shadow.transfer_id

        resp = auth_client.post(
            f"/transactions/{income_shadow_id}/mark-done",
        )
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow_id)
        assert settled.status.is_settled is True
        # Capture-on-settle froze the LIVE PITI, not the $1.00 estimate.
        assert settled.actual_amount == Decimal("1499.10")
        assert settled.effective_amount == Decimal("1499.10")
        # Both legs mirror the captured actual (Transfer Invariant 3).
        expense = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer_id,
                Transaction.id != income_shadow_id,
            )
            .one()
        )
        assert expense.actual_amount == Decimal("1499.10")

        # cash == split: the genesis split reads the frozen cash and subtracts
        # the same escrow, leaving principal = P&I.
        splits = loan_posting_service.compute_loan_payment_splits(
            loan.id, scenario_id, date.today(),
        )
        assert len(splits) == 1
        split = splits[0]
        assert split.interest == Decimal("1000.00")
        assert split.escrow == Decimal("300.00")
        assert split.principal == Decimal("199.10")
        assert split.excess == Decimal("0.00")


def test_settled_loan_payment_freeze_is_one_shot(
    app, db, auth_client, seed_user, seed_periods,
):
    """A re-settle never rewrites an already-frozen loan payment's actual cash.

    Capture-on-settle is ONE-SHOT.  After the first settle freezes 1,499.10,
    ``live_loan_payment_amount`` returns None for the now-DONE shadow (the
    ``is_projected`` guard), so a stale-tab re-POST of ``mark_done`` -- admitted
    by the ``done -> done`` identity transition on the still-present mark-paid
    button -- leaves the frozen actual untouched.  Without the guard the
    capture would recompute the CURRENT live amount and silently corrupt the
    confirmed payment's recorded cash (the value it would return here proves the
    skip: a non-None result would overwrite the freeze).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, seed_periods, scenario_id,
        )
        db.session.commit()

        income_shadow_id = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id.isnot(None),
                Transaction.account_id == loan.id,
                Transaction.scenario_id == scenario_id,
            )
            .order_by(Transaction.id)
            .first()
            .id
        )

        resp = auth_client.post(
            f"/transactions/{income_shadow_id}/mark-done",
        )
        assert resp.status_code == 200, resp.data
        db.session.expire_all()
        settled = db.session.get(Transaction, income_shadow_id)
        assert settled.status.is_settled is True
        assert settled.actual_amount == Decimal("1499.10")

        # The freeze is one-shot: the derivation returns None for a settled
        # shadow, so the settle capture can never fire a second time.
        assert loan_payment_service.live_loan_payment_amount(
            settled, scenario_id,
        ) is None

        # A stale-tab re-settle leaves the frozen actual untouched.
        resp2 = auth_client.post(
            f"/transactions/{income_shadow_id}/mark-done",
        )
        assert resp2.status_code == 200, resp2.data
        db.session.expire_all()
        assert db.session.get(Transaction, income_shadow_id).actual_amount == (
            Decimal("1499.10")
        )
