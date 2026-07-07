"""
Shekel Budget App -- Loan Payment Service

Queries shadow income transactions on debt accounts and converts them
to PaymentRecord instances for the amortization engine.  Also provides
payment preparation utilities (escrow subtraction, biweekly
redistribution), a unified data-loading function (load_loan_context)
shared by all consumers of amortization schedules, and the read-switch
ledger-view builder (confirmed_loan_view).  The resolver-seeding wrappers that
read that view -- resolve_loan_seeded / resolve_account_loan -- live in
app.services.loan_resolution (they compose these loaders with the pure
resolver); this module does not import that one, so there is no cycle.

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

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import escrow_calculator, loan_resolver
from app.services.amortization_engine import PaymentRecord, RateChangeRecord
from app.services.loan_loaders import (
    _rate_change_records_from,
    load_escrow_lines,
    load_loan_anchor_facts,
    load_loan_params,
    load_rate_history,
    query_shadow_income,
)
from app.services.rate_period_engine import monthly_due_date
from app.utils.balance_predicates import is_projected
from app.utils.money import round_money

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
    rate_changes: list[RateChangeRecord] | None,
    as_of: date | None = None,
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
    of the running balance, so no anchor or payment feed is taken (the
    read-switch arc's final commit dropped the old unused
    compatibility parameters).

    Args:
        params: LoanParams model instance with ``original_principal``,
            ``term_months``, and the ARM cadence fields.
        rate_changes: The loan's rate-change feed (origination row plus
            any ARM adjustments).  Required -- an empty/``None`` feed
            raises in the resolver, because every loan must carry an
            origination :class:`RateHistory` row.
        as_of: Optional evaluation date.  Defaults to ``date.today()``.

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
    # The rate comes from ``rate_changes`` (DH-#56 retired
    # ``LoanParams.interest_rate``), so an empty feed raises in the
    # resolver rather than silently defaulting to a wrong payment.
    return loan_resolver.compute_monthly_payment_baseline(
        params, rate_changes, as_of or date.today(),
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
    escrow_lines: list,
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
       Each payment subtracts the escrow IN EFFECT ON ITS OWN DATE
       (:func:`~app.services.escrow_calculator.escrow_monthly_as_of` on the
       payment's pay-period start), NOT one current figure: once escrow can
       be future-dated, a payment made under the old escrow must have the old
       escrow backed out to recover its P&I, or the resolver's replay / payoff
       projection mis-attributes the escrow delta as extra principal.  This is
       the same date-keyed escrow the genesis split subtracts, so the two agree
       on every payment's P&I.

    2. Biweekly redistribution: Pay period start dates are biweekly and
       sometimes place two mortgage payments in the same calendar month
       (e.g., the Aug 1 payment falls in a Jul 29 pay period).  The
       engine sums same-month payments, double-counting one month and
       leaving the next empty.  This shifts extra same-month payments
       to subsequent months to restore one-payment-per-month alignment.

    Args:
        payments: List of PaymentRecord from get_payment_history().
        payment_day: Mortgage payment day of month (from LoanParams).
        escrow_lines: The loan's escrow lines with their full version history
            (:func:`~app.services.loan_loaders.load_escrow_lines`); each
            payment resolves its own as-of escrow from them.  Empty for a loan
            with no escrow, which skips the subtraction entirely.
        contractual_pi: Standard monthly P&I payment (no escrow).

    Returns:
        Corrected list of PaymentRecord.
    """
    if not payments:
        return payments

    sorted_payments = sorted(payments, key=lambda p: p.payment_date)

    # Step 1: Subtract each payment's as-of escrow from the portion that
    # exceeds contractual P&I, so payments equal to or below P&I (no escrow
    # included) are untouched.  Skipped entirely for a loan with no escrow
    # lines; a line that resolves to 0 on a given date (not yet in effect, or
    # a removal tombstone) subtracts nothing for that payment.
    if escrow_lines:
        adjusted = []
        for p in sorted_payments:
            escrow = escrow_calculator.escrow_monthly_as_of(
                escrow_lines, p.payment_date,
            )
            if escrow > Decimal("0.00") and p.amount > contractual_pi:
                new_amount = p.amount - min(
                    escrow, p.amount - contractual_pi,
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


def confirmed_loan_view(
    account_id: int, scenario_id: int | None, as_of: date,
) -> "loan_resolver.ConfirmedLedgerView | None":
    """Read a loan's genesis-ledger confirmed view (balance + history), or None.

    The read switch's SINGLE injection point: the one and only call site of
    the genesis balance / history readers
    (:func:`app.services.loan_posting_service.confirmed_loan_balance_at` /
    :func:`app.services.loan_posting_service.confirmed_loan_history_rows`),
    so the whole app reads a loan's confirmed state from the ledger through
    exactly this function.  Every
    :class:`~app.services.loan_resolver.ConfirmedLedgerView` the db-facing
    loaders and the loan-detail chart / payoff calculators thread into the
    resolver comes from here, which is why the readers have one seam for the
    W9906 balance-producer fence to allowlist, and why the loaders cannot
    drift on HOW the ledger is read.  Bundling the balance WITH its history
    rows in one read is what keeps the loan card, the amortization table's
    confirmed rows, and the forward projection on one producer -- they either
    all read the ledger or all fall back together.

    Returns ``None`` -- so the caller falls back to the resolver's anchor
    replay, exactly the pre-switch behaviour -- whenever the confirmed ledger
    cannot answer:

    * ``scenario_id`` is ``None`` (no baseline scenario, so no scenario to
      scope postings to);
    * ``as_of`` is after today (a future date is a forward projection, out of
      the confirmed readers' domain -- the resolver projects it, and asking
      a reader would raise); or
    * a reader returns ``None`` -- the loan has no OPENING posting in the
      scenario (an unconfigured loan, a what-if the opening was never posted
      into -- the C4 M2 case -- or any loan not yet backfilled), or no
      :class:`LoanParams` (the history reader's extra guard).

    For those cases the ``None`` fallback makes the read switch safe by
    construction: a loan the ledger has not opened resolves exactly as it did
    before the switch.  The ONE case that does NOT fall back is a genuinely
    broken chart-of-accounts -- a loan account with no linked ledger account at
    all -- where the readers raise ``PostingError`` rather than returning
    ``None``, failing loud on the invariant violation (the project's fail-loud
    rule).  Every account is paired with a linked ledger by the account-create
    hook and the Step-2 backfill, so a configured loan cannot reach that path
    in practice.

    Args:
        account_id: The loan account whose confirmed view to read.  The
            caller MUST have already established that the current user owns it
            (the readers trust this arg, matching the sibling
            ``account_posting_total`` convention); the scenario scope is a
            second guard, since a cross-owner account has no postings in this
            user's scenario and so reads ``None``.
        scenario_id: The baseline scenario id, or ``None``.
        as_of: The evaluation date; typically ``date.today()``.

    Returns:
        The :class:`~app.services.loan_resolver.ConfirmedLedgerView`, or
        ``None`` to fall back to the resolver's anchor replay.

    Raises:
        PostingError: If the loan account has no linked ledger account (a
            broken chart-of-accounts pairing) -- the one non-fallback path.
    """
    if scenario_id is None or as_of > date.today():
        return None
    # Pylint: ``import-outside-toplevel`` -- the confirmed-ledger readers
    # (``loan_posting_service``) are imported HERE, inside the read switch's
    # sole injection point, ON PURPOSE rather than at module top: it keeps the
    # posted-ledger reader out of module scope so this module's resolver-feeding
    # loaders (``load_loan_context`` and siblings) stay ledger-free.  That
    # property is enforced by ``TestResolverIsLedgerFree``, which scans this
    # module MINUS its read-switch functions -- a top-level ledger import would
    # fail it.  (``loan_resolver`` is a plain top-level import; only the ledger
    # reader must stay function-local.)
    from app.services import loan_posting_service  # pylint: disable=import-outside-toplevel
    balance = loan_posting_service.confirmed_loan_balance_at(
        account_id, scenario_id, as_of,
    )
    if balance is None:
        return None
    history_rows = loan_posting_service.confirmed_loan_history_rows(
        account_id, scenario_id, as_of,
    )
    if history_rows is None:
        # Belt-and-braces: the two readers share the opening-posting guard,
        # but the history reader additionally requires LoanParams.  A view
        # must be all-ledger or nothing -- never a ledger balance over replay
        # rows -- so an asymmetric answer falls back whole.
        return None
    return loan_resolver.ConfirmedLedgerView(
        balance=balance, history_rows=history_rows,
    )


def _resolve_loan_pi(
    loan_account_id: int, scenario_id: int, as_of: date
) -> Decimal | None:
    """Resolve a loan's live monthly P&I (no escrow) as of ``as_of``, or None.

    Returns ``None`` when the loan has no ``LoanParams`` row (it cannot be
    resolved, so its shadows keep their stored amount); a configured loan is
    always resolvable, since its origination anchor fact is synthesized from
    the immutable params.  ``resolve_loan(...).monthly_payment`` is the
    rate-period P&I; the escrow term is deliberately NOT added here because it
    is per-payment-DATE (:func:`_shadow_live_amount`), not one figure per loan
    -- a future-dated escrow version means a December and a January payment
    carry different escrow, so the escrow must be resolved against each
    shadow's own date rather than folded into a single loan-level PITI.

    Args:
        loan_account_id: The destination loan account to resolve.
        scenario_id: The budget scenario the payments live in.
        as_of: The evaluation date for the rate-period P&I.

    Returns:
        The loan's monthly P&I ``Decimal``, or ``None`` when the account is
        not a configured loan.
    """
    params = load_loan_params(loan_account_id)
    if params is None:
        return None
    context = load_loan_context(loan_account_id, scenario_id, params)
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(
            params, load_loan_anchor_facts(params),
            context.payments, context.rate_changes,
        ),
        as_of,
    )
    return state.monthly_payment


