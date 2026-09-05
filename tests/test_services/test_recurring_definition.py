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
# Imported as a MODULE so the resolve-once control below patches the name the
# read door resolves at CALL time; patching this file's imported name would
# leave the composition calling the real one.
from app.services.recurrence import _reading
from app.services.loan_recurrence_sync import (
    bind_rule_to_loan,
    is_standing_loan_payment,
)
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


def _second_transfer_into(seed_user, db_session, loan):
    """Return ``(first, second)``: two recurring transfers paying *loan*.

    The FIRST is the definition the app bounds -- ``is_standing_loan_payment``
    names the account's active recurring transfer, tie-broken on id -- so its column
    is the chokepoints' cache and the door reads it as such (ruling **R-R56**).
    The SECOND's column is not written by the app while the first is active:
    whatever bound it carries is the owner's word, which makes it the subject
    for every case about an AUTHORED bound.  (Archive the first and the second
    is promoted; the next chokepoint then writes its column and the door reads
    it as the cache -- the same fact from both sides.)  The first is renamed
    before the second is built because the helper names a payment after its
    loan and the pair is unique per owner.

    Args:
        seed_user: The owner.
        db_session: The test session.
        loan: The loan both transfers pay into.

    Returns:
        ``(first, second)``, flushed and not yet committed.
    """
    first = make_loan_payment_template(db_session, seed_user, loan)
    first.name = f"App-bounded payment {loan.id}"
    db_session.flush()
    second = make_loan_payment_template(db_session, seed_user, loan)
    return first, second


