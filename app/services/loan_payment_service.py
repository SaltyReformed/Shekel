"""
Shekel Budget App -- Loan Payment Service

Queries shadow income transactions on debt accounts and converts them
to PaymentRecord instances for the amortization engine.  Also provides
payment preparation utilities (escrow subtraction, biweekly
redistribution), a unified data-loading function (load_loan_context)
shared by all consumers of amortization schedules, and the read-switch
seam (confirmed_loan_seed / resolve_loan_seeded / resolve_account_loan)
that seeds the resolver from the genesis posting ledger.

Shadow income transactions represent payments received by a debt
account via transfers.  When a user transfers money from checking to
a mortgage account, the transfer service creates two shadow
transactions: an expense on checking (money out) and an income on
the mortgage (money in).  This service reads the income side (via the
:mod:`app.services.loan_loaders` leaf, which owns the row loaders and
the shadow-income query this module used to host) to discover all
payments into a loan account.

This service queries ONLY budget.transactions (transfer invariant #5).
It NEVER queries budget.transfers.  The balance calculator and all
related services must never depend on the transfers table directly.

Shared by:
  - app/routes/loan/ (dashboard and payoff calculator)
  - app/services/savings_dashboard_service.py (savings projections)
  - app/services/year_end_summary_service.py (annual aggregation)
  - app/routes/debt_strategy.py (debt payoff strategies)
"""

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import escrow_calculator
from app.services.amortization_engine import PaymentRecord, RateChangeRecord
from app.services.loan_loaders import (
    _rate_change_records_from,
    load_active_escrow_components,
    load_anchor_events,
    load_loan_params,
    load_rate_history,
    query_shadow_income,
)
from app.services.rate_period_engine import monthly_due_date
from app.utils.balance_predicates import is_projected

