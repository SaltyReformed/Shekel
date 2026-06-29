"""
Shekel Budget App -- Transfer Service ownership loaders

The owned-entity lookup helpers for :mod:`app.services.transfer_service`:
each loads one entity a transfer references (account, pay period, scenario,
category, transfer template) and verifies it belongs to the acting user,
raising :class:`~app.exceptions.NotFoundError` with an identical message for
both "missing" and "not yours" (the project security-response rule -- no
existence oracle).

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the Build-Order Step 2 posting-ledger wiring lands.  These
five helpers are a cohesive, transfer-service-private cluster (single
responsibility: load-and-verify-ownership) with no dependency on the rest of
the service, mirroring the ``app/routes/transfers/_helpers.py`` split on the
route side.  Flask-isolated like the parent service: plain data in, ORM
objects out, no ``request`` / ``session`` imports.
"""

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transfer_template import TransferTemplate
from app.exceptions import NotFoundError


def _get_owned_account(account_id, user_id, label="Account"):
    """Load an Account and verify ownership.

    Args:
        account_id: The primary key.
        user_id:    The expected owner.
        label:      Human-readable label for error messages.

    Returns:
        The Account object.

    Raises:
        NotFoundError: If the account does not exist or belongs to
            another user.  The message is identical in both cases
            (security response rule).
    """
    acct = db.session.get(Account, account_id)
    if acct is None or acct.user_id != user_id:
        raise NotFoundError(f"{label} {account_id} not found.")
    return acct


def _get_owned_period(pay_period_id, user_id):
    """Load a PayPeriod and verify ownership.

    Imported inside the function to avoid circular imports (same
    pattern used by carry_forward_service and credit_workflow).

    Raises:
        NotFoundError: If the period does not exist or belongs to
            another user.
    """
    period = db.session.get(PayPeriod, pay_period_id)
    if period is None or period.user_id != user_id:
        raise NotFoundError(f"Pay period {pay_period_id} not found.")
    return period


def _get_owned_scenario(scenario_id, user_id):
    """Load a Scenario and verify ownership.

    Raises:
        NotFoundError: If the scenario does not exist or belongs to
            another user.
    """
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != user_id:
        raise NotFoundError(f"Scenario {scenario_id} not found.")
    return scenario


def _get_owned_category(category_id, user_id):
    """Load a Category and verify ownership.

    Returns None if *category_id* is None (caller explicitly passed
    no category).

    Raises:
        NotFoundError: If the category does not exist or belongs to
            another user.
    """
    if category_id is None:
        return None
    cat = db.session.get(Category, category_id)
    if cat is None or cat.user_id != user_id:
        raise NotFoundError(f"Category {category_id} not found.")
    return cat


def _get_owned_transfer_template(template_id, user_id):
    """Load a TransferTemplate and verify ownership.

    Returns None if *template_id* is None.

    Raises:
        NotFoundError: If the template does not exist or belongs to
            another user.
    """
    if template_id is None:
        return None
    tpl = db.session.get(TransferTemplate, template_id)
    if tpl is None or tpl.user_id != user_id:
        raise NotFoundError(f"Transfer template {template_id} not found.")
    return tpl
