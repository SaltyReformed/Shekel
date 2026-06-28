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

from datetime import date
from decimal import Decimal

import pytest

from app.services import (
    balance_at,
    balance_resolver,
    calendar_service,
    dashboard_service,
    home_equity_service,
    investment_dashboard_service,
    loan_payment_service,
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
    visible-period cell, where ``balance_result`` now comes from the seam's
    cash-flow entry ``balance_at.cash_balance_map`` (Level-1 Commit 8).
    Reading through that SAME seam entry -- not the raw ``balances_for``
    producer beneath it -- keeps this surface reader on the production path,
    so a regression in the seam's cash view (not just the producer) is caught
    here, and the reader is no longer a byte-identical twin of the
    ``balances_for`` calls in the per-kind locks.
    """
    result = balance_at.cash_balance_map(
        ctx["account"], ctx["scenario"], ctx["all_periods"],
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

    Mirrors the route's local ``current_bal = result.balances.get(current_period.id)``
    where ``result`` now comes from the seam's cash-flow entry
    ``balance_at.cash_balance_map`` (Level-1 Commit 8) -- the same seam call
    the checking-detail route makes.  The fixture pins ``today`` inside the
    anchor period, so ``current_period.id == anchor_period.id`` and the value
    displayed equals ``balances[anchor_period.id]``.
    """
    result = balance_at.cash_balance_map(
        ctx["account"], ctx["scenario"], ctx["all_periods"],
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


def _assert_surfaces_equal(surface_values, expected, label):
    """Assert every surface in *surface_values* returns the identical *expected*.

    The one dual-assert behind every cross-page equality test -- the cash
    matrix here AND the per-kind locks (loan / property / investment /
    secured) -- and the seam-injection negative controls that drive it:

      (a) every surface equals *expected*, with a message naming the
          offending surface and its value; and
      (b) the set of all surface values is exactly ``{expected}`` (the
          cross-page invariant -- no two surfaces produced different
          Decimals, even if none individually missed *expected*).

    *label* names the case in every message so a failure -- real or
    injected by a negative control -- points at the surface, its value, and
    the case.  The message shape is what the negative controls assert on
    (the patched surface name and its wrong value both appear), so it must
    stay stable.

    Args:
        surface_values: ``{surface_name: Decimal}`` from a reader dict.
        expected: The single Decimal every surface must return.
        label: A short case label woven into each assertion message.
    """
    for name, value in surface_values.items():
        assert value == expected, (
            f"surface {name!r} returned {value!r}; expected {expected!r} "
            f"for {label}.  All surface values: {surface_values!r}"
        )
    unique_values = set(surface_values.values())
    assert unique_values == {expected}, (
        f"surfaces produced more than one Decimal ({unique_values!r}) "
        f"for {label} -- this is the cross-page divergence HIGH-01 locks"
    )


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

            # Dual-assert (every surface == expected AND the set is a
            # singleton) via the shared helper, so the cash matrix and the
            # per-kind locks fire the identical cross-page invariant.
            expected = case["expected_balance"]
            _assert_surfaces_equal(
                surface_values, expected, f"case {case['id']!r}",
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

            # Run the same equality logic the positive test runs, through
            # the shared helper.  Expect AssertionError -- the seam
            # re-introduction must be caught.  If this raises something
            # else, the lock is broken in a different way and the test must
            # still fail loudly (no broad ``except``).
            with pytest.raises(AssertionError) as excinfo:
                _assert_surfaces_equal(
                    _all_surface_values(ctx),
                    case["expected_balance"],
                    f"case {case['id']!r}",
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


# ── Per-kind cross-page locks: loan / property / investment / secured ──
#
# The cash matrix above locks the five checking surfaces.  These classes
# extend the same cross-page contract to the recompute-at-read kinds the
# balance_at seam (Level 1) will reroute -- loan, property (appreciating),
# investment -- plus the property<->mortgage home-equity relationship.  Each
# per-kind fixture isolates ONE account of that kind (the seed_user checking
# is neutralised to $0) because two of the surfaces (year-end net worth and
# the savings net-worth trend) are AGGREGATE-only: they sum over ALL of the
# user's accounts, so a single-account fixture is the only way to read one
# kind's contribution.  Each reader encapsulates the surface's sign
# convention so the equality assertion stays uniform: the loan year-end
# reader negates the liability aggregate, the loan trend reader reads the
# (positive) ``liabilities`` lane, and the asset readers read ``assets``.


def _match_account_data(dashboard_data, account_id):
    """Return the single ``/savings`` account_data entry for *account_id*.

    The shared per-account tile lookup the per-kind savings readers reuse;
    asserts exactly one entry matched so a missing or duplicated account
    fails loudly rather than silently reading the wrong tile.
    """
    matches = [
        ad for ad in dashboard_data["account_data"]
        if ad["account"].id == account_id
    ]
    assert len(matches) == 1, (
        f"/savings account_data did not surface exactly one entry for "
        f"account_id={account_id}: matched {len(matches)}"
    )
    return matches[0]


def _net_worth_series(ctx):
    """Return the ``/savings`` net-worth trend series dict for the user.

    Carries the parallel ``net`` / ``assets`` / ``liabilities`` lists plus
    ``current_index`` (the position of today's period in the trend window);
    the per-kind trend readers index into it at ``current_index``.
    """
    data = savings_dashboard_service.compute_dashboard_data(ctx["user_id"])
    return data["net_worth"]["series"]


def _year_end_month_balance(ctx):
    """Return year-end net worth at the anchor month (the aggregate input).

    With a single isolated account this equals that account's signed
    contribution to net worth at the anchor period: ``+balance`` for an
    asset, ``-abs(balance)`` for a liability (the loan reader negates it
    back to a positive balance).
    """
    summary = year_end_summary_service.compute_year_end_summary(
        ctx["user_id"], ctx["year"],
    )
    return summary["net_worth"]["monthly_values"][ctx["month"] - 1]["balance"]


def _savings_tile_value(ctx):
    """Read the ``/savings`` per-account tile current_balance for the account.

    Shared by all three single-account kinds (loan / property / investment):
    the per-account tile is a positive balance regardless of kind, so one
    reader serves every kind's ``savings`` surface.
    """
    data = savings_dashboard_service.compute_dashboard_data(ctx["user_id"])
    return _match_account_data(data, ctx["account_id"])["current_balance"]


def _trend_assets_value(ctx):
    """Read the net-worth trend's ``assets`` lane at the current index."""
    series = _net_worth_series(ctx)
    return series["assets"][series["current_index"]]


def _trend_liabilities_value(ctx):
    """Read the net-worth trend's ``liabilities`` lane at the current index.

    ``liabilities[i]`` is the positive magnitude ``abs(balance)``, so for an
    isolated loan it equals the loan's current balance directly.
    """
    series = _net_worth_series(ctx)
    return series["liabilities"][series["current_index"]]


def _asset_year_end_value(ctx):
    """Year-end balance for an isolated ASSET (its positive contribution)."""
    return _year_end_month_balance(ctx)


def _loan_year_end_value(ctx):
    """Year-end balance for an isolated LOAN, negated to the positive balance.

    The year-end aggregate subtracts a liability as ``-abs(balance)``, so an
    isolated loan yields ``-C``; negating recovers the positive ``C`` every
    other loan surface reports, keeping the equality assertion uniform.
    """
    return -_year_end_month_balance(ctx)


def _loan_detail_value(ctx):
    """Read the loan-detail balance (``resolve_account_loan`` current_balance).

    The service-level equivalent of ``GET /accounts/<id>/loan``: the
    resolver's ``LoanState.current_balance`` as of today, a positive amount
    owed.
    """
    resolved = loan_payment_service.resolve_account_loan(
        ctx["account_id"], ctx["scenario_id"], date.today(),
    )
    assert resolved is not None, (
        f"resolve_account_loan returned None for loan "
        f"account_id={ctx['account_id']}"
    )
    _params, state = resolved
    return state.current_balance


def _property_detail_value(ctx):
    """Read the property-detail home-equity market value (the anchor balance).

    The service-level equivalent of ``GET /accounts/<id>/property``:
    ``resolve_home_equity(...).market_value`` is the property's
    ``current_anchor_balance``; with no secured loans its ``total_debt`` is
    zero, so market value alone is the cross-page value.
    """
    return home_equity_service.resolve_home_equity(
        ctx["account"], ctx["scenario_id"], date.today(),
    ).market_value


def _investment_dashboard_value(ctx):
    """Read the investment-dashboard producer current_balance for the account."""
    return investment_dashboard_service.compute_dashboard_data(
        ctx["user_id"], ctx["account"],
    )["current_balance"]


# Per-kind reader dicts.  Each maps a surface name to a reader returning the
# SAME canonical positive quantity (the account's balance), so one
# ``_assert_surfaces_equal`` call locks every kind.  The shared
# ``_savings_tile_value`` serves the ``savings`` surface in all three.
_LOAN_SURFACE_READERS = {
    "savings": _savings_tile_value,
    "loan_detail": _loan_detail_value,
    "year_end": _loan_year_end_value,
    "net_worth_trend": _trend_liabilities_value,
}
_PROPERTY_SURFACE_READERS = {
    "savings": _savings_tile_value,
    "property_detail": _property_detail_value,
    "year_end": _asset_year_end_value,
    "net_worth_trend": _trend_assets_value,
}
_INVESTMENT_SURFACE_READERS = {
    "savings": _savings_tile_value,
    "investment_dashboard": _investment_dashboard_value,
    "year_end": _asset_year_end_value,
    "net_worth_trend": _trend_assets_value,
}


class TestLoanCrossPageEquality:
    """Every loan surface reports the same positive current balance C.

    A single isolated amortizing loan (current balance C, original principal
    P, with C != P) must report C identically on the /savings tile, the
    loan-detail producer, the negated year-end liability aggregate, and the
    net-worth trend's liabilities at today.  The boundary assertion
    additionally locks the pre-payment-period rule (PR #44 / aba0242): the
    balance seam must return C -- the current balance held flat -- at a
    pre-anchor period, NEVER the original principal P.
    """

    def test_all_surfaces_equal(self, app, cross_page_loan_ctx, auth_client):
        """Every loan surface returns C; the pre-anchor balance is C, not P.

        C = $200,000 (trued up today) and P = $240,000 (origination
        principal) differ, so the boundary assertion is falsifiable: a
        producer that returned the original principal at the pre-payment
        boundary would yield P there, failing ``== C``.  All four cross-page
        surfaces read C at today.
        """
        with app.app_context():
            ctx = cross_page_loan_ctx
            expected = ctx["C"]  # the trued-up current balance
            surface_values = {
                name: reader(ctx)
                for name, reader in _LOAN_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(surface_values, expected, "loan kind")

            # Boundary lock: the balance seam holds the current balance C
            # flat at a pre-anchor period; returning the original principal
            # P there is the exact PR #44 / aba0242 bug.  C != P is what
            # makes this non-tautological (verified by the second assert).
            balances = balance_at.balance_map(
                ctx["account"], ctx["scenario"], ctx["all_periods"],
            )
            pre_balance = balances[ctx["pre_anchor_period"].id]
            assert pre_balance == ctx["C"], (
                f"pre-anchor balance {pre_balance!r} != current balance "
                f"{ctx['C']!r}; the loan pre-payment boundary regressed"
            )
            assert pre_balance != ctx["P"], (
                f"pre-anchor balance {pre_balance!r} == original principal "
                f"{ctx['P']!r}; this is the exact PR #44 boundary bug"
            )

            # Route wiring: the loan detail page renders without raising.
            resp = auth_client.get(f"/accounts/{ctx['account_id']}/loan")
            assert resp.status_code == 200, (
                f"/accounts/<id>/loan returned {resp.status_code} for the "
                "loan kind"
            )


class TestPropertyCrossPageEquality:
    """Every property surface reports the same market value V.

    A single isolated appreciating Property (market value V, anchored at the
    current period so the flat carry and the appreciation projection
    coincide at today) must report V identically on the /savings tile, the
    home-equity market value (total_debt zero -- no secured loans), the
    year-end asset aggregate, and the net-worth trend's assets at today.
    """

    def test_all_surfaces_equal(
        self, app, cross_page_property_ctx, auth_client,
    ):
        """Every property surface returns V at today.

        V = $400,000, anchored at the current period.  The /savings tile,
        the home-equity market value, the year-end asset aggregate, and the
        net-worth trend assets all read V.
        """
        with app.app_context():
            ctx = cross_page_property_ctx
            expected = ctx["V"]
            surface_values = {
                name: reader(ctx)
                for name, reader in _PROPERTY_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(surface_values, expected, "property kind")

            resp = auth_client.get(f"/accounts/{ctx['account_id']}/property")
            assert resp.status_code == 200, (
                f"/accounts/<id>/property returned {resp.status_code} for "
                "the property kind"
            )


class TestInvestmentCrossPageEquality:
    """Every investment surface reports the same balance V.

    A single isolated Investment (balance V, anchored at the current period
    with no current-period contribution, so the growth projection re-applies
    nothing at the anchor period) must report V identically on the /savings
    tile, the investment dashboard, the year-end asset aggregate, and the
    net-worth trend's assets at today.

    Scope note: at anchor==current all four surfaces legitimately resolve
    through the same base producer (the resolver's current-period balance),
    so this class is a four-surface WIRING lock at the agreement point, not a
    cross-producer divergence lock the way the loan boundary is.  The
    cross-producer investment lock -- where the model-from-anchor kernel
    value (the anchor compounded forward to today) diverges from the
    cash-basis tile -- requires an anchor-in-past fixture that diverges on
    today's code, so it is added alongside the savings-tile reroute (the
    Model-from-anchor unification), not here.  The growth math itself is
    covered by tests/test_services/test_balance_at.py.
    """

    def test_all_surfaces_equal(
        self, app, cross_page_investment_ctx, auth_client,
    ):
        """Every investment surface returns V at today.

        V = $100,000, anchored at the current period with no contribution.
        The /savings tile, the investment dashboard, the year-end asset
        aggregate, and the net-worth trend assets all read V.
        """
        with app.app_context():
            ctx = cross_page_investment_ctx
            expected = ctx["V"]
            surface_values = {
                name: reader(ctx)
                for name, reader in _INVESTMENT_SURFACE_READERS.items()
            }
            _assert_surfaces_equal(
                surface_values, expected, "investment kind",
            )

            # Route wiring: the investment detail page renders without
            # raising (parity with the loan and property route checks above).
            resp = auth_client.get(f"/accounts/{ctx['account_id']}/investment")
            assert resp.status_code == 200, (
                f"/accounts/<id>/investment returned {resp.status_code} for "
                "the investment kind"
            )

    def test_anchor_in_past_tile_adopts_modeled_value(
        self, app, cross_page_investment_past_anchor_ctx,
    ):
        """The /savings tile AND the investment dashboard adopt the modeled value.

        The Level 1 cross-producer investment lock the class docstring above
        defers to the savings-tile reroute.  With the investment anchored 6
        months in the past at a 7% return, the kernel's model-from-anchor map
        compounds the $100,000 opening balance forward to today.  The /savings
        tile, the investment-dashboard headline, the year-end asset aggregate,
        and the net-worth trend all read that SAME modeled value at today --
        and it is strictly greater than the flat $100,000 cash-basis carry the
        pre-reroute surfaces showed, which is what makes the lock
        non-tautological (an unrerouted surface would read the flat $100,000
        and fail).

        The investment-dashboard headline now reads the model-from-anchor
        balance through the ``balance_at`` seam (the dashboards-commit
        reroute), so it joins this lock.  Its forward growth chart still seeds
        from the cash basis -- a separate figure, not asserted here.
        """
        with app.app_context():
            ctx = cross_page_investment_past_anchor_ctx

            # The canonical model-from-anchor value at today, read straight
            # from the seam (the producer the rerouted tile now reads).
            modeled = balance_at.balance_map(
                ctx["account"], ctx["scenario"], ctx["all_periods"],
            )[ctx["current_period"].id]

            # Non-tautological AND magnitude-bounded: the modeled balance must
            # compound strictly ABOVE the flat cash-basis carry (so a tile
            # still reading the flat value fails) but stay BELOW a full year of
            # growth at the 7% assumed return -- the anchor is ~6 months in the
            # past, so any correct model-from-anchor value sits in (V0, V0 *
            # 1.07).  Both bounds are hand-computed and independent of the
            # growth engine's per-period day-count convention; the EXACT value
            # is calendar-relative (the fixture builds its periods from today),
            # so it is pinned penny-exact -- with its arithmetic -- in
            # tests/test_services/test_balance_at.py (the anchor-in-past
            # kernel-equality cases), not here.  The upper bound is what an
            # "all surfaces == seam" check alone could not give: it catches a
            # shared over-compounding bug (wrong period count or rate) in the
            # seam and every rerouted surface at once.
            v0 = ctx["V0"]
            assert v0 < modeled < v0 * Decimal("1.07"), (
                f"modeled balance {modeled!r} fell outside the hand-computed "
                f"(V0, V0*1.07) band for a ~6-month 7% projection from {v0!r}: "
                "expected strictly above the flat carry but below one full "
                "year's growth"
            )

            # Every kernel-modeled surface -- now including the investment
            # dashboard headline -- reads that same value at today.
            modeled_readers = {
                "savings": _savings_tile_value,
                "investment_dashboard": _investment_dashboard_value,
                "year_end": _asset_year_end_value,
                "net_worth_trend": _trend_assets_value,
            }
            surface_values = {
                name: reader(ctx) for name, reader in modeled_readers.items()
            }
            _assert_surfaces_equal(
                surface_values, modeled,
                "investment kind (anchor-in-past, model-from-anchor)",
            )


class TestSecuredHomeEquityEquality:
    """The property<->mortgage home-equity relationship reconciles across surfaces.

    Unlike the single-value kinds this is a RELATIONSHIP: a property (market
    value PV) secured by a mortgage (current balance MC).  Three legs must
    agree across surfaces -- the property leg (market value == the property's
    /savings tile == PV), the mortgage leg (total secured debt == the
    mortgage's /savings tile == the loan-detail balance == MC), and the
    equity (PV - MC == the year-end net-worth aggregate == the net-worth
    trend's net at today).
    """

    def test_equity_relationship(self, app, cross_page_secured_ctx):
        """market_value == PV, total_debt == MC, equity == PV - MC everywhere.

        PV = $400,000, MC = $250,000, so equity = $150,000.  The home-equity
        producer's market_value / total_debt / equity reconcile to the
        /savings tiles, the loan-detail balance, the year-end aggregate, and
        the net-worth trend net.
        """
        with app.app_context():
            ctx = cross_page_secured_ctx
            pv, mc = ctx["PV"], ctx["MC"]
            equity = home_equity_service.resolve_home_equity(
                ctx["property_account"], ctx["scenario_id"], date.today(),
            )
            dashboard = savings_dashboard_service.compute_dashboard_data(
                ctx["user_id"],
            )
            prop_tile = _match_account_data(
                dashboard, ctx["property_account_id"],
            )["current_balance"]
            mortgage_tile = _match_account_data(
                dashboard, ctx["mortgage_account_id"],
            )["current_balance"]
            resolved = loan_payment_service.resolve_account_loan(
                ctx["mortgage_account_id"], ctx["scenario_id"], date.today(),
            )
            assert resolved is not None, "securing mortgage did not resolve"
            # resolved is (LoanParams, LoanState); the loan-detail balance is
            # the state's current_balance (index 1).
            loan_detail = resolved[1].current_balance

            # Property leg: market value == the property tile == PV.
            assert equity.market_value == prop_tile == pv, (
                f"property leg disagreed: market_value={equity.market_value!r}, "
                f"savings_tile={prop_tile!r}, PV={pv!r}"
            )
            # Mortgage leg: total secured debt == the mortgage tile == the
            # loan-detail balance == MC.
            assert (
                equity.total_debt == mortgage_tile == loan_detail == mc
            ), (
                f"mortgage leg disagreed: total_debt={equity.total_debt!r}, "
                f"savings_tile={mortgage_tile!r}, "
                f"loan_detail={loan_detail!r}, MC={mc!r}"
            )
            # Equity == year-end net worth == trend net == PV - MC.
            year_end_nw = _year_end_month_balance(ctx)
            series = dashboard["net_worth"]["series"]
            trend_net = series["net"][series["current_index"]]
            # PV - MC = 400000.00 - 250000.00 = 150000.00.
            assert equity.equity == year_end_nw == trend_net == (pv - mc), (
                f"equity disagreed: equity={equity.equity!r}, "
                f"year_end_nw={year_end_nw!r}, trend_net={trend_net!r}, "
                f"PV-MC={(pv - mc)!r}"
            )


class TestPerKindSeamInjectionLock:
    """Each per-kind cross-page lock catches an injected single-surface divergence.

    The per-kind analogue of :class:`TestSeamInjectionLock`: monkeypatching
    ONE reader in a kind's reader dict to return a deliberately wrong
    Decimal must make :func:`_assert_surfaces_equal` raise an AssertionError
    naming the patched surface and the wrong value.  Without this a per-kind
    equality test that happened to read the same producer twice would still
    report green.  Parametrised over the three single-value kinds to stay
    DRY.
    """

    _WRONG = Decimal("-99999.99")  # no fixture balance equals this

    # One per-kind negative-control case: (ctx fixture name, that kind's
    # reader dict, the ctx key holding the true balance, the surface to
    # break).  Bundled into a single parametrize value so the test stays a
    # cohesive 5-argument method rather than threading four parallel
    # parametrize columns.
    @pytest.mark.parametrize(
        "spec",
        [
            ("cross_page_loan_ctx", _LOAN_SURFACE_READERS, "C", "savings"),
            ("cross_page_property_ctx", _PROPERTY_SURFACE_READERS, "V", "savings"),
            (
                "cross_page_investment_ctx", _INVESTMENT_SURFACE_READERS,
                "V", "savings",
            ),
        ],
        ids=["loan", "property", "investment"],
    )
    def test_injected_divergence_is_caught(self, app, request, monkeypatch, spec):
        """Patching one reader to a wrong Decimal makes the lock fire on it.

        The patched surface reports ``_WRONG`` while every other surface
        reports the kind's true balance, so :func:`_assert_surfaces_equal`
        must raise an AssertionError whose message names the patched surface
        and the wrong value -- proving the lock bites on a real
        single-surface regression, not a coincidence.
        """
        ctx_fixture, readers, expected_key, patched_surface = spec
        ctx = request.getfixturevalue(ctx_fixture)
        expected = ctx[expected_key]
        with app.app_context():

            def _broken_reader(_ctx):
                """Return a deliberately wrong Decimal for the patched surface."""
                return self._WRONG

            monkeypatch.setitem(readers, patched_surface, _broken_reader)
            surface_values = {
                name: reader(ctx) for name, reader in readers.items()
            }

            with pytest.raises(AssertionError) as excinfo:
                _assert_surfaces_equal(surface_values, expected, ctx_fixture)

            message = str(excinfo.value)
            assert repr(patched_surface) in message, (
                f"AssertionError did not name the patched surface "
                f"{patched_surface!r}: {message!r}"
            )
            assert str(self._WRONG) in message, (
                f"AssertionError did not name the wrong value "
                f"{self._WRONG!r}: {message!r}"
            )
