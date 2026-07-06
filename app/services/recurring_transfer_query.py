"""Shared query: the active recurring transfer funding an account.

A single-responsibility leaf helper (no service imports, so no cycle) for the
one query three surfaces share: "does an active recurring transfer template pay
INTO this account, and if so which one?"  The loan and investment dashboards use
it to decide whether to show the set-up-a-recurring-payment prompt, and the loan
recurrence-sync (Risk R-4) uses it to find the rule whose ``end_date`` it bounds
to the projected payoff.  Centralising it keeps those surfaces from drifting on
what counts as an account's recurring funding transfer.
"""

from app.extensions import db
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

    Args:
        account_id: The destination account (a loan or investment account).
        user_id: The owning user (scopes the query -- ownership is established by
            the caller's chokepoint).

    Returns:
        The active recurring :class:`TransferTemplate`, or ``None``.
    """
    return (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.to_account_id == account_id,
            TransferTemplate.is_active.is_(True),
            TransferTemplate.recurrence_rule_id.isnot(None),
        )
        .first()
    )
