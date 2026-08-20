"""The read pass's DERIVATION tier: what one pricing pass holds, built once.

**Split out of :mod:`._amount_source` at plan step X-au-i**, which is the leaf
that pushed that module past ``max-module-lines``.  The cut is the one its own
docstring already named: *"The DERIVATION tier is separate, and finding N-228 is
why."*  Classification (*which rule prices this row*) and resolution (*what does
that rule answer*) stayed together next door because each rule reads the
derivation its own kind owns; what lives here is the thing a pass HOLDS, which
no rule needs to see the inside of.

Nothing here imports from :mod:`._amount_source`, so the split introduces no
cycle in either direction -- this module is strictly below it.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, ``Decimal`` out; no Flask import, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Named for the annotations alone.  A runtime import of either would put the
    # paycheck / loan-resolver stacks on this module's load path, which is the
    # cycle every call site here defers to avoid (finding N-267).
    from app.services.income_service import SalaryPricing
    from app.services.loan_payment_service import LoanPricing

@dataclass(frozen=True)
class AmountBasis:
    """One read pass's live DERIVATIONS, pinned to an owner and a scenario.

    Built by :func:`amount_basis` and consumed by
    :func:`resolve_transaction_amount`.  The two derivations stay APART rather
    than merged, and that is the whole reason this type exists: a merged map
    makes "which rule applies" a question about map membership, which is the
    link-derived discriminator ruling R-FI refuted, and it hides which producer
    answered.  ``_amounts.live_amounts`` merges their ANSWERS for the callers
    that want one map, and does it in one place.

    **It holds the derivations rather than per-row answers, and that is plan
    step X-au-c2b's restructure.**  It was
    ``{priced_ids, {transaction_id: net}, {transaction_id: cash}}`` -- built
    over ONE row set, because that is the shape both producers returned.  But
    everything expensive behind those maps is scoped by the OWNER, the SCENARIO
    and the LOAN, never by the caller's row set: the paycheck engine runs over
    the owner's whole pay-period set, and a loan's P&I, payment day and escrow
    history are the loan's.  Storing the lookup's output instead of the
    derivation behind it is what made a pass row-set-shaped, and three defects
    followed from that one mistake:

      * a request that loaded two row sets paid every derivation twice --
        findings **N-268** (the dashboard pulse re-pricing rows the cash fold
        had priced) and **N-269** (the transfer settle door re-querying the
        transfer it had just loaded), which are two filings of this one cause;
      * ``live_loan_transfer_amounts`` and ``live_loan_payment_amount`` were two
        implementations of ONE rule, the second's docstring stating that it
        "mirrors" the first's candidate filter -- kept in step by hand;
      * a row outside the set had no answer, so the basis had to carry
        ``priced_ids`` as a membership guard: without it a MISS was
        indistinguishable from a producer's deliberate omission, and an
        adversarial review reproduced the consequence -- a manual loan payment
        resolved outside its own basis answered ``$1,250.00`` against a correct
        ``$1,400.00``, silently dropping a standing ``$150.00`` extra.

    **That guard is DELETED rather than kept, because the failure it caught is
    now unconstructible.**  Nothing is "absent" from a derivation: a manual
    payment's cash is COMPUTED from its own config whenever it is asked, so
    there is no membership question left to answer wrongly.  A guard against a
    state the model cannot reach is a fence, and this arc's business is making
    fences structurally unnecessary rather than adding them.

    Both derivations are LAZY, so a pass that prices no paycheck and no loan
    payment issues no query -- the "fast no-op when there are no candidates"
    property the row-set producers had, kept rather than traded for the sharing.

    Attributes:
        user_id: The owner these derivations are pinned to.
        scenario_id: The scenario they resolve under.
        salary: The owner-and-scenario salary derivation
            (:class:`app.services.income_service.SalaryPricing`): what each
            active profile pays, per template and period.
        loans: The scenario's loan-payment derivation
            (:class:`app.services.loan_payment_service.LoanPricing`): which
            transfers are loan payments, and each destination loan's P&I,
            payment day and escrow history.
    """

    user_id: int
    scenario_id: int
    salary: "SalaryPricing" = field(compare=False, repr=False)
    loans: "LoanPricing" = field(compare=False, repr=False)


def amount_basis(user_id, scenario_id) -> AmountBasis:
    """Return the read pass's :class:`AmountBasis` for an owner and scenario.

    Resolves NOTHING -- both derivations behind it are lazy -- so building one
    is free and a caller may build it before it knows whether any row will need
    it.  What it costs to ask is paid once per pass however many row sets ask,
    which is the point of plan step X-au-c2b's restructure.

    Calling the derivations per row is finding **N-228**: the paycheck engine
    runs ``paycheck_calculator.project_salary`` over the owner's whole
    pay-period set, because the biweekly rounding residue only reconciles
    against the complete annual figure.  One basis per read pass is what makes
    the per-row rules cheap; a read pass holds its own through
    :meth:`app.services.balance_at.BalanceContext.amounts`.

    **It takes the OWNER's id rather than an ``Account``, and that is plan step
    X-au-c2's re-keying.**  The only thing it ever read off the account was
    ``account.user_id`` (the salary derivation scopes its profile lookup by
    owner; the loan derivation scopes by scenario alone), so requiring the
    object forced a CROSS-ACCOUNT reader -- the calendar, the spending report, a
    dashboard -- to group its rows by account and pay for one basis per group.

    **The loan derivation's clock is ``date.today()`` and DELIBERATELY not a
    caller's as-of.**  Resolving a loan's rate-period P&I against the wall clock
    is finding **N-40**, owned by plan step X-au-g, and handing this a read
    pass's own ``as_of`` instead is plan step **X-i2**, which MOVES MONEY
    (``$3,631.74`` today against ``$3,722.53`` at a 2027 read).  Taking it here
    would ship that move inside a refactor whose gate is byte-identity, so the
    read stays where it was and is disclosed rather than quietly relocated.

    Args:
        user_id: The owner whose rows are being priced; scopes the salary
            derivation's profile lookup and its pay-period set.
        scenario_id: The scenario the amounts resolve under.

    Returns:
        The unresolved :class:`AmountBasis` for that owner and scenario.
    """
    # Pylint: ``import-outside-toplevel`` -- imported locally to keep the
    # income_service (paycheck/tax) and loan_payment_service (loan-resolver)
    # stacks off this module's load path and out of any import cycle, exactly as
    # ``_amounts`` has always done; the helpers are only needed at call time.
    # pylint: disable=import-outside-toplevel
    from app.services import income_service, loan_payment_service
    return AmountBasis(
        user_id=user_id,
        scenario_id=scenario_id,
        salary=income_service.salary_pricing(user_id, scenario_id),
        loans=loan_payment_service.loan_pricing(scenario_id, date.today()),
    )
