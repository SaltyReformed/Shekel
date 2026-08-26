"""C6a: loan_plan assembles the right records -- PLANNED shadows, ESTIMATED fill.

Plan step **C6a** (``docs/audits/balance_architecture/README.md``).  Companion to
``test_loan_plan_forward_oracle.py`` (which pins the fold ARITHMETIC by hand): this
pins the ASSEMBLY -- that :func:`app.services.balance_at._plan.loan_plan` turns a
loan's projected transfer shadows into PLANNED records and fills every FUTURE
contractual slot no record covers with an ESTIMATED one, out to payoff, while never
synthesizing a strictly-PAST installment (the B-9 fix: a missed installment with no
record pays nothing).
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.models.escrow_line import EscrowComponentVersion
from app.models.loan_features import RateHistory
from app.services import loan_loaders, transfer_service
from app.services.balance_at._plan import (
    _PAYOFF_EXTENSION_MONTHS,
    loan_plan,
    memoized_plan,
)
from app.services.balance_at._plan_fold import fold_forward
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._context import _memoize_once
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    loan_income_shadow,
)

#: The read instant for the early-settled-payment case ONLY -- deliberately
#: later than the module-wide :data:`_AS_OF` below, because that case is about a
#: payment settled 2026-05-20 and read before its 2026-06-01 installment falls
#: due.  The test freezes TODAY to this as well as reading at it: the settle must
#: be in the past for the write door to accept it (ruling R-EJ), and the two must
#: agree or the fixture's calendar contradicts itself.
_EARLY_SETTLE_AS_OF = date(2026, 5, 25)

# A short amortizing loan so the whole contractual schedule is enumerable:
# $12,000 at 6% over 6 months, originated 2026-01-01, due on the 1st.
_PRINCIPAL = Decimal("12000.00")
_RATE = Decimal("0.06")
_TERM = 6
_ORIGINATION = date(2026, 1, 1)
# Read the loan mid-life: installments due 02-01, 03-01, 04-01 are PAST; 05-01,
# 06-01, 07-01 are FUTURE.  No recurring payment is configured, so every future
# slot is ESTIMATED and every past one -- unpaid, no record -- pays nothing.
_AS_OF = date(2026, 4, 15)


def _configured_loan(seed_user, db):
    """Create the controlled short loan and return (account, ctx)."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    return account, ctx


def test_a_loan_with_no_recurring_payment_is_all_estimated_future_installments(
    seed_user, db,
):
    """No projected records -> every FUTURE contractual installment is ESTIMATED,

    followed by the post-contractual extension (C8c / N-16).
    """
    account, ctx = _configured_loan(seed_user, db)

    plan = loan_plan(account, ctx)
    payments = plan.payments

    # The contractual schedule the ESTIMATED tier draws from, for cross-checking.
    contractual = contractual_schedule_from_origination(
        account.loan_params, loan_loaders.load_rate_changes(account.id),
    )
    future_rows = [row for row in contractual if row.payment_date >= _AS_OF]

    # Every plan entry is ESTIMATED (no records exist).
    assert payments, "a configured loan must project a forward plan"
    assert all(payment.is_estimated for payment in payments)

    # The plan splits into the future CONTRACTUAL installments then the EXTENSION.
    plan_due = [p.due_date for p in payments]
    contractual_due = plan_due[:len(future_rows)]
    extension_due = plan_due[len(future_rows):]

    # The contractual prefix is exactly 05-01, 06-01, 07-01 -- the PAST installments
    # (02-01, 03-01, 04-01) are absent: a missed installment is not synthesized.
    assert contractual_due == [r.payment_date for r in future_rows]
    assert contractual_due == [
        date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1),
    ]
    # Past the contractual payoff (07-01) the tier EXTENDS with the level payment
    # for _PAYOFF_EXTENSION_MONTHS months (C8c), so a loan behind schedule can clear
    # a few months late instead of reporting no payoff.
    assert len(extension_due) == _PAYOFF_EXTENSION_MONTHS
    assert extension_due[0] == date(2026, 8, 1)  # one month past the contractual last
    # The extension pays the level P&I -- equal to a NON-last contractual
    # installment's payment (05-01 here; no standing extra) -- not the reduced
    # absorbed amount of the final contractual row.
    first_extension = payments[len(future_rows)]
    assert first_extension.cash == future_rows[0].payment
    # The CONTRACTUAL ESTIMATED cash is the contractual P&I, escrow-free; the
    # effective date is the due date (all future, so the as_of + 1d clamp is a no-op).
    for payment, row in zip(payments, future_rows):
        assert payment.cash == row.payment
        assert payment.effective_date == payment.due_date
    # The escrow is the ACCRUAL's since plan step R16-a: a month impounds it, not
    # a payment.  This loan escrows nothing, and there is exactly one charge per
    # period the payments occupy.
    assert all(charge.escrow == Decimal("0.00") for charge in plan.charges)
    assert [charge.on_date for charge in plan.charges] == plan_due