def _shadow_live_amount(
    monthly_pi: Decimal,
    escrow_lines: list,
    shadow: Transaction,
    extra_principal: Decimal,
) -> Decimal:
    """Derive-mode live cash for a loan-payment shadow: P&I + its DATE's escrow + extra.

    The single expression both the projected-display override
    (:func:`live_loan_transfer_amounts`) and the settle-time capture
    (:func:`live_loan_payment_amount`) build an AUTO-DERIVED loan payment's cash
    from, so they can never disagree.  The escrow term is
    :func:`~app.services.escrow_calculator.escrow_monthly_as_of` on the
    shadow's OWN pay-period start -- the exact date and function the genesis
    split reads (``_walk._replay_events``) -- so the cash built into a payment
    and the escrow its split subtracts are the same figure by construction
    (the cash==split invariant), never by coincidence.  ``extra_principal`` (the
    standing overpayment, spec Sec. 6) is added on top in BOTH the display and
    the settle freeze, and the split's residual ``cash - interest - escrow``
    lands it in principal automatically.  ``round_money`` holds the E-26
    sum-then-round boundary even though the terms are already 2dp.

    Args:
        monthly_pi: The loan's resolved monthly P&I (:func:`_resolve_loan_pi`),
            already resolved once per loan.
        escrow_lines: The loan's escrow lines with their full version history.
        shadow: The payment shadow whose ``pay_period.start_date`` dates the
            escrow resolution.
        extra_principal: The recurring payment's standing extra principal
            (``0.00`` when none), from :func:`_loan_payment_config`.

    Returns:
        ``round_money(monthly_pi + escrow_monthly_as_of(lines, shadow date)
        + extra_principal)``.
    """
    escrow = escrow_calculator.escrow_monthly_as_of(
        escrow_lines, shadow.pay_period.start_date,
    )
    return round_money(monthly_pi + escrow + extra_principal)


