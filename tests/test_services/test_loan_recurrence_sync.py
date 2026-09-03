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

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import BaselineMissingError
from app.services import balance_at, loan_recurrence_sync, template_amount_service
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    ClosesOn,
    DerivedStop,
    Empty,
    Indefinite,
    resolved_recurrence,
)
from app.services.loan_recurrence_sync import (
    loan_payment_window,
    recurrence_end_date,
)
from app.services.loan_payment_service import compute_contractual_pi
from app.services.loan_loaders import load_loan_params, load_rate_changes
from app.models.pay_period import PayPeriod
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    capture_sql_statements,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_every_period_rule,
    make_expense_template,
    make_loan_payment_template,
    make_transfer_template,
)
from tests.oracles.recurrence_baseline import MONTHLY


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
            tpl = make_loan_payment_template(db.session, seed_user, loan)
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
            tpl = make_loan_payment_template(db.session, seed_user, loan)
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
            tpl = make_loan_payment_template(db.session, seed_user, loan)
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
            tpl = make_loan_payment_template(db.session, seed_user, loan)
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

    def test_a_STATED_price_at_the_contractual_figure_bounds_identically(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that STATES the contract's figure bounds where DERIVE does.

        **The arm production is actually in, driven through the real sync.**
        ``budget.loan_payment_settings`` holds ZERO rows on the developer's
        database, so both live loan payments state a price rather than deriving
        one -- and plan step R7d-a made that distinction decide how every
        uncovered installment is priced. An adversarial review found the fixture
        repair had moved every sync test into the DERIVE arm, where the new rule
        reduces to the old behaviour, leaving the production arm untested
        through any door.

        A definition stating exactly the contractual P&I must reach the same
        bound as one deriving it: 2028-07-01, the control above's figure, from
        the same $12,000.00 / 24-month / 5% loan whose level payment is $526.46.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            params = load_loan_params(loan.id)
            contractual_pi = compute_contractual_pi(
                params, load_rate_changes(loan.id),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan,
                amount=str(contractual_pi), derive_from_loan=False,
            )
            template_amount_service.set_amount(
                tpl, contractual_pi, effective_on=params.origination_date,
            )
            db.session.commit()
            rule = tpl.recurrence_rule

            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date == date(2028, 7, 1)

    def test_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same payoff writes nothing new.

        A genuine fixpoint, not just a skipped write: the first sync bounds
        shadow generation at the payoff, and re-deriving against that narrower
        plan returns the same date (the removed payments are the ones the fold
        had already run past zero on).
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
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
            make_loan_payment_template(db.session, seed_user, acct)
            db.session.commit()

            loan_recurrence_sync.sync_recurring_payment_bounds(acct.id)
            db.session.commit()


class TestOwnsValidityWindow:
    """WHICH definitions this module writes bounds for -- asked once.

    Plan step R7b-4 made this the ONE predicate the recurrence form's two bound
    controls lock on and the two crafted-POST refusals fire on, because the
    form was asking a DIFFERENT question and the two disagreed on live data:
    ``_recurrence_form_refusals.is_loan_payment`` reads
    ``settings is not None``, and neither of the developer's real loan payments
    carries a ``loan_payment_settings`` row -- so the R7b-3 "Ends" lock never
    fired on either loan and a typed end date would have been silently
    overwritten by the next payoff-affecting edit.

    Every arm is exercised, and the THREE False ones are the point: a predicate
    that only ever returns True where it is asked is indistinguishable from no
    predicate.  Each mirrors one early return in
    :func:`~app.services.loan_recurrence_sync.sync_recurring_payment_bounds`.
    """

    def test_a_loans_active_recurring_payment_owns_its_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The True arm: exactly the template the sync writes for."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            template = make_loan_payment_template(db.session, seed_user, loan)
            db.session.flush()

            assert loan_recurrence_sync.owns_validity_window(template) is True

    def test_a_transfer_into_a_NON_loan_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The destination must be a CONFIGURED loan (``LoanParams``).

        Mirrors the sync's ``load_loan_params(...) is None`` return: a savings
        contribution has no contractual installment and no payoff, so nothing
        derives its bounds and its form must let the user set them.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.flush()

            assert loan_recurrence_sync.owns_validity_window(template) is False

    def test_a_SECOND_recurring_payment_into_one_loan_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Only the template the lookup RETURNS is the one written for.

        ``active_recurring_transfer_template`` answers ONE active recurring
        transfer into the account, so a second is never synced -- and its form
        must therefore not claim its bounds come from the loan. This is the arm
        the older predicate got wrong in the other direction: both templates
        carry settings if both were created through the loan flow, so
        ``settings is not None`` would lock BOTH, and the user would be told
        two different rules were derived from one payoff.

        **Asserted as "exactly one", not "the first one", and the difference is
        a real property of the lookup**: it is ``.first()`` with no
        ``ORDER BY``, so which row PostgreSQL returns is unspecified. That is
        pre-existing and deliberate -- its own docstring calls two recurring
        transfers into one account "a user misconfiguration, not a modeled
        case" -- and pinning a particular winner here would assert something
        the query does not promise. What matters for the lock is that the two
        templates never both claim it.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            first = make_loan_payment_template(db.session, seed_user, loan)
            # Built directly: the shared helper hardcodes one name and
            # ``uq_transfer_templates_user_name`` refuses a second.
            second = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=loan.id,
                name="Second Payment Into One Loan",
                default_amount=Decimal("50.00"),
            )
            db.session.add(second)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, second)
            db.session.flush()

            owned = [
                loan_recurrence_sync.owns_validity_window(first),
                loan_recurrence_sync.owns_validity_window(second),
            ]
            assert owned.count(True) == 1, (
                f"exactly one of two recurring payments into one loan may own "
                f"its validity window, got {owned}"
            )

    def test_a_definition_that_does_not_repeat_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """No rule means no bounds to derive.

        Mirrors the sync's ``template.recurrence_rule is None`` return, and it
        is what stops a one-time transfer into a loan rendering locked
        controls for a rule that does not exist.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            template = make_loan_payment_template(db.session, seed_user, loan)
            template.recurrence_rule = None
            db.session.flush()

            assert loan_recurrence_sync.owns_validity_window(template) is False

    def test_a_transaction_template_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A kind with no destination account at all answers False, not raises.

        The two form helpers are deliberately kind-agnostic -- they render
        transaction templates through the same call -- so the predicate has to
        answer for a row that carries no ``to_account_id``.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()

            assert loan_recurrence_sync.owns_validity_window(template) is False


class TestTheDerivedStopShapes:
    """The three shapes, as pure values -- no loan, no database, no clock.

    :meth:`~app.services.recurrence.DerivedStop.admits` is the
    ONE question every shape answers, so every shape is asked it here and the
    boundary is asked on both sides.  A shape whose ``admits`` were left
    unwritten cannot be constructed at all (``@abstractmethod``), which is the
    half of the contract :class:`TestTheDerivedStopIsTotal` pins.
    """

    def test_a_closing_date_admits_its_own_day(self):
        """The payoff installment is OWED, so the bound is inclusive.

        The balance reaches zero AT that installment -- it is the payment that
        clears the loan, not the first one past it -- so an exclusive bound
        would drop the final payment from every projection.
        """
        assert ClosesOn(on=date(2029, 2, 22)).admits(date(2029, 2, 22)) is True

    def test_a_closing_date_refuses_the_day_after(self):
        """One day past the payoff is the first occurrence a walk must stop on."""
        assert ClosesOn(on=date(2029, 2, 22)).admits(date(2029, 2, 23)) is False

    def test_a_closing_date_admits_everything_before_it(self):
        """Every occurrence inside the loan's life stands."""
        assert ClosesOn(on=date(2029, 2, 22)).admits(date(2026, 4, 22)) is True

    def test_indefinite_admits_a_date_far_past_any_contract(self):
        """A loan that never pays off never stops its payments.

        Asked with a date past any term this application can express, because
        "unbounded" that has a hidden bound is the failure this shape exists to
        make unconstructible.
        """
        assert INDEFINITE.admits(date(2099, 12, 31)) is True

    def test_empty_refuses_the_first_occurrence_it_was_measured_against(self):
        """An empty window covers no date, including the one it opens on.

        Plan ledger row **D35**'s measurement: originate 2026-08-01 with a
        ``payment_day`` of 1 (first installment 2026-09-01) and true the balance
        to zero on 2026-08-15.  Nought occurrences is the CORRECT answer.
        """
        assert EMPTY.admits(date(2026, 9, 1)) is False

    def test_empty_refuses_a_date_before_the_window_too(self):
        """Not "everything after X" -- nothing at all, in both directions."""
        assert EMPTY.admits(date(2020, 1, 1)) is False

    def test_the_singletons_equal_a_freshly_built_shape(self):
        """A caller may compare against the module constant OR construct one.

        Frozen dataclasses compare by value, so the two spellings are one
        value; a reader holding ``INDEFINITE`` and a reader building
        ``Indefinite()`` cannot come to disagree.
        """
        assert INDEFINITE == Indefinite()
        assert EMPTY == Empty()
        assert INDEFINITE != EMPTY

    def test_an_empty_window_is_not_a_closing_date_that_admits_nothing(self):
        """The distinction the ``date``-or-``None`` shape could not carry.

        Both admit nothing, and that identity is deliberate -- generation emits
        nothing either way.  What a reader must be able to say about them
        differs: "until Aug 15, 2026" is false about a definition that fires
        from September, where "this loan is finished" is true.  If these ever
        compare equal, that distinction has been lost and every DISPLAY reader
        plan step R7d-d moves over inherits the wrong sentence.
        """
        closes_before_it_starts = ClosesOn(on=date(2026, 8, 15))
        assert closes_before_it_starts.admits(date(2026, 9, 1)) is False
        assert EMPTY.admits(date(2026, 9, 1)) is False
        assert closes_before_it_starts != EMPTY


