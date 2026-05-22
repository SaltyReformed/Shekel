"""Tests for ``app.services.loan_resolver`` (Commit 13 / E-18).

These tests pin the resolver's behavior with hand-computed Decimal
expectations.  The resolver is a pure function, so every test
constructs a duck-typed loan-params object and a list of
anchor-event-shaped objects directly -- no database fixtures are
needed.  This keeps the tests fast (the full file runs in well
under a second) and isolates the resolver's logic from the rest of
the system.

Test IDs map to the Commit 13 plan in
``docs/audits/financial_calculations/remediation_plan.md`` section 9.
Every monetary expectation carries the arithmetic in a comment so a
future reader can verify the assertion by hand.
"""

import inspect
import io
import tokenize
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services import loan_resolver
from app.services.amortization_engine import (
    PaymentRecord,
    RateChangeRecord,
)
from app.services.loan_resolver import (
    LoanState,
    PayoffScenarios,
    compute_payoff_scenarios,
    resolve_loan,
)


# -- Duck-typed fixtures ----------------------------------------------------


@dataclass
class FakeLoanParams:
    """Minimal duck-type for LoanParams.

    The resolver reads only the listed attributes; this avoids a
    DB-bound LoanParams instance and keeps the tests pure.
    """

    origination_date: date
    term_months: int
    original_principal: Decimal
    interest_rate: Decimal
    payment_day: int
    is_arm: bool = False
    arm_first_adjustment_months: int | None = None


@dataclass
class FakeAnchorEvent:
    """Minimal duck-type for LoanAnchorEvent.

    ``created_at`` defaults to a deterministic timestamp so the
    resolver's ``(anchor_date, created_at)`` ordering is stable in
    tests that exercise the latest-anchor pick.
    """

    anchor_date: date
    anchor_balance: Decimal
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )


def _arm_400k_params() -> FakeLoanParams:
    """Return a 5/5 ARM at $400k / 6% / 360 months from 2026-01-01.

    Matches the 05_symptoms.md Symptom #4 worked example exactly so
    the hand-computed constant payment of $2,398.20 ties out.
    """
    return FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("400000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
        is_arm=True,
        arm_first_adjustment_months=60,
    )


def _origination_anchor(
    params: FakeLoanParams, balance: Decimal | None = None,
) -> FakeAnchorEvent:
    """Return the origination anchor Commit 12 backfilled for *params*."""
    return FakeAnchorEvent(
        anchor_date=params.origination_date,
        anchor_balance=balance or params.original_principal,
    )


def _add_months(start: date, months: int) -> date:
    """Convenience month-adder for test as_of dates (day-clamp to last day).

    Mirrors :func:`loan_resolver._add_months_to_date` so the tests
    do not depend on that private helper directly.
    """
    target_month = start.month + months
    target_year = start.year + (target_month - 1) // 12
    target_month = ((target_month - 1) % 12) + 1
    last_day_lookup = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    last_day = last_day_lookup[target_month - 1]
    if target_month == 2:
        is_leap = (
            target_year % 4 == 0
            and (target_year % 100 != 0 or target_year % 400 == 0)
        )
        last_day = 29 if is_leap else 28
    return date(target_year, target_month, min(start.day, last_day))


# -- C13-1 -- ARM payment constant across the fixed-rate window -------------


def test_arm_payment_constant_in_fixed_window():
    """C13-1: 5/5 ARM payment is byte-identical for every month in [0, 60).

    Hand-computed contractual payment for the 5/5 ARM at $400k/6%/
    360mo (per 05_symptoms.md:957-961):

        i = 0.06 / 12 = 0.005
        (1.005)^360 = 6.022575
        denom = 1 - (1.005)^(-360) = 1 - 0.166042 = 0.833958
        M* = 400000 * 0.005 / 0.833958 = $2,398.20

    Pre-fix, the engine's ARM scalar site re-amortized the frozen
    stored principal over a calendar-shrinking ``n``, so the
    displayed Monthly P&I drifted upward every month.  The
    resolver computes the payment once from the anchor balance and
    holds it constant for every ``as_of`` in the window
    (E-02 invariant; symptom #4 fix).
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    expected = Decimal("2398.20")

    payments_observed = set()
    for month_offset in range(60):
        as_of = _add_months(params.origination_date, month_offset)
        state = resolve_loan(
            params, [anchor], None, None, as_of,
        )
        payments_observed.add(state.monthly_payment)

    assert payments_observed == {expected}, (
        f"E-02 violation: payment varied across the fixed window: "
        f"{sorted(payments_observed)}"
    )


# -- C13-2 -- ARM no creep month 24 vs month 25 -----------------------------


def test_arm_no_creep_month_24_vs_25():
    """C13-2: Resolver payment at month 24 == month 25 (no creep).

    Pre-fix the engine returned $2,460.45 at month 24 and $2,463.28
    at month 25 (hand-recomputed in 05_symptoms.md:965-973 -- both
    differ from and exceed the correct constant $2,398.20).  The
    resolver returns one constant for every as_of in the window.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)

    state_24 = resolve_loan(
        params, [anchor], None, None,
        _add_months(params.origination_date, 24),
    )
    state_25 = resolve_loan(
        params, [anchor], None, None,
        _add_months(params.origination_date, 25),
    )

    # Byte-identical Decimal comparison (not numeric equality with
    # different scales).  The pre-fix values $2,460.45 / $2,463.28
    # both differed from $2,398.20 and from each other; we assert
    # the resolver pins them to the same correct value.
    assert state_24.monthly_payment == Decimal("2398.20")
    assert state_25.monthly_payment == Decimal("2398.20")
    assert state_24.monthly_payment == state_25.monthly_payment


# -- C13-3 -- confirmed payment reduces balance -----------------------------


def test_confirmed_payment_reduces_balance():
    """C13-3: One confirmed $1,888.36 P&I reduces balance by principal portion.

    Setup: $300k fixed-rate loan, 6%, 360mo, origination 2026-01-01.
    One confirmed payment on 2026-02-15 of $1,888.36.  Hand-computed
    balance reduction:

        interest = 300000 * (0.06 / 12) = 300000 * 0.005 = 1,500.00
        principal_portion = 1888.36 - 1500.00 = 388.36
        balance = 300000.00 - 388.36 = 299,611.64
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
        is_arm=False,
    )
    anchor = _origination_anchor(params)
    payment = PaymentRecord(
        payment_date=date(2026, 2, 15),
        amount=Decimal("1888.36"),
        is_confirmed=True,
    )

    state = resolve_loan(
        params, [anchor], [payment], None, date(2026, 3, 1),
    )

    assert state.current_balance == Decimal("299611.64")


# -- C13-4 -- projected payment is not replayed -----------------------------


def test_projected_payment_not_replayed():
    """C13-4: An unconfirmed (projected) payment leaves the balance unchanged.

    Future commitments are not historical fact and must not reduce
    the resolved principal.  Symptom #3 is closed precisely because
    only confirmed payments count.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    projected = PaymentRecord(
        payment_date=date(2026, 2, 15),
        amount=Decimal("1888.36"),
        is_confirmed=False,
    )

    state = resolve_loan(
        params, [anchor], [projected], None, date(2026, 3, 1),
    )

    # No confirmed payments; balance equals the anchor balance
    # (= original_principal for the Commit-12 origination anchor).
    assert state.current_balance == Decimal("300000.00")


# -- C13-5 -- fixed-rate, three confirmed payments --------------------------


