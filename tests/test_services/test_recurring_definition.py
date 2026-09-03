"""The composed read door: a definition's rule AND its destination's own stop.

Plan step **R7d-d**.  ``recurring_definition`` is the one place the two halves
of "when does this stop" meet -- what the owner authored, read by the pure
recurrence package, and what the destination allows, folded by
``loan_recurrence_sync``.  These grade the composition itself; the shapes are
graded in ``test_recurrence_describe`` and the resolver in
``test_loan_recurrence_sync``.

**The load-bearing claim is that the narrowing reaches the WALK**, not merely
the returned value.  A door that composed the two into a field nobody read
would look identical from the outside, so the tests below assert placements and
occurrence counts rather than only the ``Closing`` on the value.
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ForeignAccountError
from app.models.pay_period import PayPeriod
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    EMPTY,
    NEVER_ENDS,
    ClosesOn,
    EndsOnDate,
    RecurrenceResolutionError,
    describe,
    occurrence_placements,
    occurrences,
    reauthor_rule,
    recurrence_spec,
    resolved_recurrence,
)
from app.services.loan_recurrence_sync import bind_rule_to_loan
from app.services.recurring_definition import (
    read_definition,
    resolved_definition,
)
from tests.oracles.recurrence_baseline import MONTHLY
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_expense_template,
    make_loan_payment_template,
    make_transfer_template,
)

#: The day every pass in this module is measured at.  Frozen so the loan fold
#: and the derived payoff are deterministic.
_TODAY = date(2026, 7, 1)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today mid-loan so the projected schedule does not drift."""
    freeze_today(monkeypatch, _TODAY)


def _ctx(seed_user):
    """Return the read pass every composition here is measured against."""
    return BalanceContext.build(seed_user["user"].id, _TODAY)


def _restate_bound(rule, bound, ctx):
    """Re-author *rule* with a different closing bound, through the write door.

    ``reauthor_rule`` replaces a rule's WHOLE authored state, which is the
    package's partial-change idiom: read the spec, replace the one fact, write
    it back.  Reaching for the column directly would author a state the write
    door cannot produce, and the point of these tests is what a real stored
    bound does.

    Args:
        rule: The rule to re-author.
        bound: Its new closing bound.
        ctx: The read pass, for the owner's calendar.
    """
    reauthor_rule(
        rule, replace(recurrence_spec(rule), end_bound=bound), ctx.calendar(),
    )


def _loan(seed_user, db_session, **kwargs):
    """Return a 24-month $12,000 loan at 5%, originating today by default."""
    defaults = {
        "name": "Door Loan",
        "principal": Decimal("12000.00"),
        "rate": Decimal("0.05000"),
        "term": 24,
        "origination_date": _TODAY,
    }
    defaults.update(kwargs)
    return create_loan_account(seed_user, db_session, **defaults)


