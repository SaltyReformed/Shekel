"""
Shekel Budget App -- the obligations aggregator reads the DESTINATION's stop
(plan step R7d-e).

The money cases of that step, beside ``test_obligations_aggregator`` rather
than inside it because that module is at its line ceiling.  A loan payment
stops when its loan does, and until this step the only way that fact reached
the ``/obligations`` and ``/savings`` totals was a CACHE: ten chokepoints wrote
the loan's derived payoff into ``budget.recurrence_rules.end_date``, the
authored bound's own column, and ``recurrence.has_ended`` read that column.
So a retired loan's payment stayed in the Recurring surface's totals and the
emergency-fund floor until some chokepoint happened to run, and left on the
day it did -- a fact about when a page was saved rather than about the loan.

The aggregator reads the composed door now (ruling **R-R56** makes the door
read the app-written column as the cache it is), and the derived stop answers
"has this ended" under ruling **R-R57**: the definition owes no occurrence on
or after the day asked.  **Every case here is one direction of the money.**  A
loan that is FINISHED leaves both totals (the inflating direction); a loan
whose cached bound is EARLIER than its payoff stays in them (the understating
direction, plan ledger row **D35**'s shape, and the more dangerous of the two).

The fixture loan is ``$12,000.00`` at 5% over 24 months; its MONTHLY
``$200.00`` payment is ``200 * 12 / (1 * 12) = $200.00`` a month while it
commits anything.  Today is frozen mid-2026 so the loan fixtures do not drift
with the calendar, and every pass is pinned to a day the case names.
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import (
    balance_at,
    obligations_aggregator,
    savings_dashboard_service,
)
from app.services.balance_at import BalanceContext
from app.services.loan_recurrence_sync import is_standing_loan_payment
from app.services.recurrence import EndsOnDate, reauthor_rule, recurrence_spec
from tests._test_helpers import (
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_retired_loan_payment,
)
from tests.oracles.recurrence_baseline import MONTHLY

#: The frozen "today": inside the seeded 52-period schedule, after the retired
#: loan closes and on the live loan's origination.
_TODAY = date(2026, 7, 1)
#: The retired loan: originates 2026-05-01 with a ``payment_day`` of 1, so its
#: first installment is 2026-06-01, and the true-up on the 15th follows ONE
#: fired occurrence -- the derived stop is ``ClosesOn(2026-06-15)``.
_ORIGINATED = date(2026, 5, 1)
_CLEARED = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today so the loan fixtures do not depend on the calendar."""
    freeze_today(monkeypatch, _TODAY)


def _live_loan_payment(seed_user):
    """Return a MONTHLY ``$200.00`` payment against a loan originating today.

    The control's loan: still owing, first installment 2026-08-01, payoff the
    door's own tests pin at 2028-07-01 for this shape.

    Args:
        seed_user: The owner.

    Returns:
        ``(loan, template)``, committed.
    """
    loan = create_loan_account(
        seed_user, db.session, name="Live Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.05000"),
        term=24, origination_date=_TODAY, payment_day=1,
    )
    template = make_loan_payment_template(
        db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
    )
    db.session.commit()
    return loan, template


def _cache_the_bound(template, bound: date, ctx) -> None:
    """Write *bound* into the rule's column the way a chokepoint's sync does.

    Through the real write door, so the column holds exactly what a sync
    leaves and the test's precondition can assert it.

    Args:
        template: The loan payment whose rule takes the cached bound.
        bound: The date to cache.
        ctx: The owner's read pass: its calendar is what the write door
            re-authors against, and its loan-resolution memo is what the
            standing-payment identity is read off.
    """
    reauthor_rule(
        template.recurrence_rule,
        replace(
            recurrence_spec(template.recurrence_rule),
            end_bound=EndsOnDate(on=bound),
        ),
        ctx.calendar(),
    )
    db.session.commit()
    assert template.recurrence_rule.end_date == bound, (
        "precondition: the column holds the cached bound"
    )
    assert is_standing_loan_payment(template, ctx), (
        "precondition: this is the definition whose bound the app writes"
    )


