"""The seam's ONE loan timeline (recurrence:R16-c-1): what the merge must not move.

Plan step **recurrence:R16-c-1** replays a loan's recorded facts and its forward
plan as ONE stream (``balance_at._loan_stream``).  Ruling **R-R90** made it the
MERGE leaf -- byte-identical to the two folds it replaced -- and these pin the
two shapes an adversarial review of the first cut measured moving:

* **A delinquent loan**: installments skipped with their rows still PROJECTED
  behind a later SETTLED payment.  The plan's charges for the skipped months
  ride the stream as projected charges, walked behind the facts in contract
  order with the catch-ups (April's charge, April's catch-up, May's charge,
  May's catch-up), which is the order the retired forward fold applied them.
  A first cut re-dated those charges to the boundary and applied both before
  both catch-ups: ``+$2.54`` on the balance, and the first catch-up read as an
  underpayment.  Every figure below is stated by hand.
* **The date a charge is rendered under**: ``charge_date`` is the accrual
  period's identity (the loan page groups on it and prints it), so a projected
  charge keeps its own date however far behind the facts it is walked.

And the pass's visibility bound (ruling **R-R91**): a pass's walk holds the
facts visible by its ``as_of`` -- the opening always -- so a true-up dated
after the read is not folded, a payment settled after the read is not
counted, and a not-yet-originated loan still projects from its opening.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.models.amount_ownership import AmountOwnership
from app.services import balance_at, transfer_service
from app.services.balance_at import BalanceContext
from app.services.loan_ledger import installment_slot
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
)

_PRINCIPAL = Decimal("200000.00")
_RATE = Decimal("0.06")            # 1,000.00 a month on the opening balance
_CASH = Decimal("1500.00")
_ORIGINATION = date(2026, 1, 1)
_AS_OF = date(2026, 6, 15)


def _loan(seed_user, db, name="Delinquent"):
    """A $200,000 loan at 6% over 30 years, due on the 1st, originated 2026-01-01."""
    return create_loan_account(
        seed_user, db.session, name=name,
        principal=_PRINCIPAL, rate=_RATE, term=360,
        origination_date=_ORIGINATION, payment_day=1,
    )


def _settled(seed_user, db, loan, period, due, settled_on):
    """Settle a $1,500 payment on installment *due*, its cash moving *settled_on*."""
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=_CASH, settled_on=settled_on, due_date=due,
    )


def _projected(seed_user, loan, period, due):
    """Leave a $1,500 payment PROJECTED on installment *due* (a skipped month)."""
    transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(_CASH),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name="Loan Payment",
            due_date=due,
        ),
    )


class TestSkippedMonthsBehindASettledPayment:
    """The plan's charges for skipped months interleave with their catch-ups."""

    def test_two_catch_ups_walk_charge_pay_charge_pay(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Feb, Mar, Jun paid; Apr and May skipped, rows projected; read 06-15.

        The recorded facts, in contract order (6% is 0.5% a month):

          Feb: charge 1,000.00 -> principal 500.00 -> 199,500.00
          Mar: charge   997.50 -> principal 502.50 -> 198,997.50
          Jun: charge   994.99 -> principal 505.01 -> 198,492.49  (owed 06-15)

        The two catch-ups land the day after the read (ruling D1), and the
        plan's April and May charges land with them, each BEFORE its own
        catch-up and AFTER the one before it:

          Apr charge 992.46 -> Apr catch-up: principal 507.54 -> 197,984.95
          May charge 989.92 -> May catch-up: principal 510.08 -> 197,474.87

        Both charges applied first would accrue May's interest on a balance
        April's catch-up had not reduced (992.46 + 992.46 = 1,984.92 standing,
        the April catch-up paying -484.92 of principal), and the balance on
        06-16 would read 197,477.41: the +2.54 the review measured.
        """
        with app.app_context():
            freeze_today(monkeypatch, _AS_OF)
            loan = _loan(seed_user, db)
            _settled(seed_user, db, loan, seed_periods[2], date(2026, 2, 1),
                     date(2026, 2, 1))
            _settled(seed_user, db, loan, seed_periods[4], date(2026, 3, 1),
                     date(2026, 3, 1))
            _projected(seed_user, loan, seed_periods[6], date(2026, 4, 1))
            _projected(seed_user, loan, seed_periods[8], date(2026, 5, 1))
            _settled(seed_user, db, loan, seed_periods[9], date(2026, 6, 1),
                     date(2026, 6, 1))
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

            assert balance_at.balance_at(loan, ctx, _AS_OF) == (
                Decimal("198492.49")
            )
            assert balance_at.balance_at(loan, ctx, date(2026, 6, 16)) == (
                Decimal("197474.87")
            )

            april, may = balance_at.loan_installments(loan, ctx)[:2]
            assert (april.due_date, april.visible_on) == (
                date(2026, 4, 1), date(2026, 6, 16),
            )
            assert (april.interest, april.principal, april.balance_after) == (
                Decimal("992.46"), Decimal("507.54"), Decimal("197984.95"),
            )
            assert (may.interest, may.principal, may.balance_after) == (
                Decimal("989.92"), Decimal("510.08"), Decimal("197474.87"),
            )
            # Each catch-up's charge keeps ITS OWN date, however far behind the
            # facts it is walked -- the loan page groups and prints it.
            assert april.charge_date == date(2026, 4, 1)
            assert may.charge_date == date(2026, 5, 1)
            assert installment_slot(april.charge_date) == (2026, 4)

    def test_one_catch_up_keeps_its_charge_date(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Feb, Mar, Apr, Jun paid; May skipped, row projected; read 06-15.

        One skipped month is enough to reach the render: the catch-up's
        ``charge_date`` is 05-01, the accrual period it pays into, and never
        the day it is walked on (06-02, the boundary behind the June fact --
        a day that is no installment date at all).  Owed 06-15: 197,984.95;
        the catch-up clears May's 989.92 and pays 510.08 down: 197,474.87.
        """
        with app.app_context():
            freeze_today(monkeypatch, _AS_OF)
            loan = _loan(seed_user, db)
            _settled(seed_user, db, loan, seed_periods[2], date(2026, 2, 1),
                     date(2026, 2, 1))
            _settled(seed_user, db, loan, seed_periods[4], date(2026, 3, 1),
                     date(2026, 3, 1))
            _settled(seed_user, db, loan, seed_periods[6], date(2026, 4, 1),
                     date(2026, 4, 1))
            _projected(seed_user, loan, seed_periods[8], date(2026, 5, 1))
            _settled(seed_user, db, loan, seed_periods[9], date(2026, 6, 1),
                     date(2026, 6, 1))
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

            assert balance_at.balance_at(loan, ctx, _AS_OF) == (
                Decimal("197984.95")
            )
            [may, *_rest] = balance_at.loan_installments(loan, ctx)
            assert may.due_date == date(2026, 5, 1)
            assert may.charge_date == date(2026, 5, 1)
            assert (may.interest, may.principal, may.balance_after) == (
                Decimal("989.92"), Decimal("510.08"), Decimal("197474.87"),
            )


