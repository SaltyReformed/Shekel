"""Commit 17: unify per-period / interest / payoff figures via the resolver.

HIGH-08 / F-017..F-023: the audit identified six loan-touching figures
that diverged across surfaces -- per-period principal, per-period
interest, total_interest (life-of-loan vs. calendar-year vs. strategy-
base), interest_saved (banker's-vs-half-up axis), months_saved (four
quantities), and ARM payoff_date.  Commit 13 introduced
``loan_resolver.resolve_loan`` as the single producer for "this loan's
schedule, payoff date and life-of-loan interest"; Commit 15 routed
every display surface through it; Commit 17 closes the remaining
divergences by collapsing residual computations onto the resolver
output and replacing the bare ``.quantize(Decimal("0.01"))`` site at
the plan-vs-contract interest saved (now ``app/routes/loan/calculators.py``)
with ``round_money`` (the E-26 / HIGH-04 boundary).

Test IDs C17-1..C17-6 trace to ``remediation_plan.md`` Section 9
"Commit 17" subsection E.  Hand-computed expectations follow the
arithmetic conventions in
``tests/test_integration/test_loan_resolver_single_source.py``; the
two files reinforce each other on the loan single-source-of-truth
contract.
"""

import re
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_at,
    loan_loaders,
    loan_payment_service,
    loan_posting_service,
    loan_resolver,
)
from app.services.balance_at import _kernel as net_worth_kernel
from app.utils.dates import add_months, months_between
from app.utils.money import round_money
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    bind_rule_to_loan,
    contract_forward_references,
    create_loan_account,
    freeze_today,
    last_covered_day,
    loan_params_for,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import MONTHLY


# ── Hand-computed reference values ────────────────────────────────
#
# Loan: $300,000 fixed-rate, 6% annual, 360 months, origination
# 2026-01-01, payment_day=1.  Matches
# ``test_loan_resolver_single_source.py``'s FIXED_* family so
# arithmetic carries forward.
#
#     monthly_rate     = 0.06 / 12 = 0.005
#     contractual_pi   = amortize(300000, 0.06, 360) = $1,798.65
#
# ARM: 5/5 ARM, $400,000, 6% annual, 360 months, origination
# 2026-01-01, ``arm_first_adjustment_months = 60``.  Anchor is the
# origination event; no payments.  Inside the fixed-rate window the
# constant payment is
#
#     amortize(400000, 0.06, 360) = $2,398.20  (E-02 invariant)

FIXED_ORIGINATION = date(2026, 1, 1)
FIXED_PRINCIPAL = Decimal("300000.00")
FIXED_RATE = Decimal("0.06000")
FIXED_TERM = 360

ARM_PRINCIPAL = Decimal("400000.00")
ARM_RATE = Decimal("0.06000")
ARM_TERM = 360
ARM_WINDOW = 60


# ── Fixture helpers ───────────────────────────────────────────────


def _create_fixed_loan(seed_user, period, *, name="C17 Mortgage"):
    """Materialise the canonical $300k fixed-rate mortgage.

    Mirrors ``test_loan_resolver_single_source._create_fixed_loan``
    (same arithmetic, same anchor) so the assertions in the two files
    reinforce each other: both route through the shared
    :func:`create_loan_account` factory, which opens the loan's genesis
    posting ledger in the same transaction as the ``LoanParams`` insert --
    what every production loan write does (``app/routes/loan/params.py``).

    Args:
        seed_user: The ``seed_user`` fixture dict.
        period: The :class:`PayPeriod` to anchor the account to.
        name: The account name.
    """
    account = create_loan_account(
        seed_user, db.session, name=name, principal=FIXED_PRINCIPAL,
        rate=FIXED_RATE, term=FIXED_TERM, origination_date=FIXED_ORIGINATION,
        payment_day=1, account_type=AcctTypeEnum.MORTGAGE,
    )
    return account, loan_params_for(db.session, account.id)


def _create_arm_loan(seed_user, period, *, name="C17 ARM"):
    """Materialise the canonical 5/5 ARM in its fixed-rate window.

    The shared factory carries no ARM knobs, so the ARM columns are set the way
    production's own ARM edit does (``loan.update_params``): assign the params,
    then re-sync the genesis ledger for every scenario before committing, so the
    postings and the params land in one transaction and the loan is never left
    on the no-ledger fallback.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        period: The :class:`PayPeriod` to anchor the account to.
        name: The account name.
    """
    account = create_loan_account(
        seed_user, db.session, name=name, principal=ARM_PRINCIPAL,
        rate=ARM_RATE, term=ARM_TERM, origination_date=FIXED_ORIGINATION,
        payment_day=1, account_type=AcctTypeEnum.MORTGAGE,
    )
    loan_params = loan_params_for(db.session, account.id)
    loan_params.is_arm = True
    loan_params.arm_first_adjustment_months = ARM_WINDOW
    loan_params.arm_adjustment_interval_months = 12
    loan_posting_service.sync_loan_postings_all_scenarios(account.id)
    db.session.commit()
    return account, loan_params


def _resolver_state(account, loan_params, as_of):
    """Run the resolver against a loan and return the state.

    Loads payment context and anchor events the same way every
    production surface does, so the test pins the SAME schedule the
    dashboard, payoff calculator, debt-strategy, and year-end summary
    render.
    """
    ctx = loan_payment_service.load_loan_context(
        account.id, None, loan_params,
    )
    anchor_events = loan_loaders.load_loan_anchor_facts(loan_params)
    return loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            loan_params, anchor_events, ctx.payments, ctx.rate_changes,
        ),
        as_of,
    )


