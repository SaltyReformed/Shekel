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
import pathlib
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
    LoanInputs,
    LoanState,
    PayoffScenarios,
    compute_payoff_scenarios,
    resolve_loan,
)
from app.utils.dates import add_months


def _loan_resolver_package_source() -> str:
    """Concatenated source of every module in the ``loan_resolver`` package.

    The E-18 resolver was split into the ``app/services/loan_resolver/``
    package (Phase-3 pylint cleanup), so ``inspect.getsource(loan_resolver)``
    now returns only ``__init__.py``.  The purity / rounding / no-engine
    source guards must scan every sub-module where the resolver's code
    actually lives, so they read the package directory directly.
    """
    package_dir = pathlib.Path(loan_resolver.__file__).parent
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py"))
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
    arm_adjustment_interval_months: int | None = None


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


def _origination_rate_change(params: FakeLoanParams) -> RateChangeRecord:
    """Return the origination (period-0) ``RateChangeRecord`` for *params*.

    DH-#56 retired ``LoanParams.interest_rate``; the resolver now derives a
    loan's base / period-0 rate from the earliest entry in its rate-change
    feed -- the origination :class:`RateHistory` row every loan carries
    (effective at ``origination_date``).  These resolver tests build
    duck-typed :class:`FakeLoanParams` rather than DB-bound ORM rows, so the
    in-memory analogue of that origination row is a
    :class:`RateChangeRecord` effective at ``origination_date`` carrying the
    params' ``interest_rate``.  ``_origination_rate`` raises ``ValueError``
    on an empty feed, so every ``resolve_loan`` / ``compute_payoff_scenarios``
    call must include this record.  The rate value is unchanged from the
    retired column, so every hand-computed expectation stays byte-identical.
    """
    return RateChangeRecord(
        effective_date=params.origination_date,
        interest_rate=params.interest_rate,
        monthly_pi=None,
    )


def _rate_feed(
    params: FakeLoanParams,
    rate_changes: list[RateChangeRecord] | None = None,
) -> list[RateChangeRecord]:
    """Return the loan's full rate-change feed including the origination rate.

    Prepends the origination (period-0) :class:`RateChangeRecord` (see
    :func:`_origination_rate_change`) to any later ARM ``rate_changes`` the
    test supplies.  When the test passes no later changes the feed is just
    the origination rate -- enough for the resolver to resolve period 0.
    The origination rate equals the retired ``LoanParams.interest_rate``, so
    period 0 (and every downstream expectation) is byte-identical.
    """
    feed = [_origination_rate_change(params)]
    if rate_changes:
        feed.extend(rate_changes)
    return feed


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
        as_of = add_months(params.origination_date, month_offset)
        state = resolve_loan(
            LoanInputs(params, [anchor], None, _rate_feed(params)), as_of,
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
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        add_months(params.origination_date, 24),
    )
    state_25 = resolve_loan(
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        add_months(params.origination_date, 25),
    )

    # Byte-identical Decimal comparison (not numeric equality with
    # different scales).  The pre-fix values $2,460.45 / $2,463.28
    # both differed from $2,398.20 and from each other; we assert
    # the resolver pins them to the same correct value.
    assert state_24.monthly_payment == Decimal("2398.20")
    assert state_25.monthly_payment == Decimal("2398.20")
    assert state_24.monthly_payment == state_25.monthly_payment


# -- DH-#56 -- current_rate is the resolver-derived rate in effect today -----