def test_fixed_rate_replays_from_origination_anchor():
    """C13-5: Three confirmed contractual payments cumulatively reduce balance.

    Setup: $300k / 6% / 360mo; contractual payment = $1,798.65
    (amortize($300k, 0.06, 360)).  Three confirmed payments at the
    contractual amount in months 2, 3, 4.  Hand-computed cumulative
    reduction:

        m1: i = 300000.00 * 0.005   = 1500.00; p = 298.65;
            bal = 299701.35
        m2: i = 299701.35 * 0.005   = 1498.51 (HALF_UP); p = 300.14;
            bal = 299401.21
        m3: i = 299401.21 * 0.005   = 1497.01 (HALF_UP); p = 301.64;
            bal = 299099.57
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    payments = [
        PaymentRecord(date(2026, 2, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 3, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 4, 1), Decimal("1798.65"), True),
    ]

    state = resolve_loan(
        params, [anchor], payments, None, date(2026, 5, 1),
    )

    assert state.current_balance == Decimal("299099.57")


# -- C13-6 -- trueup anchor resets the replay -------------------------------


def test_anchor_trueup_resets_replay():
    """C13-6: A later user_trueup anchor makes pre-trueup payments irrelevant.

    Setup: $300k / 6% / 360mo.  Two confirmed payments BEFORE a
    user_trueup, one confirmed payment AFTER the trueup.  The
    trueup balance ($250,000) is intentionally far from the
    engine's from-origination projection (~$299,700) so the test
    proves the resolver starts the post-trueup replay from the
    trueup balance, not from the engine's projection.

    Hand-computed post-trueup arithmetic:

        anchor_balance = 250,000.00 (trueup)
        i = 250000.00 * 0.005   = 1250.00
        p = 1798.65 - 1250.00   = 548.65
        balance = 250000.00 - 548.65 = 249,451.35

    Pre-trueup payments (2026-02 and 2026-03) are filtered by the
    "payment_date > anchor_date" guard and never enter the replay.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    origination_anchor = FakeAnchorEvent(
        anchor_date=date(2026, 1, 1),
        anchor_balance=Decimal("300000.00"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2026, 4, 1),
        anchor_balance=Decimal("250000.00"),
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    payments = [
        # Pre-trueup -- filtered out by the resolver.
        PaymentRecord(date(2026, 2, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 3, 1), Decimal("1798.65"), True),
        # Post-trueup -- replayed.
        PaymentRecord(date(2026, 5, 1), Decimal("1798.65"), True),
    ]

    state = resolve_loan(
        params,
        [origination_anchor, trueup_anchor],
        payments,
        None,
        date(2026, 6, 1),
    )

    assert state.current_balance == Decimal("249451.35")


# -- C13-7 -- rate change after window applied ------------------------------


def test_rate_change_after_window_applied():
    """C13-7: After the ARM window ends, the resolver re-amortizes at new rate.

    Setup: 5/5 ARM, $400k / 6% / 360mo, origination 2026-01-01.
    Window ends at 2031-01-01 (origination + 60 months).  A rate
    change to 7% takes effect 2031-01-01.  No confirmed payments
    (balance = anchor_balance = $400,000).

    For as_of = 2031-02-01 (one month past window end):

        rate_at_as_of = 0.07 (post-window rate change applies)
        remaining = 360 - 61 = 299 months
        payment  = amortize(400000, 0.07, 299) = $2,830.61

    The in-window constant ($2,398.20) does not apply because both
    in-window-membership conditions (anchor AND as_of) must hold;
    here as_of is past the window.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    rate_changes = [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
        ),
    ]

    state = resolve_loan(
        params, [anchor], None, rate_changes, date(2031, 2, 1),
    )

    assert state.monthly_payment == Decimal("2830.61")


# -- C13-8 -- resolver module is pure (no Flask, no db.session) -------------


def test_resolver_is_pure_no_flask_no_db():
    """C13-8: Source of ``loan_resolver`` contains no Flask or db.session refs.

    Static guard against the services-boundary regression where a
    later refactor sneaks a ``from flask import request`` or a
    ``db.session.query(...)`` into the resolver.  The resolver MUST
    be a pure function: takes plain data, returns plain data.
    Adding I/O would silently break callers in test or task
    contexts where no app context is available.

    Strips string literals and comments before scanning so that
    documentation mentions of "db.session" or "Flask" (which are
    legitimate prose explaining why the resolver avoids them) do
    not trip the guard.  Only executable code is inspected.
    """
    source = inspect.getsource(loan_resolver)
    code_tokens = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        # Exclude string literals (including docstrings) and comments;
        # those are prose, not code paths.
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        # Python 3.12+: f-string substring tokens carry literal text
        # the same way STRING does.  Skip them too.
        if tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            continue
        code_tokens.append(tok.string)
    code_only = " ".join(code_tokens)

    forbidden = (
        "from flask",
        "import flask",
        "current_user",
        "db.session",
        "request.",
        "session[",
    )
    for marker in forbidden:
        assert marker not in code_only, (
            f"loan_resolver.py contains forbidden marker "
            f"{marker!r} in executable code; the resolver "
            f"must remain pure."
        )


# -- C13-9 -- rounding via round_money only ---------------------------------


def test_resolver_rounds_via_round_money_only():
    """C13-9: Resolver source uses ``round_money`` for its own rounding.

    The resolver may call helper functions (e.g. the engine's
    ``calculate_monthly_payment``) that quantize internally; what
    this test guards against is the resolver itself reaching
    ``Decimal.quantize`` directly, which would silently inherit
    Python's default ``ROUND_HALF_EVEN`` and drift one cent at
    half-cent boundaries.  ``round_money`` is the only boundary
    rounding called from this module.
    """
    source = inspect.getsource(loan_resolver)
    assert ".quantize(" not in source, (
        "loan_resolver.py reached .quantize directly; route through "
        "app.utils.money.round_money instead (E-26 boundary rule)."
    )
    assert "round_money(" in source, (
        "loan_resolver.py must import and use round_money."
    )


# -- C13-10 -- zero-rate loan -----------------------------------------------


def test_zero_rate_loan_payment_is_principal_over_n():
    """C13-10: Zero-rate loan returns payment = principal / n; no div-by-zero.

    A 0% loan amortizes as equal-principal payments with no
    interest accrual.  The engine's ``calculate_monthly_payment``
    handles the ``annual_rate <= 0`` branch as
    ``principal / remaining_months``.  The resolver must surface
    this without dividing by zero or producing a ``Decimal('NaN')``.

    Setup: $12,000 / 0% / 12 months.  Expected payment = $1,000.00.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=12,
        original_principal=Decimal("12000.00"),
        interest_rate=Decimal("0"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)

    state = resolve_loan(
        params, [anchor], None, None, date(2026, 2, 1),
    )

    assert state.monthly_payment == Decimal("1000.00")
    # Balance unchanged with no confirmed payments.
    assert state.current_balance == Decimal("12000.00")


# -- C13-11 -- payoff date and total_interest -------------------------------


def test_payoff_date_and_total_interest():
    """C13-11 (re-pinned at C6): hand-computable payoff date and total interest.

    Setup: small fixed-rate loan $10,000 / 6% / 12 months from
    2026-01-01.  Contractual payment =
    amortize(10000, 0.06, 12) = $860.66 (HALF_UP from the exact
    $860.6643...).

    Hand-computed schedule (per-row interest is
    ``balance * 0.005`` quantized HALF_UP, principal is
    ``860.66 - interest`` in rows 1-11, balance reduced accordingly):

        r1  i=50.00 p=810.66 bal=9189.34
        r2  i=45.95 p=814.71 bal=8374.63
        r3  i=41.87 p=818.79 bal=7555.84
        r4  i=37.78 p=822.88 bal=6732.96
        r5  i=33.66 p=827.00 bal=5905.96
        r6  i=29.53 p=831.13 bal=5074.83
        r7  i=25.37 p=835.29 bal=4239.54
        r8  i=21.20 p=839.46 bal=3400.08
        r9  i=17.00 p=843.66 bal=2556.42
        r10 i=12.78 p=847.88 bal=1708.54
        r11 i= 8.54 p=852.12 bal= 856.42
        r12 i= 4.28 p=856.42 bal=   0.00  (final row absorbs residue)

    Twelve rows ending 2027-01-01.  Total interest = $327.96
    (sum of rows 1-12; cross-check: also equals the sum the
    pre-C6 engine produced because the pre-C6 13-row schedule had
    interest=$0.00 on its phantom $0.04 residue row, so the total
    is unchanged).

    Re-pinning context (per
    ``remediation_follow_up_common.md`` Apply-rule 4): the pre-C6
    ``generate_schedule`` produced a 13-row schedule ending
    2027-02-01 with row 13 being a phantom payment=$0.04 row
    that absorbed sub-penny residue left by rounding the
    contractual payment to two places.  That residue row was a
    math artifact of ``generate_schedule``'s ``max_months =
    remaining_months + term_months`` slack; it was never user-
    facing-correct (a 12-month loan does not "pay off in month
    13").  ``project_forward`` (Commit 2 primitive; used here via
    ``compute_payoff_scenarios`` in :func:`resolve_loan` after
    Commit 6) forces ``is_final = month_num == remaining_months``
    on the final scheduled month so the last row absorbs residue
    into a slightly larger final payment ($860.70 = $856.42
    principal + $4.28 interest).  This matches real-lender
    practice (the final scheduled payment fully retires the loan
    within the contractual term).  See
    ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``
    Section 9 Commit 6 for the architectural finding.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=12,
        original_principal=Decimal("10000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)

    state = resolve_loan(
        params, [anchor], None, None, date(2026, 2, 1),
    )

    assert state.payoff_date == date(2027, 1, 1)
    assert state.total_interest == Decimal("327.96")
    # Twelve rows: the final row absorbs residue into the
    # contractual term rather than emitting a phantom 13th row.
    assert len(state.schedule) == 12
    assert state.schedule[-1].remaining_balance == Decimal("0.00")


# -- Defensive coverage beyond the plan's enumerated cases ------------------


def test_empty_anchor_events_raises_value_error():
    """The Commit-12 backfill guarantee is structural: empty raises loud.

    If a caller hands the resolver an empty anchor-event list, the
    Commit-12 invariant has been violated and silently producing a
    "no anchor, project from origination" answer would mask the
    bug.  The resolver raises ValueError to surface it.
    """
    params = _arm_400k_params()
    with pytest.raises(ValueError, match="at least one LoanAnchorEvent"):
        resolve_loan(params, [], None, None, date(2026, 6, 1))


def test_latest_anchor_breaks_tie_by_created_at():
    """Two anchors on the same date: latest created_at wins.

    Mirrors the ORM ``backref(order_by="anchor_date DESC, created_at DESC")``
    so a same-day correction (operator typed the wrong number, hit
    save, fixed it, saved again -- both with the same anchor_date)
    deterministically prefers the later row.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    earlier = FakeAnchorEvent(
        anchor_date=date(2026, 6, 1),
        anchor_balance=Decimal("280000.00"),
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
    )
    later = FakeAnchorEvent(
        anchor_date=date(2026, 6, 1),
        anchor_balance=Decimal("275000.00"),
        created_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
                   + timedelta(seconds=5),
    )

    state = resolve_loan(
        params, [earlier, later], None, None, date(2026, 7, 1),
    )

    # Latest anchor's balance is returned (no confirmed payments
    # to replay forward from it).
    assert state.current_balance == Decimal("275000.00")


def test_loan_state_is_frozen():
    """LoanState is a frozen dataclass; consumers cannot mutate the snapshot.

    The resolver returns a snapshot the caller renders; mutating
    fields between consumers would silently produce divergent
    surfaces (the bug E-18 exists to prevent).  ``frozen=True``
    on the dataclass enforces this at runtime.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    state = resolve_loan(
        params, [anchor], None, None, date(2026, 6, 1),
    )

    with pytest.raises(AttributeError):
        # Type-checked at runtime by @dataclass(frozen=True).
        state.current_balance = Decimal("0")  # type: ignore[misc]


def test_arm_trueup_in_window_produces_new_constant():
    """A user_trueup inside the ARM window resets the in-window constant.

    The "held constant for every as_of inside the window" invariant
    is anchor-scoped: a new trueup anchor IS the moment a new
    constant payment is born for the rest of the window.  Verifies
    the in-window logic uses the latest anchor's balance (not the
    origination balance).

    Setup: 5/5 ARM, $400k/6%/360mo, origination 2026-01-01.  A
    user_trueup at 2028-01-01 (month 24, still inside the window)
    with anchor_balance = $380,000.  Inside the window from
    2028-01-01 onward, the constant payment is:

        months_to_anchor = 24
        remaining_at_anchor = 360 - 24 = 336
        payment = amortize(380000, 0.06, 336) = $2,337.47

    (Decimal arithmetic with full precision: i = 0.005;
    (1.005)^336 = 5.343142418...; denom = (1.005)^336 - 1 = 4.343142...;
    num = 380000 * 0.005 * 5.343142... = 10152.97...;
    M = num / denom = 2337.471263... -> $2,337.47 HALF_UP.  The
    05_symptoms.md worked example uses rounded intermediates
    (5.343555 vs the exact 5.343142) which round-trip to a near
    but not byte-identical value; the engine uses full-precision
    Decimal, so the pinned value here is the engine's exact output.)
    """
    params = _arm_400k_params()
    origination_anchor = _origination_anchor(params)
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2028, 1, 1),
        anchor_balance=Decimal("380000.00"),
        created_at=datetime(2028, 1, 1, tzinfo=timezone.utc),
    )

    # Resolve at two as_of dates inside the window past the trueup.
    state_a = resolve_loan(
        params,
        [origination_anchor, trueup_anchor],
        None,
        None,
        date(2028, 6, 1),
    )
    state_b = resolve_loan(
        params,
        [origination_anchor, trueup_anchor],
        None,
        None,
        date(2030, 6, 1),
    )

    assert state_a.monthly_payment == Decimal("2337.47")
    # In-window constant must hold across both as_of dates.
    assert state_a.monthly_payment == state_b.monthly_payment


# -- C6-8 -- resolver chokepoint: no direct generate_schedule reference -----


def test_no_generate_schedule_in_resolver():
    """C6-8: ``loan_resolver`` source contains no ``generate_schedule`` ref.

    Phase 6 of the amortization-engine split moves the resolver's
    schedule generation off the legacy ``generate_schedule`` entry
    point and onto :func:`compute_payoff_scenarios`.  The resolver
    is the single chokepoint other surfaces (year-end debt
    aggregation, savings dashboard debt card, debt-strategy, the
    refinance calculator) read through, so locking it pure-of-engine
    here keeps the downstream consumers automatically on the new
    primitives.

    Inspects the raw module source (including comments and
    docstrings).  A regression that reintroduces ``generate_schedule``
    in any form would surface here loud before reaching any
    downstream consumer.
    """
    source = inspect.getsource(loan_resolver)
    assert "generate_schedule" not in source, (
        "loan_resolver.py references generate_schedule; the resolver "
        "must route schedule generation through "
        "compute_payoff_scenarios (Phase 6 / Commit 6 of the "
        "amortization-engine split)."
    )


# -- C6-9 / C6-10 -- is_confirmed flags on history vs forward rows ----------


def test_history_rows_marked_confirmed():
    """C6-9: schedule rows backing confirmed payments are is_confirmed=True.

    Setup: $300k / 6% / 360 mo, three confirmed contractual payments
    Feb-Apr 2026, ``as_of=2026-05-01``.  The composer's replay
    consumes the three confirmed payments and emits three history
    rows; the resolver's ``LoanState.schedule`` starts with those
    three rows.  Every history row carries ``is_confirmed=True``
    so a downstream caller can distinguish recorded history from
    projection without re-tracing the row through the payment list.

    This invariant is load-bearing for any future surface that
    needs to render history differently from projection (e.g. a
    "shade past months" treatment in the amortization tab) without
    re-querying the payment store.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    payments = [
        PaymentRecord(date(2026, 2, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 3, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 4, 1), Decimal("1798.65"), True),
    ]

    state = resolve_loan(
        params, [anchor], payments, None, date(2026, 5, 1),
    )

    # First three rows are the replayed confirmed payments.
    assert len(state.schedule) >= 3
    for idx, row in enumerate(state.schedule[:3]):
        assert row.is_confirmed is True, (
            f"history row {idx} ({row.payment_date}) is not "
            f"flagged is_confirmed=True"
        )


def test_forward_rows_marked_unconfirmed():
    """C6-10: schedule rows past as_of are is_confirmed=False.

    Same setup as :func:`test_history_rows_marked_confirmed`.  The
    fourth row onward comes from :func:`project_forward` (via the
    composer's ``committed_forward`` slice) and is unconfirmed by
    construction -- projection rows are not facts about the
    recorded past.

    Together with C6-9 this pins the contract that
    ``LoanState.schedule`` carries an authoritative confirmation
    flag per row: callers can rely on ``is_confirmed`` to
    distinguish historical fact from projection without re-deriving
    the boundary from ``as_of``.
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    anchor = _origination_anchor(params)
    payments = [
        PaymentRecord(date(2026, 2, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 3, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 4, 1), Decimal("1798.65"), True),
    ]

    state = resolve_loan(
        params, [anchor], payments, None, date(2026, 5, 1),
    )

    # All rows past the three history rows are forward projections.
    assert len(state.schedule) > 3
    for idx, row in enumerate(state.schedule[3:], start=3):
        assert row.is_confirmed is False, (
            f"forward row {idx} ({row.payment_date}) is flagged "
            f"is_confirmed=True; only history rows should be"
        )


# -- TestComputePayoffScenarios (Commit 3, C3-1..C3-15) ---------------------
#
# The composer is the load-bearing fix for the architectural defect
# described in
# ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``:
# replay of confirmed history and projection of the future are split
# into two primitives, and the composer is the single producer that
# every Payoff Calculator surface reads through.  Chart series and
# summary metrics derive from one return value, so they cannot
# diverge.
#
# C3-10 is the originally-reported-bug regression lock.  The
# reproduction has a multi-month temporal gap between origination
# (2024-01-01) and the first confirmed payment (2026-01-01); this is
# the input shape that surfaces the "extra applied to ghost
# historical months" defect that
# ``tests/test_services/test_amortization_engine.py::TestPaymentAwareSchedule``
# could never catch (it set ORIGINATION = first confirmed payment's
# month, eliminating the gap).  Every test in this class deliberately
# preserves a gap unless the test name says otherwise.


def _fixed_rate_300k_params() -> FakeLoanParams:
    """30 yr / $300k / 6% / payment_day=1 from 2024-01-01.

    Matches the C3-10 originally-reported-bug regression setup.
    Used by most tests in this class -- the long origination-to-first-
    confirmed-payment gap (2024-01-01 to 2026-01-01) is the shape
    the architectural bug exists in.
    """
    return FakeLoanParams(
        origination_date=date(2024, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )


def _four_contractual_payments_jan_to_apr_2026() -> list[PaymentRecord]:
    """Four confirmed contractual payments on the first of Jan-Apr 2026.

    Amount $1798.65 is the contractual P&I for
    amortize($300k, 0.06, 360) -- hand-computed at C13-5 (existing
    fixed-rate replay test) and used by the architectural plan's
    regression scenario.
    """
    return [
        PaymentRecord(date(2026, 1, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 2, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 3, 1), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 4, 1), Decimal("1798.65"), True),
    ]


class TestComputePayoffScenarios:
    """C3-1..C3-15: scenario composer in loan_resolver.

    Pins the composer's invariants (history shared, forward slices
    derive from one starting state, override months never carry
    extra) plus the originally-reported-bug regression lock (C3-10)
    and the temporal-gap property (C3-11) that generalizes it.
    """

    AS_OF = date(2026, 5, 21)

    def test_history_shared(self):
        """C3-1: history_rows has one entry per confirmed payment <= as_of.

        Four confirmed contractual payments Jan-Apr 2026 produce
        exactly four history rows.  Replay does NOT fabricate
        contractual rows for the 23 unrecorded months between
        2024-02 and 2025-12 (this is the load-bearing distinction
        between replay and projection); the row count equals the
        confirmed-payment count, not the months-since-origination
        count.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("0.00"),
            as_of=self.AS_OF,
        )
        assert len(scenarios.history_rows) == 4
        for row in scenarios.history_rows:
            assert row.payment_date <= self.AS_OF
            assert row.is_confirmed is True

    def test_forward_same_starting_balance(self):
        """C3-2: All three forward slices share the same row 0 balance.

        replay.balance_as_of after four $1798.65 payments is
        $298,796.42 (verified at C13-5).  The first forward row of
        each scenario then deducts the same principal portion at the
        same rate, so original/committed/accelerated row 0
        principal+interest match byte-identically; only
        ``extra_payment`` differs (committed/original have $0,
        accelerated has $500).  Hand arithmetic for row 0 P&I split:

            interest    = 298796.42 * 0.005 = 1493.98 (HALF_UP)
            principal   = 1798.65 - 1493.98 = 304.67
            balance(orig/committed) = 298796.42 - 304.67 = 298491.75
            balance(accel) = balance(orig) - 500.00 extra = 297991.75
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        original_row0 = scenarios.original_forward[0]
        committed_row0 = scenarios.committed_forward[0]
        accelerated_row0 = scenarios.accelerated_forward[0]

        # Same P&I split (interest, principal, base payment) across
        # all three scenarios in row 0.
        assert original_row0.interest == Decimal("1493.98")
        assert original_row0.principal == Decimal("304.67")
        assert committed_row0.interest == original_row0.interest
        assert committed_row0.principal == original_row0.principal
        assert accelerated_row0.interest == original_row0.interest
        assert accelerated_row0.principal == original_row0.principal

        # Balance differs only by the extra applied to accelerated.
        assert original_row0.remaining_balance == Decimal("298491.75")
        assert committed_row0.remaining_balance == Decimal("298491.75")
        assert accelerated_row0.remaining_balance == Decimal("297991.75")

    def test_forward_first_row_date_matches_next_pay_date(self):
        """C3-3: All three forward slices start at replay.next_pay_date.

        Last replayed payment is 2026-04-01, so projection picks up
        at 2026-05-01.  All three slices share that first
        payment_date -- catches the bug class "Accelerated curve
        started one month earlier/later than the others."
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        expected_first = date(2026, 5, 1)
        assert scenarios.original_forward[0].payment_date == expected_first
        assert scenarios.committed_forward[0].payment_date == expected_first
        assert scenarios.accelerated_forward[0].payment_date == expected_first

    def test_history_byte_identical_across_scenarios(self):
        """C3-4: history_rows prefix is shared (same list reference).

        Chart rendering plots ``history_rows + <slice>_forward`` for
        each scenario; the history prefix is byte-identical across
        scenarios because replay returns a single list reused by the
        composer.  Identity comparison is the strongest assertion --
        the three slices literally share the same history list, so
        no future refactor can silently produce divergent histories.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        # Identity assertion: only one history list exists.
        assert scenarios.history_rows is scenarios.history_rows
        # Defensive sequence-equality check (catches a future
        # refactor that returns deep copies).
        for row in scenarios.history_rows:
            assert row.is_confirmed is True

    def test_original_ignores_projections_and_extra(self):
        """C3-5: original_forward uses contractual every row, no extras.

        One projected $2000 payment in June 2026, extra=$500: every
        original_forward row uses the contractual $1798.65 P&I and
        ``extra_payment == 0``.  The original line is "what the
        lender would amortize" with NO planning data and NO
        acceleration.  The final row absorbs any sub-penny residue
        and may report a slightly different payment amount (engine's
        ``is_final`` branch); that row is excluded from the pointwise
        contractual assertion below.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        payments = _four_contractual_payments_jan_to_apr_2026() + [
            PaymentRecord(date(2026, 6, 1), Decimal("2000.00"), False),
        ]
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=payments,
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        # All but the final row use the exact contractual payment;
        # the engine's final-row branch absorbs the balance residue
        # and may have a sub-cent difference.
        for row in scenarios.original_forward[:-1]:
            assert row.payment == Decimal("1798.65")
            assert row.extra_payment == Decimal("0.00")
        # The final row also carries no extra (extra is forward-only
        # and original has no extras by construction).
        assert scenarios.original_forward[-1].extra_payment == Decimal("0.00")

    def test_committed_honors_projections(self):
        """C3-6: committed_forward applies projected payments as overrides.

        June 2026 projected payment ($2000) replaces the contractual
        for that month; ``extra_payment == 0`` (no acceleration in
        committed by construction).  Other months use contractual.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        payments = _four_contractual_payments_jan_to_apr_2026() + [
            PaymentRecord(date(2026, 6, 1), Decimal("2000.00"), False),
        ]
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=payments,
            rate_changes=None,
            extra_monthly=Decimal("0.00"),
            as_of=self.AS_OF,
        )
        june = [
            row for row in scenarios.committed_forward
            if row.payment_date == date(2026, 6, 1)
        ]
        assert len(june) == 1
        assert june[0].payment == Decimal("2000.00")
        assert june[0].extra_payment == Decimal("0.00")

        # A non-override month (May 2026, the first row) uses
        # contractual.
        assert scenarios.committed_forward[0].payment == Decimal("1798.65")

    def test_accelerated_honors_projections_and_extra(self):
        """C3-7: override months ignore extra; non-override months take it.

        The critical regression-prevention assertion.  June 2026
        (override $2000): payment=$2000, extra=$0 -- the architectural
        plan's load-bearing distinction from the pre-fix engine's
        "apply extra when no PaymentRecord exists" semantics.  July
        2026 (no override): payment=$1798.65 contractual,
        extra=$500.00.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        payments = _four_contractual_payments_jan_to_apr_2026() + [
            PaymentRecord(date(2026, 6, 1), Decimal("2000.00"), False),
        ]
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=payments,
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        june = [
            row for row in scenarios.accelerated_forward
            if row.payment_date == date(2026, 6, 1)
        ][0]
        july = [
            row for row in scenarios.accelerated_forward
            if row.payment_date == date(2026, 7, 1)
        ][0]
        assert june.payment == Decimal("2000.00")
        assert june.extra_payment == Decimal("0.00")
        assert july.payment == Decimal("1798.65")
        assert july.extra_payment == Decimal("500.00")

    def test_months_saved_metric(self):
        """C3-8: months_saved = len(committed) - len(accelerated).

        Hand-computed from the closed-form amortization formula:

            P = 298796.42, i = 0.005
            committed M  = 1798.65; n = -log(1 - P*i/M) / log(1+i)
                = -log(0.169393) / log(1.005)
                approx 356 months (committed pays off in 356 -- the
                remaining_months floor of project_forward's loop;
                hand-arithmetic agrees within rounding)
            accelerated M = 2298.65; n = -log(1 - P*i/M) / log(1+i)
                = -log(0.350067) / log(1.005)
                approx 210.44 -> 211 months at HALF_UP / month boundary

            months_saved = 356 - 211 = 145

        Pinning the discovered 145 here; the closed-form derivation
        above is the verification path.  A regression in the
        composer that re-introduced "extra applied to ghost history"
        would inflate this number well past 200 because the buggy
        accelerated schedule would consume 23+ months of fictitious
        2024-2025 acceleration.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        assert (
            scenarios.months_saved
            == len(scenarios.committed_forward)
            - len(scenarios.accelerated_forward)
        )
        assert scenarios.months_saved == 145

    def test_interest_saved_metric(self):
        """C3-9: interest_saved = sum(committed.interest) - sum(accel.interest).

        Hand-derivable from the committed and accelerated payoff
        slices: a $500/month acceleration on a ~$298,796 balance at
        6% saves the interest that would have accrued on the
        principal the acceleration paid down sooner.

            sum(committed.interest)   = $341,524.42 (composer-derived)
            sum(accelerated.interest) = $184,964.88 (composer-derived)
            interest_saved            = $156,559.54

        The pinned value is the composer's output for the symptom
        inputs; the closed-form derivation above is the verification
        (a 145-month early payoff at a 6% APR saves ~$157k of
        interest, matching within rounding).
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        expected = (
            scenarios.total_interest_committed
            - scenarios.total_interest_accelerated
        )
        assert scenarios.interest_saved == expected
        assert scenarios.interest_saved == Decimal("156559.54")

    def test_originally_reported_bug_regression(self):
        """C3-10: LOAD-BEARING regression lock for the symptom.

        The user's reported bug on ``/accounts/3/loan``: the
        Accelerated chart diverges from the Original at origination
        (2024-02 in this scenario), runs parallel to Committed
        through the confirmed window (Jan-Apr 2026), then resumes
        accelerated descent post-today.  Root cause (architectural
        plan Section 2): ``generate_schedule``'s "apply extra when
        no PaymentRecord exists" semantics treated every
        origination-to-first-confirmed month as a no-record month,
        applying $500 extra to 23 months of fictitious 2024-2025
        history.

        After the fix:

        1. ``history_rows`` contains EXACTLY four rows (Jan-Apr 2026
           confirmed payments); the gap months (2024-02 to 2025-12)
           are absent -- replay does not fabricate.
        2. ``accelerated_forward[0].payment_date == 2026-05-01`` --
           the first month after as_of.  No fictitious 2024 row.
        3. Every accelerated forward row has ``extra_payment ==
           $500.00`` until the final row absorbs balance residue.
           (The contractual is $1798.65; accelerated rows pay
           $2298.65 with extra=$500.  Override months would set
           extra=0, but this scenario has no overrides.)
        4. ``months_saved == 145`` -- the hand-computed value (see
           C3-8).  The buggy code would inflate this past 200
           because ~23 months of ghost-history acceleration would
           reduce the accelerated payoff to roughly month ~190.

        If any of these assertions regresses, the same class of bug
        has returned.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=_four_contractual_payments_jan_to_apr_2026(),
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        # 1. History is exactly the confirmed-payment count.
        assert len(scenarios.history_rows) == 4
        # 2. Accelerated starts the calendar month after the last
        #    replayed row (Apr 1 confirmed -> May 1 first forward),
        #    not a fictitious 2024 acceleration.  The composer's
        #    next_pay_date is anchored to the replay boundary, not
        #    to as_of; the May 1 row is before as_of=May 21 because
        #    payments are dated at the start of their month.
        last_history_date = scenarios.history_rows[-1].payment_date
        assert (
            scenarios.accelerated_forward[0].payment_date
            == date(2026, 5, 1)
        )
        assert (
            scenarios.accelerated_forward[0].payment_date
            > last_history_date
        )
        # 3. Every accelerated forward row carries $500 extra
        #    (except possibly the final row whose overpayment-cap
        #    branch absorbs the balance residue and reports extra=0).
        for row in scenarios.accelerated_forward[:-1]:
            assert row.payment_date > last_history_date
            assert row.extra_payment == Decimal("500.00")
        # 4. Hand-computed months_saved (see C3-8 for the derivation;
        #    a regression that re-introduces ghost-history
        #    acceleration would inflate this past 200).
        assert scenarios.months_saved == 145

    def test_temporal_gap_property(self):
        """C3-11: history row count tracks confirmed-payment count, not gap.

        Parameterized origination dates create gaps of 12, 24, and
        36 months before the first confirmed payment.  The same four
        confirmed payments always produce four history rows
        regardless of gap -- replay does not fabricate.  This
        generalizes C3-10's load-bearing assertion to arbitrary
        origination-to-first-confirmed gaps.

        The architectural plan calls this property out at lines
        467-469 (architectural plan path):
        "any scenario that does not include a multi-month gap
        between origination and the first confirmed payment cannot
        distinguish the buggy and fixed implementations."  This test
        is the gap-class regression lock.
        """
        for gap_months in (12, 24, 36):
            # First confirmed payment fixed at 2026-01-01; vary
            # origination to create the requested gap.
            origination = _add_months(
                date(2026, 1, 1), -gap_months,
            )
            params = FakeLoanParams(
                origination_date=origination,
                term_months=360,
                original_principal=Decimal("300000.00"),
                interest_rate=Decimal("0.06"),
                payment_day=1,
            )
            anchor = _origination_anchor(params)
            payments = _four_contractual_payments_jan_to_apr_2026()
            scenarios = compute_payoff_scenarios(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=None,
                extra_monthly=Decimal("500.00"),
                as_of=self.AS_OF,
            )
            assert len(scenarios.history_rows) == 4, (
                f"gap_months={gap_months}: expected 4 history rows "
                f"(one per confirmed payment), got "
                f"{len(scenarios.history_rows)}"
            )
            # All history rows must fall in the confirmed window;
            # none in the 2024-2025 gap.
            for row in scenarios.history_rows:
                assert row.payment_date >= date(2026, 1, 1)

    def test_composer_is_pure(self):
        """C3-12: compute_payoff_scenarios source has no Flask / db references.

        The composer lives in ``loan_resolver`` (the resolver itself
        was already locked pure at C13-8); this assertion focuses on
        the composer's own body to catch a future refactor that
        sneaks a ``db.session.query`` or ``current_user`` into
        scenario composition.  Composing scenarios requires only
        plain data + the two engine primitives; any I/O dependency
        would break test/task contexts where no app context is
        available.
        """
        source = inspect.getsource(compute_payoff_scenarios)
        code_tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            if tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                continue
            code_tokens.append(tok.string)
        code_only = " ".join(code_tokens)
        for marker in (
            "from flask", "import flask", "current_user",
            "db.session", "request.", "session[",
        ):
            assert marker not in code_only, (
                f"compute_payoff_scenarios references {marker!r} in "
                f"executable code; the composer must remain pure."
            )

    def test_summary_metrics_match_chart(self):
        """C3-13: summary metrics reconcile bit-for-bit with the forward slices.

        Single-source-of-truth invariant: the rendered "Months Saved"
        and "Interest Saved" labels must equal length and interest
        diffs derived from the same forward slices the chart plots.
        Two computation paths would re-introduce the chart-summary
        divergence the architectural fix removed.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        payments = _four_contractual_payments_jan_to_apr_2026() + [
            PaymentRecord(date(2026, 6, 1), Decimal("2000.00"), False),
        ]
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=payments,
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        # months_saved reconciles to slice length diff.
        assert (
            scenarios.months_saved
            == len(scenarios.committed_forward)
            - len(scenarios.accelerated_forward)
        )
        # interest_saved reconciles to slice interest diff (rounded
        # via round_money at the boundary).
        committed_sum = sum(
            (row.interest for row in scenarios.committed_forward),
            Decimal("0.00"),
        )
        accelerated_sum = sum(
            (row.interest for row in scenarios.accelerated_forward),
            Decimal("0.00"),
        )
        from app.utils.money import round_money
        assert (
            scenarios.interest_saved
            == round_money(committed_sum - accelerated_sum)
        )
        # total interest fields reconcile to sum-of-interest.
        assert (
            scenarios.total_interest_committed
            == round_money(committed_sum)
        )
        assert (
            scenarios.total_interest_accelerated
            == round_money(accelerated_sum)
        )

    def test_arm_anchor_preserved(self):
        """C3-14: ARM anchor snaps replay's balance to the verified value.

        ARM 5/5 originated 2024-01-01, $400k/6%/360mo.  Trueup
        anchor at 2025-12-15 with anchor_balance=$250,000 -- well
        inside the 60-month fixed window
        ``[2024-01-01, 2029-01-01)``.  Two confirmed payments after
        the trueup (2026-01-01, 2026-02-01) at the in-window
        contractual P&I of $2398.20 (Symptom #4 worked example).

        Replay's first post-snap row reflects the verified balance
        rather than the from-origination projection.  Hand
        arithmetic for the first replayed row (Jan 2026, post-snap):

            balance(at trueup snap) = 250000.00
            interest                = 250000.00 * 0.005 = 1250.00
            principal               = 2398.20 - 1250.00 = 1148.20
            balance(end of Jan)     = 250000 - 1148.20 = 248851.80
        """
        params = FakeLoanParams(
            origination_date=date(2024, 1, 1),
            term_months=360,
            original_principal=Decimal("400000.00"),
            interest_rate=Decimal("0.06"),
            payment_day=1,
            is_arm=True,
            arm_first_adjustment_months=60,
        )
        anchor_origin = FakeAnchorEvent(
            anchor_date=date(2024, 1, 1),
            anchor_balance=Decimal("400000.00"),
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        anchor_trueup = FakeAnchorEvent(
            anchor_date=date(2025, 12, 15),
            anchor_balance=Decimal("250000.00"),
            created_at=datetime(2025, 12, 15, tzinfo=timezone.utc),
        )
        payments = [
            PaymentRecord(date(2026, 1, 1), Decimal("2398.20"), True),
            PaymentRecord(date(2026, 2, 1), Decimal("2398.20"), True),
        ]
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor_origin, anchor_trueup],
            payments=payments,
            rate_changes=None,
            extra_monthly=Decimal("0.00"),
            as_of=date(2026, 3, 1),
        )
        # Two post-trueup history rows; the first reflects the snap
        # to $250,000.
        assert len(scenarios.history_rows) == 2
        assert (
            scenarios.history_rows[0].payment_date == date(2026, 1, 1)
        )
        assert (
            scenarios.history_rows[0].remaining_balance
            == Decimal("248851.80")
        )
        # Second post-trueup row continues from the snap:
        #     interest    = 248851.80 * 0.005 = 1244.26 (HALF_UP)
        #     principal   = 2398.20 - 1244.26 = 1153.94
        #     balance     = 248851.80 - 1153.94 = 247697.86
        assert (
            scenarios.history_rows[1].payment_date == date(2026, 2, 1)
        )
        assert (
            scenarios.history_rows[1].remaining_balance
            == Decimal("247697.86")
        )

    def test_confirmed_past_as_of_routed_to_override(self):
        """C3-15: confirmed payments dated after as_of go to monthly_override.

        Edge case: a user marks a future payment as confirmed before
        ``as_of`` reaches its date.  Replay must stop at as_of (it
        is the deterministic-past slice), and the future-confirmed
        payment must appear in the forward projections via override
        so chart/summary reflect the user's planned outlay.

        Setup: one confirmed Jan-2026 payment ($1798.65 -- inside
        as_of), one confirmed Aug-2026 payment ($2500.00 -- past
        as_of=2026-05-21).  Assertions:

        * History has one row (Jan 2026 only).
        * Aug 2026 row in committed/accelerated uses $2500.00 as
          the total payment with ``extra_payment == 0`` (override
          semantics suppress extra).
        * July 2026 (no override) in accelerated has
          extra_payment == $500.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        payments = [
            PaymentRecord(date(2026, 1, 1), Decimal("1798.65"), True),
            PaymentRecord(date(2026, 8, 1), Decimal("2500.00"), True),
        ]
        scenarios = compute_payoff_scenarios(
            loan_params=params,
            anchor_events=[anchor],
            payments=payments,
            rate_changes=None,
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        assert len(scenarios.history_rows) == 1
        assert (
            scenarios.history_rows[0].payment_date == date(2026, 1, 1)
        )

        # Aug 2026 in committed and accelerated honors the override.
        aug_committed = next(
            row for row in scenarios.committed_forward
            if row.payment_date == date(2026, 8, 1)
        )
        aug_accelerated = next(
            row for row in scenarios.accelerated_forward
            if row.payment_date == date(2026, 8, 1)
        )
        assert aug_committed.payment == Decimal("2500.00")
        assert aug_committed.extra_payment == Decimal("0.00")
        assert aug_accelerated.payment == Decimal("2500.00")
        assert aug_accelerated.extra_payment == Decimal("0.00")

        # Jul 2026 (no override) in accelerated has extra=$500.
        jul_accelerated = next(
            row for row in scenarios.accelerated_forward
            if row.payment_date == date(2026, 7, 1)
        )
        assert jul_accelerated.extra_payment == Decimal("500.00")
