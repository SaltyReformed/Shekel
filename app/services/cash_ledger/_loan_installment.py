"""
Shekel Budget App -- Cash ledger: what one loan INSTALLMENT costs.

Amount rule 4's per-shadow tier: a loan's rate periods and payment day
(:class:`_LoanCashBasis`), and the two rules that price one shadow against
them -- the DERIVE arm's installment P&I plus that installment's own escrow
plus any standing extra, and the MANUAL arm's operator-owned base plus the
extra.

**Every contractual term here resolves on the INSTALLMENT it governs, never on
a read date** (ruling **R-IJ**, plan step X-au-g-2b).  The basis holds the
loan's term SET -- a pure function of its params and its rate feed, dated by
nothing -- and :func:`_shadow_live_amount` derives the shadow's due date once
and reads both the P&I and the escrow on it.  Nothing in this module, or in
the package above it, reads a wall clock.

**It lives in THIS package rather than in ``loan_payment_service``, and plan
step X-au-g-2a is what moved it.  This is the ONE place that argument is
written; every other site states the conclusion and points here.**  Rule 4's
producer answers *what does this row's amount resolve to*, which is the amount
model's own question rather than the loan reader's -- so hosting it a tier UP
forced the amount model to reach into ``loan_payment_service`` for it, and
:mod:`app.services.row_valuation` exists as a separate leaf only because of
that reach.  Moving the producer DOWN deletes it rather than routing around
it: this module names only loan TERM primitives (``loan_loaders``,
``loan_resolver``, ``escrow_calculator``), none of which names the cash ledger,
so the arrow runs one way and the loan READING tier is free to import this
package -- which plan step X-au-g-2c SPENT, routing
``loan_payment_service.get_payment_history`` through the amount model.  The
unwind is the one :mod:`app.services.row_valuation` says plan step ``X-au-g``
owes.

**THE CYCLE WAS REAL AND THE GATE COULD NOT SEE IT, which is why the number
this argument used to quote has been replaced by a measurement with a date on
it.**  Both this file and three others said a module-level ``cash_ledger``
import anywhere in the loan stack "raised EIGHT ``cyclic-import`` findings"
(measured 2026-08-12).  Re-measured 2026-08-31 while making this move, on a
clean ``git archive HEAD``, with ``pylint app/ --disable=all
--enable=cyclic-import`` and the exit code read unpiped:

  ==================================================  =======
  tree / experiment                                   R0401
  ==================================================  =======
  HEAD, untouched                                     0
  HEAD + the loan stack importing this package        **0**
  HEAD + that import, the ONE masking line deleted    **7**
  post-move + that import                             0
  post-move + that import, every mask here deleted    **0**
  ==================================================  =======

**pylint keys its excluded-edge set by ``(module, imported module)`` with no
line granularity and excludes on ``in_type_checking_block``**, so
``_amount_basis``'s ``if TYPE_CHECKING:`` import of ``loan_payment_service``
suppressed the finding for the RUNTIME function-level import of the SAME
module.  Row three deletes that ONE line and nothing else; row five deletes
every remaining mask in this package, and is the only arm a mask cannot
explain.  The count is not stable either -- pylint's cycle enumeration shares
one visited set per root -- so what is measured is that the cycle EXISTED,
that the gate reported nothing about it, and that after this move the
experiment is green with the masks gone.  That is the difference between a
green that is STRUCTURAL and one that is masked, and it is the whole
evidential case for the move.  **The masking is not this step's to fix and is
filed rather than repaired** (rule 6, finding **N-416**): asked with pylint's
own resolver, ``app/`` carries 49 excluded edges over 22 modules and **11 of
them are MASKS, over 9 modules** -- a target imported under
``if TYPE_CHECKING:`` and again at runtime.

**It reads the loan's TERMS and never its payment rows**, which is what makes
this leaf independent of the payment-history tier rather than merely ordered
after it.  The cycle that used to run through
:func:`app.services.loan_payment_service.load_loan_context` is deleted; see
:func:`_resolve_loan_basis`.

Imports no sibling, so it is the bottom of this package's pricing line:
``_loan_installment`` -> :mod:`._loan_pricing` -> :mod:`._amount_basis` ->
:mod:`._amount_source`.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.transaction import Transaction
from app.services import escrow_calculator, loan_resolver
from app.services.loan_loaders import (
    load_loan_params,
    load_rate_changes,
    loan_payment_due_date,
)
from app.services.rate_period_engine import RatePeriod, period_for_date
from app.utils.money import round_money

@dataclass(frozen=True)
class _LoanCashBasis:
    """The two loan-level facts a shadow's live cash is built from.

    Both fall out of ONE ``LoanParams`` load (:func:`_resolve_loan_basis`), so
    they are returned together rather than re-queried per shadow: the rate
    periods are the loan's TERMS over its whole life, the payment day the
    contractual constant that turns a shadow into the installment it satisfies.

    **It holds the loan's term SET rather than one resolved P&I, and ruling
    R-IJ is why** (plan step X-au-g-2b).  A loan's contractual terms resolve on
    the INSTALLMENT they govern, never on a read date, so there is no such
    thing as "the loan's monthly P&I" for a whole pass to share: an ARM's
    December and January installments are governed by different periods when a
    recast falls between them.  What a pass CAN share is the period set, which
    is a pure function of the loan's params and its rate feed and depends on no
    date at all -- so it is resolved once per loan per pass here and each
    shadow reads the period governing its own due date
    (:func:`_shadow_live_amount`), exactly as its escrow already resolves on
    that date.

    Attributes:
        periods: The loan's ordered :class:`~app.services.rate_period_engine.RatePeriod`
            set (:func:`app.services.loan_resolver.resolve_periods`), each
            carrying the level P&I held constant for its span.  Non-empty for
            a configured loan: period 0 always starts at origination.
        payment_day: The loan's contractual day-of-month due day, 1-31, from
            :attr:`app.models.loan_params.LoanParams.payment_day` -- the
            fallback basis :func:`app.services.loan_loaders.loan_payment_due_date`
            needs for a shadow carrying no stored ``due_date``.
    """

    periods: list[RatePeriod]
    payment_day: int


def _resolve_loan_basis(loan_account_id: int) -> _LoanCashBasis | None:
    """Resolve a loan's rate periods and payment day, or ``None``.

    Returns ``None`` when the loan has no ``LoanParams`` row (it cannot be
    resolved, so its shadows keep their stored amount); a configured loan is
    always resolvable, since its origination anchor fact is synthesized from
    the immutable params.

    **It takes no date, and ruling R-IJ is what deleted the one it used to
    take** (plan step X-au-g-2b).  It resolved
    ``compute_monthly_payment_baseline(params, rate_changes, as_of)`` into a
    single ``monthly_pi`` -- one P&I, pinned at the read pass's wall clock,
    applied to every installment the pass priced (finding **N-40**).  That is
    the same producer this now calls, DECOMPOSED rather than replaced:
    ``compute_monthly_payment_baseline`` is by its own definition
    ``period_for_date(resolve_periods(params, rate_changes), as_of).period_pi``,
    so resolving the periods here and letting each shadow pick its own period
    (:func:`_shadow_live_amount`) reads the same figure from the same
    derivation on a date the pass no longer chooses.  The periods are a pure
    function of the params and the rate feed, so there is no date left to pin:
    a resolver reads no wall clock.

    The escrow term is deliberately NOT added here for the reason the P&I is
    no longer resolved here -- it is per-INSTALLMENT
    (:func:`_shadow_live_amount`), not one figure per loan.  A future-dated
    escrow version means a December and a January payment carry different
    escrow, so the escrow must be resolved against each shadow's own due date
    rather than folded into a single loan-level PITI; ruling R-IJ is that same
    rule, stated for the P&I term beside it.

    **It reads the loan's TERMS and nothing else, and that is what deletes a
    cycle three docstrings were built around.**  It used to run
    :func:`load_loan_context` and then ``resolve_loan(...).monthly_payment``,
    which put :func:`get_payment_history` on the pricing path -- so pricing a
    loan payment read the amounts of the loan's own payment rows, and that
    apparent circularity is why ``get_payment_history`` priced through
    ``cash_ledger.owned_contribution`` instead of the amount resolver, why its
    docstring concluded "the loan-side INCOME leg must keep owning its figure,
    and only the checking-side EXPENSE leg can be declared derived".

    **That is not finding N-259, and conflating the two was a first draft's
    error worth naming.**  N-259 was a WRITE-BACK cycle one layer up -- a
    settle refreshed the amount, so a settle / revert / settle compounded the
    standing extra -- and it is CLOSED at plan step ``balance:X-au-c3``
    (`3d1379d1`), which made a settle RECORD what moved.  Stating it in the
    present tense is the shape this project has already paid for: an undated
    claim quoted as a REASON decays invisibly, because nobody re-checks a
    premise.

    **The cycle never closed for the value that actually flowed.**  Exactly one
    field left ``resolve_loan`` here -- ``monthly_payment`` -- and that is
    ``period_for_date(resolve_periods(params, rate_changes), as_of).period_pi``
    (``loan_resolver._state``), whose producer
    :func:`~app.services.loan_resolver.resolve_periods` takes the params and the
    rate feed and NO payments and NO anchors.  So the whole payment history was
    loaded to derive a figure independent of it, and
    :func:`~app.services.loan_resolver.compute_monthly_payment_baseline` is the
    cheap producer documented as returning the same value for the same inputs.

    Measured on a production clone 2026-08-31, both live loans, at the read
    date then current, by
    ``tests/manual/verify_loan_pricing_ignores_payment_feed.py``: the Mortgage
    answers ``1293.96`` and the Van Loan ``531.94`` from the FULL 29-record
    history, an EMPTY one, the confirmed 5 alone, a DOUBLED 58, and the cheap
    producer -- five ways, one figure each.  *That harness pins a DATE because
    the payment-feed independence it grades is a claim about one figure; this
    function no longer takes one, so the harness now asks the period set for
    the P&I governing that date.*

    The DOUBLED feed is the arm that grades the CLAIM rather than the number,
    and the harness states why; the suite carries the same distinction as two
    tests with disjoint fail sets
    (``test_loan_payment_service.TestALoansPriceDoesNotReadItsOwnPayments``).

    **The scenario argument went with the history**, which is the honest
    signal: a loan's contractual P&I is not scenario-scoped, and the parameter
    only ever existed to scope the payment rows this no longer reads.

    Args:
        loan_account_id: The destination loan account to resolve.

    Returns:
        The loan's :class:`_LoanCashBasis`, or ``None`` when the account is
        not a configured loan.
    """
    params = load_loan_params(loan_account_id)
    if params is None:
        return None
    return _LoanCashBasis(
        periods=loan_resolver.resolve_periods(
            params, load_rate_changes(loan_account_id),
        ),
        payment_day=params.payment_day,
    )


def _shadow_live_amount(
    basis: _LoanCashBasis,
    escrow_lines: list,
    shadow: Transaction,
    extra_principal: Decimal,
) -> Decimal:
    """Derive-mode live cash for a loan-payment shadow: its INSTALLMENT's P&I + escrow + extra.

    The single expression both the projected-display override
    (:meth:`LoanPricing.live_cash`) and the settle-time capture (the SAME
    method since plan step X-au-c2b, which collapsed the two functions that
    answered this into one) build an AUTO-DERIVED loan payment's cash from, so
    they can never disagree.

    **BOTH contractual terms resolve on the shadow's own DUE date, and that is
    ruling R-IJ** (plan step X-au-g-2b): the due date
    (:func:`app.services.loan_loaders.loan_payment_due_date`) is derived ONCE
    here and drives the P&I -- the level payment of the rate period containing
    it (:func:`~app.services.rate_period_engine.period_for_date`) -- and the
    escrow -- :func:`~app.services.escrow_calculator.escrow_monthly_as_of` on
    the same day.  One date for both is what makes them one installment's
    price rather than two answers about two moments; deriving it once rather
    than twice is what makes that structural.

    **The genesis split resolves its rate period from the identical date**
    (``loan_ledger._split.split_one_payment``, ``period_for_date(periods,
    due_date)``), so the cash built into a payment and the interest and escrow
    its split backs out of principal read one period and one escrow version,
    by construction (the cash==split invariant) rather than by coincidence.
    Until R-IJ that held for the escrow alone: the P&I came from whatever
    period contained the READ date, so on an ARM whose rate had adjusted
    between the two the residual ``cash - interest - escrow`` absorbed the
    recast delta as PRINCIPAL (finding **N-40**).

    **Why the DUE date and not the pay-period start** (ruling D5, finding
    N-34): a pay period begins up to ~2 weeks before the installment it pays,
    so a version effective inside that window would build one figure into the
    cash and back a different one out of the split, silently moving the
    difference into principal.  Contract time governs both ends.  R-IJ is that
    same argument for the P&I term, and rejects the read pass's own ``as_of``
    on the same ground it rejects the period start: one figure for every
    installment is still the wrong figure for all but one of them.

    ``extra_principal`` (the standing overpayment, spec Sec. 6) is added on top
    in BOTH the display and the settle freeze, and the split's residual
    ``cash - interest - escrow`` lands it in principal automatically.
    ``round_money`` holds the E-26 sum-then-round boundary even though the terms
    are already 2dp.

    Args:
        basis: The loan's :class:`_LoanCashBasis` (:func:`_resolve_loan_basis`),
            resolved once per loan.  Taken WHOLE rather than unpacked by every
            caller: its rate periods and its payment day are two halves of one
            figure -- the payment day dates the installment whose period is
            read -- so passing them separately would let a call site pair one
            loan's terms with another's due day.
        escrow_lines: The loan's escrow lines with their full version history.
        shadow: The payment shadow whose installment dates BOTH resolutions.
        extra_principal: The recurring payment's standing extra principal
            (``0.00`` when none), from :func:`loan_payment_config`.

    Returns:
        ``round_money(period_for_date(basis.periods, due).period_pi
        + escrow_monthly_as_of(lines, due) + extra_principal)``, where ``due``
        is the shadow's installment date.
    """
    due = loan_payment_due_date(shadow, basis.payment_day)
    monthly_pi = period_for_date(basis.periods, due).period_pi
    escrow = escrow_calculator.escrow_monthly_as_of(escrow_lines, due)
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

    The base is ``estimated_amount`` (the recurring base), which is what keeps
    manual mode COHERENT with derive mode: that arm recomputes from config and
    names no per-instance figure either.

    **The reason this used to be a DISTINCTION is gone, and saying so beats
    leaving a rationale that names a state nothing can reach** (plan step
    X-au-c3).  It read: a projected shadow could carry an operator-typed
    ``actual_amount`` while still ``is_projected`` and not ``is_override``, and
    the row's CONTRIBUTION would return that actual, stacking ``extra`` on a
    per-instance typed value.  A figure now RECORDS a settle, and what keeps one
    out of this answer is the STATUS: ``row_valuation.settled_figure`` returns
    ``None`` for an unsettled row whatever it still carries, and
    :meth:`LoanPricing.live_cash` gates on ``is_projected``.  So the two
    expressions answer the same number for every reachable input.  (An earlier
    draft credited ``ck_transactions_settled_amount_needs_basis``; that CHECK
    says nothing about status, and an unsettled row carrying a recorded figure
    is the legal RETAINED state.  The conclusion held, the reason did not.)  The column is
    still the right thing to name, because it is what the RECURRENCE writes and
    what the extra is defined against; it is simply no longer a choice between
    two answers.

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
