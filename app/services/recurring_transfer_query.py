"""Shared query: the active recurring transfer funding an account.

A single-responsibility leaf helper (no service imports, so no cycle) for the
one query three surfaces share: "does an active recurring transfer template pay
INTO this account, and if so which one?"  The loan and investment dashboards use
it to decide whether to show the set-up-a-recurring-payment prompt, and the loan
recurrence-sync (Risk R-4) uses it to find the rule whose ``end_date`` it bounds
to the projected payoff.  Centralising it keeps those surfaces from drifting on
what counts as an account's recurring funding transfer.
"""

from decimal import Decimal

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate


def active_recurring_transfer_template(
    account_id: int, user_id: int,
) -> TransferTemplate | None:
    """Return the active recurring transfer template paying INTO *account_id*.

    An active (``is_active``) :class:`TransferTemplate` owned by *user_id* whose
    destination is *account_id* and which carries a recurrence rule
    (``recurrence_rule_id`` set).  Only the FIRST is returned: more than one
    recurring transfer into a single account is a user misconfiguration, not a
    modeled case.  ``None`` when the account has no recurring funding transfer.
    The 1:1 ``settings`` row is eager-loaded, since the loan callers read its
    ``extra_principal`` right after (the prompt prefill and
    :func:`loan_standing_extra`).

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established by
            the caller's chokepoint).

    Returns:
        The active recurring :class:`TransferTemplate`, or ``None``.
    """
    return (
        db.session.query(TransferTemplate)
        .options(joinedload(TransferTemplate.settings))
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.to_account_id == account_id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .first()
    )


def loan_standing_extra(account_id: int, user_id: int) -> Decimal:
    """Return a loan's standing monthly overpayment (``0.00`` when none).

    The ``extra_principal`` on the loan's active recurring payment's
    ``loan_payment_settings`` row -- the single loan-level figure the payoff
    projection threads so the committed trajectory and payoff date reflect the
    real plan (step 5).  ``Decimal("0.00")`` when the loan has no recurring
    payment, or one with no settings row (a legacy manual payment).

    Args:
        account_id: The loan account whose standing extra to read.
        user_id: The owning user (scopes the lookup).

    Returns:
        The standing ``extra_principal`` ``Decimal``, or ``Decimal("0.00")``.
    """
    template = active_recurring_transfer_template(account_id, user_id)
    if template is None or template.settings is None:
        return Decimal("0.00")
    return Decimal(str(template.settings.extra_principal))


def loan_standing_extra_for_account(account_id: int) -> Decimal:
    """Return a loan's standing overpayment, resolving the owner from the account.

    The account-scoped form of :func:`loan_standing_extra` for callers that hold
    only ``account_id`` -- the resolver read switch
    (:func:`app.services.loan_payment_service.resolve_loan_seeded`), which loads
    it once and injects it into every summary-surface resolve so the seam cannot
    drift back to the contractual (extra-free) trajectory.  Derives the owning
    user from the account (one PK lookup) then reads the active recurring
    payment's ``extra_principal``.  ``Decimal("0.00")`` when the account does not
    exist or has no recurring loan payment.

    Args:
        account_id: The loan account whose standing extra to read.

    Returns:
        The standing ``extra_principal`` ``Decimal``, or ``Decimal("0.00")``.
    """
    account = db.session.get(Account, account_id)
    if account is None:
        return Decimal("0.00")
    return loan_standing_extra(account_id, account.user_id)