def _manual_shadow_amount(
    shadow: Transaction, extra_principal: Decimal,
) -> Decimal:
    """Manual-mode live cash for a loan-payment shadow: its RECURRING base + extra.

    In manual mode the operator owns the base cash (the typed ``default_amount``
    the generated shadow stores as its ``estimated_amount``); the cash does not
    re-derive P&I or escrow (decision D).  ``extra_principal`` is still added on
    top (spec Sec. 6.1, "extra added in BOTH modes"), and the settle freeze
    captures the same base + extra so the split routes the extra into principal.

    The base is ``estimated_amount`` (the recurring base), NOT ``effective_amount``:
    a projected shadow can carry an operator-typed ``actual_amount`` while still
    ``is_projected`` and NOT ``is_override`` (a grid full-edit does not set the
    override flag), and ``effective_amount`` would return that actual, stacking
    ``extra`` on a per-instance typed value.  Keying to ``estimated_amount`` (NOT
    NULL, always the generated base) makes manual mode COHERENT with derive mode,
    which likewise recomputes from config and ignores a pre-settle typed actual.

    Args:
        shadow: The projected loan-payment shadow whose recurring base is added to.
        extra_principal: The standing extra principal (``> 0`` at the call sites).

    Returns:
        ``round_money(shadow.estimated_amount + extra_principal)``.
    """
    base = shadow.estimated_amount
    if not isinstance(base, Decimal):
        base = Decimal(str(base))
    return round_money(base + extra_principal)


