"""
Shekel Budget App -- Amortization Engine

Pure-function package for loan amortization calculations.
Generates amortization schedules, summary metrics, and payoff analysis.
No database access -- operates only on values passed in.

Supports payment-aware projections: when a list of PaymentRecord
instances is provided, the schedule replays actual/committed payments
month-by-month instead of assuming the contractual amount.  This
enables three projection scenarios from the same engine:

  1. Original schedule -- payments=None, extra_monthly=0
  2. Committed schedule -- payments=confirmed+projected transfers
  3. What-if schedule -- payments=confirmed, extra_monthly=user input

Split along the primitives/question seam (the C0302 root fix, same
shape as the ``loan_resolver`` package) so existing imports from
``app.services.amortization_engine`` keep working unchanged:

* :mod:`._projection` -- the value records, the standard payment
  formula, the date helpers, the ARM recast, and
  :func:`project_forward` itself.
* :mod:`._payoff` -- the payoff-by-date question layer
  (:class:`PayoffRequest`, :func:`required_extra_for_projection`,
  :func:`calculate_payoff_by_date`) built on the primitives.
"""

from ._payoff import (
    PayoffRequest,
    calculate_payoff_by_date,
    required_extra_for_projection,
)
from ._projection import (
    AmortizationRow,
    AmortizationSummary,
    PaymentRecord,
    ProjectionInputs,
    RateChangeRecord,
    advance_to_next_payment_date,
    calculate_monthly_payment,
    calculate_remaining_months,
    project_forward,
)

__all__ = [
    "AmortizationRow",
    "AmortizationSummary",
    "PaymentRecord",
    "PayoffRequest",
    "ProjectionInputs",
    "RateChangeRecord",
    "advance_to_next_payment_date",
    "calculate_monthly_payment",
    "calculate_payoff_by_date",
    "calculate_remaining_months",
    "project_forward",
    "required_extra_for_projection",
]
