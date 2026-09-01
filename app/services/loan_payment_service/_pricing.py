"""
Shekel Budget App -- The read pass's LOAN-PAYMENT derivation.

:class:`LoanPricing` is amount rule 4's producer, pinned to a scenario and an
``as_of``: which transfers are loan payments, and what each one's shadow costs.
Lazy, so a pass that prices no loan payment issues no query.

Sits above :mod:`._basis`, which owns the per-installment rules it delegates to.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import contains_eager

from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.loan_loaders import load_escrow_lines
from app.services.recurring_transfer_query import loan_payment_config
from app.utils.balance_predicates import is_projected
from ._basis import (
    _LoanCashBasis,
    _manual_shadow_amount,
    _resolve_loan_basis,
    _shadow_live_amount,
)

@dataclass(frozen=True)
class _LivePaymentConfig:
    """A loan-payment transfer's live-override config: mode, extra, and loan.

    Bundles the three facts :class:`LoanPricing` needs per loan-payment transfer
    so the per-shadow rule reads typed attributes instead of threading a
    3-tuple.  ``loan_account_id`` is the transfer's destination loan (used only
    in derive mode, to resolve P&I / escrow).
    """

    derive_from_loan: bool
    extra_principal: Decimal
    loan_account_id: int


class LoanPricing:
    """Everything a loan-payment shadow's live cash needs, resolved per read pass.

    **The DERIVATION half of amount rule 4, split from its per-row lookup at
    plan step X-au-c2b.**  Every expensive thing behind a loan payment's live
    figure -- which transfers in the scenario are loan payments at all, and each
    destination loan's rate-period P&I, contractual payment day and escrow line
    history -- is scoped by the SCENARIO and by the LOAN, and by nothing about
    which rows a caller happens to have loaded.  Keyed that way, one read pass
    resolves each loan once however many row sets ask
    (:class:`~app.services.cash_ledger.AmountBasis`).

    It was two ``{transaction_id: Decimal}`` producers built per row set until
    that step -- ``live_loan_transfer_amounts`` for a display row set and
    ``live_loan_payment_amount`` for one settling shadow -- and the second
    function's own docstring said it *"mirrors live_loan_transfer_amounts'
    candidate filter ... so the settle capture fires for precisely the set the
    projected override covers"*.  Two implementations of one rule, kept in step
    by hand, is what this class collapses: :meth:`live_cash` is that rule, and
    both callers ask it.  The cost of the split was findings **N-268** and
    **N-269** -- a request that priced two row sets paid the transfer/template
    lookup and the loan resolve twice.

    **Both derivations are LAZY**, so a read pass whose rows hold no loan
    payment pays nothing at all: :meth:`live_cash` answers ``None`` from
    ``transfer_id`` alone before it touches :attr:`config_by_transfer`, and the
    per-loan resolve only runs for a loan a shadow actually names.  That is the
    "no query when there are no candidates" property the row-set producers had,
    kept rather than traded away.

    **The clock is read ONCE, at construction, and that is a disclosure rather
    than a fix.**  Resolving a loan's rate-period P&I ``as_of`` the wall clock
    is finding **N-40**: a resolver may not read the clock, and ruling D5's rule
    -- a shadow's figure resolves on the shadow's own DUE date, as its escrow
    already does -- is what plan step **X-au-g** applies to the P&I term.  Until
    then the read exists; pinning it here makes it one field a reader can see
    and one value a whole pass shares, where it was one ``date.today()`` per row
    set before.
    """

    def __init__(self, scenario_id: int, as_of: date) -> None:
        """Pin the scenario and the evaluation date; resolve nothing yet.

        Args:
            scenario_id: The scenario whose loan payments this prices.
            as_of: The evaluation date for each loan's rate-period P&I.
        """
        self._scenario_id = scenario_id
        self._as_of = as_of
        self._config: "dict[int, _LivePaymentConfig] | None" = None
        self._loans: "dict[int, tuple[_LoanCashBasis | None, list]]" = {}

    @property
    def config_by_transfer(self) -> "dict[int, _LivePaymentConfig]":
        """``{transfer_id: config}`` for every loan payment in the scenario.

        Resolved on first read and kept.  Only transfers that actually need a
        read-time figure are carried: a DERIVE-mode loan payment (its cash
        re-derives P&I + as-of escrow + extra) or a MANUAL one carrying a
        standing extra (its stored base + extra).  A generic transfer has no
        settings row and never reaches the query; a manual payment with no extra
        keeps its stored amount and is dropped here, so the absence of a key is
        the whole "this row needs no live figure" answer.

        **It is scoped by SCENARIO where the row-set producers scoped by the
        candidates' transfer ids**, which tightens it: a transfer belonging to
        another scenario can no longer be priced against this basis.  Both
        producers already took a ``scenario_id`` and resolved the LOAN against
        it, so pricing a foreign transfer meant resolving one scenario's payment
        against another's loan.  Zero such rows exist on the 2026-08-16
        production clone (``budget.loan_payment_settings`` is empty, so the map
        is empty there and this rule is graded on a seeded loan).
        """
        if self._config is None:
            self._config = _load_live_payment_configs(self._scenario_id)
        return self._config

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
            basis = _resolve_loan_basis(loan_account_id, self._as_of)
            lines = [] if basis is None else load_escrow_lines(loan_account_id)
            self._loans[loan_account_id] = (basis, lines)
        return self._loans[loan_account_id]

    def live_cash(self, shadow: Transaction) -> "Decimal | None":
        """Return the live cash that SUPERSEDES *shadow*'s stored figure, or ``None``.

        **The ONE rule both the projected display and the settle freeze ask**,
        and collapsing the two copies of it is plan step X-au-c2b's doing.  A
        stored transfer amount is a cache of this derivation, so every balance
        and display surface shows the recompute -- which is what keeps a payment
        row from disagreeing with the loan card after an escrow, rate, or extra
        change.  At a settle the same figure is what FREEZES, so the frozen cash
        and the genesis split read one number on the shadow's own DUE date and
        ``cash == split`` holds by construction.

        ``None`` -- leave the stored estimate or a typed actual alone -- for
        every shadow that needs no live figure: no transfer, an operator
        ``is_override`` (the operator owns that amount), an already-settled
        shadow, a transfer that is not a loan payment, a MANUAL payment with no
        standing extra (its stored estimate already IS the cash), or a loan that
        will not resolve.

        **The ``is_projected`` guard is what makes the settle freeze ONE-SHOT.**
        ``transfer_service`` resolves the figure BEFORE applying the status, so a
        genuine first settle still sees a Projected shadow; a re-settle of an
        already-DONE shadow -- the ``done -> done`` identity a stale tab can
        submit -- answers ``None`` here, so a frozen ``actual_amount`` is never
        rewritten to a later figure that was never paid.

        Args:
            shadow: The shadow being asked about.  Either leg resolves the same
                figure (both share the transfer id, the pay period and the due
                date), so Transfer Invariant 3 is preserved whichever is passed.

        Returns:
            The live cash, or ``None`` when this shadow keeps its stored figure.
        """
        if (
            shadow.transfer_id is None
            or shadow.is_override
            or not is_projected(shadow)
        ):
            return None
        config = self.config_by_transfer.get(shadow.transfer_id)
        if config is None:
            return None
        if not config.derive_from_loan:
            # Manual mode with a standing extra (the config filter guarantees
            # extra > 0 here): stored base + extra, no re-derivation.
            return _manual_shadow_amount(shadow, config.extra_principal)
        return self.derive_cash(
            shadow, config.loan_account_id, config.extra_principal,
        )

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
        COUNTS and who last touched it, never what prices it.  :meth:`live_cash`
        gates on them because the read-time REPAIR is a question about which
        stored figure to supersede; pricing is not.

        Args:
            shadow: The payment shadow whose installment dates the escrow.
            loan_account_id: The destination loan to resolve.
            extra_principal: The recurring payment's standing extra principal
                (``0.00`` when none), from :func:`loan_payment_config`.

        Returns:
            The derived cash, or ``None`` when the loan will not resolve -- an
            account carrying no ``LoanParams``, which rule 4 turns into a
            refusal rather than a fallback to the stored snapshot.
        """
        basis, escrow_lines = self._loan(loan_account_id)
        if basis is None:
            return None
        return _shadow_live_amount(basis, escrow_lines, shadow, extra_principal)