class TestThePassSeesTheFactsVisibleByItsAsOf:
    """Ruling R-R91: the pass's walk is bounded at the load, the opening always in."""

    def test_a_true_up_dated_after_the_read_is_not_folded(
        self, app, db, seed_user, monkeypatch,
    ):
        """Trued to $0.00 on 06-15; read 06-10 the loan still owes its opening.

        No payment was ever made, so the pass's walk holds the opening alone:
        owed 06-10 is 200,000.00, the loan is not retired, and its payoff is
        a FORWARD date.  Read 06-20 the true-up is a fact: owed 0.00, retired.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 20))
            loan = _loan(seed_user, db, name="Trued Later")
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=date(2026, 6, 15),
            )
            db.session.commit()
            owner = seed_user["user"].id

            before = BalanceContext.build(owner, date(2026, 6, 10))
            assert [r.is_opening for r in before.loan_walk(loan).stream.resets] == [
                True,
            ]
            assert balance_at.balance_at(loan, before, date(2026, 6, 10)) == (
                _PRINCIPAL
            )
            assert balance_at.loan_figures(loan, before).is_retired is False
            assert balance_at.loan_payoff_date(loan, before) > date(2026, 6, 15)

            after = BalanceContext.build(owner, date(2026, 6, 20))
            assert len(after.loan_walk(loan).stream.resets) == 2
            assert balance_at.balance_at(loan, after, date(2026, 6, 20)) == (
                Decimal("0.00")
            )
            assert balance_at.loan_figures(loan, after).is_retired is True

    def test_a_payment_settled_after_the_read_is_not_a_fact_yet(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Two payments on one installment; the earlier-funded one settles late.

        Period 2's payment (due 02-01) settled 01-30; period 1's (also due
        02-01) settled LATE, 02-13.  Read 02-01 the pass has one payment: it
        opens the period, clears 1,000.00 of interest and pays 500.00 down,
        199,500.00 -- what the ledger showed that day.  Read 02-13 both are
        facts in pay-period order: the late one clears the charge (500.00 of
        principal), the other pays 1,500.00 of pure principal: 198,000.00.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 2, 13))
            loan = _loan(seed_user, db, name="Late Settle")
            _settled(seed_user, db, loan, seed_periods[1], date(2026, 2, 1),
                     date(2026, 2, 13))
            _settled(seed_user, db, loan, seed_periods[2], date(2026, 2, 1),
                     date(2026, 1, 30))
            db.session.commit()
            owner = seed_user["user"].id

            mid = BalanceContext.build(owner, date(2026, 2, 1))
            [only] = mid.loan_walk(loan).settled_splits
            assert (only.interest, only.principal) == (
                Decimal("1000.00"), Decimal("500.00"),
            )
            assert balance_at.balance_at(loan, mid, date(2026, 2, 1)) == (
                Decimal("199500.00")
            )

            later = BalanceContext.build(owner, date(2026, 2, 13))
            first, second = later.loan_walk(loan).settled_splits
            assert (first.interest, first.principal) == (
                Decimal("1000.00"), Decimal("500.00"),
            )
            assert (second.interest, second.principal) == (
                Decimal("0.00"), Decimal("1500.00"),
            )
            assert balance_at.balance_at(loan, later, date(2026, 2, 13)) == (
                Decimal("198000.00")
            )

    def test_a_loan_closing_next_month_projects_from_its_opening(
        self, app, db, seed_user, monkeypatch,
    ):
        """The opening is kept whatever its date: owed 0.00 today, 200,000 on closing."""
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 3, 20))
            loan = create_loan_account(
                seed_user, db.session, name="Closing In April",
                principal=_PRINCIPAL, rate=_RATE, term=360,
                origination_date=date(2026, 4, 15), payment_day=15,
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 3, 20))

            [opening] = ctx.loan_walk(loan).stream.resets
            assert opening.is_opening and opening.on_date == date(2026, 4, 15)
            assert balance_at.balance_at(loan, ctx, date(2026, 3, 20)) == (
                Decimal("0.00")
            )
            assert balance_at.balance_at(loan, ctx, date(2026, 4, 15)) == (
                _PRINCIPAL
            )
            assert balance_at.loan_figures(loan, ctx).is_retired is False