if TYPE_CHECKING:
    # Type-only import: ``loan_resolver`` imports from this module
    # (``load_loan_context``), so a runtime top-level import would be
    # circular.  ``resolve_account_loan`` returns its ``LoanState`` and
    # ``resolve_loan_seeded`` takes a ``LoanInputs``; both values are
    # produced / passed via the function-local imports below.
    from app.services.loan_resolver import LoanInputs, LoanState

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
        escrow_components: Active EscrowComponent ORM objects for
            display and escrow calculation.
        monthly_escrow: Aggregated monthly escrow Decimal.
        contractual_pi: Standard monthly P&I payment (no escrow).
        rate_history: RateHistory ORM objects for rate display.  Carries
            the origination row for every loan plus any ARM adjustments;
            the loan dashboard shows the table only for ARM loans.
    """

    payments: list[PaymentRecord]
    rate_changes: list[RateChangeRecord] | None
    escrow_components: list  # list[EscrowComponent]
    monthly_escrow: Decimal
    contractual_pi: Decimal
    rate_history: list = field(default_factory=list)  # list[RateHistory]


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
    # Escrow -- loaded first because payment preparation needs it.
    escrow_components = load_active_escrow_components(account_id)
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

    # Anchor events for the SSOT monthly_payment calculation.  Commit
    # 12's origination backfill guarantees at least one anchor per
    # loan; an empty list only arises in direct unit-test invocations
    # that bypass the backfill, and compute_contractual_pi tolerates it
    # (the period P&I is anchor-independent -- it reads the rate-change
    # feed, not the anchor).
    anchor_events = load_anchor_events(account_id)

    # Payment history from shadow income transactions.
    raw_payments = (
        get_payment_history(account_id, scenario_id)
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
        loan_params,
        anchor_events=anchor_events,
        rate_changes=rate_changes,
        as_of=date.today(),
        payments=raw_payments,
    )
    payments = prepare_payments_for_engine(
        raw_payments, loan_params.payment_day,
        monthly_escrow, contractual_pi,
    )

    return LoanContext(
        payments=payments,
        rate_changes=rate_changes,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        contractual_pi=contractual_pi,
        rate_history=rate_history_records,
    )










def get_payment_history(
    account_id: int, scenario_id: int,
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

    The is_confirmed flag is determined by the status.is_settled
    boolean:
      - True for Paid, Received, Settled (payment actually occurred)
      - False for Projected (payment is committed but not yet made)

    Uses effective_amount (not manual actual/estimated logic) to
    respect the 5A.1 fix: actual_amount when populated, else
    estimated_amount, with correct zero-vs-null handling.

    Args:
        account_id: The debt account receiving payments.
        scenario_id: The active budget scenario.

    Returns:
        List of PaymentRecord instances sorted by payment date
        (ascending).  Empty list if no qualifying transactions exist.
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
        amount = txn.effective_amount
        # Defensive: ensure Decimal even if effective_amount somehow
        # returns a non-Decimal from a DB column.
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))

        payments.append(PaymentRecord(
            payment_date=txn.pay_period.start_date,
            amount=amount,
            is_confirmed=txn.status.is_settled,
        ))

    return payments


def compute_contractual_pi(
    params: LoanParams,
    anchor_events: list | None = None,
    rate_changes: list[RateChangeRecord] | None = None,
    as_of: date | None = None,
    payments: list[PaymentRecord] | None = None,
) -> Decimal:
    """Return the SSOT monthly P&I number for a loan.

    Routes through :func:`loan_resolver.compute_monthly_payment_baseline`
    so the returned value is byte-identical to
    ``LoanState.monthly_payment`` -- the loan card, the schedule's
    projected rows, and the escrow-subtraction threshold in
    :func:`prepare_payments_for_engine` all converge on one number.

    The monthly P&I is the level payment of the rate period containing
    ``as_of``, derived from the loan's rate-change feed (its origination
    :class:`RateHistory` row plus any ARM adjustments).  DH-#56 retired
    the ``LoanParams.interest_rate`` column, so the rate now comes
    exclusively from ``rate_changes``; the prior legacy pure-LoanParams
    fallback (which read the column) is gone.  The value is independent
    of the running balance, so ``anchor_events`` and ``payments`` are
    accepted for caller compatibility only and are not read.

    Args:
        params: LoanParams model instance with ``original_principal``,
            ``term_months``, and the ARM cadence fields.
        anchor_events: Accepted for caller compatibility; unused (the
            period P&I does not depend on the anchor balance).
        rate_changes: The loan's rate-change feed (origination row plus
            any ARM adjustments).  Required -- an empty/``None`` feed
            raises in the resolver, because every loan must carry an
            origination :class:`RateHistory` row.
        as_of: Optional evaluation date.  Defaults to ``date.today()``.
        payments: Accepted for caller compatibility; unused.

    Returns:
        Decimal monthly P&I payment, or ``Decimal("0")`` when
        ``original_principal`` is NULL (defensive: the column is NOT
        NULL, so this is unreachable in practice).

    Raises:
        ValueError: When ``rate_changes`` is empty/``None`` (the
            origination-rate invariant is violated) -- surfaced by
            :func:`loan_resolver._periods._origination_rate`.
    """
    if params.original_principal is None:
        return Decimal("0")
    # Pylint: ``import-outside-toplevel`` -- local import avoids a
    # top-level circular import (loan_resolver imports ``load_loan_context``
    # from this module, so a top-level import here would be circular).
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    # ``anchor_events`` and ``payments`` are unused by the baseline (the
    # period P&I is anchor-independent); passed through for signature
    # compatibility.  The rate comes from ``rate_changes`` (DH-#56 retired
    # ``LoanParams.interest_rate``), so an empty feed raises in the
    # resolver rather than silently defaulting to a wrong payment.
    return loan_resolver.compute_monthly_payment_baseline(
        params,
        anchor_events or [],
        rate_changes,
        as_of or date.today(),
        payments=payments,
    )


def _redistribute_to_distinct_months(
    payments: list[PaymentRecord], payment_day: int
) -> list[PaymentRecord]:
    """Shift payments sharing a monthly DUE month to consecutive months.

    Biweekly pay periods sometimes place two mortgage payments in the same
    calendar month; the monthly engine would sum them, double-counting one
    month and leaving the next empty.  At most one extra payment per month
    (~2x/year) is expected, so cascading collisions are not, but the
    while-loop handles them defensively.  The collision key is the true
    monthly DUE month (``monthly_due_date`` of the pay-period start), NOT
    the pay-period-start month: two pay periods that both fall before the
    same ``payment_day`` (e.g. Apr 10 and Apr 24, both due May 1) collide on
    the May schedule row, and the schedule/override key everything by due
    month -- a pay-period-start-month key would leave that collision
    unresolved and sum both into a single double payment.
    """
    result: list[PaymentRecord] = []
    allocated_months: set[tuple[int, int]] = set()
    for p in payments:
        due = monthly_due_date(p.payment_date, payment_day)
        ym = (due.year, due.month)
        if ym not in allocated_months:
            result.append(p)
            allocated_months.add(ym)
        else:
            y, m = ym
            m += 1
            if m > 12:
                m = 1
                y += 1
            while (y, m) in allocated_months:
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            max_day = calendar.monthrange(y, m)[1]
            new_date = date(y, m, min(payment_day, max_day))
            result.append(PaymentRecord(
                payment_date=new_date,
                amount=p.amount,
                is_confirmed=p.is_confirmed,
            ))
            allocated_months.add((y, m))
    return result


def prepare_payments_for_engine(
    payments: list[PaymentRecord],
    payment_day: int,
    monthly_escrow: Decimal,
    contractual_pi: Decimal,
) -> list[PaymentRecord]:
    """Prepare payment records for the amortization engine.

    Corrects two mismatches between biweekly shadow transactions and
    the monthly amortization schedule:

    1. Escrow subtraction: Recurring transfers include escrow in their
       total amount, but the engine handles P&I only.  Without this
       correction, the engine treats escrow as extra principal, inflating
       paydown speed and showing escrow as spurious "Extra" entries.
       Only subtracts escrow from the portion that exceeds the standard
       P&I payment, so payments that do not include escrow are unaffected.

    2. Biweekly redistribution: Pay period start dates are biweekly and
       sometimes place two mortgage payments in the same calendar month
       (e.g., the Aug 1 payment falls in a Jul 29 pay period).  The
       engine sums same-month payments, double-counting one month and
       leaving the next empty.  This shifts extra same-month payments
       to subsequent months to restore one-payment-per-month alignment.

    Args:
        payments: List of PaymentRecord from get_payment_history().
        payment_day: Mortgage payment day of month (from LoanParams).
        monthly_escrow: Monthly escrow amount from escrow_calculator.
        contractual_pi: Standard monthly P&I payment (no escrow).

    Returns:
        Corrected list of PaymentRecord.
    """
    if not payments:
        return payments

    sorted_payments = sorted(payments, key=lambda p: p.payment_date)

    # Step 1: Subtract escrow from payments that include it.
    # Only subtract from the excess above contractual P&I so that
    # payments equal to or below P&I (no escrow included) are untouched.
    if monthly_escrow > Decimal("0.00"):
        adjusted = []
        for p in sorted_payments:
            if p.amount > contractual_pi:
                new_amount = p.amount - min(
                    monthly_escrow, p.amount - contractual_pi,
                )
            else:
                new_amount = p.amount
            adjusted.append(PaymentRecord(
                payment_date=p.payment_date,
                amount=new_amount,
                is_confirmed=p.is_confirmed,
            ))
        sorted_payments = adjusted

    # Step 2: Redistribute payments that share a monthly DUE month to
    # consecutive months so the monthly engine sees one per due month.
    return _redistribute_to_distinct_months(sorted_payments, payment_day)


def confirmed_loan_seed(
    account_id: int, scenario_id: int | None, as_of: date,
) -> Decimal | None:
    """Read the genesis-ledger confirmed loan balance for the resolver seed, or None.

    The read switch's SINGLE injection point (plan Section 8): the one and
    only call site of the genesis reader
    (:func:`app.services.loan_posting_service.confirmed_loan_balance_at`), so
    the whole app reads a loan's confirmed balance from the ledger through
    exactly this function.  Every ``forward_seed_balance`` the three db-facing
    loaders and the loan-detail chart / payoff calculators thread into the
    resolver comes from here, which is why the reader has one seam for the
    W9906 balance-producer fence to allowlist when it joins the fence (plan
    Section 11), and why the loaders cannot drift on HOW the ledger is read.

    Returns ``None`` -- so the caller falls back to the resolver's anchor
    replay, exactly today's behaviour -- whenever the confirmed ledger cannot
    answer:

    * ``scenario_id`` is ``None`` (no baseline scenario, so no scenario to
      scope postings to);
    * ``as_of`` is after today (a future date is a forward projection, out of
      the confirmed reader's domain -- the resolver projects it, and asking
      the reader would raise); or
    * the reader itself returns ``None`` -- the loan has no OPENING posting in
      the scenario: an unconfigured loan, a what-if the opening was never
      posted into (the C4 M2 case), or any loan not yet backfilled with
      genesis postings.

    For those three cases the ``None`` fallback makes the read switch safe by
    construction: a loan the ledger has not opened resolves exactly as it did
    before the switch.  The ONE case that does NOT fall back is a genuinely
    broken chart-of-accounts -- a loan account with no linked ledger account at
    all -- where the reader raises ``PostingError`` rather than returning
    ``None``, failing loud on the invariant violation (the project's fail-loud
    rule; :func:`._reader._ledger_account_for` is designed to raise, not paper
    over a missing pairing).  Every account is paired with a linked ledger by
    the account-create hook and the Step-2 backfill, so a configured loan cannot
    reach that path in practice.

    Args:
        account_id: The loan account whose confirmed balance to read.  The
            caller MUST have already established that the current user owns it
            (the reader trusts this arg, matching the sibling
            ``account_posting_total`` convention); the scenario scope is a
            second guard, since a cross-owner account has no postings in this
            user's scenario and so reads ``None``.
        scenario_id: The baseline scenario id, or ``None``.
        as_of: The evaluation date; typically ``date.today()``.

    Returns:
        The ledger-confirmed balance owed as a ``Decimal``, or ``None`` to
        fall back to the resolver's anchor replay.

    Raises:
        PostingError: If the loan account has no linked ledger account (a
            broken chart-of-accounts pairing, from
            :func:`._reader._ledger_account_for`) -- the one non-fallback path.
    """
    if scenario_id is None or as_of > date.today():
        return None
    # Pylint: ``import-outside-toplevel`` -- loan_posting_service's walk/sync
    # import this module (``load_loan_params`` etc.), so resolving the reader
    # here rather than at module top keeps the dependency one-directional (the
    # same pattern the loan_resolver imports use).  Imported from its defining
    # ``_reader`` submodule -- which imports nothing from here -- rather than the
    # package, so the static import graph carries no ``loan_payment_service ->
    # loan_posting_service`` cycle for the package's walk/sync back-edge to close.
    from app.services.loan_posting_service._reader import (  # pylint: disable=import-outside-toplevel
        confirmed_loan_balance_at,
    )
    return confirmed_loan_balance_at(account_id, scenario_id, as_of)


def resolve_loan_seeded(
    loan_inputs: "LoanInputs",
    account_id: int,
    scenario_id: int | None,
    as_of: date,
) -> "LoanState":
    """Resolve a loan with its genesis-ledger confirmed balance as the seed.

    The single injection helper the read switch (plan Section 8) routes the
    three db-facing loaders through -- :func:`resolve_account_loan`, the loan
    route's ``_resolve``, and the savings dashboard's
    ``_compute_loan_account`` -- so they cannot drift on HOW the ledger seeds
    the resolver: read the confirmed balance once via
    :func:`confirmed_loan_seed`, then run the pure resolver with it threaded as
    ``forward_seed_balance`` (which overrides BOTH the headline balance and the
    forward projection seed, so they cannot desync off-schedule).

    When the ledger cannot answer (``confirmed_loan_seed`` returns ``None``),
    the resolver falls back to its anchor replay -- the pre-switch behaviour --
    so this is safe for any loan the ledger has not opened.

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
    # Pylint: ``import-outside-toplevel`` -- loan_resolver imports from this
    # module (``load_loan_context``), so resolving it here rather than at
    # module top keeps the dependency one-directional.
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    seed = confirmed_loan_seed(account_id, scenario_id, as_of)
    return loan_resolver.resolve_loan(
        loan_inputs, as_of, forward_seed_balance=seed,
    )


