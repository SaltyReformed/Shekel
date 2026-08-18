"""Architecture test: what ONE HTTP render opens, and what it runs twice.

Plan steps **C2-f2d-1** (ledger row **P43**) and **C2-f2d-2** (row **P57**) --
two counts of the same shape, kept in one file because they share one seeding
fixture and one counting discipline, and because the question is the same
question at two levels: what does a render do more than once that it should do
exactly once?

What a read pass is
-------------------

:class:`app.services.balance_at.BalanceContext` pins the three facts every
money answer on a page depends on -- the owner, the baseline scenario, and the
day -- and memoizes what is expensive to derive from them: each loan's ledger
walk, and the owner's pay calendar.  It exists so a render answers those
questions once.

What went wrong, and what it cost
---------------------------------

Nothing enforced that.  Any producer holding a ``user_id`` could call
``BalanceContext.build`` and start a SECOND pass, reading the clock again, and
three did: ``retirement_projection.load_projection_batch`` (a leaf),
``retirement_dashboard_service.compute_gap_data`` and
``retirement_levers.compute_lever_data`` (each via that leaf).  Measured on a
production clone before this leaf: ``/savings`` opened 2 passes and derived the
pay calendar 3 times; ``/retirement`` opened 2; ``/retirement/readiness`` with
a what-if and a lever stepper opened 3.

Two figures published on ONE screen from two passes are two figures computed
against two clocks.  Reproduced on that clone by pinning each pass to a day
either side of a payday: the verdict card's after-tax projected savings read
``$836,398.65`` while the levers card beside it read ``$836,402.83`` -- ``$4.18``
apart.  The two projection AXES differed by one period (517 against 516), which
is not merely a countdown: the contribution lever's annuity factor folds over
the baseline probe's axis, so a one-period difference changes the extra
per-period contribution the page tells the owner to make.  The figures were not
wrong; they contradicted each other, and nothing in the application could
notice.

Why this test is BEHAVIORAL, not a name list
--------------------------------------------

The obvious gate is "these modules may not call ``BalanceContext.build``", and
as a NAME LIST it is the wrong one: it protects exactly the modules someone
remembered to list.  Counting the passes an actual RENDER opens has no list in
it, so a producer wired into one of these routes is covered the day it is wired
in.

**The gate that WOULD be structural is a layer predicate** -- "no module under
``app/services/**`` calls ``BalanceContext.build``", the pass built at the HTTP
boundary and nowhere below it (adversarial design review, 2026-08-16).  That is
not a name list and it is the right end state; **eight service modules stand
between here and it**, measured with ``grep -rl "BalanceContext\\.build("
app/services/`` -- the trailing paren matters, because the bare name also
matches the docstring in ``retirement_projection`` that records the call this
leaf DELETED, and reports nine.  They are ``calendar_service``,
``dashboard_service._pulse``, ``dashboard_service``,
``investment_dashboard_service/_context``,
``investment_dashboard_service/_orchestrator``, ``loan_recurrence_sync``,
``savings_dashboard_service/_orchestrator`` and ``tax_report_service``.  The
seventh is one of THESE two pages -- it is ``/savings``'s own door, left
deliberately (see ``_orchestrator._pass_for``) and owned by ``C2-f2d-3``.
Ledger row **P56** carries the layer predicate.  Asserting it here today would fail on
eight modules this leaf does not touch, so the gate is per-render and grows a
render at a time.

**What this file therefore does NOT prove.** It counts read passes, not clock
reads: ``compute_pension_summary``, ``compute_gap_net_biweekly`` and
``build_employer_salary_basis`` each still call ``date.today()`` for themselves
on the very renders below, so these can be green while two cards disagree
across a New Year boundary.  Ledger row **P55** owns that.

One LOAD, and one derivation per plan (C2-f2d-2)
-----------------------------------------------

Sharing the pass left the render loading everything TWICE and deriving the
readiness picture twice.  ``retirement_dashboard_service.compute_gap_data``
computed that picture at the stored retirement date, and
``retirement_levers._probe`` computed it at a shifted one; on a default page
load the lever solver's month-0 probe recomputed, from its own 46 queries,
exactly what the readiness hero had already drawn.  Measured on a production
clone: one page render issued 179 queries of which 86 -- 48% -- were the second
copy, and the two derivations agreed (funded ratio ``0.7463``, required
``$1,120,707.00``, projected after tax ``$836,398.65``) as they had every day
until one of them was edited.

``TestOneLoadPerRender`` counts what a render RUNS rather than what it opens.
Its subject is the four producers that were duplicated -- the gap inputs, the
projection context, the projection batch, and the per-account walk -- and the
walk is the interesting one: its count is NOT one, because the retire-later
lever legitimately probes many candidate retirement dates.  What the assertion
says is that the count equals the number of DISTINCT plans the render asked
about, which is the honest form of "nothing is derived twice".

Test IDs
--------

- ``test_retirement_render_opens_one_read_pass``
- ``test_savings_render_opens_one_read_pass``
- ``test_readiness_whatif_fragment_opens_one_read_pass``
- ``test_the_counter_sees_a_second_pass`` (negative control: the counter
  must be able to fail, or the three above grade nothing)
- ``test_retirement_render_loads_each_input_once``
- ``test_the_page_derives_no_plan_twice``
- ``test_the_load_counter_sees_a_second_load`` (negative control, same argument)
"""

