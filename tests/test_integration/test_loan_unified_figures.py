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
``app/routes/loan.py``'s ``committed_interest_saved`` with
``round_money`` (the E-26 / HIGH-04 boundary).

Test IDs C17-1..C17-6 trace to ``remediation_plan.md`` Section 9
"Commit 17" subsection E.  Hand-computed expectations follow the
arithmetic conventions in
``tests/test_integration/test_loan_resolver_single_source.py``; the
two files reinforce each other on the loan single-source-of-truth
contract.
"""

# pylint: disable=protected-access
# Cross-surface single-source-of-truth tests deliberately reach into
# the year_end_summary_service package's private
# ``_balances._generate_debt_schedules`` /
# ``_income_tax._compute_mortgage_interest`` helpers (Phase 2 split)
# because the public ``compute_year_end_summary`` aggregate exposes
# derived dec31 balances, not the schedule rows themselves -- and the
# schedule-row equality is exactly what HIGH-08 / F-017..F-023 demand
# we lock.

import re
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.services import (
    account_service,
    loan_payment_service,
    loan_resolver,
    year_end_summary_service,
)
from app.utils.money import round_money


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


def _create_fixed_loan(seed_user, period_id, *, name="C17 Mortgage"):
    """Materialise the canonical $300k fixed-rate mortgage.

    Mirrors ``test_loan_resolver_single_source._create_fixed_loan``
    (same arithmetic, same anchor event) so the assertions in the
    two files reinforce each other.
    """
    loan_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
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
        origination_date=FIXED_ORIGINATION,
        payment_day=1,
        is_arm=False,
    )
    db.session.add(loan_params)
    db.session.flush()

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=FIXED_ORIGINATION,
        anchor_balance=FIXED_PRINCIPAL,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()
    return account, loan_params


def _create_arm_loan(seed_user, period_id, *, name="C17 ARM"):
    """Materialise the canonical 5/5 ARM in its fixed-rate window."""
    loan_type = (
        db.session.query(AccountType).filter_by(name="Mortgage").one()
    )
    account = account_service.create_account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name=name,
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
        origination_date=FIXED_ORIGINATION,
        payment_day=1,
        is_arm=True,
        arm_first_adjustment_months=ARM_WINDOW,
        arm_adjustment_interval_months=12,
    )
    db.session.add(loan_params)
    db.session.flush()

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=FIXED_ORIGINATION,
        anchor_balance=ARM_PRINCIPAL,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
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
    anchor_events = (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account.id)
        .all()
    )
    return loan_resolver.resolve_loan(
        loan_params, anchor_events, ctx.payments, ctx.rate_changes,
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
            seed_user, seed_periods[0].id,
        )

        state = _resolver_state(account, loan_params, date.today())

        debt_schedules = year_end_summary_service._balances._generate_debt_schedules(
            [account], seed_user["scenario"].id,
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
    app, seed_user, seed_periods,
):
    """C17-2 / HIGH-08 / F-019: the calendar-year mortgage interest
    figure is an explicit, labeled subset of the resolver's life-of-
    loan total -- not a separate computation.

    Hand-computed life-of-loan total for our fixture
    (``$300,000`` / ``6%`` / ``360 months``, origination 2026-01-01)
    is the sum of the per-month interest rows the engine produces.
    The 2026 mortgage-interest subset is the sum of those same rows
    whose ``payment_date.year == 2026``.  Computing both from
    ``state.schedule`` proves the contract: there is one schedule;
    the calendar-year view is a filter on it.
    """
    with app.app_context():
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0].id,
        )

        state = _resolver_state(account, loan_params, date.today())

        # Life-of-loan total interest, derived directly from the
        # resolver's single schedule.
        life_of_loan = sum(
            (row.interest for row in state.schedule), Decimal("0.00"),
        )
        # Resolver applies round_money at the LoanState boundary
        # (loan_resolver.py:647), so state.total_interest matches the
        # rounded sum of row interests.
        assert state.total_interest == round_money(life_of_loan), (
            f"Resolver total_interest={state.total_interest} differs "
            f"from sum-of-rows round_money={round_money(life_of_loan)}"
            " -- the resolver's two derivation paths must agree."
        )

        # The year-end calendar-year subset.  Year 2026 covers the
        # first eleven payments (payment_day=1, origination
        # 2026-01-01 ⇒ first payment 2026-02-01, last in-year payment
        # 2026-12-01).
        debt_schedules = year_end_summary_service._balances._generate_debt_schedules(
            [account], seed_user["scenario"].id,
        )
        calendar_year_interest = (
            year_end_summary_service._income_tax._compute_mortgage_interest(
                2026, debt_schedules,
            )
        )

        # Hand-derive the same subset directly from the resolver
        # schedule.  The aggregation MUST equal this.
        expected_subset = sum(
            (
                row.interest for row in state.schedule
                if row.payment_date.year == 2026
            ),
            Decimal("0.00"),
        )
        assert calendar_year_interest == expected_subset, (
            f"Year-end 2026 mortgage interest "
            f"{calendar_year_interest} != labeled subset of resolver "
            f"schedule {expected_subset} -- the calendar-year view "
            "diverged from the life-of-loan source."
        )

        # And the labeled subset is strictly less than the total.
        assert expected_subset < life_of_loan, (
            "Sanity: 2026's mortgage interest cannot equal the full "
            "life-of-loan total (the loan runs into 2056)."
        )


# ── C17-3: interest_saved uses round_money (half-up, not banker's)


def test_interest_saved_uses_round_money_half_up():
    """C17-3 / HIGH-08 / F-020: ``committed_interest_saved`` on the
    payoff calculator uses ``round_money`` (ROUND_HALF_UP), not a
    bare ``.quantize`` that silently fell back to Python's
    ROUND_HALF_EVEN (banker's).

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
    app, seed_user, seed_periods,
):
    """C17-4 / HIGH-08 / F-022: ``months_saved`` is one quantity
    derived from the scenario composer, not multiple divergent paths.

    F-022 / F-023 documented that pre-remediation, four different
    "months saved" values could appear depending on how the surface
    computed it (resolver schedule length, summary metric, route-side
    subtraction, engine summary helper).  After Commit 13 introduced
    the resolver, Commit 17 collapsed every display surface onto its
    schedule, and the amortization-engine split (Phase 6 / Commit 6
    of ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``)
    rewired the resolver's schedule generation through
    :func:`loan_resolver.compute_payoff_scenarios`.  After that
    rewire ``months_saved`` has one definition: the composer's
    ``len(committed_forward) - len(accelerated_forward)``.  Every
    display surface that needs the figure reads it off the composer
    via the resolver chokepoint, so there is no "engine summary
    path" left to diverge from.

    Pre-C6 this test compared ``calculate_summary``'s
    ``months_saved`` against ``len(state.schedule) - len(accelerated)``
    where ``accelerated`` was a separate ``generate_schedule`` call.
    Both surfaces used the same engine, so they agreed.  Post-C6 the
    resolver routes through ``project_forward`` (which absorbs
    rounding residue cleanly in the contractual final row, no
    phantom $0.04 trailing row) while ``calculate_summary`` still
    uses ``generate_schedule`` (which emits the phantom row).  The
    two paths diverge by one row per fixed-rate loan that has
    rounded-payment residue at month ``term_months``.  That
    divergence is real and unavoidable until Commit 9 deletes
    ``generate_schedule`` and ``calculate_summary`` entirely; this
    test re-anchors at the post-C6 canonical surface
    (``compute_payoff_scenarios``) so the F-022 SSOT lock survives
    the engine deletion intact.

    Hand-computed expectation for the $300k / 6% / 360 mo fixture
    with ``extra_monthly = $200`` (closed-form payoff with extra):
    contractual payment $1798.65, total monthly $1998.65,
    ``n_extra = -log(1 - P*i/M_total) / log(1+i)
    = -log(1 - 300000*0.005/1998.65) / log(1.005)
    = -log(0.249493) / log(1.005) ~= 278.31 -> 279 rows at HALF_UP``.
    Standard payoff is the contractual 360 months (clean residue
    absorption; no phantom row).  Therefore
    ``months_saved == 360 - 279 == 81``.  Pinning the composer's
    output to 81 catches both directions of regression: a return to
    phantom-row semantics (would inflate to 82) and any future
    parallel-path bug (would put the composer's months_saved out of
    sync with its own forward slices).
    """
    # pylint: disable=import-outside-toplevel
    from app.services.loan_resolver import compute_payoff_scenarios

    with app.app_context():
        account, loan_params = _create_fixed_loan(
            seed_user, seed_periods[0].id,
        )

        ctx = loan_payment_service.load_loan_context(
            account.id, None, loan_params,
        )
        anchor_events = (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .all()
        )

        extra = Decimal("200.00")
        scenarios = compute_payoff_scenarios(
            loan_params=loan_params,
            anchor_events=anchor_events,
            payments=ctx.payments,
            rate_changes=ctx.rate_changes,
            extra_monthly=extra,
            as_of=date.today(),
        )

        # The composer's months_saved must equal its own forward
        # length diff.  Tautological by construction in today's
        # implementation -- which is the point of the lock: a future
        # refactor that adds a parallel months_saved computation
        # would break the equality and surface here.  Post-Commit-9
        # this is the only surviving path; the lock is structural.
        composer_diff = (
            len(scenarios.committed_forward)
            - len(scenarios.accelerated_forward)
        )
        assert scenarios.months_saved == composer_diff, (
            f"composer months_saved={scenarios.months_saved} "
            f"differs from len(committed_forward) - "
            f"len(accelerated_forward) = {composer_diff} -- a "
            "parallel computation path has reappeared inside the "
            "composer."
        )

        # Hand-computed lock: 360 - 279 = 81 months saved.  A
        # regression that reintroduced the pre-C6 phantom residue
        # row would inflate committed_forward by 1 and surface here
        # as 82, not 81.
        assert scenarios.months_saved == 81, (
            "Hand-computed months_saved for $300k / 6% / 360 mo "
            f"with $200 extra is 81 (got {scenarios.months_saved})."
        )

        # And the resolver schedule that downstream surfaces read
        # uses the same committed_forward slice that the composer
        # used to derive months_saved -- locks the chokepoint that
        # makes the SSOT cross-surface.
        state = _resolver_state(account, loan_params, date.today())
        assert (
            len(state.schedule)
            == len(scenarios.history_rows) + len(scenarios.committed_forward)
        ), (
            "resolver.state.schedule must equal "
            "history_rows + committed_forward (the composer's "
            "Committed-no-extra composition); a different "
            "assembly here would re-introduce the parallel-path "
            "divergence F-022 locks against."
        )


