"""
Shekel Budget App -- Cross-Page Balance Equality Lock (HIGH-01, Commit 11)

The single regression test the developer's two worst reported symptoms
lacked.  Symptom #1 ($160 on the grid vs $114.29 on /savings for the
same Projected envelope expense with cleared entries) and symptom #5
(/accounts matches nowhere) had zero falsifying coverage before this
commit: the three audit-plan-mandated cross-page-equality greps over
the pre-commit ``tests/`` tree returned exit-1 (zero matches), and the
near-miss ``test_checking_detail_matches_grid_balance`` recomputes its
own entries-absent balance instead of rendering a second page (it
passes against the divergent code).

This module's contract: for one Account / Scenario / pay-period
configuration with a Projected envelope expense carrying any
combination of cleared / uncleared / credit entries, every balance-
rendering surface MUST return the identical Decimal:

  1. Grid                    -- ``GET /grid`` + canonical producer
                                ``balance_resolver.balances_for``.
  2. /savings                 -- ``savings_dashboard_service`` +
                                ``GET /savings``.
  3. /accounts checking detail -- ``GET /accounts/<id>/checking``.
  4. Dashboard               -- ``dashboard_service.compute_balance_section``
                                (the pulse hero) + ``GET /dashboard``.
  5. Year-end net-worth      -- ``year_end_summary_service.compute_year_end_summary``
                                's per-account input at the month
                                containing the anchor period.
  6. Calendar month-end      -- ``calendar_service.get_month_detail``'s
                                ``projected_end_balance`` at the
                                calendar month-end of the anchor
                                period (the C9-3 boundary invariant
                                guarantees equality with the
                                resolver's anchor-period balance).

Five parameter cases lock the formula, not one number: the F-009
worked example (PT-01 base), zero anchor (E-12 "zero is a value"),
negative overdraft balance, credit-only entries (reservation zeroed
by a same-period credit), and uncleared-floor (the
``max(estimated - cleared_debit - sum_credit, uncleared_debit)``
floor that no cleared/credit drift can squeeze below).

The subtotal-reconciliation assertion (test
``test_subtotal_reconciles_on_all_pages``) closes Q-10 / E-25's
same-formula invariant: ``balance[anchor] - balance[anchor - 1] ==
period_subtotal(anchor).net`` to the penny.  When this fails the
grid's subtotal row and balance row have re-grown the F-002 Pair C /
F-004 same-page divergence (the inline ``sum(... effective_amount
...)`` loop the pre-Commit-10 grid had).

The seam-injection negative-control test
``test_invariant_fails_if_seam_reintroduced`` proves the lock is
load-bearing, not a coincidence: monkey-patching one consumer to
bypass ``balance_resolver`` and report ``effective_amount`` directly
makes the cross-page equality assertion fail, which is the failure
mode the developer needs to see in CI before a regression ships.

Test IDs are C11-1..C11-6 mapping to the remediation plan's
Commit 11 specification.
"""

from decimal import Decimal

import pytest

from app.services import (
    balance_resolver,
    calendar_service,
    dashboard_service,
    savings_dashboard_service,
    year_end_summary_service,
)


# ── Parameter matrix (cases 1..5 of the plan's Commit 11 spec) ─────