def loan_pricing(scenario_id: int, as_of: date) -> LoanPricing:
    """Return the read pass's :class:`LoanPricing` for *scenario_id*.

    The named constructor the amount model calls, so no caller reaches for the
    class directly and the two pins are always supplied together.  Resolves
    nothing: every derivation behind it is lazy, so a pass that prices no loan
    payment issues no query.

    Args:
        scenario_id: The scenario whose loan payments this prices.
        as_of: The evaluation date for each loan's rate-period P&I.

    Returns:
        The unresolved :class:`LoanPricing` handle.
    """
    return LoanPricing(scenario_id, as_of)


def _load_live_payment_configs(
    scenario_id: int,
) -> "dict[int, _LivePaymentConfig]":
    """Load ``{transfer_id: config}`` for the scenario's loan-payment transfers.

    One query: the scenario's transfers INNER-joined through their template to a
    ``loan_payment_settings`` row, so a scenario with no loan payment at all --
    which production is -- returns an empty map from a single indexed read and
    resolves no loan.  The join is what keeps this scenario-wide load cheap
    where a bare ``transfers`` scan would not be.

    Args:
        scenario_id: The scenario to load.

    Returns:
        ``{transfer_id: _LivePaymentConfig}``, carrying only the transfers that
        need a read-time figure: DERIVE mode, or MANUAL with a standing extra.
    """
    transfers = (
        db.session.query(Transfer)
        .join(TransferTemplate, Transfer.transfer_template_id == TransferTemplate.id)
        .join(
            LoanPaymentSettings,
            LoanPaymentSettings.transfer_template_id == TransferTemplate.id,
        )
        .options(
            contains_eager(Transfer.template).contains_eager(
                TransferTemplate.settings,
            ),
        )
        .filter(Transfer.scenario_id == scenario_id)
        .all()
    )
    config: dict[int, _LivePaymentConfig] = {}
    for xfer in transfers:
        derive, extra = loan_payment_config(xfer.template)
        if not derive and extra <= Decimal("0.00"):
            continue
        config[xfer.id] = _LivePaymentConfig(
            derive_from_loan=derive,
            extra_principal=extra,
            loan_account_id=xfer.to_account_id,
        )
    return config