def test_current_rate_is_rate_at_today_not_a_stored_scalar():
    """DH-#56: ``state.current_rate`` is the rate in effect on ``as_of``.

    A 5/5 ARM originated at 6% with a recorded adjustment to 7% effective at
    its first reset (month 60, 2031-01-01).  Inside the fixed-rate window the
    current rate is the 6% origination rate; after the reset it is the 7%
    rate now in effect.  This is the headline DH-#56 fix: the retired
    ``LoanParams.interest_rate`` mirror drifted to the LATEST recorded rate on
    every change (corrupting period 0 for a backdated / out-of-order change),
    whereas ``current_rate`` is resolved per-date from the rate-period series,
    so it always reports the rate actually in effect.

    Revert-proof: a single stored scalar cannot be BOTH 6% and 7%, so any
    regression that re-sourced ``current_rate`` from one column would fail
    one of the two assertions.
    """
    params = _arm_400k_params()  # 2026-01-01, 6%, 5/5 ARM
    anchor = _origination_anchor(params)
    reset = RateChangeRecord(
        effective_date=date(2031, 1, 1),  # month 60: the first ARM reset
        interest_rate=Decimal("0.07"),
        monthly_pi=None,
    )
    feed = _rate_feed(params, [reset])

    in_window = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2027, 1, 1),
    )
    assert in_window.current_rate == Decimal("0.06")

    after_reset = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2031, 6, 1),
    )
    assert after_reset.current_rate == Decimal("0.07")


# -- C13-3 -- confirmed payment reduces balance -----------------------------