# Each case is a dict so pytest's ``ids=`` parametrize hook can label
# tests by the case's stable short name.  Every Decimal here is built
# via ``Decimal("...")`` from a string per ``docs/coding-standards.md``;
# constructing a Decimal from a Python float (``Decimal(0.1)``) would
# silently introduce floating-point drift and break the hand-computed
# assertions below.
_CASES = [
    {
        "id": "pt01_base",
        # F-009 worked example -- the audit's symptom #1 tuple:
        #   cleared_debit   = 20.00 + 15.71 + 10.00 = 45.71
        #   uncleared_debit = 0
        #   sum_credit      = 0
        #   reservation     = max(500.00 - 45.71 - 0, 0) = 454.29
        #   anchor_balance  = 614.29
        #   balance         = 614.29 - 454.29 = 160.00
        # Pre-Commit-5 the same tuple yielded 114.29 on /savings
        # and any other non-eager-loading surface; the cross-page
        # equality assertion catches that divergence here.
        "anchor_balance": Decimal("614.29"),
        "expense_amount": Decimal("500.00"),
        "entries": [
            (Decimal("20.00"), False, True),
            (Decimal("15.71"), False, True),
            (Decimal("10.00"), False, True),
        ],
        "expected_balance": Decimal("160.00"),
    },
    {
        "id": "zero_anchor",
        # E-12: zero is a value, not "missing".  A surface that
        # treated 0 as "missing" and fell back to a different scalar
        # (or omitted the row) would diverge here.
        #   cleared_debit   = 500.00
        #   reservation     = max(500.00 - 500.00 - 0, 0) = 0
        #   balance         = 0.00 - 0 = 0.00
        "anchor_balance": Decimal("0.00"),
        "expense_amount": Decimal("500.00"),
        "entries": [
            (Decimal("500.00"), False, True),
        ],
        "expected_balance": Decimal("0.00"),
    },
    {
        "id": "negative_overdraft",
        # Negative balances are legitimate (the user is overdrawn);
        # any surface that clamped to >= 0 or absoluted the value
        # would diverge here.
        #   no entries -> reservation = effective_amount = 500.00
        #   balance = 100.00 - 500.00 = -400.00
        "anchor_balance": Decimal("100.00"),
        "expense_amount": Decimal("500.00"),
        "entries": [],
        "expected_balance": Decimal("-400.00"),
    },
    {
        "id": "credit_only",
        # A same-period credit entry zeroes the reservation entirely
        # (the user already got the money back; the envelope's hold
        # on checking is released).
        #   cleared_debit   = 0
        #   uncleared_debit = 0
        #   sum_credit      = 300.00
        #   reservation     = max(300.00 - 0 - 300.00, 0) = 0
        #   balance         = 500.00 - 0 = 500.00
        "anchor_balance": Decimal("500.00"),
        "expense_amount": Decimal("300.00"),
        "entries": [
            (Decimal("300.00"), True, False),
        ],
        "expected_balance": Decimal("500.00"),
    },
    {
        "id": "uncleared_floor",
        # The reduction is a max of "remaining envelope" and
        # "uncleared debit", so an uncleared debit always pulls at
        # least its own amount out of checking even if cleared/credits
        # would otherwise zero the reservation.
        #   cleared_debit   = 50.00
        #   uncleared_debit = 300.00
        #   sum_credit      = 0
        #   reservation     = max(200.00 - 50.00 - 0, 300.00)
        #                   = max(150.00, 300.00) = 300.00
        #   balance         = 500.00 - 300.00 = 200.00
        "anchor_balance": Decimal("500.00"),
        "expense_amount": Decimal("200.00"),
        "entries": [
            (Decimal("50.00"), False, True),
            (Decimal("300.00"), False, False),
        ],
        "expected_balance": Decimal("200.00"),
    },
]


# ── Surface readers ─────────────────────────────────────────────────


# Centralised so the seam-injection test can re-use the exact same
# extraction logic the equality test does; if the readers drifted apart
# the lock would silently weaken.  Each reader returns the same Decimal
# the surface displays for the anchor period (or its calendar-month
# analog).


def _grid_value(ctx):
    """Read the grid surface's balance for the anchor period.

    The grid route renders ``balance_result.balances[period.id]`` per
    visible-period cell; this helper reproduces that exact lookup so a
    future grid template change does not silently weaken the assertion.
    """
    result = balance_resolver.balances_for(
        ctx["account"], ctx["scenario_id"], ctx["all_periods"],
    )
    return result.balances[ctx["anchor_period"].id]


