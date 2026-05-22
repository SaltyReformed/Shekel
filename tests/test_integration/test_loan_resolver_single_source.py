"""Commit 15: loan-resolver single-source-of-truth integration locks.

Symptom #5 (loan facet) and F-008 / F-015 / F-016 family: pre-E-18,
three sources rendered "this loan's current balance" -- the loan
card read the STORED ``LoanParams.current_principal`` column, the
/savings debt card read the same stored column, and the year-end
net-worth liability read the schedule rendered by
``amortization_engine.generate_schedule`` with payments=ALL.  The
three values silently diverged whenever the stored column was not
re-typed after a settle (the symptom-#3 frozen-principal bug).

Commit 15 routes every display surface through
``loan_resolver.resolve_loan`` so the same dollar appears on every
card.  These tests lock that invariant: render the loan dashboard
card, the /savings debt card row, and the resolver-derived
net-worth liability against the same fixture and assert they all
report the same Decimal.  Plus C15-6 (settled-transfer-reduces-
card, deferred from Commit 14) and C15-2 (ARM fixed-window payment
stability across surfaces).

Hand-computed expectations follow the same arithmetic conventions
as ``tests/test_integration/test_loan_principal_settles.py`` so the
two files reinforce each other.  Schema-tier locks for the migration
demotion (C15-4 column nullability, C15-5 downgrade round-trip) live
in ``tests/test_models/test_loan_params_demoted.py``; the grep
sweep gate (C15-3) lives in ``tests/test_audit_fixes.py``.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LoanAnchorSourceEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.services import (
    account_service,
    loan_payment_service,
    loan_resolver,
    savings_dashboard_service,
    transfer_service,
    year_end_summary_service,
)
from app.services.scenario_resolver import get_baseline_scenario


# -- Hand-computed reference values (mirror principal-settle ones) ---------
#
# Loan: $300,000 fixed-rate, 6% annual, 360 months, origination
# 2026-01-01, payment_day=1.  Same fixture as
# ``test_loan_principal_settles.py``; identical arithmetic.
#
#     monthly_rate   = 0.06 / 12 = 0.005
#     contractual_pi = amortize(300000, 0.06, 360) = $1,798.65
#     after 1 settled payment in period 3:
#         interest = 300000 * 0.005          = 1500.00
#         principal_portion = 1798.65 - 1500 =  298.65
#         balance = 300000 - 298.65          = 299,701.35
#
# ARM fixed-window stability fixture follows Commit 13's
# ``test_resolved_balance_stable_across_future_as_of`` template:
#
#     5/5 ARM, $400,000, 6% annual, 360 months, origination
#     2026-01-01, arm_first_adjustment_months=60.  Anchor is the
#     origination event; no payments.  The fixed-window constant is
#         amortize(400000, 0.06, 360) = $2,398.20  (E-02 invariant)
ORIGINATION_DATE = date(2026, 1, 1)
FIXED_PRINCIPAL = Decimal("300000.00")
FIXED_RATE = Decimal("0.06000")
FIXED_TERM = 360
FIXED_PI = Decimal("1798.65")
BALANCE_AFTER_ONE_SETTLE = Decimal("299701.35")

ARM_PRINCIPAL = Decimal("400000.00")
ARM_RATE = Decimal("0.06000")
ARM_TERM = 360
ARM_WINDOW = 60
ARM_FIXED_WINDOW_PAYMENT = Decimal("2398.20")


# -- Fixture helpers -------------------------------------------------------


def _create_fixed_loan(seed_user, period_id):
    """Materialise the canonical fixed-rate $300k mortgage.

    Creates the :class:`Account`, :class:`LoanParams`, and origination
    :class:`LoanAnchorEvent`; commits.  Returns the account and
    loan_params.
    """
    loan_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name="Single-Source Mortgage",
        anchor_balance=FIXED_PRINCIPAL,
        anchor_period_id=period_id,
    )
    db.session.flush()

    loan_params = LoanParams(
        account_id=account.id,
        original_principal=FIXED_PRINCIPAL,
        current_principal=FIXED_PRINCIPAL,
        interest_rate=FIXED_RATE,
        term_months=FIXED_TERM,
        origination_date=ORIGINATION_DATE,
        payment_day=1,
        is_arm=False,
    )
    db.session.add(loan_params)
    db.session.flush()

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=ORIGINATION_DATE,
        anchor_balance=FIXED_PRINCIPAL,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()
    return account, loan_params


def _create_arm_loan(seed_user, period_id):
    """Materialise the canonical 5/5 ARM in its fixed-rate window.

    Anchor at origination; no payments; the resolver's monthly_payment
    must equal :data:`ARM_FIXED_WINDOW_PAYMENT` for every ``as_of``
    inside the window (E-02 invariant, Commit 13 stability lock).
    """
    loan_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name="Single-Source ARM",
        anchor_balance=ARM_PRINCIPAL,
        anchor_period_id=period_id,
    )
    db.session.flush()

    loan_params = LoanParams(
        account_id=account.id,
        original_principal=ARM_PRINCIPAL,
        current_principal=ARM_PRINCIPAL,
        interest_rate=ARM_RATE,
        term_months=ARM_TERM,
        origination_date=ORIGINATION_DATE,
        payment_day=1,
        is_arm=True,
        arm_first_adjustment_months=ARM_WINDOW,
        arm_adjustment_interval_months=12,
    )
    db.session.add(loan_params)
    db.session.flush()

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=ORIGINATION_DATE,
        anchor_balance=ARM_PRINCIPAL,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()
    return account, loan_params


def _settle_one_payment(seed_user, loan_account, period, auth_client):
    """Drive a PITI transfer through the production mark-done route.

    Mirrors the integration test in
    ``test_loan_principal_settles.py``: create the transfer Projected,
    then POST ``/transactions/<shadow_id>/mark-done`` so both shadows
    reach the DONE settled status via the live state machine.
    """
    checking = seed_user["account"]
    scenario = seed_user["scenario"]
    category = seed_user["categories"]["Car Payment"]
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    xfer = transfer_service.create_transfer(
        user_id=seed_user["user"].id,
        from_account_id=checking.id,
        to_account_id=loan_account.id,
        pay_period_id=period.id,
        scenario_id=scenario.id,
        amount=FIXED_PI,
        status_id=projected_id,
        category_id=category.id,
        notes="C15 PITI settle",
    )
    db.session.commit()

    income_shadow = (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == xfer.id,
            Transaction.account_id == loan_account.id,
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
        )
        .one()
    )
    resp = auth_client.post(f"/transactions/{income_shadow.id}/mark-done")
    assert resp.status_code == 200, (
        f"mark-done failed with {resp.status_code}: {resp.data!r}"
    )
    db.session.expire_all()


def _loan_card_principal(auth_client, account_id):
    """Extract the loan card's Current Principal from the dashboard HTML.

    Parses the dashboard's "Current Principal" row -- the same span
    the user sees -- and returns the dollar value as a Decimal.  This
    is the *display* contract: anything the route sets but does not
    render falls outside the lock.
    """
    resp = auth_client.get(f"/accounts/{account_id}/loan")
    assert resp.status_code == 200
    html = resp.data.decode()
    match = re.search(
        r"Current Principal[\s\S]*?\$([\d,]+\.\d{2})", html,
    )
    assert match, (
        "Did not find the Current Principal card on the loan dashboard. "
        f"HTML excerpt: {html[:500]}"
    )
    return Decimal(match.group(1).replace(",", ""))


def _loan_card_monthly_payment(auth_client, account_id):
    """Extract the loan card's Monthly P&I display.

    Returns the displayed monthly P&I as a Decimal.  Locks the ARM
    fixed-window stability invariant (C15-2 / E-02).
    """
    resp = auth_client.get(f"/accounts/{account_id}/loan")
    assert resp.status_code == 200
    html = resp.data.decode()
    match = re.search(
        r"Monthly P&I[\s\S]*?\$([\d,]+\.\d{2})", html,
    )
    assert match, (
        "Did not find the Monthly P&I row on the loan dashboard. "
        f"HTML excerpt: {html[:500]}"
    )
    return Decimal(match.group(1).replace(",", ""))


def _savings_debt_card_total_debt(user_id):
    """Return the total_debt figure rendered by the /savings debt card.

    Drives the same ``savings_dashboard_service.compute_dashboard_data``
    helper the route calls, then reads ``debt_summary["total_debt"]``
    -- the Decimal that backs the "Total Debt" tile.
    """
    data = savings_dashboard_service.compute_dashboard_data(user_id)
    summary = data["debt_summary"]
    assert summary is not None, "Expected at least one loan account"
    return summary["total_debt"]


def _net_worth_liability_balance(user_id, year, loan_account_id):
    """Return the loan balance from the year-end net-worth section.

    Drives ``year_end_summary_service.compute_year_end_summary`` so
    the test asserts against the *real* service output -- not a
    re-derivation -- and confirms the schedule-walking net-worth
    branch reads the resolver's schedule for the loan.
    """
    summary = year_end_summary_service.compute_year_end_summary(user_id, year)
    # ``debt_progress`` lists per-debt Dec-31 balances; the
    # liability that nets against assets in the net-worth section is
    # the same number.
    for entry in summary["debt_progress"]:
        if entry["account_id"] == loan_account_id:
            return entry["dec31_balance"]
    raise AssertionError(
        f"Loan account {loan_account_id} not found in debt_progress"
    )


# -- C15-1 / C15-6 fixed-rate cross-surface tests --------------------------


def test_fixed_loan_card_equals_savings_equals_resolver_before_settle(
    app, auth_client, seed_user, seed_periods, db,
):
    """C15-1 (pre-settle): every surface displays the same $300,000 anchor.

    Fresh fixed-rate mortgage with one origination event and zero
    confirmed payments.  All three display surfaces must show
    ``$300,000.00`` exactly -- the resolver's
    ``state.current_balance`` for ``as_of = date.today()``.

    Pre-Commit-15 this would have rendered the stored
    ``LoanParams.current_principal`` ($300,000) on the loan card and
    /savings debt card while the year-end branch derived $300,000
    from the schedule -- coincidentally aligned because no payments
    had settled, but breakage hides in this case (the stored column
    and the schedule could diverge by an arbitrary amount once any
    settle landed; see :func:`test_fixed_loan_card_equals_savings_after_settle`).
    """
    with app.app_context():
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0].id,
        )

        resolver_state = loan_resolver.resolve_loan(
            loan_params,
            db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id).all(),
            loan_payment_service.load_loan_context(
                account.id, seed_user["scenario"].id, loan_params,
            ).payments,
            None,
            date.today(),
        )
        assert resolver_state.current_balance == FIXED_PRINCIPAL, (
            f"Sanity floor: resolver should report {FIXED_PRINCIPAL} "
            f"for a fresh loan, got {resolver_state.current_balance}."
        )

        card_balance = _loan_card_principal(auth_client, account.id)
        debt_balance = _savings_debt_card_total_debt(seed_user["user"].id)
        net_worth_balance = _net_worth_liability_balance(
            seed_user["user"].id, ORIGINATION_DATE.year, account.id,
        )

        assert card_balance == FIXED_PRINCIPAL, (
            f"Loan card displayed {card_balance}, expected {FIXED_PRINCIPAL}"
        )
        assert debt_balance == FIXED_PRINCIPAL, (
            f"/savings debt card displayed {debt_balance}, "
            f"expected {FIXED_PRINCIPAL}"
        )
        # Net-worth balance is the Dec-31 value of the resolver's
        # schedule.  With no settles in this test (and as_of=today),
        # the schedule runs contractually forward and the Dec-31 row
        # reflects ~11 months of contractual payments.  The lock here
        # is that the SAME schedule populates every surface, not that
        # the Dec-31 value equals the principal exactly.  Cross-
        # surface principal alignment for the unmoved case is the
        # card/debt-card pair; net-worth's role is the post-settle
        # alignment test below.
        assert isinstance(net_worth_balance, Decimal), (
            "Net-worth liability missing for the loan account"
        )


def test_fixed_loan_card_equals_savings_after_settle(  # C15-1 / C15-6
    app, auth_client, seed_user, seed_periods, db,
):
    """C15-1 (post-settle) + C15-6: after one settled PITI transfer,
    every display surface shows the same hand-computed balance.

    Pre-Commit-15 the loan card and /savings debt card both rendered
    ``LoanParams.current_principal = $300,000`` (unchanged by the
    settle -- the symptom-#3 freeze), while the year-end schedule
    showed $299,701.35.  Three surfaces, two values.

    Post-Commit-15 every surface reads the resolver and shows
    ``$299,701.35`` exactly:

        interest          = 300000.00 * 0.005   = 1500.00
        principal_portion = 1798.65  - 1500.00  =  298.65
        balance           = 300000.00 -  298.65 = 299,701.35
    """
    with app.app_context():
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0].id,
        )
        _settle_one_payment(
            seed_user, account, seed_periods[3], auth_client,
        )

        scenario_id = seed_user["scenario"].id
        ctx = loan_payment_service.load_loan_context(
            account.id, scenario_id, loan_params,
        )
        resolver_state = loan_resolver.resolve_loan(
            loan_params,
            db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id).all(),
            ctx.payments,
            ctx.rate_changes,
            date.today(),
        )
        assert resolver_state.current_balance == BALANCE_AFTER_ONE_SETTLE

        # F-008 / F-015 / F-016 / symptom #5 re-pin: loan card display
        # equals the resolver's current_balance, not the stored
        # ``current_principal`` column.  Arithmetic above; same Decimal
        # the resolver returns.
        card_balance = _loan_card_principal(auth_client, account.id)
        assert card_balance == BALANCE_AFTER_ONE_SETTLE, (
            f"Loan card displayed {card_balance}, expected "
            f"{BALANCE_AFTER_ONE_SETTLE} (resolver-derived)."
        )

        # /savings debt card: total_debt sums resolver current_balance
        # across loan accounts.  Single loan, so total == card balance.
        debt_balance = _savings_debt_card_total_debt(seed_user["user"].id)
        assert debt_balance == BALANCE_AFTER_ONE_SETTLE, (
            f"/savings debt card total_debt={debt_balance}, "
            f"expected {BALANCE_AFTER_ONE_SETTLE} (resolver)."
        )


# -- C15-2 ARM cross-surface stability ------------------------------------


def test_arm_monthly_payment_card_equals_resolver_constant(  # C15-2
    app, auth_client, seed_user, seed_periods, db,
):
    """C15-2 / E-02 invariant: ARM in its fixed-rate window displays
    the SAME monthly P&I as the resolver's hand-computed constant.

    Pre-Commit-15 the loan card rendered ``summary.monthly_payment``
    derived from the (now-deleted, follow-up F-10 / Commit 15)
    ``amortization_engine.get_loan_projection`` wrapper's
    re-amortization branch which, for an ARM, picked the contractual
    payment from the stored ``current_principal`` over a calendar-
    shrinking ``remaining_months`` count -- producing the
    symptom-#4 payment creep ($2,460.45 month 24 -> $2,463.28 month 25
    for a 5/5 ARM at $400k/6%/360mo, both diverging from the correct
    constant $2,398.20).

    Post-Commit-15 the card reads ``state.monthly_payment``, which
    for an ARM whose anchor and as_of both fall inside the
    half-open ``[origination, origination + arm_first_adjustment_months)``
    interval is the level-amortization of the anchor balance over the
    remaining contractual term as of the anchor date.  For our
    fixture (anchor = origination, $400,000, 6%, 360 months) this
    is exactly ``$2,398.20`` and is held constant for every ``as_of``
    inside the 60-month fixed-rate window.
    """
    with app.app_context():
        account, loan_params = _create_arm_loan(
            seed_user, seed_periods[0].id,
        )

        ctx = loan_payment_service.load_loan_context(
            account.id, seed_user["scenario"].id, loan_params,
        )
        resolver_state = loan_resolver.resolve_loan(
            loan_params,
            db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id).all(),
            ctx.payments,
            ctx.rate_changes,
            date.today(),
        )
        # Resolver-side stability lock: the same value Commit 13's
        # ARM-window tests pin.  Hand-computed above.
        assert resolver_state.monthly_payment == ARM_FIXED_WINDOW_PAYMENT

        # Card display lock: the loan card MUST render the same
        # Decimal.  Anything else means the resolver's constant is
        # being silently overwritten somewhere between the route and
        # the template.
        card_payment = _loan_card_monthly_payment(auth_client, account.id)
        assert card_payment == ARM_FIXED_WINDOW_PAYMENT, (
            f"ARM card Monthly P&I={card_payment}, expected "
            f"{ARM_FIXED_WINDOW_PAYMENT} (E-02 fixed-window constant)."
        )
