"""Balance-at-T seam -- the LIABILITY view (multi-date, forward-only).

The seam's third shape, beside the period-keyed maps (:mod:`._kind_correct`)
and the scalar-at-a-date: every liability's owed magnitude at a list of FORWARD
calendar dates, answered in ONE loan-resolution pass.

It exists because a long-horizon liability band needs each debt's owed balance
at ~25 annual sample dates, and the scalar
(:func:`~app.services.balance_at.balance_at`) would re-resolve each loan once per
date.  Before this view existed, the horizon band reached PAST the seam into
``net_worth_kernel.loan_owed_at_dates`` directly -- a consumer holding a
balance-at-T boundary rule, the exact pattern the W9906 fence exists to prevent
(``docs/audits/balance_architecture/followup_fence_loan_owed_at_dates.md``).
"""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.scenario import Scenario
from app.services import net_worth_kernel
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

from ._inputs import ZERO


def _spliced_owed_series(
    sample_dates: list[date],
    today: date,
    current: Decimal,
    owed_by_date: dict[date, Decimal],
) -> list[Decimal]:
    """Splice the confirmed present with the forward projection, per sample date.

    A date at or before *today* reads *current* -- the ledger-confirmed balance
    the caller supplied, which is the figure the net-worth hero renders -- and a
    strictly-future date reads its OWN projected value out of *owed_by_date*.

    The join is BY DATE, not by position.  An earlier draft consumed the
    producer's list positionally, which was correct only because
    :func:`~app.services.net_worth_kernel.loan_owed_at_dates` happens to build it
    in the caller's order with no sort and no dedupe -- an unwritten cross-module
    contract that a future "harmless" tidy-up (sorting or de-duplicating the
    sample dates before the expensive schedule walk) would have broken SILENTLY,
    mis-valuing every point of the liability band with no crash and no failing
    test.  Keying on the date makes that state impossible to reach.

    ``abs`` is applied to the projected value for the same reason it is applied
    to *current*: this view's contract is a POSITIVE owed magnitude at every
    date.  A schedule row's ``remaining_balance`` is non-negative, but the
    empty / paid-off fallback is the resolver's ``current_balance``, which has no
    zero floor (an overpaid payoff resolves negative) -- so without this an
    overpaid loan would ADD its overpayment to the liability band today and
    SUBTRACT it at every future point.

    Args:
        sample_dates: The dates to build the series over (the output order).
        today: The present-vs-future boundary (the caller's as-of date).
        current: The liability's current owed magnitude (the today value,
            already an absolute magnitude).
        owed_by_date: The projected owed balance keyed by each strictly-future
            date among *sample_dates*.

    Returns:
        The owed magnitude at each of *sample_dates*.
    """
    return [
        abs(owed_by_date[sample_date]) if sample_date > today else current
        for sample_date in sample_dates
    ]