class TestWhatTheDoorComposes:
    """The derived stop reaches the resolved value, or is honestly absent."""

    def test_a_definition_with_no_rule_does_not_repeat(
        self, app, db, seed_user, seed_periods,
    ):
        """``None`` means "does not repeat", passed through unchanged.

        The door must not invent a reading for a one-off charge: the absence
        of a ``budget.recurrence_rules`` row naming the definition IS how a
        definition says it does not repeat.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            tpl.recurrence_rule = None
            db.session.commit()

            ctx = _ctx(seed_user)

            assert resolved_definition(tpl, ctx) is None
            reading = read_definition(tpl, ctx)
            assert reading.resolved is None
            assert reading.placements == ()

    def test_a_destination_that_is_not_a_loan_carries_NO_derived_stop(
        self, app, db, seed_user, seed_periods,
    ):
        """The 41-of-46 case, and ``None`` here is an answer rather than a gap.

        A transfer into a savings account has no derived stop, so its closing
        holds the authored bound alone -- which is what makes "this step
        changes no rendered character for a non-loan row" a property the suite
        holds rather than a claim the commit body makes.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            resolved = resolved_definition(tpl, _ctx(seed_user))

            assert resolved.closing.derived is None
            assert resolved.closing.authored == NEVER_ENDS

    def test_an_expense_template_pays_into_no_account_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``TransactionTemplate`` has no destination, so it has no stop.

        The door is kind-agnostic on purpose: the Recurring surface hands it
        income, expense and transfer definitions from one loop, and a producer
        that raised on two of the three would push the branch back up into the
        surface it exists to keep simple.
        """
        with app.app_context():
            tpl = make_expense_template(db.session, seed_user)
            db.session.commit()

            resolved = resolved_definition(tpl, _ctx(seed_user))

            assert resolved.closing.derived is None

    def test_a_LOAN_payment_carries_the_loans_derived_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The whole point: the stop is ASKED for, never read off a column.

        Asserted against the seam's own ``payoff_date`` as well as against the
        date, so it cannot pass by agreeing with a constant the seam has since
        moved away from.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = _ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date == date(2028, 7, 1)

            resolved = resolved_definition(tpl, ctx)

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))

    def test_the_stored_column_is_NOT_what_the_door_reads(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row **D35**, made unconstructible for this reader.

        The column is deliberately falsified to a date the loan's own fold
        does not name.  Before this step the surface read that value; the door
        asks the loan instead, so the falsified column reaches only the
        AUTHORED half -- where it belongs, since a stored bound is what an
        owner authors.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            stale = date(2027, 1, 1)
            _restate_bound(rule, EndsOnDate(on=stale), _ctx(seed_user))
            db.session.commit()

            resolved = resolved_definition(tpl, _ctx(seed_user))

            assert rule.end_date == stale, "precondition: the column is stale"
            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))
            assert resolved.closing.authored == EndsOnDate(on=stale)


class TestTheNarrowingReachesTheWalk:
    """A composed value nobody walks under would look identical from outside."""

    def test_the_loans_life_TRUNCATES_the_occurrences(
        self, app, db, seed_user, seed_periods,
    ):
        """The rule names occurrences past the payoff; the walk stops at it.

        The rule is authored unbounded, so nothing but the loan can stop it --
        and every occurrence the pure walk emits past 2028-07-01 is a payment
        the app would project against a debt that is gone.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)
            calendar = ctx.calendar()
            # PAST the saved horizon deliberately.  ``occurrence_placements``
            # walks only as far as the schedule reaches (2026-05-21 for this
            # fixture) and the payoff is two years beyond it, so a placement
            # count could not see the truncation at all -- the walk is asked
            # directly, with a window that spans the payoff.
            beyond = date(2030, 1, 1)
            payoff = date(2028, 7, 1)

            unnarrowed = list(occurrences(
                resolved_recurrence(tpl.recurrence_rule, calendar),
                calendar, through=beyond,
            ))
            narrowed = list(occurrences(
                resolved_definition(tpl, ctx), calendar, through=beyond,
            ))

            assert narrowed, "precondition: the definition fires at all"
            assert len(narrowed) < len(unnarrowed), (
                "the derived stop reached the value but not the walk"
            )
            assert max(narrowed) <= payoff
            assert max(unnarrowed) > payoff, (
                "precondition: the rule's own bound does not stop it here, so "
                "the loan is the only thing that can"
            )

    def test_an_EARLIER_authored_bound_still_binds(
        self, app, db, seed_user, seed_periods,
    ):
        """ANDed, never substituted -- the direction that costs money.

        A closing bound the owner authored is a real input: a rule that
        ignored it in favour of the loan's payoff would model cash the owner
        has said will stop moving.  The authored date here precedes the
        payoff, so it is the one that must bind.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            authored = date(2027, 3, 1)
            _restate_bound(
                tpl.recurrence_rule, EndsOnDate(on=authored), _ctx(seed_user),
            )
            db.session.commit()

            ctx = _ctx(seed_user)
            reading = read_definition(tpl, ctx)

            assert reading.placements, "precondition: it fires at all"
            assert max(p.occurrence for p in reading.placements) <= authored
            assert describe(reading.resolved).stops == "until Mar 01, 2027"

    def test_a_loan_that_closed_before_its_first_firing_names_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):
        """The EMPTY window, and the cell that must not name its date.

        Originated 2026-06-20 with a ``payment_day`` of 15, so the first
        installment is 2026-07-15; trued to zero it retires before the
        definition ever fires, and the window ``[2026-07-15, 2026-07-01]``
        is correct at nought occurrences.
        """
        with app.app_context():
            loan = _loan(
                seed_user, db.session, name="Closed First",
                origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=15,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            reading = read_definition(tpl, _ctx(seed_user))

            assert reading.resolved.closing.derived == EMPTY
            assert reading.placements == ()
            assert describe(reading.resolved).stops == "never runs"


class TestWhatTheDoorRefuses:
    """The refusals it inherits, and the ORDER it inherits them in."""

    def test_an_owner_with_no_pay_periods_reads_as_not_repeating(
        self, app, db, seed_user,
    ):
        """The one refusal ``resolved_recurrence`` swallows, passed through.

        The Recurring surface renders every definition a user has, and taking
        a whole page to a 500 for a schedule state no rule of this rule's is
        wrong about would be a fence rather than a fix.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).delete(synchronize_session=False)
            db.session.flush()

            ctx = _ctx(seed_user)
            assert not ctx.calendar().periods

            assert resolved_definition(tpl, ctx) is None
            assert read_definition(tpl, ctx).placements == ()

    def test_a_cross_owner_pairing_is_REFUSED_before_the_loan_is_folded(
        self, app, db, seed_user, second_user, seed_periods,
    ):
        """The order inside the door is load-bearing, not incidental.

        ``resolved_recurrence`` refuses a rule paired with another owner's
        calendar; ``loan_payment_window`` does NOT refuse the same pairing --
        its own docstring records that it produces a plausible BLENDED answer,
        because the loan bundle scopes its payment feed by the PASS's scenario
        and its standing payment by the ACCOUNT's owner.  So resolving FIRST
        is what turns a blended figure into a refusal.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            foreign = BalanceContext.build(second_user["user"].id, _TODAY)

            with pytest.raises(
                (RecurrenceResolutionError, ForeignAccountError),
            ):
                resolved_definition(tpl, foreign)


class TestTheDoorAgreesWithItsOwnParts:
    """The composition equals its pieces, so neither can drift alone."""

    def test_the_reading_places_exactly_what_the_walk_places(
        self, app, db, seed_user, seed_periods,
    ):
        """``read_definition`` IS ``resolved_definition`` plus the walk.

        Stated as an equality rather than trusted, because the two entry
        points are what the Recurring surface's active sections and its
        archived drawer take respectively -- and a drawer describing a
        different narrowing from the list beside it is exactly the
        one-row-disagreeing-with-itself shape this step removes.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)

            resolved = resolved_definition(tpl, ctx)
            reading = read_definition(tpl, ctx)

            assert reading.resolved == resolved
            assert reading.placements == occurrence_placements(
                resolved, ctx.calendar(),
            )

    def test_the_authored_half_is_the_rules_own_stored_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The door narrows; it does not re-author.

        Reading the column twice -- once for the authored half and once to
        rebuild it -- would be the second spelling this arc exists to delete,
        so the authored bound is carried across from the value the pure
        resolver already built.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            _restate_bound(
                tpl.recurrence_rule,
                EndsOnDate(on=date(2027, 3, 1)),
                _ctx(seed_user),
            )
            db.session.commit()

            resolved = resolved_definition(tpl, _ctx(seed_user))

            assert resolved.closing.authored == recurrence_spec(
                tpl.recurrence_rule,
            ).end_bound


class TestTheDerivedStopIsMeasuredInTheCallersPass:
    """One pass in, one answer out -- the read clock is never this module's."""

