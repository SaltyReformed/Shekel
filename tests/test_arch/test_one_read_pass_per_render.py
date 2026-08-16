"""Architecture test: one HTTP render opens exactly ONE read pass.

Plan step **C2-f2d-1**, ledger row **P43**.

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
apart -- and the countdown disagreed by one paycheck (517 against 516).  The
figures were not wrong; they contradicted each other, and nothing in the
application could notice.

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
between here and it** (measured, ``grep -rl "BalanceContext\\.build"
app/services/``): ``calendar_service``, ``dashboard_pulse_service``,
``dashboard_service``, ``investment_dashboard_service/_context``,
``investment_dashboard_service/_orchestrator``, ``loan_recurrence_sync``,
``savings_dashboard_service/_data`` and ``tax_report_service``.  The seventh is
one of THESE two pages -- it is ``/savings``'s own door, left deliberately (see
``_orchestrator.compute_dashboard_data``) and owned by ``C2-f2d-3``.  Ledger row
**P56** carries the layer predicate.  Asserting it here today would fail on
eight modules this leaf does not touch, so the gate is per-render and grows a
render at a time.

**What this file therefore does NOT prove.** It counts read passes, not clock
reads: ``compute_pension_summary``, ``compute_gap_net_biweekly`` and
``build_employer_salary_basis`` each still call ``date.today()`` for themselves
on the very renders below, so these can be green while two cards disagree
across a New Year boundary.  Ledger row **P55** owns that.

Test IDs
--------

- ``test_retirement_render_opens_one_read_pass``
- ``test_savings_render_opens_one_read_pass``
- ``test_readiness_whatif_fragment_opens_one_read_pass``
- ``test_the_counter_sees_a_second_pass`` (negative control: the counter
  must be able to fail, or the three above grade nothing)
"""

from contextlib import contextmanager
from decimal import Decimal

from app.services.balance_at import BalanceContext
from tests._test_helpers import make_investment_account


@contextmanager
def _counting_read_passes():
    """Count every ``BalanceContext.build`` while the block runs.

    Patched on the CLASS rather than on any module's imported name: several
    producers hold their own reference to ``BalanceContext``, so patching one
    module's attribute would count some builds and miss others -- and a gate
    that undercounts reads exactly like a gate that passes.
    """
    counter = {"n": 0}
    real = BalanceContext.build.__func__

    def counting(cls, user_id, as_of=None):
        counter["n"] += 1
        return real(cls, user_id, as_of)

    BalanceContext.build = classmethod(counting)
    try:
        yield counter
    finally:
        BalanceContext.build = classmethod(real)


def _seed_projecting_account(db, seed_user, seed_periods_today):
    """Give the owner a 401(k), so the engine actually runs on both pages.

    Necessary for ``/savings`` and not merely tidy: its Horizon skips the
    /retirement engine entirely -- returning zero bands before it builds a
    projection context at all -- when the owner has no retirement or investment
    account.  A count taken on the bare fixture would therefore read ``1`` on
    the tree this leaf FIXED and on the tree it fixed it FROM, which is the
    arm-that-cannot-fail shape ``docs/plans/lessons.md`` records.

    ``/retirement`` does not need it (both its producers load a projection
    batch before any early return), and it is seeded there anyway so the two
    counts are taken over the same state.

    **It varies on ONE axis, and that bounds what the counts above cover**
    (adversarial design review, 2026-08-16).  A future producer reached only by
    an owner who has a loan, an active goal, a credit card or a current period
    sits behind an early return on this fixture, so its pass would go uncounted.
    Widening the owner is the honest way to widen the gate; the claim these
    tests make is over the state seeded HERE, not over every owner.
    """
    return make_investment_account(
        seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
    )


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

        with _counting_read_passes() as counter:
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

        with _counting_read_passes() as counter:
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

        with _counting_read_passes() as counter:
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

        with _counting_read_passes() as render_only:
            resp = auth_client.get("/retirement")
        assert resp.status_code == 200

        with _counting_read_passes() as render_plus_one:
            assert auth_client.get("/retirement").status_code == 200
            with app.app_context():
                BalanceContext.build(seed_user["user"].id)

        assert render_plus_one["n"] == render_only["n"] + 1, (
            "the read-pass counter did not see a deliberately opened second "
            "pass, so the assertions beside it grade nothing"
        )