class TestWhatTheDoorComposes:
    """The derived stop reaches the resolved value, or is honestly absent."""

    def test_a_definition_with_no_rule_does_not_repeat(
        self, app, db, seed_user, seed_periods,
    ):
        """``None`` means "does not repeat", passed through unchanged.

        The door must not invent a reading for a one-off charge: the absence
        of a ``budget.recurrence_rules`` row naming the definition IS how a
        definition says it does not repeat.

        **The definition pays into a LOAN, deliberately.**  Since plan step
        R7d-d the resolver takes the resolved recurrence, and a one-time
        transfer into a loan is the state in which there is none to hand it --
        so this is where a door that asked the loan before checking the rule
        would fail, and a savings transfer could not show that.  (The
        resolver's own case for this state moved here with that step.)
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
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
        does not name.  The derived half comes from the loan and never from the
        column; and for the loan payment the app itself bounds, the column is
        the chokepoints' CACHE of that payoff rather than the owner's word, so
        the door reads it as no authored bound at all (ruling **R-R56**,
        developer, 2026-09-04).  The falsified value therefore reaches NEITHER
        half.  Until that ruling the stale date sat in the authored half and,
        being EARLIER, still bound -- which an adversarial review of this step
        measured against D35's own shape (``2029-01-22`` stored against
        ``2029-02-22`` derived).
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
            assert is_standing_loan_payment(tpl, _ctx(seed_user)), (
                "precondition: this is the definition whose bound the app writes"
            )
            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))
            assert resolved.closing.authored == NEVER_ENDS

    def test_an_app_written_stored_bound_is_read_as_the_cache_it_is(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-R56**: a stale EARLIER cache no longer binds the phrase or the walk.

        For a loan payment the ``end_date`` column is not the owner's word --
        the form locks the Ends control and ten chokepoints write the loan's
        derived payoff into it -- and the composed value cannot tell a cached
        date from an authored one: it ANDs the two
        (:meth:`~app.services.recurrence.Closing.admits`, and the ``min`` in
        ``_describe._derived_closes_on``).  So until this ruling, where the
        cache was EARLIER than the loan's closing date -- plan ledger row
        **D35**'s measured shape -- the cell named the cached date and the walk
        stopped there, exactly as before plan step R7d-d.  The door now
        composes ``NEVER_ENDS`` for the definition the app bounds, so the
        derived stop is the whole answer: the phrase names the payoff and the
        walk runs to it.  Plan step R7d-g deletes the column and this arm.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            stale = date(2027, 1, 1)
            _restate_bound(
                tpl.recurrence_rule, EndsOnDate(on=stale), _ctx(seed_user),
            )
            db.session.commit()

            ctx = _ctx(seed_user)
            resolved = resolved_definition(tpl, ctx)
            narrowed = list(occurrences(
                resolved, ctx.calendar(), through=date(2030, 1, 1),
            ))

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1)), (
                "precondition: the loan's own stop is LATER than the cache"
            )
            assert describe(resolved).stops == "until Jul 01, 2028"
            assert narrowed, "precondition: it fires at all"
            assert max(narrowed) > stale, (
                "the cached column bound the walk; the door read it as authored"
            )
            assert max(narrowed) <= date(2028, 7, 1)


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

        **The bound is authored on a SECOND transfer into the loan.**  The
        first recurring transfer into a loan is the one the app bounds -- its
        column is the chokepoints' cache and the door reads it as such (ruling
        **R-R56**) -- so a bound restated on it would be read as the cache and
        this case could not fire.  The second transfer's column is never
        written by the app; what it holds IS the owner's word, and that is the
        definition whose authored bound must still bind.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            first, tpl = _second_transfer_into(seed_user, db.session, loan)
            db.session.commit()
            authored = date(2027, 3, 1)
            _restate_bound(
                tpl.recurrence_rule, EndsOnDate(on=authored), _ctx(seed_user),
            )
            db.session.commit()
            ctx = _ctx(seed_user)
            assert is_standing_loan_payment(first, ctx), (
                "precondition: the app bounds the first transfer"
            )
            assert not is_standing_loan_payment(tpl, ctx), (
                "precondition: the app does not bound this one"
            )

            resolved = resolved_definition(tpl, ctx)
            # Walked PAST the fixture's horizon, as the truncation case above
            # is.  ``read_definition``'s placements reach only as far as the
            # schedule does (2026-05-21), which precedes the authored date by
            # 21 months -- so "every placement is on or before 2027-03-01"
            # held with NO bound at all, and a first draft of this test
            # asserted exactly that.  An adversarial review of the step caught
            # it; the walk is asked directly, through a window that spans both
            # stops.
            beyond = date(2030, 1, 1)
            narrowed = list(occurrences(resolved, ctx.calendar(), through=beyond))

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1)), (
                "precondition: the loan alone would run 16 months past the "
                "authored date, so only the authored bound can stop the walk "
                "at it"
            )
            assert narrowed, "precondition: it fires at all"
            assert max(narrowed) <= authored
            assert max(narrowed) >= authored - timedelta(days=31), (
                f"the walk stopped at {max(narrowed)}, well short of the "
                f"authored {authored}, so something other than that bound -- a "
                f"horizon -- ended it"
            )
            assert describe(resolved).stops == "until Mar 01, 2027"

    def test_a_loan_that_closed_before_its_first_firing_names_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):
        """The EMPTY window, and the cell that must not name its date.

        Originated 2026-06-20 with a ``payment_day`` of 15, so the first
        installment is 2026-07-15; trued to zero the day after origination
        (the helper's default) it retires on 2026-06-21, before the definition
        ever fires, and the window ``[2026-07-15, 2026-06-21]`` is correct at
        nought occurrences.  The closing date is the day the loan was CLEARED,
        not the read pass's now (plan step ``recurrence:R7d-h``), so the
        window is stable across read dates.
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
            ctx = _ctx(seed_user)
            assert balance_at.loan_figures(loan, ctx).closing_date == (
                date(2026, 6, 21)
            ), "precondition: the loan closed the day after it originated"

            reading = read_definition(tpl, ctx)

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

        **The resolver never meets this state since plan step R7d-d.**  It
        takes the resolved recurrence, and an owner with no pay periods has
        none to hand it -- so the door answers ``None`` before the loan is
        asked, nothing says "finished" about the definition, and the
        ``rule.starts_on`` fallback the resolver used to carry for this state is
        deleted rather than kept for a caller that cannot reach it.  (What the
        Recurring surface does with that ``None`` for a rule-bearing definition
        is RAISE, as a broken invariant; ``test_recurring_view`` holds that.)

        **The definition pays into a LOAN, deliberately**, built while the
        bootstrap period still exists and the schedule emptied afterwards --
        the order the state arises in.  With a loan behind it this is the state
        where a door that handed ``resolved=None`` down would fail at
        ``resolved.starts_on``; the resolver's own case for it moved here.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session, name="No Schedule Loan")
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
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
        """The order inside the door is graded, not merely described.

        ``resolved_recurrence`` refuses a rule paired with another owner's
        calendar with the rule's own exception.  The read pass would refuse the
        foreign loan too (``ForeignAccountError`` from ``_memoize_once``, plan
        step X-i4), but only after loading the account -- so the door resolves
        first, and this asserts the RULE's refusal alone.  Accepting either
        exception, as a first draft did, would have passed with the two calls
        reversed.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            foreign = BalanceContext.build(second_user["user"].id, _TODAY)

            with pytest.raises(RecurrenceResolutionError):
                resolved_definition(tpl, foreign)


class TestTheDoorResolvesTheRuleOnce:
    """One rule, one resolution per pass -- ``CLAUDE.md`` rule 14's ONE WALK."""

    def test_a_loan_payment_resolves_its_rule_exactly_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The resolver TAKES the resolved recurrence; it does not re-derive it.

        Its EMPTY comparison needs the definition's first occurrence, and the
        first build of this step had ``loan_payment_window`` resolve the rule
        again on its own to get one -- the same rule, twice, on one pass.  The
        door now hands down the value it already built.  Counted at the
        definition site (``_reading.resolve``), and the control is shown to
        fire: the composed value carries the loan's stop, so the door did run.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)

            calls = []
            real_resolve = _reading.resolve

            def counting_resolve(spec, calendar):
                calls.append(spec.unit)
                return real_resolve(spec, calendar)

            monkeypatch.setattr(_reading, "resolve", counting_resolve)

            resolved = resolved_definition(tpl, ctx)

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))
            assert len(calls) == 1, (
                f"one loan payment resolved its rule {len(calls)} times on "
                f"one pass"
            )


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
        resolver already built.  Asserted on a SECOND transfer into the loan,
        whose column the app never writes: for the first, ruling **R-R56** has
        the door read the column as the cache and compose ``NEVER_ENDS``
        instead, which the case above it holds.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _first, tpl = _second_transfer_into(seed_user, db.session, loan)
            db.session.commit()
            _restate_bound(
                tpl.recurrence_rule,
                EndsOnDate(on=date(2027, 3, 1)),
                _ctx(seed_user),
            )
            db.session.commit()
            ctx = _ctx(seed_user)
            assert not is_standing_loan_payment(tpl, ctx), (
                "precondition: the app does not write this transfer's bound"
            )

            resolved = resolved_definition(tpl, ctx)

            assert resolved.closing.authored == recurrence_spec(
                tpl.recurrence_rule,
            ).end_bound
            assert resolved.closing.authored == EndsOnDate(on=date(2027, 3, 1))