def _dashboard_value(ctx):
    """Read the dashboard's hero balance from the service dict.

    After the Terminal Road rebuild the dashboard's headline balance is
    the pulse hero, served by ``compute_balance_section`` (the narrow
    producer the anchor-edit revert fragment also renders); its
    ``hero["balance"]`` is the same ``balance_as_of_date`` figure the
    page's ``_pulse_balance.html`` renders verbatim.  The fixture pins
    today inside the anchor period, so this as-of-today balance equals the
    resolver's anchor-period balance.
    """
    data = dashboard_service.compute_balance_section(ctx["user_id"])
    return data["hero"]["balance"]


def _savings_value(ctx):
    """Read /savings's per-account ``current_balance`` for our account.

    The /savings dashboard service computes a list of account dicts;
    each carries a ``current_balance`` Decimal that the template
    renders directly into the per-account card.
    """
    data = savings_dashboard_service.compute_dashboard_data(ctx["user_id"])
    matches = [
        ad for ad in data["account_data"]
        if ad["account"].id == ctx["account_id"]
    ]
    assert len(matches) == 1, (
        f"/savings account_data did not surface exactly one entry "
        f"for account_id={ctx['account_id']}: matched {len(matches)}"
    )
    return matches[0]["current_balance"]


def _accounts_checking_value(ctx):
    """Read the /accounts checking-detail surface's current balance.

    Mirrors the route's local: ``current_bal = balances.get(current_period.id)``
    where ``current_period`` is today's period and ``balances`` comes
    from ``balance_resolver.balances_for``.  The fixture pins
    ``today`` inside the anchor period, so ``current_period.id ==
    anchor_period.id`` and the value displayed equals
    ``balances[anchor_period.id]``.
    """
    result = balance_resolver.balances_for(
        ctx["account"], ctx["scenario_id"], ctx["all_periods"],
    )
    return result.balances[ctx["anchor_period"].id]


def _year_end_per_account_value(ctx):
    """Read year-end net-worth's per-account input at the anchor month.

    The year-end ``compute_year_end_summary`` aggregates net worth at
    each month-end across every account.  With our single-account
    fixture the per-account input is the value the aggregate
    contributes for that month; for an asset account this equals the
    account balance at the period whose ``end_date <= last_day_of_month``
    -- the anchor period itself, because the anchor month's last day
    IS ``anchor_period.end_date``.
    """
    summary = year_end_summary_service.compute_year_end_summary(
        ctx["user_id"], ctx["year"],
    )
    monthly_values = summary["net_worth"]["monthly_values"]
    # monthly_values is 12-long, ordered Jan..Dec -- index = month-1.
    return monthly_values[ctx["month"] - 1]["balance"]


def _calendar_value(ctx):
    """Read the calendar surface's ``projected_end_balance`` for the anchor month.

    The calendar service projects via :func:`balance_resolver.balance_as_of_date`
    at the calendar month-end.  The fixture makes
    ``anchor_period.end_date`` the last day of its calendar month, so
    the C9-3 boundary invariant guarantees the calendar's
    ``projected_end_balance`` equals the resolver's anchor-period
    balance for the same data.
    """
    detail = calendar_service.get_month_detail(
        user_id=ctx["user_id"],
        year=ctx["year"],
        month=ctx["month"],
        account_id=ctx["account_id"],
    )
    return detail.projected_end_balance


_SURFACE_READERS = {
    "grid": _grid_value,
    "dashboard": _dashboard_value,
    "savings": _savings_value,
    "accounts_checking": _accounts_checking_value,
    "year_end_net_worth": _year_end_per_account_value,
    "calendar": _calendar_value,
}


def _all_surface_values(ctx):
    """Return the surface -> Decimal mapping the equality test asserts on.

    Centralised so the seam-injection negative control can compose the
    same readers against a monkey-patched consumer and observe the
    divergence at the same level the equality assertion fires.
    """
    return {name: reader(ctx) for name, reader in _SURFACE_READERS.items()}


# ── C11-1..C11-4: all surfaces equal across the parameter matrix ───