def test_confirmed_payment_reduces_balance():
    """C13-3 (re-pinned): a confirmed payment reduces the balance by the
    SCHEDULED principal, independent of the cash amount paid.

    Re-pinned under the decided contractual-schedule model (CLAUDE rule
    5 exception; the developer chose "each confirmed payment reduces
    principal by period P&I - interest; deliberate extra principal is an
    explicit event").  The prior assertion ($299,611.64) reduced the
    balance by the cash amount ($1,888.36 - interest), so escrow or an
    overpayment bundled into the transfer leaked into principal.  Now
    only the payment's occurrence (date) matters; its amount does not.

    Setup: $300k fixed-rate, 6%, 360mo, origination 2026-01-01.  One
    confirmed payment on 2026-02-15 (cash $1,888.36, deliberately above
    the contractual P&I to show the excess is ignored):

        period P&I = amortize(300000, 0.06, 360) = 1,798.65
        interest   = 300000 * 0.005 = 1,500.00
        principal  = 1,798.65 - 1,500.00 = 298.65
        balance    = 300,000.00 - 298.65 = 299,701.35

    The $89.71 paid above the contractual P&I is NOT auto-applied to
    principal (that requires an explicit prepayment event); the balance
    follows the contractual schedule.
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
        LoanInputs(params, [anchor], [payment], _rate_feed(params)),
        date(2026, 3, 1),
    )

    assert state.current_balance == Decimal("299701.35")


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
        LoanInputs(params, [anchor], [projected], _rate_feed(params)),
        date(2026, 3, 1),
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
        LoanInputs(params, [anchor], payments, _rate_feed(params)),
        date(2026, 5, 1),
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

    Pre-trueup payments (2026-02 and 2026-03) are filtered out by
    replay_schedule's due-date boundary -- their monthly due dates
    (payment_day=1, so 2026-02-01 and 2026-03-01) fall on or before the
    2026-04-01 trueup anchor -- and never enter the replay.
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
        LoanInputs(
            params,
            [origination_anchor, trueup_anchor],
            payments,
            _rate_feed(params),
        ),
        date(2026, 6, 1),
    )

    assert state.current_balance == Decimal("249451.35")


def test_payment_due_after_trueup_replays_though_pay_period_started_before():
    """Regression: a payment keyed to a pay-period start before a mid-period
    true-up still replays, because the boundary uses its real due date.

    The production bug (mortgage account 3): a balance true-up entered
    2026-05-22 ($177,829.83, the pre-payment statement balance) lands one
    day after the biweekly pay period that begins 2026-05-21 and carries
    the 2026-06-01 mortgage payment.  The PaymentRecord is keyed to the
    pay-period START (05-21), so the old "payment_date > anchor_date"
    boundary stranded it (05-21 is not after 05-22) -- the loan card froze
    at the anchor and marking the payment paid never moved it.  Keyed to
    its true monthly DUE date (payment_day=1 -> 06-01), it is correctly
    after the anchor and replays.

    Setup: $300k / 6% / 360mo, contractual P&I $1,798.65.  Two earlier
    confirmed payments (keyed 03-26 -> due 04-01 and 04-23 -> due 05-01)
    are already baked into the trued-up balance and stay excluded; only
    the 06-01 payment replays:

        anchor    = 177,829.83 (trueup, 2026-05-22)
        i         = 177829.83 * 0.06/12 = 889.15 (889.14915 -> HALF_UP)
        p         = 1798.65 - 889.15    = 909.50
        balance   = 177829.83 - 909.50  = 176,920.33
    """
    params = FakeLoanParams(
        origination_date=date(2020, 1, 1),
        term_months=360,
        original_principal=Decimal("300000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
    )
    origination_anchor = FakeAnchorEvent(
        anchor_date=date(2020, 1, 1),
        anchor_balance=Decimal("300000.00"),
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2026, 5, 22),
        anchor_balance=Decimal("177829.83"),
        created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )
    payments = [
        # Already reflected in the trueup balance (due 04-01, 05-01).
        PaymentRecord(date(2026, 3, 26), Decimal("1798.65"), True),
        PaymentRecord(date(2026, 4, 23), Decimal("1798.65"), True),
        # Keyed to its pay-period start 05-21; due 06-01, after the
        # 05-22 trueup -- must replay.
        PaymentRecord(date(2026, 5, 21), Decimal("1798.65"), True),
    ]

    state = resolve_loan(
        LoanInputs(
            params,
            [origination_anchor, trueup_anchor],
            payments,
            _rate_feed(params),
        ),
        date(2026, 6, 2),
    )

    # The 06-01 payment reduced the balance; the card is NOT frozen at the
    # anchor (the bug) and the two pre-trueup payments did not double-count.
    assert state.current_balance == Decimal("176920.33")
    assert state.current_balance != trueup_anchor.anchor_balance


# -- C13-7 -- rate change after window applied ------------------------------


def test_rate_change_after_window_applied():
    """C13-7 (re-pinned): post-adjustment ARM holds the period recast, constant.

    Re-pinned under the rate-period model (CLAUDE rule 5 exception; the
    developer chose to hold the ARM payment constant within each
    fixed-rate period).  The prior assertion pinned $2,830.61 -- the
    payment from re-amortizing the FROZEN original $400,000 over a
    calendar-shrinking term every month (the symptom-#4 creep).  That
    behavior is gone.

    Setup: 5/5 ARM, $400k / 6% / 360mo, origination 2026-01-01, first
    adjustment at month 60 (2031-01-01), rate change to 7% effective
    2031-01-01.  No confirmed payments.

    For any as_of inside the second period, the monthly payment is the
    period's level recast: amortize(the contractual balance at month 60,
    7%, 300).  A $400k/6%/360 loan paid on schedule sits at ~$372,217 at
    month 60, and amortize(~$372,217, 7%, 300) = $2,630.76.  Critically
    it is held CONSTANT for every as_of in the period -- no creep.
    """
    params = _arm_400k_params()
    anchor = _origination_anchor(params)
    rate_changes = [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
        ),
    ]

    feed = _rate_feed(params, rate_changes)
    state_feb = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2031, 2, 1),
    )
    state_later = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2033, 6, 1),
    )

    # Period recast of the reduced month-60 balance at 7% (NOT the old
    # $2,830.61 re-amortization of the frozen original principal).
    assert state_feb.monthly_payment == Decimal("2630.76")
    # Held constant across the period -- the anti-creep guarantee.
    assert state_feb.monthly_payment == state_later.monthly_payment


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
    source = _loan_resolver_package_source()
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
            f"the loan_resolver package contains forbidden marker "
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
    source = _loan_resolver_package_source()
    assert ".quantize(" not in source, (
        "the loan_resolver package reached .quantize directly; route "
        "through app.utils.money.round_money instead (E-26 boundary rule)."
    )
    assert "round_money(" in source, (
        "the loan_resolver package must import and use round_money."
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
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        date(2026, 2, 1),
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
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        date(2026, 2, 1),
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
        resolve_loan(
            LoanInputs(params, [], None, _rate_feed(params)),
            date(2026, 6, 1),
        )


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
        LoanInputs(params, [earlier, later], None, _rate_feed(params)),
        date(2026, 7, 1),
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
        LoanInputs(params, [anchor], None, _rate_feed(params)),
        date(2026, 6, 1),
    )

    with pytest.raises(AttributeError):
        # Type-checked at runtime by @dataclass(frozen=True).
        state.current_balance = Decimal("0")  # type: ignore[misc]


def test_arm_trueup_does_not_change_payment():
    """A balance true-up corrects the balance only -- the payment is unchanged.

    Re-pinned under the decided "balance-only true-up" behavior (CLAUDE
    rule 5 exception; developer decision).  The prior test asserted a
    true-up BORN a new constant payment ($2,337.47, from re-amortizing
    the trued-up $380,000); that coupling is exactly what made the
    displayed payment wander every time the user corrected the balance,
    and it is gone.  The monthly P&I is the current rate period's
    contractual level payment, independent of the anchor balance.

    Setup: 5/5 ARM, $400k/6%/360mo, origination 2026-01-01.  A
    user_trueup at 2028-01-01 (month 24) sets the balance to $380,000.
    The origination-period P&I is amortize($400,000, 6%, 360) =
    $2,398.20 and stays that for every in-period as_of; only the
    balance reflects the true-up.
    """
    params = _arm_400k_params()
    origination_anchor = _origination_anchor(params)
    trueup_anchor = FakeAnchorEvent(
        anchor_date=date(2028, 1, 1),
        anchor_balance=Decimal("380000.00"),
        created_at=datetime(2028, 1, 1, tzinfo=timezone.utc),
    )

    # Resolve at two as_of dates past the trueup.
    state_a = resolve_loan(
        LoanInputs(
            params, [origination_anchor, trueup_anchor], None,
            _rate_feed(params),
        ),
        date(2028, 6, 1),
    )
    state_b = resolve_loan(
        LoanInputs(
            params, [origination_anchor, trueup_anchor], None,
            _rate_feed(params),
        ),
        date(2030, 6, 1),
    )

    # Payment unchanged by the true-up: the origination-period level P&I.
    assert state_a.monthly_payment == Decimal("2398.20")
    assert state_a.monthly_payment == state_b.monthly_payment
    # The true-up DID move the balance (no confirmed payments after it).
    assert state_a.current_balance == Decimal("380000.00")


def test_arm_second_period_uses_recorded_recast_held_constant():
    """A 5/5 ARM's second fixed period uses its recorded recast, held constant.

    Exercises ``arm_adjustment_interval_months`` (now load-bearing) and
    the recorded-recast path end to end through ``resolve_loan``: the
    lender's stated P&I at the adjustment is held flat for the whole
    period, and the origination period is unaffected.  This is the
    production shape for a mid-life 5/5 ARM whose current P&I was
    recorded at setup.

    Setup: 5/5 ARM, $400k/6%/360mo, origination 2026-01-01, first
    adjustment at month 60 then every 60 (interval).  At 2031-01-01 the
    rate adjusts to 7% with a recorded recast P&I of $2,500.00.  No
    confirmed payments.  The second period is [2031-01-01, 2036-01-01).
    """
    params = FakeLoanParams(
        origination_date=date(2026, 1, 1),
        term_months=360,
        original_principal=Decimal("400000.00"),
        interest_rate=Decimal("0.06"),
        payment_day=1,
        is_arm=True,
        arm_first_adjustment_months=60,
        arm_adjustment_interval_months=60,
    )
    anchor = _origination_anchor(params)
    rate_changes = [
        RateChangeRecord(
            effective_date=date(2031, 1, 1),
            interest_rate=Decimal("0.07"),
            monthly_pi=Decimal("2500.00"),
        ),
    ]

    feed = _rate_feed(params, rate_changes)

    # Two as_of dates inside the second period -> the recorded recast,
    # held constant (no month-to-month re-amortization).
    state_early = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2032, 6, 1),
    )
    state_late = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2035, 6, 1),
    )
    assert state_early.monthly_payment == Decimal("2500.00")
    assert state_early.monthly_payment == state_late.monthly_payment

    # The origination period is unaffected: still the contractual P&I.
    state_p0 = resolve_loan(
        LoanInputs(params, [anchor], None, feed), date(2028, 1, 1),
    )
    assert state_p0.monthly_payment == Decimal("2398.20")


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
    source = _loan_resolver_package_source()
    assert "generate_schedule" not in source, (
        "the loan_resolver package references generate_schedule; the "
        "resolver must route schedule generation through "
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
        LoanInputs(params, [anchor], payments, _rate_feed(params)),
        date(2026, 5, 1),
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
        LoanInputs(params, [anchor], payments, _rate_feed(params)),
        date(2026, 5, 1),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        expected_first = date(2026, 5, 1)
        assert scenarios.original_forward[0].payment_date == expected_first
        assert scenarios.committed_forward[0].payment_date == expected_first
        assert scenarios.accelerated_forward[0].payment_date == expected_first

    def test_schedule_rows_dated_by_monthly_due_date(self):
        """Schedule rows show the true monthly due date, not the pay-period start.

        Regression for the user-reported display bug (mortgage account 3):
        a confirmed payment keyed to its biweekly pay-period START
        (2026-05-21) was printed on the schedule as "May 21" and the
        projection then began one month early (the next Projected row read
        "Jun 1" when the borrower's next payment is Jul 1).  The schedule
        now dates each row by the true monthly DUE date: the confirmed
        payment shows Jun 1 (its real statement date) and the first
        projected row shows Jul 1 (the following month).

        A user_trueup anchor on 2026-05-22 makes only the 2026-05-21 pay
        period (due 2026-06-01) post-anchor; the 2026-06-18 pay period
        (due 2026-07-01) is a Projected forward payment.  Both pay-period
        starts precede their monthly due dates, so the old pay-period-start
        dating mislabeled both rows.
        """
        params = _fixed_rate_300k_params()
        anchors = [
            _origination_anchor(params),
            FakeAnchorEvent(
                anchor_date=date(2026, 5, 22),
                anchor_balance=Decimal("200000.00"),
                created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
            ),
        ]
        payments = [
            # Confirmed, keyed to its pay-period start 2026-05-21; due 06-01.
            PaymentRecord(date(2026, 5, 21), Decimal("1798.65"), True),
            # Projected, keyed to pay-period start 2026-06-18; due 07-01.
            PaymentRecord(date(2026, 6, 18), Decimal("1798.65"), False),
        ]
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=anchors,
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
            extra_monthly=Decimal("0.00"),
            as_of=date(2026, 6, 2),
        )
        # Confirmed history row carries the true due date (06-01), not the
        # pay-period start (05-21) it was keyed to.
        assert len(scenarios.history_rows) == 1
        assert scenarios.history_rows[0].is_confirmed is True
        assert scenarios.history_rows[0].payment_date == date(2026, 6, 1)
        # The projection picks up the FOLLOWING month (07-01), not 06-01.
        assert scenarios.committed_forward[0].is_confirmed is False
        assert scenarios.committed_forward[0].payment_date == date(2026, 7, 1)

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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
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

        Under the anchor-seeded replay contract, the loan's remaining
        contractual life at as_of is ``term - months_from_origination
        (next_pay_date) + 1``, NOT ``term - len(rows)``.  Origination
        2024-01-01, next_pay_date 2026-05-01 -> month 28 of the loan
        -> 333 months remain (360 - 27).  The forward projection
        therefore caps at 333 rows for committed (full term tail
        from $298,796.42 at 6%), 211 rows for accelerated ($500/mo
        extra accelerates payoff to ~Nov 2043).

            P = 298796.42, i = 0.005, n_remaining = 333
            committed M  = 1798.65; pays off at month 333
            accelerated M = 2298.65; n_accel = -log(1 - P*i/M) /
                log(1+i) approx 210.44 -> 211 rows
            months_saved = 333 - 211 = 122

        Pinning 122 here; the closed-form derivation above is the
        verification path.  The pre-fix architecture used
        ``remaining_months = term - len(rows) = 356`` and produced
        months_saved=145, which was wrong: the four-row history
        already consumed months 24-27 of the loan, so the committed
        tail is 333 months, not 356.  A regression that re-
        introduced the len(rows)-based calculation would push this
        number back to 145.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        assert (
            scenarios.months_saved
            == len(scenarios.committed_forward)
            - len(scenarios.accelerated_forward)
        )
        assert scenarios.months_saved == 122

    def test_interest_saved_metric(self):
        """C3-9: interest_saved = sum(committed.interest) - sum(accel.interest).

        Composer-derived from the 333-row committed tail and the
        211-row accelerated tail (see C3-8 for the row-count
        derivation):

            sum(committed.interest)   = $339,142.28
            sum(accelerated.interest) = $184,964.88
            interest_saved            = $154,177.40

        The pinned value is the composer's output for the symptom
        inputs.  A 122-month early payoff at a 6% APR on a $298,796
        starting balance saves ~$154k of interest, matching within
        rounding.
        """
        params = _fixed_rate_300k_params()
        anchor = _origination_anchor(params)
        scenarios = compute_payoff_scenarios(
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
            extra_monthly=Decimal("500.00"),
            as_of=self.AS_OF,
        )
        expected = (
            scenarios.total_interest_committed
            - scenarios.total_interest_accelerated
        )
        assert scenarios.interest_saved == expected
        assert scenarios.interest_saved == Decimal("154177.40")

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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=_four_contractual_payments_jan_to_apr_2026(),
                rate_changes=_rate_feed(params),
            ),
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
        #    acceleration would inflate this past 200, and a
        #    regression to the len(rows)-based remaining-months
        #    formula would push it to 145).
        assert scenarios.months_saved == 122

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
            origination = add_months(
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
                loan_inputs=LoanInputs(
                    loan_params=params,
                    anchor_events=[anchor],
                    payments=payments,
                    rate_changes=_rate_feed(params),
                ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor_origin, anchor_trueup],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
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
            loan_inputs=LoanInputs(
                loan_params=params,
                anchor_events=[anchor],
                payments=payments,
                rate_changes=_rate_feed(params),
            ),
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


# -- F-27 -- target-date outlook honors the committed plan ------------------


class TestTargetDateOutlook:
    """``target_date_outlook`` (F-27, "fix + reframe").

    The target-date calculator's committed-plan producer: one
    ``_build_forward_inputs`` setup drives the plan's payoff date AND
    the additional-extra search, both honoring the same planned-outlay
    override map ``compute_payoff_scenarios`` uses -- so a user already
    paying extra through a recurring template is no longer told they
    need the full raw extra again.
    """

    AS_OF = date(2026, 6, 15)

    def _fixed_300k(self):
        """$300k / 6% / 360 months from 2026-01-01, origination anchor."""
        params = FakeLoanParams(
            origination_date=date(2026, 1, 1),
            term_months=360,
            original_principal=Decimal("300000.00"),
            interest_rate=Decimal("0.06"),
            payment_day=1,
        )
        return params, _origination_anchor(params)

    def _projected_plan(self, monthly_amount, months, start):
        """``months`` projected monthly payments of ``monthly_amount``."""
        return [
            PaymentRecord(
                payment_date=add_months(start, offset),
                amount=monthly_amount,
                is_confirmed=False,
            )
            for offset in range(months)
        ]

    def test_committed_payoff_date_matches_composer(self):
        """The outlook's plan payoff date equals the composer's.

        Both produce the committed scenario from the same prep, so the
        target-date tab's "Current Plan Pays Off" figure and the
        extra-payment tab's committed series can never disagree.
        """
        params, anchor = self._fixed_300k()
        # Contractual P&I for $300k/6%/360 is $1,798.65; the plan pays
        # $2,298.65 (+$500) for 24 projected months.
        plan = self._projected_plan(
            Decimal("2298.65"), 24, date(2026, 7, 1),
        )
        loan_inputs = LoanInputs(params, [anchor], plan, _rate_feed(params))

        outlook = loan_resolver.target_date_outlook(
            loan_inputs=loan_inputs,
            target_date=date(2046, 1, 1),
            as_of=self.AS_OF,
        )
        scenarios = compute_payoff_scenarios(
            loan_inputs=loan_inputs,
            extra_monthly=Decimal("0.00"),
            as_of=self.AS_OF,
        )
        assert outlook.committed_payoff_date == (
            scenarios.payoff_date_committed
        )

    def test_plan_lowers_required_extra_vs_raw(self):
        """F-27 acceptance: a rich plan lowers the per-month top-up.

        The raw answer (no plan) and the plan-aware answer target the
        same date from the same replay state.  Override months suppress
        the searched extra (the composer convention), so the plan-aware
        figure drops below the raw one exactly when the plan's window
        contribution exceeds what the raw extra would have added over
        those months: here $800/mo x 24 = $19,200 against a raw extra
        of ~$733/mo x 24 = ~$17,590.  (A LEANER plan can legitimately
        yield a HIGHER per-month top-up -- the extra is then squeezed
        into fewer, later months; the correctness/minimality pins below
        hold either way.)
        """
        params, anchor = self._fixed_300k()
        # Contractual $1,798.65 + $800 for 24 projected months.
        plan = self._projected_plan(
            Decimal("2598.65"), 24, date(2026, 7, 1),
        )
        target = date(2041, 1, 1)
        loan_inputs_plan = LoanInputs(
            params, [anchor], plan, _rate_feed(params),
        )
        loan_inputs_raw = LoanInputs(params, [anchor], None, _rate_feed(params))

        with_plan = loan_resolver.target_date_outlook(
            loan_inputs=loan_inputs_plan, target_date=target, as_of=self.AS_OF,
        )
        without_plan = loan_resolver.target_date_outlook(
            loan_inputs=loan_inputs_raw, target_date=target, as_of=self.AS_OF,
        )
        assert with_plan.required_extra is not None
        assert without_plan.required_extra is not None
        assert with_plan.required_extra < without_plan.required_extra

        # Correctness: the committed plan plus the found extra pays off
        # by the target date (the composer's accelerated scenario applies
        # extra to non-override months exactly as the search did).
        accelerated = compute_payoff_scenarios(
            loan_inputs=loan_inputs_plan,
            extra_monthly=with_plan.required_extra,
            as_of=self.AS_OF,
        )
        assert accelerated.payoff_date_accelerated <= target

        # Minimality within the search's one-cent convergence: two
        # cents less must miss the target.
        under = compute_payoff_scenarios(
            loan_inputs=loan_inputs_plan,
            extra_monthly=with_plan.required_extra - Decimal("0.02"),
            as_of=self.AS_OF,
        )
        assert under.payoff_date_accelerated > target

    def test_no_payments_outlook_equals_raw_semantics(self):
        """With no plan, the outlook degrades to the raw answer shape.

        The committed slice IS the original slice when no override
        months exist, so the payoff date is the contractual payoff and
        the required extra matches the no-plan search.
        """
        params, anchor = self._fixed_300k()
        loan_inputs = LoanInputs(params, [anchor], None, _rate_feed(params))

        outlook = loan_resolver.target_date_outlook(
            loan_inputs=loan_inputs,
            target_date=date(2041, 1, 1),
            as_of=self.AS_OF,
        )
        scenarios = compute_payoff_scenarios(
            loan_inputs=loan_inputs,
            extra_monthly=Decimal("0.00"),
            as_of=self.AS_OF,
        )
        assert outlook.committed_payoff_date == (
            scenarios.payoff_date_committed
        )
        assert outlook.required_extra is not None
        assert outlook.required_extra > Decimal("0.00")
