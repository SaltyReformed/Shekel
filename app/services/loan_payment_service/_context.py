"""
Shekel Budget App -- A loan's LOADED context and its payment history.

One account's payments, rate changes, escrow and contractual P&I, loaded once
and shared by every consumer of an amortization schedule
(:func:`load_loan_context`), plus the query that turns shadow income rows into
the engine's :class:`PaymentRecord` feed (:func:`get_payment_history`).

Sits above :mod:`._engine_prep`, whose two corrections it applies to the feed
before returning it.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import escrow_calculator
from app.services.amortization_engine import PaymentRecord, RateChangeRecord
from app.services.loan_ledger import payment_visible_on
from app.services.loan_loaders import (
    _rate_change_records_from,
    load_escrow_lines,
    load_rate_history,
    loan_payment_due_date,
    query_shadow_income,
)
from app.services.row_valuation import owned_contribution
from ._engine_prep import compute_contractual_pi, prepare_payments_for_engine

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


@dataclass
class LoanContext:
    """All context data needed for loan projection.

    Loaded once per account via load_loan_context(), shared across all
    projection consumers (loan dashboard, savings dashboard, year-end
    service, debt strategy).  Eliminates duplicated data loading logic.

    Attributes:
        payments: Prepared PaymentRecord list (escrow-subtracted,
            biweekly month-aligned).  Ready for the amortization engine.
        rate_changes: List of RateChangeRecord for the loan -- its
            origination row plus any ARM adjustments (DH-#56: every loan
            carries an origination row).  ``None`` only for a loan with
            no RateHistory rows at all, which the origination-row
            invariant forbids in production.
        escrow_components: The loan's escrow lines resolved to their in-effect
            version on today (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`),
            for display and escrow calculation.
        monthly_escrow: Aggregated monthly escrow Decimal.
        contractual_pi: Standard monthly P&I payment (no escrow).
        rate_history: RateHistory ORM objects for rate display.  Carries
            the origination row for every loan plus any ARM adjustments;
            the loan dashboard shows the table only for ARM loans.
        escrow_lines: The account's escrow LINES with their full version
            history (:class:`~app.models.escrow_line.EscrowLine`), the raw
            source ``escrow_components`` is resolved from.  Kept so the loan
            dashboard builds the escrow card's version drawer
            (:func:`app.services.escrow_calculator.build_escrow_card`) off the
            same load, rather than re-querying the lines.
    """

    payments: list[PaymentRecord]
    rate_changes: list[RateChangeRecord] | None
    escrow_components: list  # list[ResolvedEscrowLine]
    monthly_escrow: Decimal
    contractual_pi: Decimal
    rate_history: list = field(default_factory=list)  # list[RateHistory]
    escrow_lines: list = field(default_factory=list)  # list[EscrowLine]


def load_loan_context(
    account_id: int,
    scenario_id: int | None,
    loan_params: LoanParams,
) -> LoanContext:
    """Load and prepare all context data for a loan account.

    Consolidates the data loading pattern repeated in loan routes,
    savings dashboard, year-end service, and debt strategy: payment
    history retrieval, escrow loading, payment preparation (escrow
    subtraction + biweekly redistribution), and rate change loading
    for ARM loans.

    This is a pure data-loading function -- no Flask request/session
    imports.  Callers pass the scenario_id explicitly.

    Args:
        account_id: The loan account ID.
        scenario_id: Baseline scenario ID for payment history lookup.
            None means no payments are loaded (empty list).
        loan_params: LoanParams model instance for the account.

    Returns:
        LoanContext with all data needed for amortization projection.
    """
    # Escrow -- loaded first because payment preparation needs it.  Resolve the
    # lines to their in-effect versions on today ONCE: ``escrow_components`` is
    # that resolved-today display/calc set and ``monthly_escrow`` its sum, which
    # equals ``escrow_monthly_as_of(escrow_lines, today)`` by construction.
    escrow_lines = load_escrow_lines(account_id)
    escrow_components = escrow_calculator.resolve_active_lines(
        escrow_lines, date.today(),
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )

    # Rate history for EVERY loan -- needed BEFORE contractual_pi so
    # the rate (origination plus any ARM adjustments) factors into the
    # SSOT monthly_payment.  DH-#56 retired LoanParams.interest_rate, so
    # the loan's base / period-0 rate now lives in its origination
    # RateHistory row; every loan carries one (create_params seeds it on
    # setup; the DH-#56 migration backfilled pre-existing loans).  The
    # load is therefore no longer ARM-gated -- a fixed-rate loan resolves
    # its single rate period from its one origination row.  The raw rows
    # are kept for the ``rate_history`` display field; the feed is the
    # same rows mapped for the engine.
    rate_history_records = load_rate_history(account_id)
    rate_changes = _rate_change_records_from(rate_history_records)

    # Payment history from shadow income transactions.
    raw_payments = (
        get_payment_history(account_id, scenario_id, loan_params.payment_day)
        if scenario_id else []
    )

    # Prepare: subtract escrow and fix biweekly month overlaps.  The
    # ARM-aware contractual_pi makes the escrow-subtraction threshold
    # match LoanState.monthly_payment -- the SSOT property the user
    # called out (P&I, escrow, and monthly payment numbers must be the
    # same across the loan card, the schedule's projected rows, and
    # the prepared-payment net amount).  ``raw_payments`` is passed
    # so the baseline does a conservative anchor-walk over the raw
    # (gross-of-escrow) amounts -- guarantees the threshold is at-
    # or-below ``state.monthly_payment``, which guarantees the
    # escrow-subtraction min() in :func:`prepare_payments_for_engine`
    # picks the FULL escrow amount.  Without this, the threshold is
    # an anchor-based approximation that slightly overestimates the
    # true P&I, under-subtracts escrow, and leaks a few cents per
    # row into the schedule's "Payment" column.
    contractual_pi = compute_contractual_pi(
        loan_params, rate_changes, date.today(),
    )
    payments = prepare_payments_for_engine(
        raw_payments, loan_params.payment_day,
        escrow_lines, contractual_pi,
    )

    return LoanContext(
        payments=payments,
        rate_changes=rate_changes,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        contractual_pi=contractual_pi,
        rate_history=rate_history_records,
        escrow_lines=escrow_lines,
    )










def get_payment_history(
    account_id: int, scenario_id: int, payment_day: int,
) -> list[PaymentRecord]:
    """Query shadow income transactions on a debt account.

    Returns PaymentRecord instances for all non-deleted, non-excluded
    shadow income transactions linked to the given account and scenario.
    Shadow income transactions represent payments received by a debt
    account via transfers.

    Filtering logic:
      - transfer_id IS NOT NULL (shadow transactions only)
      - transaction_type_id = Income (income side of the transfer)
      - is_deleted = False (excludes soft-deleted transactions)
      - status.excludes_from_balance = False (excludes Cancelled and
        Credit statuses, which do not represent actual payments)

    Prices each row through
    :func:`~app.services.cash_ledger.owned_contribution` -- the accessor whose
    NAME asserts the row owns its figure -- rather than through the amount
    model's resolver.  This query is NOT settled-only: ``query_shadow_income``
    filters Credit and Cancelled but ADMITS Projected, and a Projected
    loan-side income shadow is exactly the kind plan step **X-au-g** declares
    derived.

    **This docstring used to blame a CYCLE, and the restriction outlived it.**
    The path it named -- the LOAN_PAYMENT rule ->
    :meth:`~app.services.cash_ledger.LoanPricing.derive_cash` ->
    ``_resolve_loan_basis`` -> :func:`load_loan_context` -> this function -- is
    deleted; see :func:`app.services.cash_ledger._resolve_loan_basis` (a module
    of the amount model since plan step X-au-g-2a, which moved rule 4's
    producer down a tier).  What still stops the loan-side INCOME leg
    being declared derived is this function's own pricing: it reads each row
    through :func:`~app.services.row_valuation.owned_contribution`, which
    REFUSES a row whose plan is derived.  So finding **N-266** (a) is
    MISDIAGNOSED rather than closed -- one unrouted reader, not an irreducible
    cycle -- the rule-4 controls
    (``test_amount_source._declare_loan_payment_derived``) still declare only
    the checking-side EXPENSE leg, and X-au-g routes THIS reader first and
    declares both legs after.  The full argument is written once, at
    :func:`app.services.balance_at._plan._planned_from_shadows`.

    **That is not finding N-259**, which was a WRITE-BACK cycle one layer up
    and is CLOSED: a settle used to refresh the amount, so a settle / revert /
    settle compounded the standing extra.  Plan step ``balance:X-au-c3``
    (`3d1379d1`) made a settle RECORD what moved instead, so that compounding
    is no longer reproducible.  It is named only because conflating the two
    cycles is what kept the bound above alive.

    The accessor's refusal still earns its place: a cutover that declares this
    leg derived fails LOUDLY here instead of feeding a ``None`` into the
    amortization engine, so X-au-g routes this reader deliberately rather than
    discovering it.

    The valuation runs through ``row_valuation.owned_contribution``: ``0`` for a
    row contributing nothing, what a SETTLED row RECORDED as moved, else the
    row's own amount (``actual_amount`` before plan step X-au-c3's record).

    Each record carries all three of a loan payment's dates (see
    :class:`~app.services.amortization_engine.PaymentRecord`): ``payment_date``
    is the pay-period start (the funding basis), ``due_date`` is the
    installment it satisfies, from the ONE derivation the genesis write walk
    also uses (:func:`app.services.loan_loaders.loan_payment_due_date`), and
    ``settled_on`` is the day the cash moved, from the ONE derivation the
    genesis fold dates that payment's principal by
    (:func:`app.services.loan_ledger.payment_visible_on`).

    **Deriving the DUE date or the CASH day from the pay period has cost a
    defect each** -- the first mis-dated a late payment to the following
    month, the second was finding **N-187**, where an early payment was history
    to the ledger and a plan to the resolver at the same instant.  The funding
    basis has cost none: it decides only the replay's rate lookup, which finding
    **N-36** records as deliberate and step X-n owns.

    **This function is the ONE place status and settle day are arbitrated, and
    ``PaymentRecord.is_confirmed`` is derived from the result.**  ``status.is_settled``
    decides whether the payment happened; the day is then REQUIRED, because
    :func:`~app.utils.balance_predicates.settled_day` (inside
    ``payment_visible_on``) refuses a settled row carrying none rather than
    inventing one.  A row broken the other way -- Projected but still carrying a
    stale day, which only a seam bypass can produce -- has that day dropped
    here, so the record cannot report it as confirmed.  Status-first is the
    same order the fold's loader uses
    (:func:`app.services.loan_loaders.settled_income_shadows`, which filters on
    ``status_id``), which is why the two producers cannot classify a broken row
    differently.

    Args:
        account_id: The debt account receiving payments.
        scenario_id: The active budget scenario.
        payment_day: The loan's contractual day-of-month due day
            (:attr:`app.models.loan_params.LoanParams.payment_day`), used only
            to reconstruct the due date of a shadow that stores none.

    Returns:
        List of PaymentRecord instances sorted by payment date
        (ascending).  Empty list if no qualifying transactions exist.

    Raises:
        UndatedSettleError: When a shadow in a settled status carries no
            ``settled_on`` -- the settled-iff-dated invariant is broken on that
            row, and dating it by a fallback would put real money on a day
            nothing recorded (see :func:`app.utils.balance_predicates.settled_day`).
            **The mark-paid door no longer reaches this, and that is a
            refusal this step DELETED rather than an edge it kept.**  The
            paragraph here used to record the door arriving through
            :meth:`~app.services.cash_ledger.LoanPricing.live_cash` ->
            :func:`load_loan_context`; that
            edge is gone with the pricing cycle, so a settle now reaches
            pricing via ``transfer_service._settle`` -> ``amount_basis`` ->
            ``live_cash`` and never loads a payment history.  The three
            remaining callers of :func:`load_loan_context` are
            ``routes/loan/_helpers.py`` (twice) and
            ``balance_at/_resolution.py``.

            The row is still broken on every other surface -- the sibling cash
            leg refuses at ``cash_ledger._events`` and the loan fold at
            ``loan_ledger._visible`` -- so no loan carrying such a shadow is
            silently healthy; what changed is only WHICH door reports it
            first.  Stated rather than quietly dropped, because a refusal that
            stops being reachable from a door is a behaviour change even when
            every other reader still refuses.
    """
    # Shadow-income transactions for this account across every period,
    # ordered by period start for the chronological payment timeline.
    # ``query_shadow_income`` owns the shared "what counts as shadow income"
    # predicate; the explicit ``join(Transaction.pay_period)`` brings the
    # PayPeriod alias into scope for the ``order_by`` (the builder's
    # ``joinedload`` is the separate N+1-avoiding eager-load).
    txns = (
        query_shadow_income(account_id, scenario_id)
        .join(Transaction.pay_period)
        .order_by(PayPeriod.start_date)
        .all()
    )

    payments = []
    for txn in txns:
        amount = owned_contribution(txn)
        # Defensive: ensure Decimal even if the stored column somehow
        # yields a non-Decimal.
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))

        payments.append(PaymentRecord(
            payment_date=txn.pay_period.start_date,
            due_date=loan_payment_due_date(txn, payment_day),
            # The day the cash moved, read through the SAME accessor the fold
            # dates this payment's principal by, so the resolver's
            # "already happened" and the ledger's are one derivation and not
            # two (plan step X-an).  ``None`` for a Projected shadow: its cash
            # has not moved, and ``PaymentRecord.is_confirmed`` is exactly
            # that absence.
            settled_on=(
                payment_visible_on(txn) if txn.status.is_settled else None
            ),
            amount=amount,
        ))

    return payments