class TestTheDerivedStopIsTotal:
    """A shape that does not answer ``admits`` cannot exist.

    The ``@abstractmethod`` is not decoration: the default it refuses -- "a
    shape that does not recognise the question keeps firing" -- is a
    commitment to go on charging a debt the owner has cleared.  Pinned by
    CONSTRUCTION rather than by reading the source, so a later step that adds a
    fourth shape and forgets the method fails here rather than in production.
    """

    def test_the_base_type_itself_cannot_be_instantiated(self):
        """``DerivedStop()`` is not a stop; it is the question."""
        with pytest.raises(TypeError):
            DerivedStop()  # pylint: disable=abstract-class-instantiated

    def test_a_shape_omitting_admits_cannot_be_instantiated(self):
        """The fourth-shape trap, sprung deliberately.

        This is what a half-written plan step R8 shape would look like; it must
        be a ``TypeError`` at construction and never a window that silently
        admits everything.
        """
        class _HalfWritten(DerivedStop):
            """A shape that states no rule for admitting an occurrence."""

        with pytest.raises(TypeError):
            _HalfWritten()  # pylint: disable=abstract-class-instantiated

    def test_every_concrete_shape_answers_admits(self):
        """Each shape returns a real bool, never ``None`` or a truthy stand-in.

        ``is True`` / ``is False`` rather than truthiness: a shape returning
        ``None`` would read as "does not admit" at every call site and pass a
        truthiness assertion while meaning nothing.
        """
        answers = [
            ClosesOn(on=date(2030, 1, 1)).admits(date(2029, 1, 1)),
            INDEFINITE.admits(date(2029, 1, 1)),
            EMPTY.admits(date(2029, 1, 1)),
        ]
        assert answers == [True, True, False]
        assert all(isinstance(answer, bool) for answer in answers)