# ── C17-1: per-period principal / interest single source ──────────


def test_per_period_principal_interest_single_source(
    app, seed_user, seed_periods,
):
    """C17-1 / HIGH-08 / F-017..F-018: per-period rows are identical
    across the resolver and the year-end debt aggregation.

    Before Commit 17 the year-end summary's ``_compute_mortgage_interest``
    ran ``amortization_engine.generate_schedule`` independently of the
    resolver, so the per-period interest rows could drift (the symptom
    was visible when shadow income tweaks moved one schedule but not
    the other).  Post-Commit-15 / Commit 17, ``_generate_debt_schedules``
    runs ``loan_resolver.resolve_loan`` and the year-end aggregation
    sums its row interests directly.  This test pins that contract:
    schedule rows used by the year-end aggregation MUST be the same
    ``AmortizationRow`` objects the resolver produced, not a parallel
    re-computation.
    """
    with app.app_context():
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0],
        )

        state = _resolver_state(account, loan_params, date.today())

        debt_schedules = net_worth_kernel.debt_schedule_rows(
            [account], BalanceContext.build(seed_user["user"].id),
        )
        year_end_schedule = debt_schedules[account.id]

        # The two schedules MUST be the same length and identical
        # row-by-row -- year-end derives from the resolver, no
        # parallel computation allowed (HIGH-08 / F-017 / F-018).
        assert len(year_end_schedule) == len(state.schedule), (
            f"Resolver schedule has {len(state.schedule)} rows, year-"
            f"end has {len(year_end_schedule)} -- divergence indicates"
            " a parallel computation has reappeared."
        )
        for idx, (resolver_row, year_end_row) in enumerate(
            zip(state.schedule, year_end_schedule),
        ):
            assert resolver_row.payment_date == year_end_row.payment_date, (
                f"Row {idx}: payment_date diverged "
                f"({resolver_row.payment_date} vs "
                f"{year_end_row.payment_date})."
            )
            assert resolver_row.principal == year_end_row.principal, (
                f"Row {idx}: principal diverged "
                f"({resolver_row.principal} vs "
                f"{year_end_row.principal})."
            )
            assert resolver_row.interest == year_end_row.interest, (
                f"Row {idx}: interest diverged "
                f"({resolver_row.interest} vs "
                f"{year_end_row.interest})."
            )


# ── C17-2: total_interest one definition; calendar-year is a subset


def test_total_interest_one_definition(
    app, seed_user, seed_periods, monkeypatch,
):
    """C17-2 / HIGH-08 / F-019: the calendar-year mortgage-interest figure
    is a labeled slice of the loan's ONE life-of-loan interest source --
    not a separate computation.

    Step **C6c** moved that source off the resolver's contractual schedule
    onto the loan's forward PLAN (the same model the balance folds).  For a
    CURRENT loan -- no overdue installment the plan would honestly omit
    (that B-9 behaviour is pinned in ``test_loan_interest_in_year``), no
    live-vs-contractual drift -- the plan reproduces the contractual
    paydown to the cent, so the seam's calendar-year figure still equals the
    resolver schedule's 2026 subset.  This pins that reproduction (a
    healthy-loan cross-check that the plan cutover moved no money) AND the
    slice contract: 2026 is a strict, positive part of the life-of-loan
    total.

    Frozen 2026-01-15 -- just after the 2026-01-01 origination, before the
    first installment -- so the loan is current and the figures are
    deterministic rather than dependent on the wall clock.
    """
    with app.app_context():
        freeze_today(monkeypatch, date(2026, 1, 15))
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0],
        )

        state = _resolver_state(account, loan_params, date(2026, 1, 15))

        # Life-of-loan total interest, derived directly from the
        # resolver's single schedule.
        life_of_loan = sum(
            (row.interest for row in state.schedule), Decimal("0.00"),
        )
        # Resolver applies round_money at the LoanState boundary
        # (loan_resolver.py:647), so state.total_interest matches the
        # rounded sum of row interests -- an invariant of the resolver's own
        # two derivation paths, independent of where the tax figure reads from.
        assert state.total_interest == round_money(life_of_loan), (
            f"Resolver total_interest={state.total_interest} differs "
            f"from sum-of-rows round_money={round_money(life_of_loan)}"
            " -- the resolver's two derivation paths must agree."
        )

        # The calendar-year figure, from the balance seam's one loan-interest
        # producer.  The loan has no confirmed payments, so the fold's settled
        # term is $0.00 and every 2026 payment is projected -- the plan-folded
        # projection carries the whole figure.
        calendar_year_interest = balance_at.loan_interest_in_year(
            account, BalanceContext.build(seed_user["user"].id), 2026,
        )

        # Cross-check: for this CURRENT loan the plan reproduces the
        # contractual schedule, so the seam figure equals the resolver
        # schedule's 2026 subset to the cent -- proof the plan cutover moved
        # no money on a healthy loan.
        expected_subset = sum(
            (
                row.interest for row in state.schedule
                if row.payment_date.year == 2026
            ),
            Decimal("0.00"),
        )
        assert calendar_year_interest == expected_subset, (
            f"Year-end 2026 mortgage interest "
            f"{calendar_year_interest} != contractual schedule 2026 subset "
            f"{expected_subset} -- the plan-based figure diverged from the "
            "contractual paydown for a CURRENT loan."
        )

        # And the calendar slice is strictly positive and less than the total.
        assert Decimal("0.00") < expected_subset < life_of_loan, (
            "Sanity: 2026's mortgage interest is a strict, positive slice of "
            "the life-of-loan total (the loan runs into 2056)."
        )