def resolve_account_loan(
    account_id: int, scenario_id: int, today: date
) -> "tuple[LoanParams, LoanState] | None":
    """Load a debt account's ``LoanParams`` and run the resolver as of ``today``.

    The per-account "load LoanParams (skip if unconfigured), load anchor
    events + context, run the resolver" preamble shared by the debt-strategy
    route and the year-end schedule generation.  Centralizing it keeps the
    two consumers from drifting on HOW a loan account is resolved (which
    inputs feed :func:`loan_resolver.resolve_loan`, in what order).  Since the
    read switch (plan Section 8) it resolves through :func:`resolve_loan_seeded`
    so its ``current_balance`` is the genesis-ledger confirmed balance (falling
    back to the anchor replay when the ledger has not opened the loan).

    Returns ``None`` when the account has no ``LoanParams`` row (it is not a
    configured loan); the caller skips it.  Unlike
    :func:`_resolve_loan_piti`, an account WITH params but no anchor events
    is still resolved here -- both callers screen those out downstream
    (debt-strategy via its zero-balance/zero-payment guard, the year-end
    feed via the resulting empty schedule), so the no-anchor short-circuit
    that PITI needs is intentionally absent.

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
    # Pylint: ``import-outside-toplevel`` -- loan_resolver imports from this
    # module (``load_loan_context``), so resolving it here rather than at
    # module top keeps the dependency one-directional.
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    params = load_loan_params(account_id)
    if params is None:
        return None
    anchor_events = load_anchor_events(account_id)
    ctx = load_loan_context(account_id, scenario_id, params)
    state = resolve_loan_seeded(
        loan_resolver.LoanInputs(
            params, anchor_events, ctx.payments, ctx.rate_changes,
        ),
        account_id, scenario_id, today,
    )
    return params, state


def _resolve_loan_piti(
    loan_account_id: int, scenario_id: int, as_of: date
) -> Decimal | None:
    """Resolve a loan's full live monthly payment (P&I + escrow), or None.

    Returns None when the loan has no ``LoanParams`` row or no anchor
    events (it cannot be resolved, so its shadows keep their stored amount).
    ``resolve_loan(...).monthly_payment`` is the rate-period P&I;
    ``context.monthly_escrow`` adds the escrow component to reach PITI.
    """
    # Pylint: ``import-outside-toplevel`` -- loan_resolver imports nothing
    # from this module, so resolving it here rather than at module top
    # keeps the dependency one-directional.
    from app.services import loan_resolver  # pylint: disable=import-outside-toplevel
    params = load_loan_params(loan_account_id)
    if params is None:
        return None
    context = load_loan_context(loan_account_id, scenario_id, params)
    anchor_events = load_anchor_events(loan_account_id)
    if not anchor_events:
        return None
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            params, anchor_events, context.payments, context.rate_changes,
        ),
        as_of,
    )
    return state.monthly_payment + context.monthly_escrow


def live_loan_transfer_amounts(
    scenario_id: int,
    transactions: list,
) -> dict[int, Decimal]:
    """Return ``{transaction_id: live PITI}`` for derive-from-loan transfer shadows.

    The read-time analogue of a recurring loan payment's stored
    ``TransferTemplate.default_amount``: for every Projected,
    non-overridden shadow transaction whose parent transfer's template
    has ``derive_from_loan=True``, recompute the full monthly payment
    LIVE from the destination loan -- ``resolve_loan(...).monthly_payment``
    (the rate-period P&I) plus the loan's monthly escrow/components.  A
    balance/display consumer can then treat the stored transfer amount as
    a cache that cannot silently disagree with the loan card after an
    escrow or rate change.  Directly mirrors the salary-income
    live-recompute, :func:`app.services.income_service.live_projected_net`.

    Both shadow legs of a transfer (the checking-side expense and the
    loan-side income) share the transfer id, so both receive the same
    PITI -- preserving Transfer Invariant 3 in the projection.  The
    checking expense leg moves the checking balance; the loan income leg
    does not affect the loan balance (that is resolver-derived), but
    keeping both equal avoids any surface showing mismatched shadows.

    Boundary discipline: no Flask import; inputs are plain data, output a
    plain dict.  Returns an empty dict when no candidate transfer targets
    a derive-from-loan template -- the common case for non-loan transfers
    and every pre-existing template (the flag defaults False) -- after at
    most one transfer/template lookup, so the balance render is unchanged
    for loans that have not opted in.

    Args:
        scenario_id: Scenario to resolve each loan against.
        transactions: Already-loaded (user-scoped) :class:`Transaction`
            rows.  Each must expose ``transfer_id``, ``status`` (for
            ``is_projected``), ``is_override``, and ``id``.

    Returns:
        ``dict`` mapping transaction id to the live PITI Decimal; empty
        when no derive-from-loan transfer is present.
    """
    candidates = [
        txn for txn in transactions
        if txn.transfer_id is not None
        and is_projected(txn)
        and not txn.is_override
    ]
    if not candidates:
        return {}

    transfer_ids = {txn.transfer_id for txn in candidates}
    transfers = (
        db.session.query(Transfer)
        .options(joinedload(Transfer.template))
        .filter(Transfer.id.in_(transfer_ids))
        .all()
    )
    loan_by_transfer = {
        xfer.id: xfer.to_account_id
        for xfer in transfers
        if xfer.template is not None and xfer.template.derive_from_loan
    }
    if not loan_by_transfer:
        return {}

    # Resolve each distinct loan's full monthly payment (P&I + escrow)
    # once, then map it onto every candidate shadow of that loan.
    today = date.today()
    piti_by_loan: dict[int, Decimal] = {}
    for loan_account_id in set(loan_by_transfer.values()):
        piti = _resolve_loan_piti(loan_account_id, scenario_id, today)
        if piti is not None:
            piti_by_loan[loan_account_id] = piti

    overrides: dict[int, Decimal] = {}
    for txn in candidates:
        loan_account_id = loan_by_transfer.get(txn.transfer_id)
        if loan_account_id is None:
            continue
        piti = piti_by_loan.get(loan_account_id)
        if piti is not None:
            overrides[txn.id] = piti
    return overrides