from datetime import date
from decimal import Decimal

from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    counting_calls,
    counting_read_passes,
    make_investment_account,
    make_salary_profile,
)

#: The producers plan step C2-f2d-2 collapsed, as ``(module path, attribute)``.
#: The three LOADS ran twice per page render and three times per fragment; the
#: WALK ran once more than the render had distinct plans to ask about.
_ONCE_PER_RENDER = (
    ("app.services.retirement_dashboard_service", "load_gap_inputs"),
    ("app.services.retirement_projection", "build_projection_context"),
    ("app.services.retirement_projection", "load_projection_batch"),
)
_PER_PLAN = ("app.services.retirement_projection", "project_accounts_with_batch")

#: The one database door every pay-calendar derivation goes through.
#: A read pass MEMOIZES the calendar, so "one pass" and "one derivation" ought
#: to be the same claim -- and they were not.  Measured across pay-calendar plan
#: step C2-f2d-3: ``/savings`` opened ONE pass and derived the owner's schedule
#: SEVEN times, ``/retirement`` SIX, growing with the owner's investment-account
#: count, because two producers reached ``income_service`` through call chains
#: that held a pass and did not hand it over.  The pass counter beside this one
#: read 1 throughout.  Two counts of one question, and only one of them could
#: see it.
_CALENDAR_DOOR = ("app.services.pay_calendar._loader", "calendar_for")


