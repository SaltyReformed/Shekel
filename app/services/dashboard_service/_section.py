"""
Shekel Budget App -- Dashboard: the section the page is ABOUT.

The dashboard renders one account, over one pay period, out of one read
pass.  Naming that subject is what this module does, and it does it
exactly once per render: :func:`resolve_section` is the shared
head-of-function resolution the pulse producer
(:func:`~._pulse.compute_pulse_section`) and the hero fragment
(:func:`~._balance.compute_balance_section`) both start from, and the
route reads its own ``has_account`` off the same answer.

Pure aggregation -- no Flask imports, no database writes.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.extensions import db
from app.models.account import Account
from app.models.user import UserSettings
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext

if TYPE_CHECKING:
    # Type-only: the value arrives from ``balance_ctx.calendar()``, which
    # this module already reaches through ``BalanceContext``.
    from app.services.pay_calendar import DerivedPeriod

# Anchor-staleness fallback when the user has no settings row.  Shared
# with ``_pulse._anchor_is_stale`` (the rebuild surfaces the staleness
# signal on the hero's "last updated" caption).
_DEFAULT_STALENESS_DAYS = 14


@dataclass(frozen=True)
class DashboardSection:
    """One dashboard render's subject: one read pass, one account, one period.

    Every producer on this page answers about the same account over the same
    paycheck out of the same read pass, and this is that statement as a value.

    **It replaced a three-slot tuple of optionals** (pay-calendar plan step
    C2-f2e).  ``_resolve_section_context`` returned
    ``(Account | None, BalanceContext | None, PayPeriod | None)`` under a
    coupling rule written only in its docstring -- "``account`` is ``None``
    when the user has no resolvable grid account, and the other two are then
    ``None`` with it" -- so every consumer had to know that rule and nothing
    could hold them to it.  A caller with no account gets ``None`` from
    :func:`resolve_section` instead: the coupling is the return type now, and
    a section that EXISTS carries all three facts.

    **Why the account and the settings ride here rather than being looked up
    again.**  ``resolve_grid_account`` needs the settings row and the pulse
    hero needs it a second time for the staleness threshold; before this step
    ``/`` resolved the account TWICE per render (the route for its
    ``has_account`` flag, the producer for its own use) and queried the
    settings row TWICE inside the pulse producer alone.  Measured on the test
    database over an owner with a salary profile, a 401(k), a mortgage and an
    active goal: ``resolve_grid_account`` 2, ``_get_user_settings`` 2.  Both
    are 1 now, because the render resolves its subject once and hands it down.

    Attributes:
        balance_ctx: The render's read pass -- the pinned ``as_of``, the
            baseline scenario, and the memoized pay calendar and amount basis.
            **Built by the ROUTE**, which is what makes "one pass per render"
            structural here rather than a coincidence of what each producer
            happened to call; this package holds no ``BalanceContext.build``
            call at all (ledger row **P56**).
        account: The dashboard's account -- ``resolve_grid_account``'s pick,
            which may be ANY non-amortizing kind (a user can point the
            dashboard at an HYSA, or the fallback can land on a non-checking
            account).  Never ``None``: a render with no resolvable account has
            no section.
        settings: The owner's :class:`~app.models.user.UserSettings`, or
            ``None`` when they have no row.  Loaded once, as the input to the
            account resolution above, and read again by the hero's staleness
            caption and the chart's low-balance threshold.
    """

    balance_ctx: BalanceContext
    account: Account
    settings: UserSettings | None

    @property
    def current_period(self) -> "DerivedPeriod | None":
        """The paycheck containing the pass's day, or ``None`` outside the schedule.

        **A PROPERTY, not a field, and the reason is a defect this shape has
        already caused once**
        (:class:`~app.services.savings_dashboard_service._types._DashboardCoreData`,
        whose own docstring records it).  Deriving the current period inside
        the loader made a producer that returns early RAISE ``PayCalendarError``
        for a legacy owner -- a period stored before ``budget.pay_schedule``
        existed, whose span ``resolve_cadence``'s fallback reads back as a
        cadence outside 1..365.  A page must not fail for a fact it never uses,
        so this is derived where it is ASKED and :func:`resolve_section` touches
        no calendar.

        **It is DERIVED from the owner's paydays, not read off the stored
        span** (pay-calendar plan step C2-f2e).  It was
        ``pay_period_service.get_current_period(user_id)`` -- SQL matching
        ``start_date <= today <= end_date`` against the two columns plan step
        **C4** drops, resolving a second ``date.today()`` of its own.  Where a
        stored ``end_date`` disagrees with the one the paydays imply (plan
        finding **P1**, the disagreement nothing reconciles) the two name
        different paychecks, and this page then labelled one period's balance
        with another's dates.  One derivation now, off the pass's memoized
        calendar and the pass's pinned day.

        It cannot answer twice differently: both terms are pinned on a frozen
        :class:`~app.services.balance_at.BalanceContext`, so the bisect is a
        pure function of state nothing can move mid-render.

        Returns:
            The covering :class:`~app.services.pay_calendar.DerivedPeriod`,
            whose ``period_id`` is never ``None``, or ``None`` when the pass's
            day precedes the owner's first payday or lies past their horizon.
        """
        return self.balance_ctx.calendar().period_containing(
            self.balance_ctx.as_of,
        )


def resolve_section(
    balance_ctx: BalanceContext,
) -> "DashboardSection | None":
    """Resolve what this dashboard render is about, or ``None`` for no account.

    The shared head-of-function resolution the pulse producer
    (:func:`~._pulse.compute_pulse_section`), the hero fragment
    (:func:`~._balance.compute_balance_section`) and the page's own
    ``has_account`` flag all read, so the account and the settings row are
    resolved ONCE per render rather than once per consumer.

    **It TAKES the read pass rather than building one** (pay-calendar plan step
    C2-f2e, ledger rows **P56** and **P61**).  It called
    ``BalanceContext.build`` itself, and ``compute_tracks_section`` called it
    too, so ``/`` opened TWO passes and derived the owner's pay calendar TWICE
    per render where ``/grid``, ``/savings`` and ``/retirement`` each derive it
    once.  Two figures published on one screen out of two passes are two
    figures computed against two clocks.  The route opens the pass now, and
    nothing under ``app/services/dashboard_service/`` opens another.

    **It resolves no period**, deliberately -- see
    :attr:`DashboardSection.current_period` for the legacy owner that arm would
    otherwise raise for.

    Args:
        balance_ctx: The render's read pass, built by the route.

    Returns:
        The :class:`DashboardSection`, or ``None`` when the owner has no
        resolvable grid account -- the state the page renders its "Set up an
        account" empty state for.  A user with no baseline scenario is NOT
        reported here: the seam raises and one application-level handler
        answers (plan step X-v2, ruling R-BW).
    """
    settings = _get_user_settings(balance_ctx.user_id)
    account = resolve_grid_account(balance_ctx.user_id, settings)
    if account is None:
        return None
    return DashboardSection(
        balance_ctx=balance_ctx, account=account, settings=settings,
    )


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
