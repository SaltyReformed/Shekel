"""
Shekel Budget App -- Cash ledger: the read pass's LOAN-PAYMENT derivation.

:class:`LoanPricing` is amount rule 4's producer: what one loan-payment shadow's
installment costs, with each destination loan resolved at most once per read
pass.  Lazy, so a pass that prices no loan payment issues no query.

Sits above :mod:`._loan_installment`, which owns the per-installment rules it
delegates to, and below :mod:`._amount_basis`, which holds ONE of these per
read pass.

**Moved here from ``loan_payment_service`` at plan step X-au-g-2a.**  The
argument for the move is written once, in :mod:`._loan_installment`.

**It is a MEMO now and no longer a repair, and plan step X-au-g-2c-2 is what
took the second half.**  It carried :meth:`live_cash` -- the read-time override
that SUPERSEDED a loan-payment shadow's stored figure on every balance and
display surface -- together with the scenario-wide ``{transfer_id: config}``
map that decided which shadows it fired for.  Ruling **R-FI** deletes a
read-time repair by making the state it repairs unrepresentable, and that is
what a shadow declaring ``PARENT_TRANSFER`` with no stored figure does: there
is no stale copy left to supersede, so the override has nothing to find and
:func:`._amount_source._loan_payment_answer` prices the row directly.

**Deleting that map took the package's ONLY ``budget.transfers`` query with
it.**  ``_load_live_payment_configs`` INNER-joined the scenario's transfers
through their template to ``loan_payment_settings`` to discover which of them
were loan payments -- a question the amount model now asks of the parent it was
handed, per row, off a relationship (ruling **R-FK**).  :mod:`._events` invokes
Transfer Invariant 5 as a principle of this package ("the same reason the
projection engine never queries ``Transfer`` directly"), and these thirteen
modules make no statement against that table at all again.

**It takes no SCENARIO either, and that is the same deletion one level down.**
The scenario only ever scoped the config map; a loan's terms are not
scenario-scoped, which :func:`._loan_installment._resolve_loan_basis` already
recorded when it lost its own scenario argument at plan step X-au-g-1 (*"the
parameter only ever existed to scope the payment rows this no longer reads"*).
The pin a caller can still get wrong -- pricing a row against another
scenario's basis -- is refused by
:func:`._amount_source.resolve_transaction_amount`, which asks the row's own
``scenario_id`` column and is TOTAL where the map's membership never was.
"""

from decimal import Decimal

from app.models.transaction import Transaction
from app.services.loan_loaders import load_escrow_lines
from ._loan_installment import (
    _LoanCashBasis,
    _resolve_loan_basis,
    _shadow_live_amount,
)