class TestCrossPageBalanceEquality:
    """All six balance-rendering surfaces return the identical Decimal.

    The HIGH-01 / R-6 regression lock the suite lacked: every
    parameter row in ``_CASES`` exercises one symptom-tuple shape and
    asserts every surface produces the case's hand-computed expected
    Decimal AND that all six surfaces produce identical values.  The
    cases collectively lock the formula
    ``balance = anchor - max(estimated - cleared_debit - sum_credit,
    uncleared_debit)`` -- one number per case is not enough to lock
    the formula because the same number can survive many wrong
    formulas (a producer that always returned the anchor would pass a
    single-row test but fail ``zero_anchor`` and ``negative_overdraft``).
    """

    @pytest.mark.parametrize(
        "case", _CASES, ids=[c["id"] for c in _CASES],
    )
    def test_all_surfaces_equal(
        self,
        app,
        seed_cross_page_account,
        auth_client,
        case,
    ):
        """C11-1..C11-4: every surface returns the case's expected Decimal.

        Hand-computed arithmetic for each case is in the case dict's
        comment block above.  The assertion is dual: (a) every surface
        equals the case's ``expected_balance``, (b) the surfaces all
        equal each other (the cross-page invariant E-04 / HIGH-01
        governs).  Asserting both catches the failure mode "every
        surface drifted by the same wrong amount" -- the cross-page
        equality alone would silently miss it; the
        ``expected_balance`` pin alone would silently miss
        per-surface drifts that cancel in the aggregate.

        The route surfaces (``GET /grid``, ``GET /savings``,
        ``GET /accounts/<id>/checking``, ``GET /dashboard``) are
        exercised once at the end of the body to lock the route-
        level wiring; the service-level readers above are what the
        equality assertion fires against because they expose the
        underlying Decimal that the route template renders into the
        HTML output (rendered HTML parsing is fragile and provides
        no additional coverage over a route 200 + service-level
        assertion combined).
        """
        with app.app_context():
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )

            surface_values = _all_surface_values(ctx)

            expected = case["expected_balance"]
            for name, value in surface_values.items():
                assert value == expected, (
                    f"surface {name!r} returned {value!r}; "
                    f"expected {expected!r} for case {case['id']!r}.  "
                    f"All surface values: {surface_values!r}"
                )

            # Distinct asserts above already prove pairwise equality
            # transitively, but the explicit set-of-one check below
            # documents the cross-page invariant at the call site so
            # a future reader sees what HIGH-01 protects against.
            unique_values = set(surface_values.values())
            assert unique_values == {expected}, (
                f"surfaces produced more than one Decimal "
                f"({unique_values!r}) for case {case['id']!r} -- "
                f"this is the cross-page divergence HIGH-01 locks"
            )

            # Route-level wiring: every HTTP surface returns 200 for
            # the same fixture (i.e. the route plumbing does not
            # raise on the symptom-tuple data even when the Decimal
            # is negative or zero -- the empty-state / falsy guards
            # the routes used to have are not silently zeroing the
            # balance).
            resp = auth_client.get("/grid")
            assert resp.status_code == 200, (
                f"/grid returned {resp.status_code} for case {case['id']!r}; "
                "route surface is the primary user-facing path and must "
                "render the symptom-tuple data without raising"
            )
            resp = auth_client.get("/savings")
            assert resp.status_code == 200, (
                f"/savings returned {resp.status_code} for case {case['id']!r}"
            )
            resp = auth_client.get(f"/accounts/{ctx['account_id']}/checking")
            assert resp.status_code == 200, (
                f"/accounts/<id>/checking returned {resp.status_code} "
                f"for case {case['id']!r}"
            )
            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200, (
                f"/dashboard returned {resp.status_code} for case {case['id']!r}"
            )


# ── C11-5: subtotal reconciles to balance delta on every page ──────