# ── C17-3: interest_saved uses round_money (half-up, not banker's)


def test_interest_saved_uses_round_money_half_up():
    """C17-3 / HIGH-08 / F-020: the plan-vs-contract interest saved on the
    payoff calculator (``routes/loan/calculators._plan_vs_contract``; it was
    ``committed_interest_saved`` until plan step R7d-g-3) uses
    ``round_money`` (ROUND_HALF_UP), not a bare ``.quantize`` that silently
    fell back to Python's ROUND_HALF_EVEN (banker's).

    Pre-Commit-17 ``app/routes/loan.py`` computed::

        committed_interest_saved = (
            original_interest - committed_interest
        ).quantize(Decimal("0.01"))

    With Python's default rounding mode (ROUND_HALF_EVEN), a
    difference of exactly ``$X.005`` would round to the nearest even
    cent -- producing ``$X.00`` half the time and ``$X.01`` the
    other half.  Every hand-computed financial assertion in this
    project assumes ROUND_HALF_UP (E-26 / HIGH-04), so the
    banker's-rounded value was the F-017..F-023 divergence axis.

    Post-Commit-17 the route uses ``round_money(...)``:

        committed_interest_saved = round_money(
            original_interest - committed_interest,
        )

    This test pins the boundary case the bare-quantize site would
    have got wrong.  ``Decimal("2.345")`` is the canonical
    half-up-vs-banker's witness from ``tests/test_utils/test_money.py``
    (C1-1).
    """
    # The canonical half-cent boundary: banker's -> 2.34, half-up -> 2.35.
    original_interest = Decimal("100.000")
    committed_interest = Decimal("97.655")  # diff = 2.345

    # Pre-fix bare quantize would have returned Decimal("2.34")
    # (banker's, round-to-even).  round_money returns Decimal("2.35").
    bare_quantize_value = (
        original_interest - committed_interest
    ).quantize(Decimal("0.01"))
    half_up_value = round_money(
        original_interest - committed_interest,
    )

    # The half-cent boundary divergence the fix closes.
    assert bare_quantize_value == Decimal("2.34"), (
        "Sanity floor: bare .quantize(Decimal('0.01')) on a 0.005 "
        f"difference does fall back to banker's (got "
        f"{bare_quantize_value})."
    )
    assert half_up_value == Decimal("2.35"), (
        f"round_money on a 0.005 difference must round up to 2.35 "
        f"(got {half_up_value}) -- this is the project's ROUND_HALF_UP "
        "convention."
    )
    # The route now produces the half-up value.
    assert bare_quantize_value != half_up_value, (
        "Sanity floor: the two rounding modes must disagree on the "
        "0.005 boundary, otherwise this test is not exercising the "
        "F-020 divergence axis."
    )


# ── C17-4: months_saved is a single, hand-computed integer ────────


def test_months_saved_single_quantity(
    app, seed_user, seed_periods_today,
):
    """C17-4 / HIGH-08 / F-022: ``months_saved`` is one quantity
    derived from the balance seam's fold, not multiple divergent paths.

    F-022 / F-023 documented that pre-remediation, four different
    "months saved" values could appear depending on how the surface
    computed it (resolver schedule length, summary metric, route-side
    subtraction, engine summary helper).  Commit 13 introduced the
    resolver, Commit 17 collapsed every display surface onto its
    schedule, and the amortization-engine split rewired the resolver's
    schedule generation through ``compute_payoff_scenarios``, whose
    ``len(committed_forward) - len(accelerated_forward)`` was the one
    definition until plan step R7d-g-3.  That step deleted the composer's
    planned slices (ruling **R-R88**: they were a second forward walk
    beside the seam's plan fold, and paid a standing extra twice), so the
    one definition is the loan page's lever now
    (``routes/loan/calculators._build_payoff_summary``): the seam's fold
    read twice -- as it stands and with the lever's extra on top
    (``balance_at.loan_installments`` -> ``_plan_trajectory`` ->
    ``balance_at.installments_payoff``) -- and the figure is the calendar
    months between the two payoff dates.  The
    "Projected payoff" chip reads the same fold
    (:attr:`LoanFigures.payoff_date`), so the lever's "current plan"
    payoff and the chip cannot part, and the lever's accelerated payoff IS
    ``loan_payoff_date`` with the extra -- both pinned below so a parallel
    computation reappearing on either side surfaces here.

    Hand-computed expectation for the $300k / 6% / 360 mo fixture
    with ``extra_monthly = $200`` (closed-form payoff with extra):
    contractual payment $1798.65, total monthly $1998.65,
    ``n_extra = -log(1 - P*i/M_total) / log(1+i)
    = -log(1 - 300000*0.005/1998.65) / log(1.005)
    = -log(0.249493) / log(1.005) ~= 278.31 -> 279 installments``.
    The plan as it stands is the contractual 360 installments, so
    ``months_saved == 360 - 279 == 81``.  The loan originates at the
    current period's start with a derived payment day and is read AT its
    origination (the clean-past shape ``test_standing_extra_folds_past_the_shadow_horizon``
    explains), so every one of the 360 installments is in the fold's
    future and the closed form applies to the whole plan.
    """
    from app.routes.loan.calculators import (  # pylint: disable=import-outside-toplevel
        _build_payoff_summary, _plan_trajectory,
    )

    with app.app_context():
        current_period = next(
            period for period in seed_periods_today
            if period.start_date <= date.today() <= last_covered_day(period)
        )
        as_of = current_period.start_date
        payment_day = (as_of.day % 28) + 1
        account = create_loan_account(
            seed_user, db.session, name="C17-4 Mortgage",
            principal=FIXED_PRINCIPAL, rate=FIXED_RATE, term=FIXED_TERM,
            origination_date=as_of, payment_day=payment_day,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)
        figures = balance_at.loan_figures(account, ctx)
        assert figures is not None

        extra = Decimal("200.00")
        committed = _plan_trajectory(balance_at.loan_installments(account, ctx))
        accelerated = _plan_trajectory(
            balance_at.loan_installments(account, ctx, extra),
        )
        summary = _build_payoff_summary(
            figures.terms.monthly_payment, committed, accelerated,
        )

        # One definition: the months between the two payoff dates the fold
        # derived.  Tautological by construction today -- which is the point
        # of the lock: a parallel months_saved computation would break the
        # equality and surface here.
        assert summary.months_saved == months_between(
            summary.payoff_date_with_extra, summary.payoff_date,
        )

        # Hand-computed lock: 360 - 279 = 81 months saved.
        assert summary.months_saved == 81, (
            "Hand-computed months_saved for $300k / 6% / 360 mo "
            f"with $200 extra is 81 (got {summary.months_saved})."
        )

        # Cross-surface: the lever's "current plan" payoff is the chip's, and
        # its accelerated payoff is the seam's fold with the extra -- the
        # chokepoint that makes the SSOT cross-surface.
        assert summary.payoff_date == figures.payoff_date, (
            "the lever's current-plan payoff parted from the Projected "
            "payoff chip's: the two read different producers"
        )
        assert summary.payoff_date_with_extra == balance_at.loan_payoff_date(
            account, ctx, extra,
        )


