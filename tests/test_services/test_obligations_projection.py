"""
Tests for ``app.services.obligations_projection`` (DH cash-flow panel).

``project_cash_flow`` replaces the old flat ``/obligations`` net-cash-flow
scalar with a grid-reconciled projection of the default account's balance:
the value reuses ``balance_resolver.balances_for`` so it equals the grid's
Projected End Balance footer.  These tests lock:

  * ``None`` when the user has no baseline scenario (setup-incomplete);
  * ``now_balance`` is the resolved anchor, and a transaction-free user
    projects flat (``direction == "flat"``);
  * a climbing balance yields ``direction == "growing"`` with the exact
    end balance;
  * ``negative_period_count`` counts only the periods whose projected end
    balance is below zero;
  * the ~12-month marker is selected by period END date, so it is the
    balance reached by the one-year mark and not a later period.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.models.account import AccountAnchorHistory
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services import obligations_projection, pay_period_service


# -- Helpers ----------------------------------------------------------------


def _override_anchor(db_session, account, pay_period, anchor_balance):
    """Repoint ``account``'s anchor to ``pay_period`` at ``anchor_balance``.

    Appends a latest-wins :class:`AccountAnchorHistory` row and syncs the
    ``current_anchor_*`` cache columns so the resolver's reconciliation
    path stays quiet (cache and history agree).
    """
    db_session.add(AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=pay_period.id,
        anchor_balance=anchor_balance,
        notes="obligations_projection tests: anchor override",
    ))
    db_session.flush()
    account.current_anchor_balance = anchor_balance
    account.current_anchor_period_id = pay_period.id
    db_session.commit()


def _make_projected_txn(db_session, seed_user, pay_period, *,
                        amount, is_income, name):
    """Create one Projected income/expense transaction in ``pay_period``.

    Ad-hoc (``template_id`` is None) so the row carries its
    ``estimated_amount`` straight into the projection -- no salary-profile
    live override, no entries -- making the resulting balance exact.
    """
    status = db_session.query(Status).filter_by(name="Projected").one()
    type_name = "Income" if is_income else "Expense"
    txn_type = db_session.query(TransactionType).filter_by(name=type_name).one()
    category = seed_user["categories"]["Salary" if is_income else "Groceries"]
    txn = Transaction(
        pay_period_id=pay_period.id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=status.id,
        name=name,
        category_id=category.id,
        transaction_type_id=txn_type.id,
        estimated_amount=amount,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


# -- Tests ------------------------------------------------------------------


class TestProjectCashFlow:
    """Behavior of ``obligations_projection.project_cash_flow``."""

    def test_returns_none_without_baseline_scenario(self, app, db, bare_user):
        """A user with no baseline scenario yields ``None`` (panel hidden)."""
        with app.app_context():
            result = obligations_projection.project_cash_flow(
                bare_user["user"].id, bare_user["settings"],
            )
            assert result is None

    def test_now_balance_is_anchor_and_flat_without_transactions(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No projected transactions -> balance stays at the anchor.

        Anchor overridden to 1500.00 on period 0; with nothing to roll
        forward, every period's end balance equals the anchor, so now ==
        end, the trend is "flat", and no period dips below zero.
        """
        with app.app_context():
            _override_anchor(
                db.session, seed_user["account"], seed_periods_today[0],
                Decimal("1500.00"),
            )

            result = obligations_projection.project_cash_flow(
                seed_user["user"].id, seed_user["settings"],
            )

            assert result is not None
            assert result.account_name == "Checking"
            assert result.now_balance == Decimal("1500.00")
            assert result.end.balance == Decimal("1500.00")
            assert result.twelve_month.balance == Decimal("1500.00")
            assert result.negative_period_count == 0
            assert result.direction == "flat"

    def test_direction_growing_with_climbing_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Projected income after the current period grows the balance.

        Anchor 1000.00 on period 0; today is period 4 (seed_periods_today).
        Adding 200.00 income to each of periods 5-9 rolls the end balance
        to 1000 + 5*200 = 2000.00, strictly above the 1000.00 anchor, so
        the trend is "growing" and nothing goes negative.
        """
        with app.app_context():
            for period in seed_periods_today[5:10]:
                _make_projected_txn(
                    db.session, seed_user, period,
                    amount=Decimal("200.00"), is_income=True, name="Side Gig",
                )
            db.session.commit()

            result = obligations_projection.project_cash_flow(
                seed_user["user"].id, seed_user["settings"],
            )

            assert result is not None
            assert result.now_balance == Decimal("1000.00")
            # 1000 + 5 * 200 = 2000.00 (income lands in periods 5-9).
            assert result.end.balance == Decimal("2000.00")
            assert result.twelve_month.balance == Decimal("2000.00")
            assert result.direction == "growing"
            assert result.negative_period_count == 0

    def test_negative_period_count_counts_only_sub_zero_periods(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Only periods whose projected end balance is below zero are counted.

        Anchor 100.00 on period 0; today is period 4.  A 500.00 expense in
        period 5 drives that period's end balance to 100 - 500 = -400.00
        (one negative period); a 1000.00 income in period 6 recovers it to
        +600.00, so periods 6-9 are positive.  Exactly one period is below
        zero.
        """
        with app.app_context():
            _override_anchor(
                db.session, seed_user["account"], seed_periods_today[0],
                Decimal("100.00"),
            )
            _make_projected_txn(
                db.session, seed_user, seed_periods_today[5],
                amount=Decimal("500.00"), is_income=False, name="Big Bill",
            )
            _make_projected_txn(
                db.session, seed_user, seed_periods_today[6],
                amount=Decimal("1000.00"), is_income=True, name="Bonus",
            )
            db.session.commit()

            result = obligations_projection.project_cash_flow(
                seed_user["user"].id, seed_user["settings"],
            )

            assert result is not None
            assert result.now_balance == Decimal("100.00")
            # period 5: 100 - 500 = -400 (negative); period 6: -400 + 1000 = 600.
            assert result.negative_period_count == 1
            assert result.end.balance == Decimal("600.00")
            assert result.direction == "growing"

    def test_twelve_month_marker_selected_by_end_date(self, app, db, seed_user):
        """The ~12-month marker is the last period ending within a year.

        Generates 40 today-relative biweekly periods (~15 months) so the
        projection extends past one year, with the anchor on the first.
        The marker period must end on or before one year out, the
        projection must extend beyond that cutoff, and the marker must be
        an earlier period than the end -- proving selection by ``end_date``
        rather than the last available period.
        """
        with app.app_context():
            # Place today in period 4 of a 40-period run aligned to a Monday.
            today = date.today()
            start = today - timedelta(days=today.weekday() + 4 * 14)
            periods = pay_period_service.generate_pay_periods(
                user_id=seed_user["user"].id,
                start_date=start,
                num_periods=40,
                cadence_days=14,
            )
            db.session.flush()
            _override_anchor(
                db.session, seed_user["account"], periods[0],
                Decimal("1000.00"),
            )

            result = obligations_projection.project_cash_flow(
                seed_user["user"].id, seed_user["settings"],
            )

            assert result is not None
            current = pay_period_service.get_current_period(seed_user["user"].id)
            one_year_out = current.start_date + timedelta(days=365)
            # Marker is within a year; projection runs past it; marker is an
            # earlier period than the end (so it was not just the last one).
            assert result.twelve_month.as_of_date <= one_year_out
            assert result.end.as_of_date > one_year_out
            assert result.twelve_month.as_of_date < result.end.as_of_date