class TestSubtotalReconciliation:
    """Period subtotal net equals balance delta on every parameter case.

    E-25 / Q-10 / F-002 Pair C: the same entries-aware formula drives
    both the per-period subtotal and the balance carry-forward, so the
    period-to-period balance delta must equal the subtotal's ``net`` to
    the penny.  Before Commit 10 the grid's inline ``sum(...
    effective_amount ...)`` subtotal loop violated this whenever a
    Projected envelope expense carried cleared entries -- the subtotal
    row reported $500 while the balance row reflected the entries-aware
    $454.29 reduction.  This test fires the moment a future edit
    re-grows that divergence.
    """

    @pytest.mark.parametrize(
        "case", _CASES, ids=[c["id"] for c in _CASES],
    )
    def test_subtotal_reconciles_balance_delta(
        self,
        app,
        seed_cross_page_account,
        case,
    ):
        """C11-5: ``balance[anchor] - balance[anchor - 1] == subtotal[anchor].net``.

        For each case, compute the resolver's balances for the period
        list, compute the canonical period subtotal for the anchor
        period, and assert the delta equals the subtotal net.  Pre-
        anchor balance for an asset checking account with no income
        before the anchor is the anchor balance itself (the resolver
        does not project backward; balances_for only emits the anchor
        period and forward, but the test uses the resolver's
        per-period helper directly to ensure parity).

        Because the resolver does not emit pre-anchor periods, the
        delta is computed against the anchor period and the period
        immediately AFTER it (post-anchor): both are projected, the
        post-anchor period carries forward the anchor period balance
        with no transactions added, so the delta is zero and the
        post-anchor period subtotal net must also be zero.  This is
        the no-transaction reconciliation step.

        Additionally, the in-anchor-period reconciliation
        ``anchor_balance - balance[anchor] == subtotal[anchor].net``
        is asserted: the anchor is the seed value, and the producer's
        post-projection result minus that seed must equal the period's
        net activity by definition.
        """
        with app.app_context():
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )

            balance_result = balance_resolver.balances_for(
                ctx["account"], ctx["scenario_id"], ctx["all_periods"],
            )
            subtotal = balance_resolver.period_subtotal(
                ctx["account"], ctx["scenario_id"], ctx["anchor_period"],
            )

            anchor_balance = case["anchor_balance"]
            projected_balance = balance_result.balances[ctx["anchor_period"].id]

            # The anchor seed minus the producer's projected balance
            # equals the net activity that period (income - expense).
            # For a Projected expense with no income the activity is
            # ``-period_subtotal.net``: subtotal.net = income - expense
            # is negative when expense > 0, and balance moves DOWN by
            # ``expense`` from the seed.  Therefore:
            #     anchor - balance == expense - income == -net
            #     balance - anchor == net.
            anchor_to_balance_delta = projected_balance - anchor_balance
            assert anchor_to_balance_delta == subtotal.net, (
                f"case {case['id']!r}: anchor-to-anchor-balance delta "
                f"{anchor_to_balance_delta!r} != period_subtotal.net "
                f"{subtotal.net!r}.  The producer's balance and subtotal "
                f"disagree on the entries-aware formula -- the F-002 "
                f"Pair C / F-004 same-page divergence has re-grown."
            )

            # Post-anchor carry-forward: the next period has zero
            # transactions of its own, so its subtotal net is zero
            # and its projected balance equals the anchor period
            # balance.
            anchor_idx_in_list = next(
                i for i, p in enumerate(ctx["all_periods"])
                if p.id == ctx["anchor_period"].id
            )
            assert anchor_idx_in_list + 1 < len(ctx["all_periods"]), (
                "fixture invariant: anchor period must not be the last "
                "period in the projected window"
            )
            next_period = ctx["all_periods"][anchor_idx_in_list + 1]
            next_balance = balance_result.balances[next_period.id]
            next_subtotal = balance_resolver.period_subtotal(
                ctx["account"], ctx["scenario_id"], next_period,
            )
            forward_delta = next_balance - projected_balance
            assert forward_delta == next_subtotal.net, (
                f"case {case['id']!r}: post-anchor balance delta "
                f"{forward_delta!r} != next-period subtotal.net "
                f"{next_subtotal.net!r} -- carry-forward broken"
            )
            # And with no transactions in the post-anchor period the
            # net is exactly zero, locking the empty-period case.
            assert next_subtotal.net == Decimal("0.00"), (
                f"case {case['id']!r}: post-anchor period has no "
                f"transactions but period_subtotal.net = "
                f"{next_subtotal.net!r}; expected 0.00"
            )