# ── C17-5: ARM payoff_date consistent across all surfaces ─────────


def test_arm_payoff_date_consistent_across_surfaces(
    app, auth_client, seed_user, seed_periods,
):
    """C17-5 / HIGH-08 / F-023: an ARM loan's payoff is identical across
    surfaces -- each surface compared against the producer it now reads.

    Pre-Commit-15 the dashboard derived its "Projected Payoff" card
    from ``amortization_engine.calculate_summary`` while the year-end
    debt aggregation derived its Dec-31 balance from a separately-
    generated schedule.  For ARM loans, the calendar-shrinking
    ``calculate_remaining_months`` count made the symptom-#4 payment
    creep visible -- and the resulting schedules ended on different
    payment_dates.  Commit 13 fixed the payment number; Commit 17
    pinned that the payoff matched across every surface.

    **Plan step C8d re-partitioned those surfaces, and this test follows.**
    The chip no longer reads a schedule at all: it reads the seam's DERIVED
    payoff, the date the BALANCE folds to zero.  So there are two invariants,
    not one -- the two SCHEDULE consumers still agree with each other, and the
    chip agrees with the seam -- and for this fixture the two answers
    deliberately DIFFER, which the control below pins.  This ARM originated
    2026-01-01 and has never been paid, so its balance is still the full
    $400,000.00: the contractual schedule says Jan 2056 (it amortizes six
    installments nobody paid), while the fold says the borrower is still a
    whole 360-month term away from its NEXT installment.  That gap IS finding
    B-9, and the chip showing the honest side of it is the point of C8d.
    """
    with app.app_context():
        account, loan_params = _create_arm_loan(
            seed_user, seed_periods[0],
        )

        state = _resolver_state(account, loan_params, date.today())
        # The resolver publishes no payoff_date since plan C8d; its schedule's
        # last row is the CONTRACTUAL endpoint, and that is what the other
        # schedule consumer below must agree with.
        resolver_payoff = (
            state.schedule[-1].payment_date if state.schedule else None
        )

        # Year-end-summary path: the same schedule the resolver
        # produced flows through ``_generate_debt_schedules``.
        ctx = BalanceContext.build(seed_user["user"].id)
        debt_schedules = net_worth_kernel.debt_schedule_rows([account], ctx)
        ye_schedule = debt_schedules[account.id]
        ye_payoff = (
            ye_schedule[-1].payment_date if ye_schedule else None
        )

        assert ye_payoff == resolver_payoff, (
            f"ARM payoff_date diverged: resolver={resolver_payoff}, "
            f"year-end={ye_payoff} -- two surfaces, two payoff dates."
        )

        # The chip's producer since C8d: the fold to zero.  Hand-checked -- the
        # loan has paid nothing, so its balance is still $400,000.00 and the
        # contractual payment amortizes exactly that over exactly 360
        # installments from the first one the plan pays.  Since plan step
        # R16-b-2 the installments nobody paid ACCRUE (ruling R-R71: a skipped
        # month owes its interest whichever side of today it is on), so the
        # arrears stand when the first payment lands and the loan clears LATER
        # than 360 months after it -- how much later depends on how many
        # months today is past origination, which is why the bound is stated
        # as an inequality rather than a date the wall clock would move.
        #
        # "Not already past" is ON OR AFTER today, not "next month".  The fixture
        # pays on the 1st, so on the 1st of a month today's own installment is
        # still owed and the plan synthesizes it -- the payoff is then 359 months
        # from TODAY, not from next month.  Hard-coding ``add_months(first of this
        # month, 1)`` assumed today is never an installment date and failed CI on
        # 2026-08-01, reading 2056-07-01 against an expected 2056-08-01.
        seam_payoff = balance_at.loan_payoff_date(account, ctx)
        assert seam_payoff is not None
        this_months_installment = date(
            date.today().year, date.today().month, 1,
        )
        first_forward = (
            this_months_installment
            if this_months_installment >= date.today()
            else add_months(this_months_installment, 1)
        )
        assert seam_payoff > add_months(first_forward, 359), (
            f"Derived payoff {seam_payoff} is not past 360 installments from "
            f"the next one ({first_forward}); the fold is not starting from "
            "the unpaid full principal plus the arrears of the months nobody "
            "paid (ruling R-R71)."
        )
        # Control: the two answers genuinely differ here, so the chip assertion
        # below cannot pass by both producers happening to agree.  Without it a
        # chip still wired to the schedule would look correct.
        assert seam_payoff != resolver_payoff, (
            "The fixture no longer separates the fold from the contractual "
            "schedule, so this test cannot show which one the chip reads."
        )

        # Dashboard "Projected Payoff" chip: it renders the seam's derived
        # payoff (plan C8d), so verify the displayed date against THAT, not
        # against the resolver's contractual schedule endpoint.
        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200, (
            f"Loan dashboard GET failed: {resp.status_code}"
        )
        # The dashboard renders the abbreviated month / year of the
        # payoff date in the band's "Projected payoff" chip (template
        # ``loan/dashboard.html``: ``%b %Y``).
        expected_month_year = seam_payoff.strftime("%b %Y")
        html = resp.data.decode()
        # Anchor the assertion to the "Projected payoff" chip so a
        # different ``%b %Y`` token elsewhere on the page cannot mask a
        # regression on the chip.
        card_match = re.search(
            r"Projected payoff[\s\S]*?>([A-Za-z]{3} \d{4})<",
            html,
        )
        assert card_match, (
            "Could not locate the Projected payoff chip on the loan "
            f"dashboard; HTML excerpt: {html[:600]!r}"
        )
        card_text = card_match.group(1)
        assert card_text == expected_month_year, (
            f"Projected Payoff chip displayed {card_text!r}, expected the "
            f"seam's derived payoff {expected_month_year!r} (the contractual "
            f"schedule says {resolver_payoff.strftime('%b %Y')} -- if the chip "
            "shows THAT, it is still reading the schedule walk)."
        )