def _seed_projecting_account(db, seed_user, seed_periods_today):
    """Give the owner a 401(k) AND a retirement date, so both engines run.

    Necessary for ``/savings`` and not merely tidy: its Horizon skips the
    /retirement engine entirely -- returning zero bands before it builds a
    projection context at all -- when the owner has no retirement or investment
    account.  A count taken on the bare fixture would therefore read ``1`` on
    the tree this leaf FIXED and on the tree it fixed it FROM, which is the
    arm-that-cannot-fail shape ``docs/plans/lessons.md`` records.

    ``/retirement`` does not need it (both its producers load a projection
    batch before any early return), and it is seeded there anyway so the two
    counts are taken over the same state.

    **The retirement DATE is load-bearing and was missing until 2026-08-16.**
    Without one, ``compute_lever_data`` short-circuits on its ``no_horizon``
    arm before deriving anything, so the retire-later search never runs -- and
    a planted defect that made the lever solver bypass the picture memo
    entirely was measured to pass ``test_the_page_derives_no_plan_twice``
    unchanged.  The gate was grading a render whose expensive half never
    executed.  A horizon far enough out that the plan is NOT already funded
    keeps the binary search bisecting rather than answering at offset 0.

    **It varies on ONE axis, and that bounds what the counts above cover**
    (adversarial design review, 2026-08-16).  A future producer reached only by
    an owner who has a loan, an active goal, a credit card or a current period
    sits behind an early return on this fixture, so its pass would go uncounted.
    Widening the owner is the honest way to widen the gate; the claim these
    tests make is over the state seeded HERE, not over every owner.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.user import UserSettings
    from app.utils.dates import add_months

    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=seed_user["user"].id)
        .one()
    )
    settings.planned_retirement_date = add_months(date.today(), 240)
    # **The SALARY PROFILE is load-bearing and was missing until 2026-08-16.**
    # ``income_service.get_current_gross_biweekly`` returns at its profile
    # lookup for an owner who has none, and every producer that reaches it does
    # so behind that early return -- so a calendar count taken without a
    # profile read the same number on the tree that derived the owner's
    # schedule SEVEN times and on the tree that derives it once.  That is the
    # arm-that-cannot-fail shape one line up, found the same way: by an
    # adversarial review re-running the measurement over a richer owner.
    make_salary_profile(seed_user, db.session)
    account = make_investment_account(
        seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
    )
    db.session.commit()
    return account


class TestOneReadPassPerRender:
    """Every render below opens exactly one :class:`BalanceContext`."""

    def test_retirement_render_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement opens ONE pass, though it runs two producers.

        The route computes the readiness verdict
        (``retirement_dashboard_service.compute_gap_data``) and the two levers
        (``retirement_levers.compute_lever_data``).  Each opened a pass of its
        own before plan step C2-f2d-1, which is what let the verdict card and
        the lever card be computed against different days.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"/retirement opened {counter['n']} read passes; the route builds "
            "one and every producer below it must take that one"
        )

    def test_savings_render_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings opens ONE pass, though its Horizon reuses the engine.

        The Horizon range projects the retirement and investment bands through
        the /retirement engine verbatim -- deliberately, so the band is the
        engine's own projection rather than a parallel model -- and that engine
        built a second pass until plan step C2-f2d-1.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"/savings opened {counter['n']} read passes; the Horizon's "
            "engine reuse must run in the page's own pass"
        )

    def test_readiness_whatif_fragment_opens_one_read_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The what-if fragment opens ONE pass for three computations.

        It publishes the stored-settings baseline, the override, AND the lever
        outcome -- three pictures, three passes before this leaf.  The panel's
        whole product is the DELTA between the first two, which is the
        override's effect only if nothing else about the two reads differs; a
        second clock read is something else differing.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as counter:
            resp = auth_client.get(
                "/retirement/readiness"
                "?merit_raise_horizon_years=7&months=24",
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        assert counter["n"] == 1, (
            f"the readiness fragment opened {counter['n']} read passes; the "
            "baseline, the what-if and the levers share the route's one"
        )

    def test_the_counter_sees_a_second_pass(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: the counter must be able to report a failure.

        The three assertions above are worth exactly as much as this one: a
        counter that never incremented would pass every one of them, on this
        tree and on the tree that opened two passes per render alike.

        Stated as a DIFFERENCE rather than against a literal, so it grades the
        instrument and only the instrument.  A hardcoded expectation here would
        be the render's own cost restated -- it would move whenever the counts
        above moved, and a control that fails for the same reason as the thing
        it controls is not a control.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_read_passes() as render_only:
            resp = auth_client.get("/retirement")
        assert resp.status_code == 200

        with counting_read_passes() as render_plus_one:
            assert auth_client.get("/retirement").status_code == 200
            with app.app_context():
                BalanceContext.build(seed_user["user"].id)

        assert render_plus_one["n"] == render_only["n"] + 1, (
            "the read-pass counter did not see a deliberately opened second "
            "pass, so the assertions beside it grade nothing"
        )


class TestOneLoadPerRender:
    """A render loads its inputs once and derives each plan once.

    Plan step **C2-f2d-2**, ledger row **P57**.
    """

    def test_retirement_render_loads_each_input_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement runs each loader exactly once.

        Two producers, one load.  Before this step the verdict producer and the
        lever solver each ran the whole set: the gap inputs (a settings, a
        pension and a salary query plus a full paycheck-engine run), the account
        query behind the projection context, and the batch (deductions,
        contributions, params, and a balance fold per account).
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(*_ONCE_PER_RENDER) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts == {name: 1 for _, name in _ONCE_PER_RENDER}, (
            f"/retirement ran its loaders {counts}; the route loads once and "
            "every producer below it takes what it was handed"
        )

    def test_the_page_derives_no_plan_twice(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The projection walk runs once per DISTINCT plan, never twice for one.

        The count is not 1 and must not be asserted as 1: the retire-later
        lever bisects over real candidate retirement dates and every one of
        them is a different plan that has to be projected.  What this pins is
        that no plan is projected twice -- so the number of walks equals the
        number of distinct :class:`~app.services.retirement_plan.PlanPoint`
        values the render asked about, which the memo makes observable.

        Asserting the walk count against the memo's SIZE rather than against a
        literal is deliberate: a literal would be the binary search's own step
        count restated, and it would have to be edited every time the seeded
        scenario's funding changed -- which is how a gate stops grading its
        subject and starts grading its fixture.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        # pylint: disable=import-outside-toplevel
        from app.services import retirement_levers, retirement_plan, retirement_readiness

        with app.app_context():
            inputs = retirement_plan.load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            with counting_calls(_PER_PLAN) as counts:
                picture = retirement_plan.picture_at(
                    inputs, inputs.stored_plan,
                )
                retirement_readiness.readiness_from_picture(picture)
                retirement_levers.compute_lever_data(inputs)

        walks = counts["project_accounts_with_batch"]
        assert walks == len(inputs.picture_memo), (
            f"the render walked the projection {walks} times for "
            f"{len(inputs.picture_memo)} distinct plans; a plan derived twice "
            "is two independent answers to one question"
        )
        # And the readiness hero's plan is ONE of them, not a private extra:
        # the lever card's month-0 probe is that same object.
        assert inputs.picture_memo[inputs.stored_plan] is picture

    def test_the_load_counter_sees_a_second_load(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: the load counter must be able to report a failure.

        Same argument as :meth:`TestOneReadPassPerRender
        .test_the_counter_sees_a_second_pass`: a counter that never incremented
        would pass both assertions above, on this tree and on the tree that ran
        every loader twice alike.  Stated as a DIFFERENCE so it grades the
        instrument and not the render's cost.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        # pylint: disable=import-outside-toplevel
        from app.services import retirement_dashboard_service

        with counting_calls(*_ONCE_PER_RENDER) as render_only:
            assert auth_client.get("/retirement").status_code == 200
        baseline = render_only["load_gap_inputs"]

        with counting_calls(*_ONCE_PER_RENDER) as render_plus_one:
            assert auth_client.get("/retirement").status_code == 200
            with app.app_context():
                retirement_dashboard_service.load_gap_inputs(
                    BalanceContext.build(seed_user["user"].id),
                )

        assert render_plus_one["load_gap_inputs"] == baseline + 1, (
            "the call counter did not see a deliberately repeated load, so "
            "the assertions beside it grade nothing"
        )


class TestOneCalendarDerivationPerRender:
    """Every render below derives the owner's pay calendar exactly ONCE.

    **A read pass memoizes the calendar, so this ought to be the pass count
    restated -- and for three renders it was not** (pay-calendar plan step
    C2-f2d-3, found by that step's adversarial design review).  Measured
    through the routes over an owner with a salary profile and three
    investment accounts: ``/savings`` derived it SEVEN times and
    ``/retirement`` SIX, against ONE read pass each, because
    ``income_service.get_current_gross_biweekly`` derived its own from a
    ``user_id`` while every chain reaching it already held the pass.  The
    count grew with the account count, since the seam asks for a
    per-account contribution feed.

    **This is a SECOND count of the same question and that is the point.**
    ``TestOneReadPassPerRender`` proves the render opens one pass; it cannot
    see a producer that holds the pass and derives anyway, because opening
    nothing is exactly what such a producer does.

    ``/`` is deliberately NOT here: it opens TWO passes, one per producer, so
    it derives twice by construction.  That is ledger row **P61** and plan step
    **C2-f2e** closes it -- pinning 2 here would pin the defect.
    """

    def test_savings_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings derives the owner's pay calendar exactly once."""
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["calendar_for"] == 1, (
            f"/savings derived the pay calendar {counts['calendar_for']} "
            "times; the route opens one read pass and every producer below it "
            "must read that pass's memo"
        )

    def test_retirement_derives_the_calendar_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement derives the owner's pay calendar exactly once."""
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts["calendar_for"] == 1, (
            f"/retirement derived the pay calendar {counts['calendar_for']} "
            "times; see the class docstring"
        )

    def test_the_count_grows_with_the_account_set_when_a_producer_derives(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """NEGATIVE CONTROL: a per-account derivation would be VISIBLE here.

        The regression these two cases exist for scaled with the owner's
        investment-account count, so the control adds accounts and requires the
        count NOT to move.  A counter that never incremented, or a fixture whose
        producers sat behind an early return, would pass the two assertions
        above on the tree that derived seven times -- which is what happened.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)
            for index in range(2):
                make_investment_account(
                    seed_user, db.session, seed_periods_today[0],
                    Decimal("50000.00"), name=f"extra-401k-{index}",
                )
            db.session.commit()

        with counting_calls(_CALENDAR_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["calendar_for"] == 1, (
            f"/savings derived the pay calendar {counts['calendar_for']} times "
            "for three investment accounts, so a producer is deriving PER "
            "ACCOUNT rather than reading the read pass's memo"
        )