class TestTheDerivedStopIsMeasuredInTheCallersPass:
    """One pass in, one answer out -- the read clock is never this module's."""

    def test_the_passes_as_of_selects_which_crossing_answers(
        self, app, db, seed_user, seed_periods,
    ):
        """Two passes over ONE loan answer two stops, and the pass decides which.

        The loan originates 2026-05-01 and is trued to ``$0.00`` on 2026-06-15.
        Read as of 2026-06-10 it still owes, so the derived stop is the
        FORWARD crossing, a payoff after the true-up day; read as of ``_TODAY``
        (2026-07-01) it is retired, so the stop is the day it LAST became
        closed (plan step ``recurrence:R7d-h``).  The door reads neither the
        clock nor a calendar of its own: both answers come off the pass it was
        handed, which is what the two DIFFERING shows -- a door that built its
        own pass or read ``date.today()`` would answer the frozen day's stop
        for both.
        """
        with app.app_context():
            loan = _loan(
                seed_user, db.session, name="Two Passes",
                origination_date=date(2026, 5, 1), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=date(2026, 6, 15),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()
            owner = seed_user["user"].id
            before = BalanceContext.build(owner, date(2026, 6, 10))
            after = BalanceContext.build(owner, _TODAY)

            still_owing = resolved_definition(tpl, before).closing.derived
            retired = resolved_definition(tpl, after).closing.derived

            assert retired == ClosesOn(on=date(2026, 6, 15))
            assert isinstance(still_owing, ClosesOn)
            assert still_owing.on > date(2026, 6, 15), (
                f"read before the true-up the loan still owes, so its stop is "
                f"a forward payoff, not {still_owing.on}"
            )

