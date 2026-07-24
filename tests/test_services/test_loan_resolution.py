"""Tests for ``app.services.loan_resolution`` (the resolver-seeding layer).

Covers :func:`contractual_schedule_from_origination` -- the pure from-origination
contractual amortization the property equity chart's (a) contractual
back-projection reads to fill a tracking-start loan's pre-tracking debt line
(``docs/plans/implementation_plan_property_equity_chart_rebuild.md``, commit 1).
"""

from datetime import date
from decimal import Decimal

from app.services.loan_loaders import load_loan_params, load_rate_changes
from app.services.loan_resolution import contractual_schedule_from_origination
from tests._test_helpers import (
    create_loan_account,
    insert_tracking_start_event,
)


class TestContractualScheduleFromOrigination:
    """The pure contractual-from-origination schedule producer."""

    def test_exact_amortization_from_origination(self, app, db, seed_user):
        """A $12,000 / 12mo / 6% loan amortizes to zero over 12 rows from day one.

        Hand-computed first row (origination 2020-01-01, payment_day 1, so the
        first payment falls one month later on 2020-02-01):

            monthly P&I = amortize(12000, 0.06, 12) = 1032.80
            interest    = 12000 * (0.06 / 12) = 12000 * 0.005 = 60.00
            principal   = 1032.80 - 60.00 = 972.80
            balance     = 12000.00 - 972.80 = 11027.20

        and the 12th (final) row clears the loan to 0.00 on 2021-01-01.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Clean",
                principal=Decimal("12000.00"), rate=Decimal("0.06000"),
                term=12, origination_date=date(2020, 1, 1), payment_day=1,
            )
            params = load_loan_params(loan.id)
            rate_changes = load_rate_changes(loan.id)

            schedule = contractual_schedule_from_origination(params, rate_changes)

            assert len(schedule) == 12
            first = schedule[0]
            assert first.payment_date == date(2020, 2, 1)
            assert first.payment == Decimal("1032.80")
            assert first.interest == Decimal("60.00")
            assert first.principal == Decimal("972.80")
            assert first.extra_payment == Decimal("0.00")
            assert first.remaining_balance == Decimal("11027.20")
            # A contractual estimate is never recorded fact.
            assert first.is_confirmed is False
            last = schedule[-1]
            assert last.payment_date == date(2021, 1, 1)
            assert last.remaining_balance == Decimal("0.00")

    def test_spans_full_term_for_a_mortgage(self, app, db, seed_user):
        """A 360-month mortgage yields 360 rows from origination to a 0.00 payoff.

        Origination 2018-06-01, payment_day 1: the first payment is 2018-07-01
        and the 360th is exactly 30 years of monthly payments later, 2048-06-01.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Mortgage",
                principal=Decimal("300000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2018, 6, 1), payment_day=1,
            )
            params = load_loan_params(loan.id)
            rate_changes = load_rate_changes(loan.id)

            schedule = contractual_schedule_from_origination(params, rate_changes)

            assert len(schedule) == 360
            assert schedule[0].payment_date == date(2018, 7, 1)
            assert schedule[-1].payment_date == date(2048, 6, 1)
            assert schedule[-1].remaining_balance == Decimal("0.00")

    def test_ignores_tracking_start_and_seeds_from_origination(
        self, app, db, seed_user,
    ):
        """The producer seeds from origination even when a tracking-start exists.

        ``resolve_loan_bundle`` opens a mid-life-imported loan at its
        tracking-start balance; this producer must NOT -- it is the
        from-origination contractual reference the chart back-projects the
        pre-tracking months with.  Adding a tracking-start opening (recent, below
        the contractual balance) leaves the schedule byte-identical to the
        no-tracking-start case: still 360 rows from the origination first
        payment, so the back-projection is unaffected by where tracking began.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Imported",
                principal=Decimal("300000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2018, 6, 1), payment_day=1,
            )
            params = load_loan_params(loan.id)
            baseline = contractual_schedule_from_origination(
                params, load_rate_changes(loan.id),
            )

            # A mid-life tracking-start opening, well after origination.
            insert_tracking_start_event(
                params, Decimal("250000.00"), date(2024, 1, 1),
            )
            db.session.commit()

            after = contractual_schedule_from_origination(
                params, load_rate_changes(loan.id),
            )
            assert [
                (row.payment_date, row.remaining_balance) for row in after
            ] == [
                (row.payment_date, row.remaining_balance) for row in baseline
            ]
            assert after[0].payment_date == date(2018, 7, 1)
