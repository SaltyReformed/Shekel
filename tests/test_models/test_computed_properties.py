"""
Shekel Budget App -- Model Computed Property Tests

Tests for computed properties on models:
  - Transaction: what it contributes (the retired ``effective_amount``
    rules, now ``row_valuation.owned_contribution``), is_income, is_expense
  - Transfer: what its amount resolves to (``resolve_transfer_amount``)
  - Category: display_name
  - PaycheckBreakdown: total_pre_tax, total_post_tax, total_taxes
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from decimal import Decimal

from app.extensions import db
from app.models.ref import Status, TransactionType
from app.models.mixins import reject_settle_instant
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.paycheck_calculator import (
    DeductionBreakdown,
    DeductionLine,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)
from app.services import account_service
from app.utils.dates import display_today
from app.utils.dates import add_months
from app.services.cash_ledger import resolve_transfer_amount
from app.services.row_valuation import owned_contribution
from tests._test_helpers import (
    an_entered_day,
    default_settle_day,
    open_books_before_the_first_assertion,
    settle_day_columns,
    settlement_columns,
)
from app.services.settle_day import record_settle_day
from app.models.amount_ownership import AmountOwnership


# ── What a TRANSACTION contributes ───────────────────────────────────


class TestTransactionEffectiveAmount:
    """What one transaction contributes, by the rules the property held.

    ``Transaction.effective_amount`` was deleted at plan step X-au-c2 -- a
    model property cannot resolve a DERIVED amount, being a pure in-memory read
    with no session -- and its four arms moved to
    ``row_valuation.owned_contribution`` for a row that owns its figure.  These
    cases moved with them unchanged, because the RULES did not change: zero for
    a soft-deleted or excluded row, what a SETTLED row recorded, else the row's
    own stored figure.

    **The second arm was ``actual_amount`` until plan step X-au-c3, and TWO
    cases went with the change rather than being retargeted.**  That column was
    reachable on an UNSETTLED row and OUTRANKED the plan there, so the class
    pinned "a Projected row with an actual prefers the actual" (case 5A.1) and
    its ``$0.00`` twin (E-12's projected half).  Both are refuted now, and by
    the STATUS rather than by the columns: an unsettled row carrying a recorded
    figure is perfectly constructible -- it is the RETAINED state a revert
    leaves behind, which ``ck_transactions_settle_day_needs_a_record`` admits on
    purpose -- and ``row_valuation.settled_figure`` answers ``None`` for it
    whatever it still remembers, so such a row is worth its PLAN.  The
    preference those two cases asserted has no state left to hold in.

    The five production rows carrying a figure while unsettled were promoted
    into their PLAN by migration ``e4b8a71c0f36`` -- balance-neutrally, because
    the valuation was already answering the promoted figure.  So the correction
    5A.1 was about is made on ``estimated_amount`` now, which
    ``test_projected_returns_estimated`` grades, and E-12's rule survives on the
    settled half in ``test_done_with_zero_actual``.  That the retained state is
    STORABLE and worth nothing is graded in
    ``tests/test_models/test_settlement_record.py``.
    """

    def _make_txn(self, seed_user, seed_periods, status_name, estimated, actual=None):
        """Helper: create a transaction with given status and amounts.

        A row built in a SETTLED status carries the whole record -- the day, the
        figure and how the figure is known -- through the one door a bare-built
        fixture uses (``_test_helpers.settlement_columns``).  *actual* is the
        figure a human typed, which makes the record a ``corrected`` one; with
        no *actual* the record is ``derived`` at the row's own plan, which is
        what a settle with nothing to correct records.
        """
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        settled_on = default_settle_day(seed_periods[0], status.id)
        txn = Transaction(
            user_id=seed_periods[0].user_id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=status.id,
            name="Test",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            amount_ownership=AmountOwnership.own(estimated),
            **settle_day_columns(settled_on),
            **settlement_columns(settled_on, estimated, submitted=actual),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_projected_returns_estimated(self, app, db, seed_user, seed_periods):
        """Projected transaction returns estimated_amount."""
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "Projected", Decimal("150.00"))
            assert owned_contribution(txn) == Decimal("150.00")

    # ── The case that arrived at plan step X-c2c2c ───────────────────
    #
    # It was asserted inside ``test_balance_calculator.py``, through the
    # balance walk, by two tests whose other six cases duplicated the ones
    # above.  The duplicates deleted; the survivors had no home here and are
    # the whole of what that move preserved.  The property is the MODEL's, so
    # it is graded against the model.  Two of the three went at plan step
    # X-au-c3 -- see the class docstring for the state they described and why
    # it no longer exists.

    def test_a_soft_deleted_row_is_worth_zero_whatever_its_status(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_deleted`` zeroes the row regardless of status or amounts.

        The last of the property's four branches, and the only one no test
        here covered: a soft-deleted row is worth nothing even when its status
        is a settled one and it recorded a figure.
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Paid",
                Decimal("100.00"), actual=Decimal("75.00"),
            )
            txn.is_deleted = True
            db.session.flush()

            assert owned_contribution(txn) == Decimal("0")

    def test_done_with_actual_returns_actual(self, app, db, seed_user, seed_periods):
        """A Paid row that recorded a human's CORRECTION is worth that figure."""
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Paid",
                Decimal("150.00"), actual=Decimal("145.00"),
            )
            assert owned_contribution(txn) == Decimal("145.00")

    def test_done_without_actual_returns_estimated(self, app, db, seed_user, seed_periods):
        """A Paid row that recorded no correction is worth what it DERIVED.

        The figure is the row's own plan, and the two are equal here -- but they
        are read from different columns since plan step X-au-c3, and that is the
        point.  This used to be the FALL-BACK case: a settled row stored no
        figure at all, so the valuation read its plan.  A settle now records the
        figure it booked, so the same ``$150.00`` is an answer about the money
        rather than about the forecast.
        """
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "Paid", Decimal("150.00"))
            assert owned_contribution(txn) == Decimal("150.00")

    def test_credit_status_returns_zero(self, app, db, seed_user, seed_periods):
        """Credit-status transaction contributes Decimal('0').

        Credit transactions are excluded from checking balance calculations
        because the charge is on a credit card, not the checking account.
        Expected: the contribution == Decimal('0') regardless of estimated_amount.
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Credit", Decimal("250.00"),
            )
            assert owned_contribution(txn) == Decimal("0")

    def test_cancelled_status_returns_zero(self, app, db, seed_user, seed_periods):
        """Cancelled transaction contributes Decimal('0').

        Cancelled transactions should not affect balance projections at all.
        Expected: the contribution == Decimal('0').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Cancelled", Decimal("500.00"),
            )
            assert owned_contribution(txn) == Decimal("0")

    def test_received_uses_estimated_when_no_actual(self, app, db, seed_user, seed_periods):
        """A Received row that recorded no correction is worth what it DERIVED.

        'Received' settles exactly as 'Paid' does: the record states the figure,
        and with nobody correcting it the figure is what the row was worth.
        Expected: the contribution == Decimal('150.00').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Received", Decimal("150.00"),
            )
            assert owned_contribution(txn) == Decimal("150.00")

    def test_received_uses_actual_when_set(self, app, db, seed_user, seed_periods):
        """A Received row that recorded a CORRECTION is worth that figure.

        The record and the plan are two different columns, and a settled row is
        worth what it recorded.
        Expected: the contribution == Decimal('145.00').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Received",
                Decimal("150.00"), actual=Decimal("145.00"),
            )
            assert owned_contribution(txn) == Decimal("145.00")

    def test_done_with_zero_actual(self, app, db, seed_user, seed_periods):
        """A Paid row that recorded ``$0.00`` is worth zero (e.g., a waived fee).

        Zero is a VALUE, not "missing" (E-12): a row whose record says nothing
        moved must not fall back to the ``$100.00`` it planned.
        Expected: the contribution == Decimal('0.00').
        """
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods, "Paid",
                Decimal("100.00"), actual=Decimal("0.00"),
            )
            assert owned_contribution(txn) == Decimal("0.00")


# ── Transaction.is_income / is_expense ───────────────────────────────


class TestTransactionTypeProperties:
    """Tests for Transaction.is_income and is_expense properties."""

    def test_is_income(self, app, db, seed_user, seed_periods):
        """is_income returns True for income-type transactions."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            txn = Transaction(
                user_id=seed_periods[0].user_id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Paycheck",
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                amount_ownership=AmountOwnership.own(Decimal("2000.00")),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.is_income is True
            assert txn.is_expense is False

    def test_is_expense(self, app, db, seed_user, seed_periods):
        """is_expense returns True for expense-type transactions."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            txn = Transaction(
                user_id=seed_periods[0].user_id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=projected.id,
                name="Groceries",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                amount_ownership=AmountOwnership.own(Decimal("85.00")),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.is_expense is True
            assert txn.is_income is False


# ── What a TRANSFER's amount is ──────────────────────────────────────


class TestTransferAmountResolves:
    """What ``budget.transfers.amount`` resolves to, after the property went.

    ``Transfer.effective_amount`` was deleted at plan step X-au-c2 and these
    cases were retargeted onto :func:`cash_ledger.resolve_transfer_amount`, the
    rule that answers the same question under the amount model.  They are NOT
    equivalent and the difference is the point: the property zeroed a Cancelled
    transfer, the resolver answers what the row's amount IS and leaves "does
    this count" to the caller -- the same split the transaction side already
    has between ``resolve_transaction_amount`` and ``contributed_amount``.

    **The zeroing arm went with the property because it had no consumer.**  An
    AST census at X-au-c2 found ZERO reads of ``Transfer.effective_amount``
    anywhere in ``app/`` -- every one of the 24 sites was on ``Transaction`` --
    so the Cancelled case it graded was a rule nothing asked.  Its
    ``test_cancelled_returns_zero`` is therefore deleted rather than
    retargeted; the transfer status gate that DOES have consumers is
    ``balance_predicates.is_balance_contributing``, tested in
    ``test_utils/test_balance_predicates.py``.
    """

    def _make_transfer(self, seed_user, seed_periods, status_name, amount):
        """Helper: create a transfer with given status and amount."""
        from app.models.ref import AccountType

        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(savings)
        db.session.flush()

        status = db.session.query(Status).filter_by(name=status_name).one()
        xfer = Transfer(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            status_id=status.id,
            name="Test Transfer",
            amount_ownership=AmountOwnership.own(amount),
        )
        db.session.add(xfer)
        db.session.flush()
        return xfer

    def test_projected_resolves_to_its_own_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected ad-hoc transfer owns its figure, so it resolves to it."""
        with app.app_context():
            xfer = self._make_transfer(
                seed_user, seed_periods, "Projected", Decimal("500.00"),
            )
            assert resolve_transfer_amount(xfer) == Decimal("500.00")

    def test_settled_resolves_to_its_own_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Leaving Projected does not change WHO owns the figure."""
        with app.app_context():
            xfer = self._make_transfer(
                seed_user, seed_periods, "Paid", Decimal("500.00"),
            )
            assert resolve_transfer_amount(xfer) == Decimal("500.00")

    def test_cancelled_still_resolves_to_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled transfer's AMOUNT is unchanged -- the gate is elsewhere.

        The deleted property answered ``$0.00`` here.  The resolver answers
        what the row's amount is and says nothing about whether it counts,
        which is the separation the amount model is built on: a status is a
        fact about CONTRIBUTION, not about ownership (see
        ``_amount_source.amount_rule`` on why soft deletion does not change the
        rule either).  Pinned so the difference is deliberate and visible
        rather than discovered.
        """
        with app.app_context():
            xfer = self._make_transfer(
                seed_user, seed_periods, "Cancelled", Decimal("500.00"),
            )
            assert resolve_transfer_amount(xfer) == Decimal("500.00")


class TestTransferSettleDay:
    """Tests for ``Transfer.settled_on`` -- the pair's shared day, read once.

    A transfer has no ``settled_on`` COLUMN: the day lives on its two shadow
    ``Transaction`` rows (Transfer Invariant 3).  The property exists so the
    full-edit form's correction input -- rendered from TWO blueprints, the
    transfers page and a grid shadow cell -- asks one question rather than each
    re-deriving "which shadow, and what if it is missing".
    """

    @staticmethod
    def _settled_transfer(seed_user, seed_periods, day):
        """Return a settled transfer whose pair carries *day*, through the service."""
        from app import ref_cache
        from app.enums import StatusEnum
        from app.models.ref import AccountType
        from app.services import transfer_service

        savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Savings",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(savings)
        db.session.flush()
        # Its BOOKS open before anything this fixture dates (plan step
        # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day
        # it asserts -- the owner's today -- and this helper settles the
        # transfer on a day the caller chooses, which is earlier.
        open_books_before_the_first_assertion(db.session, savings)

        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount=Decimal("250.00"),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=seed_user["categories"]["Rent"].id,
                name="Settle day probe",
            ),
        )
        db.session.flush()
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id, settle_day=an_entered_day(day),
        )
        db.session.flush()
        return xfer

    def test_a_projected_transfer_has_no_settle_day(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer whose money has not moved answers ``None``.

        Its shadows carry no day (the settled-iff-dated invariant), so the
        property must report the absence rather than inventing one -- the
        template renders no correction input on that answer.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import StatusEnum
            from app.models.ref import AccountType
            from app.services import transfer_service

            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("250.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                    name="Undated probe",
                ),
            )
            db.session.flush()
            assert xfer.settled_on is None

    def test_a_settled_transfer_reports_its_pairs_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The property returns the day both shadows carry.

        Asserted against the SHADOWS as well as against a literal, so the test
        grades "the property reads the pair" rather than "the property returns
        the constant the fixture happened to pass".
        """
        with app.app_context():
            day = display_today() - timedelta(days=21)
            xfer = self._settled_transfer(seed_user, seed_periods, day)

            shadow_days = {
                shadow.settled_on
                for shadow in db.session.query(Transaction)
                .filter_by(transfer_id=xfer.id, is_deleted=False)
                .all()
            }
            assert shadow_days == {day}
            assert xfer.settled_on == day

    def test_it_reads_the_income_shadow(
        self, app, db, seed_user, seed_periods,
    ):
        """The day comes from the TO-account shadow, the row the ledger reads.

        ``posting_service._entry_date`` dates a transfer's journal entry from
        the income shadow alone, so this property must read the same row: were
        it to read the expense side, a pair that had somehow diverged would
        render one day on the form and file the postings under another.  The
        divergence is forced here (bypassing the service, which is what keeps
        the pair equal) precisely because the two rows are otherwise identical
        and the choice would be untestable.

        **The unfalsifiable version of this test was fixed in the CODE, not
        here, and that is the point worth keeping.**  The property used to
        iterate the ``shadow_transactions`` backref, which declares no
        ``order_by`` -- so "reads the income shadow" and "reads whichever row
        the unordered SELECT returned first" were indistinguishable, and a
        POSITIONAL implementation is right half the time.  A neutral review
        planted a last-row read and it survived; a second, swapped-days control
        written against it survived too, because a positional read that happens
        to land on the income row lands there in both arrangements.  No test
        over a two-row pair can separate them.

        The property now names the row in SQL (``account_id ==
        to_account_id``), so position is not expressible and the only mutation
        left is naming the WRONG account -- which this test kills in both
        arrangements.  The days are still swapped and the property still asked
        twice, because that is what proves the answer tracks the ACCOUNT rather
        than a value the fixture happened to write last.
        """
        with app.app_context():
            day = display_today() - timedelta(days=21)
            xfer = self._settled_transfer(seed_user, seed_periods, day)
            income_shadow = (
                db.session.query(Transaction)
                .filter_by(
                    transfer_id=xfer.id, account_id=xfer.to_account_id,
                    is_deleted=False,
                )
                .one()
            )
            expense_shadow = (
                db.session.query(Transaction)
                .filter_by(
                    transfer_id=xfer.id, account_id=xfer.from_account_id,
                    is_deleted=False,
                )
                .one()
            )
            day_a = display_today() - timedelta(days=15)
            day_b = display_today() - timedelta(days=17)
            for income_day, expense_day in ((day_a, day_b), (day_b, day_a)):
                record_settle_day(income_shadow, an_entered_day(income_day))
                record_settle_day(expense_shadow, an_entered_day(expense_day))
                db.session.flush()
                db.session.expire(xfer, ["shadow_transactions"])

                assert xfer.settled_on == income_day, (
                    "the property did not read the income (to-account) shadow, "
                    "which is the row posting_service._entry_date dates the "
                    f"transfer's journal entry from: got {xfer.settled_on} "
                    f"with income={income_day} expense={expense_day}"
                )

    def test_a_soft_deleted_shadow_is_skipped(
        self, app, db, seed_user, seed_periods,
    ):
        """A soft-deleted shadow does not answer for the pair.

        A soft-deleted transfer's shadows are soft-deleted with it, and the
        backref carries them regardless; answering from one would show a day
        for a transfer that no longer moves money.
        """
        with app.app_context():
            day = display_today() - timedelta(days=21)
            xfer = self._settled_transfer(seed_user, seed_periods, day)
            for shadow in xfer.shadow_transactions:
                shadow.is_deleted = True
            db.session.flush()
            db.session.expire(xfer, ["shadow_transactions"])

            assert xfer.settled_on is None

    def test_it_cannot_be_assigned(
        self, app, db, seed_user, seed_periods,
    ):
        """Assigning the property raises -- the seam stays the single writer.

        ``status_seam.apply_status_change`` is the one door that writes
        ``Transaction.settled_on``; a settable property here would be a second
        one, which is the shape finding **N-183** closed on
        ``update_transfer``.  ``AttributeError`` is that refusal made
        structural rather than reviewed.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods, display_today() - timedelta(days=21),
            )
            with pytest.raises(AttributeError):
                record_settle_day(xfer, an_entered_day(date(2026, 7, 20)))


# ── Category.display_name ────────────────────────────────────────────


class TestCategoryDisplayName:
    """Tests for Category.display_name property."""

    def test_display_name_format(self, app, db, seed_user):
        """display_name returns 'group: item' format."""
        with app.app_context():
            cat = seed_user["categories"]["Rent"]
            assert cat.display_name == "Home: Rent"


# ── PaycheckBreakdown computed totals ────────────────────────────────


class TestPaycheckBreakdownTotals:
    """Tests for the section totals on PaycheckBreakdown.

    ``deductions.total_pre_tax`` / ``deductions.total_post_tax`` and
    ``taxes.total`` (formerly the flat ``total_pre_tax`` / ``total_post_tax``
    / ``total_taxes`` properties on the breakdown itself).
    """

    def test_total_pre_tax(self):
        """total_pre_tax sums pre-tax deduction amounts."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                gross_biweekly=Decimal("2884.62"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    DeductionLine(name="401k", amount=Decimal("250.00")),
                    DeductionLine(name="HSA", amount=Decimal("100.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("350.00")

    def test_total_post_tax(self):
        """total_post_tax sums post-tax deduction amounts."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                gross_biweekly=Decimal("2884.62"),
            ),
            deductions=DeductionBreakdown(
                post_tax=[
                    DeductionLine(name="Roth IRA", amount=Decimal("200.00")),
                    DeductionLine(name="Life Insurance", amount=Decimal("25.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_post_tax == Decimal("225.00")

    def test_total_taxes(self):
        """total sums federal + state + ss + medicare."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                gross_biweekly=Decimal("2884.62"),
            ),
            taxes=TaxLines(
                federal=Decimal("300.00"),
                state=Decimal("130.00"),
                social_security=Decimal("178.85"),
                medicare=Decimal("41.83"),
            ),
        )
        assert breakdown.taxes.total == Decimal("650.68")

    def test_empty_deductions_return_zero(self):
        """Empty deduction lists produce Decimal('0') totals."""
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                gross_biweekly=Decimal("2884.62"),
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("0")
        assert breakdown.deductions.total_post_tax == Decimal("0")

    def test_net_pay_stored_value(self):
        """net_pay is a stored field, not a computed property.

        The Earnings section stores net_pay as set by calculate_paycheck.
        Verify that when constructed with an explicit net_pay value, it
        equals gross - pre_tax - taxes - post_tax.
        Expected: net_pay == Decimal('1607.69').
        """
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("75000"),
                gross_biweekly=Decimal("2307.69"),
                net_pay=Decimal("1607.69"),
            ),
            taxes=TaxLines(
                federal=Decimal("250.00"),
                state=Decimal("100.00"),
                social_security=Decimal("75.00"),
                medicare=Decimal("25.00"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    DeductionLine(name="401k", amount=Decimal("200.00")),
                ],
                post_tax=[
                    DeductionLine(name="Roth", amount=Decimal("50.00")),
                ],
            ),
        )
        # Verify net_pay stores the exact value we set.
        assert breakdown.earnings.net_pay == Decimal("1607.69")
        # Cross-check: gross - all deductions/taxes should equal net_pay.
        expected = (
            breakdown.earnings.gross_biweekly
            - breakdown.deductions.total_pre_tax
            - breakdown.taxes.total
            - breakdown.deductions.total_post_tax
        )
        assert expected == Decimal("1607.69")
        assert breakdown.earnings.net_pay == expected

    def test_net_pay_all_zeros(self):
        """net_pay with zero deductions and taxes equals gross.

        When all deductions and taxes are zero, the entire gross pay
        should pass through as net pay.
        Expected: net_pay == Decimal('2000.00').
        """
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("52000"),
                gross_biweekly=Decimal("2000.00"),
                net_pay=Decimal("2000.00"),
            ),
        )
        assert breakdown.earnings.net_pay == Decimal("2000.00")
        assert breakdown.deductions.total_pre_tax == Decimal("0")
        assert breakdown.taxes.total == Decimal("0")
        assert breakdown.deductions.total_post_tax == Decimal("0")

    def test_net_pay_negative_when_deductions_exceed_gross(self):
        """net_pay can be negative when deductions exceed gross.

        The Earnings section does not clamp net_pay to zero.  If
        calculate_paycheck produces a negative net_pay (which is a data
        configuration error), the section stores it as-is.
        Expected: net_pay == Decimal('-200.00').
        """
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(period_id=1),
            earnings=Earnings(
                annual_salary=Decimal("52000"),
                gross_biweekly=Decimal("2000.00"),
                net_pay=Decimal("-200.00"),
            ),
            taxes=TaxLines(
                federal=Decimal("500.00"),
                state=Decimal("200.00"),
                social_security=Decimal("0"),
                medicare=Decimal("0"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    DeductionLine(name="401k", amount=Decimal("1500.00")),
                ],
            ),
        )
        assert breakdown.earnings.net_pay == Decimal("-200.00")
        # Verify the math: 2000 - 1500 - 700 = -200
        expected = (
            breakdown.earnings.gross_biweekly
            - breakdown.deductions.total_pre_tax
            - breakdown.taxes.total
            - breakdown.deductions.total_post_tax
        )
        assert expected == Decimal("-200.00")