class LoanPricing:
    """Each destination loan's terms, resolved at most once per read pass.

    **The DERIVATION half of amount rule 4, split from its per-row lookup at
    plan step X-au-c2b.**  Everything expensive behind a loan payment's figure
    -- the destination loan's rate-period set, its contractual payment day and
    its escrow line history -- is scoped by the LOAN and by nothing about which
    rows a caller happens to have loaded.  Keyed that way, one read pass
    resolves each loan once however many row sets ask
    (:class:`~app.services.cash_ledger.AmountBasis`).

    It was two ``{transaction_id: Decimal}`` producers built per row set until
    that step -- ``live_loan_transfer_amounts`` for a display row set and
    ``live_loan_payment_amount`` for one settling shadow -- and the second
    function's own docstring said it *"mirrors live_loan_transfer_amounts'
    candidate filter ... so the settle capture fires for precisely the set the
    projected override covers"*.  Two implementations of one rule, kept in step
    by hand, is what this class collapsed; the cost of the split had been
    findings **N-268** and **N-269**, a request that priced two row sets paying
    the loan resolve twice.

    **The derivation is LAZY**, so a read pass whose rows hold no loan payment
    pays nothing at all: the per-loan resolve only runs for a loan a shadow
    actually names.  That is the "no query when there are no candidates"
    property the row-set producers had, kept rather than traded away.

    **IT READS NO CLOCK, and plan step X-au-g-2b is what deleted the one it
    used to read.**  It took an ``as_of`` and resolved each loan's rate-period
    P&I against it -- one figure per pass, applied to every installment the
    pass priced, which is finding **N-40**.  Ruling **R-IJ** put a loan's
    contractual terms on the INSTALLMENT they govern, as ruling D5 had already
    put a payment's escrow, so there is no pass-level date left to pin: the
    per-loan resolve (:func:`._loan_installment._resolve_loan_basis`) answers
    the loan's term SET, which no date parameterises, and each shadow reads the
    period governing its own due date.  What a whole pass now shares is the
    derivation rather than an answer.

    **It states no ownership rule and reads no status, which is where it
    differs from the ``live_cash`` it replaced** (plan step X-au-g-2c-2).  That
    method answered ``None`` for an operator ``is_override``, an already-settled
    shadow, or a manual payment with no standing extra, because a read-time
    REPAIR is a question about which stored figure to supersede.  Pricing is
    not: whether a row COUNTS and who last touched it are finding **N-262**'s
    separate question, answered above this tier.
    """

    def __init__(self) -> None:
        """Resolve nothing; each loan is loaded on first use and kept."""
        self._loans: "dict[int, tuple[_LoanCashBasis | None, list]]" = {}

    def _loan(self, loan_account_id: int) -> "tuple[_LoanCashBasis | None, list]":
        """Return ``(basis, escrow lines)`` for one loan, resolving it at most once.

        Membership, never truthiness: the basis is legitimately ``None`` for an
        account carrying no ``LoanParams``, and a truthiness check would
        re-resolve that on every shadow of every pass.

        Args:
            loan_account_id: The destination loan account to resolve.

        Returns:
            The loan's :class:`_LoanCashBasis` (``None`` when it is not a
            configured loan) paired with its escrow lines (empty then).
        """
        if loan_account_id not in self._loans:
            basis = _resolve_loan_basis(loan_account_id)
            lines = [] if basis is None else load_escrow_lines(loan_account_id)
            self._loans[loan_account_id] = (basis, lines)
        return self._loans[loan_account_id]

    def derive_cash(
        self,
        shadow: Transaction,
        loan_account_id: int,
        extra_principal: Decimal,
    ) -> "Decimal | None":
        """Return a DERIVE-mode shadow's cash: P&I + its installment's escrow + extra.

        **Amount rule 4's derive arm, and it reads no status** -- not
        ``is_projected``, not ``is_override``, not ``is_deleted``.  That is
        finding **N-262**'s rule one tier down: those three say whether a row
        COUNTS and who last touched it, never what prices it.

        Args:
            shadow: The payment shadow whose installment dates the escrow.
                Either leg resolves the same figure -- both share the transfer
                id, the pay period and the due date -- so Transfer Invariant 3
                is preserved whichever is passed.
            loan_account_id: The destination loan to resolve.
            extra_principal: The recurring payment's standing extra principal
                (``0.00`` when none), from
                :func:`~app.services.recurring_transfer_query.loan_payment_config`.

        Returns:
            The derived cash, or ``None`` when the loan will not resolve -- an
            account carrying no ``LoanParams``, which rule 4 turns into a
            refusal rather than a fallback to a stored snapshot.
        """
        basis, escrow_lines = self._loan(loan_account_id)
        if basis is None:
            return None
        return _shadow_live_amount(basis, escrow_lines, shadow, extra_principal)


def loan_pricing() -> LoanPricing:
    """Return a read pass's :class:`LoanPricing`.

    The named constructor the amount model calls, so no caller reaches for the
    class directly.  Resolves nothing: the derivation behind it is lazy, so a
    pass that prices no loan payment issues no query.

    It took an ``as_of`` until plan step X-au-g-2b and a ``scenario_id`` until
    X-au-g-2c-2, and the argument for the absence of both is on
    :class:`LoanPricing`: a loan's contractual terms resolve on the installment
    they govern (ruling **R-IJ**) and are not scenario-scoped, so a read pass
    has neither a date nor a scenario to hand this.

    Returns:
        The unresolved :class:`LoanPricing` handle.
    """
    return LoanPricing()
