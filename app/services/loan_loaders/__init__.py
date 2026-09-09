"""
Shekel Budget App -- Loan Data Loaders (the loan services' leaf layer)

The pure data-loading functions every loan consumer shares, in two leaves:

* :mod:`._terms` -- a loan's CONTRACTUAL facts: the
  :class:`~app.models.loan_params.LoanParams` /
  :class:`~app.models.loan_anchor_event.LoanAnchorEvent` /
  :class:`~app.models.loan_features.RateHistory` /
  :class:`~app.models.escrow_line.EscrowLine` loaders, the synthesized
  origination anchor, and the ONE derivation of which installment a payment
  satisfies.
* :mod:`._shadows` -- WHICH rows are an account's payments and which of them
  have HAPPENED: the shadow-income predicate and the single settled/projected
  partition every settled-payment consumer reads.

The graph is a line -- ``_terms`` imports ``_shadows`` for the settled set its
anchor-ordering guards bound on, and nothing there reads back.

**It was one module until plan step balance:X-bl-2a**, which gave the shadow
half a partition producer and moved the amount model's eager-load set out of the
query and onto the caller, pushing the file to 1,054 lines past pylint's 1,000
ceiling.  The cut is the seam that step created rather than the line count, and
it is a PACKAGE rather than a second module so every
``app.services.loan_loaders.<name>`` path a caller or a docstring already spells
stays true.

Extracted from :mod:`app.services.loan_payment_service` (the read switch's
final arc) so the loan POSTING package and the loan PAYMENT service both
depend on one leaf instead of on each other: the posting package's
walk and reader need these loaders, while ``loan_payment_service`` hosts the
read-switch seam that imports the posting package's reader -- loading through
a shared leaf is what keeps that dependency one-directional (no import
cycle), rather than a lazy-import workaround.

This package is a LEAF: it imports models, the pure engine primitives
(:class:`~app.services.amortization_engine.RateChangeRecord`,
:func:`~app.services.rate_period_engine.monthly_due_date`), and the shared
balance predicates -- never another loan service.  Flask-isolated, reads only,
no commits.

This service queries ONLY budget.transactions (transfer invariant #5).
It NEVER queries budget.transfers.
"""

from ._shadows import (
    ShadowSets,
    income_shadows,
    projected_income_shadows,
    query_shadow_income,
    settled_income_shadows,
)
from ._terms import (
    LoanAnchorFact,
    _rate_change_records_from,
    earliest_settled_payment_due_date,
    installment_for,
    latest_settled_payment_due_date,
    load_all_loan_account_ids,
    load_escrow_lines,
    load_loan_account_ids_for_user,
    load_loan_anchor_facts,
    load_loan_params,
    load_rate_changes,
    load_rate_history,
    loan_payment_due_date,
    synthesize_origination_anchor,
)

__all__ = [
    "LoanAnchorFact",
    "ShadowSets",
    "earliest_settled_payment_due_date",
    "income_shadows",
    "installment_for",
    "latest_settled_payment_due_date",
    "load_all_loan_account_ids",
    "load_escrow_lines",
    "load_loan_account_ids_for_user",
    "load_loan_anchor_facts",
    "load_loan_params",
    "load_rate_changes",
    "load_rate_history",
    "loan_payment_due_date",
    "projected_income_shadows",
    "query_shadow_income",
    "settled_income_shadows",
    "synthesize_origination_anchor",
]