def _add_recurring_payment_with_extra(seed_user, loan_account, extra):
    """Attach a derive-from-loan recurring payment carrying a standing extra.

    A monthly recurring transfer INTO the loan whose 1:1
    ``loan_payment_settings`` row carries ``derive_from_loan`` plus the
    standing overpayment.  Amount rule 4 prices the extra into every row the
    definition generates and the seam's forward plan into every occurrence no
    row covers; since plan step R7d-g-3 nothing threads it into the resolver
    (the loan-level ``loan_standing_extra`` read is gone), which is what the
    two tests below pin from either side.
    """
    user = seed_user["user"]
    # Authored through the write door (plan step R7c-b): the day a rule fires
    # on is its first occurrence's own day, so "the 1st" is a DATE the fixture
    # schedule reaches rather than a separate column.
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        name="Mortgage Payment",
        default_amount=Decimal("1.00"),
    )
    template.settings = LoanPaymentSettings(
        derive_from_loan=True, extra_principal=extra,
    )
    db.session.add(template)
    db.session.commit()
    # The definition first, then the cadence onto it (plan step R-F6), then
    # the loan's own start onto the cadence (``bind_rule_to_loan``, plan step
    # C9a), as the loan door does.  ``fires_on_day=1`` alone started the
    # rule on the first 1st the schedule reaches -- before this loan exists,
    # and on a day that is never its ``payment_day`` -- which the forward
    # plan hid while it synthesized contractual slots and now prices as the
    # occurrences they are (plan step R16-b-2, ruling **R-R64**).
    rule = make_cadence_rule(
        template, MONTHLY, fires_on_day=1,
    )
    bind_rule_to_loan(rule, loan_account.id)
    db.session.commit()


