"""The Schedule A mortgage-interest hybrid: ledger-ACTUAL + schedule-PROJECTED.

Direct unit coverage of ``tax_report_service._compute_mortgage_interest`` /
``_loan_year_interest`` -- the per-loan hybrid that sums a year's mortgage
interest from the genesis ledger (settled payments, the tax-correct actual) plus
the schedule (the year's still-projected remainder).

Relocated from ``test_year_end_summary_service.py`` when step F2 deleted the dead
year-end summary service and MOVED these two functions to their only live caller
(``tax_report_service._build_schedule_a``; plan step F2, ruling R-D).  The LIVE
Schedule-A path is exercised in ``test_tax_report_service.py``
(``TestScheduleAMortgageInterest`` -- wiring, hand-computed value, and the N-9
domain control); these tests pin the hybrid's INTERNAL partition (confirmed +
projected, the early-settled de-duplication, and the display-timezone paid-year
attribution) that the live value test alone does not reach.  Step C3c replaces the
hybrid with ``positions().cum_interest`` and retires this file.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.services import loan_loaders, loan_payment_service, loan_resolver
from app.services.loan_posting_service import confirmed_loan_interest_in_year
from app.services.net_worth_kernel import debt_schedule_rows as _debt_schedule_rows
from app.services.resolution_context import BalanceContext
from app.services.tax_report_service import _compute_mortgage_interest
from tests._test_helpers import (
    SPLIT_LOAN,
    clear_loan_ledger,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
)


def _paid_utc(year, month, day):
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _genesis_off_schedule_loan(seed_user, seed_periods):
    """Create a genesis SPLIT_LOAN with an OFF-schedule confirmed history.

    Trued up to $100,000 (origination $250,000 @ 6% on 2025-01-01), then a
    $2,000 (extra) payment and a $1,000 payment, both PAID mid-2026, so their
    real split posts to the genesis ledger:

      P1 ($2,000): interest round(100000 * 0.005) = 500.00; principal 1,500.00;
                   real balance 98,500.00.
      P2 ($1,000): interest round(98500 * 0.005) = 492.50 -- on the REAL balance,
                   NOT the schedule replay's ~99,900 (the replay advances by the
                   scheduled principal, not the actual extra).

    Ledger interest for 2026 is therefore 500.00 + 492.50 = 992.50 -- the ACTUAL
    figure the schedule replay does not reproduce.  Returns the loan account.
    """
    (orig_principal, orig_date, rate, anchor_balance,
     anchor_date, p1, p2, _p3) = SPLIT_LOAN
    loan = create_loan_with_trueup(
        seed_user, db.session, origination_principal=orig_principal,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=orig_date,
    )
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, seed_periods[p1],
        amount=Decimal("2000.00"), paid_at=_paid_utc(2026, 2, 15),
    )
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, seed_periods[p2],
        amount=Decimal("1000.00"), paid_at=_paid_utc(2026, 3, 15),
    )
    db.session.commit()
    return loan


class TestMortgageInterestGenesisHybrid:
    """The tax hybrid: ledger-ACTUAL (settled) + schedule-PROJECTED interest.

    A genesis loan's confirmed interest reads from the ledger -- correct even for
    an off-schedule payment, where the schedule replay is not -- attributed by
    each payment's civil paid date (the tax-correct basis); the future remainder
    comes from the schedule.  A loan with no genesis opening falls back to the
    full schedule.
    """

    def test_current_year_is_ledger_actual_plus_projected(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The current year is ledger-actual to date PLUS the schedule's future.

        With today frozen at 2026-04-01, the two OFF-schedule confirmed payments
        are ledger history (992.50, the ACTUAL figure) and the rest of 2026 is
        projected.  The hybrid is exactly ledger-confirmed + schedule-projected,
        both halves non-zero.  Since the C11 history read switch the schedule's
        confirmed rows are themselves LEDGER-derived, so their interest sum now
        AGREES with the tax figure (the display and the deduction unified); the
        non-vacuity divergence is therefore pinned against the UN-SEEDED
        replay's confirmed rows -- the pre-switch producer, which off-schedule
        still shows the scheduled interest, not the actual.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 4, 1))
            scenario_id = seed_user["scenario"].id
            loan = _genesis_off_schedule_loan(seed_user, seed_periods)
            bctx = BalanceContext.build(seed_user["user"].id)
            debt_schedules = _debt_schedule_rows([loan], bctx)
            debt = debt_schedules[loan.id]

            ledger_confirmed = confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            )
            projected = sum(
                (
                    row.interest for row in debt
                    if not row.is_confirmed and row.payment_date.year == 2026
                ),
                Decimal("0.00"),
            )
            schedule_confirmed = sum(
                (
                    row.interest for row in debt
                    if row.is_confirmed and row.payment_date.year == 2026
                ),
                Decimal("0.00"),
            )
            hybrid = _compute_mortgage_interest(2026, debt_schedules, scenario_id)

            # Exactly ledger-actual (confirmed) + schedule (projected), both nonzero.
            assert hybrid == ledger_confirmed + projected
            assert ledger_confirmed == Decimal("992.50")
            assert projected > Decimal("0.00")
            # C11 unification: the schedule's confirmed rows are ledger-derived,
            # so the amortization table's interest column now AGREES with the
            # Schedule A figure -- one truth for the confirmed region.
            assert schedule_confirmed == ledger_confirmed
            # Non-vacuity: the UN-SEEDED replay (the pre-switch producer) still
            # shows the SCHEDULED confirmed interest, which off-schedule differs
            # from the ledger's actual figure the hybrid uses.
            params = loan_loaders.load_loan_params(loan.id)
            ctx = loan_payment_service.load_loan_context(
                loan.id, scenario_id, params,
            )
            replay_state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params, loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments, ctx.rate_changes,
                ),
                date(2026, 4, 1),
            )
            replay_confirmed = sum(
                (
                    row.interest for row in replay_state.schedule
                    if row.is_confirmed and row.payment_date.year == 2026
                ),
                Decimal("0.00"),
            )
            assert replay_confirmed != ledger_confirmed
            assert hybrid != replay_confirmed + projected

    def test_interest_deducts_in_the_year_it_was_paid(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A 2026-scheduled payment PAID in 2025 deducts in 2025 (all-ledger year).

        Mortgage interest is deductible in the year PAID.  A period-``P1`` payment
        (scheduled 2026) settled on 2025-12-20 attributes its 500.00 interest to
        2025 -- a year with NO amortization rows (the loan's first payment is
        2026), so the figure is PURE ledger (the "past year, all-ledger" case).
        The old payment-date behaviour would report 0.00 for 2025.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            scenario_id = seed_user["scenario"].id
            (orig_principal, orig_date, rate, anchor_balance,
             anchor_date, p1, _p2, _p3) = SPLIT_LOAN
            loan = create_loan_with_trueup(
                seed_user, db.session, origination_principal=orig_principal,
                anchor_balance=anchor_balance, anchor_date=anchor_date,
                rate=rate, origination_date=orig_date,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2025, 12, 20),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)
            debt_schedules = _debt_schedule_rows([loan], bctx)

            # 2025 has NO amortization rows (first payment is 2026), so the figure
            # is PURE ledger -- the "all-ledger" year.
            schedule_2025 = sum(
                (
                    row.interest
                    for row in debt_schedules[loan.id]
                    if row.payment_date.year == 2025
                ),
                Decimal("0.00"),
            )
            assert schedule_2025 == Decimal("0.00")
            # The paid-in-2025 interest deducts in 2025 (500.00), not its 2026
            # scheduled year -- the tax-correct paid-date basis.
            assert _compute_mortgage_interest(
                2025, debt_schedules, scenario_id,
            ) == Decimal("500.00")

    def test_new_years_eve_evening_settle_deducts_in_the_display_year(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """THE L9 CASE, through the hybrid: 8:05 PM EST Dec 31 is Dec 31.

        A payment settled 2025-12-31 20:05 Eastern is stored as
        2026-01-01 01:05 UTC, so the UTC calendar day (and the stored
        ``entry_date``) is already 2026.  Schedule-A attribution follows the
        user's wall-clock day (L9, decided 2026-07-03): the 500.00 interest
        deducts in 2025 -- a year with NO amortization rows, so the figure is
        PURE ledger and the pre-L9 UTC attribution reported 0.00 here.  This
        pins the hybrid WIRING onto the display-timezone basis, not just the
        loan reader beneath it.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            scenario_id = seed_user["scenario"].id
            (orig_principal, orig_date, rate, anchor_balance,
             anchor_date, p1, _p2, _p3) = SPLIT_LOAN
            loan = create_loan_with_trueup(
                seed_user, db.session, origination_principal=orig_principal,
                anchor_balance=anchor_balance, anchor_date=anchor_date,
                rate=rate, origination_date=orig_date,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=datetime(2026, 1, 1, 1, 5, tzinfo=timezone.utc),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)
            debt_schedules = _debt_schedule_rows([loan], bctx)

            assert _compute_mortgage_interest(
                2025, debt_schedules, scenario_id,
            ) == Decimal("500.00")

    def test_early_settled_payment_is_counted_exactly_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """An early-settled payment's due slot leaves the projected term.

        A payment settled BEFORE its pay period begins posts its actual
        interest at settle (the R1 split-at-settlement rule), attributed to
        its paid year -- but the schedule replay bounds confirmed payments by
        "period has begun", so the same due slot's row stays
        ``is_confirmed=False``.  Counting that row in the projected term would
        double-count the slot; the partition rule is "a slot is projected iff
        no settled payment occupies it"
        (``loan_loaders.load_settled_payment_due_months``).

        Frozen 2026-02-10: P1 (due 02-01, begun) and P3 (due 04-01, EARLY)
        both settle.  Ledger interest = P1 500.00 (100000 * 0.005) + P3 497.50
        (round(99500 * 0.005)) = 997.50, both paid 2026.  The hybrid must be
        ledger + projected-minus-the-April-slot; the pre-fix hybrid (ledger +
        every not-confirmed 2026 row) reads HIGHER by exactly the April row's
        interest -- the double count this test kills.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 2, 10))
            scenario_id = seed_user["scenario"].id
            (orig_principal, orig_date, rate, anchor_balance,
             anchor_date, p1, _p2, p3) = SPLIT_LOAN
            loan = create_loan_with_trueup(
                seed_user, db.session, origination_principal=orig_principal,
                anchor_balance=anchor_balance, anchor_date=anchor_date,
                rate=rate, origination_date=orig_date,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2026, 2, 5),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p3], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2026, 2, 10),
            )
            db.session.commit()
            # The premise: P3's period has not begun by the frozen today (an
            # EARLY settle), so its schedule row is not replay-confirmed.
            assert seed_periods[p3].start_date > date(2026, 2, 10)

            bctx = BalanceContext.build(seed_user["user"].id)
            debt_schedules = _debt_schedule_rows([loan], bctx)
            debt = debt_schedules[loan.id]
            ledger_confirmed = confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            )
            # P1 500.00 + early P3 497.50, both paid (and deductible) in 2026.
            assert ledger_confirmed == Decimal("997.50")

            # The April due slot still projects a not-confirmed row (the
            # replay's period-begun bound cannot see the early settle) ...
            april_rows = [
                row for row in debt
                if not row.is_confirmed
                and (row.payment_date.year, row.payment_date.month) == (2026, 4)
            ]
            assert len(april_rows) == 1
            assert april_rows[0].interest > Decimal("0.00")
            naive_projected = sum(
                (
                    row.interest for row in debt
                    if not row.is_confirmed and row.payment_date.year == 2026
                ),
                Decimal("0.00"),
            )

            # ... but the hybrid excludes it: ledger + projected WITHOUT the
            # settled April slot.  The pre-fix hybrid (ledger + every
            # not-confirmed row) reads higher by exactly that row's interest.
            hybrid = _compute_mortgage_interest(2026, debt_schedules, scenario_id)
            assert hybrid == (
                ledger_confirmed + naive_projected - april_rows[0].interest
            )
            assert hybrid < ledger_confirmed + naive_projected


class TestMortgageInterestNoGenesisFallback:
    """The fallback: a loan with NO genesis opening sums the FULL schedule.

    ``confirmed_loan_interest_in_year`` returns ``None`` when the loan's linked
    ledger carries no OPENING posting (an un-backfilled loan, or a what-if the
    opening was never posted into), so ``_loan_year_interest`` cannot read
    ledger-actual interest and falls back to summing the resolver's full schedule
    (confirmed history + projection) by ``payment_date`` year.

    This branch lost its only test when F2 deleted the year-end suite.  Its old
    fixture (a mortgage originated in the future) no longer reaches it -- since C1
    the origination anchor is ALWAYS posted regardless of date, so
    ``_has_opening_posting`` is true for any ledger-opened loan.  The state that
    genuinely reaches the fallback is a MISSING opening, which ``clear_loan_ledger``
    constructs explicitly (the BROKEN state production's backfill prevents).
    """

    def test_no_opening_posting_sums_the_full_schedule(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """No genesis opening -> confirmed is None -> the full schedule is summed.

        A cleared-ledger SPLIT_LOAN (trued up to $100,000 @ 6% on 2026-01-05) has
        no OPENING posting, so the ledger reader declines and the hybrid sums every
        2026 schedule row's interest -- byte-identical to the pre-read-switch path.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            scenario_id = seed_user["scenario"].id
            (orig_principal, orig_date, rate, anchor_balance,
             anchor_date, _p1, _p2, _p3) = SPLIT_LOAN
            loan = create_loan_with_trueup(
                seed_user, db.session, origination_principal=orig_principal,
                anchor_balance=anchor_balance, anchor_date=anchor_date,
                rate=rate, origination_date=orig_date,
            )
            db.session.commit()
            # Construct the BROKEN state on purpose: remove the genesis opening so
            # the ledger cannot answer and the reader returns None.
            clear_loan_ledger(loan.id)
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            debt_schedules = _debt_schedule_rows([loan], bctx)
            debt = debt_schedules[loan.id]

            # Premise: no opening posting, so the ledger reader declines (the
            # None sentinel this branch rests on).
            assert confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2026,
            ) is None
            # The fallback sums EVERY 2026 schedule row's interest (confirmed
            # history + projection), not just a ledger term.
            expected = sum(
                (row.interest for row in debt if row.payment_date.year == 2026),
                Decimal("0.00"),
            )
            assert expected > Decimal("0.00")  # non-vacuous
            assert _compute_mortgage_interest(
                2026, debt_schedules, scenario_id,
            ) == expected