#: One instance of each window shape, so immutability is asserted over the
#: WHOLE set rather than over three hand-written examples.  Held total by
#: ``TestTheWindowShapesAreValues.test_every_concrete_shape_is_sampled``.
_WINDOW_SAMPLES: dict[type[DerivedStop], DerivedStop] = {
    ClosesOn: ClosesOn(on=date(2029, 2, 22)),
    Indefinite: INDEFINITE,
    Empty: EMPTY,
}


class TestTheWindowShapesAreValues:
    """Frozen records -- a DERIVED window is not mutable state."""

    def test_every_concrete_shape_is_sampled(self):
        """A shape left out of the table narrows the sweep below in silence.

        The contract ``END_BOUND_KINDS`` gets in ``test_recurrence_bounds``:
        the property is asserted over the CLOSED SET, so a fourth shape a
        later step adds fails here rather than quietly sitting outside it.

        **Scoped to shapes declared in ``app/``, and this file is why.**
        ``TestTheDerivedStopIsTotal`` above declares ``_HalfWritten``
        inside a test body, ``__subclasses__()`` is a live interpreter-wide
        registry, and a class object survives until the cyclic collector takes
        it.  Unscoped, this gate fails whenever that test has already run --
        a failure unrelated to the code, which is broken rather than flaky.
        """
        declared_in_app = {
            kind for kind in DerivedStop.__subclasses__()
            if kind.__module__.startswith("app.")
        }

        assert declared_in_app == set(_WINDOW_SAMPLES)

    @pytest.mark.parametrize("kind", list(_WINDOW_SAMPLES))
    def test_no_shape_can_be_mutated_after_construction(self, kind):
        """Two of the three carry no field, and both are SHARED singletons.

        :data:`INDEFINITE` and :data:`EMPTY` hold no value, so the module
        builds ONE of each and hands the same object to every caller -- the
        comment beside them gives frozen-ness as the reason that is safe.
        Nothing asserted it until this test.

        Probed through ``admits``, the one name every shape declares: the base
        makes it ``@abstractmethod``, so a shape without one cannot be built
        at all (``TestTheDerivedStopIsTotal`` holds that), which makes it
        each shape's OWN name rather than an arbitrary one.  **One name is the
        whole claim** -- frozen is a property of the CLASS and is
        all-or-nothing, so a shape that refuses one name refuses every name,
        and walking ``dataclasses.fields`` instead would ask ZERO times for
        the two shapes that carry none.

        ``admits`` is also the name with the money on it.  An instance
        attribute shadowing the method answers for every holder of the
        singleton at once, and that is the difference between a loan that goes
        on charging a debt the owner has cleared and one that stops paying a
        debt they still owe.
        """
        with pytest.raises(FrozenInstanceError):
            setattr(_WINDOW_SAMPLES[kind], "admits", None)