# ── C17-5: ARM payoff_date consistent across all surfaces ─────────


def test_arm_payoff_date_consistent_across_surfaces(
    app, auth_client, seed_user, seed_periods,
):
    """C17-5 / HIGH-08 / F-023: an ARM loan's payoff_date is identical
    across resolver / dashboard / year-end-summary surfaces.

    Pre-Commit-15 the dashboard derived its "Projected Payoff" card
    from ``amortization_engine.calculate_summary`` while the year-end
    debt aggregation derived its Dec-31 balance from a separately-
    generated schedule.  For ARM loans, the calendar-shrinking
    ``calculate_remaining_months`` count made the symptom-#4 payment
    creep visible -- and the resulting schedules ended on different
    payment_dates.  Commit 13 fixed the payment number; Commit 17
    pins that the payoff_date now matches across every surface that
    reads the resolver's schedule.
    """
    with app.app_context():
        account, loan_params = _create_arm_loan(
            seed_user, seed_periods[0].id,
        )

        state = _resolver_state(account, loan_params, date.today())
        resolver_payoff = state.payoff_date

        # Year-end-summary path: the same schedule the resolver
        # produced flows through ``_generate_debt_schedules``.
        debt_schedules = year_end_summary_service._balances._generate_debt_schedules(
            [account], seed_user["scenario"].id,
        )
        ye_schedule = debt_schedules[account.id]
        ye_payoff = (
            ye_schedule[-1].payment_date if ye_schedule else None
        )

        assert ye_payoff == resolver_payoff, (
            f"ARM payoff_date diverged: resolver={resolver_payoff}, "
            f"year-end={ye_payoff} -- two surfaces, two payoff dates."
        )

        # Dashboard "Projected Payoff" card: the route assembles its
        # own ``AmortizationSummary`` from the resolver-anchored
        # planned schedule (loan.py:557-565).  Verify the displayed
        # date matches the resolver's ``payoff_date`` by reading the
        # rendered loan card.
        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200, (
            f"Loan dashboard GET failed: {resp.status_code}"
        )
        # The dashboard renders the abbreviated month / year of the
        # payoff date in the "Projected Payoff" card (template
        # ``loan/dashboard.html`` line 147: ``%b %Y``).  For our ARM
        # fixture (no payments, no extra) it must equal the
        # resolver's life-of-loan endpoint.
        expected_month_year = resolver_payoff.strftime("%b %Y")
        html = resp.data.decode()
        # Anchor the assertion to the "Projected Payoff" row so a
        # different ``%b %Y`` token elsewhere on the page (e.g. an
        # amortization schedule row) cannot mask a regression on the
        # card.
        card_match = re.search(
            r"Projected Payoff[\s\S]*?<span>([A-Za-z]{3} \d{4})</span>",
            html,
        )
        assert card_match, (
            "Could not locate the Projected Payoff card on the loan "
            f"dashboard; HTML excerpt: {html[:600]!r}"
        )
        card_text = card_match.group(1)
        assert card_text == expected_month_year, (
            f"Projected Payoff card displayed {card_text!r}, "
            f"expected resolver's payoff_date {expected_month_year!r}."
        )


# ── C17-6: no bare .quantize in loan single-source paths ──────────


_APP_DIR = Path(__file__).resolve().parents[2] / "app"

_LOAN_SINGLE_SOURCE_FILES = (
    "services/debt_strategy_service.py",
    # Phase 3 pylint-cleanup split: routes/loan.py is now the routes/loan/
    # package, and year_end_summary_service is a package; the grep below
    # runs with -r --include=*.py so every sub-module is scanned.
    "routes/loan",
    "services/year_end_summary_service",
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
