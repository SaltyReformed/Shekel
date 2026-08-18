"""
Shekel Budget App -- Dashboard: the section the page is ABOUT.

The dashboard renders one account, over one pay period, out of one read
pass.  Resolving that subject is what this module does, and it does it
exactly once per render: :func:`_resolve_section_context` is the shared
head-of-function resolution the pulse producer
(:func:`~._pulse.compute_pulse_section`) and the hero fragment
(:func:`~._balance.compute_balance_section`) both start from.

Pure aggregation -- no Flask imports, no database writes.
"""

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.user import UserSettings
from app.services import pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext

# Anchor-staleness fallback when the user has no settings row.  Shared
# with ``_pulse._anchor_is_stale`` (the rebuild surfaces the staleness
# signal on the hero's "last updated" caption).
_DEFAULT_STALENESS_DAYS = 14


def _resolve_section_context(
    user_id: int,
) -> tuple[Account | None, BalanceContext | None, PayPeriod | None]:
    """Resolve the account, the read pass, and the current period.

    The shared head-of-function resolution the pulse producer
    (:func:`~._pulse.compute_pulse_section`) and
    :func:`compute_balance_section` both need, so the resolution is
    defined once rather than copied.

    **The middle slot is a ``BalanceContext``, not a ``Scenario``.**  The
    annotation said ``Scenario | None`` from before the context existed, while
    both consumers read ``.has_baseline`` / ``.scenario`` off it -- a type that
    documented a value this function has never returned (corrected at plan step
    X-v2, which deleted the ``has_baseline`` reads that made it visible).

    Returns:
        ``(account, balance_ctx, current_period)``.  ``account`` is ``None``
        when the user has no resolvable grid account, and the other two are
        then ``None`` with it; ``current_period`` is ``None`` when no period
        contains today.  A user with no baseline scenario is NOT reported here
        -- the seam raises and one application-level handler answers (plan step
        X-v2, ruling R-BW).
    """
    settings = _get_user_settings(user_id)
    account = resolve_grid_account(user_id, settings)
    if account is None:
        return None, None, None

    balance_ctx = BalanceContext.build(user_id)
    current_period = pay_period_service.get_current_period(user_id)
    return account, balance_ctx, current_period


# ── Shared settings helper ─────────────────────────────────────────
#
# ``_get_last_anchor_date`` used to live here and is DELETED (ruling R-EP,
# plan step X-f1c3a).  It was a THIRD statement of "which assertion is the
# latest one", ordering by ``created_at`` alone -- so once ``observed_on``
# became user-supplied it named a different row than
# ``cash_ledger.resolve_anchor``'s ``(observed_on, created_at, id)`` for any
# back-dated assertion, on a caption sitting next to a balance the resolver
# produced.  Its one consumer (the pulse hero's "last updated") now reads
# ``cash_ledger.reconciled_through(...).observed_day``: the day the balance was
# TRUE, from the accessor that already answers that question for the reconcile
# panel and the entry list.


def _get_user_settings(user_id: int) -> UserSettings | None:
    """Load user settings."""
    return (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )
