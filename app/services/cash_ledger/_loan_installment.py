"""
Shekel Budget App -- Cash ledger: what one loan INSTALLMENT costs.

Amount rule 4's per-INSTALLMENT tier: a loan's rate periods and payment day
(:class:`_LoanCashBasis`), and the rule that prices one installment against
them -- the DERIVE arm's installment P&I plus that installment's own escrow
plus any standing extra.  The MANUAL arm lived here too until plan step
X-au-g-2c-2; see the note at the foot of this file for where it went and why.

**Nothing here takes a ROW, and plan step X-au-f-2 is what re-typed it**
(ruling **R-BAL10**).  :func:`_installment_cash` took the payment SHADOW and
read two columns off it; the answer's home is the PARENT transfer now, which
carries the identical two facts under the identical names and is not a
:class:`~app.models.transaction.Transaction`.  So it takes the two VALUES --
the same shape :func:`app.services.loan_loaders.installment_for` beneath it
already had, and the same shape
:func:`app.services.settle_day.settle_day_from_columns` takes precisely so a
transfer can answer it.  The module names no model at all as a result.

**Every contractual term here resolves on the INSTALLMENT it governs, never on
a read date** (ruling **R-IJ**, plan step X-au-g-2b).  The basis holds the
loan's term SET -- a pure function of its params and its rate feed, dated by
nothing -- and :func:`_installment_cash` derives the installment date once and
reads both the P&I and the escrow on it.  Nothing in this module, or in
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
from datetime import date
from decimal import Decimal

from app.services import escrow_calculator, loan_resolver
from app.services.loan_loaders import (
    installment_for,
    load_loan_params,
    load_rate_changes,
)
from app.services.rate_period_engine import RatePeriod, period_for_date
from app.utils.money import round_money

@dataclass(frozen=True)
class _LoanCashBasis:
    """The two loan-level facts one installment's live cash is built from.

    Both fall out of ONE ``LoanParams`` load (:func:`_resolve_loan_basis`), so
    they are returned together rather than re-queried per row: the rate
    periods are the loan's TERMS over its whole life, the payment day the
    contractual constant that turns a payment into the installment it
    satisfies.

    **It holds the loan's term SET rather than one resolved P&I, and ruling
    R-IJ is why** (plan step X-au-g-2b).  A loan's contractual terms resolve on
    the INSTALLMENT they govern, never on a read date, so there is no such
    thing as "the loan's monthly P&I" for a whole pass to share: an ARM's
    December and January installments are governed by different periods when a
    recast falls between them.  What a pass CAN share is the period set, which
    is a pure function of the loan's params and its rate feed and depends on no
    date at all -- so it is resolved once per loan per pass here and each
    payment reads the period governing its own due date
    (:func:`_installment_cash`), exactly as its escrow already resolves on
    that date.

    Attributes:
        periods: The loan's ordered :class:`~app.services.rate_period_engine.RatePeriod`
            set (:func:`app.services.loan_resolver.resolve_periods`), each
            carrying the level P&I held constant for its span.  Non-empty for
            a configured loan: period 0 always starts at origination.
        payment_day: The loan's contractual day-of-month due day, 1-31, from
            :attr:`app.models.loan_params.LoanParams.payment_day` -- the
            fallback basis :func:`app.services.loan_loaders.installment_for`
            needs for a payment carrying no stored ``due_date``.
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
    so resolving the periods here and letting each payment pick its own period
    (:func:`_installment_cash`) reads the same figure from the same
    derivation on a date the pass no longer chooses.  The periods are a pure
    function of the params and the rate feed, so there is no date left to pin:
    a resolver reads no wall clock.

    The escrow term is deliberately NOT added here for the reason the P&I is
    no longer resolved here -- it is per-INSTALLMENT
    (:func:`_installment_cash`), not one figure per loan.  A future-dated
    escrow version means a December and a January payment carry different
    escrow, so the escrow must be resolved against each payment's own due date
    rather than folded into a single loan-level PITI; ruling R-IJ is that same
    rule, stated for the P&I term beside it.

    **It reads the loan's TERMS and nothing else, and that is what deletes a
    cycle three docstrings were built around.**  It used to run
    :func:`load_loan_context` and then ``resolve_loan(...).monthly_payment``,
    which put :func:`get_payment_history` on the pricing path -- so pricing a
    loan payment read the amounts of the loan's own payment rows, and that
    apparent circularity is why ``get_payment_history`` priced through
    ``cash_ledger.settled_contribution`` instead of the amount resolver, why its
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