def liability_owed_at_dates(
    liabilities: list[Account],
    scenario: Scenario | None,
    sample_dates: list[date],
    current_balances: dict[int, Decimal],
    today: date,
) -> dict[int, list[Decimal]]:
    """Return every liability's owed magnitude at each FORWARD sample date.

    The seam's multi-date, multi-account LIABILITY view.  It owns BOTH forward
    rules a liability can take, so no consumer has to know which is which:

    * **AMORTIZING with a resolvable schedule** -- the ledger-seeded confirmed
      balance today, reduced by the payments scheduled by each date, batched
      through :func:`~app.services.net_worth_kernel.loan_owed_at_dates` (ONE
      :func:`~app.services.net_worth_kernel.generate_debt_schedules` pass for the
      whole set, not one per date).  The same amortization the debt card and the
      ``2 years`` liability series consume, so a band built on this cannot drift
      from them.
    * **Every other liability** -- a revolving Credit Card, a loan with no
      ``LoanParams``, or ANY liability when there is no baseline scenario -- has
      NO forward model, so it holds FLAT at its current owed magnitude.  This is
      a balance rule, not a display choice: it is the same no-forward-model
      branch a loan the resolver cannot resolve already takes, and it lives HERE
      rather than in each consumer.  When revolving debt one day gets a real
      forward model, this is the ONE place that changes.

    ``scenario`` is nullable, and this is the ONE public seam entry that does not
    call :func:`._inputs._require_scenario`.  That guard exists to turn a missing
    baseline into a loud failure instead of a silently wrong number; here a
    missing baseline is not an error but the DEGENERATE CASE OF THE SAME RULE --
    no loan is resolvable, so every debt falls to the flat hold above, which is
    the correct answer.  Raising would force every caller to re-derive that flat
    hold, which is precisely the boundary-rule duplication the seam exists to
    prevent.

    Sign convention: the result is a POSITIVE owed magnitude per date, matching
    :func:`~app.services.net_worth_kernel.sum_net_worth_at_period`'s
    liability-minus rule (``total -= abs(bal)``).  *current_balances* may be
    signed either way (a loan resolves positive-owed, a Credit Card's cash
    balance is negative); ``abs`` is applied here.

    The today point comes from *current_balances*, NOT from a schedule walk, and
    that is load-bearing: the caller's current balance is the ledger-confirmed
    figure the net-worth hero renders, so a band built on this reconciles with
    the hero at index 0 by construction.  A schedule walk at ``today`` would
    instead report the balance net of any OVERDUE unconfirmed payment
    (understating the debt), which is exactly why
    :func:`~app.services.net_worth_kernel.loan_owed_at_dates` REJECTS a
    today-or-earlier date and only strictly-future dates are forwarded to it.

    *today* is the CALLER'S as-of date, not a fresh :func:`datetime.date.today`
    read here, and that is deliberate.  The caller already built *sample_dates*
    against some notion of "now"; if this function re-read the clock, a request
    that crossed midnight between the two reads would see its own index-0 sample
    as a PAST date and raise -- turning a benign race into a 500 on a page that
    previously just held the band flat.  Re-reading would also silently assume
    the caller's dates are UTC-anchored, when the project's own policy is that
    user-facing dates are display-tz (``app/utils/dates``).  One clock, chosen by
    the caller, so the sample axis and the present/future boundary cannot
    disagree.  (The loan RESOLVER still takes its own as-of internally when it
    decides which payments are confirmed; that is unchanged and independent of
    this projection boundary.)

    Args:
        liabilities: The liability accounts to value (every one appears in the
            result).  ``account_type`` must be loaded -- the canonical
            :func:`~app.services.account_projection.classify_account` selects the
            amortizing subset.
        scenario: The baseline scenario, or ``None`` (no baseline: every
            liability holds flat -- see above).
        sample_dates: The calendar dates to value each liability at, in the
            desired output order (any order; the projection is joined BY DATE,
            not by position).  Every date must be on or after *today*.
        current_balances: ``{account_id: Decimal}`` each liability's current
            balance as the caller already resolved it (the figure its hero
            renders).  A missing account is treated as ``0``.
        today: The caller's as-of date -- the present/future boundary, and the
            same "now" its *sample_dates* were built against (see above).

    Returns:
        ``{account_id: [Decimal owed magnitude at each sample date]}`` -- one
        list per account in *liabilities*, aligned with *sample_dates*.

    Raises:
        ValueError: When any sample date precedes *today*.  A past balance is a
            LEDGER read, not a projection: ask
            :func:`~app.services.balance_at.balance_at`, which routes an
            amortizing account's past to the genesis ledger (the only complete
            record -- it books the true-ups that have no schedule row).
    """
    stale = sorted({d for d in sample_dates if d < today})
    if stale:
        raise ValueError(
            "liability_owed_at_dates projects FORWARD; a past date is a ledger "
            "read, not a projection -- ask balance_at.balance_at, which reads "
            "the genesis ledger for an amortizing account's past. Rejected "
            f"dates: {[d.isoformat() for d in stale]} (today={today.isoformat()})"
        )

    # Deduplicated so a repeated sample date does not pay for a second schedule
    # walk; the result is joined BY DATE below, so the producer's order and
    # cardinality are its own business, not an implicit contract.
    future_dates = sorted({d for d in sample_dates if d > today})
    loan_accounts = [
        account for account in liabilities
        if classify_account(account) is AccountProjectionKind.AMORTIZING
    ]
    owed_by_loan = (
        net_worth_kernel.loan_owed_at_dates(
            loan_accounts, scenario.id, future_dates, today,
        )
        if scenario is not None and loan_accounts and future_dates
        else {}
    )

    result: dict[int, list[Decimal]] = {}
    for account in liabilities:
        raw_current = current_balances.get(account.id)
        current = abs(raw_current) if raw_current is not None else ZERO
        forward = owed_by_loan.get(account.id)
        if forward is None:
            # No forward model: hold the current owed magnitude flat.
            result[account.id] = [current] * len(sample_dates)
            continue
        result[account.id] = _spliced_owed_series(
            sample_dates, today, current, dict(zip(future_dates, forward)),
        )
    return result
