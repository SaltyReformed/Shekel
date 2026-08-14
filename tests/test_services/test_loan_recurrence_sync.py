"""Tests for loan_recurrence_sync (Risk R-4: recurring end_date off the GET path).

``app.services.loan_recurrence_sync`` keeps a loan's recurring-payment
``RecurrenceRule.end_date`` equal to the loan's projected payoff, so the
recurrence engine stops generating shadow transactions past payoff.  It used to
run as a write on the loan-detail GET (Risk R-4); it now runs at every
payoff-affecting mutation.

Since plan step C8d the bound is DERIVED from the balance
(``balance_at.loan_payoff_date`` -- the date the fold reaches zero) instead of
being read off the last row of the resolver's committed schedule walk.  These
tests pin the pure mapping (``recurrence_end_date``) and the service
(``sync_recurring_payment_bounds``) against real loans.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import loan_recurrence_sync
from app.services.loan_recurrence_sync import recurrence_end_date
from app.services.loan_loaders import load_loan_params
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    freeze_today,
    make_transfer_template,
)


class TestRecurrenceEndDate:
    """The three states of a DERIVED payoff, mapped onto the recurrence bound.

    Takes the payoff and the retired predicate directly -- there is no schedule
    to hand-build any more, which is the point: the pre-C8d function read
    ``remaining_balance`` off stub rows, so it could only ever be tested against
    a schedule shape rather than against a loan.
    """

    def test_a_payoff_date_is_the_bound(self):
        """A loan that pays off stops recurrence the month it reaches zero."""
        assert recurrence_end_date(
            date(2030, 2, 1), False, date(2026, 7, 1),
        ) == date(2030, 2, 1)

    def test_a_retired_loan_halts_at_the_as_of(self):
        """A RETIRED loan plans no further payments, so the bound is the as-of.

        ``None`` here means "no forward crossing left", not "never pays off":
        the loan already owes nothing.  Any past-or-today bound halts future
        generation; the as-of is the pass's own now, so there is one rule rather
        than a per-producer fallback date.
        """
        assert recurrence_end_date(
            None, True, date(2026, 7, 1),
        ) == date(2026, 7, 1)

    def test_a_loan_that_never_pays_off_stays_indefinite(self):
        """``None`` and NOT retired leaves recurrence unbounded.

        Negative amortization, or an underpayment too severe to clear even the
        plan's post-contractual extension.  The payments must keep generating --
        the loan still owes -- until the user raises the payment (which is what
        C7's drift warning prompts).
        """
        assert recurrence_end_date(None, False, date(2026, 7, 1)) is None


class TestSyncRecurringPaymentBounds:
    """The relocated end_date write, driven directly against a resolvable loan."""

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected schedule is deterministic."""
        freeze_today(monkeypatch, date(2026, 7, 1))

    def _loan(self, seed_user, db_session):
        """A 24-month $12,000 loan originated 2025-01-01, with NO payments made."""
        return create_loan_account(
            seed_user, db_session, name="Recurring Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2025, 1, 1),
        )

    def _current_loan(self, seed_user, db_session):
        """A 24-month $12,000 loan originating TODAY -- nothing overdue yet."""
        return create_loan_account(
            seed_user, db_session, name="Current Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 7, 1),
        )

    def test_a_current_loan_bounds_at_its_contractual_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with nothing overdue bounds recurrence at its contractual payoff.

        Originated on the as-of, so its whole 24-month term is ahead of it and
        every installment is synthesized at the contractual P&I: the fold reaches
        zero on the contractual last installment, 2028-07-01 (origination
        2026-07-01 + 24 monthly payments, the first on 2026-08-01).  This is the
        no-drift control for the delinquent case below -- the derived payoff and
        the contractual payoff are the SAME date when the borrower is on plan.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            assert rule.end_date is None

            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date == date(2028, 7, 1)
            assert isinstance(rule.end_date, date)

    def test_a_count_bound_is_REPLACED_by_the_derived_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The crash plan step R7b-3's bound type exists to make impossible.

        A loan payment's stop is DERIVED, and this module states its change as
        ``replace(spec, end_bound=...)``.  While the bound was two independent
        columns the same call wrote a date beside a count the rule already
        carried, and ``ck_recurrence_rules_single_end_bound`` refused the pair
        at the flush -- a 500 on an ordinary loan edit.

        A count can only reach a loan payment's rule around the form door,
        which refuses one; this drives the sync directly against such a row, so
        the TYPE's half of the guarantee is pinned rather than resting on the
        door's.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            rule = tpl.recurrence_rule
            rule.max_occurrences = 12
            db.session.commit()

            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date == date(2028, 7, 1)
            assert rule.max_occurrences is None

    def test_a_count_bound_is_cleared_even_when_the_loan_never_pays_off(
        self, app, db, seed_user, seed_periods,
    ):
        """The case the COLUMN comparison could not see.

        The idempotence guard used to read ``rule.end_date``; a count-bounded
        rule has ``end_date IS NULL``, so against a loan whose derived payoff is
        ``None`` it compared ``None == None`` and returned early -- leaving a
        count bound on a payment whose stop this module owns.  Comparing BOUNDS
        is what closes it.

        Reached with a template that names no loan the seam can value: the
        no-configured-loan path returns before any write, so the case is built
        instead on a loan that DOES resolve and a bound that is already
        correct -- the count must still go.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            rule = tpl.recurrence_rule
            db.session.commit()

            # First sync writes the derived payoff.
            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)
            payoff = rule.end_date
            assert payoff is not None

            # Now put the rule in the state only a row written around the form
            # door can reach: a COUNT bound where the derived answer is a date.
            rule.end_date = None
            rule.max_occurrences = 6
            db.session.commit()

            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date == payoff
            assert rule.max_occurrences is None

    def test_unpaid_overdue_installments_push_the_bound_out(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan whose past installments were never PAID pays off later (B-9).

        This 24-month loan originated 2025-01-01 and is read on 2026-07-01 with
        no settled payment at all, so it still owes the full $12,000.00 with only
        seven contractual installments left.  The pre-C8d bound came off the
        resolver's schedule walk, which amortizes an installment per month
        whether or not one was paid, and so reported the CONTRACTUAL 2027-01-01 --
        a payoff the borrower has not remotely earned.  The fold reports when the
        balance actually reaches zero: the seven remaining contractual
        installments plus the post-contractual extension (plan C8c) at the same
        level payment.  Hand-checked: the level P&I on $12,000.00 / 24 months /
        5% is $526.46, and $12,000.00 at 5%/12 amortizes in exactly 24 payments
        at that figure -- so a borrower who has paid NOTHING is still a full
        24 installments from zero.  Counting from the first one the plan
        synthesizes (2026-07-01, since a strictly-past installment with no record
        pays nothing) that lands on 2026-06-01: seven contractual installments
        and seventeen from the extension, 18 months past the contractual
        2027-01-01.
        """
        with app.app_context():
            loan = self._loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule

            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date is not None
            assert rule.end_date > date(2027, 1, 1), (
                f"end_date {rule.end_date} is at or before the CONTRACTUAL "
                "payoff 2027-01-01, so the bound is still coming off the "
                "schedule walk that pays down installments nobody paid (B-9)."
            )
            assert rule.end_date == date(2028, 6, 1)

    def test_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same payoff writes nothing new.

        A genuine fixpoint, not just a skipped write: the first sync bounds
        shadow generation at the payoff, and re-deriving against that narrower
        plan returns the same date (the removed payments are the ones the fold
        had already run past zero on).
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_transfer_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            first = rule.end_date
            assert first is not None

            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)
            assert rule.end_date == first

    def test_no_template_is_a_noop(self, app, db, seed_user, seed_periods):
        """A loan with no recurring transfer is a safe no-op (no crash)."""
        with app.app_context():
            loan = self._loan(seed_user, db.session)
            # No template created; the sync must return cleanly.
            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()

    def test_unconfigured_account_is_a_noop(self, app, db, seed_user):
        """A non-loan account with no recurring transfer is a safe no-op.

        Returns at the template check, before the seam is consulted at all.
        """
        with app.app_context():
            loan_recurrence_sync.sync_recurring_payment_bounds(
                seed_user["account"].id,
            )
            db.session.commit()

    def test_an_amortizing_account_without_params_is_a_noop(
        self, app, db, seed_user, seed_periods,
    ):
        """A MORTGAGE-typed account with a recurring transfer but NO LoanParams.

        The not-a-loan guard's own shape, and it is reachable: an account whose
        TYPE is amortizing but whose loan details were never filled in still
        classifies as amortizing, so a transfer settling into it reaches this
        sync (``transfer_service._loan_posting`` gates on the account TYPE, not on the
        params row).  The seam's ``loan_figures`` answers ``None`` for it, which
        is what this must return on -- without the guard the payoff read would
        raise on ``None``, from a WRITE path, mid-mutation.
        """
        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Unconfigured Mortgage",
            )
            db.session.flush()
            assert load_loan_params(acct.id) is None, (
                "precondition: this account must have NO LoanParams"
            )
            make_transfer_template(db.session, seed_user, acct)
            db.session.commit()

            loan_recurrence_sync.sync_recurring_payment_bounds(acct.id)
            db.session.commit()