class TestLoanPaymentWindowResolver:
    """The RESOLVER, against real loans (plan step R7d-b).

    Its first reader arrived at plan step R7d-d -- the composed door
    ``recurring_definition.resolved_definition``, which puts this answer on
    the resolved recurrence's ``Closing`` -- so these are no longer the whole
    of its coverage; ``test_recurring_definition`` grades what a surface does
    with the answer, and this grades the answer.
    """

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected schedule is deterministic."""
        freeze_today(monkeypatch, date(2026, 7, 1))

    def _ctx(self, seed_user):
        """The read pass every resolve in this class is measured against."""
        return BalanceContext.build(seed_user["user"].id, date(2026, 7, 1))

    def _current_loan(self, seed_user, db_session):
        """A 24-month $12,000 loan originating TODAY -- nothing overdue yet."""
        return create_loan_account(
            seed_user, db_session, name="Window Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 7, 1),
        )

    def test_a_live_loan_closes_on_its_DERIVED_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver answers the date the BALANCE folds to zero.

        The same $12,000 / 24-month / 5% loan the sync tests use, whose level
        P&I is $526.46 and whose fold therefore reaches zero on 2028-07-01
        (origination 2026-07-01, first installment 2026-08-01).  Asserted
        against the seam's own figure as well as against the date, so this
        cannot pass by agreeing with a hardcoded constant the seam has moved
        away from.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date == date(2028, 7, 1)

            assert loan_payment_window(tpl, ctx) == ClosesOn(
                on=date(2028, 7, 1),
            )

    def test_it_agrees_with_what_the_SYNC_writes_into_the_column(
        self, app, db, seed_user, seed_periods,
    ):
        """The additive claim, measured: the resolver moves no figure.

        Plan step R7d-b changes no behaviour precisely because the resolver
        answers what the ten call sites already write.  The window and the
        column are derived by two different code paths here -- one through
        :func:`recurrence_end_date` into an ``EndBound``, one through it into a
        :class:`~app.services.recurrence.DerivedStop` -- so this is the seam where they could
        disagree, and R7d-g deletes the writer on the strength of them not
        doing so.

        **It grades the WRAPPING, not the MAPPING**, and that limit is worth
        stating because this test is named as what R7d-g's deletion rests on:
        both paths call the same :func:`recurrence_end_date`, so a wrong RULE
        inside it would move both together and read green here. What it can see
        is the two ways that one answer is dressed coming apart.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            loan_recurrence_sync.sync_recurring_payment_bounds(loan.id)
            db.session.commit()
            rule = tpl.recurrence_rule
            db.session.refresh(rule)
            assert rule.end_date is not None

            window = loan_payment_window(tpl, self._ctx(seed_user))

            assert window == ClosesOn(on=rule.end_date)

    def test_a_SECOND_recurring_transfer_into_one_loan_gets_the_SAME_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The property that DISSOLVES plan ledger row **D47** for the bound.

        Nothing states which recurring transfer into a loan is "the" payment,
        and
        :func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`
        tie-breaks it on ``id``.  Measured on a production clone, a
        ``$200.00``/mo transfer into the Mortgage created before the real
        ``$1,910.95`` one wins that tie-break and drives the derived payoff
        from ``2048-12-01`` to ``None``.

        The resolver never asks: every recurring transfer into a loan is
        paying it down (the settled fold and the PLANNED tier both already sum
        every one of them with no template filter), and each stops when the
        loan does.  So the two definitions here must get the SAME answer --
        which is what makes ``owns_validity_window``'s "is it the one the
        lookup returns" clause unnecessary at plan step R7d-f.

        Contrast ``TestOwnsValidityWindow``'s twin, which asserts exactly ONE
        of two such templates owns its window: that is the predicate this
        replaces, and the difference between the two assertions is the whole
        ruling (**R-R35**).

        **What it does NOT assert is that the shared answer is RIGHT**, and an
        adversarial review of this step is why that is written down. The
        window's SUBJECT is no longer tie-broken, but its VALUE still reaches
        the tie-break through ``loan_figures`` -> ``standing_payment`` ->
        ``active_recurring_transfer_template``, so with the sweep winning that
        pick both definitions can agree on a WRONG window. Plan ledger row
        **D47** carries that half and **R16** closes it.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            # **The SWEEP is built FIRST, and that ordering is the point.**
            # ``active_recurring_transfer_template`` tie-breaks on ascending
            # ``id``, so a definition created earlier WINS it. Building the
            # loan payment first would let the tie-break pick the RIGHT
            # template, and the test would pass without exercising anything.
            # Built directly: the shared helper hardcodes one name and
            # ``uq_transfer_templates_user_name`` refuses a second.
            sweep = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=loan.id,
                name="Sweep Into One Loan",
                default_amount=Decimal("50.00"),
            )
            db.session.add(sweep)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, sweep)
            payment = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            assert sweep.id < payment.id, (
                "precondition: the sweep must WIN the id tie-break, or this "
                "test never exercises the wrong pick"
            )

            ctx = self._ctx(seed_user)
            sweep_window = loan_payment_window(sweep, ctx)
            payment_window = loan_payment_window(payment, ctx)

            assert sweep_window == payment_window, (
                "two recurring transfers into one loan resolved to different "
                "windows, so the bound still depends on WHICH definition is "
                "asked -- the identity question R-R35 deletes"
            )

    def test_a_loan_that_never_pays_off_is_INDEFINITE(
        self, app, db, seed_user, seed_periods,
    ):
        """The shape that must not be spelled as a date.

        A $240,000 / 30-year contract at 6% trued up to $900,000: the ~$1,439
        level payment cannot cover $4,500 of monthly interest, so the balance
        grows and the fold never reaches zero.  The payments must keep
        generating -- the loan still owes -- which is why ``None`` from
        :func:`recurrence_end_date` is a window shape rather than a missing
        answer.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Never Clears",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2026, 1, 1),
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("900000.00"),
            )
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date is None, "precondition: it never clears"
            assert figures.is_retired is False, "precondition: it still owes"

            assert loan_payment_window(tpl, ctx) == INDEFINITE

    def test_a_RETIRED_loan_is_ALREADY_OVER_and_names_no_closing_date(
        self, app, db, seed_user, seed_periods,
    ):
        """A finished loan whose payment HAS already fired is over, not dated.

        **This asserted ``ClosesOn(2026-07-01)`` until plan step R7d-d**, and
        ruling **R-R50** (developer, 2026-09-02) is what changed the expected
        answer rather than the code drifting from it.  That date is the READ
        PASS's own now -- :func:`recurrence_end_date` substitutes it because a
        retired loan has no forward crossing for
        :func:`~app.services.balance_at.loan_payoff_date` to date -- so
        spelling it ``ClosesOn`` stated a fact about when the page was loaded
        as a fact about the loan.  Measured on a production clone with the Van
        Loan trued to ``$0.00``: the same untouched loan answered 2026-09-02,
        2026-09-03 and 2026-12-25 on three read dates, and once plan step
        R7d-g NULLs the cached column nothing pins that date at all.

        The CONTROL for the EMPTY case below, and the pair is what proves the
        two shapes are told apart rather than collapsed.  Both loans are
        retired, so both map through :func:`recurrence_end_date` to the SAME
        date -- 2026-07-01 -- and the ONLY difference between them is where it
        falls relative to the rule's first occurrence.  This loan originated
        2026-05-01 with a ``payment_day`` of 1, so its first contractual
        installment is 2026-06-01: already past, so the definition HAS fired
        and "never runs" would be false about it.

        **The date this closes on is the READ PASS's own now, and that is the
        defect plan step R7d-h deletes.**  A retired loan has no forward
        crossing, so ``recurrence_end_date`` substitutes ``ctx.as_of`` -- which
        means the admitted set GROWS by one occurrence per cadence period as
        the clock moves.  R7d-h gives the loan one closing date over its past
        as well as its future, after which this test's expected value becomes
        the day the loan was actually cleared and stops depending on when it is
        read.

        The rule's opening bound is written by ``bind_rule_to_loan``, the
        production door, rather than by a fixture day -- so the date this rests
        on is the one the app itself derives from the contract.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Retired Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 5, 1), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            loan_recurrence_sync.bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.is_retired is True, "precondition: it owes nothing"
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 6, 1), (
                "precondition: the first contractual installment is one month "
                f"after origination, got {rule.starts_on}"
            )
            assert rule.starts_on <= date(2026, 7, 1), (
                "precondition: this loan's payment has already fired, which is "
                "the ONLY thing separating it from the EMPTY case below"
            )

            window = loan_payment_window(tpl, ctx)

            assert window == ClosesOn(on=date(2026, 7, 1))
            assert window.admits(date(2026, 7, 1)) is True
            assert window.admits(date(2026, 7, 2)) is False

    def test_a_loan_RETIRED_before_its_payment_first_fires_is_EMPTY(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row **D35**'s own measurement, resolved.

        A loan originated 2026-06-20 with a ``payment_day`` of 15 owes its
        first installment 2026-07-15; true its balance to zero the day after
        origination and it retires, so the derived window closes at the read
        pass's now -- 2026-07-01, BEFORE the rule ever fires.  That pair is
        ``[2026-07-15, 2026-07-01]``: correct at nought occurrences, and
        exactly the state ``ck_recurrence_rules_valid_window`` was drafted for
        and then HELD BACK on, because a CHECK cannot tell it from an owner's
        mistake and would turn a true-up into an unhandled ``CheckViolation``.

        The control above is the same loan one month earlier in its life and
        reaches ``ClosesOn`` on the identical closing date, so what this pins
        is the EMPTY test itself and not the retired mapping.

        **A loan that has not ORIGINATED cannot stand in for this**, and the
        first draft of this test used one: an unborrowed loan owes ``$0.00``
        and is emphatically NOT retired (its whole debt line is ahead of it),
        so it resolves to a live ``ClosesOn`` at its contractual payoff.  The
        loan must be borrowed AND cleared.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Retired Before It Fires",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=15,
            )
            loan_recurrence_sync.bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.terms.is_originated is True, (
                "precondition: the loan must be BORROWED, or it is not retired"
            )
            assert figures.is_retired is True, "precondition: it owes nothing"
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 7, 15), (
                "precondition: the first contractual installment is one month "
                f"after origination, got {rule.starts_on}"
            )

            assert loan_payment_window(tpl, ctx) == EMPTY

    def test_a_loan_RETIRING_ON_the_day_it_first_fires_is_NOT_empty(
        self, app, db, seed_user, seed_periods,
    ):
        """The boundary between the two shapes above: ``[D, D]`` is ONE occurrence.

        A loan originated 2026-06-01 with a ``payment_day`` of 1 owes its first
        installment 2026-07-01, which is the read pass's own now; retired, its
        window closes on that same day.  A window whose ends coincide is not
        empty -- it admits exactly the occurrence on that date, because
        ``ClosesOn`` is INCLUSIVE, and here that is the boundary being pinned
        rather than a claim about which installment cleared the loan: this
        loan was cleared by a true-up BEFORE its first installment ever fired,
        so the 2026-07-01 payment is emphatically not the one that paid it off.
        The inclusive comparison is what separates a window whose ends coincide
        from an empty one, and getting it wrong turns a loan's last payment
        into a loan that never had one.

        **This is the case the other two cannot see.** The control's closing
        date is a month past its first occurrence and the EMPTY case's is two
        weeks before, so ``closes < starts_on`` and ``closes <= starts_on``
        answer identically for both; only here do they differ, and getting it
        wrong turns a loan's last payment into a loan that never had one.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Retired On The Day",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 6, 1), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            loan_recurrence_sync.bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            ctx = self._ctx(seed_user)
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 7, 1), (
                "precondition: the first occurrence must fall ON the read "
                f"pass's as-of for this boundary to exist, got {rule.starts_on}"
            )
            assert balance_at.loan_figures(loan, ctx).is_retired is True

            window = loan_payment_window(tpl, ctx)

            assert window == ClosesOn(on=date(2026, 7, 1))
            assert window.admits(date(2026, 7, 1)) is True

    def test_the_EMPTY_test_uses_the_RESOLVED_first_occurrence_not_the_column(
        self, app, db, seed_user, seed_periods,
    ):
        """The `PERIOD`-unit case that REFUTED this step's first premise.

        The premise was that ``budget.recurrence_rules.starts_on`` IS the
        rule's first occurrence, so the EMPTY test could read the column.
        True for MONTH, WEEK and YEAR -- ``_authoring._author`` writes
        ``resolve``'s normalised date, and it was measured equal for all 43
        live rules on a production clone -- and FALSE for ``PERIOD``, which
        ``_resolution`` re-normalises on every read to the START of the
        paycheck covering the stored date.  Once a schedule edit leaves that
        date mid-period, the walk's first occurrence lands BEFORE the column
        (plan ledger row **D39**'s drift).

        This test puts the rule in exactly that state and asserts the window
        the WALK implies.  Reading the column answers `EMPTY` -- "this loan is
        finished" about a definition with a live occurrence -- which is what
        plan step R7d-f would then tell the owner.

        The two dates are read off the calendar rather than hardcoded, so the
        test states the RELATION (the resolved occurrence precedes the stored
        column, and the loan's life ends between them) rather than a pair of
        literals a schedule change would silently invalidate.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Paycheck-Cadence Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 2), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            rule = tpl.recurrence_rule
            db.session.flush()

            ctx = self._ctx(seed_user)
            calendar = ctx.calendar()
            # Drift the column INTO a period rather than onto its payday --
            # the state a pay-schedule edit leaves behind, which no door
            # writes but every schedule rebuild can produce.
            resolved_first = rule.starts_on
            drifted = resolved_first + timedelta(days=3)
            rule.starts_on = drifted
            db.session.flush()
            assert resolved_recurrence(rule, calendar).starts_on == (
                resolved_first
            ), (
                "precondition: the resolved first occurrence must precede the "
                f"stored column; got {resolved_recurrence(rule, calendar).starts_on} "
                f"against a column of {drifted}"
            )

            # A read pass BETWEEN the two: the loan's life covers the real
            # occurrence and stops before the column claims the rule starts.
            between = BalanceContext.build(
                seed_user["user"].id, resolved_first + timedelta(days=1),
            )
            figures = balance_at.loan_figures(loan, between)
            assert figures.is_retired is True, "precondition: it owes nothing"

            window = loan_payment_window(tpl, between)

            assert window == ClosesOn(
                on=resolved_first + timedelta(days=1),
            ), (
                "the window was decided against the stored column, so a "
                "definition with a live occurrence reads as finished"
            )
            assert window.admits(resolved_first) is True

    def test_a_definition_the_app_cannot_RESOLVE_still_answers(
        self, app, db, seed_user,
    ):
        """An owner with NO pay periods gets the conservative answer.

        ``resolved_recurrence`` returns ``None`` for an empty schedule -- the
        one refusal it swallows, because the Recurring surface renders every
        definition a user has and a whole page must not 500 for it.  Nothing
        generates for such an owner either way, so the honest answer is the one
        that does NOT claim the definition is finished.

        The loan and its definition are built while the ``seed_user``
        bootstrap period still exists -- the account factory anchors against
        it -- and the schedule is emptied afterwards, which is the order the
        state actually arises in.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="No Schedule Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 7, 1),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            db.session.flush()
            db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).delete(synchronize_session=False)
            db.session.flush()

            ctx = self._ctx(seed_user)
            assert not ctx.calendar().periods, (
                "precondition: this owner must have no pay periods"
            )

            window = loan_payment_window(tpl, ctx)

            assert not isinstance(window, Empty), (
                "an owner whose schedule does not exist yet was told their "
                "loan payment is finished"
            )

    def test_a_transfer_into_a_NON_loan_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """``None`` is "no loan bounds this", never a fourth window shape.

        A savings contribution has no contractual installment and no payoff, so
        nothing about a loan stops it and its own authored bound is the whole
        answer.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            assert loan_payment_window(tpl, self._ctx(seed_user)) is None

    def test_an_amortizing_account_without_params_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """A MORTGAGE-typed account whose loan details were never filled in.

        Reachable rather than hypothetical: an account classifies as amortizing
        by TYPE, so a transfer can settle into one that has no ``LoanParams``
        at all.  The seam answers ``None`` for it BEFORE its scenario guard,
        which is what this rests on.
        """
        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Unconfigured Mortgage",
            )
            db.session.flush()
            assert load_loan_params(acct.id) is None, (
                "precondition: this account must have NO LoanParams"
            )
            tpl = make_loan_payment_template(db.session, seed_user, acct)
            db.session.commit()

            assert loan_payment_window(tpl, self._ctx(seed_user)) is None

    def test_a_definition_that_does_not_repeat_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """No rule means no occurrences to bound.

        A one-time transfer into a loan is a single dated payment; there is no
        cadence for a window to narrow, and answering a shape for it would
        invite a reader to apply one.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            tpl.recurrence_rule = None
            db.session.commit()

            assert loan_payment_window(tpl, self._ctx(seed_user)) is None

    def test_a_transaction_template_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """A kind with no destination account answers ``None``, not raises.

        The readers plan steps R7d-d through R7d-f move over are kind-agnostic
        -- the Recurring surface and the two form helpers render transaction
        templates through the same calls -- so the resolver has to answer for a
        row that carries no ``to_account_id`` at all.
        """
        with app.app_context():
            tpl = make_expense_template(db.session, seed_user)
            db.session.commit()

            assert loan_payment_window(tpl, self._ctx(seed_user)) is None

    def test_resolving_a_SECOND_definition_on_one_pass_costs_NO_queries(
        self, app, db, seed_user, seed_periods,
    ):
        """The cost property plan step R7d-c rests on: the pass answers once.

        Generation walks every recurring definition an owner has, so the
        resolver is about to be asked once per definition.  Two things make
        that free after the first loan it touches, and neither is an accident:
        ``TransferTemplate.to_account`` is ``lazy="joined"``, so the
        destination arrives with the template rather than costing a lookup per
        definition; and the loan's resolution and its payoff are both memoized
        on the :class:`~app.services.balance_at.BalanceContext`, so a second
        definition into the SAME loan re-reads nothing.

        **The templates are loaded BEFORE the capture opens**, deliberately: a
        capture that straddles the load charges the subject its own eager
        loads, and the control would then measure the fixture rather than the
        resolver.

        Asserted as a MARGINAL cost of zero rather than against an absolute
        statement count, which would break on any unrelated query the seam
        adds and would say nothing about the property this pins.

        **BOTH axes are measured, because an adversarial review of this step
        pointed out that only one of them is the shape generation walks.**
        Several definitions into ONE loan is the state
        ``active_recurring_transfer_template``'s own docstring calls a
        misconfiguration; definitions across DIFFERENT loans is the ordinary
        one, and each new loan legitimately pays a full ``resolve_loan_bundle``.
        So the second loan below is asserted to COST something -- a control
        that measured zero there would be measuring a memo keyed on nothing --
        while a further definition into a loan already touched costs nothing.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            make_loan_payment_template(db.session, seed_user, loan)
            for index in (2, 3):
                extra = TransferTemplate(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=loan.id,
                    name=f"Into One Loan {index}",
                    default_amount=Decimal("50.00"),
                )
                db.session.add(extra)
                db.session.flush()
                make_every_period_rule(db.session, extra)
            db.session.commit()

            ctx = self._ctx(seed_user)
            # Staged OUTSIDE the capture: this is the query generation has
            # already run to find the definitions, and its eager loads are
            # its own cost rather than the resolver's.
            templates = (
                db.session.query(TransferTemplate)
                .filter_by(to_account_id=loan.id)
                .order_by(TransferTemplate.id)
                .all()
            )
            assert len(templates) == 3, "precondition: three definitions"
            # The first resolve warms the pass's loan resolution and payoff.
            first, first_statements = capture_sql_statements(
                lambda: loan_payment_window(templates[0], ctx),
            )
            rest, rest_statements = capture_sql_statements(
                lambda: [loan_payment_window(t, ctx) for t in templates[1:]],
            )

            assert isinstance(first, ClosesOn)
            assert rest == [first, first], (
                "every definition into one loan must resolve to ONE window"
            )
            assert len(rest_statements) == 0, (
                f"resolving two further definitions into the same loan cost "
                f"{len(rest_statements)} queries after a first that cost "
                f"{len(first_statements)}; the pass is re-reading the loan "
                "per definition, which generation would pay per template: "
                + "; ".join(text for text, _params in rest_statements[:5])
            )

            # The OTHER axis: a SECOND loan is a second subject, so it costs.
            other_loan = create_loan_account(
                seed_user, db.session, name="Second Window Loan",
                principal=Decimal("8000.00"), rate=Decimal("0.04000"),
                term=24, origination_date=date(2026, 7, 1),
            )
            other_tpl = make_loan_payment_template(
                db.session, seed_user, other_loan,
            )
            db.session.commit()
            staged = db.session.get(TransferTemplate, other_tpl.id)
            other, other_statements = capture_sql_statements(
                lambda: loan_payment_window(staged, ctx),
            )

            assert isinstance(other, ClosesOn)
            assert len(other_statements) > 0, (
                "a SECOND loan resolved for free, so this control is keyed on "
                "something that is not the loan and would read zero however "
                "many loans a generation pass walked"
            )

    def test_a_configured_loan_REFUSES_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-R30**: a producer needing a scenario refuses, never guesses.

        The early return this replaces left the last-written bound standing --
        and once plan step R7d-g stops writing the column there is nothing to
        stand, so the honest answer is the refusal every other producer needing
        a baseline already makes.  ONE application-level handler answers it
        (ruling **R-BW**), so no caller pre-checks.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None,
                as_of=date(2026, 7, 1),
            )
            with pytest.raises(BaselineMissingError):
                loan_payment_window(tpl, ctx)

    def test_a_NON_loan_still_resolves_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The not-a-loan answer is reached BEFORE the scenario guard.

        The other half of R-R30, and it is what keeps a savings or investment
        transfer's form rendering for an owner whose baseline is missing -- a
        broken invariant that must not take down every unrelated recurrence
        surface with it.  If the guard ever moves ahead of the loan test, this
        raises instead.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None,
                as_of=date(2026, 7, 1),
            )
            assert loan_payment_window(tpl, ctx) is None