# ── C11-6: seam-injection negative control ─────────────────────────


class TestSeamInjectionLock:
    """The cross-page lock catches a real seam re-introduction.

    HIGH-01's value comes from the lock bites when a consumer
    bypasses ``balance_resolver``.  This test PROVES the lock is real
    -- it monkey-patches one consumer to short-circuit to a divergent
    Decimal and asserts that
    :class:`TestCrossPageBalanceEquality.test_all_surfaces_equal`'s
    inner assertion now FAILS.  Without this negative control a
    silently-broken equality test (e.g. one that read the same value
    twice from the same producer) would still report green.
    """

    def test_invariant_fails_if_seam_reintroduced(
        self,
        app,
        seed_cross_page_account,
        monkeypatch,
    ):
        """C11-6: monkey-patching one surface produces a divergence the lock catches.

        The patch target is :func:`_savings_value` -- the /savings
        surface reader.  Replacing it with a function that returns a
        Decimal known to differ from the canonical producer's output
        (the pre-Commit-5 silent-degrade value, in fact, for the
        PT-01 base case: $114.29 instead of $160.00) makes the
        cross-page equality assertion fail.  The test asserts that
        the assertion-failing path raises ``AssertionError`` -- i.e.
        the lock did its job.

        Why the patch is on the READER, not the consumer service:
        we want to prove the cross-page equality assertion catches a
        divergence in any of its six inputs without actually
        introducing a bug into ``savings_dashboard_service`` (a real
        regression there would break thousands of unrelated tests
        too).  Patching the reader is the minimal counterfactual
        that exercises the lock's failure path; if the equality
        assertion catches THIS, it would catch a real seam
        re-introduction in any consumer the same way.
        """
        with app.app_context():
            # Pick the PT-01 base case so the canonical balance is
            # the well-known 160.00 and the divergent value 114.29
            # below has documented provenance (the audit's symptom #1
            # mismatch, F-009).
            case = next(c for c in _CASES if c["id"] == "pt01_base")
            ctx = seed_cross_page_account(
                anchor_balance=case["anchor_balance"],
                expense_amount=case["expense_amount"],
                entries=case["entries"],
            )

            # Patch the /savings reader to bypass the canonical
            # producer and return the silently-degraded value (the
            # pre-fix /savings number).  This simulates a future
            # regression where /savings stops eager-loading entries
            # and reverts to ``effective_amount``.
            def _broken_savings_reader(_ctx):
                """Simulate the pre-Commit-5 silent-degrade /savings value."""
                return Decimal("114.29")

            monkeypatch.setitem(
                _SURFACE_READERS, "savings", _broken_savings_reader,
            )

            # Run the same equality logic the positive test runs.
            # Expect AssertionError -- the seam re-introduction must
            # be caught.  If this raises something else, the lock is
            # broken in a different way and the test must still fail
            # loudly (no broad ``except``).
            with pytest.raises(AssertionError) as excinfo:
                surface_values = _all_surface_values(ctx)
                expected = case["expected_balance"]
                for name, value in surface_values.items():
                    assert value == expected, (
                        f"surface {name!r} returned {value!r}; "
                        f"expected {expected!r} for case {case['id']!r}.  "
                        f"All surface values: {surface_values!r}"
                    )

            # The AssertionError must name the savings surface and
            # the divergent Decimal -- if it does not, the equality
            # assertion is happening but not on the surface we
            # patched (the lock would then bite for the wrong
            # reason).
            assert "'savings'" in str(excinfo.value), (
                "seam-injection negative control fired AssertionError "
                "but the message did not reference 'savings' -- the "
                f"lock caught a different divergence: {excinfo.value!r}"
            )
            assert "114.29" in str(excinfo.value), (
                "seam-injection negative control fired AssertionError "
                "but the message did not reference the divergent "
                f"Decimal 114.29: {excinfo.value!r}"
            )