def test_missed_installments_with_no_record_do_not_pay_the_loan_down(seed_user, db):
    """The B-9 fix at the plan level: an unpaid past installment reduces nothing."""
    account, ctx = _configured_loan(seed_user, db)
    plan = loan_plan(account, ctx)

    # Nothing has been paid, so the confirmed present is the full opening balance.
    seed = _PRINCIPAL
    folded = fold_forward(
        seed, _ORIGINATION, plan,
        # as_of, then a date past the missed 04-01 installment but before the
        # first FUTURE one (05-01), then the first real paydown.
        [_AS_OF, date(2026, 4, 30), date(2026, 5, 1)],
    )

    # The three missed installments (02-01, 03-01, 04-01) never happened, so the
    # balance is still the full $12,000 -- not paid down three installments as the
    # old schedule walk did (finding B-9).
    assert folded[_AS_OF] == _PRINCIPAL
    assert folded[date(2026, 4, 30)] == _PRINCIPAL
    # Only the first FUTURE installment (05-01) begins to pay it down.
    assert folded[date(2026, 5, 1)] < _PRINCIPAL
    # Concretely: interest = round(12000 * 0.06 / 12) = 60.00; principal =
    # contractual P&I - 60.00.
    first = plan.payments[0]
    expected = _PRINCIPAL - (first.cash - Decimal("60.00"))
    assert folded[date(2026, 5, 1)] == expected


def _project_loan_payment(seed_user, db, loan, period, amount, due_date):
    """Create a PROJECTED loan-payment transfer and pin its due date."""
    transfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name="Loan Payment",
        ),
    )
    shadow = loan_income_shadow(db.session, transfer.id, loan.id)
    shadow.due_date = due_date
    db.session.commit()
    return shadow