def _loan_payment_config(template: TransferTemplate) -> tuple[bool, Decimal]:
    """Return ``(derive_from_loan, extra_principal)`` for a transfer template.

    The single accessor for a recurring transfer's loan-payment settings
    (:class:`~app.models.loan_payment_settings.LoanPaymentSettings`, decision B),
    which live in a 1:1 table rather than on the generic template.  A template
    with NO settings row is not a loan payment: ``derive_from_loan`` defaults
    ``False`` and ``extra_principal`` ``Decimal("0.00")``, so the live-derive and
    overpayment machinery stays dormant for every investment contribution and
    generic transfer.  The ``settings`` relationship must already be loaded by
    the caller (the readers ``joinedload`` it) so this stays a pure in-memory
    read with no N+1.

    Args:
        template: The :class:`~app.models.transfer_template.TransferTemplate`
            whose loan-payment settings to read.

    Returns:
        ``(derive_from_loan, extra_principal)`` -- the settings row's values, or
        ``(False, Decimal("0.00"))`` when the template has no settings row.
    """
    settings = template.settings
    if settings is None:
        return False, Decimal("0.00")
    return settings.derive_from_loan, Decimal(str(settings.extra_principal))


def live_loan_payment_amount(
    shadow: Transaction, scenario_id: int,
) -> Decimal | None:
    """Live payment-date cash for a single loan payment to freeze at settlement, or None.

    The settle-time counterpart of :func:`live_loan_transfer_amounts`: the
    amount a loan-payment shadow should FREEZE as its actual cash when it
    settles, so a one-click "mark paid" records the live payment-date cash
    instead of the stale template estimate.  Same two modes as the projected
    override: DERIVE (P&I + escrow-as-of + extra, :func:`_shadow_live_amount`)
    or MANUAL with a standing extra (stored base + extra,
    :func:`_manual_shadow_amount`).  Because the frozen cash and the genesis
    split read the same figure on the shadow's own date, ``cash == split`` holds
    by construction -- closing the gap where a plain settle reverted to
    ``estimated_amount`` (the creation-time escrow, and no extra) and desynced
    the split.

    Returns ``None`` -- so the caller leaves the stored estimate / a typed
    actual untouched -- for any shadow that does NOT need a capture: no transfer,
    an operator ``is_override`` (the operator owns that amount), an
    already-settled (non-Projected) shadow, a transfer that is not a loan payment
    (no settings row), a MANUAL loan payment with no extra (its stored estimate
    already IS the cash), or a loan that cannot resolve.  Mirrors
    :func:`live_loan_transfer_amounts`'s candidate filter (transfer,
    ``is_projected``, not ``is_override``, needs-a-live-override), so the settle
    capture fires for precisely the set the projected override covers.

    The ``is_projected`` guard makes the freeze ONE-SHOT: at the genuine first
    settle the status flip happens inside ``update_transfer`` AFTER this runs,
    so the shadow is still Projected and the capture fires; a re-settle of an
    already-DONE shadow (a stale-tab click on the still-present mark-paid
    button, which the ``done -> done`` identity transition admits) resolves to
    ``None`` here, so the frozen ``actual_amount`` is never silently rewritten
    to a later live figure that was never paid.

    Args:
        shadow: The transfer shadow being settled (either leg -- both legs
            share the transfer id and pay period, so either resolves the same
            amount, preserving Transfer Invariant 3).
        scenario_id: The scenario to resolve the loan against.

    Returns:
        The live cash ``Decimal`` to freeze, or ``None`` when the shadow does
        not need a settle-time capture.
    """
    if (
        shadow.transfer_id is None
        or shadow.is_override
        or not is_projected(shadow)
    ):
        return None
    transfer = (
        db.session.query(Transfer)
        .options(
            joinedload(Transfer.template).joinedload(TransferTemplate.settings),
        )
        .filter(Transfer.id == shadow.transfer_id)
        .first()
    )
    if transfer is None or transfer.template is None:
        return None
    derive_from_loan, extra_principal = _loan_payment_config(transfer.template)
    if derive_from_loan:
        monthly_pi = _resolve_loan_pi(
            transfer.to_account_id, scenario_id, date.today(),
        )
        if monthly_pi is None:
            return None
        escrow_lines = load_escrow_lines(transfer.to_account_id)
        return _shadow_live_amount(
            monthly_pi, escrow_lines, shadow, extra_principal,
        )
    # Manual mode: freeze the typed base + the standing extra so the split
    # routes the extra into principal.  A manual payment with no extra needs no
    # capture -- the plain settle keeps its stored estimate.
    if extra_principal > Decimal("0.00"):
        return _manual_shadow_amount(shadow, extra_principal)
    return None