# ── Transaction.days_until_due / days_paid_before_due ──────────────


class TestDaysUntilDue:
    """Tests for Transaction.days_until_due computed property."""

    def _make_txn(self, seed_user, seed_periods, status_name, due_date_val):
        """Helper: create a transaction with given status and due_date."""
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        txn = Transaction(
            user_id=seed_periods[0].user_id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=status.id,
            name="Test Due",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            amount_ownership=AmountOwnership.own(Decimal("100.00")),
            due_date=due_date_val,
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_days_until_due_future(self, app, db, seed_user, seed_periods):
        """Projected transaction with future due_date returns positive days."""
        with app.app_context():
            future = add_months(date.today(), 12)
            txn = self._make_txn(seed_user, seed_periods, "Projected", future)
            assert txn.days_until_due is not None
            assert txn.days_until_due > 0

    def test_days_until_due_past(self, app, db, seed_user, seed_periods):
        """Projected transaction with past due_date returns negative days."""
        with app.app_context():
            past = date(2020, 1, 1)
            txn = self._make_txn(seed_user, seed_periods, "Projected", past)
            assert txn.days_until_due is not None
            assert txn.days_until_due < 0

    def test_days_until_due_settled(self, app, db, seed_user, seed_periods):
        """Settled transaction returns None -- no action needed."""
        with app.app_context():
            future = add_months(date.today(), 12)
            txn = self._make_txn(seed_user, seed_periods, "Paid", future)
            assert txn.days_until_due is None

    def test_days_until_due_no_due_date(self, app, db, seed_user, seed_periods):
        """Transaction with no due_date returns None."""
        with app.app_context():
            txn = self._make_txn(seed_user, seed_periods, "Projected", None)
            assert txn.days_until_due is None


class TestSettleDayRefusesAnInstant:
    """The COLUMN refuses a ``datetime``, on every ORM write path (N-179).

    ``datetime`` subclasses ``date``, so the type annotation catches nothing and
    the value reaches PostgreSQL, which coerces it into the ``DATE`` column on
    the SESSION clock -- UTC.  An instant at 2026-03-04 04:30 UTC is
    2026-03-03 23:30 Eastern, so the row would store 2026-03-04: one day late,
    silently, which is the split ruling R-DH (b) exists to delete.

    **These pin the VALIDATOR, and they exist because nothing did.**  The seam
    calls :func:`~app.models.mixins.reject_settle_instant` itself, ahead of
    any assignment, so ``test_status_seam.py``'s refusal test proves the SEAM
    and cannot reach the column hook at all -- it asserts the row was left
    untouched, i.e. that the assignment never ran.  Deleting the ``@validates``
    decorator therefore regressed nothing detectable, which is finding N-182's
    own shape (a guarantee with no pin) inside the fix for N-179.  The hook is
    what makes the claim "the column simply does not accept the type" true of a
    future fixture or service that writes the attribute directly.
    """

    def test_a_plain_assignment_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """``txn.settled_on = <datetime>`` raises before anything is stored."""
        with app.app_context():
            status = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            txn = Transaction(
                user_id=seed_periods[0].user_id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=status.id,
                name="Instant refusal",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=expense_type.id,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
                **settle_day_columns(seed_periods[0].start_date),
                **settlement_columns(
                    seed_periods[0].start_date, Decimal("100.00"),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            # The BARE column assignment, which is exactly the path the
            # validator exists for: ``SettleDay`` refuses an instant at
            # construction, so a caller going through the pair writer never
            # reaches the column at all (plan step X-az).  This is the fixture
            # or service that writes the attribute directly.
            with pytest.raises(TypeError, match="must be a date"):
                txn.settled_on = datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc)
            # The stored day is untouched: the validator runs before the set.
            assert txn.settled_on == seed_periods[0].start_date

    def test_a_constructor_kwarg_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """``Transaction(settled_on=<datetime>)`` raises at construction.

        The declarative constructor assigns through ``setattr``, so the same
        validator covers it -- which is the path a fixture is most likely to
        take, and the one the X-f1 conversion actually took 16 times.
        """
        with app.app_context():
            status = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = (
                db.session.query(TransactionType).filter_by(name="Expense").one()
            )
            with pytest.raises(TypeError, match="must be a date"):
                Transaction(
                    user_id=seed_periods[0].user_id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    account_id=seed_user["account"].id,
                    status_id=status.id,
                    name="Instant refusal",
                    category_id=seed_user["categories"]["Groceries"].id,
                    transaction_type_id=expense_type.id,
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    settled_on=datetime(2026, 3, 4, 4, 30, tzinfo=timezone.utc),
                )

    def test_a_civil_date_and_none_both_pass_through(self):
        """The rule refuses instants only -- it is not a general type gate.

        Asserted on the shared function rather than through the ORM so the
        pass-through arms are stated where the refusal is, and so a future
        caller can see that ``None`` (an unsettled row) is legal.
        """
        assert reject_settle_instant(date(2026, 3, 4)) == date(2026, 3, 4)
        assert reject_settle_instant(None) is None


class TestDaysPaidBeforeDue:
    """Tests for Transaction.days_paid_before_due computed property."""

    def _make_txn(self, seed_user, seed_periods, due_date_val, settled_on_val):
        """Helper: create a transaction with given due_date and settle day."""
        status = db.session.query(Status).filter_by(name="Paid").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        txn = Transaction(
            user_id=seed_periods[0].user_id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            account_id=seed_user["account"].id,
            status_id=status.id,
            name="Test Paid Timing",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=expense_type.id,
            amount_ownership=AmountOwnership.own(Decimal("100.00")),
            due_date=due_date_val,
            **settle_day_columns(settled_on_val),
            **settlement_columns(settled_on_val, Decimal("100.00")),
        )
        db.session.add(txn)
        db.session.flush()
        return txn

    def test_days_paid_before_due_early(self, app, db, seed_user, seed_periods):
        """Paid 3 days before due returns 3 (positive = early)."""
        with app.app_context():
            due = date(2026, 3, 15)
            paid = date(2026, 3, 12)
            txn = self._make_txn(seed_user, seed_periods, due, paid)
            assert txn.days_paid_before_due == 3

    def test_days_paid_before_due_late(self, app, db, seed_user, seed_periods):
        """Paid 2 days after due returns -2 (negative = late)."""
        with app.app_context():
            due = date(2026, 3, 15)
            paid = date(2026, 3, 17)
            txn = self._make_txn(seed_user, seed_periods, due, paid)
            assert txn.days_paid_before_due == -2

    def test_days_paid_before_due_no_settle_day(self, app, db, seed_user, seed_periods):
        """No settle day returns None -- an unsettled row's timeliness is moot."""
        with app.app_context():
            txn = self._make_txn(
                seed_user, seed_periods,
                due_date_val=date(2026, 3, 15),
                settled_on_val=None,
            )
            assert txn.days_paid_before_due is None

    def test_days_paid_before_due_is_exact_civil_date_arithmetic(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle ON the due date is on time (0), with no timezone in it.

        **This test used to carry the F3 rule and no longer can, by
        construction.**  It pinned an 8:05pm-Eastern settle -- stored as
        2026-01-16 01:05 UTC -- reporting 0 against a 2026-01-15 due date,
        because the property converted the instant to the display timezone
        before truncating it; truncating in UTC reported -1, a day late by
        wall-clock drift.  Plan step X-f1 removed the conversion along with the
        instant: both operands are civil dates now and the subtraction is exact.

        **The rule did not disappear, it MOVED to the write door**, which is the
        only place an instant still becomes a day:
        ``test_status_seam.py::TestApplyStatusChangeSettleDay::``
        ``test_the_stamped_day_is_the_users_day_not_the_process_utc_day``
        freezes the clock at an evening-Eastern instant and asserts the seam
        records the Eastern day.  Naming that here matters: a reader who finds
        this test looking for F3's coverage must be sent to where it lives, not
        left thinking it was dropped.
        """
        with app.app_context():
            due = date(2026, 1, 15)
            txn = self._make_txn(seed_user, seed_periods, due, due)
            assert txn.days_paid_before_due == 0
