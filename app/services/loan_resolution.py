"""Loan resolution: the db-facing wrappers that seed the pure resolver.

The read switch's seeding layer.  The pure :mod:`app.services.loan_resolver`
takes plain data and returns a :class:`~app.services.loan_resolver.LoanState`; the
two functions here are the db-facing entry points every SUMMARY surface (net
worth, year-end, /savings tile, debt-strategy, home equity, the loan route card,
loan recurrence-sync) resolves a loan account through.  They load the loan's
genesis-ledger confirmed view AND its standing overpayment, then delegate to the
pure resolver -- so no summary surface can drift on HOW a loan is resolved, and
none can silently fall back to the contractual (extra-free) trajectory.

Kept OUT of :mod:`app.services.loan_payment_service` deliberately: that module
owns the payment/escrow loaders and the read-switch view builder
(``confirmed_loan_view`` / ``load_loan_context``), and it sits at its size
ceiling; the resolver-seeding wrappers are a distinct concern (they compose the
loaders + the pure resolver), so they live here and import what they need.  This
module imports FROM ``loan_payment_service``; ``loan_payment_service`` does not
import back, so there is no cycle.
"""

from datetime import date

from app.models.loan_params import LoanParams
from app.services import loan_resolver
from app.services.loan_loaders import (
    load_loan_anchor_facts,
    load_loan_params,
)
from app.services.loan_payment_service import (
    confirmed_loan_view,
    load_loan_context,
)
from app.services.recurring_transfer_query import (
    loan_standing_extra_for_account,
)


def resolve_loan_seeded(
    loan_inputs: loan_resolver.LoanInputs,
    account_id: int,
    scenario_id: int | None,
    as_of: date,
) -> loan_resolver.LoanState:
    """Resolve a loan with its ledger view AND standing extra threaded in.

    The single injection helper the three db-facing loaders route through --
    :func:`resolve_account_loan`, the loan route's ``_resolve``, and the savings
    dashboard's ``_compute_loan_account`` -- so they cannot drift on HOW a loan
    is resolved.  It reads two dated inputs ONCE and threads both into the pure
    resolver:

    * The genesis-ledger confirmed view (:func:`confirmed_loan_view`): its
      balance overrides BOTH the headline balance and the forward projection's
      seed, and its ledger-derived rows become the schedule's confirmed slice,
      so the balance, the history, and the projection cannot desync
      off-schedule.  When the ledger cannot answer (``None`` -- a loan it has not
      opened) the resolver falls back to its anchor replay, the pre-switch
      behaviour.
    * The loan's standing overpayment
      (:func:`recurring_transfer_query.loan_standing_extra_for_account`): applied
      to every forward month so ``LoanState``'s schedule, payoff, and interest
      are the COMMITTED (plan-aware) trajectory every summary surface shows,
      matching the loan detail page (step 8,
      ``docs/design/escrow_line_identity_refactor.md`` Sec. 16).  ``0.00`` for a
      loan with no recurring payment, so the injection is a safe no-op there.

    Centralizing BOTH loads here is what makes it structurally impossible for a
    summary surface to resolve a loan without its plan: a new caller cannot
    silently regress to the contractual trajectory, because this chokepoint owns
    the loads.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle.  The caller
            builds it, since the three loaders each load slightly different
            surrounding data (the route also needs the context, the savings
            tile the paid-off probe).
        account_id: The loan account, already owner-checked by the caller.
        scenario_id: The baseline scenario id, or ``None``.
        as_of: The evaluation date; typically ``date.today()``.

    Returns:
        The resolved :class:`~app.services.loan_resolver.LoanState`.
    """
    view = confirmed_loan_view(account_id, scenario_id, as_of)
    return loan_resolver.resolve_loan(
        loan_inputs, as_of, confirmed_view=view,
        extra_principal=loan_standing_extra_for_account(account_id),
    )


def resolve_account_loan(
    account_id: int, scenario_id: int, today: date
) -> tuple[LoanParams, loan_resolver.LoanState] | None:
    """Load a debt account's ``LoanParams`` and run the resolver as of ``today``.

    The per-account "load LoanParams (skip if unconfigured), load anchor
    events + context, run the resolver" preamble shared by the debt-strategy
    route, the net-worth / year-end schedule generation, home equity, and the
    recurrence-sync writer.  Centralizing it keeps those consumers from drifting
    on HOW a loan account is resolved (which inputs feed
    :func:`loan_resolver.resolve_loan`, in what order).  It resolves through
    :func:`resolve_loan_seeded`, so its ``current_balance`` is the
    genesis-ledger confirmed balance (falling back to the anchor replay when the
    ledger has not opened the loan) and its schedule / payoff / interest reflect
    the loan's standing overpayment.

    Returns ``None`` when the account has no ``LoanParams`` row (it is not a
    configured loan); the caller skips it.  A configured loan is always
    resolvable -- its origination anchor fact is synthesized from the
    immutable params -- so there is no anchor-based short-circuit here.

    Args:
        account_id: The debt account to resolve.
        scenario_id: The active budget scenario (for payment history and the
            ledger seed scope).
        today: The as-of date passed through to the resolver.

    Returns:
        ``(params, state)`` -- the loaded :class:`LoanParams` and the
        resolved :class:`~app.services.loan_resolver.LoanState` -- or
        ``None`` if the account has no ``LoanParams``.
    """
    params = load_loan_params(account_id)
    if params is None:
        return None
    anchor_facts = load_loan_anchor_facts(params)
    ctx = load_loan_context(account_id, scenario_id, params)
    state = resolve_loan_seeded(
        loan_resolver.LoanInputs(
            params, anchor_facts, ctx.payments, ctx.rate_changes,
        ),
        account_id, scenario_id, today,
    )
    return params, state