@dataclass(frozen=True)
class _LivePaymentConfig:
    """A loan-payment transfer's live-override config: mode, extra, and loan.

    Bundles the three facts :func:`live_loan_transfer_amounts` needs per
    candidate transfer so the per-shadow loop reads typed attributes instead of
    threading a 3-tuple.  ``loan_account_id`` is the transfer's destination loan
    (used only in derive mode, to resolve P&I / escrow).
    """

    derive_from_loan: bool
    extra_principal: Decimal
    loan_account_id: int


def _live_config_by_transfer(
    candidates: list,
) -> dict[int, "_LivePaymentConfig"]:
    """Map ``transfer_id -> _LivePaymentConfig`` for candidates needing a live override.

    Loads each candidate's transfer with its template + settings in one query
    (eager, no N+1) and keeps only the transfers that actually need a read-time
    override: a DERIVE-mode loan payment (its cash re-derives P&I + as-of escrow
    + extra) or a MANUAL loan payment carrying a standing extra (its stored base
    + extra).  A generic transfer (no settings row) and a manual payment with no
    extra keep their stored amount and are omitted, so the balance render is
    unchanged for everything that has not opted in.

    Args:
        candidates: The projected, non-overridden transfer shadows to resolve
            configs for (their ``transfer_id``s are the query keys).

    Returns:
        ``{transfer_id: _LivePaymentConfig}``; empty when no candidate transfer
        needs a live override.
    """
    transfer_ids = {txn.transfer_id for txn in candidates}
    transfers = (
        db.session.query(Transfer)
        .options(
            joinedload(Transfer.template).joinedload(TransferTemplate.settings),
        )
        .filter(Transfer.id.in_(transfer_ids))
        .all()
    )
    config: dict[int, _LivePaymentConfig] = {}
    for xfer in transfers:
        if xfer.template is None:
            continue
        derive, extra = _loan_payment_config(xfer.template)
        if not derive and extra <= Decimal("0.00"):
            continue
        config[xfer.id] = _LivePaymentConfig(
            derive_from_loan=derive,
            extra_principal=extra,
            loan_account_id=xfer.to_account_id,
        )
    return config