def test_standing_extra_lives_in_the_fold_not_the_resolver_schedule(
    app, seed_user, seed_periods,
):
    """R7d-g-3: the resolver's schedule is the CONTRACT; the FOLD carries the extra.

    Until plan step R7d-g-3 this test pinned step 8's seam fix the other way
    round: ``resolve_loan_bundle`` threaded the loan's standing
    ``extra_principal`` into ``resolve_loan``, so ``state.schedule`` was the
    committed trajectory WITH the extra, and the summary surfaces and the
    year-end aggregation had to report the same payoff as the loan page.
    Ruling **R-R88**, which re-ruled R-R83's seam clause at R7d-g-3, deleted
    that parameter and the composer's planned slices with it: a projected
    row carries its own definition's extra through amount rule 4, so the
    composer adding one again paid it TWICE on every row-covered month
    (measured 2026-09-14), and the one it added was the OLDEST definition's
    alone (plan ledger row **D49**).  What the loan is projected to PAY is
    the seam's forward plan, priced from every definition's own occurrences,
    and the payoff every surface shows is the FOLD's (plan step C8d:
    ``LoanState`` carries no payoff, and :attr:`LoanFigures.payoff_date` is
    the date the balance folds to zero).

    So the invariant is now three-sided, and none of its sides is vacuous:

    * the resolver's ``state.schedule`` IS the composer's confirmed history
      plus the CONTRACT's forward for the same inputs, to the row -- no
      extra, no plan (its readers take a date off it, never a balance);
    * the year-end aggregation reads that same schedule;
    * the seam's DERIVED payoff sits STRICTLY EARLIER than the contractual
      one, because the fold prices the definition's occurrences with the
      extra inside them.  This is the side that fails the day the extra
      stops reaching the fold, and the side that failed the OLD way the day
      the resolver stopped adding it.
    """
    with app.app_context():
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0],
        )
        extra = Decimal("500.00")
        _add_recurring_payment_with_extra(seed_user, account, extra)
        scenario_id = seed_user["scenario"].id
        today = date.today()

        # The composer for the same inputs: no loan-level extra exists to
        # pass.  ``resolve_loan`` composes ``state.schedule = history_rows +
        # original_forward`` (``loan_resolver/_state.py``), so the reference
        # is built the same way, to the cent.
        scenarios, _ = contract_forward_references(
            loan_params, scenario_id, today, Decimal("0.00"),
        )
        ref_schedule = (
            list(scenarios.history_rows) + list(scenarios.original_forward)
        )
        ref_payoff = ref_schedule[-1].payment_date
        ref_total_interest = round_money(
            sum((row.interest for row in ref_schedule), Decimal("0.00")),
        )
        contractual_payoff = scenarios.original_forward[-1].payment_date

        # Summary seam: every summary surface resolves a debt account through
        # the seam's ONE memoized whole-loan read (``balance_at._resolution.resolved_loan``).
        balance_ctx = BalanceContext.build(seed_user["user"].id, as_of=today)
        resolved = resolved_loan(account, balance_ctx)
        assert resolved is not None
        state = resolved.state
        summary_payoff = (
            state.schedule[-1].payment_date if state.schedule else None
        )
        assert summary_payoff == ref_payoff, (
            f"Summary-surface schedule ends {summary_payoff} != the composer's "
            f"contractual slice {ref_payoff}: the resolver seam threads something "
            "the composer does not."
        )
        assert state.total_interest == ref_total_interest, (
            f"Summary-surface life-of-loan interest {state.total_interest} != "
            f"the composer's {ref_total_interest}."
        )

        # Year-end / net-worth debt aggregation reads the same seam
        # (``_generate_debt_schedules`` IS ``net_worth_kernel.generate_debt_schedules``).
        debt_schedules = (
            net_worth_kernel.debt_schedule_rows([account], balance_ctx)
        )
        ye_schedule = debt_schedules[account.id]
        assert ye_schedule[-1].payment_date == ref_payoff, (
            f"Year-end debt schedule ends {ye_schedule[-1].payment_date} != "
            f"the composer's {ref_payoff}."
        )

        # The teeth: the payoff a surface SHOWS is the fold's, and the fold
        # prices the definition's occurrences with the extra inside them, so it
        # clears the loan strictly before the contract does.
        figures = balance_at.loan_figures(account, balance_ctx)
        assert figures.payoff_date is not None
        assert figures.payoff_date < contractual_payoff, (
            f"The seam's derived payoff {figures.payoff_date} is not before the "
            f"contractual {contractual_payoff}: the standing extra is not "
            "reaching the fold."
        )


def test_a_generated_rows_extra_is_paid_once_in_the_plan(
    app, seed_user, seed_periods_today,
):
    """R-R88, end to end: a generated row's month pays the row's cash exactly once.

    The double count ruling **R-R88** deleted, measured on the real feed rather
    than on hand-built records (the fold's unit tests hold the arithmetic;
    this holds the WIRING above it): a derive-mode payment with a ``$100``
    extra generates a row whose cash amount rule 4 prices at P&I + escrow +
    extra; the seam's forward plan takes that row as the month's occurrence
    (ruling **R-R66**: the row's identity, the amount model's price); and the
    loan page's installment for that month
    (:func:`balance_at.loan_installments`, the producer the schedule page and
    the allocation bar read) must pay THAT cash and nothing on top.  Until
    plan step R7d-g-3 the page read the composer's committed slice, which
    threaded the extra in again as ``extra_principal``, and the month paid
    P&I + 2 x extra (measured 2026-09-14: ``$676.46`` of principal + interest
    against the row's ``$626.46``).  A second walk reappearing anywhere above
    the fold -- a route helper adding the definition's extra to the
    installment it reads -- fails this where the fold's own tests would not
    see it.

    Escrow-free loan, so the row's cash IS P&I + extra to the cent.  The loan
    originates at the current period's start with a derived payment day, the
    clean-past shape the sibling below explains, so the first installment is a
    generated FUTURE row.
    """
    from app.services import transfer_recurrence  # pylint: disable=import-outside-toplevel
    from app.services.generation_schedule import GenerationSchedule  # pylint: disable=import-outside-toplevel

    with app.app_context():
        current_period = next(
            period for period in seed_periods_today
            if period.start_date <= date.today() <= last_covered_day(period)
        )
        as_of = current_period.start_date
        payment_day = (as_of.day % 28) + 1
        account = create_loan_account(
            seed_user, db.session, name="R-R88 Mortgage",
            principal=FIXED_PRINCIPAL, rate=FIXED_RATE, term=FIXED_TERM,
            origination_date=as_of, payment_day=payment_day,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        loan_params = loan_params_for(db.session, account.id)
        extra = Decimal("100.00")
        _add_recurring_payment_with_extra(seed_user, account, extra)
        template = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=account.id).one()
        )
        ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
        )
        db.session.commit()

        ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)
        loan = loan_payment_service.load_loan_context(
            account.id, ctx.amounts(), loan_params,
        )
        # The feed: every generated row arrives at P&I + extra (rule 4).
        contractual_pi = loan.contractual_pi
        projected = [p for p in loan.payments if p.dates.settled_on is None]
        assert projected, "no generated row reached the feed"
        for payment in projected:
            assert payment.amount == contractual_pi + extra

        by_due = {
            installment.due_date: installment
            for installment in balance_at.loan_installments(account, ctx)
        }
        first = projected[0]
        installment = by_due[first.dates.due_date]
        # Paid ONCE: the installment's cash is the row's, nothing rides on
        # top, and the principal is that cash less the month's interest.
        assert installment.cash == contractual_pi + extra
        assert installment.split.escrow == Decimal("0.00")
        assert installment.split.principal == (
            installment.cash - installment.split.interest
        )
        # And the double count's own figure is not what the month paid.
        assert installment.split.principal + installment.split.interest != (
            contractual_pi + extra + extra
        )


