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
from app.services.loan_resolver import LoanState, resolve_loan


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
    """C13-11: Schedule yields a hand-computable payoff date and total interest.

    Setup: small fixed-rate loan $10,000 / 6% / 12 months from
    2026-01-01.  Contractual payment = amortize(10000, 0.06, 12)
    = $860.66.  With rounding residue absorbed in the final row,
    the engine produces a 13-row schedule ending 2027-02-01.
    Life-of-loan interest sums to $327.96 (verified by walking the
    engine output: each row's interest is balance * 0.005 quantized
    HALF_UP; the cumulative sum is the schedule's total_interest).
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

    assert state.payoff_date == date(2027, 2, 1)
    assert state.total_interest == Decimal("327.96")


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