class TestADerivedStopLeavesTheObligationsTotal:
    """A loan payment leaves the totals on the day its loan closed."""

    def test_a_retired_loans_payment_leaves_the_total_on_the_day_the_loan_closed(
        self, app, seed_user, seed_periods_52,
    ):
        """Live the day before the true-up, gone from the true-up day on.

        The rule's ``end_date`` column is NULL (asserted), so the authored
        bound alone says "never ends" and before this step the aggregator
        counted ``$200.00`` a month, indefinitely, for a loan that was
        finished.  Read ON the day the loan closed, the definition owes
        nothing on or after that day -- its 2026-07-01 installment falls past
        the close -- so under R-R57 the commitment has ended that day, where
        "the closing date has passed" would have counted it one day more.
        """
        with app.app_context():
            loan, tpl = make_retired_loan_payment(
                db.session, seed_user,
                origination_date=_ORIGINATED, cleared_on=_CLEARED,
            )
            assert tpl.recurrence_rule.end_date is None, (
                "precondition: nothing stored could have supplied the stop"
            )
            owner = seed_user["user"].id
            on_close = BalanceContext.build(owner, _CLEARED)
            assert balance_at.loan_figures(loan, on_close).closing_date == (
                _CLEARED
            ), "precondition: the loan closed on the day it was trued to zero"

            # The day before: the loan still owes, so the payment commits.
            assert obligations_aggregator.template_monthly_or_none(
                tpl, BalanceContext.build(owner, _CLEARED - timedelta(days=1)),
            ) == Decimal("200.00")
            # The day it closed, and after: nothing is owed on or after it.
            assert obligations_aggregator.template_monthly_or_none(
                tpl, on_close,
            ) is None, (
                "a payment against a loan trued to zero today still counts as "
                "a monthly commitment"
            )
            assert obligations_aggregator.committed_monthly(
                [tpl], BalanceContext.build(owner, _TODAY),
            ) == Decimal("0.00")

    def test_a_cached_payoff_no_longer_keeps_a_finished_payment_counted(
        self, app, seed_user, seed_periods_52,
    ):
        """The column holds a FUTURE payoff a chokepoint cached before the clear.

        The shape the step's sentence names: the payment left the totals "on
        the day some chokepoint last ran".  A loan projected to pay off in
        2028 is cleared early; its rule's column still holds the 2028 date the
        last sync wrote, and ``has_ended`` read that column -- a date that has
        not passed -- so the payment stayed in both totals for two more years
        unless a later chokepoint happened to rewrite it.  The door reads the
        app-written column as the cache it is (ruling **R-R56**) and the
        derived stop is the whole answer.
        """
        with app.app_context():
            _loan, tpl = make_retired_loan_payment(
                db.session, seed_user,
                origination_date=_ORIGINATED, cleared_on=_CLEARED,
            )
            today = BalanceContext.build(seed_user["user"].id, _TODAY)
            _cache_the_bound(tpl, date(2028, 5, 1), today)

            assert obligations_aggregator.template_monthly_or_none(
                tpl, today,
            ) is None, (
                "a cached 2028 payoff kept a loan cleared in June 2026 in the "
                "committed total"
            )

    def test_a_cached_bound_EARLIER_than_the_payoff_no_longer_drops_a_live_payment(
        self, app, seed_user, seed_periods_52,
    ):
        """Plan ledger row **D35**'s shape: the understating direction.

        A live loan originating on the frozen day, first installment
        2026-08-01; its column holds 2026-07-15 -- the production shape,
        ``2029-01-22`` stored against ``2029-02-22`` derived, moved onto the
        fixture.  Read on 2026-07-20, before this step
        ``EndsOnDate(2026-07-15).has_closed`` said the bound had passed and the
        payment LEFT both totals while the loan owed all twenty-four
        installments -- the direction that understates what the owner is
        committed to.  The door composes ``NEVER_ENDS`` for the app-written
        column (ruling **R-R56**) and the loan's own stop is later than the
        cache, so the payment counts.
        """
        with app.app_context():
            loan, tpl = _live_loan_payment(seed_user)
            owner = seed_user["user"].id
            stale = date(2026, 7, 15)
            _cache_the_bound(tpl, stale, BalanceContext.build(owner, _TODAY))
            after_the_cache = BalanceContext.build(owner, date(2026, 7, 20))
            closing = balance_at.loan_figures(loan, after_the_cache).closing_date
            assert closing is not None and closing > stale, (
                f"precondition: the loan's own stop ({closing}) must be LATER "
                f"than the cache ({stale})"
            )

            assert obligations_aggregator.template_monthly_or_none(
                tpl, after_the_cache,
            ) == Decimal("200.00"), (
                "a stale cache earlier than the payoff dropped a live loan "
                "payment out of the committed total"
            )

    def test_the_emergency_fund_floor_drops_a_retired_loans_payment(
        self, app, seed_user, seed_periods_52,
    ):
        """The ``/savings`` surface end to end, with its control beside it.

        ``avg_monthly_expenses`` is the higher of the last six periods' settled
        checking expenses and the committed floor -- the aggregator's total
        over the checking account's outgoing definitions.  With nothing
        settled the floor IS the aggregator's answer, so a retired loan's
        ``$200.00`` payment leaving from checking inflated the emergency-fund
        baseline by ``$200.00`` a month until this step.  The control is a
        second loan still owing: its identical payment must count, or a floor
        of zero would prove only that the floor reads nothing.
        """
        with app.app_context():
            make_retired_loan_payment(
                db.session, seed_user,
                origination_date=_ORIGINATED, cleared_on=_CLEARED,
            )
            owner = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(owner, _TODAY),
            )
            assert data["avg_monthly_expenses"] == Decimal("0.00"), (
                "a retired loan's payment inflates the emergency-fund floor"
            )

            # The control: the same payment against a loan that still owes.
            _live_loan_payment(seed_user)

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(owner, _TODAY),
            )
            assert data["avg_monthly_expenses"] == Decimal("200.00"), (
                "the control does not fire: a live loan's $200.00 monthly "
                "payment must set the committed floor"
            )