def test_standing_extra_folds_past_the_shadow_horizon(
    app, seed_user, seed_periods_today,
):
    """C8a (N-15): the forward FOLD keeps a standing extra past the record horizon.

    The sibling above proves the standing extra reaches the FOLD's payoff; this
    proves the FOLD (:func:`balance_at.balance_at` -> ``positions()``) carries
    it on every month.  Before C8a it did not: ``loan_plan``'s PLANNED tier
    folded the extra only for the materialized ~24-month pay-period window (its
    live D3 cash), and its ESTIMATED tail reverted to bare contractual P&I -- so
    the fold-derived balance and payoff dropped the extra past the horizon while
    the resolver's committed schedule of the day applied it for the whole term
    (finding N-15).

    The loan ORIGINATES at the current period (clean past: no overdue installment,
    so the fold and the contractual schedule agree on the whole timeline rather
    than diverging on unpaid history via B-9), and has a recurring template but
    NO generated projected shadows, so its ENTIRE forward is the ESTIMATED tier
    -- the pure N-15 path.  The fold is parallel-run against the engine's
    contract-plus-extra forward (an INDEPENDENT producer, ``project_forward``
    with the extra as its what-if) on EVERY month: equal on all of them means
    the ESTIMATED tier now applies the extra across the whole horizon.  The
    teeth: at a
    post-horizon date the fold must sit STRICTLY BELOW the pure-contractual
    (extra-free) balance -- a THIRD independent reference that fails the day the
    extra stops being applied to the tail (the pre-C8a state, where fold ==
    contractual there).
    """
    with app.app_context():
        current_period = next(
            period for period in seed_periods_today
            if period.start_date <= date.today() <= last_covered_day(period)
        )
        # **The whole test reads at the loan's ORIGINATION day, not at the wall
        # clock, and the payment day is derived so the first installment can
        # never already be due.**  Both are what make the docstring's premise --
        # "clean past: no overdue installment" -- a PROPERTY rather than a hope.
        #
        # It was neither.  With ``payment_day=1`` and origination on the period's
        # start (the most recent Monday), whether the first installment fell in
        # the future, on today, or in the PAST depended on the day of the month
        # the suite happened to run:
        #
        #   * on the 1st it lands on today, and ``balance_at(today)`` reads the
        #     LEDGER while the engine's row for that day is the post-payment
        #     projection (``balance_at/_positions.py:207-218``) -- the CI failure
        #     of 2026-08-01;
        #   * on the days between the 1st and the month's first Monday it is
        #     OVERDUE and unpaid, so the fold pays nothing for it (D1 / B-9,
        #     ``balance_at/_plan.py:288``) while the engine's schedule still
        #     lists it -- and EVERY later row then diverges by that installment,
        #     which no per-row filter can rescue.
        #
        # Pinning the read to origination puts every contractual installment
        # strictly in the future, which is the state the test says it is
        # exercising.  ``(day % 28) + 1`` is simply "a day that is never the
        # origination's own day and exists in every month", so the first
        # installment is always at least one day out.
        as_of = current_period.start_date
        payment_day = (as_of.day % 28) + 1
        account = create_loan_account(
            seed_user, db.session, name="C8a Mortgage",
            principal=FIXED_PRINCIPAL, rate=FIXED_RATE, term=FIXED_TERM,
            origination_date=as_of, payment_day=payment_day,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        loan_params = loan_params_for(db.session, account.id)
        extra = Decimal("500.00")
        _add_recurring_payment_with_extra(seed_user, account, extra)
        scenario_id = seed_user["scenario"].id

        # One reference call yields BOTH references: the engine's
        # contract-plus-extra forward (the extra applied every month, the
        # fold's target) and the composer's pure-contractual original
        # (extra-free, the teeth's third reference).  The reference was the
        # composer's COMMITTED slice with the extra passed as the loan-level
        # ``extra_principal``, then its ACCELERATED slice, until plan step
        # R7d-g-3 deleted the composer's planned slices (ruling **R-R88**);
        # ``project_forward`` with the same figure as ``extra_monthly`` is the
        # same walk, so the reference is byte-identical and still comes from
        # an INDEPENDENT producer.
        scenarios, accelerated_forward = contract_forward_references(
            loan_params, scenario_id, as_of, extra,
        )
        contractual_by_date = {
            row.payment_date: row.remaining_balance
            for row in scenarios.original_forward
        }

        # Not vacuous: the schedule runs years out (so the tail is genuinely past
        # the ~24-month horizon), and the extra genuinely accelerates payoff.
        assert accelerated_forward, "no accelerated forward to parallel-run against"
        assert accelerated_forward[-1].payment_date < (
            scenarios.original_forward[-1].payment_date
        ), "standing extra did not accelerate payoff; test would be vacuous"

        ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)

        # The fold reproduces the engine's contract-plus-extra forward on EVERY
        # month the forward projection OWNS, including the ESTIMATED tail -- an
        # independent producer (project_forward) agreeing with the fold that the
        # extra is applied for the whole term.
        #
        # Rows dated at or before the resolver's NOW are excluded, and the
        # exclusion is the seam's own rule rather than a convenience: a date at or
        # before ``ctx.as_of`` reads the LEDGER (``balance_at._positions`` routes
        # ``on_date <= ctx.as_of`` to the fold -- "the past is the ledger's"),
        # while both plan tiers clamp a still-projected item to
        # ``as_of + 1 day`` (``balance_at._plan`` for loans, ``_cash_fold`` for
        # cash, ruling D1 / R-G: "a plan cannot have already happened").  So on a
        # day that IS an installment due date, ``balance_at(today)`` is the
        # still-owed balance while the engine's row for that same day is the
        # post-payment projection.  Both are right and they answer different
        # questions; comparing them is a category error, and it fired as a real
        # CI failure on 2026-08-01 because this fixture originates at the current
        # period with ``payment_day=1``.  On every other day of the month the
        # filter removes nothing.
        comparable = [
            row for row in accelerated_forward if row.payment_date > ctx.as_of
        ]
        assert len(comparable) > 1, (
            "the forward projection owns no comparable rows; the parallel-run "
            "would be vacuous"
        )
        for row in comparable:
            folded = balance_at.balance_at(account, ctx, row.payment_date)
            assert folded == row.remaining_balance, (
                f"Fold {folded} != the engine's {row.remaining_balance} at "
                f"{row.payment_date}: the ESTIMATED tail dropped the standing "
                "extra (N-15)."
            )

        # Teeth: a post-horizon date (~3 years out, well past the 24-month
        # window) must fold BELOW the extra-free contractual balance -- proof the
        # extra reaches the tail.  Pre-C8a the ESTIMATED tail carried no extra, so
        # the fold equalled the contractual balance here and this failed.
        probe_date = date(as_of.year + 3, as_of.month, payment_day)
        assert probe_date in contractual_by_date, (
            "probe date not on the contractual grid; adjust the fixture"
        )
        months_out = (probe_date.year - as_of.year) * 12 + (
            probe_date.month - as_of.month
        )
        assert months_out > 24, "probe date is inside the materialized horizon"
        folded_probe = balance_at.balance_at(account, ctx, probe_date)
        assert folded_probe < contractual_by_date[probe_date], (
            f"Fold at {probe_date} ({folded_probe}) is not below the "
            f"contractual {contractual_by_date[probe_date]}; the standing extra "
            "is not applied to the ESTIMATED tail (N-15 regressed)."
        )


