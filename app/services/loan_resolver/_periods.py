"""Loan-resolver shared foundation: rate periods, anchor selection, replay.

The pure machinery both the resolver state (:mod:`._state`) and the payoff
composer (:mod:`._payoff`) build on: constructing a loan's rate periods from
its params + rate-change feed, selecting the governing anchor event, replaying
confirmed payments forward from it, and the :class:`LoanInputs` bundle that
carries a loan's loaded data through every entry point.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app.services.amortization_engine import (
    PaymentRecord,
    RateChangeRecord,
)
from app.services.rate_period_engine import (
    BalanceAnchor,
    LoanTerms,
    RatePeriod,
    ScheduleReplay,
    build_rate_periods,
    replay_schedule,
)

if TYPE_CHECKING:
    # Typing-only import: keeps this resolver a runtime model-free leaf
    # (no Flask, no db.session, duck-typed ``loan_params``) while still
    # giving ``LoanInputs.loan_params`` a precise hint.  ``from __future__
    # import annotations`` makes the reference a lazy string, so the model
    # layer is never imported at runtime.
    from app.models.loan_params import LoanParams

ZERO_MONEY = Decimal("0.00")


def _origination_rate(rate_changes: list | None) -> Decimal:
    """Return the loan's origination (period-0) rate from the rate-change feed.

    DH-#56 retired ``LoanParams.interest_rate``, so the engine's base /
    period-0 rate is now the earliest :class:`RateChangeRecord` in the
    feed -- the origination :class:`RateHistory` row every loan carries
    (``create_params`` seeds it on setup; the DH-#56 migration backfilled
    every pre-existing loan).  Because that origination-dated row always
    covers period 0 via :func:`_rate_at_date`, this value is what
    :func:`build_rate_periods` resolves for period 0 anyway; populating
    ``LoanTerms.base_rate`` from it keeps the (now-unreached) fallback
    consistent with the feed.

    Raises:
        ValueError: When ``rate_changes`` is empty/``None``.  Every loan
            must carry an origination :class:`RateHistory` row, so an
            empty feed is a data-invariant violation the caller must
            surface loudly rather than paper over with a silent default.
    """
    if not rate_changes:
        raise ValueError(
            "loan rate resolution requires at least one RateHistory row "
            "(the origination rate) -- received an empty rate-change "
            "feed.  create_params seeds the origination row on setup and "
            "the DH-#56 migration backfilled every pre-existing loan; an "
            "empty feed means that invariant was violated."
        )
    earliest = min(rate_changes, key=lambda change: change.effective_date)
    return Decimal(str(earliest.interest_rate))


def _loan_terms_from(loan_params, base_rate: Decimal) -> LoanTerms:
    """Build the rate-period engine's :class:`LoanTerms` from a LoanParams.

    Reads the immutable origination fields a loan's amortization is
    defined by.  ``base_rate`` is the loan's origination (period-0) rate,
    supplied by the caller from the origination :class:`RateHistory` row
    (see :func:`_origination_rate`) since DH-#56 retired the
    ``LoanParams.interest_rate`` column; the :class:`RateHistory`-layered
    rate changes override it per period.  The ARM cadence columns drive
    the fixed-rate period boundaries.

    Args:
        loan_params: A LoanParams-shaped object exposing
            ``origination_date``, ``original_principal``,
            ``term_months``, ``is_arm``,
            ``arm_first_adjustment_months``, and
            ``arm_adjustment_interval_months``.
        base_rate: The origination annual rate (decimal fraction) the
            caller resolved from the loan's earliest rate-change record.

    Returns:
        The corresponding :class:`LoanTerms`.
    """
    return LoanTerms(
        origination_date=loan_params.origination_date,
        original_principal=Decimal(str(loan_params.original_principal)),
        base_rate=base_rate,
        term_months=loan_params.term_months,
        is_arm=bool(getattr(loan_params, "is_arm", False)),
        arm_first_adjustment_months=getattr(
            loan_params, "arm_first_adjustment_months", None,
        ),
        arm_adjustment_interval_months=getattr(
            loan_params, "arm_adjustment_interval_months", None,
        ),
    )


def _recorded_pi_from(
    rate_changes: list[RateChangeRecord] | None,
) -> dict[date, Decimal]:
    """Extract the recorded recast-P&I map from the rate-change feed.

    A rate change's ``monthly_pi`` (when present) is the lender's
    recorded recast payment for the fixed-rate period that change
    begins.  Keying by ``effective_date`` lets
    :func:`rate_period_engine.build_rate_periods` hold that exact figure
    constant for the period instead of deriving it.

    Args:
        rate_changes: Optional :class:`RateChangeRecord` list; entries
            without a ``monthly_pi`` are omitted (their period's P&I is
            derived).

    Returns:
        A ``{effective_date: monthly_pi}`` dict (empty when none recorded).
    """
    if not rate_changes:
        return {}
    return {
        change.effective_date: Decimal(str(change.monthly_pi))
        for change in rate_changes
        if change.monthly_pi is not None
    }


def _resolve_periods(loan_params, rate_changes):
    """Build the loan's rate periods from its params and rate-change feed.

    One construction shared by every resolver entry point
    (:func:`._state.resolve_loan`,
    :func:`._state.compute_monthly_payment_baseline`,
    :func:`._payoff.compute_payoff_scenarios`) so the period set they read
    cannot drift apart.

    Args:
        loan_params: The loan's :class:`LoanParams`-shaped object.
        rate_changes: Optional :class:`RateChangeRecord` feed.

    Returns:
        The ordered :class:`~app.services.rate_period_engine.RatePeriod`
        list for the loan.
    """
    return build_rate_periods(
        terms=_loan_terms_from(loan_params, _origination_rate(rate_changes)),
        rate_changes=rate_changes,
        recorded_period_pi=_recorded_pi_from(rate_changes),
    )


@dataclass(frozen=True)
class LoanInputs:
    """The loaded input data for a single loan, shared by every resolver entry point.

    Bundles the four pieces of loan data that :func:`._state.resolve_loan`
    and :func:`._payoff.compute_payoff_scenarios` both consume into one
    immutable argument.  Every caller already loads exactly these four
    together (the ``LoanParams`` row, its anchor events, and the payment +
    rate-change feeds from
    :func:`app.services.loan_payment_service.load_loan_context`), so naming
    the clump lets the two entry points share one cohesive parameter instead
    of threading the same four values by hand.  The evaluation date
    (``as_of``) and the accelerated-scenario ``extra_monthly`` are
    deliberately NOT bundled here -- they are the per-call question asked of
    a given loan, not part of the loan's data.

    Frozen so a caller can derive a variant with
    :func:`dataclasses.replace` (the resolver passes a confirmed-only
    ``payments`` view to the composer this way) without mutating the
    shared instance.

    Attributes:
        loan_params: A :class:`LoanParams`-shaped object exposing the
            origination / principal / rate / term / ARM-cadence fields
            and ``payment_day``.  Plain SQLAlchemy ``LoanParams`` rows
            work unchanged; duck-typed test fixtures work too (the type
            hint is a typing-only forward reference, not a runtime
            constraint).
        anchor_events: Non-empty list of LoanAnchorEvent-shaped objects
            (``anchor_date``, ``anchor_balance``, ``created_at``).
            Commit 12's origination backfill guarantees at least one per
            loan; an empty list raises ``ValueError`` when the latest
            anchor is selected.
        payments: Prepared :class:`PaymentRecord` list from
            :func:`app.services.loan_payment_service.prepare_payments_for_engine`
            (escrow subtracted, biweekly redistributed).  ``None`` or
            empty when the loan has no payment history.
        rate_changes: Optional :class:`RateChangeRecord` ARM
            rate-history.  ``None`` or empty for a fixed-rate loan.
    """

    loan_params: LoanParams
    anchor_events: list
    payments: list[PaymentRecord] | None
    rate_changes: list[RateChangeRecord] | None


def _select_latest_anchor(anchor_events: list) -> object:
    """Return the most recent anchor event by (anchor_date, created_at) DESC.

    Mirrors the ORM ``backref(order_by=...)`` on
    :class:`LoanAnchorEvent`: ``anchor_date DESC, created_at DESC``.
    A loan can carry multiple events on the same day (origination
    plus a later trueup); ``created_at`` is the deterministic
    tie-breaker so the same anchor list always selects the same
    event.

    Args:
        anchor_events: Non-empty list of LoanAnchorEvent-shaped
            objects with ``anchor_date`` (date) and ``created_at``
            (datetime) attributes.

    Returns:
        The single most recent event.

    Raises:
        ValueError: If ``anchor_events`` is empty.  Commit 12's
            origination backfill guarantees at least one event per
            loan; an empty list signals a data invariant violation
            the caller must surface, not silently paper over.
    """
    if not anchor_events:
        raise ValueError(
            "loan_resolver requires at least one LoanAnchorEvent; "
            "Commit 12 backfill should have produced an origination "
            "event for every loan -- received an empty list."
        )
    return max(
        anchor_events,
        key=lambda event: (event.anchor_date, event.created_at),
    )


def _replay_from_anchor(
    loan_inputs: LoanInputs,
    periods: list[RatePeriod],
    as_of: date,
) -> ScheduleReplay:
    """Replay confirmed payments forward from the loan's latest anchor.

    Shared by :func:`._state.resolve_loan` (which reads ``balance_as_of``
    for the current balance) and :func:`._payoff.compute_payoff_scenarios`
    (which reads the full replay -- rows, balance, next pay date, remaining
    months -- as the deterministic-past slice).  Selecting the latest
    anchor and starting replay from its verified balance is identical
    work for both, so it lives here once: the resolver's
    independently-derived balance and the composer's history rows walk
    the same replay and cannot diverge.

    Only confirmed payments reduce the balance.  An unconfirmed payment
    is a Projected transfer the user has not yet marked received; it is
    a future commitment, not historical fact, so it is filtered out here
    (the forward projection picks it up via the override map).
    ``replay_schedule`` owns the anchor-boundary and as-of cap, keying
    each payment by its true monthly due date so a pay period that
    straddles a mid-period balance true-up is classified correctly.

    Args:
        loan_inputs: The loan's loaded input bundle.  ``anchor_events``
            must be non-empty (the Commit-12 invariant).
        periods: The loan's ordered rate periods, built once by the
            caller via :func:`_resolve_periods`.
        as_of: Evaluation date; replay stops at the latest payment whose
            pay period has begun by this date.

    Returns:
        The :class:`~app.services.rate_period_engine.ScheduleReplay` for
        the confirmed-payment history through ``as_of``.

    Raises:
        ValueError: When ``loan_inputs.anchor_events`` is empty (via
            :func:`_select_latest_anchor`).
    """
    anchor = _select_latest_anchor(loan_inputs.anchor_events)
    return replay_schedule(
        periods=periods,
        anchor=BalanceAnchor(
            balance=Decimal(str(anchor.anchor_balance)),
            as_of_date=anchor.anchor_date,
        ),
        confirmed_payment_dates=[
            payment.payment_date
            for payment in (loan_inputs.payments or [])
            if payment.is_confirmed
        ],
        payment_day=loan_inputs.loan_params.payment_day,
        as_of=as_of,
    )