def live_loan_transfer_amounts(
    scenario_id: int,
    transactions: list,
) -> dict[int, Decimal]:
    """Return ``{transaction_id: live cash}`` for loan-payment transfer shadows.

    The read-time analogue of a recurring loan payment's stored
    ``TransferTemplate.default_amount``: for every Projected, non-overridden
    shadow transaction of a loan payment (:func:`_live_config_by_transfer`),
    recompute the cash LIVE so the stored transfer amount is a cache that cannot
    silently disagree with the loan card after an escrow, rate, or extra change.
    Two modes (spec Sec. 6):

    * **Derive** (``derive_from_loan``): ``resolve_loan(...).monthly_payment``
      (the rate-period P&I, resolved once per loan) + the escrow in effect on
      THAT shadow's pay-period start + the standing ``extra_principal``
      (:func:`_shadow_live_amount`).
    * **Manual with a standing extra**: the shadow's stored base +
      ``extra_principal`` (:func:`_manual_shadow_amount`); the base is
      operator-owned and not re-derived (decision D).

    Directly mirrors the salary-income live-recompute,
    :func:`app.services.income_service.live_projected_net`.

    Per-shadow escrow (not one PITI per loan) is mandatory once escrow can be
    future-dated: a December projected payment and a January projected payment
    must carry different escrow when a new version takes effect between them,
    and each must match the escrow its eventual split subtracts -- the same
    ``escrow_monthly_as_of`` on the same date.

    Both shadow legs of a transfer (the checking-side expense and the
    loan-side income) share the transfer id, so both receive the same
    PITI -- preserving Transfer Invariant 3 in the projection.  The
    checking expense leg moves the checking balance; the loan income leg
    does not affect the loan balance (that is resolver-derived), but
    keeping both equal avoids any surface showing mismatched shadows.

    Boundary discipline: no Flask import; inputs are plain data, output a
    plain dict.  Returns an empty dict when no candidate transfer needs a live
    override -- the common case for non-loan transfers, and for a manual loan
    payment with no standing extra -- after at most one transfer/template
    lookup, so the balance render is unchanged for loans that have not opted in.

    Args:
        scenario_id: Scenario to resolve each loan against.
        transactions: Already-loaded (user-scoped) :class:`Transaction`
            rows.  Each must expose ``transfer_id``, ``status`` (for
            ``is_projected``), ``is_override``, ``pay_period``, and ``id``.

    Returns:
        ``dict`` mapping transaction id to the live cash Decimal; empty
        when no loan-payment transfer needs a live override.
    """
    candidates = [
        txn for txn in transactions
        if txn.transfer_id is not None
        and is_projected(txn)
        and not txn.is_override
    ]
    if not candidates:
        return {}

    config_by_transfer = _live_config_by_transfer(candidates)
    if not config_by_transfer:
        return {}

    # Resolve each DERIVE-mode loan's monthly P&I once and load its escrow lines
    # once; the escrow itself is resolved per-shadow (each shadow's own date)
    # below, so two shadows of one loan with different pay-period starts pick up
    # different future-dated escrow.  Manual-mode transfers need neither -- their
    # base is the shadow's stored amount -- so only derive loans are resolved.
    today = date.today()
    derive_loan_ids = {
        cfg.loan_account_id for cfg in config_by_transfer.values()
        if cfg.derive_from_loan
    }
    pi_by_loan: dict[int, Decimal] = {}
    lines_by_loan: dict[int, list] = {}
    for loan_account_id in derive_loan_ids:
        monthly_pi = _resolve_loan_pi(loan_account_id, scenario_id, today)
        if monthly_pi is not None:
            pi_by_loan[loan_account_id] = monthly_pi
            lines_by_loan[loan_account_id] = load_escrow_lines(loan_account_id)

    overrides: dict[int, Decimal] = {}
    for txn in candidates:
        cfg = config_by_transfer.get(txn.transfer_id)
        if cfg is None:
            continue
        if cfg.derive_from_loan:
            monthly_pi = pi_by_loan.get(cfg.loan_account_id)
            if monthly_pi is not None:
                overrides[txn.id] = _shadow_live_amount(
                    monthly_pi, lines_by_loan[cfg.loan_account_id],
                    txn, cfg.extra_principal,
                )
        else:
            # Manual mode with a standing extra (the config filter guarantees
            # extra > 0 here): stored base + extra, no re-derivation.
            overrides[txn.id] = _manual_shadow_amount(txn, cfg.extra_principal)
    return overrides
