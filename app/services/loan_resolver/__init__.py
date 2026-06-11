"""
Shekel Budget App -- Loan Resolver (E-18 canonical loan producer)

Single source of truth for "this loan's current balance, monthly
payment, schedule, payoff date, and life-of-loan interest."  Every
loan-touching surface (dashboard card, /savings debt card, net-worth
liability, debt strategy, year-end summary) reads from here so the
same loan on the same day cannot show three different numbers.

Pure function, no Flask, no `db.session` reads or writes.  The caller
loads the data and passes it in; the resolver returns plain data.

## Package layout

This is a package, split (Phase-3 pylint cleanup) by concern into
sub-modules; the public surface is re-exported here so
``from app.services.loan_resolver import resolve_loan`` and
``loan_resolver.compute_payoff_scenarios`` keep working unchanged:

* :mod:`._periods` -- the shared foundation: rate-period construction,
  anchor selection, confirmed-payment replay, and the :class:`LoanInputs`
  bundle every entry point takes.
* :mod:`._state` -- :func:`resolve_loan` (the SSOT current-state producer)
  and :func:`compute_monthly_payment_baseline` (the cheap level-payment
  lookup), plus :class:`LoanState`.
* :mod:`._payoff` -- :func:`compute_payoff_scenarios` (the three-scenario
  "what-if" composer) and :class:`PayoffScenarios`.

## What this fixes

Pre-E-18, sixteen sites assembled their own ``(principal, rate, n)``
triple.  Two failure modes appeared on the displayed cards:

* **Symptom #3 -- frozen principal.** ``LoanParams.current_principal``
  had no settle-driven writer (`grep proved zero attribute writes`),
  so confirmed PITI transfers never reduced it.  Until a user manually
  edited the field, the card stayed at the originally-entered value.

* **Symptom #4 -- ARM fixed-window payment creep.** The ARM scalar
  site (`amortization_engine.py:950-954` pre-fix) re-amortized the
  frozen stored principal over a calendar-shrinking
  ``calculate_remaining_months`` count.  The displayed P&I drifted a
  few dollars upward every month inside the supposedly fixed-rate
  window (hand-recomputed: $2,460.45 at month 24 to $2,463.28 at
  month 25 for a 5/5 ARM at $400k/6%/360mo, both above the correct
  constant $2,398.20).  See ``docs/audits/financial_calculations/
  05_symptoms.md`` Symptom #4 for the worked example.

The resolver collapses both onto a single derivation:

1. Pick the latest ``LoanAnchorEvent`` (Commit 12 guarantees every
   loan has at least one -- the origination event).
2. Replay only ``is_confirmed`` payments whose true monthly due date
   (``rate_period_engine.monthly_due_date`` of the pay-period-start the
   payment is keyed to) is strictly after the anchor date.  Comparing
   the due date rather than the pay-period start keeps a payment whose
   biweekly pay period began on or before a mid-period balance true-up
   but whose monthly payment is not due until after it.  Projected
   (unconfirmed) payments do not reduce the balance -- they are future
   commitments, not historical fact.
3. For an ARM whose anchor and as_of both fall inside
   ``[origination_date, origination_date + arm_first_adjustment_months)``
   (the fixed-rate window), compute the monthly payment ONCE from
   the anchor balance over the remaining contractual term as of the
   anchor date, and hold it constant for every ``as_of`` inside the
   window.  This is the E-02 fixed-window invariant.  A subsequent
   ``user_trueup`` anchor inside the window produces a new constant
   (the trueup IS the moment a new constant is born).
4. Outside the fixed-rate window (or for any non-ARM loan that is
   not yet paid off), amortize the current balance at the rate in
   effect for ``as_of`` over the remaining months.
5. Use ``round_money`` as the only rounding boundary in this module.

## What the resolver is NOT

* Not a query layer.  Callers load ``LoanAnchorEvent`` rows,
  ``PaymentRecord`` instances, and ``RateChangeRecord`` instances
  themselves (typically via ``loan_payment_service.load_loan_context``
  for the payment + rate-change feeds, and a direct query for the
  anchor events).
* Not a payment preparation step.  ``payments`` is expected to be
  the already-prepared list from
  ``loan_payment_service.prepare_payments_for_engine`` (escrow
  subtracted, biweekly redistributed).  Passing raw shadow-income
  payments will misalign principal/interest splits.
* Not a writer.  The resolver never inserts or updates anything;
  Commit 16 owns the trueup write path via ``anchor_service``.
"""

from ._payoff import (
    PayoffScenarios,
    TargetDateOutlook,
    compute_payoff_scenarios,
    target_date_outlook,
)
from ._periods import LoanInputs, engine_terms
from ._state import (
    LoanState,
    compute_monthly_payment_baseline,
    resolve_loan,
)

__all__ = [
    "LoanInputs",
    "LoanState",
    "PayoffScenarios",
    "TargetDateOutlook",
    "compute_monthly_payment_baseline",
    "compute_payoff_scenarios",
    "engine_terms",
    "resolve_loan",
    "target_date_outlook",
]