def _installment_cash(
    basis: _LoanCashBasis,
    escrow_lines: list,
    due_date: "date | None",
    period_start: date,
    extra_principal: Decimal,
) -> Decimal:
    """Derive-mode live cash for one loan payment: its INSTALLMENT's P&I + escrow + extra.

    The single expression every reader of an AUTO-DERIVED loan payment's cash
    builds it from, so no two can disagree.  It backed a read-time override and
    a settle-time capture as one method until plan step X-au-g-2c-2 deleted
    both: a derive-mode payment stores no figure for an override to supersede,
    and a settle books what the amount model resolves
    (:func:`._amount_source.resolve_transfer_amount`), so the display figure and
    the booked one are one expression rather than two kept in step.

    **It takes the two DATING COLUMNS rather than the row that carries them,
    and plan step X-au-f-2 is what re-typed it** (ruling **R-BAL10**).  It was
    ``_shadow_live_amount(basis, escrow_lines, shadow, extra_principal)`` and
    read ``shadow.due_date`` and ``shadow.pay_period.start_date`` through
    :func:`app.services.loan_loaders.loan_payment_due_date`.  R-BAL10 puts a
    loan payment's amount on the PARENT transfer, which carries both of those
    facts under the same names and is not a
    :class:`~app.models.transaction.Transaction`, so a row-shaped reader could
    not be handed one.  Taking the values is the shape
    :func:`app.services.loan_loaders.installment_for` beneath it already had
    for exactly this reason -- the transfer WRITE boundary must date an
    installment before any row exists -- and the shape
    :func:`app.services.settle_day.settle_day_from_columns` takes *precisely so
    a transfer can answer it*.

    **BOTH contractual terms resolve on the payment's own DUE date, and that is
    ruling R-IJ** (plan step X-au-g-2b): the installment date
    (:func:`app.services.loan_loaders.installment_for`) is derived ONCE
    here and drives the P&I -- the level payment of the rate period containing
    it (:func:`~app.services.rate_period_engine.period_for_date`) -- and the
    escrow -- :func:`~app.services.escrow_calculator.escrow_monthly_as_of` on
    the same day.  One date for both is what makes them one installment's
    price rather than two answers about two moments; deriving it once rather
    than twice is what makes that structural.

    **The genesis charge resolves its rate period and its escrow from the
    identical date** (``loan_ledger._charges.charges_for_due_dates``,
    ``period_for_date(periods, on_date)``), so the cash built into a payment and
    the interest and escrow its split backs out of principal read one period and
    one escrow version, by construction (the cash==split invariant) rather than
    by coincidence.  **That still holds now the cash is dated from the PARENT
    and the split from the SHADOW**: ``due_date`` is a mirrored field with the
    parent canonical (``models/transfer.py``), written to all three rows in one
    statement by ``transfer_service._update`` and corrected on restore.  A
    census of every writer of a shadow's ``due_date`` or ``pay_period_id``
    across ``app/`` returns those three sites and no other -- no bulk update, no
    ``setattr`` splat reaches a leg -- so the two reads are one value.
    **It is one value with TWO HOMES kept equal by a maintenance contract,
    which is rule 14's own shape**: this step created the second read rather
    than inheriting it, and what deletes it is ``X-bi-6`` removing the shadow
    rows -- one row is left to date anything from.  **A charge is dated at the EARLIEST installment
    due in its accrual period, which for the one-payment-a-month shape IS this
    due date**; a SECOND payment in one period deliberately clears no fresh
    charge, so there the cash carries an escrow the split does not back out and
    the whole payment is principal (plan step X-au-g-2c-3b-2).
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
    **``round_money`` is ONE call over THREE terms and must not become two**
    (ruling E-26): summing then rounding once is the boundary, where
    ``round_money(round_money(pi + escrow) + extra)`` would double-round and
    part a cutover advertised as byte-identical from its predecessor by a cent.

    Args:
        basis: The loan's :class:`_LoanCashBasis` (:func:`_resolve_loan_basis`),
            resolved once per loan.  Taken WHOLE rather than unpacked by every
            caller: its rate periods and its payment day are two halves of one
            figure -- the payment day dates the installment whose period is
            read -- so passing them separately would let a call site pair one
            loan's terms with another's due day.
        escrow_lines: The loan's escrow lines with their full version history.
        due_date: The payment's own stored due date, or ``None``.  Both dating
            values come off ONE row at every call site, for the reason *basis*
            is taken whole.
        period_start: The start date of the payment's pay period -- the
            fallback basis, read on every call because
            :func:`~app.services.loan_loaders.installment_for` takes it eagerly.
        extra_principal: The recurring payment's standing extra principal
            (``0.00`` when none), from :func:`loan_payment_config`.

    Returns:
        ``round_money(period_for_date(basis.periods, due).period_pi
        + escrow_monthly_as_of(lines, due) + extra_principal)``, where ``due``
        is the installment this payment satisfies.
    """
    due = installment_for(due_date, period_start, basis.payment_day)
    monthly_pi = period_for_date(basis.periods, due).period_pi
    escrow = escrow_calculator.escrow_monthly_as_of(escrow_lines, due)
    return round_money(monthly_pi + escrow + extra_principal)


# ``_manual_shadow_amount`` lived here until plan step X-au-g-2c-2, and what
# deleted it is the state it read becoming unrepresentable rather than a
# caller changing its mind.  It derived a MANUAL-mode loan payment's cash as
# ``round_money(shadow.estimated_amount + extra_principal)`` -- the operator's
# stored base plus the standing extra -- under a docstring asserting that
# column was "the recurring base".  A transfer shadow stores no figure at all
# now: it declares ``PARENT_TRANSFER`` and reads its parent through the amount
# model, so the manual arm is the parent's own series price plus the extra
# (:func:`._amount_source._loan_payment_cash`) -- the DEFINITION's price
# rather than a copy of it, which is the same arm ruling **R-FI** gives every
# other manually-priced row.  Plan step X-au-f-2 moved that arm off the SHADOW
# dispatch onto the TRANSFER's (**R-BAL10**), which is where both arms of rule
# 4 now live.
#
# **The two answered the same number for every row that existed**: Transfer
# Invariant 3 held on the column, so a shadow's ``estimated_amount`` WAS its
# parent's ``amount`` (measured 2026-09-01 on production, stamp
# ``a4c6f1d92b73``: 0 of 350 shadows differed).  What is gone is the way they
# could come apart -- and the ordering hazard the balance README recorded,
# where plan step X-au-f would have NULLed the very column this read.