def test_a_projected_record_makes_its_slot_planned_not_estimated(
    seed_user, db, seed_periods,
):
    """A projected shadow becomes PLANNED; its contractual slot is not also ESTIMATED."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    # A projected payment for the 2026-06-01 installment (placed in a period, its
    # due date pinned to the installment it satisfies).
    _project_loan_payment(
        seed_user, db, account, seed_periods[9],
        amount=Decimal("2100.00"), due_date=date(2026, 6, 1),
    )
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

    plan = loan_plan(account, ctx)

    by_due = {payment.due_date: payment for payment in plan.payments}
    # The 2026-06 slot is folded exactly once, as a PLANNED record at its cash --
    # never doubled by an ESTIMATED synthesis (the de-dup).
    assert [p.due_date for p in plan.payments].count(date(2026, 6, 1)) == 1
    june = by_due[date(2026, 6, 1)]
    assert june.is_estimated is False
    assert june.cash == Decimal("2100.00")
    # The other future installments have no record, so they are ESTIMATED.
    assert by_due[date(2026, 5, 1)].is_estimated is True
    assert by_due[date(2026, 7, 1)].is_estimated is True


def test_a_planned_record_keys_its_rate_and_escrow_on_the_due_date(
    seed_user, db, seed_periods,
):
    """A PLANNED record's rate and escrow key on its INSTALLMENT, not its pay period.

    The forward half of finding **N-34** (ruling D5's contract time), and the
    firing control for :func:`app.services.balance_at._plan._planned_from_shadows`.
    The projected payment satisfies the 2026-06-01 installment but is booked in the
    pay period starting 2026-05-08, so a version effective **2026-05-25** lands
    STRICTLY inside that window.  Both a rate change (6% -> 12%) and an escrow
    change ($100/mo -> $500/mo) are placed there:

      * DUE-date keying (as built): ``annual_rate`` 0.12, ``escrow`` 500.00.
      * PERIOD-START keying (the N-34 defect): 0.06 and 100.00.

    This is not cosmetic on the forward side.  The escrow figure is what
    :func:`app.services.balance_at._plan_fold.fold_forward` subtracts from the record's
    cash, and the cash itself is now built on the DUE date's escrow
    (``loan_payment_service._shadow_live_amount``); if the two ends key on
    different dates, the difference lands silently in PROJECTED principal and
    propagates to the forward balance, ``plan_payoff_date``,
    ``plan_required_extra``, the projected Schedule A interest, and the property
    equity chart's debt line.
    """
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    escrow = add_escrow_line(
        db.session, account.id, "Tax", Decimal("1200.00"),
        effective_date=_ORIGINATION,
    )
    db.session.add(EscrowComponentVersion(
        line_id=escrow.line_id,
        effective_date=date(2026, 5, 25),
        annual_amount=Decimal("6000.00"),
    ))
    db.session.add(RateHistory(
        account_id=account.id, effective_date=date(2026, 5, 25),
        interest_rate=Decimal("0.12"),
    ))
    shadow = _project_loan_payment(
        seed_user, db, account, seed_periods[9],
        amount=Decimal("2100.00"), due_date=date(2026, 6, 1),
    )
    # The versions really are inside the window: after the pay-period start,
    # before the installment they govern.
    assert (
        shadow.pay_period.start_date
        < date(2026, 5, 25)
        < shadow.due_date
    )

    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    plan = loan_plan(account, ctx)

    june = {p.due_date: p for p in plan.payments}[date(2026, 6, 1)]
    assert june.is_estimated is False
    # The rate and the escrow are the ACCRUAL's since plan step R16-a, and its
    # date is the period's earliest due -- the INSTALLMENT, which is what this
    # control measures.  Both mutations die here and they die differently: a
    # charge DATED on the pay-period start KeyErrors this lookup, and one dated
    # right but RESOLVED on the period start reads 0.06 / 100.00 against the two
    # asserts below.
    june_charge = {
        charge.on_date: charge for charge in plan.charges
    }[date(2026, 6, 1)]
    assert june_charge.annual_rate == Decimal("0.12")
    assert june_charge.escrow == Decimal("500.00")


def test_two_payments_in_one_month_produce_ONE_charge_at_the_EARLIEST(
    seed_user, db, seed_periods,
):
    """The firing control for :func:`app.services.balance_at._plan._charges_for`.

    **The producer half of plan step R16-a had NO test until an adversarial
    review mutated it and the suite stayed green.**  Replacing ``_charges_for``
    with the pre-R16-a rule -- one charge per PAYMENT -- left 5,427 tests
    passing, because every plan any other test builds through the real producer
    holds exactly one payment per slot, where "one charge per slot" and "one
    charge per payment" are indistinguishable.  The two ``_plan()`` helpers that
    DO build multi-payment plans state their charges by hand, so they grade the
    FOLD and can never reach the builder.  This test builds the multi-payment
    month through ``loan_plan`` itself.

    Two projected payments land in June 2026 -- the 2026-06-01 installment and an
    extra on 2026-06-20 -- and the month must yield exactly ONE charge.

    **Its date is the EARLIEST of the two, and a rate and an escrow version
    effective BETWEEN them are what make that a firing assertion rather than a
    coincidence.**  Dated at the earliest the charge reads 6% / $100.00; dated at
    the latest it reads 12% / $500.00, which is the same wrong-date defect N-34
    names one function over, reached through the charge instead of the payment.
    """
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    escrow = add_escrow_line(
        db.session, account.id, "Tax", Decimal("1200.00"),
        effective_date=_ORIGINATION,
    )
    # Effective strictly BETWEEN the month's two payments, so the two candidate
    # charge dates resolve to different figures.
    db.session.add(EscrowComponentVersion(
        line_id=escrow.line_id,
        effective_date=date(2026, 6, 10),
        annual_amount=Decimal("6000.00"),
    ))
    db.session.add(RateHistory(
        account_id=account.id, effective_date=date(2026, 6, 10),
        interest_rate=Decimal("0.12"),
    ))
    _project_loan_payment(
        seed_user, db, account, seed_periods[9],
        amount=Decimal("2100.00"), due_date=date(2026, 6, 1),
    )
    _project_loan_payment(
        seed_user, db, account, seed_periods[9],
        amount=Decimal("500.00"), due_date=date(2026, 6, 20),
    )

    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    plan = loan_plan(account, ctx)

    june_payments = [
        payment for payment in plan.payments
        if (payment.due_date.year, payment.due_date.month) == (2026, 6)
    ]
    assert sorted(p.due_date for p in june_payments) == [
        date(2026, 6, 1), date(2026, 6, 20),
    ], "precondition: both payments must reach the plan"

    june_charges = [
        charge for charge in plan.charges
        if (charge.on_date.year, charge.on_date.month) == (2026, 6)
    ]
    assert len(june_charges) == 1, (
        "a month charges ONCE however many payments fall in it -- one charge "
        "per PAYMENT is the pre-R16-a rule this test exists to refuse"
    )
    charge = june_charges[0]
    assert charge.on_date == date(2026, 6, 1), "dated at the EARLIEST due"
    # Resolved AT that date: the versions effective 06-10 govern neither.
    assert charge.annual_rate == _RATE
    assert charge.escrow == Decimal("100.00")     # 1,200.00 a year

    # And the whole plan holds one charge per occupied month, no more.
    occupied = {
        (payment.due_date.year, payment.due_date.month)
        for payment in plan.payments
    }
    assert len(plan.charges) == len(occupied)
    assert len({charge.on_date for charge in plan.charges}) == len(plan.charges)


def test_an_early_settled_payment_is_not_re_synthesized_as_estimated(
    seed_user, db, monkeypatch, seed_periods,
):
    """A payment settled by as_of but due after it is in the SEED, not the plan.

    The C3c settled-slot overlap, at the balance: a payment settled on/before
    as_of whose contractual installment is due AT OR AFTER as_of is already paid
    down inside the fold's seed, is not a projected record, and has ``due >=
    as_of`` -- so without a settled-slot exclusion the ESTIMATED tier would
    synthesize it and :func:`fold_forward` would subtract its principal a SECOND
    time.  It must be absent from the plan.

    **Today is moved to the read instant**, overriding this suite's module
    freeze at 2026-03-20 (which its conftest invites a test to do).  The whole
    premise is a payment that HAS settled (2026-05-20), read a few days later;
    under the module clock that settle sits two months in its own future, which
    ruling R-EJ refuses at the write door because a settled row asserts that
    money has already moved.  The fixture's calendar has to contain its own
    today.
    """
    freeze_today(monkeypatch, _EARLY_SETTLE_AS_OF)
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    # A payment for the 2026-06-01 installment, SETTLED early on 2026-05-20.
    transfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], account,
        seed_periods[9], amount=Decimal("2100.00"),
        settled_on=date(2026, 5, 20),
    )
    shadow = loan_income_shadow(db.session, transfer.id, account.id)
    shadow.due_date = date(2026, 6, 1)
    db.session.commit()

    # Read after the settlement but before the installment's due date.
    ctx = BalanceContext.build(seed_user["user"].id, _EARLY_SETTLE_AS_OF)
    plan = loan_plan(account, ctx)

    dues = [payment.due_date for payment in plan.payments]
    # The June installment is in the seed already, so it is NOT re-synthesized.
    assert date(2026, 6, 1) not in dues
    # The genuinely-uncovered July installment still is (ESTIMATED).
    assert date(2026, 7, 1) in dues
    assert all(payment.is_estimated for payment in plan.payments)
    # And no June CHARGE either: a period the plan does not pay in charges
    # nothing, so the seed's own accrual is never counted twice.
    assert date(2026, 6, 1) not in [c.on_date for c in plan.charges]


# ── D-ctx-b: the plan memo is a PUBLIC pass-through cache the seam fills ──────
#
# ``loan_plan`` used to be imported lazily INSIDE a ``BalanceContext.loan_plan``
# method, which made the seam's dependency inversion a real runtime cycle -- one
# that pylint's ``cyclic-import`` could not see, because a type-only import of the
# same module excludes the edge from its graph (finding N-25).  Plan step D0a
# worked around it by INJECTING the builder into the method; plan step D-ctx-b
# retired the injection entirely: the plan is a PUBLIC per-pass cache
# (``BalanceContext.plans``, keyed by account id) that the seam FILLS through the
# ONE funnel ``memoized_plan`` and the shared ``_memoize_once`` primitive.  No
# builder crosses into the context -- the seam owns the derivation, the context
# owns the storage.


def test_the_plan_is_built_once_per_read_pass(seed_user, db):
    """Two seam reads of one loan's forward plan build it ONCE.

    The cache exists because a single ``/savings`` or property render folds the
    same loan's future from four readers (the scalar, the per-period map, the
    liability band, the equity chart).  Proven by identity: the second read
    returns the SAME list object, so no second build happened.
    """
    account, ctx = _configured_loan(seed_user, db)
    assert not ctx.plans, "the cache starts empty"

    first = memoized_plan(account, ctx)
    assert first.payments, "precondition: this loan has a non-empty forward plan"

    # The slot the seam's funnel filled -- keyed on the account id alone now the
    # builder is no longer injected (plan step D-ctx-b).
    assert ctx.plans[account.id] is first

    assert memoized_plan(account, ctx) is first, (
        "the second read must be served from the cache, not rebuilt"
    )


def test_the_cache_stores_on_membership_not_truthiness():
    """An empty result is CACHED, not re-derived on every read.

    ``_memoize_once`` -- the ONE primitive both forward memos
    (``memoized_plan`` / ``memoized_payoff``) fill through -- tests
    ``key not in cache``, never the value's truthiness, because a derivation may
    have a legitimately falsy answer: a ``None`` payoff (a loan that never
    clears).  A truthiness check would rebuild that on EVERY read of every pass,
    unbounded and green under any test that happens to use a loan that clears.
    Pinned directly on the shared primitive, so it holds for the payoff cache
    too.

    **The empty PLAN stopped being the second example at plan step R16-a**, and
    the docstring said otherwise until an adversarial merge review found it one
    site over from where it had already been corrected
    (``_context._memoize_once``).  ``loan_plan`` answered ``[]``; it now answers
    a ``LoanForwardPlan(payments=[], charges=[])``, which is unconditionally
    TRUTHY.  This test is unaffected -- the primitive is generic and its
    ``_build_empty`` below returns a real ``[]`` -- but the CLAIM about the plan
    was false, and a falsy-answer example that is no longer falsy is how the
    rule it argues for gets dropped by the next reader.
    """
    cache: dict[int, list] = {}
    builds = []

    def _build_empty():
        """A derivation whose answer is legitimately falsy."""
        builds.append(1)
        return []

    assert _memoize_once(cache, 7, _build_empty) == []
    # The SECOND read must be served from the cache even though the value is falsy.
    assert _memoize_once(cache, 7, _build_empty) == []
    assert builds == [1], "an empty result must cache, not re-derive"


def test_the_cache_does_not_store_a_raising_build():
    """A build that RAISES is never cached, so a fail-loud guard fires every call.

    ``_memoize_once`` assigns ``cache[key]`` only from a returned value, so the
    seam's ``require_scenario`` guard (raised inside the build for a no-baseline
    context) cannot be worn down by retrying: the key stays absent and the next
    read re-raises.  Pinned on the primitive both forward funnels fill through --
    the property the funnel docstrings assert.
    """
    cache: dict[int, list] = {}
    attempts = []

    def _raising_build():
        """A derivation that fails loud, as the no-baseline guard does."""
        attempts.append(1)
        raise ValueError("no baseline")

    for _ in range(2):
        with pytest.raises(ValueError):
            _memoize_once(cache, 7, _raising_build)
    assert attempts == [1, 1], "a raising build must re-run, never be cached"
    assert 7 not in cache