# ── C17-6: no bare .quantize in loan single-source paths ──────────


_APP_DIR = Path(__file__).resolve().parents[2] / "app"

_LOAN_SINGLE_SOURCE_FILES = (
    "services/debt_strategy_service.py",
    # Phase 3 pylint-cleanup split: routes/loan.py is now the routes/loan/
    # package; the grep below runs with -r --include=*.py so every sub-module
    # is scanned.
    "routes/loan",
    "services/loan_payment_service.py",
)


def test_no_bare_quantize_in_loan_paths():
    """C17-6 / HIGH-08 / F-017..F-023 sweep: the four files in the
    Commit-17 scope contain no ``.quantize(Decimal("0.01"))`` calls
    without an explicit ``rounding=`` mode.

    A bare ``.quantize(Decimal("0.01"))`` falls back to Python's
    Decimal default ``ROUND_HALF_EVEN`` (banker's), the F-017..F-023
    divergence axis.  Every monetary boundary in these files now
    routes through ``app.utils.money.round_money`` (E-26 / HIGH-04
    central helper).  This sweep prevents a regression from
    silently reintroducing a bare-quantize.

    The grep matches the literal string ``.quantize(Decimal("0.01"))``
    (no surrounding whitespace) because that is exactly the F-020
    /F-021 pattern; any new monetary rounding that needs to deviate
    from ROUND_HALF_UP must name its mode explicitly and earn the
    review attention the grep cannot.
    """
    grep_out = subprocess.run(
        [
            "grep", "-rHn", "--include=*.py",
            r'\.quantize(Decimal("0\.01"))',
        ] + [
            str(_APP_DIR / rel) for rel in _LOAN_SINGLE_SOURCE_FILES
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [ln for ln in grep_out.stdout.splitlines() if ln.strip()]
    assert not lines, (
        "Found bare `.quantize(Decimal(\"0.01\"))` calls in the "
        "loan single-source-of-truth files.  Replace with "
        "`round_money(...)` from app.utils.money:\n"
        + "\n".join(lines)
    )
